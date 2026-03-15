"""@image subagent - image generation via multiple providers."""

from __future__ import annotations

import base64
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple
import uuid

import httpx
from openai import AsyncOpenAI

from orchestrator.subagents.base import BaseSubagent, SubagentResult, SubagentType
from providers.xai_imagine import XAIImagineClient, XAIImagineError
from providers.openai_sora import OpenAISoraClient, OpenAISoraError
from db.video_credits import VideoCreditsDAL
from config.video_pricing import estimate_cost
from orchestrator.config import get_settings, get_sora_api_key

logger = logging.getLogger(__name__)


class ImageProvider(ABC):
    """Abstract base class for image providers."""

    @abstractmethod
    async def generate_image(self, prompt: str, size: str) -> Dict[str, Any]:
        """Generate an image from a prompt.

        Args:
            prompt: Text description of the image to generate
            size: Size specification (e.g., "1024x1024")

        Returns:
            Dictionary with image data including:
                - url: Image URL
                - base64: Base64 encoded image data (optional)
                - width: Image width
                - height: Image height
                - provider: Provider name
        """
        pass

    async def generate_video(
        self, prompt: str, duration: int, **kwargs
    ) -> Dict[str, Any]:
        """Generate a video from a prompt.

        Args:
            prompt: Text description of the video to generate
            duration: Duration of the video in seconds
            **kwargs: Additional provider-specific parameters

        Returns:
            Dictionary with video data including:
                - url: Video URL
                - duration: Video duration in seconds
                - provider: Provider name
        """
        raise NotImplementedError("Video generation not supported by this provider")


class OpenRouterImageProvider(ImageProvider):
    """Image provider using Gemini via OpenRouter."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        """Initialize OpenRouter provider."""
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = 120.0

    async def generate_image(self, prompt: str, size: str) -> Dict[str, Any]:
        """Generate an image using OpenRouter."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://daemon.ai",
            "X-Title": "Daemon AI Assistant",
        }

        size_map = {
            "small": "1K",
            "medium": "2K",
            "large": "4K",
        }
        image_size = size_map.get(size, "1K")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
            "image_config": {"image_size": image_size},
        }

        async with httpx.AsyncClient() as client:
            endpoint = f"{self.base_url}/chat/completions"
            response = await client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                error_detail = exc.response.text
                try:
                    error_json = exc.response.json()
                    if isinstance(error_json, dict):
                        error_detail = error_json.get("error", error_detail)
                        if isinstance(error_detail, dict):
                            error_detail = error_detail.get(
                                "message", str(error_detail)
                            )
                except Exception:
                    pass  # Keep original text if JSON parsing fails

                logger.error(
                    f"[IMAGE DEBUG] OpenRouter API error: "
                    f"Status={exc.response.status_code}, "
                    f"Endpoint={endpoint}, "
                    f"Error={error_detail}"
                )

                raise RuntimeError(
                    f"OpenRouter chat completion failed "
                    f"({exc.response.status_code}): {error_detail}"
                ) from exc
            data = response.json()

            logger.debug(f"[IMAGE DEBUG] Response status: {response.status_code}")
            logger.debug(f"[IMAGE DEBUG] Response headers: {dict(response.headers)}")
            logger.debug(f"[IMAGE DEBUG] Full response: {json.dumps(data, indent=2)}")

            if data.get("error"):
                logger.error(
                    f"[IMAGE DEBUG] API error in response: {data.get('error')}"
                )
                raise RuntimeError(f"API error: {data.get('error')}")

            choices = data.get("choices") or []
            logger.debug(f"[IMAGE DEBUG] Number of choices: {len(choices)}")

            if not choices:
                logger.warning("[IMAGE DEBUG] No choices in response")
                raise RuntimeError("No choices in response")

            message = (choices[0] or {}).get("message") or {}
            logger.debug(f"[IMAGE DEBUG] Message keys: {list(message.keys())}")
            logger.debug(f"[IMAGE DEBUG] Full message: {json.dumps(message, indent=2)}")

            images = message.get("images") or []
            logger.debug(f"[IMAGE DEBUG] Images array length: {len(images)}")

            image_url = ""
            image_base64 = ""

            if images:
                image_info = images[0] or {}
                logger.debug(
                    f"[IMAGE DEBUG] Image info keys: {list(image_info.keys())}"
                )
                logger.debug(
                    f"[IMAGE DEBUG] Image info: {json.dumps(image_info, indent=2)}"
                )
                image_url = (image_info.get("image_url") or {}).get("url") or ""
            else:
                content = message.get("content")
                logger.debug(f"[IMAGE DEBUG] Content field type: {type(content)}")

                if content and isinstance(content, str):
                    logger.debug(
                        f"[IMAGE DEBUG] Content is string, length: {len(content)}"
                    )
                    if content.startswith("data:image"):
                        logger.info("[IMAGE DEBUG] Found image data in content field")
                        image_url = content
                    elif content.startswith("https://") or content.startswith(
                        "http://"
                    ):
                        logger.info("[IMAGE DEBUG] Found image URL in content field")
                        image_url = content
                elif content and isinstance(content, list):
                    logger.debug(
                        f"[IMAGE DEBUG] Content is list with {len(content)} items"
                    )
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "image_url":
                                image_url = part.get("image_url", {}).get("url", "")
                                if image_url:
                                    logger.info(
                                        "[IMAGE DEBUG] Found image_url in content list"
                                    )
                                    break
                            elif "image_url" in part:
                                image_url = part["image_url"]
                                if image_url:
                                    logger.info(
                                        "[IMAGE DEBUG] Found image_url in content part"
                                    )
                                    break

            if not image_url:
                logger.warning(
                    "[IMAGE DEBUG] No images found in response (checked images array and content field)"
                )
                logger.warning(
                    f"[IMAGE DEBUG] Full response structure: {json.dumps(data, indent=2)[:2000]}..."
                )
                raise RuntimeError(
                    "No images found in response - provider may have changed response format or model is unavailable"
                )

            if image_url.startswith("data:") and "base64," in image_url:
                image_base64 = image_url.split("base64,", 1)[1]
                logger.debug(
                    f"[IMAGE DEBUG] Extracted base64, length: {len(image_base64)}"
                )

            width, height = self._parse_size(size)

            return {
                "url": image_url,
                "base64": image_base64,
                "width": width,
                "height": height,
                "provider": "openrouter",
            }

    def _parse_size(self, size: str) -> Tuple[int, int]:
        """Parse size string to width and height."""
        size_map = {
            "small": (1024, 1024),
            "medium": (2048, 2048),
            "large": (4096, 4096),
        }

        if size in size_map:
            return size_map[size]

        try:
            width, height = map(int, size.split("x"))
            return width, height
        except (ValueError, AttributeError):
            return 1024, 1024


class OpenAISoraProvider(ImageProvider):
    """Video provider using OpenAI Sora."""

    def __init__(self, api_key: str) -> None:
        """Initialize OpenAI Sora provider."""
        self.client = OpenAISoraClient()
        if api_key:
            self.client.api_key = api_key
            if self.client.client:
                self.client.client.api_key = api_key

    async def generate_video(
        self, prompt: str, duration: int, **kwargs
    ) -> Dict[str, Any]:
        """Generate a video using OpenAI Sora."""
        try:
            resolution = kwargs.get("resolution") or "720p"

            # Map duration to Sora's supported values
            if duration <= 5:
                sora_duration = 5
            elif duration <= 10:
                sora_duration = 10
            else:
                sora_duration = 15  # Sora's max is 15 seconds for most models

            video_job = await self.client.generate_video(
                prompt=prompt,
                duration_seconds=sora_duration,
                size=resolution,
            )
            video_result = await self.client.poll_video_job(video_job.job_id)

            return {
                "url": video_result.url,
                "duration": video_result.duration_seconds,
                "provider": "openai_sora",
            }
        except OpenAISoraError as e:
            raise RuntimeError(f"OpenAI Sora video error: {str(e)}") from e

    async def generate_image(self, prompt: str, size: str) -> Dict[str, Any]:
        """Generate an image - not supported by Sora provider."""
        raise NotImplementedError("Image generation not supported by Sora provider")


class XAIImageProvider(ImageProvider):
    """Image provider using xAI Imagine."""

    def __init__(self, api_key: str) -> None:
        """Initialize xAI provider."""
        self.client = XAIImagineClient()
        if api_key:
            self.client.api_key = api_key

    async def generate_image(self, prompt: str, size: str) -> Dict[str, Any]:
        """Generate an image using xAI Imagine."""
        aspect_ratio = self._size_to_aspect_ratio(size)

        try:
            result = await self.client.generate_image(
                prompt=prompt, aspect_ratio=aspect_ratio, model="grok-4.1-image"
            )

            width, height = self._aspect_ratio_to_dimensions(aspect_ratio)

            return {
                "url": result.url,
                "base64": "",
                "width": width,
                "height": height,
                "provider": "xai",
            }
        except XAIImagineError as e:
            raise RuntimeError(f"xAI Imagine error: {str(e)}") from e

    async def generate_video(
        self, prompt: str, duration: int, **kwargs
    ) -> Dict[str, Any]:
        """Generate a video using xAI Imagine."""
        try:
            video_job = await self.client.generate_video(
                prompt=prompt, duration_seconds=duration
            )
            video_result = await self.client.poll_video_job(video_job.job_id)

            return {
                "url": video_result.url,
                "duration": video_result.duration_seconds,
                "provider": "xai",
            }
        except XAIImagineError as e:
            raise RuntimeError(f"xAI Imagine video error: {str(e)}") from e

    def _size_to_aspect_ratio(self, size: str) -> str:
        """Convert size string to aspect ratio."""
        size_map = {
            "small": "1:1",
            "medium": "1:1",
            "large": "1:1",
            "1024x1024": "1:1",
            "2048x2048": "1:1",
            "4096x4096": "1:1",
        }

        if size in size_map:
            return size_map[size]

        try:
            width, height = map(int, size.split("x"))
            ratio = width / height
            if ratio >= 2.0:
                return "16:9"
            elif ratio <= 0.5:
                return "9:16"
            elif 1.2 <= ratio < 1.8:
                return "4:3"
            elif 0.6 <= ratio < 1.2:
                return "3:4"
            else:
                return "1:1"
        except (ValueError, AttributeError):
            return "1:1"

    def _aspect_ratio_to_dimensions(self, aspect_ratio: str) -> Tuple[int, int]:
        """Convert aspect ratio to width and height."""
        ratio_map = {
            "1:1": (1024, 1024),
            "16:9": (1920, 1080),
            "9:16": (1080, 1920),
            "4:3": (1440, 1080),
            "3:4": (1080, 1440),
        }

        return ratio_map.get(aspect_ratio, (1024, 1024))


class ImageSubagent(BaseSubagent):
    """Image generation subagent using multiple providers."""

    agent_type = SubagentType.IMAGE
    description = (
        "Generates images from text prompts using AI (multiple providers supported)"
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize image subagent."""
        super().__init__(config)

        config_dict = config or {}
        self.provider_name = (
            os.environ.get("TIER_PRO_IMAGE_PROVIDER")
            or config_dict.get("image_provider")
            or "openrouter"
        ).lower()
        if self.provider_name == "sora":
            self.provider_name = "openai_sora"

        # For video mode, check if a specific provider is requested
        self.video_provider_name = (
            os.environ.get("TIER_PRO_VIDEO_PROVIDER")
            or config_dict.get("video_provider")
            or self.provider_name
        ).lower()
        if self.video_provider_name == "sora":
            self.video_provider_name = "openai_sora"

        if self.provider_name == "xai":
            xai_api_key = (
                config_dict.get("xai_api_key") if config_dict else None
            ) or os.environ.get("XAI_API_KEY", "")
            self.provider = XAIImageProvider(xai_api_key)
        elif self.provider_name == "openai_sora":
            openai_api_key = (
                (config_dict.get("openai_sora_api_key") if config_dict else None)
                or (config_dict.get("openai_api_key") if config_dict else None)
                or os.environ.get("OPENAI_SORA_API_KEY", "")
                or get_sora_api_key()
                or ""
            )
            self.provider = OpenAISoraProvider(openai_api_key)
        else:
            openrouter_api_key = (
                config_dict.get("openrouter_api_key") if config_dict else None
            ) or os.environ.get("OPENROUTER_API_KEY")

            openrouter_base_url = (
                config_dict.get("openrouter_base_url") if config_dict else None
            ) or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            openrouter_base_url = openrouter_base_url.rstrip("/")

            if openrouter_base_url.endswith("/chat/completions"):
                openrouter_base_url = openrouter_base_url[: -len("/chat/completions")]
            if openrouter_base_url.endswith("/images/generations"):
                openrouter_base_url = openrouter_base_url[: -len("/images/generations")]
            if (
                "openrouter.ai" in openrouter_base_url
                and "/api/v1" not in openrouter_base_url
            ):
                openrouter_base_url = "https://openrouter.ai/api/v1"

            openrouter_model = (
                config_dict.get("image_model") if config_dict else None
            ) or os.environ.get(
                "OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image"
            )

            if not openrouter_api_key:
                raise ValueError("OPENROUTER_API_KEY not configured")

            self.provider = OpenRouterImageProvider(
                openrouter_api_key, openrouter_base_url, openrouter_model
            )

    async def execute(
        self, task: str, context: dict[str, Any] | None = None
    ) -> SubagentResult:
        """Execute image or video generation task.

        Args:
            task: The generation prompt/description
            context: Optional context (may include size, style preferences, mode, duration, user_id, tier)

        Returns:
            SubagentResult with image/video data or error
        """
        context_payload = context or {}
        task_for_prompt = self._apply_history(task, context_payload)
        enhanced_prompt = self._enhance_prompt(task_for_prompt, context_payload)
        mode = context_payload.get("mode", "image")

        if mode == "video":
            return await self._generate_video(enhanced_prompt, context_payload)
        else:
            return await self._generate_image(enhanced_prompt, context_payload)

    async def _generate_image(
        self, prompt: str, context: dict[str, Any]
    ) -> SubagentResult:
        try:
            size = context.get("size", "1024x1024")
            image_result = await self.provider.generate_image(prompt, size)

            image_base64 = image_result.get("base64", "")
            image_url = image_result.get("url", "")
            provider = image_result.get("provider", self.provider_name)
            width = image_result.get("width", 1024)
            height = image_result.get("height", 1024)

            if image_base64 or image_url:
                return self._create_result(
                    success=True,
                    data={
                        "prompt": prompt,
                        "enhanced_prompt": prompt,
                        "image_base64": image_base64,
                        "image_url": image_url,
                        "width": width,
                        "height": height,
                        "format": "png",
                    },
                    metadata={
                        "provider": provider,
                        "size": size,
                    },
                )
            else:
                return self._create_result(
                    success=False,
                    error="Image generation returned empty result",
                )

        except Exception as e:
            return self._create_result(
                success=False,
                error=f"Image generation failed: {str(e)}",
            )

    async def _generate_video(
        self, prompt: str, context: dict[str, Any]
    ) -> SubagentResult:
        # Get user ID and tier from context
        user_id_str = context.get("user_id")
        tier = context.get("tier", "free").lower()

        # Get tier configuration
        settings = get_settings()
        tier_config = settings.get_tier_config(tier)

        # Validate tier - Check if video generation is enabled for this tier
        if not tier_config.tier_video_enabled:
            return self._create_result(
                success=False,
                error=f"Video generation is not available for {tier.capitalize()} tier users. Please upgrade to a higher tier.",
            )

        # Get duration from context, default to 5 seconds
        duration_seconds = context.get("duration", 5)

        # Enforce duration limits per tier
        if (
            tier_config.tier_video_max_duration is not None
            and duration_seconds > tier_config.tier_video_max_duration
        ):
            duration_seconds = tier_config.tier_video_max_duration

        # Get video credits DAL from config
        db_pool = self.config.get("db_pool") if self.config else None
        if not db_pool:
            return self._create_result(
                success=False,
                error="Database pool not configured for video credit operations",
            )

        video_credits_dal = VideoCreditsDAL(db_pool)

        # Convert user_id to UUID
        try:
            user_id = uuid.UUID(user_id_str) if user_id_str else None
        except ValueError:
            return self._create_result(
                success=False,
                error="Invalid user ID format",
            )

        if not user_id:
            return self._create_result(
                success=False,
                error="User ID is required for video generation",
            )

        # Determine video provider based on context or tier config
        video_provider_name = self.video_provider_name
        if context.get("video_provider"):
            video_provider_name = context["video_provider"].lower()
        if video_provider_name == "sora":
            video_provider_name = "openai_sora"
        if video_provider_name == "openai-sora":
            video_provider_name = "openai_sora"

        # Calculate cost AFTER determining provider
        cost_int = estimate_cost(
            duration_seconds=duration_seconds,
            tier=tier,
            provider=video_provider_name,
            resolution=context.get("resolution"),
        )

        # BYOK tier uses own API key, skip credit check/debit
        transaction_id = None
        if cost_int > 0:
            balance = await video_credits_dal.get_balance(user_id)
            if balance < cost_int:
                return self._create_result(
                    success=False,
                    error=f"Insufficient video credits. Required: {cost_int}, Available: {balance}",
                )

            debit_result = await video_credits_dal.debit_credits(
                user_id, cost_int, f"Video generation: {prompt[:50]}...", None
            )

            if not debit_result.success:
                return self._create_result(
                    success=False,
                    error=f"Failed to debit video credits: {debit_result.message}",
                )

            transaction_id = debit_result.transaction_id

        # Create appropriate video provider if needed
        video_provider = self.provider
        if video_provider_name != self.provider_name:
            if video_provider_name == "xai":
                xai_api_key = (
                    self.config.get("xai_api_key") if self.config else None
                ) or os.environ.get("XAI_API_KEY", "")
                video_provider = XAIImageProvider(xai_api_key)
            elif video_provider_name == "openai_sora":
                openai_api_key = (
                    (self.config.get("openai_sora_api_key") if self.config else None)
                    or (self.config.get("openai_api_key") if self.config else None)
                    or os.environ.get("OPENAI_SORA_API_KEY", "")
                    or get_sora_api_key()
                    or ""
                )
                video_provider = OpenAISoraProvider(openai_api_key)
            else:
                # Fallback to current provider
                video_provider = self.provider

        # Check if the selected provider supports video generation
        try:
            video_result = await video_provider.generate_video(
                prompt=prompt,
                duration=duration_seconds,
                resolution=context.get("resolution"),
            )
        except NotImplementedError:
            refund_result = (
                await video_credits_dal.refund_transaction(transaction_id)
                if transaction_id
                else None
            )
            return self._create_result(
                success=False,
                error=f"Video generation is not supported with {video_provider_name} provider",
                data={"refunded": bool(refund_result and refund_result.success)},
                metadata={
                    "provider": video_provider_name,
                    "refunded": bool(refund_result and refund_result.success),
                    "refund_message": refund_result.message if refund_result else None,
                    "cost": cost_int,
                },
            )
        except Exception as e:
            # Refund credits on failure
            refund_result = None
            if transaction_id:
                refund_result = await video_credits_dal.refund_transaction(
                    transaction_id
                )

            return self._create_result(
                success=False,
                error=f"Video generation failed: {str(e)}",
                data={"refunded": bool(refund_result and refund_result.success)},
                metadata={
                    "provider": video_provider_name,
                    "duration": duration_seconds,
                    "cost": cost_int,
                    "refunded": bool(refund_result and refund_result.success),
                    "refund_message": refund_result.message if refund_result else None,
                },
            )

        # Return video metadata in result
        return self._create_result(
            success=True,
            data={
                "prompt": prompt,
                "video_url": video_result["url"],
                "duration_seconds": duration_seconds,
                "format": "mp4",
            },
            metadata={
                "provider": video_result["provider"],
                "duration": duration_seconds,
                "cost": cost_int,
            },
        )

    def _enhance_prompt(self, task: str, context: dict[str, Any]) -> str:
        """Enhance user prompt with style/size preferences from context."""
        style = context.get("style", "")
        size = context.get("size", "1024x1024")

        enhanced = task

        if style:
            enhanced = f"{enhanced}, style: {style}"

        quality_keywords = ["high quality", "detailed", "professional"]
        if not any(kw in task.lower() for kw in quality_keywords):
            enhanced = f"high quality, detailed, {enhanced}"

        return enhanced

    def _apply_history(self, task: str, context: dict[str, Any]) -> str:
        history = context.get("history")
        if not history or not isinstance(history, list):
            return task

        last = history[-1] if history else None
        if not isinstance(last, dict):
            return task

        last_task = last.get("task") if isinstance(last.get("task"), str) else ""
        result = last.get("result") if isinstance(last.get("result"), dict) else {}
        last_prompt = ""
        if isinstance(result, dict):
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            if isinstance(data, dict):
                last_prompt = (
                    data.get("prompt") if isinstance(data.get("prompt"), str) else ""
                )

        previous = last_prompt or last_task
        if not previous:
            return task

        lowered = task.lower()
        followup_markers = [
            "again",
            "retry",
            "another",
            "different",
            "not ",
            "change",
            "adjust",
            "fix",
            "try again",
            "redo",
        ]
        if (
            any(marker in lowered for marker in followup_markers)
            or len(task.split()) <= 6
        ):
            return f"{task}. Previous request: {previous}"

        return task
