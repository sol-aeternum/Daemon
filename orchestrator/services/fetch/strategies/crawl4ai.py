"""Crawl4AI fetch strategy implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from orchestrator.config import get_settings
from orchestrator.services.fetch.models import FetchResult

if TYPE_CHECKING:
    from orchestrator.services.fetch.models import FetchPolicy

logger = logging.getLogger(__name__)

# Module-level semaphore to limit concurrent calls to 1
SEM = asyncio.Semaphore(1)


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
        settings = get_settings()
        crawl4ai_url = settings.crawl4ai_url.rstrip("/")
        api_url = f"{crawl4ai_url}/crawl"

        # Acquire semaphore to limit concurrent calls
        async with SEM:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
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
                        logger.warning("No result in Crawl4AI response")
                        return None

                    result_item = result_list[0]
                    markdown_content = result_item.get("markdown", "")

                    if not markdown_content:
                        logger.warning("No markdown content in Crawl4AI response")
                        return None

                    if isinstance(markdown_content, str) and not self.policy.content_is_valid(
                        markdown_content
                    ):
                        logger.debug("Content validation failed")
                        return None

                    if not isinstance(markdown_content, str):
                        logger.warning("Invalid markdown content type in Crawl4AI response")
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

            except httpx.ConnectError:
                logger.warning("Crawl4AI connection refused")
                return None
            except httpx.ConnectTimeout:
                logger.warning("Crawl4AI connection timeout")
                return None
            except Exception:
                # Transport/provider errors carry the requested URL and the
                # provider response; neither is logged at any level.
                logger.warning("Crawl4AI fetch failed")
                return None
