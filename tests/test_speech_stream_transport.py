"""Real httpx stream adapter with deterministic offline transport; no replay."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock
import uuid

from fastapi import FastAPI
import httpx
import pytest

from orchestrator.speech.contracts import SpeechError, SpeechRequest
from orchestrator.speech.stream_protocol import (
    CONTENT_TYPE,
    MAGIC,
    encode_audio,
    encode_frame,
    metadata,
)
from orchestrator.speech.stream_transport import InternalProgressiveProvider
from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import Settings, get_settings
from orchestrator.routes import speech_stream as routes
from tts.app import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_qualified", [True, False])
async def test_real_runtime_ready_through_provider_and_public_capability_gate(
    monkeypatch, runtime_qualified
):
    runtime = SimpleNamespace(
        name="kokoro",
        model="fixture",
        progressive_ready=runtime_qualified,
        load=Mock(),
        synthesize=Mock(),
    )
    private = create_app(cast(Any, runtime))
    original_client = httpx.AsyncClient
    calls = []

    async def handle(request):
        calls.append((request.method, request.url.path))
        async with original_client(
            transport=httpx.ASGITransport(app=private), base_url="http://private"
        ) as client:
            return await client.send(request)

    provider = transport(monkeypatch, handle)
    public = FastAPI()
    public.include_router(routes.router)
    monkeypatch.setattr(routes, "get_speech_provider", lambda _: provider)
    public.dependency_overrides[require_device_auth] = lambda: AuthenticatedDevice(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    )
    public.dependency_overrides[get_settings] = lambda: cast(Any, Settings)(_env_file=None)
    async with private.router.lifespan_context(private):
        async with original_client(
            transport=httpx.ASGITransport(app=private), base_url="http://private"
        ) as client:
            readiness = (await client.get("/ready")).json()
        assert isinstance(readiness["capabilities"]["progressive"], list)
        assert await provider.progressive_health() is runtime_qualified
        async with original_client(
            transport=httpx.ASGITransport(app=public), base_url="http://public"
        ) as client:
            # Gate off is always the actual production source default.
            response = await client.get("/tts/capabilities")
            assert response.status_code == 200 and response.json()["streams"] == []
            monkeypatch.setattr(routes, "PROGRESSIVE_SPEECH_QUALIFIED", True)
            response = await client.get("/tts/capabilities")
            assert response.status_code == 200
            assert response.json()["streams"] == (
                [
                    {
                        "version": 1,
                        "format": "mp3",
                        "mime": "audio/mpeg",
                        "sample_rate": 24000,
                        "rendering": "speech-mp3-progressive-v1",
                    }
                ]
                if runtime_qualified
                else []
            )
    assert calls and all(method == "GET" and path == "/ready" for method, path in calls)
    runtime.synthesize.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {},
        [],
        [None],
        [
            {
                "version": True,
                "format": "mp3",
                "mime": "audio/mpeg",
                "sample_rate": 24000,
                "rendering": "speech-mp3-progressive-v1",
            }
        ],
        [
            {
                "version": 1,
                "format": "mp3",
                "mime": "audio/mpeg",
                "sample_rate": 24000,
                "rendering": "wrong",
            }
        ],
    ],
)
async def test_invalid_or_unqualified_runtime_profile_never_qualifies(monkeypatch, invalid):
    provider = transport(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            json={
                "ready": True,
                "provider": "kokoro",
                "model": "fixture",
                "capabilities": {"progressive": invalid},
            },
        ),
    )
    assert not await provider.progressive_health()


def wire():
    speech = SpeechRequest("Fictional streaming transport.")
    preamble = MAGIC + encode_frame(0, metadata("kokoro", "fixture", speech))
    audio = encode_audio(0, b"fixture")
    complete = encode_frame(
        2,
        {
            "frames": 1,
            "bytes": 7,
            "source_seconds": 1,
            "encoded_seconds": 1.048,
            "synthesis_seconds": 0.1,
            "audio_path": None,
            "cache_available": False,
        },
    )
    return speech, preamble, audio, complete


def transport(monkeypatch, handle):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "orchestrator.speech.stream_transport.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    return InternalProgressiveProvider(
        name="kokoro", model="fixture", url="http://private", timeout=125
    )


class Bytes(httpx.AsyncByteStream):
    def __init__(self, *parts):
        self.parts = parts
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            yield part

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_initial_metadata_does_not_wait_for_audio_or_byte_aggregation(monkeypatch):
    speech, preamble, audio, complete = wire()
    release = asyncio.Event()

    class Paused(Bytes):
        async def __aiter__(self):
            yield preamble
            await release.wait()
            yield audio + complete

    body = Paused()
    provider = transport(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"Content-Type": CONTENT_TYPE}, stream=body),
    )
    stream = await asyncio.wait_for(provider.open_stream(speech), 0.1)
    assert stream.metadata["model"] == "fixture"
    release.set()
    events = [event async for event in stream.events()]
    assert [event.kind for event in events] == [1, 2]
    await stream.close()
    assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["redirect", "busy", "wrong-mime", "reset-after-terminal", "wrong-owner-model"]
)
async def test_provider_failure_never_replays_started_post(monkeypatch, mode):
    speech, preamble, audio, complete = wire()
    calls = []

    class Reset(Bytes):
        async def __aiter__(self):
            yield preamble + audio + complete
            raise httpx.ReadError("fictional connection reset")

    def handle(request):
        calls.append(request)
        if mode == "redirect":
            return httpx.Response(307, headers={"Location": "http://other/synthesize/stream/v1"})
        if mode == "busy":
            return httpx.Response(429)
        if mode == "wrong-mime":
            return httpx.Response(
                200, headers={"Content-Type": "audio/mpeg"}, stream=Bytes(preamble)
            )
        if mode == "wrong-owner-model":
            return httpx.Response(
                200,
                headers={"Content-Type": CONTENT_TYPE},
                stream=Bytes(
                    MAGIC + encode_frame(0, metadata("kokoro", "different-model", speech))
                ),
            )
        return httpx.Response(200, headers={"Content-Type": CONTENT_TYPE}, stream=Reset())

    provider = transport(monkeypatch, handle)
    opened = None
    try:
        with pytest.raises(SpeechError):
            opened = await provider.open_stream(speech)
            _ = [event async for event in opened.events()]
    finally:
        if opened is not None:
            await opened.close()
    assert len(calls) == 1 and calls[0].method == "POST"
