"""Bounded native-to-HTTP queue and one nonseekable continuous MP3 encoder."""

import asyncio
from collections import deque
from collections.abc import Callable, Iterable
import importlib
import math
import threading
import time
from typing import Any

from orchestrator.speech.contracts import MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, SpeechError
from orchestrator.speech.stream_protocol import (
    COMPLETE,
    MAX_AUDIO_FRAME_PAYLOAD,
    MAX_AUDIO_FRAMES,
    MAX_PADDING_SECONDS,
    QUEUE_BYTES,
    QUEUE_FRAMES,
    SAMPLE_RATE,
    SEND_DRAIN_SECONDS,
    encode_audio,
    encode_frame,
)

DRAIN_SECONDS = SEND_DRAIN_SECONDS
BLOCK_SAMPLES = 4096


class ByteQueue:
    """No pending executor puts. Cancellation wakes condition AND async reader."""

    def __init__(self, cancel: threading.Event, deadline: float):
        self.cancel, self.deadline = cancel, deadline
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._condition = threading.Condition()
        self._frames: deque[bytes] = deque()
        self._bytes = 0
        self._ended = False
        self._error: BaseException | None = None
        self._last_drain = time.monotonic()
        self._notified = False

    @property
    def queued_bytes(self) -> int:
        with self._condition:
            return self._bytes

    @property
    def queued_frames(self) -> int:
        with self._condition:
            return len(self._frames)

    def _notify(self) -> None:
        # Called under the condition: at most ONE queued eventloop callback.
        if not self._notified:
            self._notified = True
            self._loop.call_soon_threadsafe(self._wake.set)

    def stop(self) -> None:
        with self._condition:
            self.cancel.set()
            self._condition.notify_all()
            self._notify()

    def put(self, frame: bytes) -> None:
        if len(frame) > MAX_AUDIO_FRAME_PAYLOAD + 5:
            raise SpeechError("speech_protocol_error")
        with self._condition:
            blocked_since = time.monotonic()
            while True:
                now = time.monotonic()
                if self.cancel.is_set():
                    raise SpeechError("speech_cancelled", 499)
                if now >= self.deadline:
                    raise SpeechError("speech_timeout", 504)
                if self._bytes + len(frame) <= QUEUE_BYTES and len(self._frames) < QUEUE_FRAMES:
                    self._frames.append(frame)
                    self._bytes += len(frame)
                    self._notify()
                    return
                stalled_since = max(blocked_since, self._last_drain)
                if now - stalled_since >= DRAIN_SECONDS:
                    raise SpeechError("speech_backpressure_timeout")
                self._condition.wait(
                    min(0.1, self.deadline - now, DRAIN_SECONDS - (now - stalled_since))
                )

    def finish(self, error: BaseException | None = None) -> None:
        with self._condition:
            self._error, self._ended = error, True
            self._condition.notify_all()
            self._notify()

    async def frames(self):
        while True:
            with self._condition:
                self._wake.clear()
                self._notified = False
                if self.cancel.is_set():
                    raise SpeechError("speech_cancelled", 499)
                if self._error is not None:
                    raise self._error
                if self._frames:
                    frame = self._frames.popleft()
                    self._bytes -= len(frame)
                    self._last_drain = time.monotonic()
                    self._condition.notify_all()
                elif self._ended:
                    return
                else:
                    frame = None
            if frame is not None:
                yield frame
            else:
                await self._wake.wait()


class NonseekableOutput:
    def __init__(self, write: Callable[[bytes], None]):
        self.write_bytes = write
        self.position = 0

    def write(self, value: Any) -> int:
        data = bytes(value)
        self.write_bytes(data)
        self.position += len(data)
        return len(data)

    def tell(self) -> int:
        return self.position

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False


def mp3_packet_samples(data: bytes) -> int:
    """Count actual emitted MPEG-2 Layer III frames, not trimmed packet duration.

    libmp3lame's final AVPacket.duration can be trimmed to source samples; without
    Xing/seek-back metadata the decoder still plays that full 576-sample frame.
    """
    offset, samples = 0, 0
    bitrates = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    while offset < len(data):
        if len(data) - offset < 4:
            raise SpeechError("invalid_speech_output")
        header = int.from_bytes(data[offset : offset + 4], "big")
        bitrate_index, rate_index = (header >> 12) & 15, (header >> 10) & 3
        if (
            header >> 21 != 0x7FF
            or (header >> 19) & 3 != 2
            or (header >> 17) & 3 != 1
            or rate_index != 1
            or not 1 <= bitrate_index <= 14
        ):
            raise SpeechError("invalid_speech_output")
        size = 72 * bitrates[bitrate_index] * 1000 // SAMPLE_RATE + ((header >> 9) & 1)
        if offset + size > len(data):
            raise SpeechError("invalid_speech_output")
        offset += size
        samples += 576
    if not samples:
        raise SpeechError("invalid_speech_output")
    return samples


class ContinuousMP3Encoder:
    """Single encoder/muxer across PCM chunks, bounded 4096-sample staging."""

    def __init__(self, emit: Callable[[bytes], None], cancel: threading.Event, deadline: float):
        self.av = importlib.import_module("av")
        self.np = importlib.import_module("numpy")
        self.emit, self.cancel, self.deadline = emit, cancel, deadline
        self.source_samples = self.encoded_samples = self.audio_bytes = self.frames = 0
        self._closed = False
        self.output = NonseekableOutput(self._write)
        self.container = self.av.open(
            self.output,
            mode="w",
            format="mp3",
            options={"write_xing": "0", "id3v2_version": "0", "flush_packets": "1"},
        )
        try:
            self.stream = self.container.add_stream("libmp3lame", rate=SAMPLE_RATE)
            self.stream.layout = "mono"
            self.stream.bit_rate = 64000
            self.resampler = self.av.AudioResampler(
                format=self.stream.codec_context.format, layout="mono", rate=SAMPLE_RATE
            )
        except BaseException:
            self.container.close()
            raise

    def check(self) -> None:
        if self.cancel.is_set():
            raise SpeechError("speech_cancelled", 499)
        if time.monotonic() >= self.deadline:
            raise SpeechError("speech_timeout", 504)

    def _write(self, data: bytes) -> None:
        self.check()
        if self.audio_bytes + len(data) > MAX_AUDIO_BYTES:
            raise SpeechError("speech_output_too_large")
        for offset in range(0, len(data), MAX_AUDIO_FRAME_PAYLOAD - 4):
            self.check()
            piece = data[offset : offset + MAX_AUDIO_FRAME_PAYLOAD - 4]
            if self.frames >= MAX_AUDIO_FRAMES:
                raise SpeechError("speech_output_too_large")
            self.emit(encode_audio(self.frames, piece))
            self.frames += 1
            self.audio_bytes += len(piece)

    def _packets(self, packets: Iterable[Any]) -> None:
        for packet in packets:
            self.check()
            count = mp3_packet_samples(bytes(packet))
            packet_seconds = float(packet.duration * packet.time_base)
            if (
                not math.isfinite(packet_seconds)
                or not 0 <= packet_seconds <= count / SAMPLE_RATE + 1e-9
            ):
                raise SpeechError("invalid_speech_output")
            self.encoded_samples += count
            if self.encoded_samples / SAMPLE_RATE > MAX_AUDIO_SECONDS + MAX_PADDING_SECONDS:
                raise SpeechError("speech_output_too_long", 413)
            self.container.mux(packet)

    def add(self, audio: Any, rate: int) -> None:
        self.check()
        if type(rate) is not int or rate != SAMPLE_RATE:
            raise SpeechError("invalid_speech_output")
        samples = self.np.asarray(audio)
        if samples.ndim != 1 or not len(samples) or not self.np.isfinite(samples).all():
            raise SpeechError("invalid_speech_output")
        if self.source_samples + len(samples) > MAX_AUDIO_SECONDS * SAMPLE_RATE:
            raise SpeechError("speech_output_too_long", 413)
        self.source_samples += len(samples)
        for offset in range(0, len(samples), BLOCK_SAMPLES):
            self.check()
            frame = self.av.AudioFrame.from_ndarray(
                self.np.asarray(
                    samples[offset : offset + BLOCK_SAMPLES], dtype=self.np.float32
                ).reshape(1, -1),
                format="flt",
                layout="mono",
            )
            frame.sample_rate = SAMPLE_RATE
            for converted in self.resampler.resample(frame):
                self._packets(self.stream.encode(converted))

    def finish(self) -> tuple[float, float]:
        self.check()
        for converted in self.resampler.resample(None):
            self._packets(self.stream.encode(converted))
        self._packets(self.stream.encode(None))
        self.close()
        source, encoded = self.source_samples / SAMPLE_RATE, self.encoded_samples / SAMPLE_RATE
        if (
            not self.audio_bytes
            or not 0 < source <= MAX_AUDIO_SECONDS
            or (not 0 <= encoded - source <= MAX_PADDING_SECONDS + 1e-9)
        ):
            raise SpeechError("invalid_speech_output")
        return source, encoded

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.container.close()


def qualify_encoder(audio: Any, rate: int) -> None:
    """Warm actual PCM plus short/300-second synthetic boundary encodings."""
    np = importlib.import_module("numpy")
    for sample_count in (None, 1, SAMPLE_RATE * MAX_AUDIO_SECONDS):
        encoder = ContinuousMP3Encoder(lambda _: None, threading.Event(), time.monotonic() + 120)
        try:
            if sample_count is None:
                encoder.add(audio, rate)
            else:
                for offset in range(0, sample_count, BLOCK_SAMPLES):
                    encoder.add(
                        np.zeros(min(BLOCK_SAMPLES, sample_count - offset), dtype=np.float32),
                        SAMPLE_RATE,
                    )
            encoder.finish()
        finally:
            encoder.close()


def completion(
    encoder: ContinuousMP3Encoder, source: float, encoded: float, started: float
) -> bytes:
    encoder.check()
    elapsed = time.monotonic() - started
    if elapsed > 120:
        raise SpeechError("speech_timeout", 504)
    return encode_frame(
        COMPLETE,
        {
            "frames": encoder.frames,
            "bytes": encoder.audio_bytes,
            "source_seconds": source,
            "encoded_seconds": encoded,
            "synthesis_seconds": elapsed,
            "audio_path": None,
            "cache_available": False,
        },
    )
