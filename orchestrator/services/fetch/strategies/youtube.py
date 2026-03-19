"""YouTube transcript fetch strategy implementation."""

from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from youtube_transcript_api import YouTubeTranscriptApi

from orchestrator.services.fetch.models import FetchResult

if TYPE_CHECKING:
    from orchestrator.services.fetch.models import FetchPolicy

logger = logging.getLogger(__name__)

# Regex patterns for YouTube URL formats
YOUTUBE_URL_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([^&\n\r\s?#]+)"),
    re.compile(r"(?:https?://)?(?:www\.)?youtu\.be/([^&\n\r\s?#]+)"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([^&\n\r\s?#]+)"),
]


class YouTubeTranscriptStrategy:
    """YouTube transcript fetch strategy using youtube-transcript-api."""

    def __init__(self, policy: FetchPolicy) -> None:
        self.policy: FetchPolicy = policy
        # Dedicated ThreadPoolExecutor for YouTube transcript fetching
        self.executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=3)

    def _extract_video_id(self, url: str) -> str | None:
        """
        Extract YouTube video ID from URL.

        Args:
            url: YouTube URL

        Returns:
            Video ID string or None if not found
        """
        for pattern in YOUTUBE_URL_PATTERNS:
            match = pattern.search(url)
            if match:
                return match.group(1)
        return None

    def _segment_value(self, segment: Any, key: str) -> Any:
        if isinstance(segment, dict):
            return segment.get(key)
        return getattr(segment, key, None)

    def _format_transcript(self, transcript: Any) -> str:
        """
        Format transcript as markdown with timestamps.

        Args:
            transcript: List of transcript segments

        Returns:
            Formatted markdown string
        """
        formatted_segments = []
        for segment in transcript:
            start_value = self._segment_value(segment, "start")
            text_value = self._segment_value(segment, "text")
            if start_value is None or text_value is None:
                continue

            seconds = int(float(start_value))
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            timestamp = f"[{hours:02d}:{minutes:02d}:{secs:02d}]"
            text = str(text_value).strip()
            if not text:
                continue
            formatted_segments.append(f"{timestamp} {text}")

        return "\n".join(formatted_segments)

    async def fetch(self, url: str) -> FetchResult | None:
        """
        Fetch YouTube transcript for URL.

        Args:
            url: YouTube URL

        Returns:
            FetchResult with transcript content or None if fetch failed
        """
        video_id = self._extract_video_id(url)
        if not video_id:
            logger.debug(f"Could not extract video ID from URL: {url}")
            return None

        try:
            # Use ThreadPoolExecutor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(
                self.executor,
                lambda: YouTubeTranscriptApi().fetch(video_id, languages=["en"]),
            )

            if not transcript:
                logger.debug(f"No transcript available for video ID: {video_id}")
                return None

            # Format transcript as markdown
            content = self._format_transcript(transcript)

            return FetchResult(
                url=url,
                content=content,
                title=f"YouTube Transcript: {video_id}",
                strategy_used="youtube",
                cached=False,
                fetch_time_ms=0.0,  # Will be populated by caller
                content_length=len(content),
            )

        except Exception as e:
            logger.warning(f"YouTube transcript fetch failed for {url}: {e}")
            return None

    def __del__(self) -> None:
        """Clean up ThreadPoolExecutor on destruction."""
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=False)
