"""Jina Reader fetch strategy implementation."""

from __future__ import annotations

import asyncio
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

# Upstream origin is hardcoded; ``validate_url`` is run on the fixed origin
# string only (NOT on the encoded composed URL) so URL-encoding expansion
# past ``MAX_URL_LENGTH`` cannot falsely reject a previously-valid user URL.
_JINA_UPSTREAM_ORIGIN = "https://r.jina.ai/"


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
        #
        # ``asyncio.to_thread`` offloads the synchronous ``socket.getaddrinfo``
        # inside ``validate_url`` so a slow or unavailable resolver cannot
        # stall the FastAPI event loop on unrelated requests. Same pattern
        # used by ``HttpRequestTool`` and the Archive strategy.
        try:
            await asyncio.to_thread(
                validate_url,
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

        # Validate the fixed upstream origin (https://r.jina.ai/) against
        # the strict allowlist. The upstream is hardcoded today, but the
        # pre-flight pattern protects against a future override of the
        # base URL pointing at a private IP. The validation runs on the
        # fixed origin string only — NOT on the URL-encoded composed
        # ``jina_url`` — because URL-encoding a long query string can
        # expand past ``MAX_URL_LENGTH`` and falsely reject a previously
        # valid user URL (e.g. a 1,521-char query of 500 ``&a=`` pairs
        # expands to ~3,549 chars). The origin is operator-controlled and
        # constant; the user-input length gate already ran on the user
        # URL above.
        try:
            await asyncio.to_thread(validate_url, _JINA_UPSTREAM_ORIGIN)
        except SsrfViolation as exc:
            logger.error(
                "Configured Jina upstream %s violates SSRF policy: %s; refusing to fetch",
                _JINA_UPSTREAM_ORIGIN,
                exc,
            )
            raise

        encoded_url = quote(url, safe="")
        jina_url = f"{_JINA_UPSTREAM_ORIGIN}{encoded_url}"

        headers: dict[str, str] = {}
        if jina_api_key:
            headers["Authorization"] = f"Bearer {jina_api_key}"

        try:
            # ``socket_guard`` patches process-global state for the full
            # duration of the awaited request. That is intentional: every
            # DNS lookup during the second-hop HTTP call (initial
            # validation, connect-time resolution, retries) must be forced
            # through the public-IP policy or the rebinding window between
            # pre-flight and connect opens. The guard is reference-counted
            # under a lock so concurrent callers are safe, but overlapping
            # coroutines that issue unrelated DNS lookups during this
            # window will inherit the policy. That is acceptable because
            # the SSRF policy is a whole-process invariant — unrelated
            # callers that needed private-IP resolution would be a
            # separate configuration bug, not a side effect of this
            # strategy. Scoping the patch to a single ``httpx.AsyncClient``
            # would not close the rebinding window for retries/redirects
            # inside that call and is therefore rejected on the merits.
            with socket_guard():
                # ``trust_env=False`` disables honouring of
                # ``HTTPS_PROXY`` / ``ALL_PROXY`` and the ``no_proxy``
                # bypass list from the process environment, so the SSRF
                # guard cannot be bypassed by an operator-configured
                # proxy that resolves only the public hostname but
                # routes traffic elsewhere. Same treatment as the
                # guarded ``HttpRequestTool`` and the Archive strategy.
                async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
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
            # fall back to a strategy that bypasses policy. The
            # ``FetchService._attempt_strategy`` exception handler is
            # intentionally narrow — it does NOT catch ``SsrfViolation``,
            # so a violation here short-circuits the chain (see
            # ``service.py``).
            raise
        except Exception as e:
            logger.warning(f"Jina Reader fetch failed for {url}: {e}")
            return None
