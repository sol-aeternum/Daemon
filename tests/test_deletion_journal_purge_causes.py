"""Deletion purge cannot erase the request's recorded suspension evidence."""

import hashlib
import struct
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import (
    Cause,
    Command,
    Entry,
    ReadyFields,
    RequestFields,
    TerminalFields,
    command_digest,
    encode_entry,
)
from orchestrator.services.deletion_journal.store import Journal, initialize
from tests.deletion_journal_support import request


def _frame(entry: Entry) -> bytes:
    payload = encode_entry(entry)
    prefix = struct.pack(">I", len(payload))
    return prefix + payload + hashlib.sha256(prefix + payload).digest()


@pytest.mark.parametrize("no_grace", [False, True])
@pytest.mark.parametrize("damage", ["all", "deletion", "abuse", "billing", "operator", "invented"])
def test_purge_preserves_all_causes_on_append_and_hashed_replay(tmp_path, no_grace, damage):
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    command = request()
    assert isinstance(command.fields, RequestFields)
    causes = command.fields.causes + tuple(
        Cause(code, uuid4()) for code in ("abuse", "billing", "operator")
    )
    command = replace(
        command,
        fields=replace(
            command.fields,
            prior_status="suspended",
            prior_reason="abuse",
            causes=causes,
            no_grace=no_grace,
            reason="legal" if no_grace else None,
        ),
    )
    seq, decision = 1, command.decision_at
    with Journal(path, identity) as journal:
        journal.append(command, uuid4(), None, None)
        if not no_grace:
            started = decision + timedelta(days=1)
            decision = started + timedelta(days=7)
            journal.append(
                Command(
                    command.account_id,
                    command.operation_id,
                    "grace_started",
                    started,
                    ReadyFields(started, decision),
                ),
                uuid4(),
                seq,
                seq,
            )
            seq += 1
        prefix = (path / "entries.log").read_bytes()
        head = (path / "HEAD").read_bytes()
        purge = Command(
            command.account_id, command.operation_id, "purged", decision, TerminalFields(causes, 1)
        )
        if damage == "all":
            damaged_causes = ()
        elif damage == "invented":
            damaged_causes = causes + (Cause("billing", uuid4()),)
        else:
            damaged_causes = tuple(c for c in causes if c.code != damage)
        broken = replace(purge, fields=TerminalFields(damaged_causes, 1))
        with pytest.raises(JournalError, match="^invalid_entry$"):
            journal.append(broken, uuid4(), seq, seq)
        assert (path / "entries.log").read_bytes() == prefix
        assert (path / "HEAD").read_bytes() == head
        terminal = journal.append(purge, uuid4(), seq, seq)
    with Journal(path, identity) as journal:
        assert journal.verify().entries[-1].command == purge

    # The public frame encoder and same identity/predecessors/digest calculation
    # pass for the full-set control. Only the terminal cause set then changes.
    assert prefix + _frame(terminal) == (path / "entries.log").read_bytes()
    broken_entry = replace(terminal, command=broken, command_digest=command_digest(broken))
    replay_path = tmp_path / "broken-replay"
    initialize(replay_path, identity)
    log = prefix + _frame(broken_entry)
    (replay_path / "entries.log").write_bytes(log)
    head = (replay_path / "HEAD").read_bytes()
    with Journal(replay_path, identity) as journal:
        with pytest.raises(JournalError, match="^corrupt$"):
            journal.verify()
    assert (replay_path / "entries.log").read_bytes() == log
    assert (replay_path / "HEAD").read_bytes() == head
