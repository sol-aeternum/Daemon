"""Application boundary: provider choice, bounded execution and payload-free telemetry."""

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging
import time

from orchestrator.config import Settings
from orchestrator.speech.contracts import SpeechAudio, SpeechError, SpeechProvider, SpeechRequest
from orchestrator.speech.stream_transport import InternalProgressiveProvider

logger = logging.getLogger(__name__)
_active = 0


def get_speech_provider(settings: Settings) -> SpeechProvider:
    # Add a provider factory here, not in route/client code. There is no implicit
    # fallback to an external service. Server config selects deployment identity.
    if settings.tts_provider != "kokoro":
        raise SpeechError("speech_configuration_error")
    return InternalProgressiveProvider(
        name=settings.tts_provider,
        model=settings.tts_model,
        url=settings.tts_service_url,
        timeout=settings.tts_timeout_seconds,
    )


@asynccontextmanager
async def speech_admission() -> AsyncIterator[None]:
    global _active
    if _active >= 4:
        raise SpeechError("speech_busy", 429)
    _active += 1
    try:
        yield
    finally:
        _active -= 1


async def synthesize(
    provider: SpeechProvider, request: SpeechRequest, timeout: float
) -> SpeechAudio:
    started = time.monotonic()
    outcome, audio = "failure", None
    try:
        async with speech_admission(), asyncio.timeout(timeout):
            audio = await provider.synthesize(request)
            outcome = "success"
            return audio
    except TimeoutError as exc:
        outcome = "timeout"
        raise SpeechError("speech_timeout", 504) from exc
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        logger.info(
            "speech_usage provider=%s model=%s characters=%d audio_seconds=%.3f "
            "synthesis_seconds=%.3f wall_seconds=%.3f outcome=%s",
            provider.name,
            provider.model,
            len(request.text),
            audio.duration if audio else 0,
            audio.synthesis_seconds if audio else 0,
            time.monotonic() - started,
            outcome,
        )
