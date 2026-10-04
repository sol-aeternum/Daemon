"""Kokoro adapter: fixed offline files, CPU session, central voice mapping."""

from collections.abc import Callable
from io import BytesIO
from pathlib import Path
import re
import importlib
import logging
import threading
import time
from typing import Any

from orchestrator.speech.contracts import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    SpeechAudio,
    SpeechError,
    SpeechRequest,
)

MODEL = "kokoro-82m-v1.0"
VOICE_MAP = {"daemon-default": "af_heart"}


def chunks(text: str) -> list[str]:
    # Semantic splitting with a hard per-native-call bound. No text truncation.
    result: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        while len(sentence) > 240:
            split = sentence.rfind(" ", 0, 240)
            if split <= 0:
                split = 240
            result.append(sentence[:split])
            sentence = sentence[split:].lstrip()
        if sentence.strip():
            result.append(sentence.strip())
    return result


def encode(samples: Any, sample_rate: int, format: str) -> tuple[bytes, int]:
    av = importlib.import_module("av")
    np = importlib.import_module("numpy")

    output = BytesIO()
    container_format, codec = {
        "wav": ("wav", "pcm_s16le"),
        "mp3": ("mp3", "libmp3lame"),
        "opus": ("ogg", "libopus"),
    }[format]
    encoded_rate = 48000 if format == "opus" else sample_rate
    with av.open(output, mode="w", format=container_format) as container:
        stream = container.add_stream(codec, rate=encoded_rate)
        stream.layout = "mono"
        if format != "wav":
            stream.bit_rate = 64000
        resampler = av.AudioResampler(
            format=stream.codec_context.format, layout="mono", rate=encoded_rate
        )
        for offset in range(0, len(samples), 4096):
            frame = av.AudioFrame.from_ndarray(
                np.asarray(samples[offset : offset + 4096], dtype=np.float32).reshape(1, -1),
                format="flt",
                layout="mono",
            )
            frame.sample_rate = sample_rate
            for converted in resampler.resample(frame):
                for packet in stream.encode(converted):
                    container.mux(packet)
        for converted in resampler.resample(None):
            for packet in stream.encode(converted):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue(), encoded_rate


class KokoroRuntime:
    name = "kokoro"
    model = MODEL

    def __init__(self, root: Path = Path("/opt/models")):
        self.root = root
        self.engine: Any = None
        self.progressive_ready = False

    def load(self) -> None:
        self.progressive_ready = False
        # Fail on absent assets; no from_pretrained/download/remote model loader.
        model_path = self.root / "kokoro-v1.0.onnx"
        voices_path = self.root / "voices-v1.0.bin"
        if not model_path.is_file() or not voices_path.is_file():
            raise RuntimeError("Required bundled TTS assets are unavailable")
        ort = importlib.import_module("onnxruntime")
        Kokoro = importlib.import_module("kokoro_onnx").Kokoro
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # Sleeping idle workers avoid wasting the container's bounded CPU quota.
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.engine = Kokoro.from_session(session, str(voices_path))
        if VOICE_MAP["daemon-default"] not in self.engine.get_voices():
            raise RuntimeError("Required bundled TTS voice is unavailable")
        # Actual phonemization, inference and every advertised encoder before ready.
        audio, rate = self.engine.create("Daemon is ready.", voice=VOICE_MAP["daemon-default"])
        for format in ("mp3", "opus", "wav"):
            content, _ = encode(audio, rate, format)
            if not content:
                raise RuntimeError("TTS warm synthesis failed")
        from tts.streaming import qualify_encoder

        try:
            qualify_encoder(audio, rate)
        except Exception:
            # A new encoder qualification failure cannot take established
            # buffered codecs offline. No payload/model details in this log.
            logging.getLogger(__name__).warning("speech_progressive_qualification_failed")
        else:
            self.progressive_ready = True

    def stream(
        self, request: SpeechRequest, cancel: threading.Event, queue: Any, deadline: float
    ) -> None:
        from tts.streaming import ContinuousMP3Encoder, completion

        started = time.monotonic()
        encoder = ContinuousMP3Encoder(queue.put, cancel, deadline)
        try:
            for text in chunks(request.text):
                encoder.check()
                audio, rate = self.engine.create(
                    text, voice=VOICE_MAP[request.voice], speed=request.speed, lang="en-us"
                )
                # A cancelled native call may have finished, but may not encode or
                # publish that abandoned chunk. The owning thread still closes.
                encoder.add(audio, rate)
            source, encoded = encoder.finish()
            encoder.check()
            queue.put(completion(encoder, source, encoded, started))
        finally:
            encoder.close()

    def synthesize(self, request: SpeechRequest, cancel: threading.Event) -> SpeechAudio:
        np = importlib.import_module("numpy")

        started = time.monotonic()
        parts, count, rate = [], 0, 24000
        for text in chunks(request.text):
            if cancel.is_set():
                raise SpeechError("speech_cancelled", 499)
            audio, rate = self.engine.create(
                text, voice=VOICE_MAP[request.voice], speed=request.speed, lang="en-us"
            )
            count += len(audio)
            if count / rate > MAX_AUDIO_SECONDS:
                raise SpeechError("speech_output_too_long", 413)
            parts.append(audio)
        if cancel.is_set():
            raise SpeechError("speech_cancelled", 499)
        samples = np.concatenate(parts)
        if not len(samples) or not np.isfinite(samples).all():
            raise SpeechError("invalid_speech_output")
        content, encoded_rate = encode(samples, rate, request.format)
        if len(content) > MAX_AUDIO_BYTES:
            raise SpeechError("speech_output_too_large")
        return SpeechAudio(content, count / rate, encoded_rate, time.monotonic() - started)


SynthesizeFunction = Callable[[SpeechRequest, threading.Event], SpeechAudio]
