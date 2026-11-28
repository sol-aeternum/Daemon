"""Closed mixed-version metadata and replay; no execution, keys or runtime I/O.

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
ARTIFACT_DOMAIN = b"daemon.restore-history.frame.v2\n"
TERMINAL_DOMAIN = b"daemon.restore-history.frame.v3\n"
NONSUCCESS_DOMAIN = b"daemon.restore-history.frame.v4\n"
INBOX_RETIRED_DOMAIN = b"daemon.restore-history.frame.v5\n"
Profile = Literal[
    "h1", "artifact-v2", "terminal-v3", "terminal-v4", "inbox-retired-v1", "inbox-research-v1"
]
RESEARCH_SENDS = ("completion", "web_search", "completion")

# These codes can describe a returned terminal answer, not a retry/Stop policy.
NONSUCCESS_LOCAL_LIMIT_KINDS = {
    "budget_exceeded": "compute_budget",
    "extended_budget_exceeded": "extended_compute_budget",
    "extended_agents_exceeded": "extended_agent_runs",
    "trial_exhausted": "trial_budget",
    "trial_extended_agents_exhausted": "trial_extended_agent_runs",
    "context_limit": "context",
    "tool_price_exceeded": "tool_price",
    "embedding_price_exceeded": "embedding_price",
    "rate_limited": "rate",
    "concurrency_exceeded": "concurrency",
    "deadline_exceeded": "time",
}
NONSUCCESS_FAILURE_REASONS = (
    "provider_length",
    "provider_content_filter",
    "stream_done_with_pending",
    *NONSUCCESS_LOCAL_LIMIT_KINDS,
)
NONSUCCESS_UNKNOWN_REASONS = ("finish_evidence_missing", "finish_evidence_conflict")


def check_profile(profile: Profile) -> Profile:
    if type(profile) is not str or profile not in (
        "h1",
        "artifact-v2",
        "terminal-v3",
        "terminal-v4",
        "inbox-retired-v1",
        "inbox-research-v1",
    ):
        raise HistoryError("invalid_profile")
    return profile


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


@dataclass(frozen=True, slots=True)
class ArtifactPrepare:
    account_id: UUID
    account_generation: int
    task_id: UUID
    acceptance_record_id: UUID
    epoch: int
    lifecycle_id: UUID
    financial_scope_id: UUID
    metadata_scope_id: UUID
    producer_instance_id: UUID
    intent_id: UUID
    resource_id: UUID
    version_id: UUID
    job_id: UUID
    source_operation_id: UUID
    output_slot: int
    original_attempt_id: UUID
    operation_schema: Literal["first-output-csv-catalog-v1"]


@dataclass(frozen=True, slots=True)
class TerminalOutputPrepare:
    account_id: UUID
    account_generation: int
    task_id: UUID
    result_message_id: UUID
    acceptance_record_id: UUID
    epoch: int
    terminal_schema: Literal["native-complete-body-v1"]


@dataclass(frozen=True, slots=True)
class NonsuccessOutputPrepare:
    account_id: UUID
    account_generation: int
    task_id: UUID
    result_message_id: UUID
    acceptance_record_id: UUID
    epoch: int
    terminal_schema: Literal["native-nonsuccess-body-v1"]
    status: Literal["failed", "needs_attention"]
    reason: str


@dataclass(frozen=True, slots=True)
class InboxOriginalRetired:
    terminal_record_id: UUID
    send_record_id: UUID


TERMINAL_KINDS = (TerminalOutputPrepare, NonsuccessOutputPrepare)


RecordData = (
    AcceptPrepare
    | SendPossible
    | StopIntent
    | RestoreBegin
    | RestoreAssessed
    | ArtifactPrepare
    | TerminalOutputPrepare
    | NonsuccessOutputPrepare
    | InboxOriginalRetired
)
_KINDS = {
    AcceptPrepare: ("ACCEPT_PREPARE", "backend"),
    SendPossible: ("SEND_POSSIBLE", "worker"),
    StopIntent: ("STOP_INTENT", "backend"),
    RestoreBegin: ("RESTORE_BEGIN", "operator"),
    RestoreAssessed: ("RESTORE_ASSESSED", "operator"),
    ArtifactPrepare: ("ARTIFACT_PREPARE", "backend"),
    TerminalOutputPrepare: ("TERMINAL_OUTPUT_PREPARE", "worker"),
    NonsuccessOutputPrepare: ("NONSUCCESS_OUTPUT_PREPARE", "worker"),
    InboxOriginalRetired: ("INBOX_ORIGINAL_RETIRED", "worker"),
}


def _data(kind: str, raw: Any) -> RecordData:
    if kind == "INBOX_ORIGINAL_RETIRED":
        d = _keys(raw, "terminal_record_id send_record_id")
        return InboxOriginalRetired(_uuid(d["terminal_record_id"]), _uuid(d["send_record_id"]))
    if kind == "NONSUCCESS_OUTPUT_PREPARE":
        d = _keys(
            raw,
            "account_id account_generation task_id result_message_id acceptance_record_id epoch terminal_schema status reason",
        )
        status = _literal(d["status"], ("failed", "needs_attention"))
        return NonsuccessOutputPrepare(
            _uuid(d["account_id"]),
            _number(d["account_generation"]),
            _uuid(d["task_id"]),
            _uuid(d["result_message_id"]),
            _uuid(d["acceptance_record_id"]),
            _number(d["epoch"], 1),
            _literal(d["terminal_schema"], ("native-nonsuccess-body-v1",)),
            status,
            _literal(
                d["reason"],
                NONSUCCESS_FAILURE_REASONS
                if status == "failed"
                else (*NONSUCCESS_UNKNOWN_REASONS, "uncertain_effect"),
            ),
        )
    if kind == "TERMINAL_OUTPUT_PREPARE":
        d = _keys(
            raw,
            "account_id account_generation task_id result_message_id acceptance_record_id epoch terminal_schema",
        )
        return TerminalOutputPrepare(
            _uuid(d["account_id"]),
            _number(d["account_generation"]),
            _uuid(d["task_id"]),
            _uuid(d["result_message_id"]),
            _uuid(d["acceptance_record_id"]),
            _number(d["epoch"], 1),
            _literal(d["terminal_schema"], ("native-complete-body-v1",)),
        )
    if kind == "ARTIFACT_PREPARE":
        names = "account_id account_generation task_id acceptance_record_id epoch lifecycle_id financial_scope_id metadata_scope_id producer_instance_id intent_id resource_id version_id job_id source_operation_id output_slot original_attempt_id operation_schema"
        d = _keys(raw, names)
        slot = _number(d["output_slot"])
        if slot > 2**31 - 1:
            raise HistoryError("invalid_record")
        return ArtifactPrepare(
            _uuid(d["account_id"]),
            _number(d["account_generation"]),
            _uuid(d["task_id"]),
            _uuid(d["acceptance_record_id"]),
            _number(d["epoch"], 1),
            _uuid(d["lifecycle_id"]),
            _uuid(d["financial_scope_id"]),
            _uuid(d["metadata_scope_id"]),
            _uuid(d["producer_instance_id"]),
            _uuid(d["intent_id"]),
            _uuid(d["resource_id"]),
            _uuid(d["version_id"]),
            _uuid(d["job_id"]),
            _uuid(d["source_operation_id"]),
            slot,
            _uuid(d["original_attempt_id"]),
            _literal(d["operation_schema"], ("first-output-csv-catalog-v1",)),
        )
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


def encode_entry(
    history_id: UUID, seq: int, previous: bytes, command: Command, *, profile: Profile = "h1"
) -> bytes:
    check_profile(profile)
    try:
        version, domain, profiles = _FRAME_VERSIONS.get(type(command.data), (1, DOMAIN, None))
        if profiles is not None and profile not in profiles:
            raise HistoryError("invalid_record")
        kind, actor = _KINDS[type(command.data)]
        payload = canonical(
            {
                "v": version,
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
        frame = raw + hashlib.sha256(domain + raw).digest()
        decode_entry(frame, profile=profile)
        return frame
    except (AttributeError, KeyError, TypeError, ValueError):
        raise HistoryError("invalid_record") from None


_FRAME_VERSIONS: dict[type[Any], tuple[int, bytes, tuple[Profile, ...]]] = {
    ArtifactPrepare: (
        2,
        ARTIFACT_DOMAIN,
        ("artifact-v2", "terminal-v3", "terminal-v4", "inbox-retired-v1"),
    ),
    TerminalOutputPrepare: (
        3,
        TERMINAL_DOMAIN,
        ("terminal-v3", "terminal-v4", "inbox-retired-v1", "inbox-research-v1"),
    ),
    NonsuccessOutputPrepare: (4, NONSUCCESS_DOMAIN, ("terminal-v4",)),
    InboxOriginalRetired: (5, INBOX_RETIRED_DOMAIN, ("inbox-retired-v1",)),
}
_WIRE_VERSIONS = {_KINDS[kind][0]: shape for kind, shape in _FRAME_VERSIONS.items()}


def decode_entry(frame: bytes, *, profile: Profile = "h1") -> Entry:
    check_profile(profile)
    if not isinstance(frame, bytes) or len(frame) < 36:
        raise HistoryError("invalid_record")
    size = struct.unpack(">I", frame[:4])[0]
    if not 1 <= size <= MAX_PAYLOAD or len(frame) != size + 36:
        raise HistoryError("invalid_record")
    d = _keys(parse(frame[4:-32]), "v history_id seq prev_digest record_id kind actor data")
    if type(d["kind"]) is not str:
        raise HistoryError("invalid_record")
    version, domain, profiles = _WIRE_VERSIONS.get(d["kind"], (1, DOMAIN, None))
    if (
        type(d["v"]) is not int
        or d["v"] != version
        or (profiles is not None and profile not in profiles)
    ):
        raise HistoryError("invalid_record")
    if not hmac.compare_digest(hashlib.sha256(domain + frame[:-32]).digest(), frame[-32:]):
        raise HistoryError("invalid_record")
    data = _data(d["kind"], d["data"])
    seq = _number(d["seq"], 1)
    if d["actor"] != _KINDS[type(data)][1] or (version in (4, 5) and seq > MAX_RECORDS):
        raise HistoryError("invalid_record")
    record_id = _uuid(d["record_id"])
    if (
        isinstance(data, InboxOriginalRetired)
        and len({record_id, data.terminal_record_id, data.send_record_id}) != 3
    ):
        raise HistoryError("invalid_record")
    return Entry(
        _uuid(d["history_id"]),
        seq,
        bytes.fromhex(_hex(d["prev_digest"])),
        Command(record_id, data),
        frame[-32:],
    )


@dataclass(frozen=True, slots=True)
class HistoryState:
    entries: tuple[Entry, ...]
    open_restore: UUID | None

    @property
    def digest(self) -> bytes:
        return self.entries[-1].digest if self.entries else ZERO


def validate_chain(
    history_id: UUID, entries: Sequence[Entry], *, profile: Profile = "h1"
) -> HistoryState:
    check_profile(profile)
    if not isinstance(history_id, UUID) or not history_id.int:
        raise HistoryError("invalid_record")
    previous = ZERO
    stopped: set[UUID] = set()
    restored: set[UUID] = set()
    operations: set[UUID] = set()
    accepted: dict[UUID, AcceptPrepare] = {}
    acceptance_records: dict[UUID, UUID] = {}
    artifact_intents: set[UUID] = set()
    terminal_tasks: set[UUID] = set()
    nonsuccess_tasks: set[UUID] = set()
    keys: set[tuple[UUID, int, str]] = set()
    sent: set[tuple[UUID, UUID, UUID]] = set()
    open_restore: tuple[UUID, UUID] | None = None
    retirement_task: UUID | None = None
    research_sends: dict[UUID, list[SendPossible]] = {}
    research_originals: dict[UUID, list[AcceptPrepare]] = {}
    if len(entries) > MAX_RECORDS:
        raise HistoryError("capacity")
    for seq, e in enumerate(entries, 1):
        # Reject forged in-memory digest/structure just as strictly as bytes.
        if (
            decode_entry(
                encode_entry(e.history_id, e.seq, e.prev_digest, e.command, profile=profile),
                profile=profile,
            )
            != e
        ):
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
        if profile == "inbox-research-v1":
            if isinstance(d, (RestoreBegin, RestoreAssessed, ArtifactPrepare)):
                raise HistoryError("unsupported_inbox_history")
            if isinstance(d, AcceptPrepare):
                originals = research_originals.setdefault(d.account_id, [])
                if len(originals) >= 2 or any(
                    a.account_generation != d.account_generation or a.task_id not in terminal_tasks
                    for a in originals
                ):
                    raise HistoryError("unsupported_inbox_history")
                originals.append(d)
            if isinstance(d, StopIntent) and (d.task_id in stopped or d.task_id in terminal_tasks):
                raise HistoryError("effect_stopped")
            if isinstance(d, SendPossible):
                prior = research_sends.setdefault(d.task_id, [])
                if (
                    len(prior) >= len(RESEARCH_SENDS)
                    or d.effect_kind != RESEARCH_SENDS[len(prior)]
                    or any(
                        (
                            p.epoch,
                            p.lifecycle_id,
                            p.financial_scope_id,
                            p.metadata_scope_id,
                            p.producer_instance_id,
                        )
                        != (
                            d.epoch,
                            d.lifecycle_id,
                            d.financial_scope_id,
                            d.metadata_scope_id,
                            d.producer_instance_id,
                        )
                        or p.reservation_id == d.reservation_id
                        or p.runtime_identity == d.runtime_identity
                        or p.dispatch_nonce == d.dispatch_nonce
                        for p in prior
                    )
                ):
                    raise HistoryError("unsupported_research_send")
                prior.append(d)
            if isinstance(d, TerminalOutputPrepare):
                prior = research_sends.get(d.task_id, [])
                if len(prior) != len(RESEARCH_SENDS) or prior[-1].epoch != d.epoch:
                    raise HistoryError("unsupported_research_terminal")
        if profile == "inbox-retired-v1":
            if isinstance(d, (RestoreBegin, RestoreAssessed, ArtifactPrepare)):
                raise HistoryError("unsupported_inbox_history")
            if isinstance(d, AcceptPrepare) and accepted:
                prior = accepted.get(retirement_task) if retirement_task is not None else None
                if (
                    prior is None
                    or len(accepted) != 1
                    or (prior.account_id, prior.account_generation)
                    != (d.account_id, d.account_generation)
                ):
                    raise HistoryError("unsupported_inbox_history")
            if isinstance(d, SendPossible) and (d.effect_kind != "completion" or seq not in (2, 6)):
                raise HistoryError("unsupported_inbox_history")
            if isinstance(d, TerminalOutputPrepare):
                prior_send = entries[seq - 2].command.data if seq in (3, 7) else None
                if not isinstance(prior_send, SendPossible) or (
                    prior_send.task_id,
                    prior_send.epoch,
                ) != (d.task_id, d.epoch):
                    raise HistoryError("ancestor_mismatch")
        if isinstance(d, AcceptPrepare):
            if open_restore is not None:
                raise HistoryError("restore_mismatch")
            key = (d.account_id, d.key_id, d.key_commitment)
            if key in keys or d.task_id in accepted:
                raise HistoryError("conflict")
            keys.add(key)
            accepted[d.task_id] = d
            acceptance_records[d.task_id] = e.command.record_id
        elif isinstance(d, TERMINAL_KINDS):
            a = accepted.get(d.task_id)
            if (
                a is None
                or (a.account_id, a.account_generation, a.result_message_id)
                != (d.account_id, d.account_generation, d.result_message_id)
                or acceptance_records[d.task_id] != d.acceptance_record_id
            ):
                raise HistoryError("ancestor_mismatch")
            if open_restore is not None or d.task_id in stopped:
                raise HistoryError("effect_stopped")
            if d.task_id in terminal_tasks:
                raise HistoryError("conflict")
            terminal_tasks.add(d.task_id)
            if isinstance(d, NonsuccessOutputPrepare):
                nonsuccess_tasks.add(d.task_id)
        elif isinstance(d, InboxOriginalRetired):
            # Fixed fourth position makes the whole prefix unambiguous, and
            # needs no new indexes/retained objects in any legacy profile.
            if seq != 4:
                raise HistoryError("ancestor_mismatch")
            send_cmd, terminal_cmd = entries[1].command, entries[2].command
            send, terminal = send_cmd.data, terminal_cmd.data
            if (
                not isinstance(send, SendPossible)
                or not isinstance(terminal, TerminalOutputPrepare)
                or send_cmd.record_id != d.send_record_id
                or terminal_cmd.record_id != d.terminal_record_id
                or (send.task_id, send.epoch) != (terminal.task_id, terminal.epoch)
            ):
                raise HistoryError("ancestor_mismatch")
            retirement_task = terminal.task_id
        elif isinstance(d, ArtifactPrepare):
            a = accepted.get(d.task_id)
            if (
                a is None
                or (a.account_id, a.account_generation) != (d.account_id, d.account_generation)
                or acceptance_records[d.task_id] != d.acceptance_record_id
            ):
                raise HistoryError("ancestor_mismatch")
            if open_restore is not None or d.task_id in stopped or d.task_id in terminal_tasks:
                raise HistoryError("effect_stopped")
            if d.intent_id in artifact_intents:
                raise HistoryError("conflict")
            artifact_intents.add(d.intent_id)
        elif isinstance(d, (SendPossible, StopIntent)):
            a = accepted.get(d.task_id)
            if a is None or (a.account_id, a.account_generation) != (
                d.account_id,
                d.account_generation,
            ):
                raise HistoryError("ancestor_mismatch")
            if isinstance(d, StopIntent):
                # Preserve historical v1-v3 bytes (which permitted a negative
                # Stop after COMPLETE); a v4 publication cannot be overwritten.
                if d.task_id in nonsuccess_tasks or d.task_id == retirement_task:
                    raise HistoryError("effect_stopped")
                stopped.add(d.task_id)
            if isinstance(d, SendPossible):
                if open_restore is not None or d.task_id in stopped or d.task_id in terminal_tasks:
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
