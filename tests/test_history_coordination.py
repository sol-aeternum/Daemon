"""Uncalled ownership boundary; only owned synthetic POSIX files/threads."""

import asyncio
import fcntl
import os
import threading
import time

import pytest

from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.store import HistoryStore
from tests.restore_history_support import accept
from tests.test_restore_history_ownership import fork_result
from tests.test_restore_history_store import open_store, owned


def available(path, name, mode=fcntl.LOCK_EX):
    fd = os.open(path / name, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    finally:
        os.close(fd)


async def signaled(event):
    assert await asyncio.to_thread(event.wait, 5), "owned worker did not reach fence"


async def opened(path):
    identity, ownership = owned(path)
    coordinator = HistoryCoordinator(path, identity, ownership)
    await coordinator.open(deadline=time.monotonic() + 5)
    return coordinator, identity, ownership


def test_synchronous_operation_retains_op_and_lifetime_but_not_append_lock(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as store:
        assert not available(path, "RUNTIME_LOCK")
        with store.operation(deadline=time.monotonic() + 5):
            assert not available(path, "OP_LOCK")
            assert available(path, "APPEND_LOCK")
            assert store.append(accept(), deadline=time.monotonic() + 5).created
            assert not available(path, "OP_LOCK")
            assert available(path, "APPEND_LOCK")
            assert len(store.verify(deadline=time.monotonic() + 5).entries) == 1
            with pytest.raises(HistoryError, match="operation_active"):
                store.close()
        assert available(path, "OP_LOCK")
        assert not available(path, "RUNTIME_LOCK")
    assert available(path, "RUNTIME_LOCK")


def test_child_store_close_does_not_unlock_parent_lifetime(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as store:

        def child():
            store.close()
            return {"closed": True}

        assert fork_result(child) == {"closed": True}
        assert not available(path, "RUNTIME_LOCK")
        assert store.append(accept(), deadline=time.monotonic() + 5).created
    assert available(path, "RUNTIME_LOCK")


@pytest.mark.parametrize("name", ["OP_LOCK", "APPEND_LOCK"])
@pytest.mark.parametrize("after_native", [False, True])
@pytest.mark.parametrize("error_type", [OSError, asyncio.CancelledError])
def test_interrupted_acquisition_is_owned_before_attempt_and_cannot_resume(
    tmp_path, monkeypatch, name, after_native, error_type
):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as store:
        native = fcntl.flock
        target = store._fds[name]
        original = error_type("synthetic acquisition interruption")

        def interrupted(fd, flags):
            if fd == target and flags == fcntl.LOCK_EX | fcntl.LOCK_NB:
                if after_native:
                    native(fd, flags)
                raise original
            return native(fd, flags)

        monkeypatch.setattr(fcntl, "flock", interrupted)
        with pytest.raises((OSError, HistoryError, asyncio.CancelledError)) as caught:
            with store.operation(deadline=time.monotonic() + 5):
                store.append(accept(), deadline=time.monotonic() + 5)
        if error_type is asyncio.CancelledError:
            assert caught.value is original
        monkeypatch.setattr(fcntl, "flock", native)
        assert available(path, "OP_LOCK") and available(path, "APPEND_LOCK")
        with pytest.raises(HistoryError, match="store_poisoned"):
            store.append(accept(), deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_op_precedes_simulated_native_transaction_and_history_is_not_fresh(tmp_path):
    path = tmp_path / "h"
    coordinator, _, _ = await opened(path)
    try:
        async with coordinator.operation(deadline=time.monotonic() + 5) as operation:
            # This is only a native-boundary sentinel, not DB integration.
            assert not available(path, "OP_LOCK")
            assert available(path, "APPEND_LOCK")
            first = await operation.append(accept())
            assert first.created
            assert not available(path, "OP_LOCK")
        async with coordinator.operation(deadline=time.monotonic() + 5) as operation:
            historical = await operation.append(accept())
            assert not historical.created and historical.entry == first.entry
        with pytest.raises(HistoryError, match="invalid_operation"):
            await operation.verify()
    finally:
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "timeout"])
async def test_late_success_remains_blocked_and_physical_owner_is_drained(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "h"
    coordinator, identity, ownership = await opened(path)
    entered, release = threading.Event(), threading.Event()
    native = HistoryStore.append

    def delayed(self, command, *, deadline):
        result = native(self, command, deadline=deadline)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(HistoryStore, "append", delayed)

    async def write():
        async with coordinator.operation(
            deadline=time.monotonic() + (0.08 if failure == "timeout" else 5)
        ) as operation:
            return await operation.append(accept())

    task = asyncio.create_task(write())
    try:
        await signaled(entered)
        if failure == "cancel":
            task.cancel("original cancellation")
            await asyncio.sleep(0)
            task.cancel("second cancellation")
        else:
            await asyncio.sleep(0.1)
        assert not task.done()
        assert not available(path, "OP_LOCK")
        assert not available(path, "RUNTIME_LOCK")
        assert coordinator._store is not None
        for fd in coordinator._store._fds.values():
            os.fstat(fd)  # close must not invalidate the still-running owner's FDs
        release.set()
        error = asyncio.CancelledError if failure == "cancel" else TimeoutError
        with pytest.raises(error) as caught:
            await task
        if failure == "cancel":
            assert caught.value.args == ("original cancellation",)
        with pytest.raises(HistoryError, match="coordinator_blocked"):
            async with coordinator.operation(deadline=time.monotonic() + 5):
                pytest.fail("uncertain invocation resumed")
    finally:
        release.set()
        await coordinator.aclose(deadline=time.monotonic() + 5)
    monkeypatch.setattr(HistoryStore, "append", native)
    with open_store(path, identity, ownership) as store:
        assert len(store.verify(deadline=time.monotonic() + 5).entries) == 1
        assert not store.append(accept(), deadline=time.monotonic() + 5).created


@pytest.mark.asyncio
async def test_shutdown_waits_for_scope_and_cancelled_close_cannot_release_it(tmp_path):
    path = tmp_path / "h"
    coordinator, _, _ = await opened(path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def body():
        async with coordinator.operation(deadline=time.monotonic() + 5):
            entered.set()
            await release.wait()

    active = asyncio.create_task(body())
    await entered.wait()
    closing = asyncio.create_task(coordinator.aclose(deadline=time.monotonic() + 5))
    try:
        await asyncio.sleep(0)
        closing.cancel("shutdown cancelled")
        await asyncio.sleep(0)
        closing.cancel("cancel again")
        await asyncio.sleep(0)
        assert not closing.done()
        assert not available(path, "OP_LOCK") and not available(path, "RUNTIME_LOCK")
        with pytest.raises(HistoryError):
            async with coordinator.operation(deadline=time.monotonic() + 5):
                pytest.fail("new scope admitted during shutdown")
        release.set()
        with pytest.raises(HistoryError, match="coordinator_blocked"):
            await active
        with pytest.raises(asyncio.CancelledError) as caught:
            await closing
        assert caught.value.args == ("shutdown cancelled",)
        assert available(path, "RUNTIME_LOCK") and available(path, "OP_LOCK")
    finally:
        release.set()
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_unlock_failure_cannot_return_scope_success(tmp_path, monkeypatch):
    path = tmp_path / "h"
    coordinator, _, _ = await opened(path)
    native = fcntl.flock
    assert coordinator._store is not None
    target = coordinator._store._fds["OP_LOCK"]
    failed = False

    def fail(fd, flags):
        nonlocal failed
        if fd == target and flags == fcntl.LOCK_UN and not failed:
            failed = True
            raise OSError("synthetic unlock failure")
        return native(fd, flags)

    monkeypatch.setattr(fcntl, "flock", fail)
    try:
        with pytest.raises(OSError, match="synthetic unlock"):
            async with coordinator.operation(deadline=time.monotonic() + 5) as operation:
                assert (await operation.append(accept())).created
        assert failed and not available(path, "OP_LOCK")
        with pytest.raises(HistoryError, match="coordinator_blocked"):
            async with coordinator.operation(deadline=time.monotonic() + 5):
                pytest.fail("lock failure became success")
    finally:
        await coordinator.aclose(deadline=time.monotonic() + 5)
    assert available(path, "OP_LOCK")


@pytest.mark.asyncio
async def test_close_failure_is_replayed_as_failure_without_retrying_fd(tmp_path, monkeypatch):
    coordinator, _, _ = await opened(tmp_path / "h")
    assert coordinator._store is not None
    descriptors = {*coordinator._store._fds.values(), coordinator._store._dir}
    calls = []
    native = os.close
    target = coordinator._store._fds["LOG"]

    def fail(fd):
        if fd in descriptors:
            calls.append(fd)
        native(fd)
        if fd == target:
            raise OSError("synthetic close result unavailable")

    monkeypatch.setattr(os, "close", fail)
    for _ in range(2):
        with pytest.raises(HistoryError, match="cleanup_unavailable"):
            await coordinator.aclose(deadline=time.monotonic() + 5)
    assert len(calls) == len(descriptors) == len(set(calls))


@pytest.mark.asyncio
async def test_distinct_store_contention_and_no_retry_after_deadline(tmp_path):
    path = tmp_path / "h"
    first, identity, ownership = await opened(path)
    second = HistoryCoordinator(path, identity, ownership)
    await second.open(deadline=time.monotonic() + 5)
    try:
        async with first.operation(deadline=time.monotonic() + 5):
            with pytest.raises((HistoryError, TimeoutError)):
                async with second.operation(deadline=time.monotonic() + 0.05):
                    pytest.fail("OP contention bypassed")
            assert not available(path, "OP_LOCK")
        with pytest.raises(HistoryError, match="coordinator_blocked"):
            async with second.operation(deadline=time.monotonic() + 5):
                pytest.fail("deadline automatically retried")
    finally:
        await first.aclose(deadline=time.monotonic() + 5)
        await second.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["LOG", "HEAD.pending", "directory"])
async def test_cancel_and_shutdown_retain_locks_until_fsync_ends(tmp_path, monkeypatch, phase):
    path = tmp_path / "h"
    coordinator, _, _ = await opened(path)
    assert coordinator._store is not None
    descriptors = {*coordinator._store._fds.values(), coordinator._store._dir}
    target = coordinator._store._dir if phase == "directory" else None
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    native = os.fsync

    def blocked(fd):
        name = os.readlink(f"/proc/self/fd/{fd}").rsplit("/", 1)[-1]
        if (phase == "directory" and fd == target) or name == phase:
            entered.set()
            assert release.wait(5)
            try:
                return native(fd)
            finally:
                ended.set()
        return native(fd)

    monkeypatch.setattr(os, "fsync", blocked)

    async def write():
        async with coordinator.operation(deadline=time.monotonic() + 5) as operation:
            await operation.append(accept())

    task = asyncio.create_task(write())
    closing = None
    try:
        await signaled(entered)
        task.cancel("fsync owner cancelled")
        closing = asyncio.create_task(coordinator.aclose(deadline=time.monotonic() + 5))
        await asyncio.sleep(0)
        assert not task.done() and not closing.done() and not ended.is_set()
        for name in ("RUNTIME_LOCK", "OP_LOCK", "APPEND_LOCK"):
            assert not available(path, name)
        for fd in descriptors:
            os.fstat(fd)
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("fsync owner cancelled",)
        await closing
        assert ended.is_set()
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        release.set()
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_cancellation_during_unlock_preserves_body_error_and_drains_cleanup(
    tmp_path, monkeypatch
):
    path = tmp_path / "h"
    coordinator, _, _ = await opened(path)
    assert coordinator._store is not None
    target = coordinator._store._fds["OP_LOCK"]
    entered, release = threading.Event(), threading.Event()
    native = fcntl.flock
    original = RuntimeError("original body error")

    def delayed(fd, flags):
        if fd == target and flags == fcntl.LOCK_UN:
            entered.set()
            assert release.wait(5)
            native(fd, flags)
            raise OSError("secondary cleanup error")
        return native(fd, flags)

    monkeypatch.setattr(fcntl, "flock", delayed)

    async def body():
        async with coordinator.operation(deadline=time.monotonic() + 5):
            raise original

    task = asyncio.create_task(body())
    try:
        await signaled(entered)
        task.cancel("cleanup cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated cleanup cancellation")
        await asyncio.sleep(0)
        assert not task.done() and not available(path, "OP_LOCK")
        release.set()
        with pytest.raises(RuntimeError) as caught:
            await task
        assert caught.value is original
        assert any("cleanup" in x.lower() for x in original.__notes__)
    finally:
        release.set()
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_open_close_race_waits_for_initializing_physical_owner(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    coordinator = HistoryCoordinator(path, identity, ownership)
    entered, release = threading.Event(), threading.Event()
    native = HistoryStore.__init__

    def delayed(self, *args, **kwargs):
        native(self, *args, **kwargs)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(HistoryStore, "__init__", delayed)
    opening = asyncio.create_task(coordinator.open(deadline=time.monotonic() + 5))
    closing = None
    try:
        await signaled(entered)
        closing = asyncio.create_task(coordinator.aclose(deadline=time.monotonic() + 5))
        await asyncio.sleep(0)
        assert not closing.done() and not available(path, "RUNTIME_LOCK")
        release.set()
        with pytest.raises(HistoryError, match="coordinator_closed"):
            await opening
        await closing
        assert available(path, "RUNTIME_LOCK")
    finally:
        release.set()
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_exclusive_lifetime_owner_refuses_open_without_releasing_its_lock(tmp_path):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    fd = os.open(path / "RUNTIME_LOCK", os.O_RDWR)
    coordinator = HistoryCoordinator(path, identity, ownership)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises((HistoryError, TimeoutError)):
            await coordinator.open(deadline=time.monotonic() + 0.05)
        assert not available(path, "RUNTIME_LOCK")
        assert available(path, "OP_LOCK")
        with pytest.raises(HistoryError):
            await coordinator.open(deadline=time.monotonic() + 5)
    finally:
        os.close(fd)
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_lease_cannot_be_used_from_another_task_even_if_failure_is_caught(tmp_path):
    coordinator, _, _ = await opened(tmp_path / "h")
    try:
        with pytest.raises(HistoryError, match="coordinator_blocked"):
            async with coordinator.operation(deadline=time.monotonic() + 5) as operation:
                with pytest.raises(HistoryError, match="invalid_operation"):
                    await asyncio.create_task(operation.verify())
    finally:
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_caught_worker_error_cannot_make_scope_exit_successful(tmp_path, monkeypatch):
    coordinator, _, _ = await opened(tmp_path / "h")

    def fail(*args, **kwargs):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(HistoryStore, "_write", fail)
    try:
        with pytest.raises(HistoryError, match="coordinator_blocked"):
            async with coordinator.operation(deadline=time.monotonic() + 5) as operation:
                with pytest.raises(HistoryError, match="unknown_append"):
                    await operation.append(accept())
    finally:
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_shutdown_deadline_drains_scope_before_propagating_timeout(tmp_path):
    path = tmp_path / "h"
    coordinator, _, _ = await opened(path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def body():
        async with coordinator.operation(deadline=time.monotonic() + 5):
            entered.set()
            await release.wait()

    active = asyncio.create_task(body())
    closing = None
    try:
        await entered.wait()
        closing = asyncio.create_task(coordinator.aclose(deadline=time.monotonic() + 0.04))
        await asyncio.sleep(0.06)
        assert not closing.done() and not available(path, "RUNTIME_LOCK")
        release.set()
        with pytest.raises(HistoryError, match="coordinator_blocked"):
            await active
        with pytest.raises(TimeoutError):
            await closing
        assert available(path, "RUNTIME_LOCK")
    finally:
        release.set()
        await asyncio.gather(
            active, *([closing] if closing is not None else []), return_exceptions=True
        )
        await coordinator.aclose(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["open", "operation", "aclose"])
async def test_inherited_coordinator_refuses_before_touching_parent_ownership(tmp_path, method):
    path = tmp_path / "h"
    coordinator, _, _ = await opened(path)
    try:
        async with coordinator.operation(deadline=time.monotonic() + 5) as operation:

            def child():
                # The PID guard must refuse before even requesting a loop or
                # reaching an await; do not run the inherited loop in a child.
                call = getattr(coordinator, method)(deadline=time.monotonic() + 1)
                pending = call.__aenter__() if method == "operation" else call
                try:
                    pending.send(None)
                except HistoryError as exc:
                    return {"outcome": exc.code}
                finally:
                    pending.close()
                return {"outcome": "inherited_use_allowed"}

            assert fork_result(child) == {"outcome": "forked_coordinator"}
            assert not available(path, "OP_LOCK") and not available(path, "RUNTIME_LOCK")
            assert (await operation.append(accept())).created
    finally:
        await coordinator.aclose(deadline=time.monotonic() + 5)
