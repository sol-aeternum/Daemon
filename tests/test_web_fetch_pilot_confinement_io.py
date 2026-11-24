"""Live prerequisite 4: browser network-layer confinement probes. REVIEW FIRST.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_confinement_io.py``;
even then it is opt-in (``DAEMON_DOCKER_TESTS=1``).

One owned browser-policy container (network none, pinned seccomp profile,
browser limits, ``appuser``) runs a stdlib-only probe program instead of the
browser bootstrap. It attempts direct TCP to public, metadata, Docker-bridge,
LAN and egress-gateway addresses, UDP sends standing in for DNS and QUIC, a
name lookup and IPv6 TCP. With only loopback and no routes every attempt must
fail locally (no packet can leave a network-none namespace); any "connected"
or "sent" result fails the gate. Only outcome categories are recorded.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_command import CommandExecutor
from scripts.web_fetch_pilot_docker import (
    DockerCleanupFailure,
    OfflineContainerDriver,
    browser_policy,
)
from scripts.web_fetch_pilot_process import NativeBackend, ProcessCleanupFailure, RawLauncher
from tests.test_web_fetch_pilot_browser_container_io import drain

pytestmark = pytest.mark.skipif(
    os.environ.get("DAEMON_DOCKER_TESTS") != "1",
    reason="Opt-in native confinement probe requires DAEMON_DOCKER_TESTS=1",
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "scripts/web_fetch_pilot_browser_seccomp.json"
PROBE = r"""
import errno, json, os, signal, socket, sys
signal.alarm(20)
sys.dont_write_bytecode = True
TCP = {"public": ("1.1.1.1", 443), "metadata": ("169.254.169.254", 80),
       "docker_bridge": ("172.17.0.1", 443), "lan": ("192.168.20.1", 443),
       "egress_gateway": ("10.251.248.1", 443)}
UDP = {"dns": ("8.8.8.8", 53), "quic": ("1.1.1.1", 443)}

def category(exc):
    if isinstance(exc, socket.timeout):
        return "timeout"
    if isinstance(exc, socket.gaierror):
        return "no_resolution"
    return errno.errorcode.get(exc.errno, "error") if getattr(exc, "errno", None) else "error"

record = {"interfaces": sorted(os.listdir("/sys/class/net"))}
with open("/proc/net/route") as handle:
    record["route_rows"] = sum(1 for row in handle.read().splitlines()[1:] if row.strip())
for name, address in TCP.items():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        try:
            sock.connect(address)
            record["tcp_" + name] = "connected"
        except OSError as exc:
            record["tcp_" + name] = category(exc)
for name, address in UDP.items():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.sendto(b"\0" * 12, address)
            record["udp_" + name] = "sent"
        except OSError as exc:
            record["udp_" + name] = category(exc)
try:
    socket.getaddrinfo("example.com", 443)
    record["lookup"] = "resolved"
except OSError as exc:
    record["lookup"] = category(exc)
try:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(("2606:4700:4700::1111", 443))
        record["tcp6_public"] = "connected"
except OSError as exc:
    record["tcp6_public"] = category(exc)
os.write(1, json.dumps(record, sort_keys=True).encode() + b"\n")
"""
COMMAND = (
    "-i",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "LANG=C.UTF-8",
    "/usr/local/bin/python",
    "-I",
    "-S",
    "-u",
    "-c",
    PROBE,
)
FORBIDDEN = {"connected", "sent", "resolved"}


@pytest.mark.asyncio
async def test_native_browser_container_cannot_reach_any_network(tmp_path: Path) -> None:
    fds, tasks = set(os.listdir("/proc/self/fd")), set(asyncio.all_tasks())
    config = tmp_path / "docker-config"
    config.mkdir(mode=0o700)
    os.chmod(config, 0o700)
    executor = CommandExecutor(NativeBackend())
    launchers: list[RawLauncher] = []

    def attach() -> RawLauncher:
        launcher = RawLauncher(NativeBackend(), launch_deadline=15.0, close_grace=2.0)
        launchers.append(launcher)
        return launcher

    driver = OfflineContainerDriver(
        executor,
        attach,
        config_dir=str(config),
        policy=browser_policy(str(PROFILE), PROFILE.read_bytes()),
    )
    try:
        async with asyncio.timeout(90):
            await driver.preflight()
            assert await driver.owned_ids() == ()
            container = await driver.start(COMMAND)
            container.stdin.close()
            out, _ = await asyncio.gather(drain(container.stdout), drain(container.stderr))
            code = await container.wait()
            await container.aclose()
            assert await driver.owned_ids() == ()
        record = json.loads(out)
        print("CONFINEMENT_EVIDENCE", json.dumps(record, sort_keys=True))
        assert code == 0 and container.final_state == ("exited", 0), record
        assert record["interfaces"] == ["lo"] and record["route_rows"] == 0, record
        outcomes = {k: v for k, v in record.items() if k not in ("interfaces", "route_rows")}
        assert len(outcomes) == 9 and not FORBIDDEN & set(outcomes.values()), record
    finally:
        try:
            try:
                await driver.aclose()
            except DockerCleanupFailure:
                pass  # Leftovers stay for the owner; never auto-removed.
        finally:
            for launcher in launchers:
                try:
                    await launcher.aclose()
                except ProcessCleanupFailure:
                    pass
            await executor.aclose()
    for _ in range(200):
        if set(os.listdir("/proc/self/fd")) == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert set(os.listdir("/proc/self/fd")) == fds
    assert set(asyncio.all_tasks()) == tasks
