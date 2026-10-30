"""C1 real synthetic POSIX mixed containers; no database/runtime integration."""

from __future__ import annotations

import multiprocessing
import os
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from orchestrator.services.deletion_journal import store
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.mixed_store import MixedJournal
from orchestrator.services.deletion_journal.namespace_candidate import (
    NamespaceCommand,
    NamespaceCreateFields,
    NamespaceEntry,
    encode_entry_frame,
    encode_namespace_frame,
)
from orchestrator.services.deletion_journal.store import Journal, initialize
from orchestrator.services.deletion_journal.model import Entry
from orchestrator.services.deletion_journal.upgrade import promote_container_v2
from tests.deletion_journal_support import request


def birth(account=None):
    return NamespaceCommand(
        account or uuid4(),
        "namespace_create_intended",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        NamespaceCreateFields(0, uuid4().hex),
    )


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "journal"
    identity = uuid4()
    initialize(path, identity)
    return path, identity


def test_v1_roundtrip_refuses_birth_without_writes(directory):
    path, identity = directory
    command, event = request(), uuid4()
    with MixedJournal(path, identity) as journal:
        entry = journal.append(command, event, None, None)
        assert journal.verify().container_version == 1
        before = (path / "entries.log").read_bytes()
        with pytest.raises(JournalError, match="invalid_entry"):
            journal.append(birth(), uuid4(), None, None)
        assert (path / "entries.log").read_bytes() == before
    with Journal(path, identity) as legacy:
        assert legacy.verify().entries == [entry]


def test_mixed_interleave_identity_cas_and_frozen_bytes(directory):
    path, identity = directory
    promote_container_v2(path, identity)
    with MixedJournal(path, identity) as journal:
        assert journal.verify().entries == ()
        command, event = birth(), uuid4()
        first = journal.append(command, event, None, None)
        v1 = request()
        second = journal.append(v1, uuid4(), None, None)
        assert isinstance(first, NamespaceEntry) and isinstance(second, Entry)
        assert (path / "entries.log").read_bytes() == (
            encode_namespace_frame(first) + encode_entry_frame(second)
        )
        # A subsequent head never changes the original event identity.
        assert journal.append(command, event, 999, None) == first
        with pytest.raises(JournalError, match="conflict"):
            journal.append(
                replace(command, fields=NamespaceCreateFields(0, uuid4().hex)), event, None, None
            )
        with pytest.raises(JournalError, match="conflict"):
            journal.append(request(account=command.account_id), uuid4(), None, None)
        with pytest.raises(JournalError, match="invalid_entry"):
            journal.append(command, event, None, 1)
        # An operation entry following a birth advances both heads, with an
        # independent operation predecessor check even when account CAS matches.
        next_request = request(account=command.account_id)
        with pytest.raises(JournalError, match="conflict"):
            journal.append(next_request, uuid4(), first.seq, 999)
        third = journal.append(next_request, uuid4(), first.seq, None)
        assert third.account_prev == first.seq
        assert third.seq == 3
        view = journal.verify()
        with pytest.raises(FrozenInstanceError):
            setattr(view, "container_version", 1)
        assert isinstance(view.entries, tuple)
    with Journal(path, identity) as legacy, pytest.raises(JournalError, match="corrupt"):
        legacy.verify()


@pytest.mark.parametrize("damage", ["prefix", "footer", "version", "id", "mode", "pending"])
def test_full_scan_refuses_damage_preserves_bytes(directory, damage):
    path, identity = directory
    promote_container_v2(path, identity)
    with MixedJournal(path, identity) as journal:
        journal.append(birth(), uuid4(), None, None)
    if damage == "prefix":
        with (path / "entries.log").open("ab") as log:
            log.write(b"\xff\xff\xff\xff")
    elif damage == "footer":
        content = (path / "entries.log").read_bytes()
        (path / "entries.log").write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    elif damage in ("version", "id"):
        (path / "journal.meta").write_bytes(
            store._canonical(
                {
                    "version": 3 if damage == "version" else 2,
                    "journal_id": str(uuid4() if damage == "id" else identity),
                }
            )
        )
    elif damage == "mode":
        (path / "journal.meta").chmod(0o644)
    else:
        (path / ".META.v2.pending").write_bytes(b"{")
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    with MixedJournal(path, identity) as journal, pytest.raises(JournalError, match="corrupt"):
        journal.verify()
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}


def test_v2_frame_in_metadata1_refused_and_warm_metadata_inode_pinned(directory):
    path, identity = directory
    promote_container_v2(path, identity)
    with MixedJournal(path, identity) as journal:
        journal.append(birth(), uuid4(), None, None)
        replacement = path / "replacement"
        replacement.write_bytes((path / "journal.meta").read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, path / "journal.meta")
        with pytest.raises(JournalError, match="corrupt"):
            journal.verify()
    (path / "journal.meta").write_bytes(
        store._canonical({"version": 1, "journal_id": str(identity)})
    )
    with MixedJournal(path, identity) as journal, pytest.raises(JournalError, match="corrupt"):
        journal.verify()


@pytest.mark.parametrize("target", ["journal.meta", "directory"])
def test_sync_failure_never_returns_view(directory, monkeypatch, target):
    path, identity = directory
    promote_container_v2(path, identity)
    original = os.fsync

    def sync(fd):
        name = os.readlink(f"/proc/self/fd/{fd}")
        if name == str(path if target == "directory" else path / target):
            raise OSError("synthetic")
        original(fd)

    monkeypatch.setattr(os, "fsync", sync)
    with MixedJournal(path, identity) as journal, pytest.raises(JournalError, match="unavailable"):
        journal.verify()


def test_unknown_append_recovers_original_suffix(directory, monkeypatch):
    path, identity = directory
    promote_container_v2(path, identity)
    command, event = birth(), uuid4()
    with MixedJournal(path, identity) as journal:
        original = journal._journal._publish_head

        def fail(*args):
            raise OSError("synthetic")

        monkeypatch.setattr(journal._journal, "_publish_head", fail)
        with pytest.raises(JournalError, match="unknown_append"):
            journal.append(command, event, None, None)
        frames = (path / "entries.log").read_bytes()
        monkeypatch.setattr(journal._journal, "_publish_head", original)
        recovered = journal.append(command, event, None, None)
        assert recovered.seq == 1
        assert (path / "entries.log").read_bytes() == frames
        assert journal.verify().entries == (recovered,)


def _append_birth(path, identity, command, event, ready, go, result):
    with MixedJournal(path, identity) as journal:
        ready.put(True)
        if not go.wait(10):
            return
        try:
            result.put(("ok", journal.append(command, event, None, None).seq))
        except JournalError as exc:
            result.put((exc.code, None))


@pytest.mark.parametrize("same_account", [False, True])
def test_owned_process_contention(directory, same_account):
    path, identity = directory
    promote_container_v2(path, identity)
    ctx = multiprocessing.get_context("spawn")
    ready, result, go = ctx.Queue(), ctx.Queue(), ctx.Event()
    first = birth()
    second = birth(first.account_id if same_account else None)
    jobs = [
        ctx.Process(target=_append_birth, args=(path, identity, c, uuid4(), ready, go, result))
        for c in (first, second)
    ]
    try:
        for job in jobs:
            job.start()
        for _ in jobs:
            assert ready.get(timeout=10)
        go.set()
        outcomes = [result.get(timeout=10)[0] for _ in jobs]
        assert sorted(outcomes) == (["conflict", "ok"] if same_account else ["ok", "ok"])
        for job in jobs:
            job.join(10)
            assert job.exitcode == 0
    finally:
        for job in jobs:
            if job.is_alive():
                job.terminate()
                job.join(5)
        ready.close()
        result.close()
    with MixedJournal(path, identity) as journal:
        assert len(journal.verify().entries) == (1 if same_account else 2)


def test_no_runtime_mixed_import():
    root = Path(__file__).resolve().parents[1] / "orchestrator"
    assert [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p
        not in {root / "services/deletion_journal/mixed_store.py", root / "artifact_namespaces.py"}
        and "mixed_store" in p.read_text()
    ] == []
