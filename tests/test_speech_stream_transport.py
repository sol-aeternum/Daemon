"""Real httpx stream adapter with deterministic offline transport; no replay."""

import asyncio

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
