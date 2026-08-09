"""Direct fetch strategy implementation."""

from __future__ import annotations

import logging
import random
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from orchestrator.services.fetch.models import FetchResult, FetchPolicy
from orchestrator.tools.ssrf_guard import (
    SsrfViolation,
    validate_url_and_resolve_async,
)

logger = logging.getLogger(__name__)

_FETCH_SCHEMES = frozenset({"http", "https"})
_FETCH_PORTS = frozenset({80, 443})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


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

    Hostnames are canonicalized by stripping a trailing dot before comparison so
    that DNS-equivalent forms (e.g. ``example.com.``) cannot bypass the policy.
    """
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    for blocked in blocked_domains:
        normalized = blocked.lower().strip().rstrip(".")
        if not normalized:
            continue
        if hostname == normalized or hostname.endswith(f".{normalized}"):
            return True
    return False


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
            for redirect_count in range(_MAX_REDIRECTS + 1):
                if _is_blocked_domain(current_url, self.policy.blocked_domains):
                    raise SsrfViolation(f"hostname is blocked by fetch policy: {current_url}")
                validated = await validate_url_and_resolve_async(
                    current_url,
                    allowed_schemes=_FETCH_SCHEMES,
                    allowed_ports=_FETCH_PORTS,
                )
                if not validated.addresses:
                    raise SsrfViolation(
                        f"DNS resolution returned no usable results for {validated.host!r}"
                    )
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
    ) -> httpx.Response | None:
        """Issue ``GET current_url`` against each validated address in turn.

        Returns the first successful response, or ``None`` if every address
        fails to produce a response. Network-level errors against an address
        are logged at debug and the next address is tried; non-network errors
        are propagated.

        A fresh ``httpx.AsyncClient`` is used per address so the pinned-IP
        connection pool is never reused across distinct addresses (which would
        otherwise reuse a connection keyed to the wrong host).
        """
        last_error: Exception | None = None
        for address in addresses:
            try:
                async with httpx.AsyncClient(
                    timeout=10.0, follow_redirects=False, trust_env=False
                ) as client:
                    response = await client.get(
                        _pin_url_to_address(current_url, address),
                        headers={"User-Agent": user_agent, "Host": host_header},
                        extensions={"sni_hostname": sni_hostname},
                    )
                return response
            except httpx.RequestError as exc:
                last_error = exc
                logger.debug("Direct fetch address %s for %s failed: %s", address, current_url, exc)
                continue
        if last_error is not None:
            logger.info(
                "Direct fetch exhausted %d addresses for %s; last error: %s",
                len(addresses),
                current_url,
                last_error,
            )
        return None
