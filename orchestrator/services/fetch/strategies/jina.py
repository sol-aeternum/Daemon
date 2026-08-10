"""Jina Reader fetch strategy implementation."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from orchestrator.config import get_settings
from orchestrator.services.fetch.models import FetchResult, FetchPolicy
from orchestrator.tools.ssrf_guard import (
    SsrfViolation,
    socket_guard,
    validate_url,
)

logger = logging.getLogger(__name__)

# User-supplied URLs forwarded to Jina may legitimately be plain http on
# port 80; the SSRF gate's job here is to reject URLs that resolve to
# private / loopback / link-local / CGNAT destinations, not to refuse
# plaintext http at the destination. The wider scheme/port range is still
# subject to the IP-range / DNS-rebinding blocklist enforced by
# ``validate_url`` and ``socket_guard``.
_JINA_USER_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_JINA_USER_URL_PORTS: frozenset[int] = frozenset({80, 443})


class JinaReaderStrategy:
    """Jina Reader fetch strategy using https://r.jina.ai/ API."""

    def __init__(self, policy: FetchPolicy) -> None:
        self.policy: FetchPolicy = policy

    async def fetch(self, url: str) -> FetchResult | None:
        """
        Fetch content from URL using Jina Reader API.

        Args:
            url: URL to fetch

        Returns:
            FetchResult with content or None if fetch failed
        """
        # SSRF-guard the user URL before forwarding. Jina sees every fetch
        # target an LLM is asked about through this strategy; without a
        # pre-flight check, a prompt-injected or memory-poisoned URL can
        # redirect the upstream to a private / link-local / internal
        # service. ``validate_url`` rejects disallowed schemes/ports,
        # userinfo, oversized URLs, malformed hosts, and resolves the
        # hostname to verify it is not in a disallowed IP range.
        try:
            validate_url(
                url,
                allowed_schemes=_JINA_USER_URL_SCHEMES,
                allowed_ports=_JINA_USER_URL_PORTS,
            )
        except SsrfViolation as exc:
            logger.warning(
                "Jina user URL %s violates SSRF policy: %s; refusing to fetch",
                url,
                exc,
            )
            raise

        settings = get_settings()
        jina_api_key = settings.jina_api_key

        encoded_url = quote(url, safe="")
        jina_url = f"https://r.jina.ai/{encoded_url}"

        # Validate the upstream jina.ai URL with the strict allowlist
        # (https-only on 443). The upstream is a hardcoded operator-trusted
        # destination, but the same pre-flight pattern protects against a
        # future override of the base URL pointing at a private IP.
        try:
            validate_url(jina_url)
        except SsrfViolation as exc:
            logger.error(
                "Configured Jina upstream %s violates SSRF policy: %s; refusing to fetch",
                jina_url,
                exc,
            )
            raise

        headers: dict[str, str] = {}
        if jina_api_key:
            headers["Authorization"] = f"Bearer {jina_api_key}"

        try:
            # socket_guard patches socket.getaddrinfo at process scope to
            # re-validate the resolved IP at connect time. This closes the
            # DNS-rebinding window between validate_url's pre-flight
            # resolution and httpx's actual TCP connect: a hostile DNS
            # response that returns a public IP for pre-flight and a
            # private IP for connect cannot bypass the IP blocklist.
            with socket_guard():
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(jina_url, headers=headers)

                    if response.status_code >= 400:
                        logger.debug(f"Jina Reader returned {response.status_code} for {url}")
                        return None

                    content = response.text
                    content_type: str = response.headers.get("content-type", "") or ""

                    if not self.policy.content_is_valid(content, content_type):
                        logger.debug(f"Content validation failed for {url}")
                        return None

                    return FetchResult(
                        url=url,
                        content=content,
                        title="",
                        strategy_used="jina",
                        cached=False,
                        fetch_time_ms=0.0,
                        content_length=len(content),
                    )

        except SsrfViolation:
            # SSRF violations must propagate so the strategy chain cannot
            # fall back to a strategy that bypasses policy.
            raise
        except Exception as e:
            logger.warning(f"Jina Reader fetch failed for {url}: {e}")
            return None
