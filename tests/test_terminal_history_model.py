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
    AcceptPrepare,
    ArtifactPrepare,
    Command,
    HistoryError,
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


def chain(*commands):
    entries = []
    previous = ZERO
    for seq, command in enumerate(commands, 1):
        entry = decode_entry(
            encode_entry(HISTORY, seq, previous, command, profile="terminal-v3"),
            profile="terminal-v3",
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
