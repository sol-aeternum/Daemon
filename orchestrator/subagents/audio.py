"""@audio subagent - sound effects generation via ElevenLabs."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

import httpx

from orchestrator.subagents.base import BaseSubagent, SubagentResult, SubagentType

logger = logging.getLogger(__name__)


class AudioSubagent(BaseSubagent):
    """Audio/Sound Effects generation subagent using ElevenLabs Sound Effects API."""

    agent_type = SubagentType.AUDIO
    description = "Generates sound effects and audio clips from text descriptions using AI (ElevenLabs Sound Effects)"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize audio subagent."""
        super().__init__(config)
        self.api_key = config.get("elevenlabs_api_key") if config else None
        self.api_key = self.api_key or os.environ.get("ELEVENLABS_API_KEY")
        self.base_url = "https://api.elevenlabs.io/v1"
        self.timeout = 60.0  # Sound effects are faster than images

    async def execute(
        self, task: str, context: dict[str, Any] | None = None
    ) -> SubagentResult:
        """Execute sound effect generation task.

        Args:
            task: The sound effect description (e.g., "dog barking", "car engine revving")
            context: Optional context (may include duration_seconds, prompt_influence)

        Returns:
            SubagentResult with audio data (base64) or error
        """
        if not self.api_key:
            return self._create_result(
                success=False,
                error="ELEVENLABS_API_KEY not configured",
            )

        context_payload = context or {}

        # The task IS the sound description - no need for prompt enhancement
        sound_description = task.strip()
        if not sound_description:
            return self._create_result(
                success=False,
                error="Sound description cannot be empty",
            )

        try:
            # Get optional parameters from context
            duration_seconds = context_payload.get("duration_seconds", 10)
            prompt_influence = context_payload.get("prompt_influence", 0.5)

            # Generate sound effect via ElevenLabs
            audio_result = await self._generate_sound_effect(
                sound_description,
                duration_seconds=duration_seconds,
                prompt_influence=prompt_influence,
            )

            audio_base64 = audio_result.get("audio_base64")
            audio_url = audio_result.get("audio_url")

            if audio_base64 or audio_url:
                return self._create_result(
                    success=True,
                    data={
                        "prompt": sound_description,
                        "audio_base64": audio_base64,
                        "audio_url": audio_url,
                        "duration_seconds": duration_seconds,
                        "format": "mp3",
                    },
                    metadata={
                        "provider": "elevenlabs",
                        "model": "sound-effects",
                        "duration_seconds": duration_seconds,
                    },
                )
            else:
                return self._create_result(
                    success=False,
                    error="Sound effect generation returned empty result",
                )

        except Exception as e:
            logger.error(f"Sound effect generation failed: {e}")
            return self._create_result(
                success=False,
                error=f"Sound effect generation failed: {str(e)}",
            )

    async def _generate_sound_effect(
        self, text: str, duration_seconds: int = 10, prompt_influence: float = 0.5
    ) -> dict[str, Any]:
        """Generate sound effect using ElevenLabs Sound Effects API.

        Args:
            text: Text description of the sound effect
            duration_seconds: Duration in seconds (1-22 for ElevenLabs)
            prompt_influence: How closely to follow the prompt (0.0-1.0)

        Returns:
            Dict with audio_base64 or audio_url
        """
        url = f"{self.base_url}/sound-generation"

        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        # ElevenLabs limits duration to 22 seconds for sound effects
        duration_seconds = min(duration_seconds, 22)

        body = {
            "text": text,
            "duration_seconds": duration_seconds,
            "prompt_influence": prompt_influence,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()

            # ElevenLabs returns raw audio bytes
            audio_bytes = response.content

            if audio_bytes:
                # Convert to base64 for embedding in messages
                audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
                return {
                    "audio_base64": audio_base64,
                    "format": "mp3",
                }
            else:
                return {}
