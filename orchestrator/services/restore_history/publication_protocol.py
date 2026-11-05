"""Uncalled, bounded prototype frames. No socket, authentication or effect authority.

Payloads/results are synthetic bytes in this slice, not a production CSV/client
schema. A future listener must bound reads before allocating a complete frame.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import struct
from typing import Any, Literal, cast
from uuid import UUID

from .model import HistoryError

Phase = Literal["prepare", "reserve", "catalog", "present", "terminal"]
Action = Literal["prepare", "reserve", "catalog", "present", "terminal", "observe", "retire"]
Status = Literal["pending", "established", "needs_attention", "retired"]
PHASES: tuple[Phase, ...] = ("prepare", "reserve", "catalog", "present", "terminal")
ACTIONS = (*PHASES, "observe", "retire")
MAX_FRAME = 8192
MAX_BODY = 2048
MAX_RESULT = 1024
ERROR_CODES = frozenset(
    {
        "publication_protocol_refused",
        "packet_owner_forked",
        "packet_owner_wrong_loop",
        "packet_owner_reentrant",
        "packet_owner_blocked",
        "assignment_refused",
        "assignment_conflict",
        "packet_tombstones_exhausted",
        "packet_work_owned",
        "packet_phase_order",
        "packet_retired",
        "packet_busy",
        "packet_deadline",
        "packet_capacity_exhausted",
        "packet_caller_cancelled",
        "packet_outcome_uncertain",
        "packet_result_refused",
    }
)


class ProtocolError(HistoryError):
    def __init__(self, code: str = "publication_protocol_refused") -> None:
        safe = code if type(code) is str and code in ERROR_CODES else "publication_protocol_refused"
        super().__init__(
            safe,
            uncertain=safe
            in {
                "packet_outcome_uncertain",
                "packet_owner_blocked",
                "packet_result_refused",
                "packet_owner_reentrant",
                "packet_work_owned",
            },
        )


def identifier(value: object) -> UUID:
    if type(value) is not UUID or not value.int:
        raise ProtocolError()
    return value


def _uuid(value: Any) -> UUID:
    try:
        result = UUID(value) if type(value) is str else None
        if result is None or not result.int or str(result) != value:
            raise ProtocolError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise ProtocolError() from None


def _binary(value: object, maximum: int) -> bytes:
    if type(value) is not bytes or len(value) > maximum:
        raise ProtocolError()
    return value


def _bytes(value: Any, maximum: int) -> bytes:
    if type(value) is not str or len(value) > 4 * ((maximum + 2) // 3):
        raise ProtocolError()
    try:
        result = base64.b64decode(value, validate=True)
        if base64.b64encode(result).decode("ascii") != value:
            raise ProtocolError()
        return _binary(result, maximum)
    except (ValueError, UnicodeError):
        raise ProtocolError() from None


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("ascii")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProtocolError() from None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError()
        result[key] = value
    return result


def _load(frame: bytes, keys: str) -> dict[str, Any]:
    if type(frame) is not bytes or not 4 < len(frame) <= MAX_FRAME + 4:
        raise ProtocolError()
    if struct.unpack("!I", frame[:4])[0] != len(frame) - 4:
        raise ProtocolError()
    try:
        raw = frame[4:]
        value = json.loads(raw, object_pairs_hook=_pairs)
        if type(value) is not dict or set(value) != set(keys.split()) or _canonical(value) != raw:
            raise ProtocolError()
        if type(value["version"]) is not int or value["version"] != 1:
            raise ProtocolError()
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProtocolError() from None


def _frame(value: dict[str, Any]) -> bytes:
    raw = _canonical(value)
    if not 0 < len(raw) <= MAX_FRAME:
        raise ProtocolError()
    return struct.pack("!I", len(raw)) + raw


@dataclass(frozen=True, slots=True)
class Request:
    session: UUID
    handle: UUID
    request_id: UUID
    action: Action
    payload: bytes = field(default=b"", repr=False)


@dataclass(frozen=True, slots=True)
class Reply:
    session: UUID
    handle: UUID
    request_id: UUID
    action: Action
    status: Status
    phase: Phase | None = None
    result: bytes | None = field(default=None, repr=False)


def encode_request(request: Request) -> bytes:
    if (
        type(request) is not Request
        or type(request.action) is not str
        or request.action not in ACTIONS
    ):
        raise ProtocolError()
    payload = _binary(request.payload, MAX_BODY)
    if request.action != "prepare" and payload:
        raise ProtocolError()
    return _frame(
        dict(
            version=1,
            session=str(identifier(request.session)),
            handle=str(identifier(request.handle)),
            request_id=str(identifier(request.request_id)),
            action=request.action,
            payload=base64.b64encode(payload).decode("ascii"),
        )
    )


def decode_request(frame: bytes) -> Request:
    value = _load(frame, "version session handle request_id action payload")
    request = Request(
        _uuid(value["session"]),
        _uuid(value["handle"]),
        _uuid(value["request_id"]),
        value["action"],
        _bytes(value["payload"], MAX_BODY),
    )
    if encode_request(request) != frame:
        raise ProtocolError()
    return request


def encode_reply(reply: Reply) -> bytes:
    if (
        type(reply) is not Reply
        or type(reply.action) is not str
        or reply.action not in ACTIONS
        or type(reply.status) is not str
        or reply.status not in ("pending", "established", "needs_attention", "retired")
    ):
        raise ProtocolError()
    if reply.status == "established":
        if (
            type(reply.phase) is not str
            or reply.phase not in PHASES
            or (reply.action != "observe" and reply.action != reply.phase)
            or reply.result is None
        ):
            raise ProtocolError()
        result = base64.b64encode(_binary(reply.result, MAX_RESULT)).decode("ascii")
    else:
        if reply.phase is not None or reply.result is not None:
            raise ProtocolError()
        result = None
    return _frame(
        dict(
            version=1,
            session=str(identifier(reply.session)),
            handle=str(identifier(reply.handle)),
            request_id=str(identifier(reply.request_id)),
            action=reply.action,
            status=reply.status,
            phase=reply.phase,
            result=result,
            retry_safe=False,
        )
    )


def decode_reply(frame: bytes) -> Reply:
    value = _load(frame, "version session handle request_id action status phase result retry_safe")
    if value["retry_safe"] is not False:
        raise ProtocolError()
    reply = Reply(
        _uuid(value["session"]),
        _uuid(value["handle"]),
        _uuid(value["request_id"]),
        value["action"],
        value["status"],
        value["phase"],
        None if value["result"] is None else _bytes(value["result"], MAX_RESULT),
    )
    if encode_reply(reply) != frame:
        raise ProtocolError()
    return cast(Reply, reply)
