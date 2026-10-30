"""Real fork and cleanup cancellation regressions; owned synthetic stores only."""

import asyncio
import fcntl
import json
import os
import select
import signal
import time

import pytest

from orchestrator.services.restore_history.model import HistoryError
from tests.restore_history_support import accept, send
from tests.test_restore_history_store import open_store, owned


@pytest.mark.parametrize("operation", ["append", "verify"])
@pytest.mark.parametrize("unlock_index", [0, 1])
@pytest.mark.parametrize("after_native", [False, True])
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_cancellation_at_each_unlock_attempts_all_and_poisons(
    tmp_path, monkeypatch, operation, unlock_index, after_native, exception_type
):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        native = fcntl.flock
        expected = [h._fds["APPEND_LOCK"], h._fds["OP_LOCK"]]
        calls = []
        cancelled = exception_type("synthetic unlock cancellation")

        def interrupt(fd, flags):
            if flags != fcntl.LOCK_UN:
                return native(fd, flags)
            index = len(calls)
            calls.append(fd)
            if index == unlock_index and not after_native:
                raise cancelled
            result = native(fd, flags)
            if index == unlock_index:
                raise cancelled
            return result

        monkeypatch.setattr(fcntl, "flock", interrupt)
        with pytest.raises(exception_type) as caught:
            if operation == "append":
                h.append(accept(), deadline=time.monotonic() + 5)
            else:
                h.verify(deadline=time.monotonic() + 5)
        assert caught.value is cancelled
        assert calls == expected
        monkeypatch.setattr(fcntl, "flock", native)
        with pytest.raises(HistoryError, match="store_poisoned"):
            h.append(accept() if operation == "verify" else send(), deadline=time.monotonic() + 5)


@pytest.mark.parametrize("unlock_index", [0, 1])
@pytest.mark.parametrize(
    "exception_type", [RuntimeError, KeyboardInterrupt, asyncio.CancelledError]
)
def test_unlock_cancellation_preserves_protected_body_exception(
    tmp_path, monkeypatch, unlock_index, exception_type
):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        original = exception_type("synthetic body failure")
        native = fcntl.flock
        calls = []

        def fail_sync(fd):
            raise original

        def interrupt(fd, flags):
            if flags == fcntl.LOCK_UN:
                index = len(calls)
                calls.append(fd)
                if index == unlock_index:
                    raise KeyboardInterrupt("synthetic cleanup failure")
            return native(fd, flags)

        monkeypatch.setattr(os, "fsync", fail_sync)
        monkeypatch.setattr(fcntl, "flock", interrupt)
        # Capture a wrongly substituted KeyboardInterrupt as a test failure,
        # rather than letting old broken cleanup abort the entire pytest run.
        with pytest.raises(BaseException) as caught:
            h.append(accept(), deadline=time.monotonic() + 5)
        assert caught.value is original
        assert calls == [h._fds["APPEND_LOCK"], h._fds["OP_LOCK"]]
        assert any("cleanup" in note.lower() for note in original.__notes__)
        monkeypatch.setattr(fcntl, "flock", native)
        with pytest.raises(HistoryError, match="store_poisoned"):
            h.append(accept(), deadline=time.monotonic() + 5)


def test_cleanup_cancellation_is_not_hidden_by_ambient_exception(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        cancelled = KeyboardInterrupt("synthetic cleanup failure")
        native = fcntl.flock
        calls = []

        def interrupt(fd, flags):
            if flags == fcntl.LOCK_UN:
                calls.append(fd)
                if len(calls) == 1:
                    raise cancelled
            return native(fd, flags)

        monkeypatch.setattr(fcntl, "flock", interrupt)
        try:
            raise RuntimeError("unrelated outer handler")
        except RuntimeError:
            with pytest.raises(KeyboardInterrupt) as caught:
                h.append(accept(), deadline=time.monotonic() + 5)
        assert caught.value is cancelled
        assert calls == [h._fds["APPEND_LOCK"], h._fds["OP_LOCK"]]


def fork_result(callback):
    """One real child; alarm bounds old-code deadlocks, never other processes."""
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(3)
        try:
            value = callback()
        except BaseException as exc:
            value = {"unexpected_exception": type(exc).__name__}
        try:
            os.write(writer, json.dumps(value).encode())
        finally:
            os._exit(0)
    os.close(writer)
    reaped = False
    try:
        ready, _, _ = select.select([reader], [], [], 5)
        assert ready, "owned fork child did not exit or produce a result"
        raw = os.read(reader, 4096)
        _, status = os.waitpid(pid, 0)
        reaped = True
        assert os.waitstatus_to_exitcode(status) == 0, (
            "owned child timed out in inherited operation"
        )
        assert raw, "owned child failed before reporting"
        return json.loads(raw)
    finally:
        os.close(reader)
        if not reaped:
            # An unreaped child PID cannot have been recycled to unrelated work.
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)


@pytest.mark.parametrize("operation", ["append", "verify", "enter", "close", "exit"])
@pytest.mark.parametrize("mutex_held", [False, True])
def test_inherited_store_rejects_use_and_close_keeps_parent_locks(tmp_path, operation, mutex_held):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as h:
        before = (path / "LOG").read_bytes()
        descriptors = [*h._fds.values(), h._dir]
        for name in ["OP_LOCK", "APPEND_LOCK"]:
            fcntl.flock(h._fds[name], fcntl.LOCK_EX)
        if mutex_held:
            h._mutex.acquire()
        try:

            def child():
                try:
                    if operation == "append":
                        h.append(accept(), deadline=time.monotonic() + 1)
                    elif operation == "verify":
                        h.verify(deadline=time.monotonic() + 1)
                    elif operation == "enter":
                        h.__enter__()
                    elif operation == "close":
                        h.close()
                    else:
                        h.__exit__(None, None, None)
                except HistoryError as exc:
                    outcome = exc.code
                else:
                    outcome = (
                        "closed" if operation in ["close", "exit"] else "inherited_use_allowed"
                    )
                if operation in ["close", "exit"]:
                    for fd in descriptors:
                        try:
                            os.fstat(fd)
                        except OSError:
                            continue
                        return {"outcome": "inherited_descriptor_not_closed"}
                    h.close()  # safe and idempotent without the inherited mutex
                return {"outcome": outcome}

            result = fork_result(child)
            expected = "closed" if operation in ["close", "exit"] else "forked_store"
            assert result == {"outcome": expected}
            assert (path / "LOG").read_bytes() == before
            # A different open description must still be excluded by the parent.
            probe = os.open(path / "OP_LOCK", os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe)
        finally:
            if mutex_held:
                h._mutex.release()
            for name in ["APPEND_LOCK", "OP_LOCK"]:
                fcntl.flock(h._fds[name], fcntl.LOCK_UN)
        assert h.append(accept(), deadline=time.monotonic() + 5).created
        assert len(h.verify(deadline=time.monotonic() + 5).entries) == 1


@pytest.mark.parametrize("close_index", range(6))
def test_close_cancellation_attempts_other_descriptors_once(tmp_path, monkeypatch, close_index):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    h = open_store(path, identity, ownership)
    native = os.close
    calls = []
    cancelled = KeyboardInterrupt("synthetic close cancellation")

    def interrupt(fd):
        index = len(calls)
        calls.append(fd)
        native(fd)
        if index == close_index:
            raise cancelled

    monkeypatch.setattr(os, "close", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        h.close()
    assert caught.value is cancelled
    assert len(calls) == len(set(calls)) == 6
    for fd in calls:
        with pytest.raises(OSError):
            os.fstat(fd)
    h.close()


def test_context_exit_preserves_body_error_during_close_cancellation(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    h = open_store(path, identity, ownership)
    original = RuntimeError("synthetic context body failure")
    native = os.close
    calls = []

    def interrupt(fd):
        calls.append(fd)
        native(fd)
        if len(calls) == 1:
            raise KeyboardInterrupt("synthetic context cleanup cancellation")

    monkeypatch.setattr(os, "close", interrupt)
    with pytest.raises(BaseException) as caught:
        with h:
            raise original
    assert caught.value is original
    assert any("cleanup" in note.lower() for note in original.__notes__)
    assert len(calls) == len(set(calls)) == 6
    h.close()
