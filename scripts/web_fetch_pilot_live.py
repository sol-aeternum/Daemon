"""Pure live-session support for the pilot: inventory and content-free evidence.

Nothing here opens a socket, runs a process or touches Docker. It exists so a
future, separately approved live session can (1) hand the gateway a fresh
deployment-owned address inventory that is never empty, and (2) record evidence
that never contains page text, cookies, query strings or response bodies.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from scripts.web_fetch_pilot_core import DestinationPolicyError, parse_owned_inventory

MAX_INVENTORY = 256


class InventoryRefused(ValueError):
    """The inventory is missing, malformed or too large; a live run must not start."""


def local_ipv4_addresses(fib_trie: str) -> tuple[str, ...]:
    """Pure: host-local IPv4 addresses from ``/proc/net/fib_trie`` text.

    Each ``/32 host LOCAL`` entry follows the ``|-- a.b.c.d`` line naming it.
    Loopback is excluded (the gateway denies it by policy anyway).
    """
    found: set[str] = set()
    previous = ""
    for line in fib_trie.splitlines():
        stripped = line.strip()
        if stripped.startswith("/32 host LOCAL") and previous:
            address = ipaddress.ip_address(previous)
            if isinstance(address, ipaddress.IPv4Address) and not address.is_loopback:
                found.add(str(address))
        if stripped.startswith("|-- "):
            candidate = stripped[4:].strip()
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                previous = ""
            else:
                previous = candidate
    return tuple(sorted(found, key=lambda value: ipaddress.ip_address(value)))


@dataclass(frozen=True)
class Inventory:
    entries: tuple[str, ...]
    sha256: str
    gathered_at: float  # Unix time; freshness is the caller's run-start bound.


def build_inventory(
    local_addresses: Sequence[str], owner_addresses: Sequence[str], *, now: float | None = None
) -> Inventory:
    """Merge host-local and owner-supplied deployment-owned addresses.

    ``owner_addresses`` (this machine's external NAT address and every
    production deployment address/prefix) cannot be discovered reliably and
    MUST be supplied; an empty owner list is refused. Every entry is checked
    with the gateway's own ``parse_owned_inventory``.
    """
    if type(owner_addresses) not in (list, tuple) or not owner_addresses:
        raise InventoryRefused("owner-supplied deployment addresses are required")
    if type(local_addresses) not in (list, tuple):
        raise InventoryRefused("local addresses must be a sequence")
    merged = sorted({*map(str, local_addresses), *map(str, owner_addresses)})
    if len(merged) > MAX_INVENTORY:
        raise InventoryRefused("inventory exceeds bound")
    try:
        parse_owned_inventory(merged)
    except DestinationPolicyError:
        raise InventoryRefused("inventory entry refused") from None
    if not any(_is_ipv4(entry) for entry in owner_addresses):
        raise InventoryRefused("at least one owner-supplied IPv4 address/prefix required")
    digest = hashlib.sha256("\n".join(merged).encode("ascii")).hexdigest()
    return Inventory(tuple(merged), digest, time.time() if now is None else now)


def _is_ipv4(entry: object) -> bool:
    try:
        return isinstance(ipaddress.ip_network(str(entry), strict=False), ipaddress.IPv4Network)
    except ValueError:
        return False


def url_evidence(url: str) -> dict[str, str]:
    """Host plus a hash of the full URL; never the path or query string."""
    host = urlsplit(url).hostname or ""
    return {"host": host, "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest()}


def run_evidence(
    *,
    mode: str,
    url: str,
    result_status: str | None,
    content: bytes,
    exit_codes: Mapping[str, int | None],
    counters: Mapping[str, int],
    hashes: Mapping[str, str],
    inventory: Inventory | None,
) -> dict[str, object]:
    """Content-free record of one live run; refuses non-scalar counter values.

    Content is reduced to its byte length and SHA-256 and then forgotten.
    """
    if any(type(value) is not int for value in counters.values()):
        raise ValueError("counters must be integers")
    if any(type(value) is not str or len(value) != 64 for value in hashes.values()):
        raise ValueError("hashes must be hex SHA-256 strings")
    record: dict[str, object] = {
        "mode": mode,
        **url_evidence(url),
        "result_status": result_status,
        "content_bytes": len(content),
        "content_sha256": hashlib.sha256(content).hexdigest() if content else None,
        "exit_codes": dict(exit_codes),
        "counters": dict(counters),
        "hashes": dict(hashes),
        "inventory_sha256": None if inventory is None else inventory.sha256,
        "inventory_entries": None if inventory is None else len(inventory.entries),
    }
    json.dumps(record)  # Must stay plain, serializable data.
    return record
