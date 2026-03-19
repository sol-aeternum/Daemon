"""Direct fetch strategy implementation."""

from __future__ import annotations

import logging
import random

import httpx

from orchestrator.services.fetch.models import FetchResult, FetchPolicy

logger = logging.getLogger(__name__)

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
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True, max_redirects=5
            ) as client:
                response = await client.get(url, headers={"User-Agent": user_agent})
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

        except Exception as e:
            logger.warning(f"Direct fetch failed for {url}: {e}")
            return None
