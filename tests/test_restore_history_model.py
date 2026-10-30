"""Closed-format and no-replay evidence tests; no runtime/DB/provider calls."""

from dataclasses import replace
from uuid import UUID
import hashlib
import json
import struct

import pytest

from orchestrator.services.restore_history.model import (
    HistoryError,
    canonical,
    decode_entry,
    encode_entry,
    validate_chain,
    commitments,
)
from tests.restore_history_support import HISTORY, KEY, ZERO, accept, send, stop, begin, assessed


def entry(command, seq=1, previous=ZERO):
    return decode_entry(encode_entry(HISTORY, seq, previous, command))


def test_all_record_kinds_roundtrip_and_structural_restore_is_not_permission():
    records = []
    for factory in (accept, send, stop):
        records.append(entry(factory(), len(records) + 1, records[-1].digest if records else ZERO))
    records.append(entry(begin(3, records[-1].digest), 4, records[-1].digest))
    records.append(entry(assessed(4, records[-1].digest), 5, records[-1].digest))
    state = validate_chain(HISTORY, records)
    assert state.entries == tuple(records)
    assert state.open_restore is None
    assert not hasattr(state, "permit")


@pytest.mark.parametrize("change", ["extra", "boolean", "nil", "uppercase", "wrong_kind", "null"])
def test_closed_fields_and_primitives(change):
    frame = encode_entry(HISTORY, 1, ZERO, accept())
    value = json.loads(frame[4:-32])
    if change == "extra":
        value["data"]["prompt"] = "private"
    if change == "boolean":
        value["data"]["account_generation"] = True
    if change == "nil":
        value["data"]["account_id"] = str(UUID(int=0))
    if change == "uppercase":
        value["data"]["key_commitment"] = "A" * 64
    if change == "wrong_kind":
        value["kind"] = "UNKNOWN"
    if change == "null":
        value["data"]["task_id"] = None
    payload = canonical(value)
    raw = struct.pack(">I", len(payload)) + payload
    with pytest.raises(HistoryError):
        decode_entry(raw + hashlib.sha256(b"daemon.restore-history.frame.v1\n" + raw).digest())


@pytest.mark.parametrize("payload", [b'{"v":1,"v":1}', b'{ "v":1}', b'{"v":NaN}', b"[]"])
def test_duplicate_keys_noncanonical_and_nonfinite_rejected(payload):
    raw = struct.pack(">I", len(payload)) + payload
    with pytest.raises(HistoryError):
        decode_entry(raw + hashlib.sha256(b"daemon.restore-history.frame.v1\n" + raw).digest())


@pytest.mark.parametrize("command", [send(), stop()])
def test_no_fabricated_acceptance_ancestor(command):
    with pytest.raises(HistoryError):
        validate_chain(HISTORY, [entry(command)])


def test_conflicting_key_operation_and_dispatch_tuple_rejected():
    first = entry(accept())
    for command in [accept(), replace(accept(), record_id=UUID(int=500))]:
        with pytest.raises(HistoryError):
            validate_chain(HISTORY, [first, entry(command, 2, first.digest)])
    sent = entry(send(), 2, first.digest)
    with pytest.raises(HistoryError):
        validate_chain(
            HISTORY, [first, sent, entry(replace(send(), record_id=UUID(int=501)), 3, sent.digest)]
        )


def test_wrong_account_generation_and_sequence_are_not_adopted():
    first = entry(accept())
    for data in [
        replace(send().data, account_generation=1),
        replace(send().data, account_id=UUID(int=80)),
    ]:
        with pytest.raises(HistoryError):
            validate_chain(HISTORY, [first, entry(replace(send(), data=data), 2, first.digest)])
    with pytest.raises(HistoryError):
        validate_chain(HISTORY, [entry(accept(), 2)])


def test_restore_cuts_nesting_and_assessment_reference():
    first = entry(begin())
    assert validate_chain(HISTORY, [first]).open_restore == UUID(int=40)
    with pytest.raises(HistoryError):
        validate_chain(HISTORY, [first, entry(begin(), 2, first.digest)])
    with pytest.raises(HistoryError):
        validate_chain(HISTORY, [first, entry(assessed(0, ZERO), 2, first.digest)])
    second = entry(assessed(1, first.digest, "remain_blocked"), 2, first.digest)
    assert validate_chain(HISTORY, [first, second]).open_restore == UUID(int=40)


def test_commitment_golden_domains_and_no_generation_namespace():
    a = commitments(KEY, UUID(int=10), "held-key", b'{"message":"synthetic"}')
    assert a == (
        "09aa61ecb0ce939fdbf11800e93387ce8b8aae6cf7aee118da57593969ea9ca0",
        "f839550a33afa66307e08099312a86f812dfc0a4428ce0ca5d3d7f9620c2b2ea",
    )
    assert a != commitments(KEY, UUID(int=11), "held-key", b'{"message":"synthetic"}')
    assert a[0] == commitments(KEY, UUID(int=10), "held-key", b'{"message":"changed"}')[0]
    for key in [b"", b"x" * 31]:
        with pytest.raises(HistoryError):
            commitments(key, UUID(int=10), "held-key", b"{}")
    with pytest.raises(HistoryError):
        commitments(KEY, UUID(int=10), "bad key", b"{}")


def test_stop_and_open_restore_forbid_later_send_or_new_acceptance():
    first = entry(accept())
    stopped = entry(stop(), 2, first.digest)
    with pytest.raises(HistoryError, match="effect_stopped"):
        validate_chain(HISTORY, [first, stopped, entry(send(), 3, stopped.digest)])
    restored = entry(begin(1, first.digest), 2, first.digest)
    with pytest.raises(HistoryError, match="effect_stopped"):
        validate_chain(HISTORY, [first, restored, entry(send(), 3, restored.digest)])


def test_exact_bounds_and_forged_digest():
    from orchestrator.services.restore_history import model

    assert (model.MAX_PAYLOAD, model.MAX_LOG, model.MAX_RECORDS, model.MAX_CANONICAL) == (
        8192,
        16777216,
        16384,
        67108864,
    )
    original = entry(accept())
    with pytest.raises(HistoryError):
        validate_chain(HISTORY, [replace(original, digest=ZERO)])
    with pytest.raises(HistoryError):
        decode_entry(b"\x00\x00\x20\x01" + b"x" * 8193 + ZERO)
    with pytest.raises(HistoryError):
        commitments(KEY, UUID(int=10), "held", bytes(model.MAX_CANONICAL + 1))


def test_projection_comparison_uses_full_payload_and_is_not_ready_permission():
    from orchestrator.services.restore_history.projection import ProjectionFact, compare_projections

    original = entry(accept())
    state = validate_chain(HISTORY, [original])
    fact = ProjectionFact(
        HISTORY, 1, accept().record_id, original.digest, encode_entry(HISTORY, 1, ZERO, accept())
    )
    assert compare_projections(HISTORY, state, (fact,)).missing == ()
    assert compare_projections(HISTORY, state, ()).missing == (1,)
    wrong = replace(fact, frame=fact.frame[:-1] + b"x")
    assert compare_projections(HISTORY, state, (wrong,)).mismatched == (1,)
    assert compare_projections(HISTORY, state, (replace(fact, seq=2),)).extra == (2,)
    with pytest.raises(HistoryError):
        compare_projections(HISTORY, state, (fact, fact))
    assert not hasattr(compare_projections(HISTORY, state, (fact,)), "ready")
