"""Stage E3c two-container gate: REVIEW BEFORE FIRST EXECUTION.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_two_container_io.py``;
even then it is opt-in (``DAEMON_DOCKER_TESTS=1``).

Every resource carries one run token. Topology, all owned and removed:

- one ``--internal`` network ``1.2.3.0/29`` (isolated gateway mode, no host
  route, verified not to forward DNS upstream);
- a TLS fixture container on it, aliased ``openai.com``, serving a throwaway
  self-signed certificate on port 443 and reporting only handshake outcomes;
- a gateway container on it running the UNCHANGED ``Gateway`` with the real
  ``DNSResolver``/``StdlibSpawner`` (Docker's embedded DNS) and
  ``NumericConnector`` (real numeric dial and real peer check), empty
  inventory (non-live fixture run);
- the network-none browser container from E2 (ordinary mode).

The supervisor bridges browser and gateway framed stdio through ``IPCBridge``
and ``ResultCollector``. No test-only transport, resolver or address
exception is used anywhere. Pass requires supervisor-side evidence only: the
fixture saw a handshake attempt reach it through the gateway and completed
none; RESULT ``error`` without content; clean exits for all three
entrypoints; identity-checked removal of everything. No public network, DNS
or live URL is reachable from any container.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_bridge import BridgeReason, IPCBridge
from scripts.web_fetch_pilot_browser_payload import (
    GATEWAY_MODULE_NAMES,
    MODULE_NAMES,
    browser_command,
    gateway_command,
    make_bundle,
    make_gateway_bundle,
)
from scripts.web_fetch_pilot_command import CommandExecutor
from scripts.web_fetch_pilot_docker import (
    AttachedContainer,
    ContainerPolicy,
    DockerCleanupFailure,
    OfflineContainerDriver,
    OwnedNetwork,
    browser_policy,
    networked_policy,
)
from scripts.web_fetch_pilot_io import FDFrameIO
from scripts.web_fetch_pilot_process import NativeBackend, ProcessCleanupFailure, RawLauncher
from scripts.web_fetch_pilot_results import ResultCollector
from tests.test_web_fetch_pilot_browser_container_io import AttachReader, drain
from tests.test_web_fetch_pilot_gateway_tls_io import HOST, write_certificate

pytestmark = pytest.mark.skipif(
    os.environ.get("DAEMON_DOCKER_TESTS") != "1",
    reason="Opt-in native two-container gate requires DAEMON_DOCKER_TESTS=1",
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "scripts/web_fetch_pilot_browser_seccomp.json"
URL = "https://openai.com/index/daemon-pilot-two-container-fixture/"
VERSION = "pilot-e2-innertext-1"
WRITE_CHUNK = 16 * 1024 + 12
FIXTURE_SERVER = r"""
import json, os, signal, socket, ssl, struct, sys, time
signal.alarm(40)
sys.dont_write_bytecode = True

def read_exact(count):
    data = b""
    while len(data) < count:
        chunk = os.read(0, count - len(data))
        if not chunk:
            raise SystemExit(2)
        data += chunk
    return data

(length,) = struct.unpack(">I", read_exact(4))
material = json.loads(read_exact(length))
paths = {}
for key in ("cert", "key"):
    fd = os.open("/tmp/fixture." + key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, material[key].encode("ascii"))
    os.close(fd)
    paths[key] = "/tmp/fixture." + key
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(paths["cert"], paths["key"])
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("0.0.0.0", 443))
listener.listen(4)
listener.settimeout(1.0)
os.write(1, b'{"ready": true}\n')
report = {"attempts": 0, "handshakes": 0, "failures": []}
deadline, settle = time.monotonic() + 35, None
while time.monotonic() < deadline and (settle is None or time.monotonic() < settle):
    try:
        connection, _ = listener.accept()
    except socket.timeout:
        continue
    report["attempts"] += 1
    connection.settimeout(10)
    try:
        with context.wrap_socket(connection, server_side=True) as tls:
            report["handshakes"] += 1
            tls.sendall(b"echo:" + tls.recv(1024))
    except ssl.SSLError as exc:
        report["failures"].append(str(exc.reason))
    except OSError as exc:
        report["failures"].append(type(exc).__name__)
    finally:
        connection.close()
    settle = time.monotonic() + 2.0
os.write(1, json.dumps(report, sort_keys=True).encode() + b"\n")
"""
FIXTURE_COMMAND = (
    "-i",
    "LANG=C.UTF-8",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "/usr/local/bin/python",
    "-I",
    "-S",
    "-u",
    "-c",
    FIXTURE_SERVER,
)


def trusted(names: tuple[str, ...]) -> dict[str, bytes]:
    return {name: (ROOT / "scripts" / name).read_bytes() for name in names}


async def send(container: AttachedContainer, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        offset += await container.stdin.write(data[offset : offset + WRITE_CHUNK])


async def read_line(container: AttachedContainer, buffer: bytearray, cap: int = 8192) -> bytes:
    while b"\n" not in buffer:
        data = await container.stdout.read(4096)
        if not data:
            break
        buffer.extend(data)
        assert len(buffer) <= cap
    line, _, rest = bytes(buffer).partition(b"\n")
    buffer[:] = rest
    return line


@pytest.mark.asyncio
async def test_native_browser_gateway_fixture_on_internal_network(tmp_path: Path) -> None:
    fds, tasks = set(os.listdir("/proc/self/fd")), set(asyncio.all_tasks())
    config = tmp_path / "docker-config"
    config.mkdir(mode=0o700)
    os.chmod(config, 0o700)
    cert_path, key_path = write_certificate(tmp_path, HOST)
    material = json.dumps(
        {"cert": cert_path.read_text("ascii"), "key": key_path.read_text("ascii")}
    ).encode("ascii")
    run_token = os.urandom(16).hex()
    executor = CommandExecutor(NativeBackend())
    launchers: list[RawLauncher] = []

    def attach() -> RawLauncher:
        launcher = RawLauncher(NativeBackend(), launch_deadline=15.0, close_grace=2.0)
        launchers.append(launcher)
        return launcher

    network = OwnedNetwork(executor, config_dir=str(config), run_token=run_token)
    drivers: list[OfflineContainerDriver] = []
    stderr_tasks: list[asyncio.Task[bytes]] = []
    evidence: dict[str, object] = {}
    try:
        async with asyncio.timeout(150):
            network_id = await network.create()
            net = network.name
            assert net is not None

            def driver(policy: ContainerPolicy) -> OfflineContainerDriver:
                made = OfflineContainerDriver(
                    executor, attach, config_dir=str(config), policy=policy, run_token=run_token
                )
                drivers.append(made)
                return made

            fixture_driver = driver(networked_policy(net, network_id, "fixture", alias=HOST))
            gateway_driver = driver(networked_policy(net, network_id, "gateway"))
            browser_driver = driver(browser_policy(str(PROFILE), PROFILE.read_bytes()))
            for owned in drivers:
                await owned.preflight()

            fixture = await fixture_driver.start(FIXTURE_COMMAND)
            await send(fixture, struct.pack(">I", len(material)) + material)
            fixture.stdin.close()
            fixture_out = bytearray()
            assert json.loads(await read_line(fixture, fixture_out)) == {"ready": True}
            stderr_tasks.append(asyncio.create_task(drain(fixture.stderr)))

            gateway = await gateway_driver.start(gateway_command())
            gateway_run = {"allowed_hosts": [HOST], "inventory": [], "deadline_seconds": 40.0}
            await send(gateway, make_gateway_bundle(trusted(GATEWAY_MODULE_NAMES), gateway_run))
            gateway_errors = asyncio.create_task(drain(gateway.stderr))
            stderr_tasks.append(gateway_errors)

            browser = await browser_driver.start(browser_command())
            expires_at = asyncio.get_running_loop().time() + 45.0
            browser_run = {
                "original_url": URL,
                "allowed_hosts": [HOST],
                "mode": "ordinary",
                "extraction_version": VERSION,
                "deadline_seconds": 30.0,
            }
            await send(browser, make_bundle(trusted(MODULE_NAMES), browser_run))
            browser_errors = asyncio.create_task(drain(browser.stderr))
            stderr_tasks.append(browser_errors)

            collector = ResultCollector(URL, (HOST,), VERSION)
            bridge = IPCBridge(
                FDFrameIO(AttachReader(browser.stdout), browser.stdin),
                FDFrameIO(AttachReader(gateway.stdout), gateway.stdin),
                collector,
                expires_at=expires_at,
            )
            outcome = await bridge.run()
            browser_code = await browser.wait()
            gateway_code = await gateway.wait()
            report = json.loads(await read_line(fixture, fixture_out))
            fixture_code = await fixture.wait()
            diagnostics = {
                "browser": (await asyncio.wait_for(browser_errors, 5)).decode("utf-8", "replace"),
                "gateway": (await asyncio.wait_for(gateway_errors, 5)).decode("utf-8", "replace"),
            }
            for container in (browser, gateway, fixture):
                await container.aclose()
            await network.aclose()
            evidence.update(
                bridge=outcome.reason.value,
                browser_frames=outcome.browser_frames,
                gateway_frames=outcome.gateway_frames,
                exit_codes=[browser_code, gateway_code, fixture_code],
                final_states=[c.final_state for c in (browser, gateway, fixture)],
                fixture=report,
            )
            print("E3C_EVIDENCE", json.dumps(evidence, sort_keys=True))
            print("E3C_DIAGNOSTICS", json.dumps(diagnostics))  # Record only, never decisive.
            assert outcome.reason is BridgeReason.BROWSER_FINAL_EOF, evidence
            assert outcome.candidate is not None and outcome.candidate.content == b""
            assert outcome.candidate.metadata.status == "error"
            assert report["attempts"] >= 1 and report["handshakes"] == 0, evidence
            assert [browser_code, gateway_code, fixture_code] == [0, 0, 0], evidence
            assert all(c.final_state == ("exited", 0) for c in (browser, gateway, fixture))
            assert await browser_driver.owned_ids() == ()
            assert await network.owned_ids() == ()
            assert network.retained is None and not network.cleanup_failed
            assert all(not d.cleanup_failed and d.retained is None for d in drivers)
    finally:
        try:
            for owned in reversed(drivers):
                try:
                    await owned.aclose()
                except DockerCleanupFailure:
                    pass
            try:
                await network.aclose()
            except DockerCleanupFailure:
                pass  # Leftovers stay for the owner; never auto-removed.
        finally:
            for launcher in launchers:
                try:
                    await launcher.aclose()
                except ProcessCleanupFailure:
                    pass
            await executor.aclose()
            await asyncio.gather(*stderr_tasks, return_exceptions=True)
    for _ in range(200):
        if set(os.listdir("/proc/self/fd")) == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert set(os.listdir("/proc/self/fd")) == fds
    assert set(asyncio.all_tasks()) == tasks
