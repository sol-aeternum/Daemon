"""xAI Imagine API client for image and video generation."""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
from pydantic import BaseModel, PrivateAttr

from orchestrator.config import get_settings

logger = logging.getLogger(__name__)


class _ClientReferenceMixin(BaseModel):
    """Mixin exposing a python-internal, non-serialized client reference.

    The reference is generated before each non-idempotent submit and is kept
    only in memory on the result object (a private Pydantic attribute, never
    part of the serialized wire/API fields). It is local correlation only,
    not a provider idempotency or lookup key. Durable provider registration
    remains later work (ACCOUNT_DELETION_DESIGN §6.2).
    """

    _client_reference: str | None = PrivateAttr(default=None)

    @property
    def client_reference(self) -> str | None:
        """Opaque client reference generated before this submit, in-memory only."""
        return self._client_reference


class ImageResult(_ClientReferenceMixin):
    """Result from image generation."""

    url: str
    prompt: str
    model: str
    aspect_ratio: str


class VideoJob(_ClientReferenceMixin):
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

    def __init__(
        self,
        message: str,
        client_reference: str | None = None,
    ) -> None:
        """Retain local correlation, not provider lookup, outside the message."""
        super().__init__(message)
        self.client_reference = client_reference


class XAIImagineClient:
    """Client for xAI Imagine API."""

    def __init__(self) -> None:
        """Initialize the client with API key from config."""
        settings = get_settings()
        self.api_key: str = settings.xai_api_key
        self.base_url: str = "https://api.x.ai/v1"
        self.timeout: float = 120.0
        self.max_retries: int = 3  # Read-only polling only, never submission.

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

        # Opaque client reference generated BEFORE the non-idempotent submit.
        # The submit is exactly one POST: a non-idempotent generation must not
        # be retried on timeout, transport error, 429 or 5xx, because a retry
        # could create a second provider-side job/asset that deletion can no
        # longer attribute. Failures raise with the reference attached.
        client_reference = str(uuid.uuid4())
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    endpoint, headers=headers, json=payload, timeout=self.timeout
                )
            except httpx.TimeoutException:
                raise XAIImagineError(
                    "Request timeout: outcome unconfirmed; not retried",
                    client_reference=client_reference,
                )
            except httpx.RequestError as e:
                raise XAIImagineError(
                    f"Request error; not retried: {e}",
                    client_reference=client_reference,
                )

            if response.status_code == 200:
                try:
                    data = response.json()
                    url: str = data["url"]
                    if not isinstance(url, str) or not url:
                        raise ValueError("Missing image URL")
                except (ValueError, KeyError, TypeError) as exc:
                    # Malformed response: never resubmit; retain the reference.
                    raise XAIImagineError(
                        f"API error: malformed response - {type(exc).__name__}",
                        client_reference=client_reference,
                    )
                result = ImageResult(
                    url=url,
                    prompt=prompt,
                    model=model,
                    aspect_ratio=aspect_ratio,
                )
                result._client_reference = client_reference
                return result

            # Any non-200 (including 429 and 5xx): never resubmit.
            raise XAIImagineError(
                f"API error; not retried: {response.status_code} - {response.text}",
                client_reference=client_reference,
            )

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

        # Opaque client reference generated BEFORE the non-idempotent submit.
        # Exactly one POST: video submission must never be retried on
        # timeout, transport error, 429, 5xx or a lost/malformed response
        # (the "response is lost" case in ACCOUNT_DELETION_DESIGN §8), since a
        # retry could create a second provider job that deletion cannot
        # attribute. Failures retain the local correlation reference.
        client_reference = str(uuid.uuid4())
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    endpoint, headers=headers, json=payload, timeout=self.timeout
                )
            except httpx.TimeoutException:
                raise XAIImagineError(
                    "Request timeout: outcome unconfirmed; not retried",
                    client_reference=client_reference,
                )
            except httpx.RequestError as e:
                raise XAIImagineError(
                    f"Request error; not retried: {e}",
                    client_reference=client_reference,
                )

            if response.status_code == 200:
                try:
                    data = response.json()
                    job_id: str = data["request_id"]
                    if not isinstance(job_id, str) or not job_id:
                        raise ValueError("Missing provider request id")
                except (ValueError, KeyError, TypeError) as exc:
                    raise XAIImagineError(
                        f"API error: malformed response - {type(exc).__name__}",
                        client_reference=client_reference,
                    )
                video_job = VideoJob(
                    job_id=job_id,
                    prompt=prompt,
                    duration_seconds=duration_seconds,
                    source_image_url=source_image_url,
                )
                video_job._client_reference = client_reference
                return video_job

            # Any non-200 (including 429 and 5xx): never resubmit.
            raise XAIImagineError(
                f"API error; not retried: {response.status_code} - {response.text}",
                client_reference=client_reference,
            )

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
