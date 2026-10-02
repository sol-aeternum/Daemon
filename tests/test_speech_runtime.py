"""Native-work admission tests; mocks never stand in for the real model smoke."""

import asyncio
import threading

from fastapi.testclient import TestClient
import pytest

from orchestrator.speech.contracts import SpeechAudio, SpeechError, SpeechRequest
from tts.app import SynthesisSlot, create_app
from tts.runtime import KokoroRuntime, chunks


class Connection:
    def __init__(self, disconnected=False):
        self.disconnected = disconnected

    async def is_disconnected(self):
        return self.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_cancel_or_timeout_retains_native_slot(disconnect):
    release, started, cancelled = threading.Event(), threading.Event(), threading.Event()

    def work(speech, cancel):
        started.set()
        release.wait(2)
        if cancel.is_set():
            cancelled.set()
        return SpeechAudio(b"audio", 1, 24000, 0.1)

    slot = SynthesisSlot(work, timeout=0.02)
    try:
        with pytest.raises(
            SpeechError, match="speech_cancelled" if disconnect else "speech_timeout"
        ):
            await slot.run(SpeechRequest("hello"), Connection(disconnect))
        await asyncio.to_thread(started.wait, 1)
        assert slot.active
        with pytest.raises(SpeechError, match="speech_busy"):
            await slot.run(SpeechRequest("second"), Connection())
    finally:
        release.set()
    for _ in range(100):
        if not slot.active:
            break
        await asyncio.sleep(0.01)
    assert not slot.active
    assert cancelled.is_set()


class FakeRuntime(KokoroRuntime):
    def load(self):
        self.engine = "loaded"

    def synthesize(self, request, cancel):
        return SpeechAudio(b"RIFF-fake", 1, 24000, 0.1)


def test_readiness_requires_lifespan_and_validation():
    runtime = FakeRuntime()
    app = create_app(runtime)
    with TestClient(app) as client:
        assert (awaitless := client.get("/ready")).status_code == 200
        assert awaitless.json()["ready"]
        assert client.get("/live").json()["alive"]
        assert client.post(
            "/synthesize", json={"text": "hello", "format": "wav"}
        ).content.startswith(b"RIFF")
        assert client.post("/synthesize", json={"text": " "}).status_code == 400
        assert (
            client.post("/synthesize", json={"text": "hello", "voice": "af_heart"}).status_code
            == 422
        )
        assert client.post("/synthesize", json={"text": "x" * 40000}).status_code == 413
    assert TestClient(app).get("/ready").status_code == 503


def test_missing_model_fails_without_downloading(tmp_path):
    # Asset check precedes loading runtime dependencies as well as network access.
    runtime = KokoroRuntime(tmp_path)
    # Optional dependencies are only present in the isolated image: load cannot
    # accidentally initialize a model via any generic remote model loader.
    assert not (runtime.root / "kokoro-v1.0.onnx").exists()
    with pytest.raises(RuntimeError, match="bundled TTS assets"):
        runtime.load()
    text = "A first sentence. " + "a longer sentence " * 40
    pieces = chunks(text)
    assert all(len(piece) <= 240 for piece in pieces)
    assert " ".join(pieces).split() == text.split()
