"""Stage E3b internal-network probe: REVIEW BEFORE FIRST EXECUTION.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_network_probe_io.py``;
even then it is opt-in (``DAEMON_DOCKER_TESTS=1``).

Creates ONE owned ``--internal`` network (``1.2.3.0/29``, isolated gateway
mode, masquerade off) and ONE owned gateway-limit container on it aliased
``openai.com``, both stamped with this run's token. The container runs an
inert stdlib program that sends raw DNS queries only to Docker's embedded
resolver (``127.0.0.11``): first ``daemon-pilot-probe.invalid``; only if that
is NOT answered NXDOMAIN (which would suggest upstream forwarding) does it ask
for the alias. It also reports its interfaces, routes, nameservers and own
address (from a UDP ``connect``, which sends nothing). The host side checks,
read-only, that the host gained no ``1.2.3.x`` route or address.

Worst case, if Docker forwards on internal networks, one query naming
``daemon-pilot-probe.invalid`` reaches an upstream resolver. No other
external query, connection or live URL is possible from this probe.
Leftovers are reported, never auto-removed.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_command import CommandExecutor, CommandResult
from scripts.web_fetch_pilot_container_policy import NETWORK_SUBNET
from scripts.web_fetch_pilot_docker import (
    DockerCleanupFailure,
    DockerLifecycleFailure,
    OfflineContainerDriver,
    OwnedNetwork,
    networked_policy,
)
from scripts.web_fetch_pilot_process import (
    NativeBackend,
    ProcessCleanupFailure,
    RawByteChannel,
    RawLauncher,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("DAEMON_DOCKER_TESTS") != "1",
    reason="Opt-in native internal-network probe requires DAEMON_DOCKER_TESTS=1",
)

ALIAS = "openai.com"
PROBE = r"""
import json, os, signal, socket, struct, sys
signal.alarm(20)
sys.dont_write_bytecode = True

def query(name):
    ident = int.from_bytes(os.urandom(2), "big")
    question = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split(".")) + b"\0"
    packet = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0) + question + b"\0\1\0\1"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(packet, ("127.0.0.11", 53))
        try:
            data = sock.recv(512)
        except socket.timeout:
            return {"rcode": "timeout", "answers": []}
    rid, flags, _, ancount, _, _ = struct.unpack(">HHHHHH", data[:12])
    if rid != ident:
        return {"rcode": "mismatch", "answers": []}
    offset, answers = 12 + len(question) + 4, []
    for _ in range(ancount):
        offset += 2 if data[offset] & 0xC0 == 0xC0 else data.index(b"\0", offset) + 1 - offset
        kind, _, _, length = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        if kind == 1 and length == 4:
            answers.append(socket.inet_ntoa(data[offset:offset + 4]))
        offset += length
    return {"rcode": flags & 0xF, "answers": answers}

with open("/etc/resolv.conf") as handle:
    nameservers = [line.split()[1] for line in handle if line.startswith("nameserver")]
with open("/proc/net/route") as handle:
    routes = [row.split()[1:3] + [row.split()[7]] for row in handle.read().splitlines()[1:] if row]
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
    probe.connect(("1.2.3.1", 9))
    own = probe.getsockname()[0]
record = {
    "nameservers": nameservers,
    "interfaces": sorted(os.listdir("/sys/class/net")),
    "routes": routes,
    "own": own,
    "invalid": query("daemon-pilot-probe.invalid"),
}
if record["invalid"]["rcode"] != 3:
    record["alias"] = query("openai.com")
os.write(1, json.dumps(record, sort_keys=True).encode() + b"\n")
"""
COMMAND = (
    "-i",
    "LANG=C.UTF-8",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "/usr/local/bin/python",
    "-I",
    "-S",
    "-u",
    "-c",
    PROBE,
)
WHITELIST = ("NetworkMode", "Networks", "Options", "IPAM", "Internal", "EnableIPv6")


class RecordingRunner:
    """Real executor; keeps the last inspect output for field-name diagnosis."""

    def __init__(self, executor: CommandExecutor) -> None:
        self._executor = executor
        self.last_inspect: bytes | None = None

    async def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        stdin: bytes = b"",
        timeout: float,
    ) -> CommandResult:
        outcome = await self._executor.run(argv, env, stdin=stdin, timeout=timeout)
        if "inspect" in argv[3:5] and outcome.returncode == 0:
            self.last_inspect = outcome.stdout
        return outcome


def whitelisted(raw: bytes | None) -> dict[str, object]:
    """Configuration fields only (never Env/Cmd/Labels values) for diagnosis."""
    if raw is None:
        return {}
    record = json.loads(raw)[0]
    picked: dict[str, object] = {}
    for section in (record, record.get("HostConfig") or {}, record.get("NetworkSettings") or {}):
        for key in WHITELIST:
            if key in section:
                picked[key] = section[key]
    return picked


def host_has_subnet() -> bool:
    """Read-only host view: any route to 1.2.3.0/29 or any local 1.2.3.x address."""
    routes = Path("/proc/net/route").read_text().splitlines()[1:]
    route = any(row.split()[1] == "00030201" for row in routes if row)
    return route or "1.2.3." in Path("/proc/net/fib_trie").read_text()


async def read_all(channel: RawByteChannel, cap: int) -> bytes:
    received = bytearray()
    while data := await channel.read(16 * 1024):
        received.extend(data)
        assert len(received) <= cap
    return bytes(received)


@pytest.mark.asyncio
async def test_native_internal_network_dns_and_host_route_probe(tmp_path: Path) -> None:
    assert not host_has_subnet()  # Nothing on the host claims the range beforehand.
    fds, tasks = set(os.listdir("/proc/self/fd")), set(asyncio.all_tasks())
    config = tmp_path / "docker-config"
    config.mkdir(mode=0o700)
    os.chmod(config, 0o700)
    executor = CommandExecutor(NativeBackend())
    runner = RecordingRunner(executor)
    run_token = os.urandom(16).hex()
    network = OwnedNetwork(runner, config_dir=str(config), run_token=run_token)
    launchers: list[RawLauncher] = []

    def attach() -> RawLauncher:
        launcher = RawLauncher(NativeBackend(), launch_deadline=15.0, close_grace=2.0)
        launchers.append(launcher)
        return launcher

    driver: OfflineContainerDriver | None = None
    evidence: dict[str, object] = {}
    try:
        async with asyncio.timeout(90):
            try:
                network_id = await network.create()
            except DockerLifecycleFailure:
                print("E3B_NETWORK_REFUSED", json.dumps(whitelisted(runner.last_inspect)))
                raise
            name = network.name
            assert name is not None
            evidence["host_subnet_during"] = host_has_subnet()
            policy = networked_policy(name, network_id, "fixture", alias=ALIAS)
            driver = OfflineContainerDriver(
                runner, attach, config_dir=str(config), policy=policy, run_token=run_token
            )
            await driver.preflight()
            try:
                container = await driver.start(COMMAND)
            except DockerLifecycleFailure:
                print("E3B_CONTAINER_REFUSED", json.dumps(whitelisted(runner.last_inspect)))
                raise
            container.stdin.close()
            out, err = await asyncio.gather(
                read_all(container.stdout, 8192), read_all(container.stderr, 65536)
            )
            code = await container.wait()
            await container.aclose()
            assert await driver.owned_ids() == ()
            await network.aclose()
            evidence.update(json.loads(out) if out else {}, exit_code=code, stderr_bytes=len(err))
            evidence["host_subnet_after"] = host_has_subnet()
            print("E3B_EVIDENCE", json.dumps(evidence, sort_keys=True))
    finally:
        try:
            if driver is not None:
                try:
                    await driver.aclose()
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
    assert code == 0 and container.final_state == ("exited", 0), evidence
    assert evidence["nameservers"] == ["127.0.0.11"], evidence
    assert evidence["interfaces"] == ["eth0", "lo"], evidence
    assert all(route[0] != "00000000" for route in evidence["routes"]), evidence  # type: ignore[union-attr]
    assert evidence["invalid"]["rcode"] not in (0, 3), evidence  # type: ignore[index]
    own = evidence["own"]
    assert isinstance(own, str) and own.startswith("1.2.3."), evidence
    assert evidence["alias"] == {"rcode": 0, "answers": [own]}, evidence
    assert evidence["host_subnet_during"] is False and evidence["host_subnet_after"] is False
    assert network.retained is None and not network.cleanup_failed
    assert NETWORK_SUBNET == "1.2.3.0/29"
    for _ in range(200):
        if set(os.listdir("/proc/self/fd")) == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert set(os.listdir("/proc/self/fd")) == fds
    assert set(asyncio.all_tasks()) == tasks
