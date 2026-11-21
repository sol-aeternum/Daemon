"""Closed terminal-v3 metadata and strict legacy compatibility."""

from dataclasses import replace
import hashlib
import json
import os
import struct
import time
from uuid import UUID

import pytest

from orchestrator.services.restore_history.model import (
    TERMINAL_DOMAIN,
    NONSUCCESS_DOMAIN,
    AcceptPrepare,
    ArtifactPrepare,
    Command,
    HistoryError,
    NonsuccessOutputPrepare,
    Profile,
    TerminalOutputPrepare,
    canonical,
    decode_entry,
    encode_entry,
    validate_chain,
)
from orchestrator.services.restore_history.store import HistoryStore, Ownership
from tests.restore_history_support import (
    HISTORY,
    ZERO,
    accept,
    send,
    stop,
    begin,
    initialize_test_directory,
)
from tests.test_artifact_history_model import artifact


def terminal():
    a = accept()
    assert type(a.data) is AcceptPrepare
    return Command(
        UUID(int=99),
        TerminalOutputPrepare(
            a.data.account_id,
            0,
            a.data.task_id,
            a.data.result_message_id,
            a.record_id,
            1,
            "native-complete-body-v1",
        ),
    )


def nonsuccess():
    a = terminal().data
    assert isinstance(a, TerminalOutputPrepare)
    return Command(
        UUID(int=109),
        NonsuccessOutputPrepare(
            a.account_id,
            a.account_generation,
            a.task_id,
            a.result_message_id,
            a.acceptance_record_id,
            a.epoch,
            "native-nonsuccess-body-v1",
            "failed",
            "provider_length",
        ),
    )


def chain(*commands: Command, profile: Profile = "terminal-v3"):
    entries = []
    previous = ZERO
    for seq, command in enumerate(commands, 1):
        entry = decode_entry(
            encode_entry(HISTORY, seq, previous, command, profile=profile),
            profile=profile,
        )
        entries.append(entry)
        previous = entry.digest
    return entries


def test_closed_mixed_chain_keeps_legacy_bytes_and_no_content():
    entries = chain(accept(), artifact(), terminal())
    state = validate_chain(HISTORY, entries, profile="terminal-v3")
    assert len(state.entries) == 3 and type(state.entries[1].command.data) is ArtifactPrepare
    assert encode_entry(HISTORY, 1, ZERO, accept()) == encode_entry(
        HISTORY, 1, ZERO, accept(), profile="terminal-v3"
    )
    raw = encode_entry(HISTORY, 3, entries[1].digest, terminal(), profile="terminal-v3")
    value = json.loads(raw[4:-32])
    assert value["v"] == 3 and value["actor"] == "worker"
    assert set(value["data"]) == {
        "account_id",
        "account_generation",
        "task_id",
        "result_message_id",
        "acceptance_record_id",
        "epoch",
        "terminal_schema",
    }
    assert b"content" not in raw and b"ciphertext" not in raw


@pytest.mark.parametrize("profile", ["h1", "artifact-v2"])
def test_old_profiles_never_accept_terminal(profile):
    raw = encode_entry(HISTORY, 1, ZERO, terminal(), profile="terminal-v3")
    with pytest.raises(HistoryError):
        decode_entry(raw, profile=profile)
    with pytest.raises(HistoryError):
        encode_entry(HISTORY, 1, ZERO, terminal(), profile=profile)


@pytest.mark.parametrize(
    "field,value",
    [
        ("v", 1),
        ("v", 2),
        ("v", True),
        ("actor", "backend"),
        ("kind", "ARTIFACT_PREPARE"),
        ("extra", 1),
    ],
)
def test_wrong_envelope_refuses_even_rehashed(field, value):
    raw = encode_entry(HISTORY, 1, ZERO, terminal(), profile="terminal-v3")
    v = json.loads(raw[4:-32])
    v[field] = value
    p = canonical(v)
    body = struct.pack(">I", len(p)) + p
    with pytest.raises(HistoryError):
        decode_entry(body + hashlib.sha256(TERMINAL_DOMAIN + body).digest(), profile="terminal-v3")


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_generation", True),
        ("epoch", 0),
        ("epoch", 2**63),
        ("result_message_id", UUID(int=0)),
        ("terminal_schema", "other"),
    ],
)
def test_terminal_fields_are_bounded_and_closed(field, value):
    c = terminal()
    c = replace(c, data=replace(c.data, **{field: value}))
    with pytest.raises(HistoryError):
        encode_entry(HISTORY, 1, ZERO, c, profile="terminal-v3")


@pytest.mark.parametrize(
    "commands",
    [
        lambda: [terminal()],
        lambda: [
            accept(),
            replace(terminal(), data=replace(terminal().data, result_message_id=UUID(int=44))),
        ],
        lambda: [
            accept(),
            replace(terminal(), data=replace(terminal().data, acceptance_record_id=UUID(int=44))),
        ],
        lambda: [accept(), stop(), terminal()],
        lambda: [accept(), begin(), terminal()],
        lambda: [
            accept(),
            terminal(),
            replace(terminal(), record_id=UUID(int=101), data=replace(terminal().data, epoch=2)),
        ],
        lambda: [accept(), terminal(), send()],
        lambda: [accept(), terminal(), artifact()],
    ],
)
def test_terminal_history_cannot_become_fresh_or_cross_control_fences(commands):
    with pytest.raises(HistoryError):
        validate_chain(HISTORY, chain(*commands()), profile="terminal-v3")


def test_terminal_does_not_prohibit_negative_stop():
    assert (
        len(
            validate_chain(
                HISTORY, chain(accept(), terminal(), stop()), profile="terminal-v3"
            ).entries
        )
        == 3
    )


def test_real_store_terminal_roundtrip_and_strict_default(tmp_path):
    identity = initialize_test_directory(tmp_path / "h")
    owner = Ownership(os.getuid(), frozenset({os.getuid()}), os.getgid())
    with HistoryStore(
        tmp_path / "h", identity, owner, deadline=time.monotonic() + 2, profile="terminal-v3"
    ) as store:
        for command in (accept(), terminal()):
            assert store.append(command, deadline=time.monotonic() + 2).created
        assert len(store.verify(deadline=time.monotonic() + 2).entries) == 2
    with pytest.raises(HistoryError):
        with HistoryStore(tmp_path / "h", identity, owner, deadline=time.monotonic() + 2) as store:
            store.verify(deadline=time.monotonic() + 2)


def test_v4_closed_domain_old_refusal_and_unchanged_older_frames():
    for item in (accept(), artifact(), terminal()):
        assert encode_entry(HISTORY, 1, ZERO, item, profile="terminal-v4") == encode_entry(
            HISTORY, 1, ZERO, item, profile="terminal-v3"
        )
    command = nonsuccess()
    frame = encode_entry(HISTORY, 1, ZERO, command, profile="terminal-v4")
    assert frame[-32:] == hashlib.sha256(NONSUCCESS_DOMAIN + frame[:-32]).digest()
    assert decode_entry(frame, profile="terminal-v4").command == command
    value = json.loads(frame[4:-32])
    assert (value["v"], value["actor"], value["kind"]) == (4, "worker", "NONSUCCESS_OUTPUT_PREPARE")
    assert set(value["data"]) == {
        "account_id",
        "account_generation",
        "task_id",
        "result_message_id",
        "acceptance_record_id",
        "epoch",
        "terminal_schema",
        "status",
        "reason",
    }
    for profile in ("h1", "artifact-v2", "terminal-v3"):
        with pytest.raises(HistoryError):
            decode_entry(frame, profile=profile)
        with pytest.raises(HistoryError):
            encode_entry(HISTORY, 1, ZERO, command, profile=profile)
    for fields in (
        {"provider_payload": "not history"},
        {"status": "cancelled"},
        {"reason": "failed_terminal"},
        {"epoch": 0},
        {"status": "needs_attention", "reason": "provider_length"},
    ):
        mutated = {**value, "data": {**value["data"], **fields}}
        payload = canonical(mutated)
        body = struct.pack(">I", len(payload)) + payload
        with pytest.raises(HistoryError):
            decode_entry(
                body + hashlib.sha256(NONSUCCESS_DOMAIN + body).digest(), profile="terminal-v4"
            )


def test_v4_mixed_chain_keeps_distinct_original_terminal_facts():
    a = accept()
    assert isinstance(a.data, AcceptPrepare)
    other = replace(
        a,
        record_id=UUID(int=110),
        data=replace(
            a.data,
            task_id=UUID(int=111),
            result_message_id=UUID(int=112),
            key_commitment="c" * 64,
        ),
    )
    assert isinstance(other.data, AcceptPrepare)
    last = nonsuccess()
    last = replace(
        last,
        data=replace(
            last.data,
            task_id=other.data.task_id,
            result_message_id=other.data.result_message_id,
            acceptance_record_id=other.record_id,
        ),
    )
    entries = chain(a, artifact(), terminal(), other, last, profile="terminal-v4")
    assert validate_chain(HISTORY, entries, profile="terminal-v4").entries == tuple(entries)


@pytest.mark.parametrize(
    "commands",
    [
        lambda: (nonsuccess(),),
        lambda: (accept(), stop(), nonsuccess()),
        lambda: (accept(), begin(1, chain(accept())[0].digest), nonsuccess()),
        lambda: (accept(), terminal(), nonsuccess()),
        lambda: (accept(), nonsuccess(), terminal()),
        lambda: (
            accept(),
            nonsuccess(),
            replace(
                nonsuccess(), record_id=UUID(int=999), data=replace(nonsuccess().data, epoch=2)
            ),
        ),
        lambda: (accept(), nonsuccess(), send()),
        lambda: (accept(), nonsuccess(), artifact()),
        lambda: (accept(), nonsuccess(), stop()),
    ],
)
def test_v4_terminal_negative_fences(commands):
    with pytest.raises(HistoryError):
        validate_chain(HISTORY, chain(*commands(), profile="terminal-v4"), profile="terminal-v4")
