"""Crawl4AI fetch strategy implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from orchestrator.config import get_settings
from orchestrator.services.fetch.models import FetchResult
from orchestrator.tools.ssrf_guard import (
    SsrfPolicyViolation,
    SsrfUnreachable,
    SsrfViolation,
    socket_guard,
    validate_url_and_resolve_async,
)

if TYPE_CHECKING:
    from orchestrator.services.fetch.models import FetchPolicy

logger = logging.getLogger(__name__)

# Module-level semaphore to limit concurrent calls to 1
SEM = asyncio.Semaphore(1)

# User-supplied URLs forwarded to crawl4ai may legitimately be plain http on
# port 80 (the crawl4ai service is expected to fetch both). The SSRF gate's
# job here is to reject URLs that resolve to private / loopback / link-local
# / CGNAT destinations, not to refuse plaintext http at the destination. The
# wider scheme/port range is still subject to the IP-range / DNS-rebinding
# blocklist enforced by ``validate_url`` and ``socket_guard``.
_CRAWL4AI_USER_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_CRAWL4AI_USER_URL_PORTS: frozenset[int] = frozenset({80, 443})

# The configured crawl4ai upstream uses the same wider allowlist as the user
# URL: the default ``crawl4ai_url`` is ``http://crawl4ai:11235`` (a
# docker-compose service on plain http + non-standard port) and self-hosted
# installs commonly run on either 80 or 443 with either http or https. The
# security gate on the upstream is still the IP-range / DNS-rebinding
# blocklist enforced by ``validate_url`` and ``socket_guard`` — refusing a
# configured crawl4ai on a non-standard port would break the default
# deployment. The pre-flight pattern still rejects a misconfigured
# ``crawl4ai_url`` pointing at a private IP (the actual SSRF amplification
# vector), so the security boundary holds.
_CRAWL4AI_UPSTREAM_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_CRAWL4AI_UPSTREAM_PORTS: frozenset[int] = frozenset({80, 443, 11235})


class Crawl4AIStrategy:
    """Crawl4AI fetch strategy using REST API."""

    def __init__(self, policy: FetchPolicy) -> None:
        self.policy: FetchPolicy = policy

    async def fetch(self, url: str) -> FetchResult | None:
        """
        Fetch content from URL using Crawl4AI REST API.

        Args:
            url: URL to fetch

        Returns:
            FetchResult with content or None if fetch failed
        """
        # SSRF-guard the user URL before forwarding. Crawl4ai sees every fetch
        # target an LLM is asked about through this strategy; without a
        # pre-flight check, a prompt-injected or memory-poisoned URL reaches
        # crawl4ai and the configured service dutifully fetches it. The wider
        # allowlist (http/https on 80/443) matches crawl4ai's actual
        # capability so legitimate use is not broken; the
        # IP-range / DNS-rebinding blocklist is unchanged.
        #
        # ``validate_url_and_resolve_async`` runs the synchronous
        # ``socket.getaddrinfo`` on the SSRF module's bounded resolver pool
        # (4 workers, 8 slots) so attacker-controlled slow lookups cannot
        # queue unbounded work in asyncio's process-wide default executor
        # and starve unrelated backend operations. ``SsrfUnreachable``
        # (DNS timeout / gaierror / resolver-pool exhaustion) is treated as
        # a transient reachability failure: this strategy returns ``None``
        # so the chain can fall back to Archive / Jina rather than
        # terminating on an unreachable target.
        try:
            await validate_url_and_resolve_async(
                url,
                allowed_schemes=_CRAWL4AI_USER_URL_SCHEMES,
                allowed_ports=_CRAWL4AI_USER_URL_PORTS,
            )
        except SsrfUnreachable as exc:
            logger.info(
                "Crawl4AI user URL %s is unreachable (DNS): %s; returning None",
                url,
                exc,
            )
            return None
        except SsrfPolicyViolation as exc:
            logger.warning(
                "Crawl4AI user URL %s violates SSRF policy: %s; refusing to fetch",
                url,
                exc,
            )
            raise

        settings = get_settings()
        crawl4ai_url = settings.crawl4ai_url.rstrip("/")
        api_url = f"{crawl4ai_url}/crawl"

        # Validate the configured upstream against the same wider allowlist
        # as the user URL. The default ``crawl4ai_url`` is
        # ``http://crawl4ai:11235`` (a docker-compose service on plain http
        # + non-standard port), so a strict allowlist (https+443 only)
        # would reject the default deployment. The security gate on the
        # upstream is the IP-range / DNS-rebinding blocklist enforced by
        # ``validate_url`` and the ``socket_guard`` wrapper below — not the
        # scheme/port allowlist. The pre-flight ``validate_url`` still
        # rejects a misconfigured ``crawl4ai_url`` pointing at a private
        # IP (the actual SSRF amplification vector), so the security
        # boundary holds across the explicit amplification cases. The
        # startup-time configuration check (recommended fix #1 — refuse
        # private / loopback / link-local ``crawl4ai_url`` at startup) is
        # tracked separately and is intentionally not in this PR; the
        # per-fetch pre-flight is the first line of defense and runs on
        # every call regardless of how the operator set the value.
        try:
            await validate_url_and_resolve_async(
                api_url,
                allowed_schemes=_CRAWL4AI_UPSTREAM_SCHEMES,
                allowed_ports=_CRAWL4AI_UPSTREAM_PORTS,
            )
        except SsrfUnreachable as exc:
            logger.info(
                "Configured Crawl4AI upstream %s is unreachable (DNS): %s; returning None",
                api_url,
                exc,
            )
            return None
        except SsrfPolicyViolation as exc:
            logger.error(
                "Configured Crawl4AI upstream %s violates SSRF policy: %s; refusing to fetch",
                api_url,
                exc,
            )
            raise

        # Acquire semaphore to limit concurrent calls
        async with SEM:
            try:
                # ``socket_guard`` patches process-global state for the full
                # duration of the awaited request. That is intentional: every
                # DNS lookup during the POST (initial validation, connect-time
                # resolution, retries) must be forced through the public-IP
                # policy or the rebinding window between pre-flight and
                # connect opens. The guard is reference-counted under a lock
                # so concurrent callers are safe, but overlapping coroutines
                # that issue unrelated DNS lookups during this window will
                # inherit the policy. That is acceptable because the SSRF
                # policy is a whole-process invariant — unrelated callers
                # that needed private-IP resolution would be a separate
                # configuration bug, not a side effect of this strategy.
                # Scoping the patch to a single ``httpx.AsyncClient`` would
                # not close the rebinding window for retries / redirects
                # inside that call and is therefore rejected on the merits.
                with socket_guard():
                    # ``trust_env=False`` disables honouring of
                    # ``HTTPS_PROXY`` / ``ALL_PROXY`` and the ``no_proxy``
                    # bypass list from the process environment, so the SSRF
                    # guard cannot be bypassed by an operator-configured
                    # proxy that resolves only the public hostname but
                    # routes traffic elsewhere. Same treatment as the
                    # guarded ``HttpRequestTool``, the Jina strategy, and
                    # the Archive strategy.
                    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                        response = await client.post(
                            api_url,
                            json={
                                "urls": [url],
                                "extraction_config": {"type": "markdown"},
                            },
                        )
                        _ = response.raise_for_status()

                        data = response.json()

                        # Extract markdown from response
                        result_list = data.get("result", [])
                        if not result_list:
                            logger.warning(f"No result in Crawl4AI response for {url}")
                            return None

                        result_item = result_list[0]
                        markdown_content = result_item.get("markdown", "")

                        if not markdown_content:
                            logger.warning(f"No markdown content in Crawl4AI response for {url}")
                            return None

                        if isinstance(markdown_content, str) and not self.policy.content_is_valid(
                            markdown_content
                        ):
                            logger.debug(f"Content validation failed for {url}")
                            return None

                        if not isinstance(markdown_content, str):
                            logger.warning(f"Invalid markdown content type for {url}")
                            return None

                        return FetchResult(
                            url=url,
                            content=markdown_content,
                            title="",
                            strategy_used="crawl4ai",
                            cached=False,
                            fetch_time_ms=0.0,
                            content_length=len(markdown_content),
                        )

            except SsrfViolation:
                # SSRF violations are terminal: the strategy's pre-flight or
                # upstream guard has rejected the URL, so the chain must not
                # fall back to a strategy that bypasses policy. Re-raise so
                # the strategy chain short-circuits and surfaces the
                # violation to the tool caller.
                raise
            except httpx.ConnectError as e:
                logger.warning(f"Crawl4AI connection refused for {url}: {e}")
                return None
            except httpx.ConnectTimeout as e:
                logger.warning(f"Crawl4AI connection timeout for {url}: {e}")
                return None
            except Exception as e:
                logger.warning(f"Crawl4AI fetch failed for {url}: {e}")
                return None
