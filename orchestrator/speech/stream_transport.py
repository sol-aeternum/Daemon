"""Additive private progressive HTTP adapter, with no synthesis replay.

The buffered provider contract remains unchanged. A streaming operation owns its
HTTP client until clean EOF or explicit close; callers do not reopen/retry POST.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any, Protocol, runtime_checkable

import httpx

from orchestrator.speech.contracts import SpeechError, SpeechRequest
from orchestrator.speech.provider import InternalSpeechProvider
from orchestrator.speech.stream_protocol import (
    StreamEvent,
    StreamParser,
    validate_content_type,
)


@runtime_checkable
class ProgressiveProvider(Protocol):
    name: str
    model: str

    async def health(self) -> bool: ...

    async def open_stream(self, speech: SpeechRequest) -> ProviderStream: ...


class ProviderStream:
    def __init__(
        self,
        client: httpx.AsyncClient,
        response: httpx.Response,
        speech: SpeechRequest,
        provider: str,
        model: str,
    ):
        self.client, self.response = client, response
        self.metadata: dict[str, Any] = {}
        self._closed = False
        self.parser = StreamParser(
            expected_metadata={
                "version": 1,
                "provider": provider,
                "model": model,
                "voice": speech.voice,
                "speed": speech.speed,
                "format": "mp3",
                "mime": "audio/mpeg",
                "sample_rate": 24000,
                "rendering": "speech-mp3-progressive-v1",
                "cached": False,
            }
        )
        self._iterator = self._read()

    async def start(self) -> ProviderStream:
        try:
            event = await anext(self._iterator)
            if event.kind != 0 or not isinstance(event.payload, dict):
                raise SpeechError("speech_protocol_error")
            self.metadata = event.payload
            return self
        except BaseException:
            await self.close()
            raise

    async def _read(self) -> AsyncIterator[StreamEvent]:
        try:
            # Do not use httpx's chunk_size aggregator: small heartbeats and the
            # initial metadata must arrive promptly, not wait for 256 bytes.
            # StreamParser itself processes input in bounded 256-byte slabs.
            async for chunk in self.response.aiter_bytes():
                for event in self.parser.feed(chunk):
                    yield event
            self.parser.finish()
        except httpx.TimeoutException:
            raise SpeechError("speech_stream_idle_timeout", 504) from None
        except httpx.HTTPError:
            raise SpeechError("speech_unavailable") from None

    def events(self) -> AsyncIterator[StreamEvent]:
        return self._iterator

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.response.aclose()
        finally:
            await self.client.aclose()


class InternalProgressiveProvider(InternalSpeechProvider):
    async def progressive_health(self) -> bool:
        try:
            async with httpx.AsyncClient(
                timeout=3, trust_env=False, follow_redirects=False
            ) as client:
                response = await client.get(f"{self.url}/ready")
                if response.status_code != 200:
                    return False
                data = response.json()
                profiles = data.get("capabilities", {}).get("progressive")
                return (
                    data.get("ready") is True
                    and data.get("provider") == self.name
                    and data.get("model") == self.model
                    and isinstance(profiles, list)
                    and any(
                        isinstance(profile, dict)
                        and type(profile.get("version")) is int
                        and profile.get("version") == 1
                        and profile.get("format") == "mp3"
                        and profile.get("mime") == "audio/mpeg"
                        and type(profile.get("sample_rate")) is int
                        and profile.get("sample_rate") == 24000
                        and profile.get("rendering") == "speech-mp3-progressive-v1"
                        for profile in profiles
                    )
                )
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    async def open_stream(self, speech: SpeechRequest) -> ProviderStream:
        if speech.format != "mp3":
            raise SpeechError("speech_stream_unsupported", 422)
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=3, write=10, pool=3),
            trust_env=False,
            follow_redirects=False,
        )
        response: httpx.Response | None = None
        try:
            request = client.build_request(
                "POST",
                f"{self.url}/synthesize/stream/v1",
                json=asdict(speech),
                headers={"Accept-Encoding": "identity"},
            )
            response = await client.send(request, stream=True)
            if response.status_code != 200:
                status, code = {
                    429: (429, "speech_busy"),
                    504: (504, "speech_timeout"),
                    413: (413, "speech_output_limit"),
                    422: (422, "speech_stream_unsupported"),
                }.get(response.status_code, (503, "speech_unavailable"))
                raise SpeechError(code, status)
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise SpeechError("speech_protocol_error")
            validate_content_type(response.headers.get("content-type", ""))
            stream = ProviderStream(client, response, speech, self.name, self.model)
            return await stream.start()
        except BaseException as error:
            try:
                if response is not None:
                    await response.aclose()
            finally:
                await client.aclose()
            if isinstance(error, httpx.TimeoutException):
                raise SpeechError("speech_stream_idle_timeout", 504) from None
            if isinstance(error, httpx.HTTPError):
                raise SpeechError("speech_unavailable") from None
            raise
