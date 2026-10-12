"""Focused unit tests for the J1 deletion-journal structural model.

Covers canonical command digests, UTC normalization, closed enums, canonical
v1 frame encode/decode strictness, and the replay structural validator's
chaining, CAS and closed transition-grammar rules. Structural only: no
storage, no appliers, no wallclock reads.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from orchestrator.services.deletion_journal import model as model_module
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import (
    AckFields,
    Cause,
    Command,
    Entry,
    JournalState,
    ReadyFields,
    RequestFields,
    TerminalFields,
    command_digest,
    decode_entry,
    encode_entry,
)

UTC = timezone.utc
JOURNAL_ID = UUID("0ad00000-0000-0000-0000-0000000000aa")
ACCOUNT = UUID("ac000000-0000-0000-0000-000000000001")
OTHER_ACCOUNT = UUID("ac000000-0000-0000-0000-000000000002")
OTHER_CAUSE = UUID("0c000000-0000-0000-0000-0000000000cc")
ZERO = bytes(32)

INVALID = "invalid_entry"
CONFLICT = "conflict"

DAY = timedelta(days=1)
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 1, 8, 12, 0, 0, tzinfo=UTC)  # inside the 7-day window
T3 = datetime(2026, 1, 9, 12, 0, 0, tzinfo=UTC)  # exactly the 7-day deadline
T4 = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
WINDOW_END = T1 + timedelta(days=7)  # shared deadline for ready events


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


def _raises_code(code: str, fn: Any) -> None:
    with pytest.raises(JournalError) as excinfo:
        fn()
    assert _code(excinfo.value) == code


def _causes(*items: tuple[str, UUID]) -> tuple[Cause, ...]:
    return tuple(Cause(code, cid) for code, cid in items)


def _request_fields(**overrides: Any) -> RequestFields:
    params: dict[str, Any] = {
        "prior_status": "active",
        "prior_reason": None,
        "causes": (Cause("deletion", UUID("0b000000-0000-0000-0000-0000000000bb")),),
        "generation": 5,
        "drain_deadline": T0 + DAY,
        "revocation_deadline": T0 + 2 * DAY,
        "billing_deadline": T0 + 3 * DAY,
        "no_grace": False,
        "reason": None,
    }
    params.update(overrides)
    return RequestFields(**params)


def _ready_fields(**overrides: Any) -> ReadyFields:
    params: dict[str, Any] = {"started_at": T1, "deadline": WINDOW_END}
    params.update(overrides)
    return ReadyFields(**params)


def _ack_fields(**overrides: Any) -> AckFields:
    params: dict[str, Any] = {"acknowledged_at": T2}
    params.update(overrides)
    return AckFields(**params)


def _terminal_fields(**overrides: Any) -> TerminalFields:
    params: dict[str, Any] = {"causes": (), "generation": 6, "reason": None}
    params.update(overrides)
    return TerminalFields(**params)


def _cmd(event_type: str, fields: Any, **overrides: Any) -> Command:
    params: dict[str, Any] = {
        "account_id": ACCOUNT,
        "operation_id": UUID("0b000000-0000-0000-0000-0000000000bb"),
        "event_type": event_type,
        "decision_at": T0,
        "fields": fields,
    }
    params.update(overrides)
    return Command(**params)


def _del_ready_grace(op: UUID, decision: datetime = T1) -> Command:
    return _cmd(
        "grace_started",
        _ready_fields(started_at=decision, deadline=decision + timedelta(days=7)),
        operation_id=op,
        decision_at=decision,
    )


def _reset_ready(op: UUID, decision: datetime = T1) -> Command:
    return _cmd(
        "reset_report_ready",
        _ready_fields(started_at=decision, deadline=decision + timedelta(days=7)),
        operation_id=op,
        decision_at=decision,
    )


# A suspended-account request records prior causes plus the operation's own
# cause, whose id equals the operation id (expected fixture convention).
def _suspended_request_fields(op: UUID, **overrides: Any) -> RequestFields:
    overrides.setdefault("prior_status", "suspended")
    overrides.setdefault("prior_reason", "abuse")
    overrides.setdefault("causes", _causes(("abuse", OTHER_CAUSE), ("deletion", op)))
    return _request_fields(**overrides)


def _reset_request_fields(op: UUID, generation: int = 10, **overrides: Any) -> RequestFields:
    overrides.setdefault("prior_status", "suspended")
    overrides.setdefault("prior_reason", "abuse")
    overrides.setdefault("causes", _causes(("abuse", OTHER_CAUSE), ("reset", op)))
    overrides.setdefault("generation", generation)
    return _request_fields(**overrides)


_AUTO = "auto"


def _raw_entry(
    state: JournalState,
    command: Command,
    *,
    event_id: UUID | None = None,
    journal_id: UUID = JOURNAL_ID,
    account_prev: int | None | str = _AUTO,
    operation_prev: int | None | str = _AUTO,
    seq: int | None = None,
) -> Entry:
    seq = seq if seq is not None else len(state.entries) + 1
    previous = state.entries[-1].digest if state.entries else ZERO
    aprev: int | None
    if account_prev == _AUTO:
        aprev = state.account_heads.get(command.account_id)
    else:
        assert not isinstance(account_prev, str)
        aprev = account_prev
    oprev: int | None
    if operation_prev == _AUTO:
        oprev = state.operation_heads.get(command.operation_id)
    else:
        assert not isinstance(operation_prev, str)
        oprev = operation_prev
    cd = command_digest(command)
    return Entry(
        journal_id=journal_id,
        seq=seq,
        event_id=event_id or uuid4(),
        command=command,
        account_prev=aprev,
        operation_prev=oprev,
        previous_digest=previous,
        command_digest=cd,
        digest=hashlib.sha256(seq.to_bytes(8, "big") + previous + cd).digest(),
    )


def _push(
    state: JournalState,
    command: Command,
    *,
    event_id: UUID | None = None,
    account_prev: int | None | str = _AUTO,
    operation_prev: int | None | str = _AUTO,
) -> Entry:
    entry = _raw_entry(
        state,
        command,
        event_id=event_id,
        account_prev=account_prev,
        operation_prev=operation_prev,
    )
    state.accept(entry)
    return entry


def _open_grace_deletion(
    state: JournalState,
    op: UUID | None = None,
    *,
    generation: int = 5,
    decision: datetime = T0,
    no_grace: bool = False,
    causes: tuple[Cause, ...] | None = None,
    prior_status: str = "active",
    prior_reason: str | None = None,
    reason: str | None = None,
) -> UUID:
    op = op if op is not None else uuid4()
    _push(
        state,
        _cmd(
            "requested",
            _request_fields(
                prior_status=prior_status,
                prior_reason=prior_reason,
                causes=causes if causes is not None else _causes(("deletion", op)),
                generation=generation,
                no_grace=no_grace,
                reason=reason,
            ),
            operation_id=op,
            decision_at=decision,
        ),
    )
    if not no_grace:
        _push(state, _del_ready_grace(op))
    return op


def _close_restored(
    state: JournalState,
    op: UUID,
    *,
    generation: int = 6,
    causes: tuple[Cause, ...] = (),
    decision: datetime = T2,
) -> None:
    _push(
        state,
        _cmd(
            "restored",
            _terminal_fields(generation=generation, causes=causes),
            operation_id=op,
            decision_at=decision,
        ),
    )


def _close_purged(
    state: JournalState,
    op: UUID,
    generation: int,
    *,
    causes: tuple[Cause, ...] = (),
    decision: datetime = T3,
) -> None:
    _push(
        state,
        _cmd(
            "purged",
            _terminal_fields(generation=generation, causes=causes),
            operation_id=op,
            decision_at=decision,
        ),
    )


def _open_reset_requested(
    state: JournalState,
    op: UUID | None = None,
    *,
    generation: int = 10,
    decision: datetime = T0,
    causes: tuple[Cause, ...] | None = None,
) -> UUID:
    op = op if op is not None else uuid4()
    fields = (
        _reset_request_fields(op, generation=generation)
        if causes is None
        else _reset_request_fields(op, generation=generation, causes=causes)
    )
    _push(state, _cmd("reset_requested", fields, operation_id=op, decision_at=decision))
    return op


def _open_reset_to_ready(
    state: JournalState,
    op: UUID,
    *,
    generation: int = 10,
    decision: datetime = T0,
) -> UUID:
    _open_reset_requested(state, op, generation=generation, decision=decision)
    _push(state, _reset_ready(op, decision=T1))
    return op


def _reset_ack(op: UUID, decision: datetime = T2) -> Command:
    return _cmd(
        "reset_acknowledged",
        _ack_fields(acknowledged_at=decision),
        operation_id=op,
        decision_at=decision,
    )


def _reset_cancel(
    op: UUID,
    reason: str | None,
    *,
    decision: datetime = T2,
    generation: int = 11,
) -> Command:
    fields = _terminal_fields(
        generation=generation, reason=reason, causes=_causes(("abuse", OTHER_CAUSE))
    )
    return _cmd("reset_cancelled", fields, operation_id=op, decision_at=decision)


def _reset_purge(
    op: UUID,
    *,
    generation: int = 10,
    decision: datetime = T2 + DAY,
    causes: tuple[Cause, ...] = (Cause("abuse", OTHER_CAUSE),),
) -> Command:
    fields = _terminal_fields(generation=generation, causes=causes)
    return _cmd("reset_purged", fields, operation_id=op, decision_at=decision)


# ---------------------------------------------------------------------------
# canonical command digests
# ---------------------------------------------------------------------------


class TestCommandDigest:
    def test_deterministic_and_sized(self) -> None:
        command = _cmd("requested", _request_fields())
        assert command_digest(command) == command_digest(command)
        assert len(command_digest(command)) == 32

    def test_retry_identity_ignores_allocations(self) -> None:
        command = _cmd("requested", _request_fields())
        retried = _cmd(
            "requested",
            _request_fields(
                generation=99,
                drain_deadline=T0 + 5 * DAY,
                revocation_deadline=T0 + 6 * DAY,
                billing_deadline=T0 + 7 * DAY,
            ),
            decision_at=T0 + 9 * DAY,
        )
        assert command_digest(command) == command_digest(retried)

    def test_ready_and_ack_datetime_fields_excluded(self) -> None:
        grace_a = _cmd("grace_started", _ready_fields(), decision_at=T1)
        grace_b = _cmd(
            "grace_started",
            _ready_fields(started_at=T2, deadline=T2 + timedelta(days=7)),
            decision_at=T2,
        )
        assert command_digest(grace_a) == command_digest(grace_b)
        ack_a = _cmd("reset_acknowledged", _ack_fields(), decision_at=T2)
        ack_b = _cmd("reset_acknowledged", _ack_fields(acknowledged_at=T4), decision_at=T4)
        assert command_digest(ack_a) == command_digest(ack_b)

    def test_includes_prior_status_reason_no_grace_reason(self) -> None:
        base = _cmd("requested", _request_fields())
        variants = [
            _cmd(
                "requested",
                _request_fields(prior_status="suspended", prior_reason="billing"),
            ),
            _cmd(
                "requested",
                _request_fields(prior_status="suspended", prior_reason="reset"),
            ),
            _cmd("requested", _request_fields(no_grace=True, reason="legal")),
            _cmd("requested", _request_fields(reason="legal")),
            _cmd(
                "requested",
                _request_fields(
                    prior_status="suspended", prior_reason="billing", no_grace=True, reason="abuse"
                ),
            ),
        ]
        for variant in variants:
            assert command_digest(base) != command_digest(variant)
            for other in variants:
                if variant is not other:
                    assert command_digest(variant) != command_digest(other)

    def test_cause_set_order_insensitive_but_membership_sensitive(self) -> None:
        billing_id = uuid4()
        first = _request_fields(causes=_causes(("abuse", OTHER_CAUSE), ("billing", billing_id)))
        reordered = _request_fields(causes=_causes(("billing", billing_id), ("abuse", OTHER_CAUSE)))
        assert command_digest(_cmd("requested", first)) == command_digest(
            _cmd("requested", reordered)
        )
        changed = _request_fields(causes=_causes(("abuse", OTHER_CAUSE), ("billing", uuid4())))
        assert command_digest(_cmd("requested", first)) != command_digest(
            _cmd("requested", changed)
        )

    def test_command_identity_changes_digest(self) -> None:
        command = _cmd("requested", _request_fields())
        others = [
            _cmd("requested", _request_fields(), account_id=OTHER_ACCOUNT),
            _cmd("requested", _request_fields(), operation_id=uuid4()),
            _cmd("reset_requested", _request_fields()),
        ]
        for other in others:
            assert command_digest(command) != command_digest(other)


class TestNormalization:
    def test_naive_decision_rejected(self) -> None:
        _raises_code(
            INVALID,
            lambda: _cmd("requested", _request_fields(), decision_at=datetime(2026, 1, 1)),
        )

    def test_naive_request_deadlines_rejected(self) -> None:
        for field_name in ("drain_deadline", "revocation_deadline", "billing_deadline"):
            _raises_code(
                INVALID,
                lambda name=field_name: _request_fields(**{name: datetime(2026, 2, 1)}),
            )

    def test_naive_ready_and_ack_rejected(self) -> None:
        _raises_code(
            INVALID,
            lambda: _ready_fields(started_at=datetime(2026, 1, 2), deadline=WINDOW_END),
        )
        _raises_code(INVALID, lambda: _ack_fields(acknowledged_at=datetime(2026, 1, 8)))

    def test_offsets_normalized_to_utc_for_public_commands(self) -> None:
        offset = timezone(timedelta(hours=1))
        offset_command = _cmd("requested", _request_fields(), decision_at=T0.astimezone(offset))
        utc_command = _cmd("requested", _request_fields())
        assert offset_command.decision_at.tzinfo is not None
        assert offset_command.decision_at == T0
        assert command_digest(offset_command) == command_digest(utc_command)

    def test_microseconds_preserved(self) -> None:
        decision = T2.replace(microsecond=123456)
        command = _cmd(
            "reset_acknowledged",
            _ack_fields(acknowledged_at=decision),
            decision_at=decision,
        )
        assert isinstance(command.fields, AckFields)
        assert command.fields.acknowledged_at == decision
        entry = _raw_entry(JournalState(), command)
        assert b"12:00:00.123456" in encode_entry(entry)

    def test_replay_never_reads_the_clock(self) -> None:
        # Deterministic historical timestamps (year 2000) are accepted on the
        # same structural rules as recent ones: only recorded times matter.
        old = datetime(2000, 1, 1, 12, 0, 0, tzinfo=UTC)
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
                decision_at=old,
            ),
        )
        _push(
            state,
            _cmd(
                "grace_started",
                ReadyFields(started_at=old + DAY, deadline=old + DAY + timedelta(days=7)),
                operation_id=op,
                decision_at=old + DAY,
            ),
        )
        _push(
            state,
            _cmd(
                "restored",
                _terminal_fields(generation=6),
                operation_id=op,
                decision_at=old + 2 * DAY,
            ),
        )
        assert len(state.entries) == 3


# ---------------------------------------------------------------------------
# typed-field validation (construction)
# ---------------------------------------------------------------------------


class TestRequestFieldValidation:
    def test_cause_code_closed(self) -> None:
        _raises_code(INVALID, lambda: _request_fields(causes=_causes(("data", OTHER_CAUSE))))

    def test_cause_ids_unique(self) -> None:
        same_id = _causes(("abuse", OTHER_CAUSE), ("billing", OTHER_CAUSE))
        _raises_code(INVALID, lambda: _request_fields(causes=same_id))
        exact_pair = _causes(("abuse", OTHER_CAUSE), ("abuse", OTHER_CAUSE))
        _raises_code(INVALID, lambda: _request_fields(causes=exact_pair))

    def test_cause_limit_boundaries(self) -> None:
        causes = tuple(
            Cause("billing" if i % 2 == 0 else "abuse", UUID(f"00000000-0000-0000-0000-{i:012x}"))
            for i in range(128)
        )
        assert len(_request_fields(causes=causes).causes) == 128
        many = causes + (Cause("operator", UUID("00000000-0000-0000-0000-ffffffffffff")),)
        _raises_code(INVALID, lambda: _request_fields(causes=many))

    def test_unsorted_causes_canonicalized(self) -> None:
        deletion_id = uuid4()
        fields = _request_fields(causes=_causes(("deletion", deletion_id), ("abuse", OTHER_CAUSE)))
        assert fields.causes == _causes(("abuse", OTHER_CAUSE), ("deletion", deletion_id))

    def test_generation_bounds(self) -> None:
        _raises_code(INVALID, lambda: _request_fields(generation=0))
        _raises_code(INVALID, lambda: _request_fields(generation=-5))
        _raises_code(INVALID, lambda: _request_fields(generation=2**63))
        _raises_code(INVALID, lambda: _request_fields(generation=True))

    def test_no_grace_must_be_bool(self) -> None:
        _raises_code(INVALID, lambda: _request_fields(no_grace=1))
        _raises_code(INVALID, lambda: _request_fields(no_grace="no"))

    def test_reason_closed_minor_values(self) -> None:
        _raises_code(INVALID, lambda: _request_fields(reason="grumpy"))
        assert _request_fields(reason="legal").reason == "legal"
        assert _request_fields(reason="abuse").reason == "abuse"

    def test_prior_status_enum(self) -> None:
        _raises_code(INVALID, lambda: _request_fields(prior_status="paused"))
        assert _request_fields(prior_status="suspended", prior_reason="billing")

    def test_prior_reason_closed(self) -> None:
        _raises_code(INVALID, lambda: _request_fields(prior_reason="freeform"))

    def test_active_contradicts_prior_reason(self) -> None:
        _raises_code(INVALID, lambda: _request_fields(prior_reason="abuse"))


class TestCommandAndReadyValidation:
    def test_unknown_event_type_rejected(self) -> None:
        _raises_code(INVALID, lambda: _cmd("suspended", _request_fields()))

    def test_field_kind_mismatch_rejected(self) -> None:
        _raises_code(INVALID, lambda: _cmd("requested", _ack_fields()))
        _raises_code(INVALID, lambda: _cmd("grace_started", _request_fields()))
        _raises_code(INVALID, lambda: _cmd("restored", _ready_fields()))
        _raises_code(INVALID, lambda: _cmd("reset_acknowledged", _terminal_fields()))
        _raises_code(INVALID, lambda: _cmd("purged", _ack_fields()))

    def test_ready_requires_exact_seven_day_window(self) -> None:
        _raises_code(
            INVALID,
            lambda: _ready_fields(started_at=T1, deadline=WINDOW_END - timedelta(microseconds=1)),
        )
        _raises_code(
            INVALID,
            lambda: _ready_fields(started_at=T1, deadline=WINDOW_END + timedelta(microseconds=1)),
        )
        assert _ready_fields().deadline == WINDOW_END

    def test_reset_requested_rejects_request_reason(self) -> None:
        op = uuid4()
        _raises_code(
            INVALID,
            lambda: _cmd(
                "reset_requested",
                _request_fields(
                    reason="legal",
                    prior_status="suspended",
                    prior_reason="abuse",
                    causes=_causes(("reset", op)),
                ),
                operation_id=op,
            ),
        )

    def test_terminal_reason_pairing(self) -> None:
        op = uuid4()
        _raises_code(
            INVALID,
            lambda: _cmd("reset_cancelled", _terminal_fields(reason=None), operation_id=op),
        )
        _raises_code(
            INVALID,
            lambda: _cmd("reset_cancelled", _terminal_fields(reason="boss"), operation_id=op),
        )
        assert _cmd("reset_cancelled", _terminal_fields(reason="owner"), operation_id=op)
        assert _cmd("reset_cancelled", _terminal_fields(reason="timeout"), operation_id=op)
        for event in ("restored", "purged", "reset_purged"):
            _raises_code(
                INVALID,
                lambda event=event: _cmd(event, _terminal_fields(reason="owner"), operation_id=op),
            )


class TestEntryConstruction:
    def _entry(self, **overrides: Any) -> Entry:
        params: dict[str, Any] = {
            "journal_id": JOURNAL_ID,
            "seq": 1,
            "event_id": uuid4(),
            "command": _cmd("requested", _request_fields()),
            "account_prev": None,
            "operation_prev": None,
            "previous_digest": ZERO,
            "command_digest": hashlib.sha256(b"x").digest(),
            "digest": hashlib.sha256(b"y").digest(),
        }
        params.update(overrides)
        return Entry(**params)

    def test_digests_must_be_32_bytes(self) -> None:
        _raises_code(INVALID, lambda: self._entry(digest=b"\x01"))
        _raises_code(INVALID, lambda: self._entry(digest=b"\x01" * 33))
        _raises_code(INVALID, lambda: self._entry(previous_digest="0" * 64))
        _raises_code(INVALID, lambda: self._entry(command_digest=None))

    def test_seq_and_predecessor_bounds(self) -> None:
        _raises_code(INVALID, lambda: self._entry(seq=True))
        _raises_code(INVALID, lambda: self._entry(seq=0))
        _raises_code(INVALID, lambda: self._entry(seq=2**63))
        _raises_code(INVALID, lambda: self._entry(account_prev=2**63))
        _raises_code(INVALID, lambda: self._entry(operation_prev=True))
        assert self._entry(operation_prev=None)

    def test_uuids_must_be_instances(self) -> None:
        _raises_code(INVALID, lambda: self._entry(journal_id=str(JOURNAL_ID)))
        _raises_code(INVALID, lambda: self._entry(event_id=JOURNAL_ID.hex))


# ---------------------------------------------------------------------------
# canonical framing
# ---------------------------------------------------------------------------


def _rich_command() -> Command:
    op = UUID("0b000000-0000-0000-0000-0000000000cc")
    suspended = RequestFields(
        prior_status="suspended",
        prior_reason="abuse",
        causes=_causes(("abuse", OTHER_CAUSE), ("deletion", op)),
        generation=5,
        drain_deadline=(T0 + DAY).replace(microsecond=250000),
        revocation_deadline=T0 + 2 * DAY,
        billing_deadline=T0 + 3 * DAY,
        no_grace=False,
        reason=None,
    )
    return _cmd("requested", suspended, operation_id=op)


FOOTER = hashlib.sha256(b"footer").digest()


def _dump(obj: Any) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class TestEncodeDecode:
    def test_roundtrip_equality(self) -> None:
        entry = _raw_entry(JournalState(), _rich_command())
        decoded = decode_entry(encode_entry(entry), FOOTER)
        assert decoded.digest == FOOTER
        assert decoded.digest != entry.digest  # footers differ by construction
        again = decode_entry(encode_entry(entry), entry.digest)
        assert again == entry  # identical footer reconstructs an equal entry

    def test_encode_deterministic(self) -> None:
        entry = _raw_entry(JournalState(), _rich_command())
        assert encode_entry(entry) == encode_entry(entry)

    def test_hex_hashes_and_version_present(self) -> None:
        entry = _raw_entry(JournalState(), _rich_command())
        payload = encode_entry(entry)
        assert b'"version":1' in payload
        assert entry.previous_digest.hex().encode() in payload
        assert entry.command_digest.hex().encode() in payload

    def test_digest_footer_never_serialized(self) -> None:
        entry = _raw_entry(JournalState(), _rich_command())
        payload = encode_entry(entry)
        assert entry.digest.hex().encode() not in payload
        assert b'"digest"' not in payload

    def test_causes_serialized_canonical_sorted(self) -> None:
        op = uuid4()
        fields = _reset_request_fields(op, generation=7)
        command = _cmd("reset_requested", fields, operation_id=op)
        payload = encode_entry(_raw_entry(JournalState(), command, event_id=uuid4()))
        text = payload.decode("utf-8")
        index = text.index('"causes":[')
        window = text[index : index + 200]
        assert window.index('"abuse"') < window.index('"reset"')

    def test_micro_precision_survives_reencode(self) -> None:
        entry = _raw_entry(JournalState(), _rich_command())
        payload = encode_entry(decode_entry(encode_entry(entry), ZERO))
        assert b"12:00:00.250000" in payload


class TestDecodeRejections:
    @pytest.fixture()
    def payload(self) -> bytes:
        entry = _raw_entry(JournalState(), _rich_command(), seq=2, account_prev=1)
        # Give the fixture a non-zero predecessor hash so case variants actually
        # change its canonical rendering.
        entry = dc_replace(
            entry,
            previous_digest=hashlib.sha256(b"nonzero-predecessor").digest(),
            digest=hashlib.sha256(b"nonzero-frame").digest(),
        )
        return encode_entry(entry)

    def test_footer_and_payload_types(self, payload: bytes) -> None:
        wrong_str = cast(bytes, "wrong-type-on-purpose")
        _raises_code(INVALID, lambda: decode_entry(payload, b"\x01" * 31))
        _raises_code(INVALID, lambda: decode_entry(payload, b"\x01" * 33))
        _raises_code(INVALID, lambda: decode_entry(payload, wrong_str))
        _raises_code(INVALID, lambda: decode_entry(wrong_str, FOOTER))

    def test_invalid_utf8_rejected(self) -> None:
        _raises_code(INVALID, lambda: decode_entry(b'{"version":\xff}', FOOTER))

    def test_nan_and_infinity_constants_rejected(self) -> None:
        _raises_code(INVALID, lambda: decode_entry(b'{"seq":NaN}', FOOTER))
        _raises_code(INVALID, lambda: decode_entry(b'{"seq":Infinity}', FOOTER))
        _raises_code(INVALID, lambda: decode_entry(b'{"seq":-Infinity}', FOOTER))

    def test_duplicate_top_level_key(self, payload: bytes) -> None:
        stabbed = payload.replace(b'{"account_prev":', b'{"account_prev":1,"account_prev":1,', 1)
        assert stabbed != payload
        _raises_code(INVALID, lambda: decode_entry(stabbed, FOOTER))

    def test_duplicate_nested_key(self, payload: bytes) -> None:
        stabbed = payload.replace(b'"kind":"request",', b'"kind":"request","kind":"request",', 1)
        _raises_code(INVALID, lambda: decode_entry(stabbed, FOOTER))

    def test_missing_top_level_key(self, payload: bytes) -> None:
        _raises_code(INVALID, lambda: decode_entry(payload.replace(b',"version":1}', b"}"), FOOTER))

    def test_extra_top_level_key(self, payload: bytes) -> None:
        extra = payload.replace(b'{"account_prev":1,', b'{"aaa":1,"account_prev":1,')
        _raises_code(INVALID, lambda: decode_entry(extra, FOOTER))

    def test_non_canonical_key_order(self, payload: bytes) -> None:
        obj = json.loads(payload)
        reversed_order = {key: obj[key] for key in reversed(list(obj))}
        reordered = json.dumps(
            reversed_order, ensure_ascii=False, separators=(",", ":"), sort_keys=False
        ).encode("utf-8")
        assert reordered != payload
        _raises_code(INVALID, lambda: decode_entry(reordered, FOOTER))

    def test_non_canonical_whitespace(self, payload: bytes) -> None:
        _raises_code(INVALID, lambda: decode_entry(payload + b" ", FOOTER))

    def test_version_variants(self, payload: bytes) -> None:
        _raises_code(
            INVALID,
            lambda: decode_entry(payload.replace(b'"version":1}', b'"version":2}'), FOOTER),
        )
        _raises_code(
            INVALID,
            lambda: decode_entry(payload.replace(b'"version":1}', b'"version":true}'), FOOTER),
        )

    def test_seq_variants(self, payload: bytes) -> None:
        for substitution in (
            b'"seq":"3"',
            b'"seq":true',
            b'"seq":1.0',
            b'"seq":0',
            b'"seq":9223372036854775808',
        ):
            built = re.sub(rb'"seq":\d+', substitution, payload)
            with pytest.raises(JournalError) as excinfo:
                decode_entry(built, FOOTER)
            assert _code(excinfo.value) == INVALID

    def _rebuild(self, payload: bytes, mutate: Any) -> bytes:
        obj = json.loads(payload)
        mutate(obj)
        return _dump(obj)

    def test_predecessor_variants(self, payload: bytes) -> None:
        for bad in (2**63, True, 1.5, "2", -1):
            built = self._rebuild(
                payload, lambda obj, value=bad: obj.update({"account_prev": value})
            )
            _raises_code(INVALID, lambda built=built: decode_entry(built, FOOTER))

    def test_hex_digest_cases(self, payload: bytes) -> None:
        match = re.search(rb'"previous_digest":"([0-9a-f]{64})"', payload)
        assert match is not None
        original = match.group(1)
        for replacement in (original.upper(), original[:63], b"z" * 64):
            built = payload.replace(
                b'"previous_digest":"' + original,
                b'"previous_digest":"' + replacement,
            )
            with pytest.raises(JournalError) as excinfo:
                decode_entry(built, FOOTER)
            assert _code(excinfo.value) == INVALID

    def test_uuid_canonical_cases(self, payload: bytes) -> None:
        pattern = re.compile(rb'"(journal_id|event_id|account_id|operation_id)":"([0-9a-f-]{36})"')

        def upcase(match: re.Match[bytes]) -> bytes:
            return b'"' + match.group(1) + b'":"' + match.group(2).upper() + b'"'

        def mangle(match: re.Match[bytes]) -> bytes:
            return b'"' + match.group(1) + b'":"not-canonical-uuid-00000000000000000000"'

        upcased = pattern.sub(upcase, payload)
        assert upcased != payload
        _raises_code(INVALID, lambda: decode_entry(upcased, FOOTER))
        broken = pattern.sub(mangle, payload)
        _raises_code(INVALID, lambda: decode_entry(broken, FOOTER))

    def test_datetime_canonical_cases(self, payload: bytes) -> None:
        token = b'"decision_at":"' + T0.isoformat().encode() + b'"'
        assert token in payload
        for bad_value in (
            "2026-01-01T12:00:00",
            "2026-01-01T13:00:00+01:00",
            "2026-01-01T12:00:00Z",
        ):
            built = payload.replace(token, b'"decision_at":"' + bad_value.encode() + b'"')
            with pytest.raises(JournalError) as excinfo:
                decode_entry(built, FOOTER)
            assert _code(excinfo.value) == INVALID

    def test_unknown_event_and_kind_mismatch(self, payload: bytes) -> None:
        rebuilt = payload.replace(b'"event_type":"requested"', b'"event_type":"other_event"')
        _raises_code(INVALID, lambda: decode_entry(rebuilt, FOOTER))
        swapped = payload.replace(b'"kind":"request"', b'"kind":"terminal"')
        _raises_code(INVALID, lambda: decode_entry(swapped, FOOTER))

    def test_fields_extra_and_types(self, payload: bytes) -> None:
        def mutate_extra(obj: dict[str, Any]) -> None:
            obj["command"]["fields"]["extra"] = 1

        _raises_code(INVALID, lambda: decode_entry(self._rebuild(payload, mutate_extra), FOOTER))

        def mutate_no_grace(obj: dict[str, Any]) -> None:
            obj["command"]["fields"]["no_grace"] = 1

        _raises_code(INVALID, lambda: decode_entry(self._rebuild(payload, mutate_no_grace), FOOTER))

    def test_prior_enums(self, payload: bytes) -> None:
        _raises_code(
            INVALID,
            lambda: decode_entry(
                payload.replace(b'"prior_reason":"abuse"', b'"prior_reason":"freeform"'), FOOTER
            ),
        )
        _raises_code(
            INVALID,
            lambda: decode_entry(
                payload.replace(b'"prior_status":"suspended"', b'"prior_status":"paused"'), FOOTER
            ),
        )

    def test_request_reason_enum(self, payload: bytes) -> None:
        _raises_code(
            INVALID,
            lambda: decode_entry(payload.replace(b'"reason":null', b'"reason":"grumpy"'), FOOTER),
        )

    def test_cause_payload_cases(self, payload: bytes) -> None:
        def mutate_code(obj: dict[str, Any]) -> None:
            obj["command"]["fields"]["causes"][0]["code"] = "data"

        _raises_code(INVALID, lambda: decode_entry(self._rebuild(payload, mutate_code), FOOTER))

        def mutate_id(obj: dict[str, Any]) -> None:
            obj["command"]["fields"]["causes"][0]["id"] = 42

        _raises_code(INVALID, lambda: decode_entry(self._rebuild(payload, mutate_id), FOOTER))

        def unsorted(obj: dict[str, Any]) -> None:
            obj["command"]["fields"]["causes"] = list(reversed(obj["command"]["fields"]["causes"]))

        _raises_code(INVALID, lambda: decode_entry(self._rebuild(payload, unsorted), FOOTER))

        def duplicated_ids(obj: dict[str, Any]) -> None:
            base = obj["command"]["fields"]["causes"]
            obj["command"]["fields"]["causes"] = base + [dict(base[0])]

        _raises_code(INVALID, lambda: decode_entry(self._rebuild(payload, duplicated_ids), FOOTER))

        def too_many(obj: dict[str, Any]) -> None:
            obj["command"]["fields"]["causes"] = [{"code": "abuse", "id": "0" * 36}] * 129

        _raises_code(INVALID, lambda: decode_entry(self._rebuild(payload, too_many), FOOTER))

    def test_generation_variants(self, payload: bytes) -> None:
        for bad in (0, True, 2**63, 1.5):
            built = self._rebuild(
                payload,
                lambda obj, value=bad: obj["command"]["fields"].update({"generation": value}),
            )
            with pytest.raises(JournalError) as excinfo:
                decode_entry(built, FOOTER)
            assert _code(excinfo.value) == INVALID

    def test_recorded_command_digest_mismatch(self, payload: bytes) -> None:
        other = hashlib.sha256(b"other").digest().hex().encode()
        rebuilt = re.sub(
            rb'"command_digest":"[0-9a-f]{64}"', b'"command_digest":"' + other + b'"', payload
        )
        _raises_code(INVALID, lambda: decode_entry(rebuilt, FOOTER))

    def test_semantic_command_digest_mismatch(self, payload: bytes) -> None:
        rebuilt = payload.replace(b'"reason":null', b'"reason":"legal"')
        with pytest.raises(JournalError) as excinfo:
            decode_entry(rebuilt, FOOTER)
        assert _code(excinfo.value) == INVALID  # caller args changed under old digest

    def test_cancel_reason_variants(self) -> None:
        state = JournalState()
        op = _open_reset_to_ready(state, uuid4(), generation=10)
        payload = encode_entry(_raw_entry(state, _reset_cancel(op, "owner")))
        _raises_code(
            INVALID,
            lambda: decode_entry(payload.replace(b'"reason":"owner"', b'"reason":"boss"'), FOOTER),
        )
        _raises_code(
            INVALID,
            lambda: decode_entry(payload.replace(b'"reason":"owner"', b'"reason":null'), FOOTER),
        )

    def test_terminal_reason_must_be_absent(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op)
        payload = encode_entry(
            _raw_entry(
                state,
                _cmd("purged", _terminal_fields(generation=5), operation_id=op, decision_at=T3),
            )
        )

        def inject(obj: dict[str, Any]) -> None:
            obj["command"]["fields"]["reason"] = "owner"

        rebuilt = self._rebuild(payload, inject)
        _raises_code(INVALID, lambda: decode_entry(rebuilt, FOOTER))


# ---------------------------------------------------------------------------
# replay structural validator
# ---------------------------------------------------------------------------


class TestJournalStateReplayAccepted:
    def test_state_updates_and_chain_head(self) -> None:
        state = JournalState()
        op = uuid4()
        state_before = state.entries
        first = _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        assert state.entries is state_before
        first_digest = hashlib.sha256(
            first.seq.to_bytes(8, "big") + ZERO + first.command_digest
        ).digest()
        assert first.digest == first_digest
        second = _push(state, _del_ready_grace(op))
        assert state.account_heads[ACCOUNT] == second.seq
        assert state.operation_heads[op] == second.seq
        assert state.events[first.event_id] == first
        assert state.events[second.event_id] == second
        assert state.entries == [first, second]
        assert state.head_digest == second.digest
        assert second.previous_digest == first.digest

    def test_suspended_account_restore_flow_keeps_other_causes(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(
            state,
            op,
            generation=5,
            prior_status="suspended",
            prior_reason="abuse",
            causes=_causes(("abuse", OTHER_CAUSE), ("deletion", op)),
        )
        _close_restored(state, op, generation=6, causes=_causes(("abuse", OTHER_CAUSE)))
        assert len(state.entries) == 3

    def test_full_reset_flow(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_requested(state, op, generation=10)
        _push(state, _reset_ready(op))
        _push(state, _reset_ack(op, decision=T2))
        _push(state, _reset_purge(op, generation=10, decision=T4))
        assert state.operation_heads[op] == 4

    def test_new_operation_after_restored(self) -> None:
        state = JournalState()
        first = _open_grace_deletion(state)
        _close_restored(state, first, generation=6)
        second = _open_grace_deletion(state, None, generation=7)
        _close_restored(state, second, generation=8)
        _open_reset_requested(state, None, generation=9)

    def test_new_operation_after_reset_cancelled(self) -> None:
        state = JournalState()
        op = _open_reset_to_ready(state, uuid4(), generation=10)
        _push(state, _reset_cancel(op, "owner", decision=T2))
        _open_grace_deletion(state, None, generation=12)

    def test_new_operation_after_reset_purged(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_requested(state, op, generation=10)
        _push(state, _reset_ready(op))
        _push(state, _reset_ack(op, decision=T2))
        _push(state, _reset_purge(op, generation=10, decision=T4))
        _open_grace_deletion(state, None, generation=11)

    def test_zero_grace_direct_purge(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op, generation=5, no_grace=True, reason="legal")
        _close_purged(state, op, generation=5, decision=T1)

    def test_timely_logged_terminal_accepted_later(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op, generation=5)
        _push(
            state,
            _cmd(
                "purged",
                _terminal_fields(generation=5),
                operation_id=op,
                decision_at=WINDOW_END,
            ),
        )


class TestJournalStateChainAndCas:
    def test_first_entry_must_start_sequence_one(self) -> None:
        state = JournalState()
        command = _cmd("requested", _request_fields(generation=5))
        _raises_code(INVALID, lambda: state.accept(_raw_entry(state, command, seq=3)))
        state.accept(_raw_entry(state, command, seq=1))

    def _forged(self, entry: Entry, **overrides: Any) -> Entry:
        params = {
            "journal_id": entry.journal_id,
            "seq": entry.seq,
            "event_id": entry.event_id,
            "command": entry.command,
            "account_prev": entry.account_prev,
            "operation_prev": entry.operation_prev,
            "previous_digest": entry.previous_digest,
            "command_digest": entry.command_digest,
            "digest": entry.digest,
        }
        params.update(overrides)
        return Entry(**params)

    def test_wrong_previous_link_rejected(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        grace_entry = _raw_entry(state, _del_ready_grace(op))
        tampered = self._forged(
            grace_entry,
            previous_digest=hashlib.sha256(b"foreign").digest(),
        )
        _raises_code(INVALID, lambda: state.accept(tampered))

    def test_duplicate_event_id_rejected(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        grace_entry = _raw_entry(state, _del_ready_grace(op))
        replayed = self._forged(grace_entry, event_id=state.entries[0].event_id)
        _raises_code(INVALID, lambda: state.accept(replayed))

    def test_recomputed_command_digest_must_match(self) -> None:
        state = JournalState()
        command = _cmd("requested", _request_fields(generation=5))
        entry = _raw_entry(state, command)
        forged = self._forged(entry, command_digest=hashlib.sha256(b"tampered").digest(), seq=1)
        _raises_code(INVALID, lambda: state.accept(forged))

    def test_stale_account_predecessor_conflict(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        entry = _raw_entry(state, _del_ready_grace(op), account_prev=99)
        _raises_code(CONFLICT, lambda: state.accept(entry))

    def test_stale_operation_predecessor_conflict(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        entry = _raw_entry(state, _del_ready_grace(op), operation_prev=8)
        _raises_code(CONFLICT, lambda: state.accept(entry))

    def test_missing_predecessor_conflict(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        entry = _raw_entry(state, _del_ready_grace(op), operation_prev=None)
        _raises_code(CONFLICT, lambda: state.accept(entry))

    def test_must_advance_account_head_even_with_matching_op(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        # operation head matches op (1), but stale account head of None is refused
        entry = _raw_entry(
            state,
            _cmd("grace_started", _ready_fields(), operation_id=op, decision_at=T1),
            account_prev=None,
            operation_prev=1,
        )
        _raises_code(CONFLICT, lambda: state.accept(entry))

    def test_operation_rebound_to_other_account_conflict(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        entry = _raw_entry(
            state,
            _cmd(
                "grace_started",
                _ready_fields(),
                operation_id=op,
                account_id=OTHER_ACCOUNT,
                decision_at=T1,
            ),
            account_prev=None,
            operation_prev=1,
        )
        _raises_code(CONFLICT, lambda: state.accept(entry))


class TestJournalStateDeletionGrammar:
    def test_second_open_operation_rejected(self) -> None:
        state = JournalState()
        first_op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", first_op))),
                operation_id=first_op,
            ),
        )
        second_op = uuid4()
        entry = _raw_entry(
            state,
            _cmd(
                "requested",
                _request_fields(generation=6, causes=_causes(("deletion", second_op))),
                operation_id=second_op,
                decision_at=T0 + DAY,
            ),
        )
        _raises_code(INVALID, lambda: state.accept(entry))

    def test_normal_purge_requires_grace_started(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        early_purge = _raw_entry(
            state,
            _cmd("purged", _terminal_fields(generation=5), operation_id=op, decision_at=T1),
        )
        _raises_code(INVALID, lambda: state.accept(early_purge))

    def test_restore_before_grace_started_rejected(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        early_restore = _raw_entry(
            state,
            _cmd("restored", _terminal_fields(generation=6), operation_id=op, decision_at=T2),
        )
        _raises_code(INVALID, lambda: state.accept(early_restore))

    def test_no_grace_blocks_grace_started_and_restore(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op, generation=5, no_grace=True, reason="legal")
        late_grace = _raw_entry(state, _del_ready_grace(op))
        _raises_code(INVALID, lambda: state.accept(late_grace))
        state2 = JournalState()
        op2 = uuid4()
        _open_grace_deletion(state2, op2, generation=5, no_grace=True, reason="abuse")
        late_restore = _raw_entry(
            state2,
            _cmd("restored", _terminal_fields(generation=6), operation_id=op2, decision_at=T1),
        )
        _raises_code(INVALID, lambda: state2.accept(late_restore))

    def test_restore_needs_strictly_before_grace_deadline(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op)
        at_deadline = _raw_entry(
            state,
            _cmd(
                "restored", _terminal_fields(generation=6), operation_id=op, decision_at=WINDOW_END
            ),
        )
        _raises_code(INVALID, lambda: state.accept(at_deadline))
        before = JournalState()
        op2 = uuid4()
        _open_grace_deletion(before, op2)
        _close_restored(before, op2, generation=6, decision=WINDOW_END - timedelta(microseconds=1))

    def test_purge_needs_at_or_after_grace_deadline(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op)
        early = _raw_entry(
            state,
            _cmd("purged", _terminal_fields(generation=5), operation_id=op, decision_at=T2),
        )
        _raises_code(INVALID, lambda: state.accept(early))
        fresh = JournalState()
        op2 = uuid4()
        _open_grace_deletion(fresh, op2)
        _push(
            fresh,
            _cmd(
                "purged", _terminal_fields(generation=5), operation_id=op2, decision_at=WINDOW_END
            ),
        )

    def test_closed_operation_accepts_no_second_terminal(self) -> None:
        state = JournalState()
        op = _open_grace_deletion(state)
        _close_restored(state, op, generation=6)
        second = _raw_entry(
            state,
            _cmd("purged", _terminal_fields(generation=6), operation_id=op, decision_at=T3),
        )
        _raises_code(INVALID, lambda: state.accept(second))


class TestJournalStateResetGrammar:
    def test_cancel_before_report_ready_rejected(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_requested(state, op, generation=10)
        early_cancel = _raw_entry(state, _reset_cancel(op, "owner"))
        _raises_code(INVALID, lambda: state.accept(early_cancel))

    def test_owner_cancellation_deadline_rules(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_to_ready(state, op, generation=10)
        at_deadline = _raw_entry(state, _reset_cancel(op, "owner", decision=WINDOW_END))
        _raises_code(INVALID, lambda: state.accept(at_deadline))
        state2 = JournalState()
        op2 = uuid4()
        _open_reset_to_ready(state2, op2, generation=10)
        _push(state2, _reset_cancel(op2, "owner", decision=WINDOW_END - timedelta(microseconds=1)))

    def test_timeout_cancellation_deadline_rules(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_to_ready(state, op, generation=10)
        early = _raw_entry(state, _reset_cancel(op, "timeout", decision=T2))
        _raises_code(INVALID, lambda: state.accept(early))
        state2 = JournalState()
        op2 = uuid4()
        _open_reset_to_ready(state2, op2, generation=10)
        _push(state2, _reset_cancel(op2, "timeout", decision=WINDOW_END))

    def test_acknowledgement_only_before_deadline(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_to_ready(state, op, generation=10)
        late_ack = _raw_entry(state, _reset_ack(op, decision=WINDOW_END))
        _raises_code(INVALID, lambda: state.accept(late_ack))
        state2 = JournalState()
        op2 = uuid4()
        _open_reset_to_ready(state2, op2, generation=10)
        _push(
            state2,
            _reset_ack(op2, decision=WINDOW_END - timedelta(microseconds=1)),
        )

    def test_acknowledged_at_must_equal_decision(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_to_ready(state, op, generation=10)
        forged = _cmd(
            "reset_acknowledged",
            _ack_fields(acknowledged_at=T2 + DAY),
            operation_id=op,
            decision_at=T2,
        )
        mismatched = _raw_entry(state, forged)
        _raises_code(INVALID, lambda: state.accept(mismatched))

    def test_acknowledged_reset_cannot_cancel(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_to_ready(state, op, generation=10)
        _push(state, _reset_ack(op, decision=T2))
        late_cancel = _raw_entry(state, _reset_cancel(op, "owner"))
        _raises_code(INVALID, lambda: state.accept(late_cancel))

    def test_reset_purged_requires_acknowledged(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_requested(state, op, generation=10)
        before_ready = _raw_entry(state, _reset_purge(op))
        _raises_code(INVALID, lambda: state.accept(before_ready))
        state2 = JournalState()
        op2 = uuid4()
        _open_reset_to_ready(state2, op2, generation=10)
        before_ack = _raw_entry(state2, _reset_purge(op2))
        _raises_code(INVALID, lambda: state2.accept(before_ack))

    def test_event_families_never_mix(self) -> None:
        state = JournalState()
        op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=5, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        cross = _raw_entry(
            state,
            _cmd("reset_report_ready", _ready_fields(), operation_id=op, decision_at=T1),
        )
        _raises_code(INVALID, lambda: state.accept(cross))


class TestJournalStatePurgedAccount:
    def test_purged_account_cannot_receive_entries(self) -> None:
        state = JournalState()
        first = _open_grace_deletion(state)
        _close_purged(state, first, generation=5, decision=T3)
        second_op = uuid4()
        new_request = _raw_entry(
            state,
            _cmd(
                "requested",
                _request_fields(generation=6, causes=_causes(("deletion", second_op))),
                operation_id=second_op,
                decision_at=T3 + DAY,
            ),
        )
        _raises_code(INVALID, lambda: state.accept(new_request))
        resurrect = _raw_entry(
            state,
            _cmd(
                "restored", _terminal_fields(generation=6), operation_id=first, decision_at=T3 + DAY
            ),
        )
        _raises_code(INVALID, lambda: state.accept(resurrect))

    def test_closed_operation_identity_never_reopens(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op, generation=5)
        _close_restored(state, op, generation=6)
        reused = _raw_entry(
            state,
            _cmd(
                "requested",
                _request_fields(generation=7, causes=_causes(("deletion", op))),
                operation_id=op,
            ),
        )
        _raises_code(INVALID, lambda: state.accept(reused))


class TestJournalStateGenerationFencing:
    def test_request_fence_must_exceed_account_max(self) -> None:
        state = JournalState()
        first = _open_grace_deletion(state, generation=5)
        _close_restored(state, first, generation=6)
        second_op = uuid4()
        stale = _raw_entry(
            state,
            _cmd(
                "requested",
                _request_fields(generation=6, causes=_causes(("deletion", second_op))),
                operation_id=second_op,
                decision_at=T2 + DAY,
            ),
        )
        _raises_code(INVALID, lambda: state.accept(stale))
        higher_op = uuid4()
        _push(
            state,
            _cmd(
                "requested",
                _request_fields(generation=7, causes=_causes(("deletion", higher_op))),
                operation_id=higher_op,
                decision_at=T2 + DAY,
            ),
        )

    def test_restored_generation_must_be_newer(self) -> None:
        # ACCOUNT_DELETION_DESIGN §4.2: original restore creates a new generation;
        # §6.6 replay reinstalls that terminal's recorded generation, not fence gen.
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op, generation=5)
        backward = _raw_entry(
            state,
            _cmd("restored", _terminal_fields(generation=5), operation_id=op, decision_at=T2),
        )
        _raises_code(INVALID, lambda: state.accept(backward))

    def test_reset_cancelled_generation_must_be_newer(self) -> None:
        # Same §4.2 rebuilt-namespace rule, not a generation bump on replay.
        state = JournalState()
        op = uuid4()
        _open_reset_to_ready(state, op, generation=10)
        stale = _raw_entry(state, _reset_cancel(op, "owner", generation=10))
        _raises_code(INVALID, lambda: state.accept(stale))
        state2 = JournalState()
        op2 = uuid4()
        _open_reset_to_ready(state2, op2, generation=10)
        _push(state2, _reset_cancel(op2, "owner", generation=11))

    def test_reset_purged_retains_request_generation(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_to_ready(state, op, generation=10)
        _push(state, _reset_ack(op, decision=T2))
        drift = _raw_entry(state, _reset_purge(op, generation=12))
        _raises_code(INVALID, lambda: state.accept(drift))
        state2 = JournalState()
        op2 = uuid4()
        _open_reset_to_ready(state2, op2, generation=10)
        _push(state2, _reset_ack(op2, decision=T2))
        _push(state2, _reset_purge(op2, generation=10, decision=T4))

    def test_purged_retains_request_generation(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op, generation=5)
        drift = _raw_entry(
            state,
            _cmd("purged", _terminal_fields(generation=9), operation_id=op, decision_at=T3),
        )
        _raises_code(INVALID, lambda: state.accept(drift))


class TestJournalStateTerminalCauses:
    @pytest.mark.parametrize("event", ["requested", "reset_requested"])
    @pytest.mark.parametrize("mismatch", ["missing", "wrong_kind", "wrong_id", "unrelated_only"])
    def test_request_requires_its_exact_operation_cause(self, event: str, mismatch: str) -> None:
        state = JournalState()
        op = uuid4()
        own_code = "deletion" if event == "requested" else "reset"
        causes = {
            "missing": (),
            "wrong_kind": _causes(("reset" if own_code == "deletion" else "deletion", op)),
            "wrong_id": _causes((own_code, uuid4())),
            "unrelated_only": _causes(("abuse", OTHER_CAUSE)),
        }[mismatch]
        command = _cmd(event, _request_fields(causes=causes), operation_id=op)
        _raises_code(INVALID, lambda: state.accept(_raw_entry(state, command)))
        assert state.entries == []
        valid = _cmd(event, _request_fields(causes=_causes((own_code, op))), operation_id=op)
        _push(state, valid)
        assert len(state.entries) == 1

    @pytest.mark.parametrize("event", ["restored", "reset_cancelled", "reset_purged"])
    def test_closing_operation_cannot_erase_unrelated_suspension(self, event: str) -> None:
        state = JournalState()
        op = uuid4()
        if event == "restored":
            _open_grace_deletion(
                state, op, generation=5, causes=_causes(("abuse", OTHER_CAUSE), ("deletion", op))
            )
            fields = _terminal_fields(generation=6, causes=())
        else:
            _open_reset_to_ready(state, op)
            if event == "reset_purged":
                _push(state, _reset_ack(op))
            fields = _terminal_fields(
                generation=10 if event == "reset_purged" else 11,
                causes=(),
                reason="owner" if event == "reset_cancelled" else None,
            )
        bad = _raw_entry(state, _cmd(event, fields, operation_id=op, decision_at=T2))
        _raises_code(INVALID, lambda: state.accept(bad))

    def test_reset_rejects_deletion_only_zero_grace(self) -> None:
        _raises_code(
            INVALID,
            lambda: _cmd("reset_requested", _request_fields(no_grace=True, reason="legal")),
        )

    def test_terminal_cannot_invent_a_cause(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(state, op, generation=5)
        invented = _raw_entry(
            state,
            _cmd(
                "restored",
                _terminal_fields(generation=6, causes=_causes(("abuse", OTHER_CAUSE))),
                operation_id=op,
                decision_at=T2,
            ),
        )
        _raises_code(INVALID, lambda: state.accept(invented))

    def test_cancel_cannot_invent_a_cause(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_requested(state, op, generation=10)
        _push(state, _reset_ready(op))
        invented = _raw_entry(
            state,
            _cmd(
                "reset_cancelled",
                _terminal_fields(
                    generation=11, reason="owner", causes=_causes(("billing", uuid4()))
                ),
                operation_id=op,
                decision_at=T2,
            ),
        )
        _raises_code(INVALID, lambda: state.accept(invented))

    def test_restored_drops_own_deletion_cause(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(
            state,
            op,
            generation=5,
            prior_status="suspended",
            prior_reason="abuse",
            causes=_causes(("abuse", OTHER_CAUSE), ("deletion", op)),
        )
        keeps_own = _raw_entry(
            state,
            _cmd(
                "restored",
                _terminal_fields(
                    generation=6, causes=_causes(("abuse", OTHER_CAUSE), ("deletion", op))
                ),
                operation_id=op,
                decision_at=T2,
            ),
        )
        _raises_code(INVALID, lambda: state.accept(keeps_own))
        _close_restored(state, op, generation=6, causes=_causes(("abuse", OTHER_CAUSE)))

    def test_reset_purged_drops_own_reset_cause(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_reset_requested(state, op, generation=10)
        _push(state, _reset_ready(op))
        _push(state, _reset_ack(op, decision=T2))
        keeps_own = _raw_entry(
            state,
            _cmd(
                "reset_purged",
                _terminal_fields(
                    generation=10, causes=_causes(("abuse", OTHER_CAUSE), ("reset", op))
                ),
                operation_id=op,
                decision_at=T4,
            ),
        )
        _raises_code(INVALID, lambda: state.accept(keeps_own))
        _push(
            state,
            _cmd(
                "reset_purged",
                _terminal_fields(generation=10, causes=_causes(("abuse", OTHER_CAUSE))),
                operation_id=op,
                decision_at=T4,
            ),
        )

    def test_purged_may_keep_own_cause(self) -> None:
        state = JournalState()
        op = uuid4()
        _open_grace_deletion(
            state,
            op,
            generation=5,
            prior_status="suspended",
            prior_reason="abuse",
            causes=_causes(("abuse", OTHER_CAUSE), ("deletion", op)),
        )
        _close_purged(
            state,
            op,
            generation=5,
            decision=T3,
            causes=_causes(("abuse", OTHER_CAUSE), ("deletion", op)),
        )


class TestWallclockHygiene:
    @pytest.mark.parametrize("payload", [b'{"x":"\\ud800"}', b'{"x":' + b"9" * 5000 + b"}"])
    def test_unsafe_json_still_uses_closed_error(self, payload: bytes) -> None:
        _raises_code(INVALID, lambda: decode_entry(payload, FOOTER))

    def test_model_never_reads_the_clock(self) -> None:
        source = inspect.getsource(model_module)
        assert not re.search(r"datetime\.now\(|utcnow\(|date\.today\(|time\.time\(", source)
