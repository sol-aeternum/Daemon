"""Strict, bounded v1 framing shared by private and authenticated speech hops."""

from collections.abc import Iterator
from dataclasses import dataclass
import json
import math
import re
import secrets
import struct
from typing import Any

from orchestrator.speech.contracts import MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, SpeechError

VERSION = 1
MAGIC = b"DSP1"
CONTENT_TYPE = "application/vnd.daemon.speech-stream;version=1"
RENDERING = "speech-mp3-progressive-v1"
MAX_AUDIO_FRAME_PAYLOAD = 65536
MAX_CONTROL_BYTES = 4096
MAX_AUDIO_FRAMES = 16384
MAX_HEARTBEATS = 32
MAX_WIRE_BYTES = 20_000_000
MAX_PADDING_SECONDS = 0.15
SAMPLE_RATE = 24000
FEED_SLAB = 256
QUEUE_BYTES = 256 * 1024
QUEUE_FRAMES = 64
HEARTBEAT_SECONDS = 5.0
SEND_DRAIN_SECONDS = 10.0
PROGRESSIVE_LIMITS = {
    "max_characters": 3000,
    "max_request_bytes": 32768,
    "min_speed": 0.5,
    "max_speed": 2.0,
    "max_audio_frame_payload": MAX_AUDIO_FRAME_PAYLOAD,
    "max_control_bytes": MAX_CONTROL_BYTES,
    "max_audio_bytes": MAX_AUDIO_BYTES,
    "max_source_seconds": MAX_AUDIO_SECONDS,
    "max_padding_seconds": MAX_PADDING_SECONDS,
    "max_encoded_seconds": MAX_AUDIO_SECONDS + MAX_PADDING_SECONDS,
    "max_audio_frames": MAX_AUDIO_FRAMES,
    "max_heartbeats": MAX_HEARTBEATS,
    "max_wire_bytes": MAX_WIRE_BYTES,
    "max_parser_pending_bytes": MAX_AUDIO_FRAME_PAYLOAD + 5,
    "parser_feed_slab": FEED_SLAB,
    "max_queue_bytes": QUEUE_BYTES,
    "max_queue_frames": QUEUE_FRAMES,
    "heartbeat_seconds": HEARTBEAT_SECONDS,
    "drain_seconds": SEND_DRAIN_SECONDS,
    "runtime_seconds": 120,
    "transport_idle_seconds": 15,
}
METADATA, AUDIO, COMPLETE, ERROR, HEARTBEAT = range(5)
ERROR_CODES = frozenset(
    {
        "speech_failed",
        "speech_busy",
        "speech_not_ready",
        "speech_timeout",
        "speech_cancelled",
        "speech_output_too_long",
        "speech_output_too_large",
        "invalid_speech_output",
        "speech_stream_unsupported",
        "speech_protocol_error",
        "speech_backpressure_timeout",
        "speech_authorization_lost",
        "speech_authorization_unavailable",
        "speech_stream_idle_timeout",
        "speech_unavailable",
        "speech_output_limit",
        "speech_identity_mismatch",
        "empty_speech_output",
        "speech_configuration_error",
        "speech_capacity_unavailable",
        "voice_unavailable",
        "unsupported_format",
        "invalid_speed",
        "text_required",
        "text_too_long",
        "repetitive_text",
        "invalid_text",
    }
)
_META_FIELDS = frozenset(
    {
        "version",
        "stream_id",
        "provider",
        "model",
        "voice",
        "speed",
        "format",
        "mime",
        "sample_rate",
        "rendering",
        "cached",
    }
)
_COMPLETE_FIELDS = frozenset(
    {
        "frames",
        "bytes",
        "source_seconds",
        "encoded_seconds",
        "synthesis_seconds",
        "audio_path",
        "cache_available",
    }
)
_IDENTITY_FIELDS = _META_FIELDS - {"stream_id", "cached"}


def _invalid() -> SpeechError:
    return SpeechError("speech_protocol_error")


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_content_type(value: str) -> None:
    # Parameter order/whitespace is immaterial; extra/duplicate parameters are not.
    parts = [part.strip().lower() for part in value.split(";")]
    if parts != ["application/vnd.daemon.speech-stream", "version=1"]:
        raise _invalid()


def validate_metadata(value: Any, expected: dict | None = None) -> dict:
    if type(value) is not dict or value.keys() != _META_FIELDS:
        raise _invalid()
    if type(value["version"]) is not int or value["version"] != VERSION:
        raise _invalid()
    if not isinstance(value["stream_id"], str) or not re.fullmatch(
        r"[a-f0-9]{32}", value["stream_id"]
    ):
        raise _invalid()
    for field in ("provider", "model"):
        if not isinstance(value[field], str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value[field]
        ):
            raise _invalid()
    if (
        value["voice"] != "daemon-default"
        or not _number(value["speed"])
        or not 0.5 <= value["speed"] <= 2
        or value["format"] != "mp3"
        or value["mime"] != "audio/mpeg"
        or type(value["sample_rate"]) is not int
        or value["sample_rate"] != SAMPLE_RATE
        or value["rendering"] != RENDERING
        or type(value["cached"]) is not bool
    ):
        raise _invalid()
    if expected is not None:
        for field in _IDENTITY_FIELDS:
            if field in expected and value[field] != expected[field]:
                raise _invalid()
    return value


def metadata(
    provider: str, model: str, speech: Any, cached: bool = False, stream_id: str | None = None
) -> dict:
    return validate_metadata(
        {
            "version": VERSION,
            "stream_id": secrets.token_hex(16) if stream_id is None else stream_id,
            "provider": provider,
            "model": model,
            "voice": speech.voice,
            "speed": speech.speed,
            "format": speech.format,
            "mime": "audio/mpeg",
            "sample_rate": SAMPLE_RATE,
            "rendering": RENDERING,
            "cached": cached,
        }
    )


def safe_error_code(exc: BaseException) -> str:
    return exc.code if isinstance(exc, SpeechError) and exc.code in ERROR_CODES else "speech_failed"


def encode_frame(kind: int, payload: bytes | dict) -> bytes:
    if type(kind) is not int or kind not in range(5):
        raise _invalid()
    if isinstance(payload, dict):
        try:
            payload = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError) as exc:
            raise _invalid() from exc
    if not isinstance(payload, bytes):
        raise _invalid()
    limit = MAX_AUDIO_FRAME_PAYLOAD if kind == AUDIO else MAX_CONTROL_BYTES
    if len(payload) > limit or (kind == HEARTBEAT and payload):
        raise _invalid()
    return struct.pack(">BI", kind, len(payload)) + payload


def encode_audio(sequence: int, data: bytes) -> bytes:
    if type(sequence) is not int or not 0 <= sequence < MAX_AUDIO_FRAMES or not data:
        raise _invalid()
    return encode_frame(AUDIO, struct.pack(">I", sequence) + data)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _json(payload: bytes) -> dict:
    try:
        result = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
    if type(result) is not dict:
        raise _invalid()
    return result


@dataclass(frozen=True)
class StreamEvent:
    kind: int
    payload: bytes | dict


class StreamParser:
    """Consume feed iterators fully. Success additionally requires clean-EOF finish()."""

    def __init__(self, expected_metadata: dict | None = None):
        self.expected_metadata = expected_metadata
        self.metadata: dict | None = None
        self.audio_frames = 0
        self.audio_bytes = 0
        self.wire_bytes = 0
        self.heartbeats = 0
        self.terminal_complete = False
        self._magic = False
        self._terminal = False
        self._failed = False
        self._pending = bytearray()

    @property
    def pending_bytes(self) -> int:
        return len(self._pending)

    def feed(self, data: bytes) -> Iterator[StreamEvent]:
        if self._failed:
            raise _invalid()
        try:
            view = memoryview(data)
            for offset in range(0, len(view), FEED_SLAB):
                slab = view[offset : offset + FEED_SLAB]
                self.wire_bytes += len(slab)
                if self.wire_bytes > MAX_WIRE_BYTES or (self._terminal and slab):
                    raise _invalid()
                self._pending.extend(slab)
                if not self._magic:
                    if len(self._pending) < len(MAGIC):
                        continue
                    if self._pending[:4] != MAGIC:
                        raise _invalid()
                    del self._pending[:4]
                    self._magic = True
                while len(self._pending) >= 5:
                    if self._terminal:
                        raise _invalid()
                    kind, length = struct.unpack_from(">BI", self._pending)
                    if kind not in range(5):
                        raise _invalid()
                    limit = MAX_AUDIO_FRAME_PAYLOAD if kind == AUDIO else MAX_CONTROL_BYTES
                    if (
                        length > limit
                        or (kind == AUDIO and length <= 4)
                        or (kind == HEARTBEAT and length != 0)
                    ):
                        raise _invalid()
                    if len(self._pending) < length + 5:
                        break
                    payload = bytes(self._pending[5 : 5 + length])
                    del self._pending[: 5 + length]
                    yield self._event(kind, payload)
                if self._terminal and self._pending:
                    raise _invalid()
        except Exception:
            self._failed = True
            raise

    def _event(self, kind: int, payload: bytes) -> StreamEvent:
        if self.metadata is None and kind != METADATA:
            raise _invalid()
        if kind == METADATA:
            if self.metadata is not None:
                raise _invalid()
            self.metadata = validate_metadata(_json(payload), self.expected_metadata)
            return StreamEvent(kind, self.metadata)
        if kind == AUDIO:
            if struct.unpack_from(">I", payload)[0] != self.audio_frames:
                raise _invalid()
            self.audio_frames += 1
            self.audio_bytes += len(payload) - 4
            if self.audio_frames > MAX_AUDIO_FRAMES or self.audio_bytes > MAX_AUDIO_BYTES:
                raise _invalid()
            return StreamEvent(kind, payload[4:])
        if kind == HEARTBEAT:
            self.heartbeats += 1
            if self.heartbeats > MAX_HEARTBEATS:
                raise _invalid()
            return StreamEvent(kind, b"")
        control = _json(payload)
        if kind == ERROR:
            self._terminal = True
            if (
                control.keys() != {"code"}
                or not isinstance(control["code"], str)
                or (control["code"] not in ERROR_CODES)
            ):
                raise _invalid()
            raise SpeechError(control["code"])
        if control.keys() != _COMPLETE_FIELDS:
            raise _invalid()
        for field, count in (("frames", self.audio_frames), ("bytes", self.audio_bytes)):
            if type(control[field]) is not int or control[field] != count or count <= 0:
                raise _invalid()
        source, encoded, synthesis = (
            control[field] for field in ("source_seconds", "encoded_seconds", "synthesis_seconds")
        )
        if (
            not _number(source)
            or not _number(encoded)
            or not 0 < source <= MAX_AUDIO_SECONDS
            or not source <= encoded <= MAX_AUDIO_SECONDS + MAX_PADDING_SECONDS
            or encoded - source > MAX_PADDING_SECONDS + 1e-9
            or not _number(synthesis)
            or not 0 <= synthesis <= 120
            or type(control["cache_available"]) is not bool
        ):
            raise _invalid()
        path = control["audio_path"]
        if control["cache_available"]:
            if not isinstance(path, str) or not re.fullmatch(
                r"/generated-audio/[a-f0-9]{64}\.mp3", path
            ):
                raise _invalid()
        elif path is not None:
            raise _invalid()
        if self.metadata is not None and self.metadata["cached"] and synthesis != 0:
            raise _invalid()
        self._terminal = self.terminal_complete = True
        return StreamEvent(kind, control)

    def finish(self) -> None:
        if self._failed or not self.terminal_complete or self._pending:
            raise _invalid()
