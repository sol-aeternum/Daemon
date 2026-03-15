"""OpenAI Sora API client for video generation."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel

from orchestrator.config import get_sora_api_key

logger = logging.getLogger(__name__)


class VideoJob(BaseModel):
    """Video generation job."""

    job_id: str
    prompt: str
    duration_seconds: int
    source_image_url: str | None = None


class VideoResult(BaseModel):
    """Result from video generation."""

    url: str
    prompt: str
    duration_seconds: int
    source_image_url: str | None = None
    status: str


class OpenAISoraError(Exception):
    """Base exception for OpenAI Sora API errors."""

    pass


class OpenAISoraClient:
    """Client for OpenAI Sora API."""

    def __init__(self) -> None:
        """Initialize the client with API key from config."""
        self.api_key: str | None = get_sora_api_key()
        self.timeout: float = 120.0
        self.max_retries: int = 3
        self.client: AsyncOpenAI | None = (
            AsyncOpenAI(api_key=self.api_key) if self.api_key else None
        )

    async def generate_video(
        self,
        prompt: str,
        duration_seconds: int = 5,
        size: str = "720p",
        model: str = "sora-2",
        input_image_url: str | None = None,
        duration: int | None = None,
        image_url: str | None = None,
    ) -> VideoJob:
        """Generate a video from a text prompt or image-to-video.

        Args:
            prompt: Text description of the video to generate
            duration_seconds: Duration of the video in seconds (5, 10, 15, 20, 30)
            size: Requested output resolution (provider-limited)
            model: Model to use for generation ("sora-2", "sora-2-pro")
            input_image_url: Optional source image URL for image-to-video
            duration: Backward-compatible alias for duration_seconds
            image_url: Backward-compatible alias for input_image_url

        Returns:
            VideoJob with job ID for polling

        Raises:
            OpenAISoraError: If video generation fails
        """
        if not self.api_key:
            raise OpenAISoraError("OPENAI_SORA_API_KEY not configured")

        if not self.client:
            raise OpenAISoraError("OpenAI client not initialized")

        if duration is not None:
            duration_seconds = duration
        if image_url and not input_image_url:
            input_image_url = image_url

        valid_durations = [5, 10, 15, 20, 30]
        if duration_seconds not in valid_durations:
            raise OpenAISoraError(
                f"Invalid duration. Must be one of: {valid_durations}"
            )

        valid_models = ["sora-2", "sora-2-pro"]
        if model not in valid_models:
            raise OpenAISoraError(f"Invalid model. Must be one of: {valid_models}")

        try:
            seconds: Literal["4", "8", "12"] = "12"
            if duration_seconds == 5:
                seconds = "4"
            elif duration_seconds == 10:
                seconds = "8"

            size_map: dict[
                str, Literal["720x1280", "1280x720", "1024x1792", "1792x1024"]
            ] = {
                "720p": "1280x720",
                "1280x720": "1280x720",
                "portrait": "720x1280",
                "720x1280": "720x1280",
                "landscape": "1280x720",
                "1024x1792": "1024x1792",
                "1792x1024": "1792x1024",
            }
            default_size: Literal["720x1280", "1280x720", "1024x1792", "1792x1024"] = (
                "1280x720"
            )
            api_size = size_map.get(size, default_size)

            if input_image_url:
                logger.info(
                    "Image-to-video requested with input_image_url; SDK image reference forwarding is not configured"
                )

            video = await self.client.videos.create(
                prompt=prompt,
                model=model,
                seconds=seconds,
                size=api_size,
            )

            job_id: str = video.id

            return VideoJob(
                job_id=job_id,
                prompt=prompt,
                duration_seconds=duration_seconds,
                source_image_url=input_image_url,
            )

        except Exception as e:
            raise OpenAISoraError(f"Video generation failed: {str(e)}")

    async def poll_job(self, video_id: str) -> VideoResult:
        """Poll for video generation job status.

        Args:
            video_id: ID of the video generation job

        Returns:
            VideoResult with video URL when complete

        Raises:
            OpenAISoraError: If polling fails or job expires
        """
        if not self.api_key:
            raise OpenAISoraError("OPENAI_SORA_API_KEY not configured")

        if not self.client:
            raise OpenAISoraError("OpenAI client not initialized")

        for attempt in range(self.max_retries):
            try:
                video = await self.client.videos.retrieve(video_id)
                status: str = getattr(video, "status", "unknown")

                if status == "completed":
                    url: str = getattr(video, "url", "") or ""
                    if not url:
                        raise OpenAISoraError("Video completed but URL not available")

                    prompt: str = getattr(video, "prompt", "") or ""
                    seconds: int = int(getattr(video, "seconds", 0) or 0)

                    return VideoResult(
                        url=url,
                        prompt=prompt,
                        duration_seconds=seconds,
                        source_image_url=None,
                        status="completed",
                    )
                elif status in ["pending", "processing", "queued", "in_progress"]:
                    await asyncio.sleep(5)
                    continue
                elif status == "failed":
                    raise OpenAISoraError("Video generation failed")
                elif status == "expired":
                    raise OpenAISoraError("Video generation job expired")
                else:
                    raise OpenAISoraError(f"Unknown video status: {status}")

            except httpx.TimeoutException:
                if attempt < self.max_retries - 1:
                    wait_time = float((2**attempt) + (0.1 * attempt))
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise OpenAISoraError("Request timeout after retries")
            except httpx.RequestError as e:
                if attempt < self.max_retries - 1:
                    wait_time = float((2**attempt) + (0.1 * attempt))
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise OpenAISoraError(f"Request error after retries: {str(e)}")
            except Exception as e:
                raise OpenAISoraError(f"Polling failed: {str(e)}")

        raise OpenAISoraError("Max retries exceeded")

    async def poll_video_job(self, job_id: str) -> VideoResult:
        """Backward-compatible alias for poll_job()."""
        return await self.poll_job(job_id)

    async def download_content(self, video_id: str) -> bytes:
        """Download generated video bytes by video ID."""
        result = await self.poll_job(video_id)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(result.url)
            _ = response.raise_for_status()
            return response.content
