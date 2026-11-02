"""Read-only owner renewal; synthetic POSIX files, no production configuration."""

import asyncio
import time
from typing import Any
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
import pytest

from orchestrator.auth import AuthenticatedDevice
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.observation import (
    HistoricalObservation,
    HistoryObservation,
)
from orchestrator.services.restore_history import read_supervisor
from orchestrator.services.restore_history.store import HistoryStore
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import KEY
from tests.test_history_coordination import available
from tests.test_restore_history_store import owned


def deadline():
    return time.monotonic() + 5


@pytest.fixture
def reader(tmp_path, monkeypatch):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    pool: Any = object.__new__(asyncpg.Pool)
    encryption = ContentEncryption(Fernet.generate_key().decode())
    tasks = TaskStore(pool, encryption, MemoryStore(pool, encryption))
    projection = HistoryProjection(identity, acceptor="synthetic_a", worker="synthetic_w")
    qualifications = []

    async def qualify(operation, supplied_pool, supplied_projection, *, deadline):
        assert supplied_pool is pool and supplied_projection is projection
        await operation.verify()
        qualifications.append(operation._coordinator)

    monkeypatch.setattr(read_supervisor, "qualify_read_history", qualify)
    supervisor = read_supervisor.HistoryReadSupervisor(tasks, path, projection, ownership, KEY)
    auth = AuthenticatedDevice(uuid4(), uuid4(), uuid4())
    return supervisor, path, identity, ownership, auth, qualifications


@pytest.mark.asyncio
async def test_failed_read_closes_before_fresh_request_revalidates_and_never_retries(
    reader, monkeypatch
):
    supervisor, path, _, _, auth, qualifications = reader
    calls = []
    original = RuntimeError("synthetic read failure")

    async def lookup(self, *args, **kwargs):
        calls.append(self._history)
        if len(calls) == 1:
            raise original
        return HistoricalObservation("unknown")

    monkeypatch.setattr(HistoryObservation, "lookup", lookup)
    await supervisor.open(deadline=deadline())
    old = qualifications[0]
    try:
        with pytest.raises(RuntimeError) as caught:
            await supervisor.lookup(auth, "original", deadline=deadline())
        assert caught.value is original
        assert len(calls) == len(qualifications) == 1
        assert old._closed and old._executor is None
        assert available(path, "RUNTIME_LOCK")
        assert supervisor._owner is None
        assert (await supervisor.lookup(auth, "original", deadline=deadline())).kind == "unknown"
        assert len(calls) == len(qualifications) == 2
        assert qualifications[1] is not old
    finally:
        await supervisor.aclose(deadline=deadline())


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_cancel_or_deadline_drains_late_read_before_owner_replacement(
    reader, monkeypatch, expired
):
    supervisor, path, _, _, auth, qualifications = reader
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def lookup(self, *args, **kwargs):
        calls.append(self._history)
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return HistoricalObservation("task")  # Late success must be discarded.

    monkeypatch.setattr(HistoryObservation, "lookup", lookup)
    await supervisor.open(deadline=deadline())
    old = qualifications[0]
    task = asyncio.create_task(
        supervisor.lookup(auth, "original", deadline=time.monotonic() + (0.05 if expired else 5))
    )
    await entered.wait()
    if expired:
        await asyncio.sleep(0.08)
    else:
        task.cancel("first read cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated read cancellation")
    await asyncio.sleep(0)
    assert not task.done() and not old._closed
    assert not available(path, "RUNTIME_LOCK")
    assert len(calls) == len(qualifications) == 1
    release.set()
    try:
        with pytest.raises(TimeoutError if expired else asyncio.CancelledError) as caught:
            await task
        if not expired:
            assert caught.value.args == ("first read cancellation",)
        assert old._closed and old._executor is None
        assert supervisor._owner is None and not supervisor._blocked
        assert len(calls) == len(qualifications) == 1
    finally:
        await supervisor.aclose(deadline=deadline())


@pytest.mark.asyncio
async def test_failed_physical_close_latches_reads_without_retrying_descriptors(
    reader, monkeypatch
):
    supervisor, _, _, _, auth, qualifications = reader
    original = RuntimeError("synthetic read failure")
    close = HistoryStore.close
    closes = []

    async def lookup(*args, **kwargs):
        raise original

    def failed_close(self):
        closes.append(self)
        close(self)  # Even closed descriptors do not make a failed close success.
        raise OSError("synthetic close acknowledgement failure")

    monkeypatch.setattr(HistoryObservation, "lookup", lookup)
    await supervisor.open(deadline=deadline())
    monkeypatch.setattr(HistoryStore, "close", failed_close)
    with pytest.raises(RuntimeError) as caught:
        await supervisor.lookup(auth, "original", deadline=deadline())
    assert caught.value is original and supervisor._blocked
    assert len(closes) == 1 and len(qualifications) == 1
    with pytest.raises(HistoryError):
        await supervisor.lookup(auth, "original", deadline=deadline())
    with pytest.raises(HistoryError):
        await supervisor.aclose(deadline=deadline())
    assert len(closes) == 1 and len(qualifications) == 1


@pytest.mark.asyncio
async def test_shutdown_drains_read_and_refuses_queued_new_requests(reader, monkeypatch):
    supervisor, path, _, _, auth, _ = reader
    entered, release = asyncio.Event(), asyncio.Event()

    async def lookup(*args, **kwargs):
        entered.set()
        await release.wait()
        return HistoricalObservation("unknown")

    monkeypatch.setattr(HistoryObservation, "lookup", lookup)
    await supervisor.open(deadline=deadline())
    task = asyncio.create_task(supervisor.lookup(auth, "original", deadline=deadline()))
    await entered.wait()
    closing = asyncio.create_task(supervisor.aclose(deadline=deadline()))
    await asyncio.sleep(0)
    with pytest.raises(HistoryError):
        await supervisor.lookup(auth, "another", deadline=deadline())
    assert not closing.done() and not available(path, "RUNTIME_LOCK")
    release.set()
    await task
    await closing
    assert available(path, "RUNTIME_LOCK") and supervisor._closed


@pytest.mark.asyncio
async def test_read_renewal_does_not_replace_or_unblock_a_separate_writer(reader, monkeypatch):
    supervisor, path, identity, ownership, auth, qualifications = reader
    writer = HistoryCoordinator(path, identity, ownership)
    await writer.open(deadline=deadline())
    writer._blocked = True

    async def lookup(*args, **kwargs):
        raise RuntimeError("synthetic read failure")

    monkeypatch.setattr(HistoryObservation, "lookup", lookup)
    await supervisor.open(deadline=deadline())
    try:
        with pytest.raises(RuntimeError):
            await supervisor.lookup(auth, "original", deadline=deadline())
        assert writer._blocked and not writer._closed and writer._executor is not None
        assert len(qualifications) == 1
        assert not available(path, "RUNTIME_LOCK")
    finally:
        await supervisor.aclose(deadline=deadline())
        await writer.aclose(deadline=deadline())


@pytest.mark.asyncio
async def test_queued_authentication_context_is_copied_before_admission(reader, monkeypatch):
    supervisor, _, _, _, auth, _ = reader
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def lookup(self, context, *args, **kwargs):
        seen.append((context.user_id, context.device_id, context.session_id))
        if len(seen) == 1:
            entered.set()
            await release.wait()
        return HistoricalObservation("unknown")

    monkeypatch.setattr(HistoryObservation, "lookup", lookup)
    await supervisor.open(deadline=deadline())
    first = asyncio.create_task(supervisor.lookup(auth, "first", deadline=deadline()))
    await entered.wait()
    queued_auth = AuthenticatedDevice(uuid4(), uuid4(), uuid4())
    expected = (queued_auth.user_id, queued_auth.device_id, queued_auth.session_id)
    queued = asyncio.create_task(supervisor.lookup(queued_auth, "queued", deadline=deadline()))
    await asyncio.sleep(0)
    queued_auth.user_id, queued_auth.device_id, queued_auth.session_id = uuid4(), uuid4(), uuid4()
    release.set()
    try:
        await first
        await queued
        assert seen[1] == expected
    finally:
        await supervisor.aclose(deadline=deadline())


@pytest.mark.asyncio
async def test_child_creation_failure_closes_unstarted_coroutine_and_blocks_reader(
    reader, monkeypatch
):
    supervisor, _, _, _, auth, qualifications = reader
    await supervisor.open(deadline=deadline())
    old = qualifications[0]
    create_task, captured = asyncio.create_task, []
    original = RuntimeError("synthetic task creation failure")

    def fail_once(call, *args, **kwargs):
        if not captured:
            captured.append(call)
            raise original
        return create_task(call, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", fail_once)
    try:
        with pytest.raises(RuntimeError) as caught:
            await supervisor.lookup(auth, "original", deadline=deadline())
        assert caught.value is original
        assert captured[0].cr_frame is None
        assert supervisor._blocked and old._closed and old._executor is None
    finally:
        captured[0].close()  # Red-run hygiene; the production assertion precedes this.
        await supervisor.aclose(deadline=deadline())


@pytest.mark.asyncio
async def test_owner_can_cancel_and_join_queued_reader_without_poisoning_active_owner(
    reader, monkeypatch
):
    supervisor, _, _, _, auth, qualifications = reader
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def lookup(self, *args, **kwargs):
        calls.append(self._history)
        entered.set()
        await release.wait()
        return HistoricalObservation("unknown")

    monkeypatch.setattr(HistoryObservation, "lookup", lookup)
    await supervisor.open(deadline=deadline())
    first = asyncio.create_task(supervisor.lookup(auth, "first", deadline=deadline()))
    await entered.wait()
    queued = asyncio.create_task(supervisor.lookup(auth, "queued", deadline=deadline()))
    await asyncio.sleep(0)
    queued.cancel("queued cancellation")
    done, _ = await asyncio.wait({queued}, timeout=0.5)
    try:
        assert queued in done, "queued admission incorrectly drained the current owner's work"
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert len(calls) == len(qualifications) == 1 and not supervisor._blocked
    finally:
        release.set()
        await first
        await supervisor.aclose(deadline=deadline())


@pytest.mark.asyncio
async def test_failed_close_requires_new_supervisor_and_complete_restart_validation(
    reader, monkeypatch
):
    supervisor, path, identity, ownership, auth, qualifications = reader
    native_close = HistoryStore.close

    async def fail(*a, **kw):
        raise RuntimeError("synthetic failed request")

    def fail_close(store):
        native_close(store)
        raise OSError("synthetic close uncertainty")

    monkeypatch.setattr(HistoryObservation, "lookup", fail)
    await supervisor.open(deadline=deadline())
    monkeypatch.setattr(HistoryStore, "close", fail_close)
    with pytest.raises(RuntimeError):
        await supervisor.lookup(auth, "held-key", deadline=deadline())
    assert supervisor._blocked and len(qualifications) == 1
    monkeypatch.setattr(HistoryStore, "close", native_close)
    replacement = read_supervisor.HistoryReadSupervisor(
        supervisor._tasks, path, supervisor._projection, ownership, KEY
    )
    try:
        await replacement.open(deadline=deadline())
        assert len(qualifications) == 2 and supervisor._blocked
        assert replacement._owner is not None
        assert replacement._owner.history is not qualifications[0]
    finally:
        await replacement.aclose(deadline=deadline())


@pytest.mark.asyncio
async def test_inherited_reader_refuses_before_touching_parent_owners(reader):
    import os

    supervisor, path, _, _, auth, _ = reader
    await supervisor.open(deadline=deadline())
    child = os.fork()
    if child == 0:

        async def probe():
            for call in (
                supervisor.open(deadline=deadline()),
                supervisor.lookup(auth, "key", deadline=deadline()),
                supervisor.aclose(deadline=deadline()),
            ):
                try:
                    await call
                except HistoryError as error:
                    if str(error) != "forked_reader":
                        return 2
                else:
                    return 3
            return 0

        try:
            result = asyncio.run(probe())
        except BaseException:
            result = 4
        os._exit(result)
    try:
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert not supervisor._blocked
        assert not available(path, "RUNTIME_LOCK")
    finally:
        await supervisor.aclose(deadline=deadline())


@pytest.mark.asyncio
@pytest.mark.parametrize("retire", [False, True])
async def test_cleanup_scheduling_failure_closes_unstarted_call_and_keeps_reads_blocked(
    reader, monkeypatch, retire
):
    supervisor, _, _, _, auth, _ = reader
    await supervisor.open(deadline=deadline())
    calls = []
    original = RuntimeError("synthetic read cleanup scheduling failure")

    def fail(call):
        calls.append(call)
        raise original

    create = asyncio.create_task
    monkeypatch.setattr(asyncio, "create_task", fail)
    try:
        with pytest.raises(RuntimeError) as caught:
            if retire:
                await supervisor._retire(deadline())
            else:
                await supervisor.aclose(deadline=deadline())
        assert caught.value is original
        assert supervisor._blocked if retire else supervisor._closing
        assert len(calls) == 1 and calls[0].cr_frame is None
        with pytest.raises(HistoryError):
            await supervisor.lookup(auth, "held-key", deadline=deadline())
    finally:
        # No physical finalizer started; explicitly finish owned fixture cleanup.
        monkeypatch.setattr(asyncio, "create_task", create)
        await supervisor.aclose(deadline=deadline())
