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

import contextlib
import ipaddress
import os
import socket
import threading
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

# IANA only allocates global unicast from 2000::/3. Everything outside it is
# special-use, reserved, or transition machinery (IPv4-compatible, SIIT
# encodings like ::ffff:0:a.b.c.d, etc.) and is rejected outright so oddly
# encoded literals cannot slip past the explicit denylist.
_IPV6_GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")


class SsrfViolation(Exception):
    """Raised when a URL fails SSRF validation."""


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


def _resolve_and_check(host: str, port: int) -> None:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if is_disallowed_ip(literal):
            raise SsrfViolation(f"literal IP {literal} is not allowed")
        return

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SsrfViolation(f"DNS resolution failed for {host!r}: {exc}") from exc
    except UnicodeError as exc:
        # IDNA encoding rejects URL-valid but DNS-invalid names (e.g. a
        # 64-char label) with UnicodeError, not gaierror. Fail closed.
        raise SsrfViolation(f"hostname {host!r} is not a valid DNS name: {exc}") from exc
    if not infos:
        raise SsrfViolation(f"DNS resolution returned no results for {host!r}")
    for _family, _stype, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise SsrfViolation(
                f"DNS resolver returned unparseable IP {ip_str!r} for {host!r}"
            ) from exc
        if is_disallowed_ip(ip):
            raise SsrfViolation(f"hostname {host!r} resolves to blocked IP {ip}")


def validate_url(url: str) -> str:
    """Validate `url` for SSRF safety. Returns the URL unchanged on success.

    Raises SsrfViolation on unsupported scheme, disallowed port, userinfo,
    length over MAX_URL_LENGTH, missing hostname, or non-public resolution.
    This is the pre-flight check; `socket_guard` re-validates at connect time.
    """
    if not url or not isinstance(url, str):
        raise SsrfViolation("URL is required")
    if len(url) > MAX_URL_LENGTH:
        raise SsrfViolation(f"URL exceeds {MAX_URL_LENGTH} characters")
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SsrfViolation(
            f"scheme {parsed.scheme or '<empty>'!r} is not allowed "
            f"(allowed: {sorted(ALLOWED_SCHEMES)})"
        )
    if parsed.username is not None or parsed.password is not None:
        raise SsrfViolation("URLs containing userinfo (user:pass@) are not allowed")
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        # urlparse defers netloc validation: malformed ports (e.g. :99999) and
        # invalid IPv6 brackets raise here, not at parse time. Fail closed.
        raise SsrfViolation(f"malformed host/port in URL: {exc}") from exc
    if not host:
        raise SsrfViolation("URL is missing a hostname")
    if port is not None and port not in ALLOWED_PORTS:
        raise SsrfViolation(f"port {port} is not allowed (allowed: {sorted(ALLOWED_PORTS)})")
    _resolve_and_check(host, port if port is not None else 443)
    return url


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
    raise SsrfViolation(f"hostname {host!r} is not in the egress allowlist")


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
            raise SsrfViolation(f"undecodable hostname {host!r}") from exc
    if isinstance(lookup, str):
        try:
            literal = ipaddress.ip_address(lookup)
        except ValueError:
            literal = None
        if literal is not None and is_disallowed_ip(literal):
            raise SsrfViolation(f"literal IP {literal} is not allowed")
        if literal is None:
            infos = real(host, *args, **kwargs)
            for _family, _stype, _proto, _canon, sockaddr in infos:
                resolved = ipaddress.ip_address(sockaddr[0])
                if is_disallowed_ip(resolved):
                    raise SsrfViolation(
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
