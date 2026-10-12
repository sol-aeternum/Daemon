"""Codex P2 regression: missing operation causes cannot enter/replay a journal."""

import hashlib
import struct
from dataclasses import replace
from uuid import uuid4

import pytest

from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import (
    Cause,
    Command,
    Entry,
    RequestFields,
    command_digest,
    encode_entry,
)
from orchestrator.services.deletion_journal.store import Journal, initialize
from tests.deletion_journal_support import request


def framed(command: Command, identity, event) -> bytes:
    entry = Entry(
        identity, 1, event, command, None, None, bytes(32), command_digest(command), bytes(32)
    )
    payload = encode_entry(entry)
    prefix = struct.pack(">I", len(payload))
    return prefix + payload + hashlib.sha256(prefix + payload).digest()


@pytest.mark.parametrize("reset", [False, True])
@pytest.mark.parametrize("mismatch", ["missing", "wrong_kind", "wrong_id"])
def test_no_operation_fence_cause_refuses_append_and_full_log_replay(tmp_path, reset, mismatch):
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    command = request(reset=reset)
    event = uuid4()
    control = tmp_path / "valid-control"
    initialize(control, identity)
    (control / "entries.log").write_bytes(framed(command, identity, event))
    with Journal(control, identity) as journal:
        assert journal.verify().entries[0].command == command
    assert isinstance(command.fields, RequestFields)
    causes = {
        "missing": (),
        "wrong_kind": (Cause("deletion" if reset else "reset", command.operation_id),),
        "wrong_id": (Cause("reset" if reset else "deletion", uuid4()),),
    }[mismatch]
    command = replace(command, fields=replace(command.fields, causes=causes))
    before_head = (path / "HEAD").read_bytes()
    with Journal(path, identity) as journal:
        with pytest.raises(JournalError, match="invalid_entry"):
            journal.append(command, uuid4(), None, None)
        assert (path / "entries.log").read_bytes() == b""
        assert journal.verify().entries == []
        assert (path / "HEAD").read_bytes() == before_head
    # Correctly framed/hashed bytes are insufficient if their structural intent
    # has no suspension fence. This bypasses append ONLY in test-owned storage.
    frame = framed(command, identity, event)
    (path / "entries.log").write_bytes(frame)
    head = (path / "HEAD").read_bytes()
    with Journal(path, identity) as journal:
        with pytest.raises(JournalError, match="corrupt"):
            journal.verify()
    assert (path / "entries.log").read_bytes() == frame
    assert (path / "HEAD").read_bytes() == head
