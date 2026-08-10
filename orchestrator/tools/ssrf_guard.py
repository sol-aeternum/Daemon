"""SSRF defense utilities for outbound HTTP tools.

`HttpRequestTool` lets the LLM make outbound HTTP requests. Without URL
validation and IP-range checks, an attacker (via prompt injection, poisoned
memory, or adversarial document) can instruct the model to call it against
link-local addresses (cloud metadata at 169.254.169.254), loopback (internal
services), RFC1918, ULA, CGNAT, or non-http schemes that some HTTP libraries
still accept. This module is the fail-closed validator: any failure raises
`SsrfViolation` and the caller is expected to translate that into a tool
error rather than letting the request proceed.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import ipaddress
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlparse

# The set of non-publicly-routable ranges. An IP is rejected if it falls in
# any of these networks; the union is the conservative "not safe to connect
# to from this process" set. CIDRs are listed inline so the security boundary
# is auditable at a glance.
_DISALLOWED_IPV4_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)

_DISALLOWED_IPV6_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "::/128",
        "::1/128",
        "64:ff9b::/96",
        "100::/64",
        "2001:db8::/32",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)

MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})
ALLOWED_PORTS: frozenset[int] = frozenset({443})

# DNS lookups are blocking and platform resolvers do not support cancellation.
# Isolate them from asyncio's process-wide default executor so attacker-controlled
# slow lookups cannot starve unrelated backend work. The worker and slot bounds
# cap both active lookups and queued work, including lookups that outlive a caller
# timeout because the platform resolver is still blocked.
_RESOLVER_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ssrf-resolver")
_RESOLVER_SLOTS = asyncio.Semaphore(8)

# IANA only allocates global unicast from 2000::/3. Everything outside it is
# special-use, reserved, or transition machinery (IPv4-compatible, SIIT
# encodings like ::ffff:0:a.b.c.d, etc.) and is rejected outright so oddly
# encoded literals cannot slip past the explicit denylist.
_IPV6_GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")


class SsrfViolation(Exception):
    """Raised when a URL fails SSRF validation.

    Subclasses carry typed failure categories so callers can distinguish
    policy violations (must propagate; chain terminates) from reachability
    failures (target unavailability; fallbacks may still succeed) without
    scanning the exception message text. See ``SsrfPolicyViolation`` and
    ``SsrfUnreachable``.
    """


class SsrfPolicyViolation(SsrfViolation):
    """Raised when the URL is *unsafe* by policy.

    Covers blocked IPs, blocked hostnames, disallowed schemes or ports,
    userinfo, malformed URLs, and undecodable hostnames. Must propagate
    so the strategy chain terminates per the SSRF contract.
    """


class SsrfUnreachable(SsrfViolation):
    """Raised when the target is *unreachable* but not policy-blocked.

    Covers DNS timeouts, gaierrors, no-results, and bounded-resolver
    exhaustion. Callers may swallow this and continue to fallback
    strategies that contact a different host.
    """


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    """A validated URL and the public addresses approved for its connection."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `ip` is loopback, private, link-local, CGNAT, ULA, multicast, etc.

    IPv4-mapped IPv6 (e.g. `::ffff:127.0.0.1`) is unwrapped to its IPv4 form
    so it cannot bypass the IPv4 blocklist. Beyond the auditable inline lists,
    the stdlib `is_global` classification (IANA special-purpose registries) is
    enforced for both families — this catches 6to4 (2002::/16), Teredo
    (2001::/32), documentation, and other special-use ranges the hand-written
    lists do not enumerate — and IPv6 must additionally fall inside the
    2000::/3 global-unicast allocation.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return is_disallowed_ip(mapped)
        if ip not in _IPV6_GLOBAL_UNICAST:
            return True
        if not ip.is_global:
            return True
        networks = _DISALLOWED_IPV6_NETWORKS
    else:
        if not ip.is_global:
            return True
        networks = _DISALLOWED_IPV4_NETWORKS
    return any(ip in net for net in networks)


def _resolve_and_check(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        # gaierror = the target *could not be resolved*. This is target
        # unavailability, not a policy violation — the strategy chain
        # should swallow it so fallbacks (Jina/Archive) can still try.
        raise SsrfUnreachable(f"DNS resolution failed for {host!r}: {exc}") from exc
    except UnicodeError as exc:
        # IDNA encoding rejects URL-valid but DNS-invalid names (e.g. a
        # 64-char label) with UnicodeError, not gaierror. Fail closed.
        raise SsrfPolicyViolation(f"hostname {host!r} is not a valid DNS name: {exc}") from exc
    if not infos:
        raise SsrfUnreachable(f"DNS resolution returned no results for {host!r}")
    addresses: list[str] = []
    for _family, _stype, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise SsrfPolicyViolation(
                f"DNS resolver returned unparseable IP {ip_str!r} for {host!r}"
            ) from exc
        if is_disallowed_ip(ip):
            raise SsrfPolicyViolation(f"hostname {host!r} resolves to blocked IP {ip}")
        normalized = str(ip)
        if normalized not in addresses:
            addresses.append(normalized)
    return tuple(addresses)


def validate_url(
    url: str,
    *,
    allowed_schemes: frozenset[str] = ALLOWED_SCHEMES,
    allowed_ports: frozenset[int] = ALLOWED_PORTS,
) -> str:
    """Validate `url` for SSRF safety. Returns the URL unchanged on success."""
    return validate_url_and_resolve(
        url,
        allowed_schemes=allowed_schemes,
        allowed_ports=allowed_ports,
    ).url


def validate_url_and_resolve(
    url: str,
    *,
    allowed_schemes: frozenset[str] = ALLOWED_SCHEMES,
    allowed_ports: frozenset[int] = ALLOWED_PORTS,
) -> ValidatedUrl:
    """Validate a URL and return the exact public IPs approved for connecting.

    Callers that make the connection should connect to one of ``addresses``
    directly while preserving ``host`` for the HTTP Host header and TLS SNI.
    This closes the DNS-rebinding window without mutating process-global DNS.
    """
    if not url or not isinstance(url, str):
        raise SsrfPolicyViolation("URL is required")
    if len(url) > MAX_URL_LENGTH:
        raise SsrfPolicyViolation(f"URL exceeds {MAX_URL_LENGTH} characters")
    parsed = urlparse(url)
    if parsed.scheme not in allowed_schemes:
        raise SsrfPolicyViolation(
            f"scheme {parsed.scheme or '<empty>'!r} is not allowed "
            f"(allowed: {sorted(allowed_schemes)})"
        )
    if parsed.username is not None or parsed.password is not None:
        raise SsrfPolicyViolation("URLs containing userinfo (user:pass@) are not allowed")
    try:
        host = parsed.hostname
        explicit_port = parsed.port
    except ValueError as exc:
        # urlparse defers netloc validation: malformed ports (e.g. :99999) and
        # invalid IPv6 brackets raise here, not at parse time. Fail closed.
        raise SsrfPolicyViolation(f"malformed host/port in URL: {exc}") from exc
    if not host:
        raise SsrfPolicyViolation("URL is missing a hostname")
    if explicit_port is not None and explicit_port not in allowed_ports:
        raise SsrfPolicyViolation(
            f"port {explicit_port} is not allowed (allowed: {sorted(allowed_ports)})"
        )
    default_port = 80 if parsed.scheme == "http" else 443
    port = explicit_port if explicit_port is not None else default_port
    addresses = _resolve_and_check(host, port)
    return ValidatedUrl(url=url, host=host, port=port, addresses=addresses)


async def validate_url_and_resolve_async(
    url: str,
    *,
    allowed_schemes: frozenset[str] = ALLOWED_SCHEMES,
    allowed_ports: frozenset[int] = ALLOWED_PORTS,
    timeout: float = 10.0,
) -> ValidatedUrl:
    """Validate through the bounded resolver pool without blocking the event loop."""
    validation = functools.partial(
        validate_url_and_resolve,
        url,
        allowed_schemes=allowed_schemes,
        allowed_ports=allowed_ports,
    )
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(_RESOLVER_SLOTS.acquire(), timeout=timeout)
    except TimeoutError as exc:
        raise SsrfUnreachable(f"DNS validation timed out for {url}") from exc

    try:
        future = loop.run_in_executor(_RESOLVER_EXECUTOR, validation)
    except BaseException:
        _RESOLVER_SLOTS.release()
        raise

    # Shield the resolver future so a caller timeout does not cancel the only
    # completion signal that releases capacity after the blocking lookup ends.
    future.add_done_callback(lambda _future: _RESOLVER_SLOTS.release())
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    except TimeoutError as exc:
        raise SsrfUnreachable(f"DNS validation timed out for {url}") from exc


def _load_allowed_domains() -> frozenset[str]:
    """Load the egress allowlist from `DAEMON_HTTP_ALLOWED_DOMAINS`.

    Comma-separated, lowercase. Empty/unset means no allowlist filter is
    applied (only the IP-range check). Wildcards use `*.example.com` form.
    """
    raw = os.environ.get("DAEMON_HTTP_ALLOWED_DOMAINS", "")
    if not raw.strip():
        return frozenset()
    return frozenset(d.strip().lower() for d in raw.split(",") if d.strip())


def _domain_matches(host: str, allowed: str) -> bool:
    host = host.lower()
    allowed = allowed.lower()
    if allowed.startswith("*."):
        suffix = allowed[1:]
        bare = suffix[1:]
        return host.endswith(suffix) and host != bare
    return host == allowed


def check_egress_allowlist(host: str) -> None:
    """Reject `host` if the egress allowlist is set and `host` is not in it."""
    allowed = _load_allowed_domains()
    if not allowed:
        return
    for pattern in allowed:
        if _domain_matches(host, pattern):
            return
    raise SsrfPolicyViolation(f"hostname {host!r} is not in the egress allowlist")


# socket_guard() patches process-global state, so overlapping requests must
# share one installation: a naive save/restore pair would let the first
# request's exit restore the unguarded resolver while the second request is
# still in flight. The guard is therefore reference-counted under a lock and
# only installed/removed at the 0<->1 transitions.
_guard_lock = threading.Lock()
_guard_depth = 0
_real_getaddrinfo: Any = None


def _guarded_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
    real = _real_getaddrinfo
    assert real is not None
    # httpx/anyio passes the connect-time hostname as IDNA-encoded bytes, not
    # str — both forms MUST be validated or the rebinding guard is a no-op
    # for ordinary hostnames.
    lookup = host
    if isinstance(lookup, (bytes, bytearray)):
        try:
            lookup = lookup.decode("ascii")
        except UnicodeDecodeError as exc:
            raise SsrfPolicyViolation(f"undecodable hostname {host!r}") from exc
    if isinstance(lookup, str):
        try:
            literal = ipaddress.ip_address(lookup)
        except ValueError:
            literal = None
        if literal is not None and is_disallowed_ip(literal):
            raise SsrfPolicyViolation(f"literal IP {literal} is not allowed")
        if literal is None:
            infos = real(host, *args, **kwargs)
            for _family, _stype, _proto, _canon, sockaddr in infos:
                resolved = ipaddress.ip_address(sockaddr[0])
                if is_disallowed_ip(resolved):
                    raise SsrfPolicyViolation(
                        f"hostname {host!r} resolves to blocked IP {resolved} "
                        f"on connect (DNS-rebinding protection)"
                    )
            return infos
    return real(host, *args, **kwargs)


@contextlib.contextmanager
def socket_guard() -> Iterator[None]:
    """Patch `socket.getaddrinfo` for the duration of a request.

    Every DNS lookup (initial validation, connect-time resolution, retries)
    is forced through a validator that rejects non-public IPs. Re-entrant and
    safe under overlapping requests; the original `getaddrinfo` is restored
    when the last concurrent guard exits, even if the wrapped block raises.
    """
    global _guard_depth, _real_getaddrinfo
    with _guard_lock:
        _guard_depth += 1
        if _guard_depth == 1:
            _real_getaddrinfo = socket.getaddrinfo
            socket.getaddrinfo = _guarded_getaddrinfo
    try:
        yield
    finally:
        with _guard_lock:
            _guard_depth -= 1
            if _guard_depth == 0:
                socket.getaddrinfo = _real_getaddrinfo
                _real_getaddrinfo = None
