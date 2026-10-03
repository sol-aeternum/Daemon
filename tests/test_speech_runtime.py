"""Native-work admission tests; mocks never stand in for the real model smoke."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock, call

from fastapi.testclient import TestClient
import pytest

from orchestrator.speech.contracts import SpeechAudio, SpeechError, SpeechRequest
from tts.app import SynthesisSlot, create_app
from tts.runtime import KokoroRuntime, chunks
import tts.runtime as speech_runtime


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


def test_load_keeps_fp32_voice_and_bounded_cpu_without_spinning(tmp_path, monkeypatch):
    # Verify actual loader wiring without installing native/image-only dependencies.
    model, voices = tmp_path / "kokoro-v1.0.onnx", tmp_path / "voices-v1.0.bin"
    model.touch()
    voices.touch()
    options = SimpleNamespace(add_session_config_entry=Mock())
    session, samples = object(), object()
    ort = SimpleNamespace(
        disable_telemetry_events=Mock(),
        SessionOptions=Mock(return_value=options),
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"),
        InferenceSession=Mock(return_value=session),
    )
    engine = SimpleNamespace(
        get_voices=Mock(return_value=["af_heart"]),
        create=Mock(return_value=(samples, 24000)),
    )
    kokoro = SimpleNamespace(from_session=Mock(return_value=engine))
    modules = {"onnxruntime": ort, "kokoro_onnx": SimpleNamespace(Kokoro=kokoro)}
    original_import = speech_runtime.importlib.import_module

    def import_module(name, package=None):
        return modules[name] if name in modules else original_import(name, package)

    encoder = Mock(return_value=(b"encoded-warm-audio", 24000))
    monkeypatch.setattr(speech_runtime.importlib, "import_module", import_module)
    monkeypatch.setattr(speech_runtime, "encode", encoder)
    runtime = KokoroRuntime(tmp_path)
    runtime.load()

    assert options.intra_op_num_threads == 2
    assert options.inter_op_num_threads == 1
    assert options.execution_mode == ort.ExecutionMode.ORT_SEQUENTIAL
    assert options.add_session_config_entry.call_args_list == [
        call("session.intra_op.allow_spinning", "0"),
        call("session.inter_op.allow_spinning", "0"),
    ]
    ort.disable_telemetry_events.assert_called_once_with()
    ort.InferenceSession.assert_called_once_with(
        str(model), sess_options=options, providers=["CPUExecutionProvider"]
    )
    kokoro.from_session.assert_called_once_with(session, str(voices))
    assert runtime.engine is engine
    assert runtime.model == "kokoro-82m-v1.0"
    engine.create.assert_called_once_with("Daemon is ready.", voice="af_heart")
    assert encoder.call_args_list == [
        call(samples, 24000, "mp3"),
        call(samples, 24000, "opus"),
        call(samples, 24000, "wav"),
    ]
