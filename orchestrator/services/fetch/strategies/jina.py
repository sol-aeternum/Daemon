"""Jina Reader fetch strategy implementation."""

from __future__ import annotations

import logging
from urllib.parse import quote

from orchestrator.config import get_settings
from orchestrator.services.fetch.models import FetchResult, FetchPolicy
from orchestrator.services.fetch.pinned_http import pinned_get
from orchestrator.tools.ssrf_guard import (
    SsrfPolicyViolation,
    SsrfUnreachable,
    SsrfViolation,
    validate_url_and_resolve_async,
)

logger = logging.getLogger(__name__)

# User-supplied URLs forwarded to Jina may legitimately be plain http on
# port 80; the SSRF gate's job here is to reject URLs that resolve to
# private / loopback / link-local / CGNAT destinations, not to refuse
# plaintext http at the destination. The wider scheme/port range is still
# subject to the IP-range blocklist enforced by the bounded resolver.
_JINA_USER_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_JINA_USER_URL_PORTS: frozenset[int] = frozenset({80, 443})

# Upstream origin is hardcoded; SSRF validation runs on the fixed origin
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
        # service. ``validate_url_and_resolve_async`` rejects disallowed
        # schemes/ports, userinfo, oversized URLs, malformed hosts, and
        # resolves the hostname to verify it is not in a disallowed IP range.
        #
        # Jina independently resolves this target after Daemon forwards it.
        # Retaining the fallback therefore accepts Jina's documented
        # public-URL enforcement as a third-party trust boundary; the local
        # check prevents forwarding targets that are already unsafe from
        # Daemon's view but cannot pin Jina's remote DNS result.
        try:
            await validate_url_and_resolve_async(
                url,
                allowed_schemes=_JINA_USER_URL_SCHEMES,
                allowed_ports=_JINA_USER_URL_PORTS,
                timeout=15.0,
            )
        except SsrfUnreachable:
            # A local resolver outage is target unavailability, not evidence
            # that the URL is unsafe. Static checks have already run before
            # the bounded resolver was entered, so retain the documented
            # Jina public-URL trust boundary approved for this fallback.
            # The requested URL and resolver exception text are never logged.
            logger.info(
                "Jina user URL could not be resolved locally; "
                "continuing under Jina public-URL policy"
            )
        except SsrfPolicyViolation:
            # Log without the requested URL or violation text; the raise
            # below preserves the original bounded error for the caller.
            logger.warning("Jina user URL violates SSRF policy; refusing to fetch")
            raise

        settings = get_settings()
        jina_api_key = settings.jina_api_key

        encoded_url = quote(url, safe="")
        jina_url = f"{_JINA_UPSTREAM_ORIGIN}{encoded_url}"

        headers: dict[str, str] = {}
        if jina_api_key:
            headers["Authorization"] = f"Bearer {jina_api_key}"

        try:
            # Resolve the fixed Jina origin once and connect directly to one
            # of those approved IPs while preserving ``Host: r.jina.ai`` and
            # TLS SNI. ``validation_url`` deliberately excludes the encoded
            # path so encoding expansion cannot trip the validator's input
            # length limit; the helper enforces that the request remains on
            # exactly the validated scheme/host/port.
            response = await pinned_get(
                jina_url,
                validation_url=_JINA_UPSTREAM_ORIGIN,
                headers=headers,
                timeout=15.0,
            )
            if response is None:
                return None
            if response.status_code >= 400:
                logger.debug("Jina Reader returned HTTP status %s", response.status_code)
                return None

            content = response.text
            content_type: str = response.headers.get("content-type", "") or ""

            if not self.policy.content_is_valid(content, content_type):
                logger.debug("Content validation failed")
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
            # fall back after the fixed Jina upstream fails policy. The
            # fixed origin is operator configuration; the violation message
            # itself can embed URLs and is never logged.
            logger.error(
                "Configured Jina upstream %s violates SSRF policy; refusing to fetch",
                _JINA_UPSTREAM_ORIGIN,
            )
            raise
        except Exception:
            # Fetch exceptions carry the requested URL and upstream response
            # summary; neither is logged at any level.
            logger.warning("Jina Reader fetch failed")
            return None
