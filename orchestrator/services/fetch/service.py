from __future__ import annotations

import ipaddress
import logging
import time
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlparse

from orchestrator.services.fetch.cache import FetchCache, normalize_url
from orchestrator.services.fetch.models import (
    FetchPolicy,
    FetchResult,
    load_policy_from_env,
)
from orchestrator.services.fetch.strategies.archive import ArchiveOrgStrategy
from orchestrator.services.fetch.strategies.crawl4ai import Crawl4AIStrategy
from orchestrator.services.fetch.strategies.direct import DirectFetchStrategy
from orchestrator.services.fetch.strategies.jina import JinaReaderStrategy
from orchestrator.services.fetch.strategies.youtube import YouTubeTranscriptStrategy
from orchestrator.tools.ssrf_guard import (
    MAX_URL_LENGTH,
    SsrfViolation,
    is_disallowed_ip,
    validate_url_and_resolve_async,
)

logger = logging.getLogger(__name__)

_FETCH_SCHEMES = frozenset({"http", "https"})
_FETCH_PORTS = frozenset({80, 443})


class FetchStrategy(Protocol):
    async def fetch(self, url: str) -> FetchResult | None: ...


class FetchService:
    def __init__(
        self,
        policy: FetchPolicy | None = None,
        cache: FetchCache | None = None,
    ) -> None:
        self.policy: FetchPolicy = policy or load_policy_from_env()
        self.cache: FetchCache = cache or FetchCache()

        self.youtube_strategy: FetchStrategy | None = YouTubeTranscriptStrategy(self.policy)
        self.direct_strategy: FetchStrategy | None = DirectFetchStrategy(self.policy)
        self.jina_strategy: FetchStrategy | None = JinaReaderStrategy(self.policy)
        self.crawl4ai_strategy: FetchStrategy | None = Crawl4AIStrategy(self.policy)
        self.archive_strategy: FetchStrategy | None = ArchiveOrgStrategy(self.policy)

    async def fetch(
        self,
        url: str,
        extract: str = "article",
        force_refresh: bool = False,
    ) -> FetchResult | None:
        fetch_url = url.strip()
        if not fetch_url:
            logger.info("FetchService skipping fetch: empty URL")
            return None

        # Static (non-DNS) policy checks run before cache access so that a valid
        # cached result is still served when DNS is temporarily unavailable.
        # These checks enforce scheme/port and the operator's blocked-domain
        # list against the normalized URL hostname alone — they do not require
        # resolving the host. DNS-based validation runs only when we actually
        # need to issue an outbound request.
        if not self._is_supported_url(fetch_url):
            logger.info("FetchService blocked unsupported URL %s", fetch_url)
            return None

        normalized_url = normalize_url(fetch_url)
        logger.info(
            "FetchService starting fetch for %s (extract=%s, force_refresh=%s)",
            normalized_url,
            extract,
            force_refresh,
        )

        if self._is_blocked_domain(normalized_url):
            logger.info(
                "FetchService skipping fetch for %s: blocked domain policy matched",
                normalized_url,
            )
            return None

        cached_result: FetchResult | None = None
        if force_refresh:
            logger.info(
                "FetchService skipping cache for %s: force_refresh enabled",
                normalized_url,
            )
        else:
            cached_result = await self.cache.get(normalized_url)

        if cached_result is not None:
            if self.policy.content_is_valid(cached_result.content):
                logger.info(
                    "FetchService cache hit for %s via %s",
                    normalized_url,
                    cached_result.strategy_used,
                )
                return cached_result

            logger.info(
                "FetchService skipping cached result for %s: cached content failed validation",
                normalized_url,
            )
        elif not force_refresh:
            logger.info("FetchService cache miss for %s", normalized_url)

        # DNS resolution / IP-range validation runs only when a real outbound
        # fetch is about to be attempted. A cache hit short-circuits above.
        try:
            await validate_url_and_resolve_async(
                fetch_url,
                allowed_schemes=_FETCH_SCHEMES,
                allowed_ports=_FETCH_PORTS,
            )
        except SsrfViolation as exc:
            logger.info("FetchService blocked unsafe URL %s: %s", fetch_url, exc)
            return None

        strategies: Sequence[tuple[str, FetchStrategy | None]]
        if self._is_youtube_url(normalized_url):
            logger.info(
                "FetchService detected YouTube URL for %s: skipping direct/jina/crawl4ai/archive chain",
                normalized_url,
            )
            strategies = (("youtube", self.youtube_strategy),)
        else:
            strategies = self._default_strategy_chain()

        try:
            result = await self._run_strategy_chain(
                fetch_url=fetch_url,
                normalized_url=normalized_url,
                strategies=strategies,
            )
        except SsrfViolation as exc:
            logger.info(
                "FetchService stopped fallback chain for %s: unsafe redirect: %s",
                normalized_url,
                exc,
            )
            return None
        if result is None:
            logger.info(
                "FetchService exhausted strategies for %s without success",
                normalized_url,
            )
            return None

        cached = await self.cache.set(normalized_url, result)
        if cached:
            logger.info(
                "FetchService cached result for %s via %s",
                normalized_url,
                result.strategy_used,
            )
        else:
            logger.info(
                "FetchService skipped cache write for %s via %s",
                normalized_url,
                result.strategy_used,
            )

        return result

    def _default_strategy_chain(self) -> Sequence[tuple[str, FetchStrategy | None]]:
        return (
            ("direct", self.direct_strategy),
            ("jina", self.jina_strategy),
            ("archive", self.archive_strategy),
        )

    def _is_supported_url(self, url: str) -> bool:
        """Static (non-DNS) policy gate.

        Mirrors the non-network parts of ``validate_url_and_resolve_async`` plus
        the same disallowed-range check for IP literals. The combination lets
        ``fetch`` consult the cache before issuing any DNS query, while still
        rejecting obviously-unsafe URLs up front.

        A hostname URL passes this check; the DNS-based IP-range validation
        runs later in ``fetch`` immediately before the strategy chain.
        """
        if not url or not isinstance(url, str):
            return False
        if len(url) > MAX_URL_LENGTH:
            return False
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        if parsed.scheme not in _FETCH_SCHEMES:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        try:
            host = parsed.hostname
            explicit_port = parsed.port
        except ValueError:
            return False
        if not host:
            return False
        if explicit_port is not None and explicit_port not in _FETCH_PORTS:
            return False
        # Apply exactly the same literal-IP classification as the DNS
        # validator. In particular this rejects CGNAT, documentation, mapped
        # IPv6, and transition ranges that the stdlib's individual
        # ``is_private`` / ``is_reserved`` flags do not cover consistently.
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            # IDNA validation is local and deterministic: it rejects malformed
            # host labels without issuing a DNS query, while valid hostnames
            # remain eligible for a cache hit during a resolver outage.
            try:
                host.encode("idna")
            except UnicodeError:
                return False
            return True
        return not is_disallowed_ip(literal)

    async def _run_strategy_chain(
        self,
        fetch_url: str,
        normalized_url: str,
        strategies: Sequence[tuple[str, FetchStrategy | None]],
    ) -> FetchResult | None:
        for strategy_name, strategy in strategies:
            result = await self._attempt_strategy(
                fetch_url=fetch_url,
                normalized_url=normalized_url,
                strategy_name=strategy_name,
                strategy=strategy,
            )
            if result is not None:
                return result

        return None

    async def _attempt_strategy(
        self,
        fetch_url: str,
        normalized_url: str,
        strategy_name: str,
        strategy: FetchStrategy | None,
    ) -> FetchResult | None:
        if strategy is None:
            logger.info(
                "FetchService skipping %s for %s: strategy not ready",
                strategy_name,
                normalized_url,
            )
            return None

        logger.info(
            "FetchService attempting %s for %s",
            strategy_name,
            normalized_url,
        )
        started_at = time.perf_counter()

        try:
            result = await strategy.fetch(fetch_url)
        except SsrfViolation:
            raise
        except Exception:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "FetchService %s failed for %s in %.2fms: exception raised",
                strategy_name,
                normalized_url,
                elapsed_ms,
            )
            logger.warning(
                "Unexpected exception from %s strategy for %s",
                strategy_name,
                normalized_url,
                exc_info=True,
            )
            return None

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if result is None:
            logger.info(
                "FetchService %s failed for %s in %.2fms: no result",
                strategy_name,
                normalized_url,
                elapsed_ms,
            )
            return None

        if not self.policy.content_is_valid(result.content):
            logger.info(
                "FetchService %s failed for %s in %.2fms: content validation failed",
                strategy_name,
                normalized_url,
                elapsed_ms,
            )
            return None

        result.url = normalized_url
        result.strategy_used = strategy_name
        result.cached = False
        result.fetch_time_ms = elapsed_ms
        result.content_length = len(result.content)

        logger.info(
            "FetchService %s succeeded for %s in %.2fms (%s chars)",
            strategy_name,
            normalized_url,
            elapsed_ms,
            result.content_length,
        )
        return result

    def _is_blocked_domain(self, url: str) -> bool:
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        if not hostname:
            return False

        for blocked_domain in self.policy.blocked_domains:
            normalized_blocked_domain = blocked_domain.lower().strip().rstrip(".")
            if not normalized_blocked_domain:
                continue

            if hostname == normalized_blocked_domain or hostname.endswith(
                f".{normalized_blocked_domain}"
            ):
                return True

        return False

    def _is_youtube_url(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url
