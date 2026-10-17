"""Pinned image, workflow ordering and blocking failure tests; no real downloads."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest
import yaml

from tests import owned_search_redis as owned
from tests import search_redis_image as image


def metadata(image_id=image.CONFIG_DIGEST, reference=image.IMAGE_REFERENCE):
    return [{"Id": image_id, "RepoDigests": [reference], "Os": "linux", "Architecture": "amd64"}]


@pytest.mark.parametrize("image_id", [image.CONFIG_DIGEST, image.INDEX_DIGEST])
@pytest.mark.parametrize("reference", [image.IMAGE_REFERENCE, f"redis@{image.INDEX_DIGEST}"])
def test_exact_pinned_storage_representations(image_id, reference):
    assert image.validate_cached_image(metadata(image_id, reference)) == image_id


BAD_METADATA = [
    None,
    {},
    [],
    [None],
    [metadata()[0], metadata()[0]],
    [{}],
    *[
        [{**metadata()[0], key: value}]
        for key, value in [
            ("Id", None),
            ("Id", []),
            ("Id", "sha256:" + "f" * 64),
            ("Id", image.AMD64_MANIFEST_DIGEST),
            ("Os", "windows"),
            ("Architecture", "arm64"),
            ("RepoDigests", None),
            ("RepoDigests", []),
            ("RepoDigests", image.IMAGE_REFERENCE),
            ("RepoDigests", [{}]),
            ("RepoDigests", ["redis:7-alpine"]),
            ("RepoDigests", ["foreign/redis@" + image.INDEX_DIGEST]),
            ("RepoDigests", ["redis@sha256:" + "f" * 64]),
            ("RepoDigests", ["library/redis@" + image.INDEX_DIGEST]),
            ("RepoDigests", [image.IMAGE_REFERENCE, "foreign/redis@" + image.INDEX_DIGEST]),
        ]
    ],
    *[
        [{k: v for k, v in metadata()[0].items() if k != missing}]
        for missing in ("Id", "Os", "Architecture", "RepoDigests")
    ],
]


@pytest.mark.parametrize("data", BAD_METADATA)
def test_unqualified_metadata_never_creates_container(tmp_path, monkeypatch, data):
    monkeypatch.setattr(owned.os, "fsencode", lambda value: b"synthetic-short-path")
    fixture = owned.OwnedSearchRedis(tmp_path, "synthetic-task")
    calls = []

    def docker(*args):
        calls.append(args)
        assert args == ("image", "inspect", image.IMAGE_REFERENCE)
        return json.dumps(data)

    monkeypatch.setattr(owned, "docker", docker)
    with pytest.raises(owned.OwnershipError, match="Pinned Redis prerequisite rejected"):
        fixture.start()
    fixture.close()
    assert calls == [("image", "inspect", image.IMAGE_REFERENCE)]
    assert fixture.image == fixture.container == ""


def test_invalid_json_never_creates_container(tmp_path, monkeypatch):
    monkeypatch.setattr(owned.os, "fsencode", lambda value: b"synthetic-short-path")
    fixture = owned.OwnedSearchRedis(tmp_path, "synthetic-task")
    calls = []

    def docker(*args):
        calls.append(args)
        return "not JSON"

    monkeypatch.setattr(owned, "docker", docker)
    with pytest.raises(owned.OwnershipError, match="Pinned Redis prerequisite rejected"):
        fixture.start()
    assert calls == [("image", "inspect", image.IMAGE_REFERENCE)]


def workflow():
    root = Path(__file__).resolve().parents[1]
    return yaml.safe_load((root / ".github/workflows/ci.yml").read_text())


def provision_step():
    return next(
        s
        for s in workflow()["jobs"]["backend"]["steps"]
        if s["name"] == "Provision pinned search Redis image"
    )


def test_backend_provisioning_contract_and_order():
    job = workflow()["jobs"]["backend"]
    names = [s["name"] for s in job["steps"]]
    assert job["runs-on"] == "ubuntu-latest"
    assert set(job["services"]) == {"tasks-postgres"}
    assert "permissions" not in job and "permissions" not in workflow()
    assert names.index("Sync backend dependencies") < names.index("Python dependency audit")
    assert names.index("Python dependency audit") < names.index(
        "Provision pinned search Redis image"
    )
    assert names.index("Provision pinned search Redis image") < names.index("Pytest")
    step = provision_step()
    assert set(step) == {"name", "shell", "run"}
    assert step["shell"] == "bash"
    assert step["run"].splitlines() == [
        "set -euo pipefail",
        f"docker pull --platform={image.PLATFORM} {image.IMAGE_REFERENCE}",
        "uv run python -m tests.search_redis_image",
    ]


@pytest.mark.parametrize("failure,expected", [("pull", 17), ("verify", 18), ("none", 0)])
def test_actual_provisioning_script_blocks_following_stages(tmp_path, failure, expected):
    log = tmp_path / "calls"
    tools = tmp_path / "tools"
    tools.mkdir()
    for name, exit_code in [
        ("docker", 17 if failure == "pull" else 0),
        ("uv", 18 if failure == "verify" else 0),
    ]:
        tool = tools / name
        tool.write_text(
            f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(name)} >> {shlex.quote(str(log))}\n"
            f"exit {exit_code}\n"
        )
        tool.chmod(0o700)
    script = provision_step()["run"] + f"\nprintf '%s\\n' pytest >> {shlex.quote(str(log))}\n"
    result = subprocess.run(
        ["bash", "-c", script],
        env={"PATH": f"{tools}:{os.defpath}"},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == expected
    assert log.read_text().splitlines() == (
        ["docker"]
        if failure == "pull"
        else ["docker", "uv"]
        if failure == "verify"
        else ["docker", "uv", "pytest"]
    )


@pytest.mark.parametrize("image_id", [image.CONFIG_DIGEST, image.INDEX_DIGEST])
def test_verifier_inspects_only_pin(monkeypatch, capsys, image_id):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["check"] is True and kwargs["timeout"] == 15
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(metadata(image_id)))

    monkeypatch.setattr(image.subprocess, "run", run)
    image.main()
    assert calls == [["docker", "image", "inspect", image.IMAGE_REFERENCE]]
    assert f"daemon_id={image_id}" in capsys.readouterr().out


def test_verifier_rejects_wrong_platform(monkeypatch):
    item = metadata()
    item[0]["Architecture"] = "arm64"
    monkeypatch.setattr(
        image.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=json.dumps(item)),
    )
    with pytest.raises(SystemExit, match="prerequisite rejected"):
        image.main()


def test_verifier_failed_inspect_is_bounded_escaped_and_not_retried(monkeypatch):
    calls = []
    detail = "\x1b[31m" + "x" * 10000
    cause = subprocess.CalledProcessError(1, "synthetic", output=detail, stderr=detail)

    def run(*args, **kwargs):
        calls.append(args)
        raise cause

    monkeypatch.setattr(image.subprocess, "run", run)
    with pytest.raises(SystemExit) as caught:
        image.main()
    assert len(calls) == 1 and caught.value.__cause__ is cause
    assert "\x1b" not in str(caught.value)
    assert str(caught.value) == (
        f"docker image inspect {image.IMAGE_REFERENCE} failed (exit 1); "
        f"stderr={json.dumps(detail[:2048])}; stdout={json.dumps(detail[:2048])}"
    )
