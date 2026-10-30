"""Closed H1 metadata and structural replay; no execution, keys or runtime I/O.

Wire H1 is the owner-approved prototype format, separate from J1. Checksums
protect integrity under cooperating writers; they do not authenticate actors.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import struct
from dataclasses import asdict, dataclass
from typing import Any, Literal, Sequence, cast
from uuid import UUID

MAX_PAYLOAD = 8192
MAX_LOG = 16 * 1024 * 1024
MAX_RECORDS = 16384
MAX_CANONICAL = 64 * 1024 * 1024
ZERO = bytes(32)
DOMAIN = b"daemon.restore-history.frame.v1\n"


class HistoryError(RuntimeError):
    """Safe diagnostics only. Unknown append is never a retry/dispatch permit."""

    def __init__(self, code: str, *, uncertain: bool = False) -> None:
        self.code = code
        self.uncertain = uncertain
        super().__init__(code)


def canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise HistoryError("invalid_record") from None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise HistoryError("invalid_record")
        result[key] = value
    return result


def parse(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data, object_pairs_hook=_pairs)
        if not isinstance(value, dict) or canonical(value) != data:
            raise HistoryError("invalid_record")
        return cast(dict[str, Any], value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HistoryError("invalid_record") from None


def _keys(value: Any, names: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(names.split()):
        raise HistoryError("invalid_record")
    return value


def _uuid(value: Any) -> UUID:
    try:
        result = UUID(value) if type(value) is str else None
        if result is None or not result.int or str(result) != value:
            raise HistoryError("invalid_record")
        return result
    except (ValueError, TypeError, AttributeError):
        raise HistoryError("invalid_record") from None


def _number(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise HistoryError("invalid_record")
    return value


def _hex(value: Any, size: int = 64) -> str:
    if type(value) is not str or re.fullmatch("[0-9a-f]{" + str(size) + "}", value) is None:
        raise HistoryError("invalid_record")
    return value


def _literal(value: Any, choices: tuple[str, ...]) -> Any:
    if type(value) is not str or value not in choices:
        raise HistoryError("invalid_record")
    return value


def _one(value: Any) -> int:
    if type(value) is not int or value != 1:
        raise HistoryError("invalid_record")
    return 1


@dataclass(frozen=True, slots=True)
class Cut:
    seq: int
    digest: str


def _cut(value: Any) -> Cut:
    obj = _keys(value, "seq digest")
    result = Cut(_number(obj["seq"]), _hex(obj["digest"]))
    if (result.seq == 0) != (result.digest == ZERO.hex()):
        raise HistoryError("invalid_record")
    return result


@dataclass(frozen=True, slots=True)
class AcceptPrepare:
    account_id: UUID
    account_generation: int
    task_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    result_message_id: UUID
    commitment_version: int
    key_id: int
    key_commitment: str
    request_commitment: str
    request_schema: Literal["chat-canonical-3715-v1"]


@dataclass(frozen=True, slots=True)
class SendPossible:
    account_id: UUID
    account_generation: int
    task_id: UUID
    epoch: int
    lifecycle_id: UUID
    financial_scope_id: UUID
    metadata_scope_id: UUID
    producer_instance_id: UUID
    reservation_id: UUID
    runtime_identity: UUID
    dispatch_nonce: UUID
    effect_kind: Literal["completion", "embedding", "web_search"]


@dataclass(frozen=True, slots=True)
class StopIntent:
    account_id: UUID
    account_generation: int
    task_id: UUID
    reason: Literal["owner"]


@dataclass(frozen=True, slots=True)
class RestoreBegin:
    restore_id: UUID
    backup_sha256: str
    target_binding_sha256: str
    expected_schema_sha256: str
    source_tree: str
    h_cut: Cut
    j1_id: UUID
    j1_cut: Cut


@dataclass(frozen=True, slots=True)
class RestoreAssessed:
    restore_id: UUID
    begin_record_id: UUID
    checked_through: Cut
    report_sha256: str
    disposition: Literal["remain_blocked", "qualified_to_recheck"]


RecordData = AcceptPrepare | SendPossible | StopIntent | RestoreBegin | RestoreAssessed
_KINDS = {
    AcceptPrepare: ("ACCEPT_PREPARE", "backend"),
    SendPossible: ("SEND_POSSIBLE", "worker"),
    StopIntent: ("STOP_INTENT", "backend"),
    RestoreBegin: ("RESTORE_BEGIN", "operator"),
    RestoreAssessed: ("RESTORE_ASSESSED", "operator"),
}


def _data(kind: str, raw: Any) -> RecordData:
    if kind == "ACCEPT_PREPARE":
        d = _keys(
            raw,
            "account_id account_generation task_id conversation_id user_message_id result_message_id commitment_version key_id key_commitment request_commitment request_schema",
        )
        return AcceptPrepare(
            _uuid(d["account_id"]),
            _number(d["account_generation"]),
            _uuid(d["task_id"]),
            _uuid(d["conversation_id"]),
            _uuid(d["user_message_id"]),
            _uuid(d["result_message_id"]),
            _one(d["commitment_version"]),
            _one(d["key_id"]),
            _hex(d["key_commitment"]),
            _hex(d["request_commitment"]),
            _literal(d["request_schema"], ("chat-canonical-3715-v1",)),
        )
    if kind == "SEND_POSSIBLE":
        d = _keys(
            raw,
            "account_id account_generation task_id epoch lifecycle_id financial_scope_id metadata_scope_id producer_instance_id reservation_id runtime_identity dispatch_nonce effect_kind",
        )
        return SendPossible(
            _uuid(d["account_id"]),
            _number(d["account_generation"]),
            _uuid(d["task_id"]),
            _number(d["epoch"], 1),
            _uuid(d["lifecycle_id"]),
            _uuid(d["financial_scope_id"]),
            _uuid(d["metadata_scope_id"]),
            _uuid(d["producer_instance_id"]),
            _uuid(d["reservation_id"]),
            _uuid(d["runtime_identity"]),
            _uuid(d["dispatch_nonce"]),
            _literal(d["effect_kind"], ("completion", "embedding", "web_search")),
        )
    if kind == "STOP_INTENT":
        d = _keys(raw, "account_id account_generation task_id reason")
        return StopIntent(
            _uuid(d["account_id"]),
            _number(d["account_generation"]),
            _uuid(d["task_id"]),
            _literal(d["reason"], ("owner",)),
        )
    if kind == "RESTORE_BEGIN":
        d = _keys(
            raw,
            "restore_id backup_sha256 target_binding_sha256 expected_schema_sha256 source_tree h_cut j1_cut",
        )
        j = _keys(d["j1_cut"], "journal_id seq digest")
        return RestoreBegin(
            _uuid(d["restore_id"]),
            _hex(d["backup_sha256"]),
            _hex(d["target_binding_sha256"]),
            _hex(d["expected_schema_sha256"]),
            _hex(d["source_tree"], 40),
            _cut(d["h_cut"]),
            _uuid(j["journal_id"]),
            _cut({"seq": j["seq"], "digest": j["digest"]}),
        )
    if kind == "RESTORE_ASSESSED":
        d = _keys(raw, "restore_id begin_record_id checked_through report_sha256 disposition")
        return RestoreAssessed(
            _uuid(d["restore_id"]),
            _uuid(d["begin_record_id"]),
            _cut(d["checked_through"]),
            _hex(d["report_sha256"]),
            _literal(d["disposition"], ("remain_blocked", "qualified_to_recheck")),
        )
    raise HistoryError("invalid_record")


def data_wire(data: RecordData) -> dict[str, Any]:
    def uuid_only(value: Any) -> str:
        if not isinstance(value, UUID):
            raise HistoryError("invalid_record")
        return str(value)

    try:
        d = json.loads(json.dumps(asdict(data), default=uuid_only))
        if isinstance(data, RestoreBegin):
            d["j1_cut"]["journal_id"] = d.pop("j1_id")
        kind = _KINDS[type(data)][0]
        _data(kind, d)
        return d
    except (KeyError, TypeError, ValueError, RecursionError):
        raise HistoryError("invalid_record") from None


@dataclass(frozen=True, slots=True)
class Command:
    record_id: UUID
    data: RecordData


@dataclass(frozen=True, slots=True)
class Entry:
    history_id: UUID
    seq: int
    prev_digest: bytes
    command: Command
    digest: bytes


def encode_entry(history_id: UUID, seq: int, previous: bytes, command: Command) -> bytes:
    try:
        kind, actor = _KINDS[type(command.data)]
        payload = canonical(
            {
                "v": 1,
                "history_id": str(history_id),
                "seq": seq,
                "prev_digest": previous.hex(),
                "record_id": str(command.record_id),
                "kind": kind,
                "actor": actor,
                "data": data_wire(command.data),
            }
        )
        if not 1 <= len(payload) <= MAX_PAYLOAD:
            raise HistoryError("invalid_record")
        raw = struct.pack(">I", len(payload)) + payload
        frame = raw + hashlib.sha256(DOMAIN + raw).digest()
        decode_entry(frame)
        return frame
    except (AttributeError, KeyError, TypeError, ValueError):
        raise HistoryError("invalid_record") from None


def decode_entry(frame: bytes) -> Entry:
    if not isinstance(frame, bytes) or len(frame) < 36:
        raise HistoryError("invalid_record")
    size = struct.unpack(">I", frame[:4])[0]
    if (
        not 1 <= size <= MAX_PAYLOAD
        or len(frame) != size + 36
        or not hmac.compare_digest(hashlib.sha256(DOMAIN + frame[:-32]).digest(), frame[-32:])
    ):
        raise HistoryError("invalid_record")
    d = _keys(parse(frame[4:-32]), "v history_id seq prev_digest record_id kind actor data")
    _one(d["v"])
    data = _data(d["kind"], d["data"])
    if d["actor"] != _KINDS[type(data)][1]:
        raise HistoryError("invalid_record")
    return Entry(
        _uuid(d["history_id"]),
        _number(d["seq"], 1),
        bytes.fromhex(_hex(d["prev_digest"])),
        Command(_uuid(d["record_id"]), data),
        frame[-32:],
    )


@dataclass(frozen=True, slots=True)
class HistoryState:
    entries: tuple[Entry, ...]
    open_restore: UUID | None

    @property
    def digest(self) -> bytes:
        return self.entries[-1].digest if self.entries else ZERO


def validate_chain(history_id: UUID, entries: Sequence[Entry]) -> HistoryState:
    if not isinstance(history_id, UUID) or not history_id.int:
        raise HistoryError("invalid_record")
    previous = ZERO
    stopped: set[UUID] = set()
    restored: set[UUID] = set()
    operations: set[UUID] = set()
    accepted: dict[UUID, AcceptPrepare] = {}
    keys: set[tuple[UUID, int, str]] = set()
    sent: set[tuple[UUID, UUID, UUID]] = set()
    open_restore: tuple[UUID, UUID] | None = None
    if len(entries) > MAX_RECORDS:
        raise HistoryError("capacity")
    for seq, e in enumerate(entries, 1):
        # Reject forged in-memory digest/structure just as strictly as bytes.
        if decode_entry(encode_entry(e.history_id, e.seq, e.prev_digest, e.command)) != e:
            raise HistoryError("invalid_record")
        if (
            e.history_id != history_id
            or e.seq != seq
            or e.prev_digest != previous
            or e.command.record_id in operations
        ):
            raise HistoryError("chain_mismatch")
        operations.add(e.command.record_id)
        d = e.command.data
        if isinstance(d, AcceptPrepare):
            if open_restore is not None:
                raise HistoryError("restore_mismatch")
            key = (d.account_id, d.key_id, d.key_commitment)
            if key in keys or d.task_id in accepted:
                raise HistoryError("conflict")
            keys.add(key)
            accepted[d.task_id] = d
        elif isinstance(d, (SendPossible, StopIntent)):
            a = accepted.get(d.task_id)
            if a is None or (a.account_id, a.account_generation) != (
                d.account_id,
                d.account_generation,
            ):
                raise HistoryError("ancestor_mismatch")
            if isinstance(d, StopIntent):
                stopped.add(d.task_id)
            if isinstance(d, SendPossible):
                if open_restore is not None or d.task_id in stopped:
                    raise HistoryError("effect_stopped")
                binding = (d.reservation_id, d.runtime_identity, d.dispatch_nonce)
                if binding in sent:
                    raise HistoryError("conflict")
                sent.add(binding)
        elif isinstance(d, RestoreBegin):
            if (
                d.restore_id in restored
                or open_restore is not None
                or d.h_cut != Cut(seq - 1, previous.hex())
            ):
                raise HistoryError("restore_mismatch")
            restored.add(d.restore_id)
            open_restore = (d.restore_id, e.command.record_id)
        elif isinstance(d, RestoreAssessed):
            if open_restore != (d.restore_id, d.begin_record_id) or d.checked_through != Cut(
                seq - 1, previous.hex()
            ):
                raise HistoryError("restore_mismatch")
            if d.disposition == "qualified_to_recheck":
                open_restore = None
        previous = e.digest
    return HistoryState(tuple(entries), open_restore[0] if open_restore else None)


def _secret(key: bytes) -> None:
    if type(key) is not bytes or len(key) != 32:
        raise HistoryError("invalid_key")


def key_check(key: bytes, history_id: UUID) -> str:
    _secret(key)
    if not isinstance(history_id, UUID):
        raise HistoryError("invalid_record")
    _uuid(str(history_id))
    return hmac.new(
        key, b"daemon.restore-history.key-check.v1\n" + history_id.bytes, hashlib.sha256
    ).hexdigest()


def commitments(
    key: bytes, account_id: UUID, client_key: str, request_canonical: bytes
) -> tuple[str, str]:
    """Caller supplies pinned canonical bytes; never parse/store request content."""
    _secret(key)
    if not isinstance(account_id, UUID):
        raise HistoryError("invalid_record")
    _uuid(str(account_id))
    if type(client_key) is not str or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", client_key) is None:
        raise HistoryError("invalid_key")
    if type(request_canonical) is not bytes or len(request_canonical) > MAX_CANONICAL:
        raise HistoryError("canonical_limit")
    q = client_key.encode("ascii")
    prefix = account_id.bytes + struct.pack(">Q", len(q)) + q
    lookup = hmac.new(key, b"daemon.restore-history.key.v1\n" + prefix, hashlib.sha256).hexdigest()
    request = hmac.new(
        key,
        b"daemon.restore-history.request.v1\n"
        + prefix
        + struct.pack(">Q", len(request_canonical))
        + request_canonical,
        hashlib.sha256,
    ).hexdigest()
    return lookup, request


@dataclass(frozen=True, slots=True)
class Identity:
    history_id: UUID
    key_check: str

    def wire(self) -> dict[str, Any]:
        if not isinstance(self.history_id, UUID):
            raise HistoryError("invalid_record")
        _uuid(str(self.history_id))
        _hex(self.key_check)
        return {
            "v": 1,
            "history_id": str(self.history_id),
            "key_id": 1,
            "key_check": self.key_check,
        }

    def verify_key(self, key: bytes) -> None:
        self.wire()
        if not hmac.compare_digest(self.key_check, key_check(key, self.history_id)):
            raise HistoryError("key_mismatch")
