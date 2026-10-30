"""Owned synthetic POSIX faults; no production path, key, service or DB."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import errno
import fcntl
import multiprocessing
import os
import time

import pytest

from orchestrator.services.restore_history import store
from orchestrator.services.restore_history.model import HistoryError, encode_entry
from tests.restore_history_support import (
    HISTORY,
    KEY,
    ZERO,
    accept,
    send,
    initialize_test_directory,
)


def owned(path):
    identity = initialize_test_directory(path)
    return identity, store.Ownership(os.geteuid(), frozenset({os.geteuid()}), os.getegid())


def open_store(path, identity, ownership):
    return store.HistoryStore(path, identity, ownership, deadline=time.monotonic() + 5)


def test_append_duplicate_roundtrip_and_no_initializer(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        first = h.append(accept(), deadline=time.monotonic() + 5)
        assert first.created and first.entry.seq == 1
        assert not h.append(accept(), deadline=time.monotonic() + 5).created
        second = h.append(send(), deadline=time.monotonic() + 5)
        assert h.verify(deadline=time.monotonic() + 5).entries == (first.entry, second.entry)
    with pytest.raises(HistoryError):
        open_store(tmp_path / "missing", identity, ownership)
    assert not (tmp_path / "missing").exists()
    identity.verify_key(KEY)
    with pytest.raises(HistoryError, match="key_mismatch"):
        identity.verify_key(bytes(32))


def test_short_writes_and_durability_order(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    events = []
    write = os.write
    sync = os.fsync
    rename = os.replace

    def short(fd, data):
        return write(fd, data[:7])

    def fsync(fd):
        events.append(Path(os.readlink(f"/proc/self/fd/{fd}")).name)
        return sync(fd)

    def replace_head(*args, **kwargs):
        events.append("rename")
        return rename(*args, **kwargs)

    with open_store(path, identity, ownership) as h:
        monkeypatch.setattr(os, "write", short)
        monkeypatch.setattr(os, "fsync", fsync)
        monkeypatch.setattr(os, "replace", replace_head)
        h.append(accept(), deadline=time.monotonic() + 5)
    assert events == ["LOG", "HEAD.pending", "rename", "h"]


@pytest.mark.parametrize("kind", ["tail", "complete_suffix", "pending", "corrupt", "unknown"])
def test_interrupted_and_corrupt_state_preserved(tmp_path, kind):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    if kind == "tail":
        (path / "LOG").write_bytes(b"\x00")
    if kind == "complete_suffix":
        (path / "LOG").write_bytes(encode_entry(HISTORY, 1, ZERO, accept()))
    if kind == "pending":
        (path / "HEAD.pending").write_bytes(b"{")
        (path / "HEAD.pending").chmod(0o660)
    if kind == "corrupt":
        (path / "LOG").write_bytes(b"\x00\x00\x00\x01x" + bytes(32))
    if kind == "unknown":
        (path / "unknown").write_bytes(b"keep")
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    with pytest.raises(HistoryError):
        open_store(path, identity, ownership)
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}


@pytest.mark.parametrize(
    "fault", ["write", "zero_write", "log_sync", "head_sync", "rename", "directory_sync"]
)
def test_fault_after_attempt_is_uncertain_and_poisoned(tmp_path, monkeypatch, fault):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    sync = os.fsync
    write = os.write

    def fail_sync(fd):
        name = Path(os.readlink(f"/proc/self/fd/{fd}")).name
        if (fault, name) in [
            ("log_sync", "LOG"),
            ("head_sync", "HEAD.pending"),
            ("directory_sync", "h"),
        ]:
            raise OSError(errno.EIO, "synthetic")
        return sync(fd)

    def fail_write(fd, data):
        if fault == "write":
            raise OSError(errno.ENOSPC, "synthetic")
        return 0 if fault == "zero_write" else write(fd, data)

    with open_store(path, identity, ownership) as h:
        monkeypatch.setattr(os, "fsync", fail_sync)
        monkeypatch.setattr(os, "write", fail_write)
        if fault == "rename":
            monkeypatch.setattr(
                os,
                "replace",
                lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EIO, "synthetic")),
            )
        with pytest.raises(HistoryError) as caught:
            h.append(accept(), deadline=time.monotonic() + 5)
        assert caught.value.uncertain
        with pytest.raises(HistoryError, match="store_poisoned"):
            h.append(accept(), deadline=time.monotonic() + 5)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "mode", "identity", "pin"])
def test_permission_identity_and_inode_checks(tmp_path, kind):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        if kind == "mode":
            (path / "LOG").chmod(0o666)
        if kind == "symlink":
            (path / "LOG").unlink()
            (path / "LOG").symlink_to(path / "HEAD")
        if kind == "hardlink":
            os.link(path / "LOG", tmp_path / "linked")
        if kind == "identity":
            identity = replace(identity, history_id=uuid4())
            h.expected = identity
        if kind == "pin":
            (path / "LOG").rename(path / "old")
            (path / "LOG").touch(mode=0o660)
            (path / "LOG").chmod(0o660)
            (path / "old").unlink()
        with pytest.raises(HistoryError):
            h.verify(deadline=time.monotonic() + 5)


def test_deadline_before_write_and_after_durable_io(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        with pytest.raises(HistoryError, match="deadline"):
            h.append(accept(), deadline=time.monotonic() - 1)
        assert (path / "LOG").read_bytes() == b""
        sync = os.fsync
        now = time.monotonic()
        clock = [now]

        def late(fd):
            sync(fd)
            clock[0] = now + 10

        monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(os, "fsync", late)
        with pytest.raises(HistoryError) as caught:
            h.append(accept(), deadline=now + 5)
        assert caught.value.uncertain
        assert (path / "LOG").stat().st_size > 0


def process_append(path, identity, ownership, queue):
    try:
        with open_store(path, identity, ownership) as h:
            queue.put(h.append(accept(), deadline=time.monotonic() + 5).created)
    except HistoryError as exc:
        queue.put(exc.code)


def test_multiprocess_same_operation_one_created(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    children = [
        ctx.Process(target=process_append, args=(path, identity, ownership, queue))
        for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        assert sorted([queue.get(timeout=2), queue.get(timeout=2)]) == [False, True]
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
        queue.close()
        queue.join_thread()


def test_external_lock_uses_absolute_deadline(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        fd = os.open(path / "OP_LOCK", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with pytest.raises(HistoryError, match="deadline"):
                h.verify(deadline=time.monotonic() + 0.02)
        finally:
            os.close(fd)


def test_warm_scan_sees_pending_and_identity_boolean_is_rejected(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        h.append(accept(), deadline=time.monotonic() + 5)
        h.verify(deadline=time.monotonic() + 5)
        (path / "HEAD.pending").write_bytes(b"keep")
        (path / "HEAD.pending").chmod(0o660)
        with pytest.raises(HistoryError, match="recovery_required"):
            h.verify(deadline=time.monotonic() + 5)
        assert (path / "HEAD.pending").read_bytes() == b"keep"
    (path / "HEAD.pending").unlink()
    meta = (path / "IDENTITY").read_bytes().replace(b'"v":1', b'"v":true')
    (path / "IDENTITY").chmod(0o660)
    (path / "IDENTITY").write_bytes(meta)
    (path / "IDENTITY").chmod(0o440)
    with pytest.raises(HistoryError, match="identity_mismatch"):
        open_store(path, identity, ownership)


def test_conflict_and_capacity_do_not_mutate(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        h.append(accept(), deadline=time.monotonic() + 5)
        before = {p.name: p.read_bytes() for p in path.iterdir()}
        with pytest.raises(HistoryError, match="conflict"):
            h.append(
                replace(accept(), data=replace(accept().data, request_commitment="c" * 64)),
                deadline=time.monotonic() + 5,
            )
        monkeypatch.setattr(store, "MAX_LOG", (path / "LOG").stat().st_size)
        with pytest.raises(HistoryError, match="capacity"):
            h.append(send(), deadline=time.monotonic() + 5)
        assert before == {p.name: p.read_bytes() for p in path.iterdir()}


def test_log_oversize_refused_without_truncation(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with (path / "LOG").open("r+b") as log:
        log.truncate(store.MAX_LOG + 1)
    with pytest.raises(HistoryError, match="capacity"):
        open_store(path, identity, ownership)
    assert (path / "LOG").stat().st_size == 16777217


def test_close_waits_for_native_io_owner(tmp_path, monkeypatch):
    import threading

    path = tmp_path / "h"
    identity, ownership = owned(path)
    h = open_store(path, identity, ownership)
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    results = []
    sync = os.fsync

    def paused(fd):
        entered.set()
        assert release.wait(5)
        return sync(fd)

    monkeypatch.setattr(os, "fsync", paused)

    def append():
        try:
            results.append(h.append(accept(), deadline=time.monotonic() + 5).created)
        except BaseException as exc:
            results.append(type(exc).__name__)

    def close():
        h.close()
        closed.set()

    writer = threading.Thread(target=append)
    closer = threading.Thread(target=close)
    writer.start()
    try:
        assert entered.wait(5)
        closer.start()
        assert not closed.wait(0.05)
    finally:
        release.set()
        writer.join(5)
        if closer.ident is not None:
            closer.join(5)
        h.close()
    assert results == [True] and closed.is_set()


def test_unlock_failure_cannot_return_created(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    flock = fcntl.flock
    with open_store(path, identity, ownership) as h:

        def fail_unlock(fd, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError(errno.EIO, "synthetic")
            return flock(fd, operation)

        monkeypatch.setattr(fcntl, "flock", fail_unlock)
        with pytest.raises(HistoryError, match="durable_result_unavailable") as caught:
            h.append(accept(), deadline=time.monotonic() + 5)
        assert caught.value.uncertain
    monkeypatch.setattr(fcntl, "flock", flock)
    with open_store(path, identity, ownership) as h:
        assert len(h.verify(deadline=time.monotonic() + 5).entries) == 1


def crash_writer(path, identity, ownership, connection):
    sync = os.fsync

    def pause_after_log(fd):
        sync(fd)
        if Path(os.readlink(f"/proc/self/fd/{fd}")).name == "LOG":
            connection.send("log_synced")
            connection.recv()

    os.fsync = pause_after_log
    with open_store(path, identity, ownership) as h:
        h.append(accept(), deadline=time.monotonic() + 10)


def test_process_death_after_log_sync_retains_suffix_and_blocks(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    writer = context.Process(target=crash_writer, args=(path, identity, ownership, child))
    writer.start()
    child.close()
    try:
        assert parent.poll(5) and parent.recv() == "log_synced"
        writer.terminate()
        writer.join(5)
        assert not writer.is_alive()
        before = {p.name: p.read_bytes() for p in path.iterdir()}
        with pytest.raises(HistoryError, match="recovery_required"):
            open_store(path, identity, ownership)
        assert before == {p.name: p.read_bytes() for p in path.iterdir()}
    finally:
        if writer.is_alive():
            writer.terminate()
            writer.join(5)
        parent.close()


def test_non_regular_file_cannot_block_open(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    (path / "LOG").unlink()
    os.mkfifo(path / "LOG", 0o660)
    with pytest.raises(HistoryError, match="unsafe_storage"):
        open_store(path, identity, ownership)


def test_record_capacity_and_invalid_deadline(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        h.append(accept(), deadline=time.monotonic() + 5)
        monkeypatch.setattr(store, "MAX_RECORDS", 1)
        with pytest.raises(HistoryError, match="capacity"):
            h.append(send(), deadline=time.monotonic() + 5)
        for deadline in [float("inf"), float("nan"), True]:
            with pytest.raises(HistoryError, match="invalid_deadline"):
                h.verify(deadline=deadline)


def test_cancellation_preserves_exception_and_poison(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        original = KeyboardInterrupt("synthetic cancellation")

        def interrupt(fd):
            raise original

        monkeypatch.setattr(os, "fsync", interrupt)
        with pytest.raises(KeyboardInterrupt) as caught:
            h.append(accept(), deadline=time.monotonic() + 5)
        assert caught.value is original
        assert any("uncertain" in note for note in original.__notes__)
        with pytest.raises(HistoryError, match="store_poisoned"):
            h.append(accept(), deadline=time.monotonic() + 5)


def test_waiting_thread_cannot_reset_uncertain_append_outcome(tmp_path, monkeypatch):
    import threading

    path = tmp_path / "h"
    identity, ownership = owned(path)
    entered = threading.Event()
    waiting = threading.Event()
    release = threading.Event()
    outcomes = []
    with open_store(path, identity, ownership) as h:

        def fail(fd):
            entered.set()
            assert release.wait(5)
            raise OSError(errno.EIO, "synthetic")

        monkeypatch.setattr(os, "fsync", fail)

        def append(command, second=False):
            if second:
                waiting.set()
            try:
                outcomes.append(h.append(command, deadline=time.monotonic() + 5).created)
            except HistoryError as exc:
                outcomes.append(exc.code)

        one = threading.Thread(target=append, args=(accept(),))
        two = threading.Thread(target=append, args=(send(), True))
        one.start()
        try:
            assert entered.wait(5)
            two.start()
            assert waiting.wait(5)
        finally:
            release.set()
            one.join(5)
            if two.ident is not None:
                two.join(5)
        assert set(outcomes) == {"unknown_append", "store_poisoned"}


def test_close_attempts_all_descriptors_without_retry(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    h = open_store(path, identity, ownership)
    close = os.close
    calls = []

    def failed_once(fd):
        calls.append(fd)
        close(fd)
        if len(calls) == 1:
            raise OSError(errno.EIO, "synthetic")

    monkeypatch.setattr(os, "close", failed_once)
    with pytest.raises(HistoryError, match="cleanup_unavailable"):
        h.close()
    assert len(calls) == len(set(calls)) == 6
    for fd in calls:
        with pytest.raises(OSError):
            os.fstat(fd)
    h.close()


def test_deadline_in_final_unlock_cannot_return_created(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        now = time.monotonic()
        clock = [now]
        flock = fcntl.flock

        def late_unlock(fd, operation):
            result = flock(fd, operation)
            if operation == fcntl.LOCK_UN:
                clock[0] = now + 10
            return result

        monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(fcntl, "flock", late_unlock)
        with pytest.raises(HistoryError, match="durable_result_unavailable") as caught:
            h.append(accept(), deadline=now + 5)
        assert caught.value.uncertain
