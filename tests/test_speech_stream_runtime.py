"""Shared native lifetime and bounded queue; real codec checks are optional here."""

import asyncio
from io import BytesIO
import math
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

from fastapi.testclient import TestClient
import pytest

from orchestrator.speech.contracts import SpeechAudio, SpeechError, SpeechRequest
from orchestrator.speech.stream_protocol import (
    AUDIO,
    COMPLETE,
    MAGIC,
    METADATA,
    StreamParser,
    encode_audio,
    encode_frame,
    metadata,
)
from tts.app import SynthesisSlot, create_app
from tts.runtime import KokoroRuntime
from tts.streaming import (
    ByteQueue,
    ContinuousMP3Encoder,
    QUEUE_BYTES,
    QUEUE_FRAMES,
    completion,
)
import tts.streaming as streaming


class Connection:
    async def is_disconnected(self):
        return False


def test_completion_deadline_is_typed_timeout_not_invalid_terminal():
    encoder = SimpleNamespace(check=lambda: None, frames=1, audio_bytes=5)
    with pytest.raises(SpeechError, match="speech_timeout"):
        completion(cast(Any, encoder), 1.0, 1.048, time.monotonic() - 120.01)


async def exited(slot):
    async with asyncio.timeout(2):
        while slot._workers:
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_cancelled_http_waiter_cannot_cancel_or_release_buffered_native():
    release, started = threading.Event(), threading.Event()

    def work(request, cancel):
        started.set()
        release.wait(2)
        assert cancel.is_set()
        return SpeechAudio(b"a", 1, 24000, 0.1)

    slot = SynthesisSlot(work)
    task = asyncio.create_task(slot.run(SpeechRequest("hello"), Connection()))
    await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    try:
        assert slot.active
        with pytest.raises(SpeechError, match="speech_busy"):
            slot.start_stream(SpeechRequest("next"), lambda *args: None)
    finally:
        release.set()
        await exited(slot)
    assert not slot.active


@pytest.mark.asyncio
async def test_progressive_native_blocked_retains_shared_busy_until_actual_exit():
    release, started = threading.Event(), threading.Event()
    calls = []

    def stream(request, cancel, queue, deadline):
        calls.append(request.text)
        started.set()
        release.wait(2)
        assert cancel.is_set()

    slot = SynthesisSlot(lambda *args: SpeechAudio(b"a", 1, 24000, 0.1))
    queue = slot.start_stream(SpeechRequest("hello"), stream)
    await asyncio.to_thread(started.wait, 1)
    queue.stop()
    try:
        assert slot.active
        with pytest.raises(SpeechError, match="speech_busy"):
            await slot.run(SpeechRequest("buffered"), Connection())
        with pytest.raises(SpeechError, match="speech_busy"):
            slot.start_stream(SpeechRequest("second"), stream)
    finally:
        release.set()
        await exited(slot)
    assert not slot.active and calls == ["hello"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"x", b"x" * 65532])
async def test_full_queue_cancel_wakes_blocked_put_and_releases_on_exit(payload):
    blocked = threading.Event()

    def stream(request, cancel, queue, deadline):
        frame = encode_audio(0, payload)
        while queue.queued_frames < QUEUE_FRAMES and queue.queued_bytes + len(frame) <= QUEUE_BYTES:
            queue.put(frame)
        blocked.set()
        queue.put(frame)

    slot = SynthesisSlot(lambda *args: SpeechAudio(b"a", 1, 24000, 0.1))
    queue = slot.start_stream(SpeechRequest("hello"), stream)
    await asyncio.to_thread(blocked.wait, 1)
    assert queue.queued_bytes <= QUEUE_BYTES and queue.queued_frames <= QUEUE_FRAMES
    assert slot.active
    queue.stop()
    await exited(slot)
    assert not slot.active
    with pytest.raises(SpeechError, match="speech_cancelled"):
        await anext(queue.frames())


@pytest.mark.asyncio
async def test_continuous_drain_stall_bound(monkeypatch):
    monkeypatch.setattr(streaming, "DRAIN_SECONDS", 0.02)
    queue = ByteQueue(threading.Event(), time.monotonic() + 1)
    for _ in range(QUEUE_FRAMES):
        queue.put(encode_audio(0, b"x"))
    with pytest.raises(SpeechError, match="speech_backpressure_timeout"):
        await asyncio.to_thread(queue.put, encode_audio(0, b"x"))
    queue.stop()


@pytest.mark.asyncio
async def test_stale_exit_callback_does_not_release_another_lease():
    release, started = threading.Event(), threading.Event()

    def work():
        started.set()
        release.wait(2)

    slot = SynthesisSlot(lambda *args: SpeechAudio(b"a", 1, 24000, 0.1))
    slot._start(work)
    await asyncio.to_thread(started.wait, 1)
    successor = object()
    slot._lease = successor
    release.set()
    await exited(slot)
    assert slot.active and slot._lease is successor


class FakeRuntime(KokoroRuntime):
    def load(self):
        self.progressive_ready = True

    def synthesize(self, request, cancel):
        return SpeechAudio(b"wav", 1, 24000, 0.1)

    def stream(self, request, cancel, queue, deadline):
        queue.put(encode_audio(0, b"mp3"))
        queue.put(
            encode_frame(
                COMPLETE,
                {
                    "frames": 1,
                    "bytes": 3,
                    "source_seconds": 1,
                    "encoded_seconds": 1.056,
                    "synthesis_seconds": 0.1,
                    "audio_path": None,
                    "cache_available": False,
                },
            )
        )


def test_private_route_ready_formats_validation_busy_before_200():
    runtime = FakeRuntime()
    app = create_app(runtime)
    assert TestClient(app).post("/synthesize/stream/v1", json={"text": "hello"}).status_code == 503
    with TestClient(app) as client:
        ready = client.get("/ready").json()["capabilities"]
        assert ready["streaming"] is False and ready["progressive"][0]["version"] == 1
        response = client.post("/synthesize/stream/v1", json={"text": "hello"})
        assert response.status_code == 200
        parser = StreamParser()
        list(parser.feed(response.content))
        parser.finish()
        assert client.post("/synthesize", json={"text": "hello", "format": "wav"}).content == b"wav"
        for payload, status in (
            ({"text": " "}, 400),
            ({"text": "hello", "format": "wav"}, 422),
            ({"text": "hello", "voice": "af_heart"}, 422),
        ):
            assert client.post("/synthesize/stream/v1", json=payload).status_code == status
        app.state.slot.active = True
        try:
            response = client.post("/synthesize/stream/v1", json={"text": "hello"})
            assert response.status_code == 429 and "DSP1" not in response.text
            assert client.post("/synthesize", json={"text": "hello"}).status_code == 429
        finally:
            app.state.slot.active = False
        runtime.progressive_ready = False
        assert client.get("/ready").json()["capabilities"]["progressive"] == []
        assert client.post("/synthesize/stream/v1", json={"text": "hello"}).status_code == 422


def test_runtime_chunks_one_encoder_and_cancels_after_uninterruptible_create(monkeypatch):
    cancel = threading.Event()
    calls = []
    queue = SimpleNamespace(put=lambda frame: None)

    class Encoder:
        def __init__(self, *args):
            calls.append("open")

        def check(self):
            if cancel.is_set():
                raise SpeechError("speech_cancelled")

        def add(self, audio, rate):
            self.check()
            calls.append("add")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(streaming, "ContinuousMP3Encoder", Encoder)
    runtime = KokoroRuntime()

    def create(*args, **kwargs):
        cancel.set()
        return [1], 24000

    runtime.engine = SimpleNamespace(create=create)
    with pytest.raises(SpeechError, match="speech_cancelled"):
        runtime.stream(SpeechRequest("hello"), cancel, queue, time.monotonic() + 1)
    assert calls == ["open", "close"]


@pytest.mark.parametrize(
    "audio,rate,code",
    [
        ([], 24000, "invalid_speech_output"),
        ([float("nan")], 24000, "invalid_speech_output"),
        ([float("inf")], 24000, "invalid_speech_output"),
        ([1], 48000, "invalid_speech_output"),
        ([1], True, "invalid_speech_output"),
        ([[1]], 24000, "invalid_speech_output"),
    ],
)
def test_pcm_validation_before_encoder_block(audio, rate, code):
    class Samples(list):
        @property
        def ndim(self):
            return 2 if self and isinstance(self[0], list) else 1

    np = SimpleNamespace(
        asarray=Samples,
        isfinite=lambda values: SimpleNamespace(
            all=lambda: all(math.isfinite(value) for value in values)
        ),
    )
    encoder = ContinuousMP3Encoder.__new__(ContinuousMP3Encoder)
    setattr(encoder, "np", np)
    encoder.cancel = threading.Event()
    encoder.deadline, encoder.source_samples = time.monotonic() + 1, 0
    with pytest.raises(SpeechError, match=code):
        encoder.add(audio, rate)


def test_cumulative_source_limit_before_encoding():
    encoder = ContinuousMP3Encoder.__new__(ContinuousMP3Encoder)

    class Samples(list):
        ndim = 1

    setattr(
        encoder,
        "np",
        SimpleNamespace(asarray=Samples, isfinite=lambda values: SimpleNamespace(all=lambda: True)),
    )
    encoder.cancel, encoder.deadline = threading.Event(), time.monotonic() + 1
    encoder.source_samples = 300 * 24000
    with pytest.raises(SpeechError, match="speech_output_too_long"):
        encoder.add([1], 24000)


@pytest.mark.parametrize("count", [1, 120, 24000, 7200000])
def test_optional_real_codec_padding_and_emitted_frame_sum(count):
    av = pytest.importorskip(
        "av", reason="PyAV belongs to the isolated TTS image, not root dependencies"
    )
    np = pytest.importorskip("numpy")
    frames = []
    started = time.monotonic()
    encoder = ContinuousMP3Encoder(frames.append, threading.Event(), started + 120)
    try:
        for offset in range(0, count, 4096):
            encoder.add(np.zeros(min(4096, count - offset), dtype=np.float32), 24000)
        source, encoded = encoder.finish()
    finally:
        encoder.close()
    parser = StreamParser()
    data = []
    for frame in [
        MAGIC
        + encode_frame(METADATA, metadata("kokoro", "kokoro-82m-v1.0", SpeechRequest("test"))),
        *frames,
        completion(encoder, source, encoded, started),
    ]:
        for event in parser.feed(frame):
            if event.kind == AUDIO:
                data.append(event.payload)
    parser.finish()
    with av.open(BytesIO(b"".join(data)), format="mp3") as container:
        decoded = sum(frame.samples for frame in container.decode(audio=0)) / 24000
    assert source == count / 24000
    assert 0 <= encoded - source <= 0.15
    assert decoded == encoded <= 300.15
