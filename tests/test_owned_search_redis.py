"""Negative admission/cleanup tests never contact Docker or shared Redis."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import pytest

from tests import owned_search_redis as module
from tests.owned_search_redis import OwnedSearchRedis, OwnershipError


@pytest.fixture
def owner(tmp_path):
    # Unit tests don't need a real socket or the long managed checkout pathname.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module.os, "fsencode", lambda value: b"synthetic-short-path")
        yield OwnedSearchRedis(tmp_path, "synthetic-task")


def inspected(owner: OwnedSearchRedis) -> dict[str, Any]:
    owner.container = "a" * 64
    owner.image = "sha256:" + "b" * 64
    return {
        "Id": owner.container,
        "Name": "/" + owner.name,
        "Image": owner.image,
        "Config": {
            "Labels": owner.labels.copy(),
            "User": f"{os.getuid()}:{os.getgid()}",
            "Cmd": owner.command.copy(),
        },
        "HostConfig": {
            "NetworkMode": "none",
            "PortBindings": {},
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Memory": 128 * 1024 * 1024,
            "PidsLimit": 64,
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m", "/data": "rw,noexec,nosuid,size=16m"},
        },
        "Mounts": [
            {"Type": "bind", "Source": str(owner.directory), "Destination": "/fixture", "RW": True}
        ],
        "State": {"Running": False},
    }


@pytest.mark.parametrize(
    "path,value",
    [
        (("Id",), "c" * 64),
        (("Image",), "sha256:" + "c" * 64),
        (("Name",), "/foreign"),
        (("Config", "Labels"), {"daemon.test.task": "foreign"}),
        (("Config", "User"), "0:0"),
        (("Config", "Cmd"), ["redis-server"]),
        (("HostConfig", "NetworkMode"), "host"),
        (("HostConfig", "Privileged"), True),
        (("HostConfig", "PortBindings"), {"6379/tcp": [{"HostPort": "6379"}]}),
        (("HostConfig", "Tmpfs"), {}),
        (("Mounts",), [{"Type": "volume", "Destination": "/data"}]),
    ],
)
def test_configuration_mismatch_never_stops_or_removes(owner, monkeypatch, path, value):
    item = inspected(owner)
    target = item
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    calls = []

    def fake(*args):
        calls.append(args)
        assert args == ("inspect", owner.container)
        return json.dumps([item])

    monkeypatch.setattr(module, "docker", fake)
    with pytest.raises(OwnershipError):
        owner.close()
    assert len(calls) == 1
    assert owner.events[-1]["event"] == "cleanup-uncertain-retained"


def test_stopped_owned_container_removed_without_stop_and_close_idempotent(owner, monkeypatch):
    item = inspected(owner)
    calls = []

    def fake(*args):
        calls.append(args)
        return json.dumps([item]) if args[0] == "inspect" else ""

    monkeypatch.setattr(module, "docker", fake)
    owner.close()
    owner.close()
    assert [call[0] for call in calls] == ["inspect", "inspect", "rm"]
    assert calls[-1] == ("rm", item["Id"])


def test_stop_uncertainty_retains_without_force_or_remove(owner, monkeypatch):
    item = inspected(owner)
    item["State"]["Running"] = True
    calls = []

    def fake(*args):
        calls.append(args)
        if args[0] == "stop":
            raise subprocess.TimeoutExpired("synthetic docker stop", 15)
        assert args[0] == "inspect"
        return json.dumps([item])

    monkeypatch.setattr(module, "docker", fake)
    with pytest.raises(subprocess.TimeoutExpired):
        owner.close()
    assert calls[-1] == ("stop", "--timeout=-1", owner.container)
    assert not any(call[0] == "rm" for call in calls)


def test_create_timeout_reconciles_exact_name_without_start_or_retry(owner, monkeypatch):
    item = inspected(owner)
    owner.container = ""
    calls = []

    def fake(*args):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": item["Image"]}])
        if args[0] == "create":
            assert "--pull=never" in args
            assert "--tmpfs=/data:rw,noexec,nosuid,size=16m" in args
            raise subprocess.TimeoutExpired("synthetic create", 15)
        assert args == ("inspect", owner.name)
        return json.dumps([item])

    monkeypatch.setattr(module, "docker", fake)
    with pytest.raises(subprocess.TimeoutExpired):
        owner.start()
    assert [call[0] for call in calls] == ["image", "create", "inspect"]
    assert owner.container == item["Id"]


@pytest.mark.parametrize(
    "detail",
    [
        "Error response from daemon: No such image: redis:7-alpine\n",
        "Cannot connect to the Docker daemon at unix:///synthetic/docker.sock\n",
        "permission denied while trying to connect to the Docker daemon\n",
    ],
)
def test_image_inspect_failure_reports_actual_reason_without_create_or_retry(
    owner, monkeypatch, detail
):
    calls = []
    cause = subprocess.CalledProcessError(
        1,
        ["docker", "image", "inspect", "redis:7-alpine"],
        output="[]\n",
        stderr=detail,
    )

    def fake(*args):
        calls.append(args)
        assert args == ("image", "inspect", "redis:7-alpine")
        raise cause

    monkeypatch.setattr(module, "docker", fake)
    with pytest.raises(OwnershipError) as caught:
        owner.start()
    assert caught.value.__cause__ is cause
    assert "docker image inspect redis:7-alpine failed (exit 1)" in str(caught.value)
    assert json.dumps(detail) in str(caught.value)
    assert 'stdout="[]\\n"' in str(caught.value)
    assert calls == [("image", "inspect", "redis:7-alpine")]
    assert owner.container == ""
    assert owner.events[-1] == {
        "event": "image-inspect-failed",
        "exit": 1,
        "stderr": detail,
        "stdout": "[]\n",
    }


def test_image_inspect_diagnostics_are_bounded_and_terminal_escaped(owner, monkeypatch):
    detail = "\x1b[31m" + "x" * 10000

    def fake(*args):
        assert args == ("image", "inspect", "redis:7-alpine")
        raise subprocess.CalledProcessError(2, "synthetic inspect", output=detail, stderr=detail)

    monkeypatch.setattr(module, "docker", fake)
    with pytest.raises(OwnershipError) as caught:
        owner.start()
    message = str(caught.value)
    assert "\x1b" not in message
    assert json.dumps(detail[:2048]) in message
    assert len(message) < 4300
    assert len(owner.events[-1]["stderr"]) == 2048
    assert len(owner.events[-1]["stdout"]) == 2048


def test_directory_substitution_refused_before_docker(owner, monkeypatch):
    old = owner.directory.with_name(owner.directory.name + "-retained")
    owner.directory.rename(old)
    owner.directory.mkdir(mode=0o700)
    monkeypatch.setattr(module, "docker", lambda *args: pytest.fail("Docker must not be called"))
    with pytest.raises(OwnershipError, match="directory identity"):
        owner.inspect_owned()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["run_id", "nonce"])
async def test_stale_server_or_nonce_closes_client(owner, monkeypatch, changed):
    closed = []
    owner.run_id = "d" * 40

    class Client:
        async def info(self, section):
            return {"run_id": "e" * 40 if changed == "run_id" else owner.run_id}

        async def get(self, key):
            return "foreign" if changed == "nonce" else owner.nonce

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(owner, "verify_socket", lambda: None)
    monkeypatch.setattr(module.aioredis, "Redis", lambda **kwargs: Client())
    with pytest.raises(OwnershipError):
        await owner.connect()
    assert closed == [True]
