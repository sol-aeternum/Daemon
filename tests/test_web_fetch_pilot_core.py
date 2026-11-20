"""Tests for scripts/web_fetch_pilot_core.py (offline pilot core only).

Scope guard: this suite covers ONLY the pure primitives — frame codec, strict
CONNECT preface parsing, IPv4-only destination policy over injected answers,
and synchronous byte-budget accounting. No network, socket, DNS, subprocess,
container or wallclock behaviour is exercised or claimed anywhere. Tunnel
lifecycle, pinned dialing and the actual gateway remain unimplemented by
design (see the module docstring).
"""

from __future__ import annotations

import struct

import pytest

from scripts.web_fetch_pilot_core import (
    HEADER_LEN,
    CHUNK_LIMIT,
    CONNECT_HEADER_LIMIT,
    CONTROL_STREAM_ID,
    MAX_FRAMES,
    MAX_PAYLOAD,
    MAX_STREAM_ID,
    ByteBudget,
    BudgetError,
    ConnectHeaderError,
    ConnectRequest,
    DestinationPolicyError,
    DIRECTION_READ,
    DIRECTION_WRITE,
    Frame,
    FrameCodecError,
    FrameParser,
    FrameType,
    Reservation,
    check_open_payload,
    encode_frame,
    parse_connect_headers,
    parse_owned_inventory,
    require_canonical_hostname,
    validate_dns_answers,
)

HEADER_FMT = "!BBHII"


def test_staged_payload_counts_toward_decoded_batch_limit() -> None:
    from scripts.web_fetch_pilot_core import CHUNK_LIMIT

    parser = FrameParser()
    first = encode_frame(FrameType.DATA, 1, b"x" * MAX_PAYLOAD)
    assert list(parser.feed(first[:-1])) == []
    last_size = CHUNK_LIMIT - 1 - 3 * len(first) - 12
    chunk = first[-1:] + first * 3 + encode_frame(FrameType.DATA, 1, b"x" * last_size)
    assert len(chunk) == CHUNK_LIMIT
    with pytest.raises(FrameCodecError, match="decoded payload ceiling"):
        parser.feed(chunk)
    with pytest.raises(FrameCodecError):
        parser.feed(b"")
    with pytest.raises(FrameCodecError):
        parser.end_of_stream()


def test_staged_payload_accepts_exact_decoded_batch_limit() -> None:
    from scripts.web_fetch_pilot_core import CHUNK_LIMIT

    parser = FrameParser()
    frame = encode_frame(FrameType.DATA, 1, b"x" * MAX_PAYLOAD)
    assert list(parser.feed(frame[:-1])) == []
    rows = list(parser.feed(frame[-1:] + frame * 3))
    assert sum(len(row.payload) for row in rows) == CHUNK_LIMIT
    assert len(rows) == 4
    parser.end_of_stream()


def test_reservation_subclass_cannot_impersonate_issued_token() -> None:
    from scripts.web_fetch_pilot_core import Reservation

    budget = ByteBudget(100)
    issued = budget.reserve(20, DIRECTION_READ)
    callbacks: list[str] = []

    class Forged(Reservation):
        def __hash__(self) -> int:
            callbacks.append("hash")
            return hash(issued)

        def __eq__(self, other: object) -> bool:
            callbacks.append("equality")
            return other is issued

    with pytest.raises(BudgetError, match="unknown reservation"):
        budget.reconcile(Forged(), 20)
    assert callbacks == []
    assert budget.is_closed
    assert budget.charged_actual == 0
    assert budget.outstanding == 20
    assert issued in budget._active


def raw_frame(
    ftype: int, flags: int = 0, reserved: int = 0, sid: int = 1, payload: bytes = b""
) -> bytes:
    return struct.pack(HEADER_FMT, ftype, flags, reserved, sid, len(payload)) + payload


def feed_all(data: bytes) -> list[Frame]:
    parser = FrameParser()
    return list(parser.feed(data))


# ---------------------------------------------------------------------------
# Frame codec
# ---------------------------------------------------------------------------


class TestFrameCodecHappyPath:
    def test_one_of_each_subset_type(self) -> None:
        cases: list[bytes] = [
            encode_frame(FrameType.OPEN, 1, b"openai.com:443"),
            encode_frame(FrameType.OPEN_OK, 1),
            encode_frame(FrameType.OPEN_ERROR, 1, b"refused"),
            encode_frame(FrameType.DATA, 2, b"\x01\x02"),
            encode_frame(FrameType.WINDOW, 2, (64 * 1024).to_bytes(4, "big")),
            encode_frame(FrameType.HALF_CLOSE, 2),
            encode_frame(FrameType.CLOSE, 2),
            encode_frame(FrameType.RESULT, CONTROL_STREAM_ID, b"text"),
        ]
        frames = feed_all(b"".join(cases))
        assert [f.frame_type for f in frames] == list(FrameType)
        assert frames[3].stream_id == 2
        assert frames[7].stream_id == CONTROL_STREAM_ID

    def test_max_payload_data_frame(self) -> None:
        payload = b"\0" * MAX_PAYLOAD
        frames = feed_all(encode_frame(FrameType.DATA, 40, payload))
        assert frames == [Frame(FrameType.DATA, 40, payload)]

    def test_empty_data_frame_allowed(self) -> None:
        frames = feed_all(encode_frame(FrameType.DATA, 3))
        assert frames == [Frame(FrameType.DATA, 3, b"")]

    def test_results_are_immutable_frames(self) -> None:
        (frame,) = feed_all(encode_frame(FrameType.DATA, 1, b"x"))
        assert isinstance(frame, Frame)
        with pytest.raises(AttributeError):
            frame.payload = b"tampered"  # type: ignore[misc]

    def test_open_payload_check_round_trip(self) -> None:
        assert check_open_payload(b"openai.com:443") == "openai.com:443"


class TestFrameCodecRejections:
    @pytest.mark.parametrize(
        ("ftype", "flags", "reserved", "sid"),
        [
            (4, 1, 0, 1),  # nonzero flags
            (4, 0, 1, 1),  # nonzero reserved
            (9, 0, 0, 1),  # unknown type beyond subset
            (0, 0, 0, 1),  # type 0 is not a valid frame type
            (4, 0, 0, 0),  # non-RESULT stream 0
            (4, 0, 0, MAX_STREAM_ID + 1),  # stream beyond 1..40
        ],
    )
    def test_bad_header_fields(self, ftype: int, flags: int, reserved: int, sid: int) -> None:
        with pytest.raises(FrameCodecError):
            feed_all(raw_frame(ftype, flags=flags, reserved=reserved, sid=sid))

    def test_non_result_stream_zero_is_named_violation(self) -> None:
        with pytest.raises(FrameCodecError, match="stream id outside"):
            feed_all(raw_frame(4, sid=CONTROL_STREAM_ID))

    def test_result_on_nonzero_stream_is_named_violation(self) -> None:
        with pytest.raises(FrameCodecError, match="RESULT"):
            feed_all(raw_frame(8, sid=1, payload=b"text"))

    def test_stream_id_upper_bound(self) -> None:
        with pytest.raises(FrameCodecError, match="1..40"):
            feed_all(raw_frame(4, sid=MAX_STREAM_ID + 1))
        frames = feed_all(encode_frame(FrameType.DATA, MAX_STREAM_ID, b"ok"))
        assert frames[0].stream_id == MAX_STREAM_ID

    def test_oversize_payload_declared_length(self) -> None:
        raw = struct.pack(HEADER_FMT, 4, 0, 0, 4, MAX_PAYLOAD + 1)
        with pytest.raises(FrameCodecError, match="exceeds"):
            feed_all(raw)

    def test_oversize_declared_does_not_stage_payload(self) -> None:
        parser = FrameParser()
        raw = struct.pack(HEADER_FMT, 4, 0, 0, 4, MAX_PAYLOAD + 1)
        with pytest.raises(FrameCodecError):
            list(parser.feed(raw + b"\0" * 1000))
        with pytest.raises(FrameCodecError):
            parser.end_of_stream()

    def test_wrong_payload_lengths_per_type(self) -> None:
        bad = [
            encode_frame(FrameType.OPEN_OK, 1, b"x"),
            encode_frame(FrameType.OPEN, 1),
            raw_frame(ftype=1, sid=1, payload=b"a" * 256),  # over OPEN payload cap
            encode_frame(FrameType.OPEN_ERROR, 1),
            encode_frame(FrameType.WINDOW, 1, b"\0\0\0"),
            encode_frame(FrameType.WINDOW, 1, b"\0" * 5),
            encode_frame(FrameType.HALF_CLOSE, 1, b"x"),
            encode_frame(FrameType.CLOSE, 1, b"x"),
            encode_frame(FrameType.RESULT, CONTROL_STREAM_ID),
        ]
        for bad_entry in bad:
            with pytest.raises(FrameCodecError):
                feed_all(bad_entry)


class TestFrameCanonicalOpen:
    @pytest.mark.parametrize(
        "target",
        [
            b"openai.com:8443",
            b"openai.com",
            b"openai.com:443:443",
            b"OPENAI.com:443",
            b"open_ai.com:443",
            b"-openai.com:443",
            b"openai.com-:443",
            b"openai.com.:443",
            b".openai.com:443",
            b"openai..com:443",
            b"93.184.216.34:443",
            b"0::1:443",
            b"openai.com :443",
            b"openai.com : 443",
            b"openai%2ecom:443",
            b"openai.com\x0a:443",
            b"openai.com:\x00443",
        ],
    )
    def test_noncanonical_open_rejected(self, target: bytes) -> None:
        with pytest.raises(FrameCodecError):
            feed_all(encode_frame(FrameType.OPEN, 1, target))

    def test_payload_size_boundary(self) -> None:
        # Longest canonical host: 4 labels each <= 63 chars, total <= 253;
        # with ":443" the payload stays within the 255-byte OPEN cap.
        ok = ("a" * 62 + "." + "b" * 62 + "." + "c" * 62 + "." + "d" * 61 + ":443").encode()
        assert len(ok) == 254
        assert check_open_payload(ok) == ok.decode("ascii")
        # A 256-byte OPEN payload exceeds the codec cap before any host check.
        host252 = ".".join(["x" * 63, "y" * 63, "z" * 63, "w" * 60])
        with pytest.raises(FrameCodecError):
            check_open_payload(host252.encode() + b":443")  # 256-byte payload


class TestFrameIncrementalAndBounded:
    def test_byte_by_byte_feed(self) -> None:
        parser = FrameParser()
        raw = encode_frame(FrameType.OPEN, 1, b"openai.com:443") + encode_frame(
            FrameType.OPEN_OK, 1
        )
        collected: list[Frame] = []
        for single in memoryview(raw):
            collected.extend(parser.feed(bytes([single])))
        assert len(collected) == 2
        assert collected[0].frame_type == FrameType.OPEN

    def test_split_within_header_and_payload(self) -> None:
        parser = FrameParser()
        raw = encode_frame(FrameType.DATA, 4, b"hello")
        first: list[Frame] = list(parser.feed(raw[:3]))
        assert first == []
        first.extend(parser.feed(raw[3:6]))
        assert first == []
        first.extend(parser.feed(raw[6:]))
        assert [f.payload for f in first] == [b"hello"]

    def test_trailing_partial_header_latches_on_eof(self) -> None:
        parser = FrameParser()
        chunk_tail = encode_frame(FrameType.DATA, 1, b"first") + encode_frame(FrameType.DATA, 1)[:7]
        frames = list(parser.feed(chunk_tail))
        assert frames[0].payload == b"first"
        with pytest.raises(FrameCodecError, match="truncated"):
            parser.end_of_stream()

    def test_trailing_partial_payload_latches_on_eof(self) -> None:
        parser = FrameParser()
        tail = raw_frame(4, sid=1, payload=b"\x00")[:-1]
        list(parser.feed(tail))
        with pytest.raises(FrameCodecError, match="truncated"):
            parser.end_of_stream()


class TestParserChunkLimitAndBounds:
    """Repair A: per-feed 64 KiB cap, no tail copies, staged-frame bounds."""

    def test_oversized_valid_batch_latches_before_parsing(self) -> None:
        one = encode_frame(FrameType.HALF_CLOSE, 1)
        parser = FrameParser()
        data = one * ((CHUNK_LIMIT // len(one)) + 1)
        assert len(data) > CHUNK_LIMIT
        with pytest.raises(FrameCodecError, match="exceeds"):
            list(parser.feed(data))
        with pytest.raises(FrameCodecError):  # fail-latched, no reuse
            list(parser.feed(one))

    def test_oversized_boundary_rejected_before_memoryview(self) -> None:
        parser = FrameParser()
        with pytest.raises(FrameCodecError, match="exceeds"):
            list(parser.feed(b"\0" * (CHUNK_LIMIT + 1)))

    def test_chunk_limit_boundary_accepts_and_stages_tail(self) -> None:
        parser = FrameParser()
        full = encode_frame(FrameType.DATA, 1, b"\0" * MAX_PAYLOAD) * 3  # 49188 bytes
        partial = bytes(encode_frame(FrameType.DATA, 1, b"\0" * MAX_PAYLOAD))[:16348]
        data = full + partial
        assert len(data) == CHUNK_LIMIT
        frames = list(parser.feed(data))
        assert len(frames) == 3
        assert len(parser._buf) == 16348

    def test_chunk_limit_boundary_exact_frames(self) -> None:
        parser = FrameParser()
        payload_total = CHUNK_LIMIT - 4 * HEADER_LEN  # 65488 payload bytes
        parts = (16384, 16384, 16384, payload_total - 3 * 16384)
        data = b"".join(encode_frame(FrameType.DATA, 1, b"\0" * p) for p in parts)
        assert len(data) == CHUNK_LIMIT
        assert len(list(parser.feed(data))) == 4
        assert len(parser._buf) == 0

    def test_max_frames_chunk_fits_under_chunk_limit(self) -> None:
        parser = FrameParser()
        frames = list(parser.feed(encode_frame(FrameType.HALF_CLOSE, 1) * MAX_FRAMES))
        assert len(frames) == MAX_FRAMES  # 49152 bytes, ceiling reached exactly

    def test_partial_max_frame_staging_stays_bounded(self) -> None:
        parser = FrameParser()
        raw = encode_frame(FrameType.DATA, 1, b"\0" * MAX_PAYLOAD)
        assert list(parser.feed(raw[: 12 + MAX_PAYLOAD - 1])) == []
        assert len(parser._buf) == 12 + MAX_PAYLOAD - 1
        rest = list(parser.feed(raw[-1:]))
        assert rest[0].payload == b"\0" * MAX_PAYLOAD
        assert len(parser._buf) == 0

    def test_maximum_decoded_payload_per_feed(self) -> None:
        parser = FrameParser()
        # 7 x 9000-byte DATA payloads fit under the per-feed cap together.
        chunk = b"".join(encode_frame(FrameType.DATA, 1, bytes([i]) * 9000) for i in range(7))
        assert len(chunk) <= CHUNK_LIMIT
        frames = list(parser.feed(chunk))
        assert sum(len(f.payload) for f in frames) == 63000
        # Payloads totalling the full cap with header overhead exceed it.
        over = encode_frame(FrameType.DATA, 1, b"\0" * MAX_PAYLOAD) * 4  # 65584 bytes
        with pytest.raises(FrameCodecError, match="exceeds"):
            list(parser.feed(over))

    def test_hostile_header_fails_without_staging(self) -> None:
        parser = FrameParser()
        hostile = struct.pack(HEADER_FMT, 4, 0, 0, 1, 0xFFFFFFF0) + b"\0" * 17
        with pytest.raises(FrameCodecError):
            list(parser.feed(hostile))
        assert len(parser._buf) == 0

    def test_clean_eof_then_feed_rejected(self) -> None:
        parser = FrameParser()
        list(parser.feed(encode_frame(FrameType.OPEN_OK, 1)))
        parser.end_of_stream()
        with pytest.raises(FrameCodecError, match="ended"):
            list(parser.feed(encode_frame(FrameType.OPEN_OK, 1)))
        with pytest.raises(FrameCodecError, match="ended"):
            parser.end_of_stream()

    def test_oversize_declared_does_not_stage_payload(self) -> None:
        parser = FrameParser()
        raw = struct.pack(HEADER_FMT, 4, 0, 0, 4, MAX_PAYLOAD + 1)
        with pytest.raises(FrameCodecError):
            list(parser.feed(raw + b"\0" * 1000))
        with pytest.raises(FrameCodecError):
            parser.end_of_stream()

    def test_frame_count_ceiling_exact(self) -> None:
        parser = FrameParser()
        one = encode_frame(FrameType.HALF_CLOSE, 1)
        frames = list(parser.feed(one * MAX_FRAMES))
        assert len(frames) == MAX_FRAMES
        with pytest.raises(FrameCodecError, match="ceiling"):
            list(parser.feed(one))

    def test_frame_count_ceiling_exceeded_in_one_feed(self) -> None:
        one = encode_frame(FrameType.HALF_CLOSE, 1)
        parser = FrameParser()
        with pytest.raises(FrameCodecError, match="ceiling"):
            list(parser.feed(one * (MAX_FRAMES + 1)))

    def test_ceiling_mixed_shapes(self) -> None:
        parser = FrameParser()
        blob = (
            encode_frame(FrameType.OPEN_OK, 1)
            + encode_frame(FrameType.DATA, 1, b"\0" * MAX_PAYLOAD)
            + encode_frame(FrameType.OPEN_OK, 2)
        )
        # Each blob is 3 frames; enough blobs exceed the 4096 ceiling.
        with pytest.raises(FrameCodecError):
            list(parser.feed(blob * 1500))

    def test_no_whole_input_allocation_on_huge_declared_length(self) -> None:
        # A pathologically declared length must fail without staging it.
        parser = FrameParser()
        hostile = struct.pack(HEADER_FMT, 4, 0, 0, 1, 0xFFFFFFF0) + b"\0" * 17
        with pytest.raises(FrameCodecError):
            list(parser.feed(hostile))
        # The internal staging buffer is empty (nothing was accumulated).
        assert len(parser._buf) == 0


class TestFrameFailLatched:
    def test_error_then_no_reuse(self) -> None:
        parser = FrameParser()
        with pytest.raises(FrameCodecError):
            list(parser.feed(raw_frame(9)))
        with pytest.raises(FrameCodecError):
            list(parser.feed(encode_frame(FrameType.DATA, 1)))
        with pytest.raises(FrameCodecError):
            parser.end_of_stream()

    def test_bad_frame_mid_chunk_fails_whole_chunk(self) -> None:
        good = encode_frame(FrameType.HALF_CLOSE, 1)
        parser = FrameParser()
        with pytest.raises(FrameCodecError):
            list(parser.feed(good + raw_frame(9) + good))
        with pytest.raises(FrameCodecError):
            parser.end_of_stream()


# ---------------------------------------------------------------------------
# CONNECT header parsing
# ---------------------------------------------------------------------------


def connect_raw(line: str = "CONNECT openai.com:443 HTTP/1.1") -> bytes:
    return (line + "\r\nHost: openai.com:443\r\n\r\n").encode("ascii")


class TestConnectHappyPath:
    def test_minimal_parse(self) -> None:
        req = parse_connect_headers(connect_raw())
        assert req.host == "openai.com"
        assert req.port == 443
        assert req.leftover == b""

    def test_leftover_preserved_exactly(self) -> None:
        trail = b"\x16\x03\x01\x00fake-tls-bytes"
        req = parse_connect_headers(connect_raw() + trail)
        assert isinstance(req, ConnectRequest)
        assert req.leftover == trail

    def test_ordinary_extra_headers_allowed(self) -> None:
        data = b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nUser-Agent: x\r\n\r\n"
        assert parse_connect_headers(data).port == 443


class TestConnectRejections:
    @pytest.mark.parametrize(
        "data",
        [
            # method/protocol
            b"GET openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.0\r\nHost: openai.com:443\r\n\r\n",
            b"connect openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/2\r\nHost: openai.com:443\r\n\r\n",
            # authority tricks / encodings / literal IPs / ports
            b"CONNECT openai.com:8443 HTTP/1.1\r\nHost: openai.com:8443\r\n\r\n",
            b"CONNECT openai.com HTTP/1.1\r\nHost: openai.com\r\n\r\n",
            b"CONNECT user@openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\n\r\n",
            b"CONNECT openai.com:443/x HTTP/1.1\r\nHost: openai.com:443\r\n\r\n",
            b"CONNECT openai%2ecom:443 HTTP/1.1\r\nHost: openai%2ecom:443\r\n\r\n",
            b"CONNECT 93.184.216.34:443 HTTP/1.1\r\nHost: 93.184.216.34:443\r\n\r\n",
            b"CONNECT 0.0.0.0.0:443 HTTP/1.1\r\nHost: 0.0.0.0.0:443\r\n\r\n",
            b"CONNECT 3.0.1.7.3.4:443 HTTP/1.1\r\nHost: 1.2.3.4:443\r\n\r\n",
            b"CONNECT openai.com.:443 HTTP/1.1\r\nHost: openai.com.:443\r\n\r\n",
            b"CONNECT OPENAI.com:443 HTTP/1.1\r\nHost: OPENAI.com:443\r\n\r\n",
            b"CONNECT ope\ta.com:443 HTTP/1.1\r\nHost: ope\ta.com:443\r\n\r\n",
            b"CONNECT openai.com :443 HTTP/1.1\r\nHost: openai.com:443\r\n\r\n",
            b"CONNECT openai.com : 443 HTTP/1.1\r\nHost: openai.com:443\r\n\r\n",
            # Host mismatches / duplicates / missing / framing
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nHost: openai.com:443\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\n\r\n",
            # forbidden headers and malformed lines
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nContent-Length: 5\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nUpgrade: h2c\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nProxy-Authorization: Basic xx\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\nBadHeader no-colon\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\n : fold\r\nHost: openai.com:443\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\nHost: openai.com:443\n\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nX: a\x00b\r\n\r\n",
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost\r\n\r\n",
        ],
    )
    def test_reject(self, data: bytes) -> None:
        with pytest.raises(ConnectHeaderError):
            parse_connect_headers(data)

    def test_non_ascii_authority(self) -> None:
        data = "CONNECT \u00f6ai.com:443 HTTP/1.1\r\nHost: \u00f6ai.com:443\r\n\r\n".encode("utf-8")
        with pytest.raises(ConnectHeaderError, match="ASCII"):
            parse_connect_headers(data)

    def test_header_too_large(self) -> None:
        pre = b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\n\r\n"
        data = pre + b"X" * CONNECT_HEADER_LIMIT
        with pytest.raises(ConnectHeaderError, match="8 KiB"):
            parse_connect_headers(data)

    def test_no_terminator_in_buffered_input(self) -> None:
        with pytest.raises(ConnectHeaderError, match="terminator"):
            parse_connect_headers(b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\n")

    def test_empty_and_multi_colon_targets(self) -> None:
        with pytest.raises(ConnectHeaderError):
            parse_connect_headers(b"CONNECT :443 HTTP/1.1\r\nHost: :443\r\n\r\n")
        with pytest.raises(ConnectHeaderError):
            parse_connect_headers(b"CONNECT ::443 HTTP/1.1\r\nHost: ::443\r\n\r\n")


class TestConnectHeaderCharacterPolicy:
    """Repair B: strict field-value controls, duplicate names, Host OWS."""

    @pytest.mark.parametrize(
        "control_char",
        [chr(i) for i in range(1, 32) if i != 9] + ["\x7f"],
    )
    def test_control_chars_rejected_in_header_value(self, control_char: str) -> None:
        header = f"User-Agent: a{control_char}b\r\n"
        data = (
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\n"
            + header.encode("latin-1")
            + b"\r\n"
        )
        with pytest.raises(ConnectHeaderError):
            parse_connect_headers(data)

    def test_htab_allowed_in_header_value(self) -> None:
        data = (
            b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nUser-Agent: a\tb\r\n\r\n"
        )
        req = parse_connect_headers(data)
        assert req.host == "openai.com"

    def test_bare_cr_and_bare_lf_rejected(self) -> None:
        with pytest.raises(ConnectHeaderError):
            parse_connect_headers(
                b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nX: a\rb\r\n\r\n"
            )
        with pytest.raises(ConnectHeaderError):
            parse_connect_headers(
                b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\nX: a\x0ab\r\n\r\n"
            )

    def test_duplicate_header_names_case_insensitive(self) -> None:
        base = b"CONNECT openai.com:443 HTTP/1.1\r\nHost: openai.com:443\r\n"
        with pytest.raises(ConnectHeaderError, match="duplicate"):
            parse_connect_headers(base + b"User-Agent: a\r\nUser-Agent: b\r\n\r\n")
        with pytest.raises(ConnectHeaderError, match="duplicate"):
            parse_connect_headers(base + b"USER-AGENT: a\r\nuser-agent: b\r\n\r\n")

    def test_host_ows_trim_is_sp_htab_only(self) -> None:
        data = b"CONNECT openai.com:443 HTTP/1.1\r\nHost:  openai.com:443\t\r\n\r\n"
        assert parse_connect_headers(data).host == "openai.com"


class TestCanonicalHostnameShared:
    def test_accepts_lowercase_ldh(self) -> None:
        assert require_canonical_hostname("uboat.example") == "uboat.example"
        assert require_canonical_hostname("openai.999") == "openai.999"

    @pytest.mark.parametrize(
        "host",
        [
            "",
            "a",
            "A.com",
            "-a.com",
            "a-.com",
            "a.com-.",
            "a...com",
            "openai com",
            "openai\tcom",
            "\x00.com",
            "1.2.3.4",
            "93.184.216.34",
            "openai.com:",
            "openai%2ecom",
            443,
            None,
        ],
    )
    def test_rejects(self, host: object) -> None:
        with pytest.raises(ValueError):
            require_canonical_hostname(host)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Destination policy
# ---------------------------------------------------------------------------


class TestDestinationPolicyHappyPath:
    def test_public_answers_approved_canonical(self) -> None:
        inv = parse_owned_inventory([])
        assert validate_dns_answers(["93.184.216.34", "93.184.216.34"], inv) == ["93.184.216.34"]

    def test_eight_answers_ok_nine_rejected(self) -> None:
        inv = parse_owned_inventory([])
        ok = [f"6.{i}.{i * 2}.{i}" for i in range(8)]
        assert validate_dns_answers(ok, inv)
        with pytest.raises(DestinationPolicyError, match="more than"):
            validate_dns_answers(ok + ["6.9.0.1"], inv)


class TestDestinationPolicyRejections:
    @pytest.mark.parametrize(
        "answer",
        [
            "127.0.0.1",  # loopback
            "0.0.0.0",  # unspecified
            "10.0.0.5",  # private class A
            "172.16.9.9",  # private 172.16/12
            "192.168.4.4",  # private class C
            "169.254.169.254",  # link-local metadata
            "169.254.0.1",
            "224.0.0.1",  # multicast
            "255.255.255.255",  # broadcast within reserved 240/4 tail
            "240.0.0.1",  # reserved
            "100.64.0.1",  # CGNAT shared address space
            "192.88.99.1",  # 6to4 relay (translation)
            "::1",  # IPv6
            "::ffff:93.184.216.34",  # IPv4-mapped IPv6
            "2001::1",  # Teredo-range etc.; no IPv6 dialable form is allowed
            "openai.com",  # hostname, not numeric
            "93.184.216.034",  # leading-zero form is not strict
            "3.0.1.7.3.4",  # over-long dotted
            "93.184.216.34:443",  # with port
            "",  # empty
        ],
    )
    def test_single_bad_answer_rejects_whole_admission(self, answer: str) -> None:
        inv = parse_owned_inventory([])
        with pytest.raises(DestinationPolicyError):
            validate_dns_answers(["93.184.216.34", answer], inv)
        with pytest.raises(DestinationPolicyError):
            validate_dns_answers([answer, "93.184.216.34"], inv)

    def test_mixed_public_private_never_partially_passes(self) -> None:
        inv = parse_owned_inventory([])
        with pytest.raises(DestinationPolicyError):
            validate_dns_answers(["8.8.8.8", "127.0.0.1", "1.1.1.1"], inv)

    def test_empty_dns_answers_rejected(self) -> None:
        inv = parse_owned_inventory([])
        with pytest.raises(DestinationPolicyError, match="at least one"):
            validate_dns_answers([], inv)

    def test_owned_none_is_prohibited(self) -> None:
        with pytest.raises(DestinationPolicyError, match="explicitly"):
            parse_owned_inventory(None)

    def test_owned_explicitly_empty_permitted_unit_only(self) -> None:
        inv = parse_owned_inventory([])
        assert inv.addresses == frozenset()
        assert validate_dns_answers(["93.184.216.34"], inv) == ["93.184.216.34"]

    def test_owned_address_and_network_rejection(self) -> None:
        inv = parse_owned_inventory(["13.107.42.14", "17.0.0.0/8"])
        with pytest.raises(DestinationPolicyError, match="owned"):
            validate_dns_answers(["93.184.216.34", "13.107.42.14"], inv)
        with pytest.raises(DestinationPolicyError, match="owned"):
            validate_dns_answers(["17.254.1.9"], inv)
        with pytest.raises(DestinationPolicyError, match="owned"):
            validate_dns_answers(["17.0.0.0"], inv)

    def test_owned_malformed_entries_fail_closed(self) -> None:
        with pytest.raises(DestinationPolicyError):
            parse_owned_inventory(["not-an-ip"])
        with pytest.raises(DestinationPolicyError):
            parse_owned_inventory(["203.0.113.7/99"])
        with pytest.raises(DestinationPolicyError):
            parse_owned_inventory([""])

    def test_ipv6_inventory_entries_validate_but_do_not_match(self) -> None:
        inv = parse_owned_inventory(["2001:db8::1", "2001:db8::/32", "203.0.113.5"])
        assert validate_dns_answers(["93.184.216.34"], inv) == ["93.184.216.34"]

    def test_dns_rebinding_boundary_shot(self) -> None:
        # The rebinding trap: an earlier admission approved a public answer for
        # a manifest host; a later admission of the SAME host must revalidate
        # freshly injected (possibly now-private) answers, not reuse approval.
        inv = parse_owned_inventory([])
        assert validate_dns_answers(["93.184.216.34"], inv) == ["93.184.216.34"]
        with pytest.raises(DestinationPolicyError, match="private"):
            validate_dns_answers(["10.0.0.5"], inv)
        # NOTE: actual pinned dial (fresh revalidation, numeric sockaddr-only
        # dialing with peer-address verification) is NOT implemented in this
        # pure core; this test only pins the policy-boundary behaviour.


# ---------------------------------------------------------------------------
# Byte budget
# ---------------------------------------------------------------------------


class TestByteBudget:
    def test_reserve_reconcile_basic_and_leftover_free(self) -> None:
        budget = ByteBudget()
        token = budget.reserve(1000, DIRECTION_READ)
        assert budget.remaining == budget.limit - 1000
        budget.reconcile(token, 400)
        assert budget.charged_actual == 400
        assert budget.charged_read == 400
        assert budget.charged_write == 0
        assert budget.outstanding == 0
        assert budget.remaining == budget.limit - 400

    def test_cancel_is_zero_byte_terminal(self) -> None:
        budget = ByteBudget(1000)
        token = budget.reserve(500, DIRECTION_WRITE)
        budget.cancel(token)
        assert budget.charged_actual == 0
        assert budget.charged_write == 0
        assert budget.outstanding == 0
        assert budget.remaining == 1000
        with pytest.raises(BudgetError):
            budget.reconcile(token, 0)  # cancelled/committed token cannot repeat

    def test_exhaustion_latches_closed(self) -> None:
        budget = ByteBudget(100)
        token = budget.reserve(100, DIRECTION_READ)
        budget.reconcile(token, 100)
        assert budget.is_closed
        assert "consumed" in (budget.closed_reason or "")
        with pytest.raises(BudgetError):
            budget.reserve(1, DIRECTION_READ)

    def test_over_reservation_latches_and_blocks_later_uses(self) -> None:
        budget = ByteBudget(100)
        with pytest.raises(BudgetError, match="exhausted"):
            budget.reserve(101, DIRECTION_READ)
        assert budget.is_closed
        with pytest.raises(BudgetError):
            budget.reserve(1, DIRECTION_READ)

    def test_outstanding_reservation_gates_overlapping_sequence(self) -> None:
        budget = ByteBudget(100)
        a = budget.reserve(60, DIRECTION_READ)
        b = budget.reserve(40, DIRECTION_WRITE)  # fills the remaining 40
        with pytest.raises(BudgetError, match="exhausted"):
            budget.reserve(1, DIRECTION_READ)
        # Exhaustion latches the budget closed even for legitimate tokens.
        with pytest.raises(BudgetError, match="closed"):
            budget.reconcile(b, 40)
        assert budget.is_closed
        del a

    def test_overlapping_reservations_reconcile_within_limit(self) -> None:
        budget = ByteBudget(100)
        a = budget.reserve(60, DIRECTION_READ)
        b = budget.reserve(40, DIRECTION_WRITE)
        budget.reconcile(a, 60)
        budget.reconcile(b, 40)
        assert budget.charged_read == 60
        assert budget.charged_write == 40
        assert budget.is_closed

    def test_reserve_beyond_remaining_exhausts_and_latches(self) -> None:
        budget = ByteBudget(100)
        a = budget.reserve(90, DIRECTION_READ)
        with pytest.raises(BudgetError, match="exhausted"):
            budget.reserve(20, DIRECTION_WRITE)
        assert budget.is_closed
        with pytest.raises(BudgetError):
            budget.reconcile(a, 90)  # closed accounting cannot be reused

    def test_reconcile_refund_releases_unused(self) -> None:
        budget = ByteBudget(100)
        a = budget.reserve(90, DIRECTION_READ)
        # Planned-but-actual-smaller read frees the unused remainder.
        budget.reconcile(a, 50)
        b = budget.reserve(40, DIRECTION_WRITE)
        budget.reconcile(b, 40)
        assert budget.charged_actual == 90
        assert budget.charged_read == 50
        assert budget.charged_write == 40
        assert not budget.is_closed

    def test_actual_exceeding_reserved_rejected_then_fail_latched(self) -> None:
        budget = ByteBudget(500)
        token = budget.reserve(200, DIRECTION_READ)
        with pytest.raises(BudgetError, match="exceeds reservation"):
            budget.reconcile(token, 201)
        assert budget.is_closed

    def test_double_reconcile_is_generic_unknown_token(self) -> None:
        budget = ByteBudget(1000)
        token = budget.reserve(100, DIRECTION_READ)
        budget.reconcile(token, 100)
        with pytest.raises(BudgetError, match="unknown reservation token"):
            budget.reconcile(token, 0)
        assert budget.is_closed

    def test_forged_and_caller_constructed_tokens_rejected(self) -> None:
        budget = ByteBudget(1000)
        with pytest.raises(BudgetError, match="unknown reservation token"):
            budget.reconcile(b"not-a-reservation", 1)  # type: ignore[arg-type]

        # Caller-constructed token (not issued by reserve) fails closed.
        forged = Reservation()
        with pytest.raises(BudgetError, match="unknown reservation token"):
            budget.reconcile(forged, 1)
        assert budget.is_closed
        # Tokens are zero-data: no writable state to forge through.
        other_budget = ByteBudget(1000)
        issued = other_budget.reserve(10, DIRECTION_READ)
        with pytest.raises(AttributeError):
            issued.amount = 999  # type: ignore[attr-defined]

    def test_cross_budget_token_rejected(self) -> None:
        other = ByteBudget(1000)
        budget = ByteBudget(1000)
        token = other.reserve(10, DIRECTION_READ)
        with pytest.raises(BudgetError, match="unknown reservation token"):
            budget.reconcile(token, 10)
        assert budget.is_closed
        # The other budget's token is still valid on its own budget.
        other.reconcile(token, 10)
        assert other.charged_actual == 10

    def test_registry_does_not_retain_consumed_tokens(self) -> None:
        budget = ByteBudget(2_000_000)
        for _ in range(50):
            token = budget.reserve(100, DIRECTION_READ)
            budget.reconcile(token, 100)
        assert len(budget._active) == 0

    def test_subclassed_token_cannot_forge(self) -> None:
        class EvilReservation(Reservation):
            pass

        budget = ByteBudget(1000)
        with pytest.raises(BudgetError, match="unknown reservation token"):
            budget.reconcile(EvilReservation(), 999)  # type: ignore[arg-type]
        assert budget.is_closed
        assert budget.charged_actual == 0

    def test_closed_by_close_rejects_all(self) -> None:
        budget = ByteBudget(1000)
        token = budget.reserve(100, DIRECTION_READ)
        budget.close("teardown")
        assert budget.closed_reason == "teardown"
        with pytest.raises(BudgetError, match="closed"):
            budget.reserve(1, DIRECTION_READ)
        with pytest.raises(BudgetError, match="closed"):
            budget.reconcile(token, 100)

    def test_reserve_rejects_bad_inputs_independently(self) -> None:
        # Each invalid input is tested against a fresh budget so an earlier
        # case's fail-latch cannot mask a later case (no after-fail loops).
        for amount in (0, -1, 1.0, None, True):
            budget = ByteBudget(100)
            with pytest.raises(BudgetError):
                budget.reserve(amount, DIRECTION_READ)  # type: ignore[arg-type]
            assert budget.is_closed
        budget = ByteBudget(100)
        with pytest.raises(BudgetError):
            budget.reserve(1, "udp")

    def test_reconcile_rejects_bad_actuals_independently(self) -> None:
        for actual in (-1, 1.0, None, True):
            budget = ByteBudget(1000)
            token = budget.reserve(100, DIRECTION_READ)
            with pytest.raises(BudgetError):
                budget.reconcile(token, actual)  # type: ignore[arg-type]
            assert budget.is_closed

    def test_limit_must_be_exact_positive_int(self) -> None:
        for limit in (0, -1, 1.0, None, True, 100.5):
            with pytest.raises(ValueError):
                ByteBudget(limit)  # type: ignore[arg-type]
        assert ByteBudget(77).limit == 77

    def test_sequential_interleaving_never_overspends(self) -> None:
        budget = ByteBudget(1000)
        total = 0
        for i in range(20):
            direction = DIRECTION_READ if i % 2 == 0 else DIRECTION_WRITE
            token = budget.reserve(50, direction)
            charged = 50 if i % 2 == 0 else 30
            budget.reconcile(token, charged)
            total += charged
            assert budget.remaining == 1000 - total - budget.outstanding
        assert budget.charged_actual == total

    def test_over_limit_reconcile_defence(self) -> None:
        # With reserve gating, reconcile actual <= reserved implies charged can
        # never exceed limit; the internal defence exists anyway. Probe it via
        # deliberate internal corruption.
        budget = ByteBudget(100)
        token = budget.reserve(50, DIRECTION_READ)
        budget._actual = 96
        with pytest.raises(BudgetError, match="exceed limit"):
            budget.reconcile(token, 50)

    def test_fail_run_alias(self) -> None:
        budget = ByteBudget(100)
        budget.fail_run("child died")
        assert budget.is_closed
        assert budget.closed_reason == "child died"

    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            ByteBudget(0)
        assert ByteBudget(77).limit == 77


# ---------------------------------------------------------------------------
# Scope guard: no socket / DNS / subprocess / time behaviour in this core
# ---------------------------------------------------------------------------


def test_module_imports_are_pure() -> None:
    import scripts.web_fetch_pilot_core as core

    forbidden = {
        "socket",
        "subprocess",
        "selectors",
        "ssl",
        "urllib",
        "http",
        "time",
        "asyncio",
    }
    for name, value in vars(core).items():
        if getattr(value, "__name__", None) in forbidden:
            raise AssertionError(f"forbidden module {name} imported into pilot core")


def test_offline_readiness_is_not_claimed() -> None:
    # Drain an iterator and use the core API only; nothing here may connect.
    items = []
    parser = FrameParser()
    items.extend(parser.feed(encode_frame(FrameType.OPEN, 1, b"openai.com:443")))
    items.extend(parser.feed(encode_frame(FrameType.OPEN_OK, 1)))
    parser.end_of_stream()
    assert [f.frame_type for f in items] == [FrameType.OPEN, FrameType.OPEN_OK]
