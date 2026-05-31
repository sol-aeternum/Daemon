"""xAI Imagine API client for image and video generation."""

from __future__ import annotations

import asyncio
import logging

import httpx
from pydantic import BaseModel

from orchestrator.config import get_settings

logger = logging.getLogger(__name__)


class ImageResult(BaseModel):
    """Result from image generation."""

    url: str
    prompt: str
    model: str
    aspect_ratio: str


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


class XAIImagineError(Exception):
    """Base exception for XAI Imagine API errors."""

    pass


class XAIImagineClient:
    """Client for xAI Imagine API."""

    def __init__(self) -> None:
        """Initialize the client with API key from config."""
        settings = get_settings()
        self.api_key: str = settings.xai_api_key
        self.base_url: str = "https://api.x.ai/v1"
        self.timeout: float = 120.0
        self.max_retries: int = 3

    async def generate_image(
        self, prompt: str, aspect_ratio: str = "1:1", model: str = "grok-4.1-image"
    ) -> ImageResult:
        """Generate an image from a text prompt.

        Args:
            prompt: Text description of the image to generate
            aspect_ratio: Aspect ratio of the image (e.g., "1:1", "16:9")
            model: Model to use for generation

        Returns:
            ImageResult with image URL and metadata

        Raises:
            XAIImagineError: If image generation fails
        """
        if not self.api_key:
            raise XAIImagineError("XAI_API_KEY not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {"prompt": prompt, "aspect_ratio": aspect_ratio, "model": model}

        endpoint = f"{self.base_url}/images/generations"

        async with httpx.AsyncClient() as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(
                        endpoint, headers=headers, json=payload, timeout=self.timeout
                    )

                    if response.status_code == 200:
                        data = response.json()
                        url: str = data["url"]
                        return ImageResult(
                            url=url,
                            prompt=prompt,
                            model=model,
                            aspect_ratio=aspect_ratio,
                        )
                    elif response.status_code in [429, 500, 502, 503, 504]:
                        # Retry with exponential backoff
                        if attempt < self.max_retries - 1:
                            wait_time = (2**attempt) + (0.1 * attempt)
                            await asyncio.sleep(float(wait_time))
                            continue
                        else:
                            raise XAIImagineError(
                                f"API error after {self.max_retries} retries: {response.status_code} - {response.text}"
                            )
                    else:
                        raise XAIImagineError(
                            f"API error: {response.status_code} - {response.text}"
                        )

                except httpx.TimeoutException:
                    if attempt < self.max_retries - 1:
                        wait_time = (2**attempt) + (0.1 * attempt)
                        await asyncio.sleep(float(wait_time))
                        continue
                    else:
                        raise XAIImagineError("Request timeout after retries")
                except httpx.RequestError as e:
                    if attempt < self.max_retries - 1:
                        wait_time = (2**attempt) + (0.1 * attempt)
                        await asyncio.sleep(float(wait_time))
                        continue
                    else:
                        raise XAIImagineError(f"Request error after retries: {str(e)}")

            raise XAIImagineError("Max retries exceeded")

    async def generate_video(
        self,
        prompt: str,
        duration_seconds: int = 5,
        source_image_url: str | None = None,
    ) -> VideoJob:
        """Generate a video from a text prompt or image-to-video.

        Args:
            prompt: Text description of the video to generate
            duration_seconds: Duration of the video in seconds (max 15)
            source_image_url: Optional URL of source image for image-to-video

        Returns:
            VideoJob with job ID for polling

        Raises:
            XAIImagineError: If video generation fails
        """
        if not self.api_key:
            raise XAIImagineError("XAI_API_KEY not configured")

        # Limit duration to API maximum
        duration_seconds = min(duration_seconds, 15)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {"prompt": prompt, "duration_seconds": duration_seconds}

        # Add source image if provided
        if source_image_url:
            payload["source_image_url"] = source_image_url

        endpoint = f"{self.base_url}/videos/generations"

        async with httpx.AsyncClient() as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(
                        endpoint, headers=headers, json=payload, timeout=self.timeout
                    )

                    if response.status_code == 200:
                        data = response.json()
                        job_id: str = data["request_id"]
                        return VideoJob(
                            job_id=job_id,
                            prompt=prompt,
                            duration_seconds=duration_seconds,
                            source_image_url=source_image_url,
                        )
                    elif response.status_code in [429, 500, 502, 503, 504]:
                        # Retry with exponential backoff
                        if attempt < self.max_retries - 1:
                            wait_time = (2**attempt) + (0.1 * attempt)
                            await asyncio.sleep(float(wait_time))
                            continue
                        else:
                            raise XAIImagineError(
                                f"API error after {self.max_retries} retries: {response.status_code} - {response.text}"
                            )
                    else:
                        raise XAIImagineError(
                            f"API error: {response.status_code} - {response.text}"
                        )

                except httpx.TimeoutException:
                    if attempt < self.max_retries - 1:
                        wait_time = (2**attempt) + (0.1 * attempt)
                        await asyncio.sleep(float(wait_time))
                        continue
                    else:
                        raise XAIImagineError("Request timeout after retries")
                except httpx.RequestError as e:
                    if attempt < self.max_retries - 1:
                        wait_time = (2**attempt) + (0.1 * attempt)
                        await asyncio.sleep(float(wait_time))
                        continue
                    else:
                        raise XAIImagineError(f"Request error after retries: {str(e)}")

            raise XAIImagineError("Max retries exceeded")

    async def poll_video_job(self, job_id: str) -> VideoResult:
        """Poll for video generation job status.

        Args:
            job_id: ID of the video generation job

        Returns:
            VideoResult with video URL when complete

        Raises:
            XAIImagineError: If polling fails or job expires
        """
        if not self.api_key:
            raise XAIImagineError("XAI_API_KEY not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        endpoint = f"{self.base_url}/videos/{job_id}"

        async with httpx.AsyncClient() as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.get(endpoint, headers=headers, timeout=self.timeout)

                    if response.status_code == 200:
                        data = response.json()
                        video_data = data.get("video", {})
                        status: str = str(video_data.get("status", "")).lower()

                        if status == "finished":
                            url: str = video_data["url"]["generation"]
                            prompt_list = video_data["settings"].get("prompt", [""])
                            prompt_str: str = prompt_list[0] if prompt_list else ""
                            return VideoResult(
                                url=url,
                                prompt=prompt_str,
                                duration_seconds=0,  # Not provided in response
                                source_image_url=None,  # Not provided in response
                                status="finished",
                            )
                        elif status in ["pending", "processing"]:
                            # Still processing, continue polling
                            await asyncio.sleep(5)  # Wait 5 seconds before next poll
                            continue
                        elif status == "failed":
                            raise XAIImagineError("Video generation failed")
                        elif status == "expired":
                            raise XAIImagineError("Video generation job expired")
                        else:
                            raise XAIImagineError(f"Unknown video status: {status}")

                    elif response.status_code in [429, 500, 502, 503, 504]:
                        # Retry with exponential backoff
                        if attempt < self.max_retries - 1:
                            wait_time = (2**attempt) + (0.1 * attempt)
                            await asyncio.sleep(float(wait_time))
                            continue
                        else:
                            raise XAIImagineError(
                                f"API error after {self.max_retries} retries: {response.status_code} - {response.text}"
                            )
                    else:
                        raise XAIImagineError(
                            f"API error: {response.status_code} - {response.text}"
                        )

                except httpx.TimeoutException:
                    if attempt < self.max_retries - 1:
                        wait_time = (2**attempt) + (0.1 * attempt)
                        await asyncio.sleep(float(wait_time))
                        continue
                    else:
                        raise XAIImagineError("Request timeout after retries")
                except httpx.RequestError as e:
                    if attempt < self.max_retries - 1:
                        wait_time = (2**attempt) + (0.1 * attempt)
                        await asyncio.sleep(float(wait_time))
                        continue
                    else:
                        raise XAIImagineError(f"Request error after retries: {str(e)}")

            raise XAIImagineError("Max retries exceeded")
