"""@image subagent - image generation via Gemini (OpenRouter)."""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

import httpx

from orchestrator.subagents.base import BaseSubagent, SubagentResult, SubagentType

logger = logging.getLogger(__name__)


class ImageSubagent(BaseSubagent):
    """Image generation subagent using Gemini via OpenRouter."""

    agent_type = SubagentType.IMAGE
    description = "Generates images from text prompts using AI (Gemini 2.5 Flash Image via OpenRouter)"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize image subagent."""
        super().__init__(config)
        self.api_key = config.get("openrouter_api_key") if config else None
        self.api_key = self.api_key or os.environ.get("OPENROUTER_API_KEY")
        self.base_url = (
            (config.get("openrouter_base_url") if config else None)
            or os.environ.get("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
        if self.base_url.endswith("/images/generations"):
            self.base_url = self.base_url[: -len("/images/generations")]
        if "openrouter.ai" in self.base_url and "/api/v1" not in self.base_url:
            self.base_url = "https://openrouter.ai/api/v1"
        self.model = (
            (config.get("image_model") if config else None)
            or os.environ.get("OPENROUTER_IMAGE_MODEL")
            or "google/gemini-2.5-flash-image"
        )
        self.timeout = 120.0  # Images take longer

    async def execute(
        self, task: str, context: dict[str, Any] | None = None
    ) -> SubagentResult:
        """Execute image generation task.

        Args:
            task: The image generation prompt/description
            context: Optional context (may include size, style preferences)

        Returns:
            SubagentResult with image data (base64) or error
        """
        if not self.api_key:
            return self._create_result(
                success=False,
                error="OPENROUTER_API_KEY not configured",
            )

        # Enhance prompt based on context
        context_payload = context or {}
        task_for_prompt = self._apply_history(task, context_payload)
        enhanced_prompt = self._enhance_prompt(task_for_prompt, context_payload)

        # Generate image via OpenRouter
        try:
            size = (context or {}).get("size", "1024x1024")
            image_result = await self._generate_image(enhanced_prompt, size)

            image_base64 = image_result.get("image_base64") if image_result else None
            image_url = image_result.get("image_url") if image_result else None

            if image_base64 or image_url:
                return self._create_result(
                    success=True,
                    data={
                        "prompt": task,
                        "enhanced_prompt": enhanced_prompt,
                        "image_base64": image_base64,
                        "image_url": image_url,
                        "model": self.model,
                        "format": "png",
                    },
                    metadata={
                        "provider": "openrouter",
                        "model": self.model,
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

    def _enhance_prompt(self, task: str, context: dict[str, Any]) -> str:
        """Enhance user prompt with style/size preferences from context."""
        style = context.get("style", "")
        size = context.get("size", "1024x1024")

        enhanced = task

        if style:
            enhanced = f"{enhanced}, style: {style}"

        # Add quality modifiers based on common requests
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

    async def _generate_image(self, prompt: str, size: str) -> dict[str, str] | None:
        """Generate image via OpenRouter chat completions API.

        Returns:
            Base64 encoded image data or None if failed
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://daemon.ai",  # OpenRouter requires this
            "X-Title": "Daemon AI Assistant",
        }

        request_model = self.model
        # OpenRouter API accepts model names with or without openrouter/ prefix
        # Keep the full name as OpenRouter's gateway handles the routing
        # Gemini 2.5 Flash Image expects: "google/gemini-2.5-flash-image"

        size_map = {
            "small": "1K",
            "medium": "2K",
            "large": "4K",
        }
        image_size = size_map.get(size, "1K")
        payload: dict[str, Any] = {
            "model": request_model,
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
                raise RuntimeError(
                    "OpenRouter chat completion failed "
                    f"({exc.response.status_code}) at {endpoint}: {exc.response.text}"
                ) from exc
            data = response.json()

            # DEBUG: Log full response structure
            logger.debug(f"[IMAGE DEBUG] Response status: {response.status_code}")
            logger.debug(f"[IMAGE DEBUG] Response headers: {dict(response.headers)}")
            logger.debug(f"[IMAGE DEBUG] Full response: {json.dumps(data, indent=2)}")

            if data.get("error"):
                logger.error(
                    f"[IMAGE DEBUG] API error in response: {data.get('error')}"
                )
                return None

            choices = data.get("choices") or []
            logger.debug(f"[IMAGE DEBUG] Number of choices: {len(choices)}")

            if not choices:
                logger.warning("[IMAGE DEBUG] No choices in response")
                return None

            message = (choices[0] or {}).get("message") or {}
            logger.debug(f"[IMAGE DEBUG] Message keys: {list(message.keys())}")
            logger.debug(f"[IMAGE DEBUG] Full message: {json.dumps(message, indent=2)}")

            images = message.get("images") or []
            logger.debug(f"[IMAGE DEBUG] Images array length: {len(images)}")

            image_url = ""
            image_base64 = ""

            if images:
                # Standard OpenAI-like format with images array
                image_info = images[0] or {}
                logger.debug(
                    f"[IMAGE DEBUG] Image info keys: {list(image_info.keys())}"
                )
                logger.debug(
                    f"[IMAGE DEBUG] Image info: {json.dumps(image_info, indent=2)}"
                )
                image_url = (image_info.get("image_url") or {}).get("url") or ""
            else:
                # Check if image is in content field (OpenRouter/Gemini format)
                content = message.get("content")
                logger.debug(f"[IMAGE DEBUG] Content field type: {type(content)}")

                if content and isinstance(content, str):
                    logger.debug(
                        f"[IMAGE DEBUG] Content is string, length: {len(content)}"
                    )
                    # Check if content contains image data URL
                    if content.startswith("data:image"):
                        logger.info("[IMAGE DEBUG] Found image data in content field")
                        image_url = content
                    elif content.startswith("https://") or content.startswith(
                        "http://"
                    ):
                        logger.info("[IMAGE DEBUG] Found image URL in content field")
                        image_url = content
                elif content and isinstance(content, list):
                    # Content might be a list of content parts (OpenAI format)
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
                return None

            # Extract base64 from data URL if present
            if image_url.startswith("data:") and "base64," in image_url:
                image_base64 = image_url.split("base64,", 1)[1]
                logger.debug(
                    f"[IMAGE DEBUG] Extracted base64, length: {len(image_base64)}"
                )

            return {
                "image_base64": image_base64,
                "image_url": image_url,
            }
