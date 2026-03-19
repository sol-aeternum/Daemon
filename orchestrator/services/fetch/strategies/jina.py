"""Jina Reader fetch strategy implementation."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from orchestrator.config import get_settings
from orchestrator.services.fetch.models import FetchResult, FetchPolicy

logger = logging.getLogger(__name__)


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
        settings = get_settings()
        jina_api_key = settings.jina_api_key

        encoded_url = quote(url, safe="")
        jina_url = f"https://r.jina.ai/{encoded_url}"

        headers: dict[str, str] = {}
        if jina_api_key:
            headers["Authorization"] = f"Bearer {jina_api_key}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(jina_url, headers=headers)

                if response.status_code >= 400:
                    logger.debug(
                        f"Jina Reader returned {response.status_code} for {url}"
                    )
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

        except Exception as e:
            logger.warning(f"Jina Reader fetch failed for {url}: {e}")
            return None
