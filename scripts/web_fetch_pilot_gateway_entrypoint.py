"""Gateway-container entrypoint for the pilot; never run on the host.

Staged into the gateway container by the reviewed bootstrap in
``scripts/web_fetch_pilot_browser_payload.py`` (gateway variant) and imported
there under this exact module name. It constructs the UNCHANGED ``Gateway``
with the real ``DNSResolver``/``StdlibSpawner`` and ``NumericConnector``: no
fixture resolver, connector or address exception exists in this path. The
inventory is an explicit trusted run value; an empty inventory is accepted
only as a non-live fixture run and makes no live-readiness claim.

Framed IPC owns duplicated non-inheritable stdin/stdout descriptors; fds 0/1
point at ``/dev/null``/stderr so no DNS helper or stray print can touch frames.
Before any frame is read it refuses (exit 3) unless the run's topology is the
internal one and it runs non-root with only ``eth0``/``lo``, no default route,
seccomp filtering, no-new-privileges and no effective capabilities. Diagnostics are bounded stderr only and
never interpreted by the supervisor. Exit codes: 0 gateway and resolver ended
with clean ownership (never a fetch success claim); 1 cleanup uncertainty;
3 isolation refused.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

RUN_KEYS = frozenset({"allowed_hosts", "inventory", "deadline_seconds", "topology"})
# "internal": owned --internal network, no route out; an empty inventory is allowed
# only here. "egress": any run with a route out; it MUST carry a non-empty owned
# inventory and is refused at runtime until live egress is separately approved.
TOPOLOGIES = ("internal", "egress")
MAX_DEADLINE = 44.0
DIAGNOSTIC_LIMIT = 8192
EXIT_OK, EXIT_CLEANUP, EXIT_ISOLATION = 0, 1, 3


@dataclass(frozen=True)
class GatewayRun:
    allowed_hosts: tuple[str, ...]
    inventory: tuple[str, ...]
    deadline_seconds: float
    topology: str


def parse_run(raw: object) -> GatewayRun:
    """Strict trusted run values; the gateway itself re-validates hosts/inventory."""
    if type(raw) is not dict or set(raw) != RUN_KEYS:
        raise ValueError("run configuration refused")
    hosts, inventory = raw["allowed_hosts"], raw["inventory"]
    if type(hosts) is not list or not 0 < len(hosts) <= 40:
        raise ValueError("run configuration refused")
    if any(type(host) is not str or not host or host != host.lower() for host in hosts):
        raise ValueError("run configuration refused")
    if type(inventory) is not list or len(inventory) > 256:
        raise ValueError("run configuration refused")
    if any(type(item) is not str or not item for item in inventory):
        raise ValueError("run configuration refused")
    deadline = raw["deadline_seconds"]
    if type(deadline) is not float or not 1.0 <= deadline <= MAX_DEADLINE:
        raise ValueError("run configuration refused")
    topology = raw["topology"]
    if topology not in TOPOLOGIES:
        raise ValueError("run configuration refused")
    if topology == "egress":
        from scripts.web_fetch_pilot_core import DestinationPolicyError, parse_owned_inventory

        if not inventory:
            raise ValueError("egress requires a non-empty owned-address inventory")
        try:
            parse_owned_inventory(list(inventory))
        except DestinationPolicyError:
            raise ValueError("run configuration refused") from None
    return GatewayRun(tuple(hosts), tuple(inventory), deadline, topology)


def status_ok(text: str) -> bool:
    """Pure: /proc/self/status shows seccomp filtering, no-new-privs, no capabilities."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    cap_eff = fields.get("CapEff", "")
    return (
        fields.get("Seccomp") == "2"
        and fields.get("NoNewPrivs") == "1"
        and bool(cap_eff)
        and set(cap_eff) <= {"0"}
    )


def routes_ok(table: str, *, egress: bool = False) -> bool:
    """Pure: /proc/net/route rows only on eth0; internal has NO default route, egress
    exactly ONE (via the owned egress network). Egress runs stay refused in ``main``
    until a live session is separately approved."""
    rows = [row.split() for row in table.splitlines()[1:] if row.strip()]
    if not rows or not all(len(row) >= 8 and row[0] == "eth0" for row in rows):
        return False
    defaults = sum(1 for row in rows if row[1] == "00000000")
    return defaults == (1 if egress else 0)


def diagnostic(record: dict[str, object]) -> None:
    line = json.dumps(record, sort_keys=True)[:DIAGNOSTIC_LIMIT]
    os.write(2, line.encode("utf-8", "replace") + b"\n")


def own_frame_descriptors() -> tuple[int, int]:
    """Same discipline as the browser entrypoint: frames on private dups only."""
    frame_in, frame_out = os.dup(0), os.dup(1)
    null = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.dup2(null, 0)
        os.dup2(2, 1)
    finally:
        os.close(null)
    sys.stdout = sys.stderr
    return frame_in, frame_out


def isolation_ok() -> bool:
    with open("/proc/net/route", encoding="ascii") as handle:
        table = handle.read(65536)
    with open("/proc/self/status", encoding="ascii") as handle:
        status = handle.read(65536)
    return (
        os.getuid() != 0
        and sorted(os.listdir("/sys/class/net")) == ["eth0", "lo"]
        and routes_ok(table)
        and status_ok(status)
    )


async def main(raw_run: object) -> int:
    """One bounded gateway run; returns the process exit code."""
    from scripts.web_fetch_pilot_dns import DNSResolver, StdlibSpawner
    from scripts.web_fetch_pilot_gateway import Gateway
    from scripts.web_fetch_pilot_io import AsyncFD, FDFrameIO, NumericConnector

    run = parse_run(raw_run)
    frame_in, frame_out = own_frame_descriptors()
    if run.topology != "internal":
        diagnostic({"refused": "egress topology not enabled"})
        return EXIT_ISOLATION
    if not isolation_ok():
        diagnostic({"refused": "isolation"})
        return EXIT_ISOLATION
    resolver = DNSResolver(StdlibSpawner(sys.executable))
    gateway = Gateway(
        run.allowed_hosts,
        list(run.inventory),
        FDFrameIO(AsyncFD(frame_in), AsyncFD(frame_out)),
        resolver,
        NumericConnector(list(run.inventory)),
        deadline=run.deadline_seconds,
    )
    resolver_failed = False
    try:
        outcome = await gateway.run()
    finally:
        try:
            await resolver.aclose()  # Required on every ending.
        except Exception:
            resolver_failed = True
    resolver_failed = resolver_failed or resolver.cleanup_failed
    diagnostic(
        {
            "reason": outcome.reason.value,
            "read_bytes": outcome.read_bytes,
            "written_bytes": outcome.written_bytes,
            "cleanup_failed": outcome.cleanup_failed,
            "resolver_cleanup_failed": resolver_failed,
        }
    )
    if outcome.cleanup_failed or outcome.pending_tasks or resolver_failed:
        return EXIT_CLEANUP
    return EXIT_OK
