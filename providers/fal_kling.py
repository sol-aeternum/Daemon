"""fal.ai Kling video generation client."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import fal_client
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class VideoJob(BaseModel):
    """Video generation job."""

    job_id: str
    prompt: str
    duration_seconds: int
    source_image_url: Optional[str] = None
    kling_model: str = "o3-pro"
    audio_enabled: bool = False


class VideoResult(BaseModel):
    """Result from video generation."""

    url: str
    prompt: str
    duration_seconds: int
    source_image_url: Optional[str] = None
    status: str
    kling_model: str = "o3-pro"
    audio_enabled: bool = False


class FalKlingError(Exception):
    """Base exception for Fal Kling API errors."""

    pass


class FalKlingClient:
    """Client for fal.ai Kling video generation API."""

    def __init__(self) -> None:
        """Initialize the client with API key from environment."""
        self.api_key: str = os.environ.get("FAL_KEY", "")
        if not self.api_key:
            raise FalKlingError("FAL_KEY not configured")

    def _get_model_endpoint(self, kling_model: str, has_source_image: bool) -> str:
        """Get the appropriate model endpoint based on model type and input."""
        if kling_model == "v3-pro":
            if has_source_image:
                return "fal-ai/kling-video/v3/pro/image-to-video"
            else:
                return "fal-ai/kling-video/v3/pro/text-to-video"
        else:
            if has_source_image:
                return "fal-ai/kling-video/o3/pro/image-to-video"
            else:
                return "fal-ai/kling-video/o3/pro/text-to-video"

    async def generate_video(
        self,
        prompt: str,
        duration_seconds: int = 5,
        source_image_url: Optional[str] = None,
        kling_model: str = "o3-pro",
        audio_enabled: bool = False,
    ) -> VideoJob:
        """Generate a video from a text prompt or image-to-video.

        Args:
            prompt: Text description of the video to generate
            duration_seconds: Duration of the video in seconds (3-15)
            source_image_url: Optional URL of source image for image-to-video
            kling_model: Kling model to use ("o3-pro" or "v3-pro")
            audio_enabled: Whether to generate native audio for the video

        Returns:
            VideoJob with job ID for polling

        Raises:
            FalKlingError: If video generation fails
        """
        if not self.api_key:
            raise FalKlingError("FAL_KEY not configured")

        duration_seconds = max(3, min(duration_seconds, 15))

        if kling_model not in ["o3-pro", "v3-pro"]:
            kling_model = "o3-pro"

        try:
            endpoint = self._get_model_endpoint(kling_model, bool(source_image_url))

            arguments: Dict[str, Any] = {
                "prompt": prompt,
                "duration": duration_seconds,
            }

            if source_image_url:
                arguments["image_url"] = source_image_url

            if audio_enabled:
                arguments["audio_enabled"] = True

            result = await fal_client.submit_async(
                endpoint,
                arguments=arguments,
            )

            return VideoJob(
                job_id=result.request_id,
                prompt=prompt,
                duration_seconds=duration_seconds,
                source_image_url=source_image_url,
                kling_model=kling_model,
                audio_enabled=audio_enabled,
            )

        except Exception as e:
            raise FalKlingError(f"Failed to submit video generation job: {str(e)}")

    async def poll_video_job(self, job: VideoJob) -> VideoResult:
        """Poll for video generation job status.

        Args:
            job: VideoJob with job ID to poll

        Returns:
            VideoResult with video URL when complete

        Raises:
            FalKlingError: If polling fails or job expires
        """
        if not self.api_key:
            raise FalKlingError("FAL_KEY not configured")

        try:
            endpoint = self._get_model_endpoint(job.kling_model, bool(job.source_image_url))

            result = await fal_client.result_async(
                endpoint,
                request_id=job.job_id,
            )

            if result and "video" in result:
                video_data = result["video"]
                if "url" in video_data:
                    return VideoResult(
                        url=video_data["url"],
                        prompt=job.prompt,
                        duration_seconds=job.duration_seconds,
                        source_image_url=job.source_image_url,
                        status="finished",
                        kling_model=job.kling_model,
                        audio_enabled=job.audio_enabled,
                    )

            raise FalKlingError("Video generation job failed or returned invalid result")

        except Exception as e:
            raise FalKlingError(f"Failed to poll video generation job: {str(e)}")
