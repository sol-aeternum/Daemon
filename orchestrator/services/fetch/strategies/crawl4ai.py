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
                        logger.warning(f"No result in Crawl4AI response for {url}")
                        return None

                    result_item = result_list[0]
                    markdown_content = result_item.get("markdown", "")

                    if not markdown_content:
                        logger.warning(
                            f"No markdown content in Crawl4AI response for {url}"
                        )
                        return None

                    if isinstance(
                        markdown_content, str
                    ) and not self.policy.content_is_valid(markdown_content):
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

            except httpx.ConnectError as e:
                logger.warning(f"Crawl4AI connection refused for {url}: {e}")
                return None
            except httpx.ConnectTimeout as e:
                logger.warning(f"Crawl4AI connection timeout for {url}: {e}")
                return None
            except Exception as e:
                logger.warning(f"Crawl4AI fetch failed for {url}: {e}")
                return None
