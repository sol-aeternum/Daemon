"""Direct fetch strategy implementation."""

from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import urljoin

import httpx

from orchestrator.services.fetch.models import FetchResult, FetchPolicy
from orchestrator.tools.ssrf_guard import SsrfViolation, socket_guard, validate_url

logger = logging.getLogger(__name__)

_FETCH_SCHEMES = frozenset({"http", "https"})
_FETCH_PORTS = frozenset({80, 443})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5

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
            with socket_guard():
                async with httpx.AsyncClient(
                    timeout=10.0, follow_redirects=False, trust_env=False
                ) as client:
                    for redirect_count in range(_MAX_REDIRECTS + 1):
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                validate_url,
                                current_url,
                                allowed_schemes=_FETCH_SCHEMES,
                                allowed_ports=_FETCH_PORTS,
                            ),
                            timeout=10.0,
                        )
                        response = await client.get(current_url, headers={"User-Agent": user_agent})
                        if response.status_code not in _REDIRECT_STATUSES:
                            break
                        location = response.headers.get("location")
                        if not location or redirect_count == _MAX_REDIRECTS:
                            return None
                        current_url = urljoin(current_url, location)

                assert response is not None
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

        except (TimeoutError, SsrfViolation) as e:
            logger.warning(f"Direct fetch blocked unsafe URL {url}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Direct fetch failed for {url}: {e}")
            return None
