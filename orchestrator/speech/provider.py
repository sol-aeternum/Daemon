"""Internal HTTP transport adapter. Runtime-specific schemas never reach callers."""

from dataclasses import asdict
import math

import httpx

from orchestrator.speech.contracts import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    SpeechAudio,
    SpeechCapabilities,
    SpeechError,
    SpeechRequest,
)


class InternalSpeechProvider:
    def __init__(self, *, name: str, model: str, url: str, timeout: float):
        self.name, self.model = name, model
        self.url, self.timeout = url.rstrip("/"), timeout

    def capabilities(self) -> SpeechCapabilities:
        return SpeechCapabilities()

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(f"{self.url}/ready")
                data = response.json()
                return (
                    response.status_code == 200
                    and data.get("ready") is True
                    and data.get("provider") == self.name
                    and data.get("model") == self.model
                )
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def synthesize(self, request: SpeechRequest) -> SpeechAudio:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                async with client.stream(
                    "POST", f"{self.url}/synthesize", json=asdict(request)
                ) as response:
                    if response.status_code != 200:
                        if response.status_code == 429:
                            raise SpeechError("speech_busy", 429)
                        if response.status_code == 504:
                            raise SpeechError("speech_timeout", 504)
                        if response.status_code == 413:
                            raise SpeechError("speech_output_limit", 413)
                        raise SpeechError("speech_unavailable")
                    if (
                        response.headers.get("x-speech-provider") != self.name
                        or response.headers.get("x-speech-model") != self.model
                    ):
                        raise SpeechError("speech_identity_mismatch")
                    duration = float(response.headers["x-audio-duration"])
                    elapsed = float(response.headers["x-synthesis-seconds"])
                    rate = int(response.headers["x-audio-sample-rate"])
                    if not (
                        math.isfinite(duration)
                        and 0 < duration <= MAX_AUDIO_SECONDS
                        and math.isfinite(elapsed)
                        and 0 <= elapsed <= 180
                        and 8000 <= rate <= 192000
                    ):
                        raise SpeechError("invalid_speech_output")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_AUDIO_BYTES:
                            raise SpeechError("speech_output_too_large")
                    if not content:
                        raise SpeechError("empty_speech_output")
                    return SpeechAudio(bytes(content), duration, rate, elapsed)
        except httpx.TimeoutException as exc:
            raise SpeechError("speech_timeout", 504) from exc
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise SpeechError("speech_unavailable") from exc
