"""Synthetic, network-none Redis for default search tests; never a shared DSN.

The managed task retains the directory and durable receipts. Only the exact
positively identified container is stopped/removed; uncertainty is a test error.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from typing import Any, cast
from uuid import uuid4

import redis
import redis.asyncio as aioredis

from tests.search_redis_image import IMAGE_REFERENCE, PLATFORM, validate_cached_image


class OwnershipError(RuntimeError):
    pass


def docker(*args: str) -> Any:
    result = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
        env={key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ},
    )
    return result.stdout.strip()


class OwnedSearchRedis:
    def __init__(self, root: Path, task: str):
        root = root.absolute()
        # Reject symlinks at every level, not just a resolved final component.
        if any(parent.is_symlink() for parent in (root, *root.parents)):
            raise OwnershipError("Redis resource root is a symlink")
        self.directory = Path(tempfile.mkdtemp(prefix="r", dir=root))
        self.socket = self.directory / "s"
        if len(os.fsencode(self.socket)) > 107:
            raise OwnershipError("Owned Redis Unix socket path exceeds 107 bytes")
        self.directory_identity = self._identity(self.directory, socket=False)
        self.nonce = uuid4().hex
        self.task = task
        self.name = f"daemon-search-test-{self.nonce}"
        self.labels = {"daemon.test.task": task, "daemon.test.nonce": self.nonce}
        self.image = ""
        self.container = ""
        self.socket_identity: tuple[int, int, int] | None = None
        self.run_id = ""
        self.marker = f"fixture-owner:{self.nonce}"
        self.events: list[dict[str, Any]] = []
        self.command = [
            "redis-server",
            "--port",
            "0",
            "--unixsocket",
            "/fixture/s",
            "--unixsocketperm",
            "700",
            "--save",
            "",
            "--appendonly",
            "no",
        ]
        self.record("intent", task=task, nonce=self.nonce, name=self.name)

    def record(self, event: str, **values: Any) -> None:
        self.events.append({"event": event, **values})
        # Replace only this object's receipt; source and recovery artifacts untouched.
        receipt = self.directory / "receipt.json"
        temporary = self.directory / "receipt.next"
        with temporary.open("x") as output:
            json.dump(self.events, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(receipt)

    @staticmethod
    def _identity(path: Path, *, socket: bool) -> tuple[int, int, int]:
        value = path.lstat()
        correct_type = stat.S_ISSOCK(value.st_mode) if socket else stat.S_ISDIR(value.st_mode)
        if not correct_type or value.st_uid != os.getuid() or value.st_mode & 0o077:
            raise OwnershipError(f"Unowned or non-private Redis path: {path}")
        return value.st_dev, value.st_ino, value.st_uid

    def verify_directory(self) -> None:
        if self._identity(self.directory, socket=False) != self.directory_identity:
            raise OwnershipError("Redis directory identity changed")

    def inspect_owned(self, reference: str | None = None) -> dict[str, Any]:
        self.verify_directory()
        data = json.loads(docker("inspect", reference or self.container))
        if not isinstance(data, list) or len(data) != 1:
            raise OwnershipError("Ambiguous Redis inspect result")
        item = data[0]
        host = item["HostConfig"]
        mounts = item["Mounts"]
        valid = (
            re.fullmatch(r"[0-9a-f]{64}", item["Id"])
            and (not self.container or item["Id"] == self.container)
            and item["Name"] == "/" + self.name
            and item["Image"] == self.image
            and item["Config"]["Labels"] == self.labels
            and item["Config"]["User"] == f"{os.getuid()}:{os.getgid()}"
            and item["Config"]["Cmd"] == self.command
            and host["NetworkMode"] == "none"
            and not host.get("PortBindings")
            and not host["Privileged"]
            and host["ReadonlyRootfs"]
            and host["CapDrop"] == ["ALL"]
            and host["SecurityOpt"] == ["no-new-privileges"]
            and host["Memory"] == 128 * 1024 * 1024
            and host["PidsLimit"] == 64
            and host["Tmpfs"]
            == {
                "/tmp": "rw,noexec,nosuid,size=16m",
                "/data": "rw,noexec,nosuid,size=16m",
            }
            and len(mounts) == 1
            and mounts[0]["Type"] == "bind"
            and mounts[0]["Source"] == str(self.directory)
            and mounts[0]["Destination"] == "/fixture"
            and mounts[0]["RW"] is True
        )
        if not valid:
            raise OwnershipError("Redis container identity/configuration mismatch; retained")
        return item

    def verify_socket(self) -> None:
        self.inspect_owned()
        actual = self._identity(self.socket, socket=True)
        if self.socket_identity is not None and actual != self.socket_identity:
            raise OwnershipError("Redis socket identity changed")
        self.socket_identity = actual

    def start(self) -> None:
        try:
            output = docker("image", "inspect", IMAGE_REFERENCE)
        except subprocess.CalledProcessError as error:
            # Only this fixed, credential-free inspection is reported. Do not
            # infer image absence from exit 1 or discard Docker's actual reason.
            stderr = (error.stderr or "")[:2048]
            stdout = (error.stdout or "")[:2048]
            self.record("image-inspect-failed", exit=error.returncode, stderr=stderr, stdout=stdout)
            raise OwnershipError(
                f"docker image inspect {IMAGE_REFERENCE} failed "
                f"(exit {error.returncode}); stderr={json.dumps(stderr)}; stdout={json.dumps(stdout)}"
            ) from error
        try:
            self.image = validate_cached_image(json.loads(output))
        except ValueError as error:
            raise OwnershipError(f"Pinned Redis prerequisite rejected: {error}") from error
        self.record("image", image=self.image, reference=IMAGE_REFERENCE, platform=PLATFORM)
        args = [
            "create",
            "--pull=never",
            f"--platform={PLATFORM}",
            "--name",
            self.name,
            "--network=none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=128m",
            "--pids-limit=64",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
            "--tmpfs=/data:rw,noexec,nosuid,size=16m",
            "--mount",
            f"type=bind,src={self.directory},dst=/fixture",
            "--workdir=/fixture",
        ]
        for key, value in self.labels.items():
            args.extend(["--label", f"{key}={value}"])
        self.record("creating")
        try:
            self.container = docker(*args, self.image, *self.command)
        except (subprocess.SubprocessError, OSError):
            # Reconcile the one intended name only. Do not start or retry it.
            try:
                item = self.inspect_owned(self.name)
                self.container = item["Id"]
                self.record("create-uncertain-identified", container=self.container)
            except Exception:
                self.record("create-uncertain-retained", name=self.name)
            raise
        self.inspect_owned()
        self.record("created", container=self.container)
        docker("start", self.container)
        deadline = time.monotonic() + 10
        while not self.socket.exists():
            if not self.inspect_owned()["State"]["Running"] or time.monotonic() >= deadline:
                raise OwnershipError("Owned Redis did not bind its socket")
            time.sleep(0.025)
        self.verify_socket()
        with redis.Redis(
            unix_socket_path=str(self.socket),
            db=0,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        ) as client:
            self.run_id = cast(dict[str, Any], client.info("server"))["run_id"]
            if not re.fullmatch(r"[0-9a-f]{40}", self.run_id):
                raise OwnershipError("Invalid Redis run identity")
            if not client.set(self.marker, self.nonce, nx=True):
                raise OwnershipError("Redis nonce marker already exists")
            if client.get(self.marker) != self.nonce:
                raise OwnershipError("Redis nonce verification failed")
        self.record("ready", run_id=self.run_id, socket_identity=self.socket_identity)

    async def connect(self) -> aioredis.Redis:
        self.verify_socket()
        client = aioredis.Redis(
            unix_socket_path=str(self.socket),
            db=0,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        try:
            if (await client.info("server"))["run_id"] != self.run_id:
                raise OwnershipError("Redis server run identity changed")
            if await client.get(self.marker) != self.nonce:
                raise OwnershipError("Redis server nonce changed")
        except BaseException:
            await client.aclose()
            raise
        return client

    def close(self) -> None:
        if not self.container:
            return
        try:
            item = self.inspect_owned()
            if item["State"]["Running"]:
                # Redis handles SIGTERM; timeout=-1 forbids Docker's SIGKILL fallback.
                docker("stop", "--timeout=-1", self.container)
            item = self.inspect_owned()
            if item["State"]["Running"]:
                raise OwnershipError("Redis still running; retained")
            docker("rm", self.container)
            self.record("removed", container=self.container)
            self.container = ""
        except BaseException:
            self.record("cleanup-uncertain-retained", container=self.container)
            raise
