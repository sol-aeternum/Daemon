"""Stage D actual DNS-helper offline allocation: REVIEW BEFORE FIRST EXECUTION.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_dns_container_io.py``;
even then it is opt-in (``DAEMON_DOCKER_TESTS=1``).

Runs the reviewed trusted bootstrap/fixture payload in ONE owned network-none
container through the qualified driver (pinned daemon/image, policy create
vector, identity-checked removal). The bootstrap receives the exact trusted
bundle on stdin, stages it in the container's 16 MiB ``/tmp`` and runs the
fixture: identity, interpreter flags, scrubbed environment, interfaces and
routes, capabilities/no-new-privileges/seccomp and cgroup v2 limits are checked
from inside BEFORE the single actual ``StdlibSpawner``/``getaddrinfo`` helper
attempt for ``fixture.invalid`` only. No network is attached, no public DNS
can be reached, no browser and no gateway run. The parent independently
validates the single record against hashes of the exact trusted host bytes.

``unsupported`` (cgroup interface not the fixed v2 form) is a truthful blocked
result, never success. Leftovers are reported, never auto-removed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_command import CommandExecutor
from scripts.web_fetch_pilot_dns_payload import (
    OUTPUT_RECORD_MAX,
    build_exec_argv,
    expected_hashes,
    make_payload,
    validate_record,
)
from scripts.web_fetch_pilot_docker import (
    AttachedContainer,
    DockerCleanupFailure,
    OfflineContainerDriver,
)
from scripts.web_fetch_pilot_process import (
    NativeBackend,
    ProcessCleanupFailure,
    RawByteChannel,
    RawLauncher,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("DAEMON_DOCKER_TESTS") != "1",
    reason="Opt-in native DNS-helper container gate requires DAEMON_DOCKER_TESTS=1",
)

ROOT = Path(__file__).resolve().parents[1]
# Reviewed trusted bytes (handoff SHA256SUMS); any drift refuses before Docker.
TRUSTED = {
    "scripts/web_fetch_pilot_core.py": (
        "06f77b6626e96e83b66d9dd154713aa2433519a792416dd36e3f2c7e68706b8b"
    ),
    "scripts/web_fetch_pilot_dns.py": (
        "b9979d984d011af1e9341dd9ccf547fb4c8834710bb37465b4aa1e745051bf90"
    ),
    "scripts/web_fetch_pilot_dns_payload.py": (
        "f6992570f876092c2c60696851b8370bec4f715df52bb4b88357024f17fdcb6e"
    ),
}
PYTHON = "/usr/local/bin/python"  # The fixture's own identity check verifies it.
COMMAND = (
    "-i",
    "LANG=C.UTF-8",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    *build_exec_argv(PYTHON),
)
WRITE_CHUNK = 16 * 1024 + 12
FAILURE_RECORD = b'{"status":"failure"}\n'


def trusted_bytes() -> tuple[bytes, bytes]:
    for relative, digest in TRUSTED.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, relative
    core = (ROOT / "scripts/web_fetch_pilot_core.py").read_bytes()
    dns = (ROOT / "scripts/web_fetch_pilot_dns.py").read_bytes()
    return core, dns


def fd_set() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


async def restored(fds: set[str], tasks: set[asyncio.Task[object]]) -> None:
    for _ in range(200):
        if fd_set() == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert fd_set() == fds
    assert set(asyncio.all_tasks()) == tasks


def private_config(tmp_path: Path) -> Path:
    path = tmp_path / "docker-config"
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


@asynccontextmanager
async def owned(config_dir: Path) -> AsyncIterator[OfflineContainerDriver]:
    """Real executor/attach launchers; teardown never auto-removes leftovers."""
    executor = CommandExecutor(NativeBackend())
    launchers: list[RawLauncher] = []

    def attach() -> RawLauncher:
        launcher = RawLauncher(NativeBackend(), launch_deadline=15.0, close_grace=2.0)
        launchers.append(launcher)
        return launcher

    driver = OfflineContainerDriver(executor, attach, config_dir=str(config_dir))
    try:
        async with asyncio.timeout(90):
            yield driver
    finally:
        try:
            try:
                await driver.aclose()
            except DockerCleanupFailure:
                pass  # The body asserts cleanup state; leftovers stay for the owner.
        finally:
            for launcher in launchers:
                try:
                    await launcher.aclose()
                except ProcessCleanupFailure:
                    pass
            await executor.aclose()


async def read_all(channel: RawByteChannel, cap: int) -> bytes:
    received = bytearray()
    while data := await channel.read(16 * 1024):
        received.extend(data)
        assert len(received) <= cap
    return bytes(received)


async def exchange(container: AttachedContainer, payload: bytes) -> tuple[bytes, bytes, int]:
    async def feed() -> None:
        offset = 0
        while offset < len(payload):
            offset += await container.stdin.write(payload[offset : offset + WRITE_CHUNK])
        container.stdin.close()  # Bootstrap requires EOF (StdinOnce verified).

    async with asyncio.timeout(30):  # Bootstrap's own main ceiling is 8 s.
        _, out, err = await asyncio.gather(
            feed(),
            read_all(container.stdout, OUTPUT_RECORD_MAX + 1),
            read_all(container.stderr, 64 * 1024),
        )
        code = await container.wait()
    return out, err, code


@pytest.mark.asyncio
async def test_native_dns_helper_offline_allocation_record(tmp_path: Path) -> None:
    core, dns = trusted_bytes()
    payload = make_payload(core, dns)
    manifest = expected_hashes(core, dns)
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned(private_config(tmp_path)) as driver:
        await driver.preflight()
        assert await driver.owned_ids() == ()
        container = await driver.start(COMMAND)
        out, err, code = await exchange(container, payload)
        await container.aclose()
        assert await driver.owned_ids() == ()
        assert not driver.cleanup_failed and driver.retained is None
    await restored(fds, tasks)
    assert out.endswith(b"\n") and out.count(b"\n") == 1
    record = validate_record(out[:-1], manifest)
    if record["status"] == "unsupported":
        pytest.fail("cgroup v2 interface unsupported inside container: truthful blocked")
    evidence = {
        key: record.get(key)
        for key in (
            "status",
            "interpreter",
            "helper_started",
            "helper_exit_code",
            "dns_failure",
            "cleanup_failed",
            "fds_before",
            "fds_after",
            "tasks_before",
            "tasks_after",
        )
    }
    print("STAGE_D_EVIDENCE", json.dumps(evidence, sort_keys=True))  # Non-secret fields only.
    assert record["status"] == "ok", evidence
    assert (code, err) == (0, b"")
    assert container.final_state == ("exited", 0)


@pytest.mark.asyncio
async def test_native_bootstrap_refuses_malformed_payload_before_staging(tmp_path: Path) -> None:
    core, dns = trusted_bytes()
    bundle = json.loads(make_payload(core, dns))
    bundle["extra"] = "refused"
    payload = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned(private_config(tmp_path)) as driver:
        await driver.preflight()
        container = await driver.start(COMMAND)
        out, err, code = await exchange(container, payload)
        await container.aclose()
        assert await driver.owned_ids() == ()
        assert not driver.cleanup_failed and driver.retained is None
    await restored(fds, tasks)
    assert (out, err, code) == (FAILURE_RECORD, b"", 1)
    assert dict(validate_record(out[:-1], expected_hashes(core, dns))) == {"status": "failure"}
    assert container.final_state == ("exited", 1)
