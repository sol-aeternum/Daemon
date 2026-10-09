"""Stage E2c offline browser container gate: REVIEW BEFORE FIRST EXECUTION.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_browser_container_io.py``;
even then it is opt-in (``DAEMON_DOCKER_TESTS=1``).

One browser per container, one container per mode (ordinary, basic-stealth).
The qualified driver creates the browser allocation from the pinned image with
the pinned seccomp profile (network none, read-only, 1 GiB/no swap, 128 PIDs,
one CPU, 256 MiB shm, 128 MiB /tmp). The supervisor writes the length-prefixed
trusted bundle to the container's stdin, then bridges its framed stdio through
``IPCBridge`` + ``ResultCollector`` to an in-process ``Gateway`` over real
pipes. The gateway applies its real policy to the injected public answer; the
tests-only dial spy maps it to a host-loopback TLS fixture with a throwaway
self-signed certificate. Chromium must reject that certificate.

Pass requires supervisor-side evidence only: the TLS fixture saw handshake
attempts and completed none; the final RESULT is ``error`` with no content;
the browser stream ended with final + EOF; the entrypoint exit code (0: every
in-container isolation/sandbox/synthetic check passed and RESULT committed)
from the attach CLI and Docker's recorded state; identity-checked removal.
Browser stderr diagnostics are kept bounded and printed for the record only;
nothing here decides on them. No public network, DNS or live URL is reached.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_bridge import BridgeReason, IPCBridge
from scripts.web_fetch_pilot_browser_payload import MODULE_NAMES, browser_command, make_bundle
from scripts.web_fetch_pilot_command import CommandExecutor
from scripts.web_fetch_pilot_docker import (
    DockerCleanupFailure,
    OfflineContainerDriver,
    browser_policy,
)
from scripts.web_fetch_pilot_gateway import Gateway
from scripts.web_fetch_pilot_io import AsyncFD, FDFrameIO
from scripts.web_fetch_pilot_process import (
    NativeBackend,
    ProcessCleanupFailure,
    RawByteChannel,
    RawLauncher,
)
from scripts.web_fetch_pilot_results import ResultCollector
from tests.test_web_fetch_pilot_gateway_tls_io import (
    CANDIDATE,
    HOST,
    LoopbackFixtureConnector,
    write_certificate,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("DAEMON_DOCKER_TESTS") != "1",
    reason="Opt-in native browser container gate requires DAEMON_DOCKER_TESTS=1",
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "scripts/web_fetch_pilot_browser_seccomp.json"
URL = "https://openai.com/index/daemon-pilot-offline-fixture/"  # Never contacted.
VERSION = "pilot-e2-innertext-1"
WRITE_CHUNK = 16 * 1024 + 12
DIAGNOSTIC_CAP = 64 * 1024


@dataclass
class TLSFixture:
    """Host-loopback TLS endpoint recording only handshake outcomes."""

    context: ssl.SSLContext = field(default_factory=lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
    attempts: int = 0
    handshakes: int = 0
    failures: list[str] = field(default_factory=list)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        self.attempts += 1
        try:
            await asyncio.wait_for(writer.start_tls(self.context), 10)
            self.handshakes += 1  # A browser accepting this certificate fails the gate.
        except ssl.SSLError as exc:
            self.failures.append(str(exc.reason))
        except (ConnectionError, asyncio.IncompleteReadError, TimeoutError) as exc:
            self.failures.append(type(exc).__name__)
        finally:
            writer.close()
            self.tasks.discard(task)


class AttachReader:
    """Adapts the attach session's positional-only read to FDFrameIO's reader."""

    def __init__(self, channel: RawByteChannel) -> None:
        self._channel = channel

    async def read(self, size: int) -> bytes:
        return await self._channel.read(size)

    def close(self) -> None:
        self._channel.close()


def fd_set() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


async def drain(channel: RawByteChannel) -> bytes:
    """Continuous bounded stderr drain: keep the newest bytes, never parse."""
    kept = bytearray()
    while data := await channel.read(16 * 1024):
        kept.extend(data)
        del kept[:-DIAGNOSTIC_CAP]
    return bytes(kept)


def trusted_modules() -> dict[str, bytes]:
    return {name: (ROOT / "scripts" / name).read_bytes() for name in MODULE_NAMES}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ordinary", "basic-stealth"])
async def test_native_browser_rejects_untrusted_tls_through_relay_bridge_gateway(
    tmp_path: Path, mode: str
) -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    profile = PROFILE.read_bytes()
    policy = browser_policy(str(PROFILE), profile)  # Hash-pinned before any Docker call.
    run = {
        "original_url": URL,
        "allowed_hosts": [HOST],
        "mode": mode,
        "extraction_version": VERSION,
        "deadline_seconds": 30.0,
    }
    bundle = make_bundle(trusted_modules(), run)
    cert_path, key_path = write_certificate(tmp_path, HOST)
    fixture = TLSFixture()
    fixture.context.load_cert_chain(cert_path, key_path)
    server = await asyncio.start_server(fixture.handle, "127.0.0.1", 0)
    connector = LoopbackFixtureConnector(server.sockets[0].getsockname()[1])

    async def resolve(host: str) -> tuple[str, ...]:
        assert host == HOST
        return (CANDIDATE,)

    config = tmp_path / "docker-config"
    config.mkdir(mode=0o700)
    os.chmod(config, 0o700)
    executor = CommandExecutor(NativeBackend())
    launchers: list[RawLauncher] = []

    def attach() -> RawLauncher:
        launcher = RawLauncher(NativeBackend(), launch_deadline=15.0, close_grace=2.0)
        launchers.append(launcher)
        return launcher

    driver = OfflineContainerDriver(executor, attach, config_dir=str(config), policy=policy)
    to_gateway, to_bridge = os.pipe2(os.O_CLOEXEC), os.pipe2(os.O_CLOEXEC)
    gateway = Gateway(
        (HOST,),
        (),
        FDFrameIO(AsyncFD(to_gateway[0]), AsyncFD(to_bridge[1])),
        resolve,
        connector,
        deadline=44.0,
    )
    gateway_side = FDFrameIO(AsyncFD(to_bridge[0]), AsyncFD(to_gateway[1]))
    gateway_task = asyncio.create_task(gateway.run())
    evidence: dict[str, object] = {"mode": mode}
    try:
        async with asyncio.timeout(90):
            await driver.preflight()
            assert await driver.owned_ids() == ()
            container = await driver.start(browser_command())
            expires_at = asyncio.get_running_loop().time() + 45.0  # From child start.
            offset = 0
            while offset < len(bundle):
                offset += await container.stdin.write(bundle[offset : offset + WRITE_CHUNK])
            stderr_task = asyncio.create_task(drain(container.stderr))
            collector = ResultCollector(URL, (HOST,), VERSION)
            bridge = IPCBridge(
                FDFrameIO(AttachReader(container.stdout), container.stdin),
                gateway_side,
                collector,
                expires_at=expires_at,
            )
            outcome = await bridge.run()
            gateway_outcome = await asyncio.wait_for(gateway_task, 5)
            code = await container.wait()
            diagnostics = await asyncio.wait_for(stderr_task, 5)
            await container.aclose()
            assert await driver.owned_ids() == ()
            evidence.update(
                bridge=outcome.reason.value,
                browser_frames=outcome.browser_frames,
                gateway_frames=outcome.gateway_frames,
                exit_code=code,
                final_state=container.final_state,
                tls_attempts=fixture.attempts,
                tls_handshakes=fixture.handshakes,
                tls_failures=sorted(set(fixture.failures)),
                dials=len(connector.dials),
                gateway_received_frames=gateway_outcome.received_frames,
                bundle_sha256=hashlib.sha256(bundle).hexdigest(),
            )
            print("E2C_EVIDENCE", json.dumps(evidence, sort_keys=True))
            print("E2C_DIAGNOSTICS", diagnostics.decode("utf-8", "replace"))  # Record only.
            assert outcome.reason is BridgeReason.BROWSER_FINAL_EOF, evidence
            assert outcome.candidate is not None and outcome.candidate.content == b""
            assert outcome.candidate.metadata.status == "error"
            assert outcome.candidate.metadata.final_url == URL
            assert not outcome.cleanup_failed and outcome.pending_tasks == 0
            assert fixture.attempts >= 1 and fixture.handshakes == 0, evidence
            assert connector.dials and set(connector.dials) == {(CANDIDATE, 443)}
            assert code == 0 and container.final_state == ("exited", 0), evidence
            assert not gateway_outcome.cleanup_failed
            assert not driver.cleanup_failed and driver.retained is None
    finally:
        if not gateway_task.done():
            gateway_task.cancel()
        await asyncio.gather(gateway_task, return_exceptions=True)
        gateway_side.close()
        try:
            try:
                await driver.aclose()
            except DockerCleanupFailure:
                pass  # Asserted above; leftovers stay for the owner, never auto-removed.
        finally:
            for launcher in launchers:
                try:
                    await launcher.aclose()
                except ProcessCleanupFailure:
                    pass
            await executor.aclose()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*fixture.tasks, return_exceptions=True)
    for _ in range(200):
        if fd_set() == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert fd_set() == fds
    assert set(asyncio.all_tasks()) == tasks
