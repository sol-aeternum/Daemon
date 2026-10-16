"""Crawl4AI 0.9.4 Phase 1 offline qualification (opt-in: ``DAEMON_DOCKER_TESTS=1``).

One run token scopes every resource on one owned ``--internal`` network (no
route out, embedded DNS does not forward, no host route):

- a TLS fixture container aliased ``openai.com`` with a throwaway self-signed
  certificate, reporting only handshake outcomes;
- the pinned Crawl4AI 0.9.4 service image ``C4AI_IMAGE`` (its own entrypoint,
  unchanged), aliased ``reader.pilot.test``, with an ephemeral per-run API
  token, in ``shipped`` mode (shipped config.yml, Chromium ``--no-sandbox``)
  or ``sandboxed`` mode (pinned seccomp profile plus the reviewed read-only
  config override that removes only ``--no-sandbox``);
- a stdlib client container that reads the token from stdin and exercises the
  token-authenticated API.

Pass requires: unauthenticated crawl refused (401); authenticated inline HTML
control succeeds; every private target returns the exact structured HTTP 400
application policy denial (transport/auth/internal errors are inconclusive).
Separately, the TLS fixture crawl fails
with the fixture seeing a handshake attempt and completing none (TLS verified
through upstream's egress proxy); metadata, loopback, private and IPv6
loopback targets are policy-denied; identity-checked removal of everything. A
fixture attempt also proves Chromium launched with its sandbox (it refuses to
start otherwise). Only statuses, success flags, short error excerpts and
counts are recorded; no page content. This gate is not OS confinement evidence
for the network-attached reader. Its TLS fixture may be denied at the seed
validator before a handshake on upstream 0.9.4; that is NOT TLS qualification.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_command import CommandExecutor
from scripts.web_fetch_pilot_c4ai_evidence import require_application_evidence
from scripts.web_fetch_pilot_docker import (
    AttachedContainer,
    ContainerPolicy,
    DockerCleanupFailure,
    OfflineContainerDriver,
    OwnedNetwork,
    c4ai_policy,
    networked_policy,
)
from scripts.web_fetch_pilot_process import NativeBackend, ProcessCleanupFailure, RawLauncher
from tests.test_web_fetch_pilot_browser_container_io import drain
from tests.test_web_fetch_pilot_gateway_tls_io import HOST, write_certificate
from tests.test_web_fetch_pilot_two_container_io import read_line, send

pytestmark = pytest.mark.skipif(
    os.environ.get("DAEMON_DOCKER_TESTS") != "1",
    reason="Opt-in Crawl4AI Phase 1 gate requires DAEMON_DOCKER_TESTS=1",
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "scripts/web_fetch_pilot_browser_seccomp.json"
OVERRIDE = ROOT / "scripts/web_fetch_pilot_c4ai_config_sandboxed.yml"
ALIAS = "reader.pilot.test"
URL = "https://openai.com/index/introducing-dots/"  # Resolves only to the fixture here.
PYTHON = ("-i", "LANG=C.UTF-8", "PATH=/usr/local/bin:/usr/bin:/bin", "/usr/local/bin/python")
FIXTURE_SERVER = r"""
import json, os, signal, socket, ssl, struct, sys, time
signal.alarm(280)
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
for key in ("cert", "key"):
    fd = os.open("/tmp/fixture." + key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, material[key].encode("ascii"))
    os.close(fd)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/tmp/fixture.cert", "/tmp/fixture.key")
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("0.0.0.0", 443))
listener.listen(8)
listener.settimeout(1.0)
os.write(1, b'{"ready": true}\n')
report = {"attempts": 0, "handshakes": 0, "failures": []}
deadline = time.monotonic() + 260
while time.monotonic() < deadline and not os.path.exists("/tmp/stop"):
    try:
        connection, _ = listener.accept()
    except socket.timeout:
        continue
    report["attempts"] += 1
    connection.settimeout(10)
    try:
        with context.wrap_socket(connection, server_side=True) as tls:
            report["handshakes"] += 1
    except ssl.SSLError as exc:
        report["failures"].append(str(exc.reason))
    except OSError as exc:
        report["failures"].append(type(exc).__name__)
    finally:
        connection.close()
    if report["attempts"] >= 1:
        deadline = min(deadline, time.monotonic() + 20)
os.write(1, json.dumps(report, sort_keys=True).encode() + b"\n")
"""


@pytest.mark.asyncio
# Sandboxed only: C4AI_IMAGE always sandboxes request-built browsers, so under
# Docker's default seccomp profile (shipped mode) Chromium refuses to start.
@pytest.mark.parametrize("mode", ["sandboxed"])
async def test_native_c4ai_094_isolated_auth_egress_and_tls(tmp_path: Path, mode: str) -> None:
    fds, tasks = set(os.listdir("/proc/self/fd")), set(asyncio.all_tasks())
    config = tmp_path / "docker-config"
    config.mkdir(mode=0o700)
    os.chmod(config, 0o700)
    cert_path, key_path = write_certificate(tmp_path, HOST)
    material = json.dumps(
        {"cert": cert_path.read_text("ascii"), "key": key_path.read_text("ascii")}
    ).encode("ascii")
    run_token, api_token = os.urandom(16).hex(), os.urandom(32).hex()
    executor = CommandExecutor(NativeBackend())
    launchers: list[RawLauncher] = []

    def attach() -> RawLauncher:
        launcher = RawLauncher(NativeBackend(), launch_deadline=15.0, close_grace=2.0)
        launchers.append(launcher)
        return launcher

    network = OwnedNetwork(executor, config_dir=str(config), run_token=run_token)
    drivers: list[OfflineContainerDriver] = []
    drains: list[asyncio.Task[bytes]] = []
    evidence: dict[str, object] = {"mode": mode}
    try:
        async with asyncio.timeout(300):
            network_id = await network.create()
            net = network.name
            assert net is not None

            def driver(policy: ContainerPolicy) -> OfflineContainerDriver:
                made = OfflineContainerDriver(
                    executor, attach, config_dir=str(config), policy=policy, run_token=run_token
                )
                drivers.append(made)
                return made

            sandboxed = mode == "sandboxed"
            reader_policy = c4ai_policy(
                net,
                network_id,
                ALIAS,
                api_token=api_token,
                sandboxed=sandboxed,
                profile_path=str(PROFILE) if sandboxed else None,
                profile=PROFILE.read_bytes() if sandboxed else None,
                config=OVERRIDE.read_bytes() if sandboxed else None,
            )
            fixture_driver = driver(networked_policy(net, network_id, "fixture", alias=HOST))
            reader_driver = driver(reader_policy)
            client_driver = driver(networked_policy(net, network_id, "client"))
            for owned in drivers:
                await owned.preflight()

            fixture = await fixture_driver.start((*PYTHON, "-I", "-S", "-u", "-c", FIXTURE_SERVER))
            await send(fixture, struct.pack(">I", len(material)) + material)
            fixture.stdin.close()
            fixture_out = bytearray()
            assert json.loads(await read_line(fixture, fixture_out)) == {"ready": True}
            drains.append(asyncio.create_task(drain(fixture.stderr)))

            reader: AttachedContainer = await reader_driver.start(())
            reader.stdin.close()
            reader_logs = asyncio.create_task(drain(reader.stderr))
            drains.extend((asyncio.create_task(drain(reader.stdout)), reader_logs))

            client = await client_driver.start(
                (
                    *PYTHON,
                    "-I",
                    "-S",
                    "-u",
                    "-c",
                    (ROOT / "scripts/web_fetch_pilot_c4ai_evidence.py").read_text() + "\nmain()\n",
                    URL,
                )
            )
            await send(client, api_token.encode("ascii") + b"\n")
            client.stdin.close()
            client_errors = asyncio.create_task(drain(client.stderr))
            drains.append(client_errors)
            client_out = bytearray()
            record = json.loads(await read_line(client, client_out, cap=64 * 1024) or b"{}")
            client_code = await client.wait()
            report = json.loads(await read_line(fixture, fixture_out) or b"{}")
            for container in (client, reader, fixture):
                await container.aclose()
            await network.aclose()
            evidence.update(client=record, client_exit=client_code, fixture=report)
            print("C4AI_PHASE1_EVIDENCE", json.dumps(evidence, sort_keys=True))
            logs = await asyncio.wait_for(reader_logs, 5)
            print("C4AI_LOG_TAIL", logs[-3000:].decode("utf-8", "replace"))  # Diagnosis only.
            assert client_code == 0 and record.get("health") == 200, evidence
            require_application_evidence(record)
            assert record["fixture"]["success"] is False, evidence
            assert record["fixture"]["markdown_chars"] == 0, evidence
            assert report.get("attempts", 0) >= 1 and report.get("handshakes") == 0, evidence
            assert all(d.retained is None and not d.cleanup_failed for d in drivers)
            assert network.retained is None and not network.cleanup_failed
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
            await asyncio.gather(*drains, return_exceptions=True)
    for _ in range(400):
        if set(os.listdir("/proc/self/fd")) == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert set(os.listdir("/proc/self/fd")) == fds
    assert set(asyncio.all_tasks()) == tasks
