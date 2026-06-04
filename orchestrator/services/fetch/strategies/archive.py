"""Archive.org Wayback Machine fetch strategy implementation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import cast

import httpx

from orchestrator.services.fetch.extract import html_to_markdown
from orchestrator.services.fetch.models import FetchResult, FetchPolicy

logger = logging.getLogger(__name__)


class ArchiveOrgStrategy:
    """Archive.org Wayback Machine fetch strategy."""

    def __init__(self, policy: FetchPolicy) -> None:
        self.policy: FetchPolicy = policy

    async def fetch(self, url: str) -> FetchResult | None:
        """
        Fetch content from Archive.org Wayback Machine.

        Args:
            url: URL to fetch from archive

        Returns:
            FetchResult with archived content or None if no suitable snapshot found
        """
        try:
            # Check Wayback availability API
            availability_url = f"https://archive.org/wayback/available?url={url}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(availability_url)
                _ = response.raise_for_status()

                data: dict[str, object] = cast(dict[str, object], response.json())

                # Check if snapshot exists
                snapshots: dict[str, object] = cast(
                    dict[str, object], data.get("archived_snapshots", {})
                )
                closest: dict[str, object] = cast(dict[str, object], snapshots.get("closest", {}))

                if not closest or not cast(bool, closest.get("available", False)):
                    logger.debug(f"No archive snapshot available for {url}")
                    return None

                # Check snapshot timestamp (must be within 90 days)
                timestamp_str: str = cast(str, closest.get("timestamp", ""))
                if len(timestamp_str) >= 14:
                    try:
                        # Parse timestamp format: YYYYMMDDHHMMSS
                        snapshot_time = datetime(
                            year=int(timestamp_str[0:4]),
                            month=int(timestamp_str[4:6]),
                            day=int(timestamp_str[6:8]),
                            hour=int(timestamp_str[8:10]),
                            minute=int(timestamp_str[10:12]),
                            second=int(timestamp_str[12:14]),
                            tzinfo=timezone.utc,
                        )

                        # Check if snapshot is within 90 days
                        if datetime.now(timezone.utc) - snapshot_time > timedelta(days=90):
                            logger.debug(f"Archive snapshot too old for {url}")
                            return None
                    except (ValueError, IndexError):
                        logger.warning(f"Invalid timestamp format for {url}: {timestamp_str}")
                        return None
                else:
                    logger.warning(f"Invalid timestamp for {url}: {timestamp_str}")
                    return None

                # Fetch archived HTML
                archive_url: str = cast(str, closest.get("url", ""))
                if not archive_url:
                    logger.warning(f"No archive URL in response for {url}")
                    return None

                html_response = await client.get(archive_url)
                _ = html_response.raise_for_status()

                html_content = html_response.text
                content_type: str = html_response.headers.get("content-type", "") or ""

                # Validate HTML content
                if not self.policy.content_is_valid(html_content, content_type):
                    logger.debug(f"Archived content validation failed for {url}")
                    return None

                # Convert HTML to markdown
                markdown_content = html_to_markdown(html_content)
                if not markdown_content:
                    logger.warning(f"HTML to markdown conversion failed for {url}")
                    return None

                return FetchResult(
                    url=url,
                    content=markdown_content,
                    title="",  # Will be populated by caller
                    strategy_used="archive",
                    cached=False,
                    fetch_time_ms=0.0,  # Will be populated by caller
                    content_length=len(markdown_content),
                )

        except Exception as e:
            logger.warning(f"Archive.org fetch failed for {url}: {e}")
            return None
