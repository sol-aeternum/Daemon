"""Direct fetch strategy implementation."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from orchestrator.services.fetch.models import FetchResult, FetchPolicy
from orchestrator.tools.ssrf_guard import (
    SsrfPolicyViolation,
    SsrfUnreachable,
    SsrfViolation,
    validate_url_and_resolve_async,
)

logger = logging.getLogger(__name__)

_FETCH_SCHEMES = frozenset({"http", "https"})
_FETCH_PORTS = frozenset({80, 443})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5

# Per-address socket timeout. Stays modest so a single silent drop cannot
# consume the entire per-fetch budget on its own.
_PER_ADDRESS_TIMEOUT_SECONDS = 5.0

# Hard upper bound on the total time spent iterating across addresses for one
# hop, including connect + read. Prevents an attacker-controlled DNS answer
# with many silently-dropping addresses from keeping a fetch alive for
# ``timeout × address_count × hops``.
_PER_FETCH_DEADLINE_SECONDS = 15.0

# Maximum number of distinct validated addresses to attempt per hop. Caps the
# work a hostile DNS response with thousands of entries could otherwise force.
_MAX_ADDRESS_ATTEMPTS = 4


def _encode_idna(host: str) -> str:
    """Convert a Unicode hostname to its ASCII (IDNA) form.

    Returns ``host`` unchanged when it is already ASCII. Raises ``ValueError``
    if the hostname cannot be encoded (httpx requires ASCII header values).
    """
    if host.isascii():
        return host
    return host.encode("idna").decode("ascii")


def _host_header(url: str) -> str:
    """Build an ASCII Host header while preserving brackets and an explicit port."""
    parsed = urlsplit(url)
    host = _encode_idna(parsed.hostname or "")
    authority_host = f"[{host}]" if ":" in host else host
    return f"{authority_host}:{parsed.port}" if parsed.port is not None else authority_host


def _pin_url_to_address(url: str, address: str) -> str:
    """Replace only the URL authority host with an already-validated IP."""
    parsed = urlsplit(url)
    address_authority = f"[{address}]" if ":" in address else address
    if parsed.port is not None:
        address_authority = f"{address_authority}:{parsed.port}"
    return urlunsplit(parsed._replace(netloc=address_authority))


def _is_blocked_domain(url: str, blocked_domains: list[str]) -> bool:
    """Return whether URL host exactly matches or is below a blocked domain.

    Both the requested URL host and the configured ``blocked_domains`` entries
    are canonicalized via IDNA + lowercase + trailing-dot stripping before
    comparison. This prevents DNS-equivalent forms (``example.com.``) and
    Unicode/Punycode variants (``bücher.example`` vs
    ``xn--bcher-kva.example``) from bypassing the operator's policy.
    """
    hostname = urlsplit(url).hostname or ""
    try:
        normalized_host = _canonicalize_hostname(hostname)
    except UnicodeError:
        return False
    if not normalized_host:
        return False
    for blocked in blocked_domains:
        try:
            normalized = _canonicalize_hostname(blocked)
        except UnicodeError:
            continue
        if not normalized:
            continue
        if normalized_host == normalized or normalized_host.endswith(f".{normalized}"):
            return True
    return False


def _canonicalize_hostname(hostname: str) -> str:
    """Return a lowercase, trailing-dot-stripped IDNA form of ``hostname``.

    Unicode (``bücher.example``) and Punycode (``xn--bcher-kva.example``) both
    collapse to the same ASCII IDNA representation so policy comparisons
    agree regardless of how the operator or the request spelled the name.
    Raises ``UnicodeError`` if the hostname cannot be encoded as IDNA.
    """
    if not hostname:
        return ""
    lowered = hostname.lower().rstrip(".")
    if not lowered:
        return ""
    if lowered.isascii():
        return lowered
    return lowered.encode("idna").decode("ascii").rstrip(".")


# Common browser user agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.2210.91 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.2210.91",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.2210.91",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Android 14; Mobile; rv:109.0) Gecko/114.0 Firefox/114.0",
]


class DirectFetchStrategy:
    """Direct HTTP fetch strategy using httpx with user agent rotation."""

    def __init__(self, policy: FetchPolicy) -> None:
        self.policy: FetchPolicy = policy

    async def fetch(self, url: str) -> FetchResult | None:
        """
        Fetch content from URL using direct HTTP request.

        Args:
            url: URL to fetch

        Returns:
            FetchResult with content or None if fetch failed
        """
        # Select random user agent
        user_agent = random.choice(USER_AGENTS)

        try:
            current_url = url
            response: httpx.Response | None = None
            # One shared deadline spans every redirect hop. Without this,
            # each hop would reset the budget and a server with six hops
            # could consume ``_PER_FETCH_DEADLINE_SECONDS × hops`` of
            # wall time on silently-dropping addresses.
            deadline_at = time.monotonic() + _PER_FETCH_DEADLINE_SECONDS
            # Shared across every per-address ``AsyncClient`` *and* every
            # redirect hop so Set-Cookie responses from one hop can drive
            # the Cookie header of the next hop. Per-address clients are
            # still created fresh — only the cookie jar is shared, not the
            # connection pool. (P2 finding from Codex review: sites that
            # set a cookie on a redirect and require it at the destination
            # were losing the cookie when the per-address client was
            # destroyed after each hop.)
            shared_cookies = httpx.Cookies()
            for redirect_count in range(_MAX_REDIRECTS + 1):
                # ``remaining`` is recomputed at the top of every hop and
                # passed down to both DNS validation and the address
                # fallback. Without this, ``validate_url_and_resolve_async``
                # would grant each redirect hop its own independent
                # ``timeout`` budget and the shared deadline would not
                # actually bound total wall time.
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    logger.info(
                        "Direct fetch abandoning %s: shared per-fetch deadline already exhausted",
                        current_url,
                    )
                    return None
                if _is_blocked_domain(current_url, self.policy.blocked_domains):
                    raise SsrfPolicyViolation(f"hostname is blocked by fetch policy: {current_url}")
                try:
                    validated = await validate_url_and_resolve_async(
                        current_url,
                        allowed_schemes=_FETCH_SCHEMES,
                        allowed_ports=_FETCH_PORTS,
                        timeout=remaining,
                    )
                except SsrfUnreachable as exc:
                    # Target unreachable (DNS timeout / gaierror / no
                    # results / bounded resolver exhaustion). The direct
                    # path could not connect, but fallbacks that contact
                    # r.jina.ai / archive.org instead of the target host
                    # may still succeed. Swallow and let the chain
                    # continue.
                    logger.info(
                        "Direct fetch unavailable for %s: %s; "
                        "fallback strategies may still succeed",
                        current_url,
                        exc,
                    )
                    return None
                except SsrfPolicyViolation:
                    # URL is unsafe by policy (blocked IP, blocked host,
                    # disallowed scheme/port, userinfo, malformed host,
                    # undecodable hostname). The strategy chain must
                    # terminate so the caller does not try a fallback
                    # that may itself bypass policy.
                    raise
                if not validated.addresses:
                    # No validated addresses is a target unavailability, not
                    # a safety violation — same treatment as DNS failure.
                    logger.info(
                        "Direct fetch no validated addresses for %s; "
                        "fallback strategies may still succeed",
                        url,
                    )
                    return None
                # httpx requires ASCII header values; convert IDN hosts to IDNA
                # so Unicode hostnames (e.g. bücher.example) don't raise
                # UnicodeEncodeError when constructing the request. Preserve an
                # explicit default port and IPv6 brackets in the Host header.
                try:
                    host_header = _host_header(current_url)
                    sni_hostname = _encode_idna(validated.host)
                except (ValueError, UnicodeError) as exc:
                    logger.warning(f"Direct fetch cannot encode hostname for {url}: {exc}")
                    return None
                # Use one client per address in each hop. A pooled connection is
                # keyed by the pinned-IP origin, so reusing it across hostnames
                # that share a CDN address could reuse the wrong TLS/SNI session.
                response = await self._request_with_address_fallback(
                    current_url=current_url,
                    addresses=validated.addresses,
                    host_header=host_header,
                    sni_hostname=sni_hostname,
                    user_agent=user_agent,
                    deadline_at=deadline_at,
                    cookies=shared_cookies,
                )
                if response is None:
                    # Every validated address was unreachable. Return ``None``
                    # so FetchService can continue to its next strategy.
                    return None
                if response.status_code not in _REDIRECT_STATUSES:
                    break
                location = response.headers.get("location")
                if not location or redirect_count == _MAX_REDIRECTS:
                    return None
                current_url = urljoin(current_url, location)

            if response is None:
                return None
            _ = response.raise_for_status()

            content = response.text
            content_type: str = response.headers.get("content-type", "") or ""

            # Validate content before returning
            if not self.policy.content_is_valid(content, content_type):
                logger.debug(f"Content validation failed for {url}")
                return None

            return FetchResult(
                url=url,
                content=content,
                title="",  # Will be populated by caller
                strategy_used="direct",
                cached=False,
                fetch_time_ms=0.0,  # Will be populated by caller
                content_length=len(content),
            )

        except SsrfViolation:
            raise
        except Exception as e:
            logger.warning(f"Direct fetch failed for {url}: {e}")
            return None

    async def _request_with_address_fallback(
        self,
        *,
        current_url: str,
        addresses: tuple[str, ...],
        host_header: str,
        sni_hostname: str,
        user_agent: str,
        deadline_at: float,
        cookies: httpx.Cookies | None = None,
    ) -> httpx.Response | None:
        """Issue ``GET current_url`` against each validated address in turn.

        Returns the first successful response, or ``None`` if every address
        fails to produce a response. Network-level errors against an address
        are logged at debug and the next address is tried; non-network errors
        are propagated.

        A fresh ``httpx.AsyncClient`` is used per address so the pinned-IP
        connection pool is never reused across distinct addresses (which would
        otherwise reuse a connection keyed to the wrong host). When ``cookies``
        is provided, that jar is shared across every per-address client in
        this hop so Set-Cookie responses from one attempt can drive the
        Cookie header of the next attempt; the caller is responsible for
        keeping the same jar alive across redirect hops so cookies set on
        a redirect response are available at the destination.

        ``deadline_at`` (a ``time.monotonic()`` absolute timestamp from the
        caller's redirect loop) bounds the cumulative wall time of every
        address attempt in *this hop*. The caller is responsible for
        sharing one deadline across all hops so a multi-hop redirect chain
        cannot extend the per-fetch budget by ``addresses × hops``.
        """
        last_error: Exception | None = None
        # Cap the address list so a hostile DNS response with thousands of
        # distinct addresses cannot exhaust the per-fetch deadline budget.
        capped_addresses = addresses[:_MAX_ADDRESS_ATTEMPTS]
        if len(capped_addresses) < len(addresses):
            logger.info(
                "Direct fetch capped address list at %d of %d for %s",
                len(capped_addresses),
                len(addresses),
                current_url,
            )

        for address in capped_addresses:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                logger.info(
                    "Direct fetch abandoned %s after exhausting remaining %.2fs of shared per-fetch budget",
                    current_url,
                    _PER_FETCH_DEADLINE_SECONDS,
                )
                break
            # Per-attempt httpx timeout bounds inactivity for individual
            # network operations (connect, read, write). The wall-clock
            # ``asyncio.wait_for`` below is what actually bounds the
            # entire ``client.get()`` against the shared deadline — a
            # slow-drip server could otherwise send a byte every few
            # seconds and keep ``client.get()`` alive indefinitely
            # without ever tripping the inactivity timeout.
            attempt_timeout = min(_PER_ADDRESS_TIMEOUT_SECONDS, remaining)
            try:
                client_kwargs: dict[str, Any] = {
                    "timeout": attempt_timeout,
                    "follow_redirects": False,
                    "trust_env": False,
                }
                if cookies is not None:
                    # Seed every per-address client with the shared jar so
                    # cookies set on prior attempts (this hop or a prior
                    # redirect hop) ride along with the next attempt.
                    client_kwargs["cookies"] = cookies
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await asyncio.wait_for(
                        client.get(
                            _pin_url_to_address(current_url, address),
                            headers={"User-Agent": user_agent, "Host": host_header},
                            extensions={"sni_hostname": sni_hostname},
                        ),
                        timeout=remaining,
                    )
                    if cookies is not None:
                        # ``httpx.AsyncClient`` copies the cookies argument
                        # into its own internal jar, so the shared jar is
                        # only updated *after* the response arrives. Mirror
                        # every jar mutation back so subsequent attempts
                        # (this hop or the next redirect hop) see cookies
                        # set by this attempt.
                        cookies.update(client.cookies)
                return response
            except (httpx.RequestError, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.debug("Direct fetch address %s for %s failed: %s", address, current_url, exc)
                continue
        if last_error is not None:
            logger.info(
                "Direct fetch exhausted %d addresses for %s; last error: %s",
                len(capped_addresses),
                current_url,
                last_error,
            )
        return None
