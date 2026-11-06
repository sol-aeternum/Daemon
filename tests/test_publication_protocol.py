"""Closed synthetic frames, not a listener or authenticated transport."""

import base64
from dataclasses import replace
import json
import struct
from typing import Any, cast
from uuid import UUID

import pytest

from orchestrator.services.restore_history.publication_protocol import (
    ACTIONS,
    MAX_BODY,
    MAX_FRAME,
    MAX_RESULT,
    PHASES,
    ProtocolError,
    Reply,
    Request,
    decode_reply,
    decode_request,
    encode_reply,
    encode_request,
)


def request():
    return Request(UUID(int=1), UUID(int=2), UUID(int=3), "prepare", b"synthetic")


def frame(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return struct.pack("!I", len(raw)) + raw


def value():
    return json.loads(encode_request(request())[4:])


@pytest.mark.parametrize("action", ACTIONS)
def test_request_roundtrip_has_no_worker_identity_or_admission_opcode(action):
    r = replace(request(), action=action, payload=b"" if action != "prepare" else b"\x00\xff")
    assert decode_request(encode_request(r)) == r
    assert set(json.loads(encode_request(r)[4:])) == {
        "version",
        "session",
        "handle",
        "request_id",
        "action",
        "payload",
    }


@pytest.mark.parametrize("size", [0, 1, MAX_BODY])
def test_exact_body_bounds(size):
    r = replace(request(), payload=b"x" * size)
    assert decode_request(encode_request(r)) == r
    with pytest.raises(ProtocolError):
        encode_request(replace(r, payload=b"x" * (MAX_BODY + 1)))


@pytest.mark.parametrize(
    "field,bad",
    [
        ("version", True),
        ("version", 1.0),
        ("version", 2),
        ("session", "00000000-0000-0000-0000-000000000000"),
        ("session", "00000000000000000000000000000001"),
        ("handle", None),
        ("request_id", True),
        ("action", "admit"),
        ("action", []),
        ("payload", "!"),
        ("payload", []),
        ("payload", "eA"),
        ("payload", base64.b64encode(b"x" * (MAX_BODY + 1)).decode()),
    ],
)
def test_malformed_or_open_request_schema_refuses(field, bad):
    v = value()
    v[field] = bad
    with pytest.raises(ProtocolError):
        decode_request(frame(v))


@pytest.mark.parametrize(
    "damage",
    [
        "extra",
        "missing",
        "duplicate",
        "whitespace",
        "trailing",
        "truncated",
        "wrong_length",
        "huge",
        "invalid_utf8",
        "nested",
    ],
)
def test_corruption_and_frame_ambiguity_refuse(damage):
    v = value()
    good = encode_request(request())
    if damage == "extra":
        v["worker_id"] = str(UUID(int=4))
        bad = frame(v)
    elif damage == "missing":
        del v["session"]
        bad = frame(v)
    elif damage == "duplicate":
        raw = good[4:-1] + b',"version":1}'
        bad = struct.pack("!I", len(raw)) + raw
    elif damage == "whitespace":
        raw = good[4:] + b" "
        bad = struct.pack("!I", len(raw)) + raw
    elif damage == "trailing":
        bad = good + good
    elif damage == "truncated":
        bad = good[:-1]
    elif damage == "wrong_length":
        bad = b"\xff" * 4 + good[4:]
    elif damage == "huge":
        bad = b"\x00" * (MAX_FRAME + 5)
    elif damage == "invalid_utf8":
        bad = struct.pack("!I", 1) + b"\xff"
    else:
        raw = b"[" * 2000 + b"]" * 2000
        bad = struct.pack("!I", len(raw)) + raw
    with pytest.raises(ProtocolError):
        decode_request(bad)


@pytest.mark.parametrize("phase", PHASES)
def test_only_established_bounded_reply_data_roundtrips(phase):
    r = request()
    reply = Reply(r.session, r.handle, r.request_id, phase, "established", phase, b"x" * MAX_RESULT)
    assert decode_reply(encode_reply(reply)) == reply
    wire = json.loads(encode_reply(reply)[4:])
    assert wire["retry_safe"] is False
    wire["retry_safe"] = True
    with pytest.raises(ProtocolError):
        decode_reply(frame(wire))
    with pytest.raises(ProtocolError):
        encode_reply(replace(reply, result=b"x" * (MAX_RESULT + 1)))


@pytest.mark.parametrize("status", ["pending", "needs_attention", "retired"])
def test_uncertain_or_incomplete_replies_cannot_smuggle_success(status):
    r = request()
    reply = Reply(r.session, r.handle, r.request_id, "observe", status)
    assert decode_reply(encode_reply(reply)) == reply
    with pytest.raises(ProtocolError):
        encode_reply(replace(reply, result=b"late success"))
    with pytest.raises(ProtocolError):
        encode_reply(replace(reply, phase="prepare"))


def test_direct_callers_receive_the_same_validation_and_safe_representation():
    r = request()
    assert "synthetic" not in repr(r)
    for invalid in (
        replace(r, session=cast(Any, str(r.session))),
        replace(r, action=cast(Any, True)),
        replace(r, action="reserve", payload=b"x"),
    ):
        with pytest.raises(ProtocolError):
            encode_request(invalid)


def test_error_codes_do_not_expose_input_or_mislabel_unknown_outcomes():
    assert str(ProtocolError("synthetic private input")) == "publication_protocol_refused"
    assert not ProtocolError("assignment_refused").uncertain
    assert ProtocolError("packet_outcome_uncertain").uncertain
    assert ProtocolError("packet_result_refused").uncertain
    assert ProtocolError("packet_owner_blocked").uncertain
