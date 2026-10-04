"""Strict framing tests; a playable prefix is never protocol success."""

import json
import struct

import pytest

from orchestrator.speech.contracts import SpeechError, SpeechRequest
from orchestrator.speech.stream_protocol import (
    AUDIO,
    COMPLETE,
    CONTENT_TYPE,
    ERROR,
    HEARTBEAT,
    MAGIC,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_FRAME_PAYLOAD,
    MAX_CONTROL_BYTES,
    MAX_HEARTBEATS,
    MAX_WIRE_BYTES,
    METADATA,
    StreamParser,
    encode_audio,
    encode_frame,
    metadata,
    validate_content_type,
)


def identity():
    return metadata("kokoro", "kokoro-82m-v1.0", SpeechRequest("hello"))


def terminal(frames=1, size=3, **changes):
    result = {
        "frames": frames,
        "bytes": size,
        "source_seconds": 1.0,
        "encoded_seconds": 1.056,
        "synthesis_seconds": 0.1,
        "audio_path": None,
        "cache_available": False,
    }
    return result | changes


def prefix(value=None):
    return MAGIC + encode_frame(METADATA, value or identity())


def valid_body():
    return prefix() + encode_audio(0, b"mp3") + encode_frame(COMPLETE, terminal())


def test_every_split_and_bytewise_and_coalesced():
    body = valid_body()
    for cut in range(len(body) + 1):
        parser = StreamParser()
        events = [*parser.feed(body[:cut]), *parser.feed(body[cut:])]
        parser.finish()
        assert [event.kind for event in events] == [METADATA, AUDIO, COMPLETE]
        assert events[1].payload == b"mp3"
        assert (parser.audio_frames, parser.audio_bytes, parser.wire_bytes) == (1, 3, len(body))
    parser = StreamParser()
    for byte in body:
        list(parser.feed(bytes([byte])))
        assert parser.pending_bytes <= MAX_AUDIO_FRAME_PAYLOAD + 5 + 256
    parser.finish()


@pytest.mark.parametrize(
    "body",
    [
        b"DSP2",
        b"BAD!",
        MAGIC + struct.pack(">BI", 99, 0),
        MAGIC + struct.pack(">BI", METADATA, MAX_CONTROL_BYTES + 1),
        prefix() + struct.pack(">BI", AUDIO, MAX_AUDIO_FRAME_PAYLOAD + 1),
        prefix() + encode_frame(AUDIO, b"\0\0\0\0"),
        prefix() + encode_audio(1, b"x"),
        prefix() + encode_frame(METADATA, identity()),
        prefix() + encode_frame(HEARTBEAT, b"") * (MAX_HEARTBEATS + 1),
        valid_body() + encode_frame(HEARTBEAT, b""),
        valid_body() + b"x",
        MAGIC + encode_audio(0, b"x"),
        MAGIC + encode_frame(METADATA, b'{"version":1,"version":1}'),
        MAGIC + encode_frame(METADATA, b"\xff"),
        prefix() + encode_frame(ERROR, {"code": "private stack trace"}),
        prefix() + encode_frame(ERROR, {"code": "speech_failed", "path": "/secret"}),
    ],
)
def test_malformed(body):
    parser = StreamParser()
    with pytest.raises(SpeechError, match="speech_protocol_error"):
        list(parser.feed(body))
        parser.finish()


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("sample_rate", 48000),
        ("sample_rate", 24000.0),
        ("speed", True),
        ("speed", float("nan")),
        ("speed", float("inf")),
        ("speed", 0),
        ("voice", "af_heart"),
        ("format", "wav"),
        ("mime", "audio/ogg"),
        ("rendering", "speech-v1"),
        ("cached", 1),
        ("provider", ""),
        ("model", "bad\nmodel"),
        ("stream_id", "token?secret"),
        ("speed", 10**3000),
    ],
)
def test_metadata_fields(field, value):
    meta = identity() | {field: value}
    # Raw JSON also exercises NaN rejected on decode/schema, not only encoding.
    body = MAGIC + encode_frame(METADATA, json.dumps(meta).encode())
    with pytest.raises(SpeechError, match="speech_protocol_error"):
        list(StreamParser().feed(body))


@pytest.mark.parametrize(
    "change",
    [
        {"frames": True},
        {"frames": 2},
        {"bytes": 4},
        {"bytes": 3.0},
        {"source_seconds": 0},
        {"source_seconds": 301},
        {"source_seconds": True},
        {"encoded_seconds": 0.9},
        {"encoded_seconds": 1.151},
        {"encoded_seconds": float("inf")},
        {"synthesis_seconds": -1},
        {"cache_available": 1},
        {"audio_path": "/tmp/private.mp3"},
        {"cache_available": True, "audio_path": "/generated-audio/short.mp3"},
        {"cache_available": True, "audio_path": "/generated-audio/" + "a" * 64 + ".mp3?token=x"},
        {"extra": "unapproved"},
    ],
)
def test_invalid_complete(change):
    parser = StreamParser()
    with pytest.raises(SpeechError, match="speech_protocol_error"):
        list(
            parser.feed(
                prefix()
                + encode_audio(0, b"mp3")
                + encode_frame(COMPLETE, json.dumps(terminal(**change)).encode())
            )
        )


def test_expected_identity_cache_and_paths():
    meta = identity()
    for field, value in (("provider", "other"), ("model", "other"), ("speed", 1.1)):
        with pytest.raises(SpeechError, match="speech_protocol_error"):
            list(StreamParser(meta).feed(prefix(meta | {field: value})))
    parser = StreamParser(meta)
    list(
        parser.feed(
            prefix(meta | {"cached": True})
            + encode_audio(0, b"mp3")
            + encode_frame(
                COMPLETE,
                terminal(
                    synthesis_seconds=0,
                    cache_available=True,
                    audio_path="/generated-audio/" + "a" * 64 + ".mp3",
                ),
            )
        )
    )
    parser.finish()


def test_eof_is_required_and_trailing_duplicate_poison_success():
    parser = StreamParser()
    list(parser.feed(valid_body()))
    assert parser.terminal_complete
    with pytest.raises(SpeechError, match="speech_protocol_error"):
        list(parser.feed(encode_frame(COMPLETE, terminal())))
    with pytest.raises(SpeechError):
        parser.finish()
    for cut in (0, 4, len(valid_body()) - 1):
        parser = StreamParser()
        list(parser.feed(valid_body()[:cut]))
        with pytest.raises(SpeechError):
            parser.finish()


def test_typed_error_and_duplicate_keys_and_nonempty_heartbeat():
    with pytest.raises(SpeechError, match="speech_timeout"):
        list(StreamParser().feed(prefix() + encode_frame(ERROR, {"code": "speech_timeout"})))
    with pytest.raises(SpeechError, match="speech_protocol_error"):
        list(
            StreamParser().feed(
                prefix() + encode_frame(ERROR, b'{"code":"speech_failed","code":"speech_timeout"}')
            )
        )
    with pytest.raises(SpeechError):
        list(StreamParser().feed(prefix() + struct.pack(">BI", HEARTBEAT, 1) + b"x"))


def test_large_coalesced_body_bounds_audio_and_pending_storage():
    parser = StreamParser()
    frame = encode_audio(0, b"x" * (MAX_AUDIO_FRAME_PAYLOAD - 4))
    # Fail first on totals, despite receiving an oversized wire slab in ONE feed.
    body = prefix() + b"".join(
        encode_audio(sequence, frame[9:]) for sequence in range(MAX_WIRE_BYTES // len(frame) + 2)
    )
    with pytest.raises(SpeechError):
        for _ in parser.feed(body):
            assert parser.pending_bytes <= MAX_AUDIO_FRAME_PAYLOAD + 5 + 256
    assert parser.audio_bytes > MAX_AUDIO_BYTES
    assert parser.pending_bytes <= MAX_AUDIO_FRAME_PAYLOAD + 5 + 256


@pytest.mark.parametrize("value", [CONTENT_TYPE, "application/vnd.daemon.speech-stream; version=1"])
def test_mime(value):
    validate_content_type(value)


@pytest.mark.parametrize(
    "value",
    [
        "audio/mpeg",
        CONTENT_TYPE + ";version=1",
        CONTENT_TYPE + ";x=1",
        CONTENT_TYPE.replace("=1", "=2"),
    ],
)
def test_invalid_mime(value):
    with pytest.raises(SpeechError):
        validate_content_type(value)


def test_frame_count_wire_count_and_zero_success_and_duplicate_complete_keys():
    parser = StreamParser()
    list(parser.feed(prefix()))
    for sequence in range(16384):
        list(parser.feed(encode_audio(sequence, b"x")))
    with pytest.raises(SpeechError):
        list(parser.feed(encode_frame(AUDIO, struct.pack(">I", 16384) + b"x")))
    parser = StreamParser()
    list(parser.feed(prefix()))
    parser.wire_bytes = MAX_WIRE_BYTES
    with pytest.raises(SpeechError):
        list(parser.feed(encode_audio(0, b"x")))
    with pytest.raises(SpeechError):
        list(StreamParser().feed(prefix() + encode_frame(COMPLETE, terminal(0, 0))))
    duplicate = json.dumps(terminal())[:-1] + ',"frames":1}'
    with pytest.raises(SpeechError):
        list(
            StreamParser().feed(
                prefix() + encode_audio(0, b"mp3") + encode_frame(COMPLETE, duplicate.encode())
            )
        )
