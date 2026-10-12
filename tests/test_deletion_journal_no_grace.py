"""Zero-grace intent must preserve the legal/abuse exception's reason."""

import hashlib
import json
import struct
from dataclasses import replace
from uuid import uuid4

import pytest

from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import (
    Command,
    RequestFields,
    TerminalFields,
    decode_entry,
    encode_entry,
)
from orchestrator.services.deletion_journal.store import Journal, initialize
from tests.deletion_journal_support import request


def _caller_digest(obj) -> bytes:
    args = {key: obj["command"][key] for key in ("account_id", "operation_id", "event_type")}
    fields = obj["command"]["fields"]
    args["fields"] = {
        key: fields[key] for key in ("prior_status", "prior_reason", "no_grace", "reason")
    }
    args["fields"]["causes"] = [[c["code"], c["id"]] for c in fields["causes"]]
    canonical_args = json.dumps(
        args, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical_args).digest()


def test_missing_zero_grace_reason_refuses_constructor_and_append(tmp_path):
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    ordinary = request()
    assert isinstance(ordinary.fields, RequestFields)
    before_head = (path / "HEAD").read_bytes()
    with Journal(path, identity) as journal:
        with pytest.raises(JournalError, match="^invalid_entry$"):
            journal.append(
                replace(ordinary, fields=replace(ordinary.fields, no_grace=True)),
                uuid4(),
                None,
                None,
            )
        # Validation rejects intent construction before append can write anything.
        assert journal.verify().entries == []
        assert (path / "entries.log").read_bytes() == b""
        assert (path / "HEAD").read_bytes() == before_head
        # Ordinary reason-free requests remain valid, but cannot skip grace.
        journal.append(ordinary, uuid4(), None, None)
        purge = Command(
            ordinary.account_id,
            ordinary.operation_id,
            "purged",
            ordinary.decision_at,
            TerminalFields(ordinary.fields.causes, 1),
        )
        with pytest.raises(JournalError, match="^invalid_entry$"):
            journal.append(purge, uuid4(), 1, 1)
        assert len(journal.verify().entries) == 1


@pytest.mark.parametrize("reason", ["legal", "abuse"])
def test_zero_grace_reason_append_purge_and_hashed_replay(tmp_path, reason):
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    ordinary = request()
    assert isinstance(ordinary.fields, RequestFields)
    command = replace(ordinary, fields=replace(ordinary.fields, no_grace=True, reason=reason))
    with Journal(path, identity) as journal:
        accepted = journal.append(command, uuid4(), None, None)
        purge = Command(
            command.account_id,
            command.operation_id,
            "purged",
            command.decision_at,
            TerminalFields(ordinary.fields.causes, 1),
        )
        journal.append(purge, uuid4(), 1, 1)
    with Journal(path, identity) as journal:
        assert [e.command.event_type for e in journal.verify().entries] == ["requested", "purged"]

    # A correctly hashed single request with the same legal/abuse reason passes.
    replay_path = tmp_path / "replay"
    initialize(replay_path, identity)
    payload = encode_entry(accepted)
    prefix = struct.pack(">I", len(payload))
    (replay_path / "entries.log").write_bytes(
        prefix + payload + hashlib.sha256(prefix + payload).digest()
    )
    with Journal(replay_path, identity) as journal:
        assert journal.verify().entries == [accepted]

    # Change only the reason and both hashes, so digest mismatch cannot mask the
    # regression. A fresh empty HEAD makes promotion observable if scan accepts.
    broken_path = tmp_path / "broken"
    initialize(broken_path, identity)
    obj = json.loads(payload)
    assert _caller_digest(obj) == accepted.command_digest
    obj["command"]["fields"]["reason"] = None
    obj["command_digest"] = _caller_digest(obj).hex()
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    prefix = struct.pack(">I", len(payload))
    footer = hashlib.sha256(prefix + payload).digest()
    frame = prefix + payload + footer
    with pytest.raises(JournalError, match="^invalid_entry$"):
        decode_entry(payload, footer)
    (broken_path / "entries.log").write_bytes(frame)
    before_head = (broken_path / "HEAD").read_bytes()
    with Journal(broken_path, identity) as journal:
        with pytest.raises(JournalError, match="^corrupt$"):
            journal.verify()
    assert (broken_path / "entries.log").read_bytes() == frame
    assert (broken_path / "HEAD").read_bytes() == before_head
