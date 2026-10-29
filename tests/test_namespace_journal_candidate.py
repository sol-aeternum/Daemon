"""Synthetic regressions for the uncalled N0 namespace journal candidate.

Covers the frozen canonical v1/v2 byte vectors, frame and footer strictness, the
64 KiB cap, v2 shape rejection, mixed v1/v2 replay, namespace uniqueness, birth
generation rules and the no-runtime-import guard. Structural only: no database,
filesystem, clock, random name, real account or real namespace is involved.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import (
    AckFields,
    Cause,
    Command,
    Entry,
    ReadyFields,
    RequestFields,
    TerminalFields,
    command_digest,
    decode_entry,
    encode_entry,
)
from orchestrator.services.deletion_journal.namespace_candidate import (
    MixedNamespaceJournal,
    NamespaceCommand,
    NamespaceCreateFields,
    NamespaceEntry,
    decode_candidate_entry,
    decode_candidate_frame,
    decode_frame,
    decode_namespace_entry,
    encode_entry_frame,
    encode_frame,
    encode_namespace_entry,
    encode_namespace_frame,
    namespace_command_digest,
)

UTC = timezone.utc
ZERO = bytes(32)
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_PATH = REPO_ROOT / "tests" / "fixtures" / "namespace_journal_candidate" / "vectors.json"
CANDIDATE_PATH = (
    REPO_ROOT / "orchestrator" / "services" / "deletion_journal" / "namespace_candidate.py"
)

JOURNAL_ID = UUID("0ad00000-0000-4000-8000-0000000000aa")
OTHER_JOURNAL_ID = UUID("0ad00000-0000-4000-8000-0000000000ab")
ACCOUNT = UUID("ac000000-0000-4000-8000-000000000001")
OTHER_ACCOUNT = UUID("ac000000-0000-4000-8000-000000000002")
OP_A = UUID("0b000000-0000-4000-8000-0000000000bb")
OP_B = UUID("0b000000-0000-4000-8000-0000000000bc")
OTHER_CAUSE = UUID("0c000000-0000-4000-8000-0000000000cc")
NAMESPACE = "0123456789abcdef0123456789abcdef"
OTHER_NAMESPACE = "fedcba9876543210fedcba9876543210"
INT64_MAX = 2**63 - 1
MAX_FRAME = 64 * 1024

INVALID = "invalid_entry"
CONFLICT = "conflict"

V1_VECTOR = "v1_request_first_frame"
V2_VECTOR = "v2_namespace_birth_first_frame"

DAY = timedelta(days=1)
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)
T3 = datetime(2026, 1, 9, 12, 0, 0, tzinfo=UTC)  # exactly the 7-day deadline
T4 = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)

FORBIDDEN_SOURCE = (
    r"\bopen\(",
    r"\.read_text\(",
    r"\.write_text\(",
    r"asyncio",
    r"\bos\.",
    r"random\.",
    r"time\.time\(",
    r"datetime\.now\(",
    r"utcnow\(",
    r"environ",
    r"asyncpg",
    r"httpx",
    r"requests\.",
    r"subprocess",
    r"socket",
    r"sqlite",
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _code(exc: JournalError) -> str:
    # The shared errors module is authored independently; read the code from
    # either an attribute or the single positional argument.
    attr = getattr(exc, "code", None)
    if attr is not None:
        return str(attr)
    return str(exc.args[0]) if exc.args else ""


def _raises_code(code: str, fn: Callable[[], object]) -> None:
    with pytest.raises(JournalError) as excinfo:
        fn()
    assert _code(excinfo.value) == code


def _vector(name: str) -> dict[str, Any]:
    data: Any = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    vectors = data["vectors"]
    assert isinstance(vectors, dict)
    vector = vectors[name]
    assert isinstance(vector, dict)
    return vector


def _frozen_payload(name: str) -> bytes:
    vector = _vector(name)
    source = vector["command_digest_input"].encode("utf-8")
    digest_hex = hashlib.sha256(source).hexdigest()
    template = vector["canonical_payload"]
    return template.replace("{command_digest}", digest_hex).encode("utf-8")


def _manual_digest(payload: bytes) -> bytes:
    prefix = len(payload).to_bytes(4, "big")
    return hashlib.sha256(prefix + payload).digest()


def _manual_frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload + _manual_digest(payload)


def _causes(*items: tuple[str, UUID]) -> tuple[Cause, ...]:
    return tuple(Cause(code, cid) for code, cid in items)


def _request_fields(op: UUID, **overrides: Any) -> RequestFields:
    params: dict[str, Any] = {
        "prior_status": "active",
        "prior_reason": None,
        "causes": _causes(("deletion", op)),
        "generation": 5,
        "drain_deadline": T0 + DAY,
        "revocation_deadline": T0 + 2 * DAY,
        "billing_deadline": T0 + 3 * DAY,
        "no_grace": False,
        "reason": None,
    }
    params.update(overrides)
    return RequestFields(**params)


def _ready_fields(started_at: datetime) -> ReadyFields:
    return ReadyFields(started_at=started_at, deadline=started_at + timedelta(days=7))


def _cmd(event_type: str, fields: Any, **overrides: Any) -> Command:
    params: dict[str, Any] = {
        "account_id": ACCOUNT,
        "operation_id": OP_A,
        "event_type": event_type,
        "decision_at": T0,
        "fields": fields,
    }
    params.update(overrides)
    return Command(**params)


def _deletion_request(op: UUID = OP_A, **overrides: Any) -> Command:
    overrides.setdefault("generation", 5)
    return _cmd("requested", _request_fields(op, **overrides), operation_id=op)


def _reset_request(op: UUID = OP_A, *, generation: int = 10) -> Command:
    fields = _request_fields(
        op,
        prior_status="suspended",
        prior_reason="abuse",
        causes=_causes(("abuse", OTHER_CAUSE), ("reset", op)),
        generation=generation,
    )
    return _cmd("reset_requested", fields, operation_id=op)


def _grace_started(op: UUID = OP_A, decision: datetime = T1) -> Command:
    return _cmd("grace_started", _ready_fields(decision), operation_id=op, decision_at=decision)


def _report_ready(op: UUID = OP_A, decision: datetime = T1) -> Command:
    fields = _ready_fields(decision)
    return _cmd("reset_report_ready", fields, operation_id=op, decision_at=decision)


def _acknowledged(op: UUID = OP_A, decision: datetime = T2) -> Command:
    fields = AckFields(acknowledged_at=decision)
    return _cmd("reset_acknowledged", fields, operation_id=op, decision_at=decision)


def _terminal(
    event_type: str,
    op: UUID = OP_A,
    *,
    generation: int = 6,
    decision: datetime = T2,
    causes: tuple[Cause, ...] = (),
    reason: str | None = None,
) -> Command:
    fields = TerminalFields(causes=causes, generation=generation, reason=reason)
    return _cmd(event_type, fields, operation_id=op, decision_at=decision)


def _namespace_command(
    *,
    account_id: UUID = ACCOUNT,
    generation: int = 0,
    namespace: str = NAMESPACE,
    decision_at: datetime = T0,
) -> NamespaceCommand:
    return NamespaceCommand(
        account_id=account_id,
        event_type="namespace_create_intended",
        decision_at=decision_at,
        fields=NamespaceCreateFields(generation=generation, namespace=namespace),
    )


_AUTO = "auto"


def _seal_v1(entry: Entry) -> Entry:
    return dc_replace(entry, digest=_manual_digest(encode_entry(entry)))


def _seal_v2(entry: NamespaceEntry) -> NamespaceEntry:
    return dc_replace(entry, digest=_manual_digest(encode_namespace_entry(entry)))


def _v1_entry(
    journal: MixedNamespaceJournal,
    command: Command,
    *,
    event_id: UUID | None = None,
    journal_id: UUID = JOURNAL_ID,
    account_prev: int | None | str = _AUTO,
    operation_prev: int | None | str = _AUTO,
    seq: int | None = None,
) -> Entry:
    aprev: int | None
    if account_prev == _AUTO:
        aprev = journal.account_head(command.account_id)
    else:
        assert not isinstance(account_prev, str)
        aprev = account_prev
    oprev: int | None
    if operation_prev == _AUTO:
        oprev = journal.operation_head(command.operation_id)
    else:
        assert not isinstance(operation_prev, str)
        oprev = operation_prev
    return _seal_v1(
        Entry(
            journal_id=journal_id,
            seq=seq if seq is not None else journal.frame_count + 1,
            event_id=event_id or uuid4(),
            command=command,
            account_prev=aprev,
            operation_prev=oprev,
            previous_digest=journal.head_digest,
            command_digest=command_digest(command),
            digest=ZERO,
        )
    )


def _v2_entry(
    journal: MixedNamespaceJournal,
    command: NamespaceCommand,
    *,
    event_id: UUID | None = None,
    journal_id: UUID = JOURNAL_ID,
    account_prev: int | None | str = _AUTO,
    previous_digest: bytes | None = None,
    seq: int | None = None,
) -> NamespaceEntry:
    aprev: int | None
    if account_prev == _AUTO:
        aprev = journal.account_head(command.account_id)
    else:
        assert not isinstance(account_prev, str)
        aprev = account_prev
    chain_digest = journal.head_digest if previous_digest is None else previous_digest
    return _seal_v2(
        NamespaceEntry(
            journal_id=journal_id,
            seq=seq if seq is not None else journal.frame_count + 1,
            event_id=event_id or uuid4(),
            command=command,
            account_prev=aprev,
            previous_digest=chain_digest,
            command_digest=namespace_command_digest(command),
            digest=ZERO,
        )
    )


def _push_v1(journal: MixedNamespaceJournal, command: Command) -> Entry:
    entry = _v1_entry(journal, command)
    journal.accept_entry(entry)
    return entry


def _push_v2(journal: MixedNamespaceJournal, command: NamespaceCommand) -> NamespaceEntry:
    entry = _v2_entry(journal, command)
    journal.accept_namespace_entry(entry)
    return entry


def _reject(code: str, journal: MixedNamespaceJournal, entry: Entry | NamespaceEntry) -> None:
    def accept() -> None:
        if isinstance(entry, NamespaceEntry):
            journal.accept_namespace_entry(entry)
        else:
            journal.accept_entry(entry)

    _raises_code(code, accept)


def _reject_frame(code: str, journal: MixedNamespaceJournal, frame: bytes) -> None:
    _raises_code(code, lambda: journal.accept_frame(frame))


def _snapshot(journal: MixedNamespaceJournal) -> tuple[Any, ...]:
    return (
        journal.frame_count,
        journal.head_digest,
        journal.journal_id,
        journal.account_head(ACCOUNT),
        journal.account_head(OTHER_ACCOUNT),
        journal.operation_head(OP_A),
        journal.operation_head(OP_B),
        journal.namespace_births,
        journal.lookup_namespace(NAMESPACE),
        journal.lookup_namespace(OTHER_NAMESPACE),
    )


def _frozen_v1_entry() -> Entry:
    command = Command(
        account_id=ACCOUNT,
        operation_id=OP_A,
        event_type="requested",
        decision_at=T0,
        fields=_request_fields(OP_A, generation=1),
    )
    return Entry(
        journal_id=JOURNAL_ID,
        seq=1,
        event_id=UUID("0e000000-0000-4000-8000-0000000000e1"),
        command=command,
        account_prev=None,
        operation_prev=None,
        previous_digest=ZERO,
        command_digest=command_digest(command),
        digest=_manual_digest(_frozen_payload(V1_VECTOR)),
    )


def _frozen_v2_entry() -> NamespaceEntry:
    command = _namespace_command()
    return NamespaceEntry(
        journal_id=JOURNAL_ID,
        seq=1,
        event_id=UUID("0e000000-0000-4000-8000-0000000000e2"),
        command=command,
        account_prev=None,
        previous_digest=ZERO,
        command_digest=namespace_command_digest(command),
        digest=_manual_digest(_frozen_payload(V2_VECTOR)),
    )


def _v2_payload() -> bytes:
    return _frozen_payload(V2_VECTOR)


def _command_of(obj: dict[str, Any]) -> dict[str, Any]:
    command = obj["command"]
    assert isinstance(command, dict)
    return command


def _fields_of(obj: dict[str, Any]) -> dict[str, Any]:
    fields = _command_of(obj)["fields"]
    assert isinstance(fields, dict)
    return fields


def _holder(obj: dict[str, Any], target: str) -> dict[str, Any]:
    if target == "command":
        return _command_of(obj)
    if target == "fields":
        return _fields_of(obj)
    return obj


def _rewrite(payload: bytes, mutate: Callable[[dict[str, Any]], None]) -> bytes:
    obj = json.loads(payload)
    mutate(obj)
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8")


def _mutate_at(target: str, key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(obj: dict[str, Any]) -> None:
        _holder(obj, target)[key] = value

    return mutate


def _drop_at(target: str, key: str) -> Callable[[dict[str, Any]], None]:
    def mutate(obj: dict[str, Any]) -> None:
        del _holder(obj, target)[key]

    return mutate


# ---------------------------------------------------------------------------
# frozen canonical vectors
# ---------------------------------------------------------------------------


class TestFrozenVectors:
    def test_v1_payload_is_the_unchanged_encoder_output(self) -> None:
        entry = _frozen_v1_entry()
        payload = _frozen_payload(V1_VECTOR)
        footer = _manual_digest(payload)
        assert encode_entry(entry) == payload
        assert encode_entry_frame(entry) == _manual_frame(payload)
        assert decode_entry(payload, footer) == dc_replace(entry, digest=footer)

    def test_v2_payload_is_the_candidate_encoder_output(self) -> None:
        entry = _frozen_v2_entry()
        payload = _frozen_payload(V2_VECTOR)
        footer = _manual_digest(payload)
        assert encode_namespace_entry(entry) == payload
        assert encode_namespace_frame(entry) == _manual_frame(payload)
        assert decode_namespace_entry(payload, footer) == dc_replace(entry, digest=footer)

    def test_v1_digest_input_is_frozen(self) -> None:
        source = _vector(V1_VECTOR)["command_digest_input"].encode("utf-8")
        assert command_digest(_frozen_v1_entry().command) == hashlib.sha256(source).digest()

    def test_v2_digest_input_is_frozen(self) -> None:
        source = _vector(V2_VECTOR)["command_digest_input"].encode("utf-8")
        expected = hashlib.sha256(source).digest()
        assert namespace_command_digest(_frozen_v2_entry().command) == expected

    @pytest.mark.parametrize("name", [V1_VECTOR, V2_VECTOR])
    def test_frozen_payloads_are_canonical_json(self, name: str) -> None:
        payload = _frozen_payload(name)
        obj = json.loads(payload)
        canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert canonical.encode("utf-8") == payload

    def test_candidate_reader_roundtrips_both_versions(self) -> None:
        v1 = decode_candidate_frame(_manual_frame(_frozen_payload(V1_VECTOR)))
        assert isinstance(v1, Entry)
        assert v1 == _frozen_v1_entry()
        v2 = decode_candidate_frame(_manual_frame(_frozen_payload(V2_VECTOR)))
        assert isinstance(v2, NamespaceEntry)
        assert v2 == _frozen_v2_entry()


# ---------------------------------------------------------------------------
# framing and the 64 KiB cap
# ---------------------------------------------------------------------------


class TestFraming:
    def test_roundtrip_returns_payload_and_footer(self) -> None:
        payload = _v2_payload()
        frame = _manual_frame(payload)
        assert encode_frame(payload) == frame
        assert decode_frame(frame) == (payload, _manual_digest(payload))

    def test_length_prefix_is_big_endian(self) -> None:
        payload = b'{"a":1}'
        body_start = 4 + len(payload)
        frame = encode_frame(payload)
        assert frame[:4] == len(payload).to_bytes(4, "big")
        assert frame[4:body_start] == payload
        assert frame[body_start:] == _manual_digest(payload)

    def test_frame_cap_boundary(self) -> None:
        payload = b"x" * (MAX_FRAME - 4 - 32)
        frame = encode_frame(payload)
        assert len(frame) == MAX_FRAME
        assert decode_frame(frame) == (payload, _manual_digest(payload))
        _raises_code(INVALID, lambda: encode_frame(payload + b"x"))
        _raises_code(INVALID, lambda: decode_frame(frame + b"x"))

    def test_direct_payload_decoders_also_enforce_the_frame_cap(self) -> None:
        payload = b" " * (MAX_FRAME - 4 - 32 + 1)
        _raises_code(INVALID, lambda: decode_candidate_entry(payload, ZERO))
        _raises_code(INVALID, lambda: decode_namespace_entry(payload, ZERO))

    def test_empty_and_non_bytes_inputs_rejected(self) -> None:
        _raises_code(INVALID, lambda: encode_frame(b""))
        _raises_code(INVALID, lambda: decode_frame(b""))
        _raises_code(INVALID, lambda: encode_frame(cast(bytes, "payload")))
        _raises_code(INVALID, lambda: decode_frame(cast(bytes, "frame")))

    def test_truncation_and_trailing_bytes_rejected(self) -> None:
        frame = _manual_frame(b'{"a":1}')
        _raises_code(INVALID, lambda: decode_frame(frame[:-1]))
        _raises_code(INVALID, lambda: decode_frame(frame + b"\x00"))

    def test_declared_length_must_match_the_record(self) -> None:
        payload = b'{"a":1}'
        for declared in (len(payload) - 1, len(payload) + 1):
            prefix = declared.to_bytes(4, "big")
            frame = prefix + payload + hashlib.sha256(prefix + payload).digest()
            _raises_code(INVALID, lambda: decode_frame(frame))

    def test_footer_and_payload_tampering_rejected(self) -> None:
        payload = b'{"a":1}'
        prefix = len(payload).to_bytes(4, "big")
        _raises_code(INVALID, lambda: decode_frame(prefix + payload + bytes(32)))
        tampered = bytearray(_manual_frame(payload))
        tampered[5] = 0x7B if tampered[5] != 0x7B else 0x7D
        _raises_code(INVALID, lambda: decode_frame(bytes(tampered)))


# ---------------------------------------------------------------------------
# v1 compatibility
# ---------------------------------------------------------------------------


class TestV1Compatibility:
    def test_production_decoder_still_refuses_a_valid_v2_frame(self) -> None:
        payload = _v2_payload()
        footer = _manual_digest(payload)
        _raises_code(INVALID, lambda: decode_entry(payload, footer))
        _raises_code(INVALID, lambda: decode_entry(payload, ZERO))

    def test_candidate_reader_accepts_unchanged_v1_bytes(self) -> None:
        payload = _frozen_payload(V1_VECTOR)
        footer = _manual_digest(payload)
        expected = dc_replace(_frozen_v1_entry(), digest=footer)
        assert decode_candidate_entry(payload, footer) == expected

    def test_unknown_and_missing_versions_fail_closed(self) -> None:
        payload = _frozen_payload(V1_VECTOR)
        for version in (b"3", b"0", b"true", b'"1"', b"2.0"):
            mutated = payload.replace(b'"version":1', b'"version":' + version, 1)
            _raises_code(INVALID, lambda: decode_candidate_entry(mutated, ZERO))
        without = payload.replace(b',"version":1', b"", 1)
        _raises_code(INVALID, lambda: decode_candidate_entry(without, ZERO))


# ---------------------------------------------------------------------------
# v2 shape rejections
# ---------------------------------------------------------------------------


class TestNamespaceDecodeRejections:
    @pytest.mark.parametrize(
        ("target", "key", "value"),
        [
            ("envelope", "version", 3),
            ("envelope", "version", True),
            ("envelope", "version", "2"),
            ("envelope", "version", 2.0),
            ("envelope", "operation_prev", 1),
            ("envelope", "operation_prev", 0),
            ("envelope", "operation_prev", True),
            ("envelope", "seq", 0),
            ("envelope", "seq", True),
            ("envelope", "seq", INT64_MAX + 1),
            ("envelope", "seq", "1"),
            ("envelope", "account_prev", 0),
            ("envelope", "account_prev", "1"),
            ("envelope", "journal_id", "0AD00000-0000-4000-8000-0000000000AA"),
            ("envelope", "journal_id", "0ad00000-0000-4000-8000-0000000000a"),
            ("envelope", "event_id", "{0e000000-0000-4000-8000-0000000000e2}"),
            ("envelope", "previous_digest", "0" * 63),
            ("envelope", "previous_digest", "F" * 64),
            ("envelope", "command_digest", "f" * 64),
            ("envelope", "command_digest", "0" * 63),
            ("envelope", "extra", None),
            ("command", "operation_id", str(OP_A)),
            ("command", "event_type", "namespace_create"),
            ("command", "event_type", 2),
            ("command", "decision_at", "2026-01-01T12:00:00Z"),
            ("command", "decision_at", "2026-01-01T12:00:00"),
            ("command", "decision_at", "2026-01-01T13:00:00+01:00"),
            ("command", "decision_at", "2026-01-01 12:00:00+00:00"),
            ("command", "fields", []),
            ("command", "note", "x"),
            ("fields", "kind", "namespace"),
            ("fields", "kind", 1),
            ("fields", "generation", -1),
            ("fields", "generation", True),
            ("fields", "generation", INT64_MAX + 1),
            ("fields", "generation", "0"),
            ("fields", "generation", 1.0),
            ("fields", "namespace", NAMESPACE.upper()),
            ("fields", "namespace", NAMESPACE[:31]),
            ("fields", "namespace", NAMESPACE + "0"),
            ("fields", "namespace", NAMESPACE[:31] + "g"),
            ("fields", "namespace", "/etc/passwd"),
            ("fields", "namespace", 0),
            ("fields", "path", "/tmp/x"),
        ],
    )
    def test_malformed_v2_shapes_rejected(self, target: str, key: str, value: Any) -> None:
        payload = _rewrite(_v2_payload(), _mutate_at(target, key, value))
        _raises_code(INVALID, lambda: decode_namespace_entry(payload, ZERO))
        _raises_code(INVALID, lambda: decode_candidate_entry(payload, ZERO))

    @pytest.mark.parametrize(
        ("target", "key"),
        [
            ("envelope", "command"),
            ("envelope", "seq"),
            ("command", "operation_id"),
            ("fields", "kind"),
            ("fields", "namespace"),
        ],
    )
    def test_missing_v2_keys_rejected(self, target: str, key: str) -> None:
        payload = _rewrite(_v2_payload(), _drop_at(target, key))
        _raises_code(INVALID, lambda: decode_namespace_entry(payload, ZERO))

    def test_duplicate_keys_and_non_canonical_bytes_rejected(self) -> None:
        payload = _v2_payload()
        duplicated = payload.replace(b'"version":2', b'"version":2,"version":2', 1)
        _raises_code(INVALID, lambda: decode_namespace_entry(duplicated, ZERO))
        repeated = payload.replace(
            b'"kind":"namespace_create"',
            b'"kind":"namespace_create","kind":"namespace_create"',
            1,
        )
        _raises_code(INVALID, lambda: decode_namespace_entry(repeated, ZERO))
        spaced = payload.replace(b',"', b', "', 1)
        _raises_code(INVALID, lambda: decode_namespace_entry(spaced, ZERO))
        without = payload.replace(b'"account_prev":null,', b"", 1)
        reordered = without[:-1] + b',"account_prev":null}'
        _raises_code(INVALID, lambda: decode_namespace_entry(reordered, ZERO))
        _raises_code(INVALID, lambda: decode_namespace_entry(payload + b"\n", ZERO))
        _raises_code(INVALID, lambda: decode_namespace_entry(b'{"x":"\xff"}', ZERO))
        _raises_code(INVALID, lambda: decode_namespace_entry(b'{"version":NaN}', ZERO))
        _raises_code(INVALID, lambda: decode_namespace_entry(b"[]", ZERO))
        _raises_code(INVALID, lambda: decode_namespace_entry(payload, cast(bytes, "digest")))
        _raises_code(INVALID, lambda: decode_namespace_entry(cast(bytes, "{}"), ZERO))

    @pytest.mark.parametrize("payload", [b'{"x":"\\ud800"}', b'{"version":1,"x":"\\udfff"}'])
    def test_lone_surrogates_have_closed_errors(self, payload: bytes) -> None:
        _raises_code(INVALID, lambda: decode_namespace_entry(payload, ZERO))
        _raises_code(INVALID, lambda: decode_candidate_entry(payload, ZERO))


# ---------------------------------------------------------------------------
# mixed replay: birth acceptance and chain guards
# ---------------------------------------------------------------------------


class TestNamespaceState:
    def test_birth_advances_only_the_account_head(self) -> None:
        journal = MixedNamespaceJournal()
        entry = _push_v2(journal, _namespace_command())
        assert journal.frame_count == 1
        assert journal.journal_id == JOURNAL_ID
        assert journal.head_digest == entry.digest
        assert journal.account_head(ACCOUNT) == 1
        assert journal.account_head(OTHER_ACCOUNT) is None
        assert journal.operation_head(OP_A) is None
        assert journal.namespace_births == (entry,)
        assert journal.lookup_namespace(NAMESPACE) == entry

    def test_initial_generation_may_be_zero(self) -> None:
        journal = MixedNamespaceJournal()
        entry = _push_v2(journal, _namespace_command(generation=0))
        assert journal.lookup_namespace(NAMESPACE) == entry

    def test_initial_generation_may_be_positive(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command(generation=7))
        advanced = _namespace_command(generation=8, namespace=OTHER_NAMESPACE)
        _reject(CONFLICT, journal, _v2_entry(journal, advanced))

    def test_initial_generation_may_be_int64_max(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command(generation=INT64_MAX))
        same = _v1_entry(journal, _deletion_request(generation=INT64_MAX))
        _reject(INVALID, journal, same)

    def test_later_fence_must_exceed_a_birth_generation(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command(generation=5))
        equal = _v1_entry(journal, _deletion_request(generation=5))
        _reject(INVALID, journal, equal)
        request = _push_v1(journal, _deletion_request(generation=6))
        assert journal.account_head(ACCOUNT) == request.seq

    def test_birth_uses_the_recorded_generation_after_v1_frames(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v1(journal, _deletion_request(generation=5))
        _push_v1(journal, _grace_started())
        _push_v1(journal, _terminal("restored", generation=6, decision=T2))
        below = _v2_entry(journal, _namespace_command(generation=5, namespace=OTHER_NAMESPACE))
        _reject(CONFLICT, journal, below)
        above = _v2_entry(journal, _namespace_command(generation=7, namespace=OTHER_NAMESPACE))
        _reject(CONFLICT, journal, above)
        entry = _push_v2(journal, _namespace_command(generation=6, namespace=OTHER_NAMESPACE))
        assert journal.account_head(ACCOUNT) == entry.seq

    def test_namespace_names_are_globally_unique(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command())
        elsewhere = _namespace_command(account_id=OTHER_ACCOUNT, generation=1)
        _reject(CONFLICT, journal, _v2_entry(journal, elsewhere))

    def test_one_birth_per_account_generation(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command(generation=3))
        renamed = _namespace_command(generation=3, namespace=OTHER_NAMESPACE)
        _reject(CONFLICT, journal, _v2_entry(journal, renamed))
        # A well-formed, correctly chained replay of the same intent is never a
        # second accepted event.
        _reject(INVALID, journal, _v2_entry(journal, _namespace_command(generation=3)))

    def test_birth_refused_during_an_open_operation(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v1(journal, _deletion_request(generation=5))
        _reject(INVALID, journal, _v2_entry(journal, _namespace_command(generation=5)))

    def test_birth_refused_after_final_purge(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v1(journal, _deletion_request(generation=5, no_grace=True, reason="legal"))
        causes = _causes(("deletion", OP_A))
        purged = _terminal("purged", generation=5, decision=T3, causes=causes)
        _push_v1(journal, purged)
        _reject(INVALID, journal, _v2_entry(journal, _namespace_command(generation=5)))
        _reject(INVALID, journal, _v1_entry(journal, _deletion_request(generation=6)))

    def test_journal_identity_is_fixed_by_the_first_frame(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command())
        foreign = _v2_entry(
            journal,
            _namespace_command(namespace=OTHER_NAMESPACE),
            journal_id=OTHER_JOURNAL_ID,
        )
        _reject(INVALID, journal, foreign)

    def test_sequence_holes_and_frame_replays_rejected(self) -> None:
        journal = MixedNamespaceJournal()
        entry = _push_v2(journal, _namespace_command())
        hole = _v2_entry(journal, _namespace_command(namespace=OTHER_NAMESPACE), seq=3)
        _reject(INVALID, journal, hole)
        _reject(INVALID, journal, entry)

    def test_digest_chain_link_required(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command())
        stale = _v2_entry(
            journal,
            _namespace_command(namespace=OTHER_NAMESPACE),
            previous_digest=ZERO,
        )
        _reject(INVALID, journal, stale)

    def test_event_identity_is_unique_across_versions(self) -> None:
        journal = MixedNamespaceJournal()
        birth = _push_v2(journal, _namespace_command())
        request = _deletion_request(generation=5)
        duplicate = _v1_entry(journal, request, event_id=birth.event_id)
        _reject(INVALID, journal, duplicate)

    def test_forged_footers_are_never_trusted(self) -> None:
        journal = MixedNamespaceJournal()
        birth = _v2_entry(journal, _namespace_command())
        _reject(INVALID, journal, dc_replace(birth, digest=b"\x01" * 32))
        request = _v1_entry(journal, _deletion_request(generation=1))
        _reject(INVALID, journal, dc_replace(request, digest=b"\x02" * 32))

    def test_recorded_command_digest_must_match(self) -> None:
        journal = MixedNamespaceJournal()
        entry = _v2_entry(journal, _namespace_command())
        resealed = _seal_v2(dc_replace(entry, command_digest=ZERO))
        _reject(INVALID, journal, resealed)

    def test_account_predecessor_cas(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command())
        missing = _v2_entry(
            journal,
            _namespace_command(namespace=OTHER_NAMESPACE),
            account_prev=None,
        )
        _reject(CONFLICT, journal, missing)
        stale = _v1_entry(journal, _deletion_request(generation=1), account_prev=None)
        _reject(CONFLICT, journal, stale)

    def test_lookup_resolves_only_an_identical_immutable_command(self) -> None:
        journal = MixedNamespaceJournal()
        command = _namespace_command(generation=4)
        entry = _push_v2(journal, command)
        assert journal.lookup_namespace_create(command) == entry
        retried = _namespace_command(generation=4, decision_at=T4)
        assert journal.lookup_namespace_create(retried) == entry
        assert journal.lookup_namespace_create(_namespace_command(generation=5)) is None
        renamed = _namespace_command(generation=4, namespace=OTHER_NAMESPACE)
        assert journal.lookup_namespace_create(renamed) is None
        elsewhere = _namespace_command(account_id=OTHER_ACCOUNT, generation=4)
        assert journal.lookup_namespace_create(elsewhere) is None
        assert journal.lookup_namespace(NAMESPACE) == entry
        assert journal.lookup_namespace(OTHER_NAMESPACE) is None

    def test_frame_acceptance_matches_entry_acceptance(self) -> None:
        journal = MixedNamespaceJournal()
        birth = _v2_entry(journal, _namespace_command())
        assert journal.accept_frame(encode_namespace_frame(birth)) == birth
        request = _v1_entry(journal, _deletion_request(generation=1))
        assert journal.accept_frame(encode_entry_frame(request)) == request

    def test_refused_frames_leave_no_partial_state(self) -> None:
        journal = MixedNamespaceJournal()
        empty = _snapshot(journal)
        _reject_frame(INVALID, journal, b"\x00\x00\x00\x02{}")
        assert _snapshot(journal) == empty
        assert journal.journal_id is None

        _push_v2(journal, _namespace_command(generation=3))
        snapshot = _snapshot(journal)
        duplicate = _v2_entry(journal, _namespace_command(generation=3))
        _reject_frame(INVALID, journal, encode_namespace_frame(duplicate))
        conflicting = _v2_entry(
            journal,
            _namespace_command(generation=3, namespace=OTHER_NAMESPACE),
            account_prev=None,
        )
        _reject_frame(CONFLICT, journal, encode_namespace_frame(conflicting))
        assert _snapshot(journal) == snapshot

        _push_v1(journal, _deletion_request(generation=4))
        snapshot = _snapshot(journal)
        second_open = _v1_entry(journal, _deletion_request(OP_B, generation=5))
        _reject_frame(INVALID, journal, encode_entry_frame(second_open))
        assert _snapshot(journal) == snapshot
        ready = _push_v1(journal, _grace_started())
        assert ready.seq == snapshot[0] + 1


# ---------------------------------------------------------------------------
# mixed v1/v2 lifecycles
# ---------------------------------------------------------------------------


class TestMixedLifecycle:
    def test_birth_then_deletion_restore(self) -> None:
        journal = MixedNamespaceJournal()
        birth = _push_v2(journal, _namespace_command(generation=3))
        request = _push_v1(journal, _deletion_request(generation=4))
        ready = _push_v1(journal, _grace_started())
        restored = _push_v1(journal, _terminal("restored", generation=5, decision=T2))
        assert (birth.seq, request.seq, ready.seq, restored.seq) == (1, 2, 3, 4)
        assert journal.account_head(ACCOUNT) == 4
        assert journal.operation_head(OP_A) == 4
        assert journal.head_digest == restored.digest
        assert journal.namespace_births == (birth,)

    def test_birth_next_generation_after_restore(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command(generation=3))
        _push_v1(journal, _deletion_request(generation=4))
        _push_v1(journal, _grace_started())
        _push_v1(journal, _terminal("restored", generation=5, decision=T2))
        birth = _push_v2(journal, _namespace_command(generation=5, namespace=OTHER_NAMESPACE))
        assert journal.account_head(ACCOUNT) == birth.seq
        assert journal.operation_head(OP_A) == 4
        # A closed operation UUID never reopens, even after a namespace birth.
        _reject(INVALID, journal, _v1_entry(journal, _deletion_request(generation=6)))
        request = _push_v1(journal, _deletion_request(OP_B, generation=6))
        assert journal.operation_head(OP_A) == 4
        assert journal.operation_head(OP_B) == request.seq

    def test_birth_then_reset_cancel(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command(generation=3))
        _push_v1(journal, _reset_request(generation=4))
        _push_v1(journal, _report_ready())
        cancelled = _push_v1(
            journal,
            _terminal(
                "reset_cancelled",
                generation=5,
                decision=T2,
                causes=_causes(("abuse", OTHER_CAUSE)),
                reason="owner",
            ),
        )
        assert journal.account_head(ACCOUNT) == cancelled.seq
        assert journal.operation_head(OP_A) == cancelled.seq

    def test_birth_then_reset_purge(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command(generation=3))
        _push_v1(journal, _reset_request(generation=4))
        _push_v1(journal, _report_ready())
        _push_v1(journal, _acknowledged(decision=T2))
        purged = _push_v1(
            journal,
            _terminal(
                "reset_purged",
                generation=4,
                decision=T3,
                causes=_causes(("abuse", OTHER_CAUSE)),
            ),
        )
        assert journal.account_head(ACCOUNT) == purged.seq
        assert journal.operation_head(OP_A) == purged.seq

    def test_operation_ownership_is_preserved(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v1(journal, _deletion_request(generation=3))
        fields = _request_fields(OP_A, generation=4)
        rebind = _cmd("requested", fields, operation_id=OP_A, account_id=OTHER_ACCOUNT)
        _reject(CONFLICT, journal, _v1_entry(journal, rebind))

    def test_terminal_rules_and_causes_are_preserved(self) -> None:
        journal = MixedNamespaceJournal()
        _push_v2(journal, _namespace_command(generation=3))
        _push_v1(journal, _deletion_request(generation=4))
        _push_v1(journal, _grace_started())
        invented = _terminal(
            "restored",
            generation=5,
            decision=T2,
            causes=_causes(("abuse", OTHER_CAUSE)),
        )
        _reject(INVALID, journal, _v1_entry(journal, invented))
        late = _terminal("restored", generation=5, decision=T3)
        _reject(INVALID, journal, _v1_entry(journal, late))
        early = _terminal("purged", generation=4, decision=T2)
        _reject(INVALID, journal, _v1_entry(journal, early))
        _push_v1(journal, _terminal("restored", generation=5, decision=T2))
        again = _terminal("restored", generation=6, decision=T2)
        _reject(INVALID, journal, _v1_entry(journal, again))


# ---------------------------------------------------------------------------
# candidacy guard
# ---------------------------------------------------------------------------


class TestCandidateHygiene:
    def test_module_imports_no_io_clock_or_provider_surface(self) -> None:
        allowed = {
            "__future__",
            "dataclasses",
            "datetime",
            "hashlib",
            "json",
            "re",
            "typing",
            "uuid",
        }
        relative = {"errors", "model"}
        tree = ast.parse(CANDIDATE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name in allowed, alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    assert node.module in allowed, node.module
                else:
                    assert node.level == 1
                    assert node.module in relative, node.module

    def test_module_source_has_no_effectful_calls(self) -> None:
        source = CANDIDATE_PATH.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_SOURCE:
            assert re.search(pattern, source) is None, pattern

    def test_no_runtime_module_imports_the_candidate(self) -> None:
        offenders: list[str] = []
        for path in sorted((REPO_ROOT / "orchestrator").rglob("*.py")):
            if path == CANDIDATE_PATH:
                continue
            if "namespace_candidate" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_package_does_not_export_the_candidate(self) -> None:
        init = REPO_ROOT / "orchestrator" / "services" / "deletion_journal" / "__init__.py"
        assert "namespace_candidate" not in init.read_text(encoding="utf-8")
