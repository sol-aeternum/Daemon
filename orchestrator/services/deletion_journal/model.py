"""Closed structural types plus replay validator for the J1 deletion journal.

Structural layer only (proposal §2/§3): frozen typed fields, canonical command
digests, canonical v1 frame encode/decode and a replay structural validator
(``JournalState``). No wallclock reads, no storage I/O, no phase appliers, no
pruning. Deadline eligibility is checked against recorded journal timestamps
only, never against the system clock.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn
from uuid import UUID

from .errors import JournalError

__all__ = [
    "AckFields",
    "Cause",
    "Command",
    "Entry",
    "JournalState",
    "ReadyFields",
    "RequestFields",
    "TerminalFields",
    "command_digest",
    "decode_entry",
    "encode_entry",
]

_VERSION = 1
_DIGEST_SIZE = 32
_ZERO_DIGEST = bytes(_DIGEST_SIZE)
_INT64_MAX = 2**63 - 1
_MAX_CAUSES = 128
_GRACE_WINDOW = timedelta(days=7)
_UTC_OFFSET = timedelta(0)

_CAUSE_CODES = frozenset({"billing", "abuse", "operator", "deletion", "reset"})
_PRIOR_STATUSES = frozenset({"active", "suspended"})
_REQUEST_REASONS = frozenset({"legal", "abuse"})
_RESET_CANCEL_REASONS = frozenset({"owner", "timeout"})

_REQUESTED = "requested"
_GRACE_STARTED = "grace_started"
_RESTORED = "restored"
_PURGED = "purged"
_RESET_REQUESTED = "reset_requested"
_RESET_REPORT_READY = "reset_report_ready"
_RESET_ACKNOWLEDGED = "reset_acknowledged"
_RESET_PURGED = "reset_purged"
_RESET_CANCELLED = "reset_cancelled"

_EVENT_TYPES = frozenset(
    {
        _REQUESTED,
        _GRACE_STARTED,
        _RESTORED,
        _PURGED,
        _RESET_REQUESTED,
        _RESET_REPORT_READY,
        _RESET_ACKNOWLEDGED,
        _RESET_PURGED,
        _RESET_CANCELLED,
    }
)
_FIELDS_KIND: dict[str, str] = {
    _REQUESTED: "request",
    _RESET_REQUESTED: "request",
    _GRACE_STARTED: "ready",
    _RESET_REPORT_READY: "ready",
    _RESET_ACKNOWLEDGED: "ack",
    _RESTORED: "terminal",
    _PURGED: "terminal",
    _RESET_PURGED: "terminal",
    _RESET_CANCELLED: "terminal",
}
_OP_FAMILY: dict[str, str] = {
    _REQUESTED: "deletion",
    _GRACE_STARTED: "deletion",
    _RESTORED: "deletion",
    _PURGED: "deletion",
    _RESET_REQUESTED: "reset",
    _RESET_REPORT_READY: "reset",
    _RESET_ACKNOWLEDGED: "reset",
    _RESET_PURGED: "reset",
    _RESET_CANCELLED: "reset",
}
_OWN_CAUSE_CODE: dict[str, str] = {_REQUESTED: "deletion", _RESET_REQUESTED: "reset"}

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _invalid() -> NoReturn:
    raise JournalError("invalid_entry")


def _conflict() -> NoReturn:
    raise JournalError("conflict")


def _canonical_json(value: Any) -> bytes:
    """Strict deterministic UTF-8 JSON: sorted keys, no whitespace, no NaN."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_aware(dt: datetime) -> datetime:
    if not isinstance(dt, datetime):
        _invalid()
    if dt.tzinfo is None or dt.utcoffset() is None:
        _invalid()
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    """Canonical UTC ISO-8601 rendering used inside frames."""
    return dt.astimezone(timezone.utc).isoformat()


def _require_index(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid()
    if not 0 < value <= _INT64_MAX:
        _invalid()
    return value


def _require_digest(value: object) -> bytes:
    if not isinstance(value, bytes) or len(value) != _DIGEST_SIZE:
        _invalid()
    return value


def _cause_sort_key(cause: Cause) -> tuple[str, str]:
    return (cause.code, str(cause.id))


def _canonical_causes(causes: object) -> tuple[Cause, ...]:
    if not isinstance(causes, tuple) or any(not isinstance(c, Cause) for c in causes):
        _invalid()
    if len(causes) > _MAX_CAUSES:
        _invalid()
    keys = [_cause_sort_key(c) for c in causes]
    if len(set(keys)) != len(keys) or len({str(c.id) for c in causes}) != len(causes):
        _invalid()
    return tuple(sorted(causes, key=_cause_sort_key))


@dataclass(frozen=True, slots=True)
class Cause:
    """One effective suspension cause: closed code plus opaque cause id."""

    code: str
    id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or self.code not in _CAUSE_CODES:
            _invalid()
        if not isinstance(self.id, UUID):
            _invalid()


@dataclass(frozen=True, slots=True)
class RequestFields:
    """Typed fields for ``requested`` / ``reset_requested``."""

    prior_status: str
    prior_reason: str | None
    causes: tuple[Cause, ...]
    generation: int
    drain_deadline: datetime
    revocation_deadline: datetime
    billing_deadline: datetime
    no_grace: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.prior_status, str) or self.prior_status not in _PRIOR_STATUSES:
            _invalid()
        if self.prior_reason is not None and (
            not isinstance(self.prior_reason, str) or self.prior_reason not in _CAUSE_CODES
        ):
            _invalid()
        if self.prior_status == "active" and self.prior_reason is not None:
            _invalid()
        object.__setattr__(self, "causes", _canonical_causes(self.causes))
        _require_index(self.generation)
        object.__setattr__(self, "drain_deadline", _require_aware(self.drain_deadline))
        object.__setattr__(self, "revocation_deadline", _require_aware(self.revocation_deadline))
        object.__setattr__(self, "billing_deadline", _require_aware(self.billing_deadline))
        if not isinstance(self.no_grace, bool):
            _invalid()
        if self.reason is not None and (
            not isinstance(self.reason, str) or self.reason not in _REQUEST_REASONS
        ):
            _invalid()
        if self.no_grace and self.reason is None:
            _invalid()  # zero-grace deletion must preserve its legal/abuse reason


@dataclass(frozen=True, slots=True)
class ReadyFields:
    """Typed fields for ``grace_started`` / ``reset_report_ready``; 7-day window."""

    started_at: datetime
    deadline: datetime

    def __post_init__(self) -> None:
        started = _require_aware(self.started_at)
        deadline = _require_aware(self.deadline)
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "deadline", deadline)
        if deadline - started != _GRACE_WINDOW:
            _invalid()


@dataclass(frozen=True, slots=True)
class AckFields:
    """Typed fields for ``reset_acknowledged``."""

    acknowledged_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "acknowledged_at", _require_aware(self.acknowledged_at))


@dataclass(frozen=True, slots=True)
class TerminalFields:
    """Typed fields for ``restored`` / ``purged`` / ``reset_purged`` / ``reset_cancelled``."""

    causes: tuple[Cause, ...]
    generation: int
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "causes", _canonical_causes(self.causes))
        _require_index(self.generation)
        if self.reason is not None and (
            not isinstance(self.reason, str) or self.reason not in _RESET_CANCEL_REASONS
        ):
            _invalid()


@dataclass(frozen=True, slots=True)
class Command:
    """Immutable journal command; datetimes are normalized to UTC."""

    account_id: UUID
    operation_id: UUID
    event_type: str
    decision_at: datetime
    fields: RequestFields | ReadyFields | AckFields | TerminalFields

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, UUID) or not isinstance(self.operation_id, UUID):
            _invalid()
        if not isinstance(self.event_type, str) or self.event_type not in _EVENT_TYPES:
            _invalid()
        kind = _FIELDS_KIND[self.event_type]
        if not isinstance(self.fields, (RequestFields, ReadyFields, AckFields, TerminalFields)):
            _invalid()
        if kind == "request":
            if not isinstance(self.fields, RequestFields):
                _invalid()
            if self.event_type == _RESET_REQUESTED and (
                self.fields.reason is not None or self.fields.no_grace
            ):
                _invalid()  # zero-grace and request reason codes are deletion-only
        elif kind == "ready":
            if not isinstance(self.fields, ReadyFields):
                _invalid()
        elif kind == "ack":
            if not isinstance(self.fields, AckFields):
                _invalid()
        else:
            if not isinstance(self.fields, TerminalFields):
                _invalid()
            if self.event_type == _RESET_CANCELLED:
                if self.fields.reason is None:
                    _invalid()  # cancellation needs owner/timeout
            elif self.fields.reason is not None:
                _invalid()  # other terminals carry no reason
        object.__setattr__(self, "decision_at", _require_aware(self.decision_at))


@dataclass(frozen=True, slots=True)
class Entry:
    """Immutable journal envelope; ``digest`` is the supplied frame footer."""

    journal_id: UUID
    seq: int
    event_id: UUID
    command: Command
    account_prev: int | None
    operation_prev: int | None
    previous_digest: bytes
    command_digest: bytes
    digest: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.journal_id, UUID) or not isinstance(self.event_id, UUID):
            _invalid()
        if not isinstance(self.command, Command):
            _invalid()
        _require_index(self.seq)
        for prev in (self.account_prev, self.operation_prev):
            if prev is not None:
                _require_index(prev)
        _require_digest(self.previous_digest)
        _require_digest(self.command_digest)
        _require_digest(self.digest)


def command_digest(command: Command) -> bytes:
    """SHA-256 over the canonical caller-argument digest payload.

    Covers account, operation, action and typed caller arguments
    (``prior_status``/``prior_reason``/``causes``/``no_grace``/``reason``
    where applicable). Excludes journal-assigned values: decision time,
    deadlines, generation and predecessor/sequence allocations, so retries
    return the original frame rather than newly generated deadlines.
    """
    if not isinstance(command, Command):
        _invalid()
    fields = command.fields
    digest_fields: dict[str, Any]
    if isinstance(fields, (ReadyFields, AckFields)):
        digest_fields = {}
    elif isinstance(fields, RequestFields):
        digest_fields = {
            "prior_status": fields.prior_status,
            "prior_reason": fields.prior_reason,
            "causes": [[cause.code, str(cause.id)] for cause in fields.causes],
            "no_grace": fields.no_grace,
            "reason": fields.reason,
        }
    elif isinstance(fields, TerminalFields):
        digest_fields = {
            "causes": [[cause.code, str(cause.id)] for cause in fields.causes],
            "reason": fields.reason,
        }
    else:
        _invalid()
    payload = {
        "account_id": str(command.account_id),
        "operation_id": str(command.operation_id),
        "event_type": command.event_type,
        "fields": digest_fields,
    }
    return hashlib.sha256(_canonical_json(payload)).digest()


def _fields_payload(
    fields: RequestFields | ReadyFields | AckFields | TerminalFields,
) -> dict[str, Any]:
    if isinstance(fields, RequestFields):
        return {
            "kind": "request",
            "prior_status": fields.prior_status,
            "prior_reason": fields.prior_reason,
            "causes": [{"code": cause.code, "id": str(cause.id)} for cause in fields.causes],
            "generation": fields.generation,
            "drain_deadline": _iso(fields.drain_deadline),
            "revocation_deadline": _iso(fields.revocation_deadline),
            "billing_deadline": _iso(fields.billing_deadline),
            "no_grace": fields.no_grace,
            "reason": fields.reason,
        }
    if isinstance(fields, ReadyFields):
        return {
            "kind": "ready",
            "started_at": _iso(fields.started_at),
            "deadline": _iso(fields.deadline),
        }
    if isinstance(fields, AckFields):
        return {"kind": "ack", "acknowledged_at": _iso(fields.acknowledged_at)}
    if isinstance(fields, TerminalFields):
        return {
            "kind": "terminal",
            "causes": [{"code": cause.code, "id": str(cause.id)} for cause in fields.causes],
            "generation": fields.generation,
            "reason": fields.reason,
        }
    _invalid()


def encode_entry(entry: Entry) -> bytes:
    """Canonical v1 frame payload for the envelope, without the digest footer.

    The store computes the frame digest as ``SHA256(length-prefix + payload)``
    and appends the footer; this function never serializes ``entry.digest``.
    """
    if not isinstance(entry, Entry):
        _invalid()
    command = entry.command
    payload: dict[str, Any] = {
        "version": _VERSION,
        "journal_id": str(entry.journal_id),
        "seq": entry.seq,
        "event_id": str(entry.event_id),
        "account_prev": entry.account_prev,
        "operation_prev": entry.operation_prev,
        "previous_digest": entry.previous_digest.hex(),
        "command_digest": entry.command_digest.hex(),
        "command": {
            "account_id": str(command.account_id),
            "operation_id": str(command.operation_id),
            "event_type": command.event_type,
            "decision_at": _iso(command.decision_at),
            "fields": _fields_payload(command.fields),
        },
    }
    return _canonical_json(payload)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _reject_float(_token: str) -> NoReturn:
    _invalid()


def _reject_constant(_token: str) -> NoReturn:
    _invalid()


def _parse_str_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        _invalid()
    try:
        parsed = UUID(value)
    except ValueError:
        _invalid()
    if str(parsed) != value:
        _invalid()  # canonical lowercase representation required
    return parsed


def _parse_hex_digest(value: object) -> bytes:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        _invalid()
    return bytes.fromhex(value)


def _parse_canonical_utc(value: object) -> datetime:
    if not isinstance(value, str):
        _invalid()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _invalid()
    if parsed.tzinfo is None or parsed.utcoffset() != _UTC_OFFSET:
        _invalid()
    if parsed.astimezone(timezone.utc).isoformat() != value:
        _invalid()  # canonical rendering required (rejects "Z", sparse fracs, spaces)
    return parsed


def _parse_index_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid()
    if not 0 < value <= _INT64_MAX:
        _invalid()
    return value


def _parse_bool(value: object) -> bool:
    if not isinstance(value, bool):
        _invalid()
    return value


def _parse_fixed_int(value: object, expected: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        _invalid()
    return value


def _parse_enum(value: object, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        _invalid()
    return value


def _parse_closed_str(value: object, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    return _parse_enum(value, allowed)


def _parse_causes(value: object) -> tuple[Cause, ...]:
    if not isinstance(value, list) or len(value) > _MAX_CAUSES:
        _invalid()
    causes: list[Cause] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"code", "id"}:
            _invalid()
        code = _parse_enum(item["code"], _CAUSE_CODES)
        causes.append(Cause(code, _parse_str_uuid(item["id"])))
    keys = [(cause.code, str(cause.id)) for cause in causes]
    if keys != sorted(keys) or len({str(cause.id) for cause in causes}) != len(causes):
        _invalid()  # canonical ordering and unique cause ids required
    return tuple(causes)


def _deny_extra_keys(obj: dict[str, Any], expected: set[str]) -> None:
    if set(obj) != expected:
        _invalid()


def _decode_fields(
    event_type: str, fields_obj: dict[str, Any]
) -> RequestFields | ReadyFields | AckFields | TerminalFields:
    kind = _FIELDS_KIND[event_type]
    present_kind = fields_obj.get("kind")
    if not isinstance(present_kind, str) or present_kind != kind:
        _invalid()
    if kind == "request":
        _deny_extra_keys(
            fields_obj,
            {
                "kind",
                "prior_status",
                "prior_reason",
                "causes",
                "generation",
                "drain_deadline",
                "revocation_deadline",
                "billing_deadline",
                "no_grace",
                "reason",
            },
        )
        reason = _parse_closed_str(fields_obj["reason"], _REQUEST_REASONS)
        if event_type == _RESET_REQUESTED and reason is not None:
            _invalid()  # request reason codes are deletion-only
        return RequestFields(
            prior_status=_parse_enum(fields_obj["prior_status"], _PRIOR_STATUSES),
            prior_reason=_parse_closed_str(fields_obj["prior_reason"], _CAUSE_CODES),
            causes=_parse_causes(fields_obj["causes"]),
            generation=_parse_index_value(fields_obj["generation"]),
            drain_deadline=_parse_canonical_utc(fields_obj["drain_deadline"]),
            revocation_deadline=_parse_canonical_utc(fields_obj["revocation_deadline"]),
            billing_deadline=_parse_canonical_utc(fields_obj["billing_deadline"]),
            no_grace=_parse_bool(fields_obj["no_grace"]),
            reason=reason,
        )
    if kind == "ready":
        _deny_extra_keys(fields_obj, {"kind", "started_at", "deadline"})
        return ReadyFields(
            started_at=_parse_canonical_utc(fields_obj["started_at"]),
            deadline=_parse_canonical_utc(fields_obj["deadline"]),
        )
    if kind == "ack":
        _deny_extra_keys(fields_obj, {"kind", "acknowledged_at"})
        return AckFields(acknowledged_at=_parse_canonical_utc(fields_obj["acknowledged_at"]))
    _deny_extra_keys(fields_obj, {"kind", "causes", "generation", "reason"})
    reason = _parse_closed_str(fields_obj["reason"], _RESET_CANCEL_REASONS)
    if event_type == _RESET_CANCELLED:
        if reason is None:
            _invalid()  # cancellation needs owner/timeout
    elif reason is not None:
        _invalid()  # other terminals carry no reason
    return TerminalFields(
        causes=_parse_causes(fields_obj["causes"]),
        generation=_parse_index_value(fields_obj["generation"]),
        reason=reason,
    )


def decode_entry(payload: bytes, digest: bytes) -> Entry:
    """Strict canonical roundtrip parser for one frame payload.

    ``digest`` is the supplied frame footer (the store derives it from
    ``SHA256(length-prefix + payload)``) and is stored as ``entry.digest``.
    Rejects duplicate keys, unsupported versions/events/kinds, extra or
    missing fields, floats, bools-as-ints, unbounded integers, non-canonical
    UUIDs, non-UTC or non-canonically rendered timestamps and recomputed
    command-digest mismatches.
    """
    _require_digest(digest)
    if not isinstance(payload, bytes):
        _invalid()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        _invalid()
    try:
        obj: Any = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
        if not isinstance(obj, dict) or _canonical_json(obj) != payload:
            _invalid()  # canonical strictness: no whitespace, escapes or reordering
    except (ValueError, UnicodeError, RecursionError):
        # Includes the interpreter's oversized-integer limit and escaped lone
        # surrogates; neither may escape as payload-bearing diagnostics.
        _invalid()
    _deny_extra_keys(
        obj,
        {
            "version",
            "journal_id",
            "seq",
            "event_id",
            "account_prev",
            "operation_prev",
            "previous_digest",
            "command_digest",
            "command",
        },
    )
    _parse_fixed_int(obj["version"], _VERSION)
    journal_id = _parse_str_uuid(obj["journal_id"])
    seq = _parse_index_value(obj["seq"])
    event_id = _parse_str_uuid(obj["event_id"])
    account_prev = _parse_optional_index(obj["account_prev"])
    operation_prev = _parse_optional_index(obj["operation_prev"])
    previous_digest = _parse_hex_digest(obj["previous_digest"])
    recorded_digest = _parse_hex_digest(obj["command_digest"])
    raw_command = obj["command"]
    if not isinstance(raw_command, dict):
        _invalid()
    _deny_extra_keys(
        raw_command,
        {"account_id", "operation_id", "event_type", "decision_at", "fields"},
    )
    account_id = _parse_str_uuid(raw_command["account_id"])
    operation_id = _parse_str_uuid(raw_command["operation_id"])
    event_type = _parse_enum(raw_command["event_type"], _EVENT_TYPES)
    decision_at = _parse_canonical_utc(raw_command["decision_at"])
    fields_obj = raw_command["fields"]
    if not isinstance(fields_obj, dict):
        _invalid()
    fields = _decode_fields(event_type, fields_obj)
    command = Command(
        account_id=account_id,
        operation_id=operation_id,
        event_type=event_type,
        decision_at=decision_at,
        fields=fields,
    )
    if command_digest(command) != recorded_digest:
        _invalid()
    return Entry(
        journal_id=journal_id,
        seq=seq,
        event_id=event_id,
        command=command,
        account_prev=account_prev,
        operation_prev=operation_prev,
        previous_digest=previous_digest,
        command_digest=recorded_digest,
        digest=digest,
    )


def _parse_optional_index(value: object) -> int | None:
    if value is None:
        return None
    return _parse_index_value(value)


@dataclass(slots=True)
class _AccountState:
    open_operation: UUID | None = None
    purged_final: bool = False
    max_generation: int = 0

    def note_generation(self, generation: int | None) -> None:
        if generation is not None and generation > self.max_generation:
            self.max_generation = generation


_EPOCH_SENTINEL = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(slots=True)
class _OperationState:
    kind: str
    phase: str
    request_event: str
    request_generation: int
    request_causes: tuple[Cause, ...]
    no_grace: bool
    grace_deadline: datetime | None = None
    report_deadline: datetime | None = None
    last_decision: datetime = _EPOCH_SENTINEL

    def note_decision(self, decision_at: datetime) -> None:
        self.last_decision = decision_at


class JournalState:
    """Mutable replay structural validator for the journal chain.

    Maintains account/operation CAS heads, the global chain digest and the
    per-operation lifecycle needed for the closed §3 transition grammar. It
    proves structure only; concrete handlers prove external effects.
    """

    def __init__(self) -> None:
        self.account_heads: dict[UUID, int] = {}
        self.operation_heads: dict[UUID, int] = {}
        self.events: dict[UUID, Entry] = {}
        self.entries: list[Entry] = []
        self._head_digest: bytes = _ZERO_DIGEST
        self._accounts: dict[UUID, _AccountState] = {}
        self._operations: dict[UUID, _OperationState] = {}
        self._operation_accounts: dict[UUID, UUID] = {}

    @property
    def head_digest(self) -> bytes:
        """Current global chain digest: initial zero bytes, then last frame."""
        return self._head_digest

    def accept(self, entry: Entry) -> None:
        """Validate and accept one entry at the next contiguous sequence."""
        if entry.seq != len(self.entries) + 1:
            _invalid()  # contiguous global sequence starting at 1
        if entry.previous_digest != self._head_digest:
            _invalid()  # previous global digest link (initial zeros)
        if entry.event_id in self.events:
            _invalid()  # event UUID uniqueness within the stream
        if command_digest(entry.command) != entry.command_digest:
            _invalid()  # recomputed caller-argument digest must match

        command = entry.command
        account_id = command.account_id
        operation_id = command.operation_id
        account = self._accounts.setdefault(account_id, _AccountState())
        if account.purged_final:
            _invalid()  # purged accounts never receive further entries

        bound_account = self._operation_accounts.get(operation_id)
        if bound_account is not None and bound_account != account_id:
            _conflict()  # an operation stays bound to its original account
        if entry.account_prev != self.account_heads.get(account_id):
            _conflict()  # both-head CAS: stale account predecessor
        if entry.operation_prev != self.operation_heads.get(operation_id):
            _conflict()  # both-head CAS: stale operation predecessor

        op_state = self._operations.get(operation_id)
        self._validate_transition(entry, op_state, account)
        self._commit(entry, op_state, account, account_id, operation_id)

    def _validate_transition(
        self,
        entry: Entry,
        op_state: _OperationState | None,
        account: _AccountState,
    ) -> None:
        command = entry.command
        event_type = command.event_type
        fields = command.fields
        decision_at = command.decision_at

        if event_type in (_REQUESTED, _RESET_REQUESTED):
            if not isinstance(fields, RequestFields):
                _invalid()
            if op_state is not None:
                _invalid()  # an operation identity never reopens
            if account.open_operation is not None:
                _invalid()  # at most one open deletion/reset per account
            if fields.generation <= account.max_generation:
                _invalid()  # fence generation strictly above account max
            own_code = _OWN_CAUSE_CODE[event_type]
            if not any(
                cause.code == own_code and cause.id == command.operation_id
                for cause in fields.causes
            ):
                # The complete effective set must include this operation's
                # suspension fence. Cause UUID uniqueness already proves once.
                _invalid()
            return

        if op_state is None:
            _invalid()
        if _OP_FAMILY[event_type] != op_state.kind:
            _invalid()  # deletion and reset events never mix within one op
        if decision_at < op_state.last_decision:
            _invalid()  # per-operation times are nondecreasing

        if event_type == _GRACE_STARTED:
            if not isinstance(fields, ReadyFields):
                _invalid()
            if op_state.phase != "requested":
                _invalid()
            if op_state.no_grace:
                _invalid()  # zero-grace requests never start grace
            if fields.started_at != decision_at:
                _invalid()  # ready decision equals started_at
            if fields.deadline != fields.started_at + _GRACE_WINDOW:
                _invalid()
            return

        if event_type == _RESET_REPORT_READY:
            if not isinstance(fields, ReadyFields):
                _invalid()
            if op_state.phase != "requested":
                _invalid()
            if fields.started_at != decision_at:
                _invalid()
            if fields.deadline != fields.started_at + _GRACE_WINDOW:
                _invalid()
            return

        if event_type == _RESET_ACKNOWLEDGED:
            if not isinstance(fields, AckFields):
                _invalid()
            if op_state.phase != "report_ready":
                _invalid()
            report_deadline = op_state.report_deadline
            assert report_deadline is not None
            if fields.acknowledged_at != decision_at:
                _invalid()
            if decision_at >= report_deadline:
                _invalid()  # acknowledgement only strictly before deadline
            return

        if event_type == _RESET_CANCELLED:
            if not isinstance(fields, TerminalFields):
                _invalid()
            if op_state.phase != "report_ready":
                _invalid()  # cancel only while awaiting acknowledgement
            report_deadline = op_state.report_deadline
            assert report_deadline is not None
            if fields.reason == "timeout" and decision_at < report_deadline:
                _invalid()
            if fields.reason == "owner" and decision_at >= report_deadline:
                _invalid()
            if fields.generation <= account.max_generation:
                _invalid()
            _validate_terminal_causes(fields, op_state, command.operation_id, event_type)
            return

        if event_type == _RESTORED:
            if not isinstance(fields, TerminalFields):
                _invalid()
            if op_state.phase != "grace_started":
                _invalid()
            grace_deadline = op_state.grace_deadline
            assert grace_deadline is not None
            if decision_at >= grace_deadline:
                _invalid()  # restore only strictly before grace deadline
            if fields.generation <= account.max_generation:
                _invalid()
            _validate_terminal_causes(fields, op_state, command.operation_id, event_type)
            return

        if event_type == _PURGED:
            if not isinstance(fields, TerminalFields):
                _invalid()
            if op_state.kind != "deletion":
                _invalid()
            if op_state.phase == "requested":
                if not op_state.no_grace:
                    _invalid()  # non-zero-grace deletion needs grace first
            elif op_state.phase == "grace_started":
                grace_deadline = op_state.grace_deadline
                assert grace_deadline is not None
                if decision_at < grace_deadline:
                    _invalid()  # purge only at/after grace deadline
            else:
                _invalid()
            if fields.generation != op_state.request_generation:
                _invalid()  # purge retains the original fence generation
            _validate_terminal_causes(fields, op_state, command.operation_id, event_type)
            return

        if event_type == _RESET_PURGED:
            if not isinstance(fields, TerminalFields):
                _invalid()
            if op_state.phase != "acknowledged":
                _invalid()
            if fields.generation != op_state.request_generation:
                _invalid()  # reset purge retains the original fence generation
            _validate_terminal_causes(fields, op_state, command.operation_id, event_type)
            return

        _invalid()

    def _commit(
        self,
        entry: Entry,
        op_state: _OperationState | None,
        account: _AccountState,
        account_id: UUID,
        operation_id: UUID,
    ) -> None:
        command = entry.command
        event_type = command.event_type
        fields = command.fields

        self.entries.append(entry)
        self.events[entry.event_id] = entry
        self._head_digest = entry.digest
        self.account_heads[account_id] = entry.seq
        self.operation_heads[operation_id] = entry.seq
        self._operation_accounts[operation_id] = account_id

        if event_type in (_REQUESTED, _RESET_REQUESTED):
            if not isinstance(fields, RequestFields):
                _invalid()
            self._operations[operation_id] = _OperationState(
                kind=_OP_FAMILY[event_type],
                phase="requested",
                request_event=event_type,
                request_generation=fields.generation,
                request_causes=fields.causes,
                no_grace=fields.no_grace,
                last_decision=command.decision_at,
            )
            account.open_operation = operation_id
            account.note_generation(fields.generation)
            return

        assert op_state is not None
        op_state.note_decision(command.decision_at)
        if event_type == _GRACE_STARTED:
            if not isinstance(fields, ReadyFields):
                _invalid()
            op_state.phase = "grace_started"
            op_state.grace_deadline = fields.deadline
        elif event_type == _RESET_REPORT_READY:
            if not isinstance(fields, ReadyFields):
                _invalid()
            op_state.phase = "report_ready"
            op_state.report_deadline = fields.deadline
        elif event_type == _RESET_ACKNOWLEDGED:
            op_state.phase = "acknowledged"
        else:
            if not isinstance(fields, TerminalFields):
                _invalid()
            op_state.phase = "closed"
            account.open_operation = None
            if event_type == _PURGED:
                account.purged_final = True
            account.note_generation(fields.generation)


def _validate_terminal_causes(
    terminal: TerminalFields,
    op_state: _OperationState,
    operation_id: UUID,
    event_type: str,
) -> None:
    """Structural cause-set rules for terminal transitions.

    Terminal causes cannot invent causes. For a transition that closes an
    operation without destroying the record (restore/cancel/reset-purge) the
    operation's own cause — reliably identified as the request-kind cause code
    bound to this operation id — must not survive the transition.
    """
    request_pairs = {(cause.code, str(cause.id)) for cause in op_state.request_causes}
    own_code = _OWN_CAUSE_CODE[op_state.request_event]
    if event_type in (_RESTORED, _RESET_CANCELLED, _RESET_PURGED):
        expected = request_pairs - {(own_code, str(operation_id))}
        if {(cause.code, str(cause.id)) for cause in terminal.causes} != expected:
            # Suspension changes are unsupported in J1a. Closing this operation
            # cannot silently erase another cause captured in its request.
            _invalid()
    for cause in terminal.causes:
        pair = (cause.code, str(cause.id))
        if pair not in request_pairs:
            _invalid()
        if (
            event_type in (_RESTORED, _RESET_CANCELLED, _RESET_PURGED)
            and cause.code == own_code
            and str(cause.id) == str(operation_id)
        ):
            _invalid()
