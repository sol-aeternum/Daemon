"""Real POSIX files/processes, controlled commands, no live storage or services."""

from __future__ import annotations

import multiprocessing
import fcntl
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from orchestrator.services.deletion_journal import store
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import (
    AckFields,
    Command,
    ReadyFields,
    TerminalFields,
)
from orchestrator.services.deletion_journal.store import Journal, initialize
from tests.deletion_journal_support import request


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "journal"
    identity = uuid4()
    initialize(path, identity)
    return path, identity


def test_new_only_private_initializer_and_empty_verification(directory):
    path, identity = directory
    with Journal(path, identity) as journal:
        assert journal.verify().entries == []
    assert path.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in path.iterdir())
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    with pytest.raises(JournalError):
        initialize(path, identity)
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}


def test_repeated_head_replacement_rescans_pending_and_unknown_files(directory):
    path, identity = directory
    with Journal(path, identity) as journal:
        for _ in range(8):
            journal.append(request(), uuid4(), None, None)
            assert journal.verify().entries
            # A complete leftover temporary is recoverable after a warmed scan.
            pending = path / ".HEAD.pending"
            pending.write_bytes((path / "HEAD").read_bytes())
            pending.chmod(0o600)
            journal.verify()
            assert not pending.exists()
        before = (path / "entries.log").read_bytes()
        unexpected = path / "unexpected"
        unexpected.write_bytes(b"preserve")
        with pytest.raises(JournalError, match="corrupt"):
            journal.verify()
        assert unexpected.read_bytes() == b"preserve"
        assert (path / "entries.log").read_bytes() == before


def test_warm_scan_preserves_torn_head_temporary(directory):
    path, identity = directory
    with Journal(path, identity) as journal:
        journal.append(request(), uuid4(), None, None)
        journal.verify()
        before = (path / "entries.log").read_bytes()
        pending = path / ".HEAD.pending"
        pending.write_bytes(b"{")
        pending.chmod(0o600)
        with pytest.raises(JournalError, match="corrupt"):
            journal.verify()
        assert pending.read_bytes() == b"{"
        assert (path / "entries.log").read_bytes() == before


def test_roundtrip_both_head_cas_and_duplicate_after_unknown_reply(directory):
    path, identity = directory
    command = request()
    event = uuid4()
    with Journal(path, identity) as journal:
        entry = journal.append(command, event, None, None)
        assert entry.seq == 1
        assert journal.verify().entries == [entry]
        assert journal.append(command, event, 123, 456) == entry
        retry = replace(command, decision_at=command.decision_at + timedelta(days=40))
        assert journal.append(retry, event, 999, 999) == entry
        with pytest.raises(JournalError, match="conflict"):
            journal.append(replace(command, account_id=uuid4()), event, None, None)
        with pytest.raises(JournalError):
            journal.append(request(command.account_id), uuid4(), 1, None)
        assert len(journal.verify().entries) == 1


def test_fsync_order_and_short_write_loop(directory, monkeypatch):
    path, identity = directory
    events = []
    real_sync, real_write, real_replace = os.fsync, os.write, os.replace

    def sync(fd):
        events.append(("sync", Path(os.readlink(f"/proc/self/fd/{fd}")).name))
        return real_sync(fd)

    def write(fd, data):
        return real_write(fd, data[:7])

    def rename(*args, **kwargs):
        events.append(("rename", args[0]))
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "write", write)
    monkeypatch.setattr(os, "replace", rename)
    with Journal(path, identity) as journal:
        journal.append(request(), uuid4(), None, None)
        assert events[-4:] == [
            ("sync", "entries.log"),
            ("sync", ".HEAD.pending"),
            ("rename", ".HEAD.pending"),
            ("sync", "journal"),
        ]
        assert len(journal.verify().entries) == 1


@pytest.mark.parametrize(
    "point", ["log_sync", "head_write", "head_sync", "rename", "directory_sync"]
)
def test_injected_append_failures_never_report_success(directory, monkeypatch, point):
    path, identity = directory
    with Journal(path, identity) as journal:
        journal.verify()
        real_sync, real_write, real_replace = os.fsync, os.write, os.replace
        armed = False

        def write(fd, data):
            nonlocal armed
            name = Path(os.readlink(f"/proc/self/fd/{fd}")).name
            if name == "entries.log":
                armed = True
            if armed and name == ".HEAD.pending" and point == "head_write":
                real_write(fd, data[:2])
                raise OSError("fictional")
            return real_write(fd, data)

        def sync(fd):
            name = Path(os.readlink(f"/proc/self/fd/{fd}")).name
            if armed and (
                (point == "log_sync" and name == "entries.log")
                or (point == "head_sync" and name == ".HEAD.pending")
                or (point == "directory_sync" and name == "journal")
            ):
                raise OSError("fictional")
            return real_sync(fd)

        def rename(*args, **kwargs):
            if armed and point == "rename":
                raise OSError("fictional")
            return real_replace(*args, **kwargs)

        monkeypatch.setattr(os, "write", write)
        monkeypatch.setattr(os, "fsync", sync)
        monkeypatch.setattr(os, "replace", rename)
        event, command = uuid4(), request()
        with pytest.raises(JournalError, match="unknown_append"):
            journal.append(command, event, None, None)
        monkeypatch.undo()
        if point == "head_write":
            with pytest.raises(JournalError, match="corrupt"):
                journal.verify()
        else:
            assert journal.append(command, event, None, None).seq == 1
            assert len(journal.verify().entries) == 1


@pytest.mark.parametrize(
    "damage",
    ["partial", "middle", "head_ahead", "temp", "unknown", "mode", "missing", "link", "symlink"],
)
def test_damage_preserved_and_blocks_whole_journal(directory, damage):
    path, identity = directory
    with Journal(path, identity) as journal:
        journal.append(request(), uuid4(), None, None)
    if damage == "partial":
        with (path / "entries.log").open("ab") as file:
            file.write(struct.pack(">I", 42) + b"short")
    elif damage == "middle":
        data = bytearray((path / "entries.log").read_bytes())
        data[25] ^= 1
        (path / "entries.log").write_bytes(data)
    elif damage == "head_ahead":
        (path / "entries.log").write_bytes(b"")
    elif damage in {"temp", "unknown"}:
        target = path / (".HEAD.pending" if damage == "temp" else "unknown")
        target.write_bytes(b"{")
        target.chmod(0o600)
    elif damage == "mode":
        (path / "HEAD").chmod(0o644)
    elif damage == "missing":
        (path / "HEAD").unlink()
    elif damage == "link":
        os.link(path / "entries.log", path.parent / "linked")
    else:
        (path / "HEAD").rename(path.parent / "original")
        (path / "HEAD").symlink_to(path.parent / "original")
    before = (path / "entries.log").read_bytes()
    with pytest.raises(JournalError):
        with Journal(path, identity) as journal:
            journal.verify()
    assert (path / "entries.log").read_bytes() == before


def test_wrong_identity_and_pinned_directory_replacement(directory):
    path, identity = directory
    with pytest.raises(JournalError):
        with Journal(path, uuid4()) as wrong:
            wrong.verify()
    with Journal(path, identity) as journal:
        path.rename(path.parent / "old")
        initialize(path, identity)
        with pytest.raises(JournalError, match="corrupt"):
            journal.verify()


def test_same_instance_thread_calls_are_serialized(directory):
    path, identity = directory
    command, event = request(), uuid4()
    with Journal(path, identity) as journal, ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(journal.append, command, event, None, None) for _ in range(2)]
        assert [future.result(timeout=10).seq for future in futures] == [1, 1]
        assert len(journal.verify().entries) == 1


def test_lock_timeout_preserves_log(directory, monkeypatch):
    path, identity = directory
    holder = os.open(path / ".journal.lock", os.O_RDWR)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(store, "LOCK_WAIT_S", 0.02)
        with Journal(path, identity) as journal:
            with pytest.raises(JournalError, match="unavailable"):
                journal.verify()
        assert (path / "entries.log").read_bytes() == b""
    finally:
        os.close(holder)


def _append_process(path, identity, command, event, ready, output, heads=None):
    ready.wait(10)
    try:
        with Journal(path, identity) as journal:
            account, operation = heads if heads is not None else (None, None)
            output.put(journal.append(command, event, account, operation).seq)
    except JournalError as error:
        output.put(error.code)


@pytest.mark.parametrize("duplicate", [False, True])
def test_separate_processes_cannot_fork_same_account(directory, duplicate):
    path, identity = directory
    context = multiprocessing.get_context("spawn")
    ready, output = context.Event(), context.Queue()
    command, event = request(), uuid4()
    other = command if duplicate else request(command.account_id)
    processes = [
        context.Process(target=_append_process, args=(path, identity, c, e, ready, output))
        for c, e in ((command, event), (other, event if duplicate else uuid4()))
    ]
    try:
        for process in processes:
            process.start()
        ready.set()
        results = [output.get(timeout=15), output.get(timeout=15)]
        assert results.count(1) == (2 if duplicate else 1)
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        with Journal(path, identity) as journal:
            assert len(journal.verify().entries) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        output.close()


def test_real_process_restore_purge_race_has_one_terminal_intent(directory):
    path, identity = directory
    original = request()
    started = original.decision_at + timedelta(days=1)
    deadline = started + timedelta(days=7)
    with Journal(path, identity) as journal:
        journal.append(original, uuid4(), None, None)
        journal.append(
            Command(
                original.account_id,
                original.operation_id,
                "grace_started",
                started,
                ReadyFields(started, deadline),
            ),
            uuid4(),
            1,
            1,
        )
    restore = Command(
        original.account_id,
        original.operation_id,
        "restored",
        deadline - timedelta(microseconds=1),
        TerminalFields((), 2),
    )
    purge = Command(
        original.account_id, original.operation_id, "purged", deadline, TerminalFields((), 1)
    )
    context = multiprocessing.get_context("spawn")
    ready, output = context.Event(), context.Queue()
    children = [
        context.Process(
            target=_append_process, args=(path, identity, command, uuid4(), ready, output, (2, 2))
        )
        for command in (restore, purge)
    ]
    try:
        for child in children:
            child.start()
        ready.set()
        results = [output.get(timeout=15), output.get(timeout=15)]
        assert results.count(3) == 1
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        with Journal(path, identity) as journal:
            entries = journal.verify().entries
            assert len(entries) == 3
            assert entries[-1].command.event_type in {"restored", "purged"}
            with pytest.raises(JournalError):
                journal.append(
                    purge if entries[-1].command.event_type == "restored" else restore,
                    uuid4(),
                    3,
                    3,
                )
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
        output.close()


@pytest.mark.parametrize("reason", ["owner", "timeout"])
def test_real_process_reset_acknowledgement_cancellation_race(directory, reason):
    path, identity = directory
    original = request(reset=True)
    started = original.decision_at + timedelta(days=1)
    deadline = started + timedelta(days=7)
    ack_time = deadline - timedelta(microseconds=1)
    with Journal(path, identity) as journal:
        journal.append(original, uuid4(), None, None)
        journal.append(
            Command(
                original.account_id,
                original.operation_id,
                "reset_report_ready",
                started,
                ReadyFields(started, deadline),
            ),
            uuid4(),
            1,
            1,
        )
    ack = Command(
        original.account_id,
        original.operation_id,
        "reset_acknowledged",
        ack_time,
        AckFields(ack_time),
    )
    cancel = Command(
        original.account_id,
        original.operation_id,
        "reset_cancelled",
        deadline if reason == "timeout" else ack_time,
        TerminalFields((), 2, reason),
    )
    context = multiprocessing.get_context("spawn")
    ready, output = context.Event(), context.Queue()
    children = [
        context.Process(
            target=_append_process, args=(path, identity, command, uuid4(), ready, output, (2, 2))
        )
        for command in (ack, cancel)
    ]
    try:
        for child in children:
            child.start()
        ready.set()
        results = [output.get(timeout=15), output.get(timeout=15)]
        assert results.count(3) == 1
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        with Journal(path, identity) as journal:
            last = journal.verify().entries[-1]
            with pytest.raises(JournalError):
                journal.append(
                    cancel if last.command.event_type == "reset_acknowledged" else ack,
                    uuid4(),
                    3,
                    3,
                )
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
        output.close()


def test_different_account_processes_share_contiguous_global_sequence(directory):
    path, identity = directory
    context = multiprocessing.get_context("spawn")
    ready, output = context.Event(), context.Queue()
    children = [
        context.Process(
            target=_append_process, args=(path, identity, request(), uuid4(), ready, output)
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        ready.set()
        assert sorted([output.get(timeout=15), output.get(timeout=15)]) == [1, 2]
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        with Journal(path, identity) as journal:
            assert len(journal.verify().account_heads) == 2
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
        output.close()


def _crash_after_log_sync(path: Path, identity: UUID, command: Command, event: UUID):
    # Test-owned child only: simulate process death at exact durable boundary.
    def crash(self: Journal, seq: int, digest: bytes) -> None:
        os._exit(75)

    store.Journal._publish_head = crash
    with Journal(path, identity) as journal:
        journal.append(command, event, None, None)


def test_real_process_crash_after_log_sync_recovers_original_event(directory):
    path, identity = directory
    command, event = request(), uuid4()
    child = multiprocessing.get_context("spawn").Process(
        target=_crash_after_log_sync, args=(path, identity, command, event)
    )
    child.start()
    child.join(15)
    try:
        assert child.exitcode == 75
        with Journal(path, identity) as journal:
            assert journal.append(command, event, None, None).seq == 1
            assert len(journal.verify().entries) == 1
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)


def _crash_frame_boundary(path, identity, command, event, point):
    original_write = store._write_all

    def write(fd, data):
        name = Path(os.readlink(f"/proc/self/fd/{fd}")).name
        if (point == "partial_log" and name == "entries.log") or (
            point == "partial_head" and name == ".HEAD.pending"
        ):
            os.write(fd, data[:2])
            os.fsync(fd)
            os._exit(75)
        original_write(fd, data)

    def before_replace(*args, **kwargs):
        os._exit(75)

    store._write_all = write
    if point == "valid_head":
        os.replace = before_replace
    with Journal(path, identity) as journal:
        journal.append(command, event, None, None)


@pytest.mark.parametrize("point", ["partial_log", "partial_head", "valid_head"])
def test_real_process_partial_frames_and_head_publication(directory, point):
    path, identity = directory
    command, event = request(), uuid4()
    child = multiprocessing.get_context("spawn").Process(
        target=_crash_frame_boundary, args=(path, identity, command, event, point)
    )
    child.start()
    try:
        child.join(15)
        assert child.exitcode == 75
        before = (path / "entries.log").read_bytes()
        with Journal(path, identity) as journal:
            if point == "valid_head":
                assert journal.append(command, event, None, None).seq == 1
                assert not (path / ".HEAD.pending").exists()
            else:
                with pytest.raises(JournalError, match="corrupt"):
                    journal.verify()
                if point == "partial_head":
                    assert (path / ".HEAD.pending").read_bytes()
        assert (path / "entries.log").read_bytes() == before
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
