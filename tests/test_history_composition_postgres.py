"""Owned synthetic native acceptance + H1, not HTTP/startup/dispatch proof."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
import fcntl
import os
import threading
import time
from uuid import uuid4

import asyncpg
from asyncpg.transaction import Transaction
from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.services.restore_history.acceptance import AcceptanceProjection
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.model import (
    AcceptPrepare,
    Command,
    HistoryError,
    StopIntent,
)
from orchestrator.services.restore_history.store import HistoryStore
from orchestrator.tasks.inputs import chat_request_fingerprint
from orchestrator.tasks.store import IdempotencyConflict, TaskNotFound, TaskStore
from tests.restore_history_support import KEY
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_coordination import available
from tests.test_restore_history_store import open_store, owned


def deadline():
    return time.monotonic() + 10


def submission(
    account, *, key="stable-client-key", message="synthetic", generation=0, conversation=None
) -> dict[str, Any]:
    fingerprint = chat_request_fingerprint(
        key="synthetic-pepper",
        conversation_id=conversation,
        message=message,
        attachments=None,
        model=None,
        provider=None,
        metadata=None,
        disable_memory_write=False,
    )
    return dict(
        user_id=account,
        conversation_id=conversation,
        new_conversation_title="synthetic",
        pipeline="cloud",
        user_message=message,
        task_input={"message": message},
        request_hash=fingerprint.digest,
        idempotency_key=key,
        assistant_model=None,
        request_canonical=fingerprint.canonical,
        captured_generation=CapturedGeneration(account, generation),
    )


@asynccontextmanager
async def independent_owner(s):
    history = HistoryCoordinator(s.path, s.identity, s.ownership)
    await history.open(deadline=deadline())
    try:
        yield HistoryAcceptance(s.tasks, history, s.projection, KEY)
    finally:
        await history.aclose(deadline=deadline())


@pytest_asyncio.fixture
async def scenario(projection_db, tmp_path):
    db = projection_db
    async with db.connection() as conn:
        account = await db.account(conn)
    pool = await asyncpg.create_pool(
        host=str(db.host),
        user="md_acceptor",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=2,
    )
    enc = ContentEncryption(Fernet.generate_key().decode())
    tasks = TaskStore(pool, enc, MemoryStore(pool, enc), cover_new_inference=True)
    path = tmp_path / "h"
    identity, ownership = owned(path)
    history = HistoryCoordinator(path, identity, ownership)
    projection = AcceptanceProjection(identity, acceptor="md_acceptor", worker="md_app")
    await history.open(deadline=deadline())
    s = SimpleNamespace(
        db=db,
        account=account,
        pool=pool,
        tasks=tasks,
        path=path,
        identity=identity,
        ownership=ownership,
        history=history,
        projection=projection,
        accept=HistoryAcceptance(tasks, history, projection, KEY),
    )
    try:
        yield s
    finally:
        await history.aclose(deadline=deadline())
        await asyncio.wait_for(pool.close(), 5)


def counts(s):
    return tuple(
        int(s.db.sql("SELECT count(*) FROM " + table))
        for table in (
            "tasks",
            "task_history_acceptance",
            "messages",
        )
    )


def entries(s):
    with open_store(s.path, s.identity, s.ownership) as store:
        return store.verify(deadline=deadline()).entries


@pytest.mark.asyncio
async def test_fresh_commit_requires_history_projection_native_commit_and_release(scenario):
    s = scenario
    accepted = await s.accept.accept(deadline=deadline(), **submission(s.account))
    assert accepted.created
    assert counts(s) == (1, 1, 2)
    assert available(s.path, "OP_LOCK") and available(s.path, "APPEND_LOCK")
    assert not s.history._gate.locked()
    recorded = entries(s)
    assert len(recorded) == 1
    data = recorded[0].command.data
    assert isinstance(data, AcceptPrepare)
    assert (data.task_id, data.user_message_id, data.result_message_id) == (
        accepted.task_id,
        accepted.user_message_id,
        accepted.result_message_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_historical_key_is_not_fresh_ack_or_replacement_work(scenario, conflict):
    s = scenario
    first = await s.accept.accept(deadline=deadline(), **submission(s.account))
    async with independent_owner(s) as other:
        error = IdempotencyConflict if conflict else HistoryError
        with pytest.raises(error):
            await other.accept(
                deadline=deadline(),
                **submission(s.account, message="different" if conflict else "synthetic"),
            )
    assert counts(s) == (1, 1, 2)
    data = entries(s)[0].command.data
    assert isinstance(data, AcceptPrepare) and data.task_id == first.task_id


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["after_prepare", "after_stage", "commit_before", "commit_after"])
async def test_uncertainty_never_replaces_prepared_work(scenario, monkeypatch, fault):
    s = scenario
    original_stage = s.projection.stage
    original_exit = Transaction.__aexit__
    armed = False

    async def stage(conn, entry, *, deadline):
        nonlocal armed
        if fault == "after_prepare":
            raise ConnectionError("synthetic rollback after durable prepare")
        result = await original_stage(conn, entry, deadline=deadline)
        if fault == "after_stage":
            raise ConnectionError("synthetic staged result lost")
        armed = True
        return result

    async def exit_transaction(tx, kind, value, traceback):
        nonlocal armed
        if not armed or kind is not None:
            return await original_exit(tx, kind, value, traceback)
        armed = False
        error = ConnectionError("synthetic COMMIT acknowledgement lost")
        if fault == "commit_before":
            await original_exit(tx, type(error), error, None)
        else:
            await original_exit(tx, None, None, None)
        raise error

    monkeypatch.setattr(s.projection, "stage", stage)
    monkeypatch.setattr(Transaction, "__aexit__", exit_transaction)
    with pytest.raises(ConnectionError):
        await s.accept.accept(deadline=deadline(), **submission(s.account))
    monkeypatch.setattr(Transaction, "__aexit__", original_exit)
    monkeypatch.setattr(s.projection, "stage", original_stage)
    assert counts(s) == ((1, 1, 2) if fault == "commit_after" else (0, 0, 0))
    assert len(entries(s)) == 1
    with pytest.raises(HistoryError, match="blocked"):
        await s.accept.accept(deadline=deadline(), **submission(s.account))
    async with independent_owner(s) as other:
        with pytest.raises(HistoryError, match="historical_acceptance"):
            await other.accept(deadline=deadline(), **submission(s.account))
    assert len(entries(s)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["append", "stage", "commit_pending", "commit", "release"])
async def test_cancellation_keeps_owners_and_never_acknowledges(scenario, monkeypatch, phase):
    s = scenario
    physical = phase in ("append", "release")
    entered, release = asyncio.Event(), asyncio.Event()
    physical_entered, physical_release = threading.Event(), threading.Event()
    original_fsync, original_release = os.fsync, s.history._exit
    original_stage, original_exit = s.projection.stage, Transaction.__aexit__
    armed = False

    def fsync(fd):
        if fd == s.history._store._fds["LOG"]:
            physical_entered.set()
            assert physical_release.wait(5)
        return original_fsync(fd)

    def exit_scope(original):
        physical_entered.set()
        assert physical_release.wait(5)
        return original_release(original)

    async def stage(conn, entry, *, deadline):
        nonlocal armed
        result = await original_stage(conn, entry, deadline=deadline)
        if phase == "stage":
            entered.set()
            await release.wait()
        armed = True
        return result

    async def exit_transaction(tx, kind, value, traceback):
        nonlocal armed
        if phase == "commit_pending" and armed and kind is None:
            armed = False
            entered.set()
            await release.wait()
        result = await original_exit(tx, kind, value, traceback)
        if phase == "commit" and armed and kind is None:
            armed = False
            entered.set()
            await release.wait()
        return result

    if phase == "append":
        monkeypatch.setattr(os, "fsync", fsync)
    elif phase == "release":
        monkeypatch.setattr(s.history, "_exit", exit_scope)
    else:
        monkeypatch.setattr(s.projection, "stage", stage)
        monkeypatch.setattr(Transaction, "__aexit__", exit_transaction)
    task = asyncio.create_task(s.accept.accept(deadline=deadline(), **submission(s.account)))
    try:
        if physical:
            assert await asyncio.to_thread(physical_entered.wait, 5)
        else:
            await asyncio.wait_for(entered.wait(), 5)
        task.cancel("original acceptance cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated acceptance cancellation")
        if physical:
            assert not task.done() and not available(s.path, "OP_LOCK")
            assert not available(s.path, "RUNTIME_LOCK")
            for fd in s.history._store._fds.values():
                os.fstat(fd)
            if phase == "append":
                assert not available(s.path, "APPEND_LOCK")
        release.set()
        physical_release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("original acceptance cancellation",)
        assert counts(s) == ((1, 1, 2) if phase in ("commit", "release") else (0, 0, 0))
        assert len(entries(s)) == 1
        assert available(s.path, "OP_LOCK")
    finally:
        release.set()
        physical_release.set()
        await asyncio.gather(task, return_exceptions=True)
        monkeypatch.setattr(Transaction, "__aexit__", original_exit)


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_competing_independent_history_owners_never_create_replacement(scenario, conflict):
    s = scenario
    async with independent_owner(s) as other:
        results = await asyncio.gather(
            s.accept.accept(deadline=deadline(), **submission(s.account)),
            other.accept(
                deadline=deadline(),
                **submission(s.account, message="conflicting" if conflict else "synthetic"),
            ),
            return_exceptions=True,
        )
    accepted = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, BaseException)]
    assert len(accepted) == len(refused) == 1 and accepted[0].created
    assert isinstance(refused[0], IdempotencyConflict if conflict else HistoryError)
    assert counts(s) == (1, 1, 2) and len(entries(s)) == 1


@pytest.mark.asyncio
async def test_old_owned_sql_restore_cannot_create_new_key_after_lost_projection(scenario):
    s = scenario
    cluster = s.db.cluster
    snapshot = "md_haccept_" + uuid4().hex
    assert s.db.name in cluster.databases and cluster.container is not None
    assert cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{s.db.name}'") == "0"
    cluster.sql(f'CREATE DATABASE "{snapshot}" TEMPLATE "{s.db.name}"')
    cluster.databases.add(snapshot)
    try:
        await s.accept.accept(deadline=deadline(), **submission(s.account))
        await s.pool.close()
        assert (
            cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{s.db.name}'") == "0"
        )
        cluster.sql(f'DROP DATABASE "{s.db.name}"')
        cluster.sql(f'CREATE DATABASE "{s.db.name}" TEMPLATE "{snapshot}"')
        pool = await asyncpg.create_pool(
            host=str(s.db.host),
            user="md_acceptor",
            password="",
            database=s.db.name,
            ssl=False,
            min_size=0,
            max_size=1,
        )
        try:
            enc = s.tasks._enc
            tasks = TaskStore(pool, enc, MemoryStore(pool, enc), cover_new_inference=True)
            fresh = SimpleNamespace(**{**s.__dict__, "tasks": tasks})
            async with independent_owner(fresh) as other:
                with pytest.raises(HistoryError, match="reconciliation_required"):
                    await other.accept(deadline=deadline(), **submission(s.account, key="new-key"))
            assert counts(s) == (0, 0, 0)
            assert len(entries(s)) == 1
        finally:
            await pool.close()
    finally:
        cluster.sql(f'DROP DATABASE "{snapshot}"')
        cluster.databases.remove(snapshot)


@pytest.mark.asyncio
async def test_non_acceptance_history_is_not_filtered_out_to_allow_new_work(scenario):
    s = scenario
    first = await s.accept.accept(deadline=deadline(), **submission(s.account))
    async with s.history.operation(deadline=deadline()) as operation:
        await operation.append(Command(uuid4(), StopIntent(s.account, 0, first.task_id, "owner")))
    with pytest.raises(HistoryError, match="reconciliation_required"):
        await s.accept.accept(deadline=deadline(), **submission(s.account, key="new-key"))
    assert counts(s) == (1, 1, 2) and len(entries(s)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("projection_db", [False], indirect=True)
async def test_missing_sql_binding_never_initializes_or_acknowledges(scenario):
    s = scenario
    with pytest.raises(HistoryError):
        await s.accept.accept(deadline=deadline(), **submission(s.account))
    assert s.db.sql("SELECT count(*) FROM task_history_binding") == "0"
    assert counts(s) == (0, 0, 0) and entries(s) == ()


@pytest.mark.asyncio
async def test_wrong_independent_key_refuses_before_native_work(scenario):
    s = scenario
    with pytest.raises(HistoryError, match="key_mismatch"):
        HistoryAcceptance(s.tasks, s.history, s.projection, bytes(32))
    assert counts(s) == (0, 0, 0) and entries(s) == ()


@pytest.mark.asyncio
async def test_release_failure_after_commit_is_not_acceptance_success(scenario, monkeypatch):
    s = scenario
    native = fcntl.flock
    target = s.history._store._fds["OP_LOCK"]
    failed = False

    def unlock(fd, flags):
        nonlocal failed
        if fd == target and flags == fcntl.LOCK_UN and not failed:
            failed = True
            raise OSError("synthetic acceptance release failure")
        return native(fd, flags)

    monkeypatch.setattr(fcntl, "flock", unlock)
    with pytest.raises(OSError, match="release failure"):
        await s.accept.accept(deadline=deadline(), **submission(s.account))
    assert failed and counts(s) == (1, 1, 2)
    assert not available(s.path, "OP_LOCK")
    await s.history.aclose(deadline=deadline())
    assert available(s.path, "OP_LOCK") and len(entries(s)) == 1


@pytest.mark.asyncio
async def test_late_delivery_after_commit_and_release_cannot_promote_success(scenario, monkeypatch):
    from orchestrator.services.restore_history import composition

    s = scenario
    original = s.accept._accept
    original_time = composition._check_time
    late = False

    async def delivered(*args):
        nonlocal late
        result = await original(*args)
        late = True
        return result

    def check(limit):
        if late:
            raise HistoryError("synthetic_late_acceptance_result", uncertain=True)
        original_time(limit)

    monkeypatch.setattr(s.accept, "_accept", delivered)
    monkeypatch.setattr(composition, "_check_time", check)
    with pytest.raises(HistoryError, match="late_acceptance_result"):
        await s.accept.accept(deadline=deadline(), **submission(s.account))
    assert counts(s) == (1, 1, 2) and len(entries(s)) == 1
    assert available(s.path, "OP_LOCK")


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing_key", "mismatched_message", "noncanonical"])
async def test_invalid_request_cannot_prepare_or_mutate(scenario, damage):
    s = scenario
    options = submission(s.account)
    if damage == "missing_key":
        options["idempotency_key"] = None
    elif damage == "mismatched_message":
        options["user_message"] = "unbound replacement message"
    else:
        options["request_canonical"] += " "
    with pytest.raises(HistoryError):
        await s.accept.accept(deadline=deadline(), **options)
    assert counts(s) == (0, 0, 0) and entries(s) == ()


@pytest.mark.asyncio
async def test_guard_refusal_after_reset_prepares_no_history(scenario):
    s = scenario
    s.db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    with pytest.raises(WriteGuardRefusal, match="stale_generation"):
        await s.accept.accept(deadline=deadline(), **submission(s.account))
    assert counts(s) == (0, 0, 0) and entries(s) == ()


@pytest.mark.asyncio
async def test_reset_waits_for_original_account_guard_through_history_and_commit(
    scenario, monkeypatch
):
    s = scenario
    entered, release = threading.Event(), threading.Event()
    original = HistoryStore.append

    def held(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(HistoryStore, "append", held)
    task = asyncio.create_task(s.accept.accept(deadline=deadline(), **submission(s.account)))
    reset_task = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with s.db.connection() as reset, s.db.connection() as observer:
            reset_task = asyncio.create_task(
                reset.execute(
                    "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
                    s.account,
                )
            )
            await s.db.wait_lock(observer, reset.get_server_pid())
            assert not reset_task.done() and not available(s.path, "OP_LOCK")
            release.set()
            accepted = await task
            await reset_task
        assert accepted.created and counts(s) == (1, 1, 2)
        assert s.db.sql("SELECT account_generation FROM task_history_acceptance") == "0"
        assert s.db.sql("SELECT content_generation FROM entitlement_accounts") == "1"
        async with independent_owner(s) as other:
            with pytest.raises(WriteGuardRefusal, match="stale_generation"):
                await other.accept(deadline=deadline(), **submission(s.account, key="new-key"))
        assert len(entries(s)) == 1
    finally:
        release.set()
        await asyncio.gather(
            task, *([reset_task] if reset_task is not None else []), return_exceptions=True
        )


@pytest.mark.asyncio
async def test_actual_native_account_cascade_retains_historical_acceptance(scenario):
    s = scenario
    await s.accept.accept(deadline=deadline(), **submission(s.account))
    async with s.db.connection() as owner:
        await owner.execute("DELETE FROM users WHERE id=$1", s.account)
    assert counts(s) == (0, 1, 0)
    async with independent_owner(s) as other:
        with pytest.raises(HistoryError, match="historical_acceptance"):
            await other.accept(deadline=deadline(), **submission(s.account))
    assert len(entries(s)) == 1


@pytest.mark.asyncio
async def test_same_client_key_is_account_scoped_and_native_conversation_guard_remains(scenario):
    s = scenario
    first = await s.accept.accept(deadline=deadline(), **submission(s.account))
    async with s.db.connection() as owner:
        second_account = await s.db.account(owner)
    second = await s.accept.accept(deadline=deadline(), **submission(second_account))
    assert first.task_id != second.task_id and counts(s) == (2, 2, 4)
    with pytest.raises(TaskNotFound):
        await s.accept.accept(
            deadline=deadline(),
            **submission(
                second_account,
                key="foreign-conversation",
                conversation=first.conversation_id,
            ),
        )
    assert counts(s) == (2, 2, 4) and len(entries(s)) == 2


@pytest.mark.asyncio
async def test_sql_evidence_without_restored_history_does_not_authorize_native_replay(scenario):
    s = scenario
    await s.accept.accept(deadline=deadline(), **submission(s.account))
    path = s.path.parent / "older-h"
    identity, ownership = owned(path)
    older = SimpleNamespace(
        **{**s.__dict__, "path": path, "identity": identity, "ownership": ownership}
    )
    async with independent_owner(older) as other:
        with pytest.raises(HistoryError, match="reconciliation_required"):
            await other.accept(deadline=deadline(), **submission(s.account))
    assert entries(older) == () and counts(s) == (1, 1, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncpg.UniqueViolationError, WriteGuardRefusal])
async def test_native_fallback_cannot_readback_or_retry_after_history_prepare(
    scenario, monkeypatch, error
):
    s = scenario
    lookup = s.tasks._by_key
    calls = []

    async def by_key(*args):
        calls.append(True)
        return await lookup(*args)

    async def fail_stage(*args, **kwargs):
        raise error(
            "stale_generation" if error is WriteGuardRefusal else "synthetic post-prepare refusal"
        )

    monkeypatch.setattr(s.tasks, "_by_key", by_key)
    monkeypatch.setattr(s.projection, "stage", fail_stage)
    with pytest.raises(error):
        await s.accept.accept(deadline=deadline(), **submission(s.account))
    assert len(calls) == 1
    assert counts(s) == (0, 0, 0) and len(entries(s)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_composed_caller_cancellation_settles_queued_child_before_owner_exit(
    scenario, monkeypatch, structured
):
    s = scenario
    queued = asyncio.Event()
    attempts = []
    native = s.history._gate.acquire
    child = None
    original = RuntimeError("synthetic structured caller failure")

    async def acquire():
        attempts.append(asyncio.current_task())
        queued.set()
        return await native()

    async def owner():
        nonlocal child
        async with s.history.operation(deadline=deadline()):
            monkeypatch.setattr(s.history._gate, "acquire", acquire)
            if structured:
                async with asyncio.TaskGroup() as group:
                    child = group.create_task(
                        s.accept.accept(deadline=deadline(), **submission(s.account))
                    )
                    await queued.wait()
                    raise original
            else:
                child = asyncio.create_task(
                    s.accept.accept(deadline=deadline(), **submission(s.account))
                )
                await queued.wait()
                child.cancel("original queued caller cancellation")
                with pytest.raises(asyncio.CancelledError) as caught:
                    await child
                assert caught.value.args == ("original queued caller cancellation",)
                assert not available(s.path, "OP_LOCK")

    task = asyncio.create_task(owner())
    try:
        await queued.wait()
        done, _ = await asyncio.wait({task}, timeout=0.5)
        assert task in done, "cancelled composed caller deadlocked joining queued child"
        if structured:
            with pytest.raises(ExceptionGroup) as caught:
                await task
            assert caught.value.exceptions == (original,)
        else:
            with pytest.raises(HistoryError, match="coordinator_blocked"):
                await task
        assert not s.history._gate.locked() and not s.history._gate._waiters
        assert counts(s) == (0, 0, 0) and entries(s) == ()
    finally:
        for attempt in attempts:
            if attempt is not None and not attempt.done():
                attempt.cancel()
        await asyncio.gather(task, *([child] if child is not None else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_native_connection_release_failure_after_commit_is_not_ack(scenario, monkeypatch):
    s = scenario
    stage = s.projection.stage
    release = asyncpg.Pool.release
    armed = False
    calls = []

    async def staged(*args, **kwargs):
        nonlocal armed
        result = await stage(*args, **kwargs)
        armed = True
        return result

    async def released(pool, connection, **kwargs):
        nonlocal armed
        result = await release(pool, connection, **kwargs)
        if pool is s.pool and armed:
            armed = False
            calls.append(True)
            raise RuntimeError("synthetic native release result lost")
        return result

    monkeypatch.setattr(s.projection, "stage", staged)
    monkeypatch.setattr(asyncpg.Pool, "release", released)
    with pytest.raises(RuntimeError, match="release result lost"):
        await s.accept.accept(deadline=deadline(), **submission(s.account))
    assert calls == [True]
    assert counts(s) == (1, 1, 2) and len(entries(s)) == 1
    assert available(s.path, "OP_LOCK")


@pytest.mark.asyncio
async def test_actual_worker_connection_is_not_acceptor_authority(scenario):
    s = scenario
    pool = await asyncpg.create_pool(
        host=str(s.db.host),
        user="md_app",
        password="",
        database=s.db.name,
        ssl=False,
        min_size=0,
        max_size=1,
    )
    try:
        enc = s.tasks._enc
        tasks = TaskStore(pool, enc, MemoryStore(pool, enc), cover_new_inference=True)
        refused = HistoryAcceptance(tasks, s.history, s.projection, KEY)
        with pytest.raises(HistoryError):
            await refused.accept(deadline=deadline(), **submission(s.account))
        assert counts(s) == (0, 0, 0) and entries(s) == ()
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_legacy_task_configuration_cannot_prepare_history(scenario):
    s = scenario
    enc = s.tasks._enc
    tasks = TaskStore(s.pool, enc, MemoryStore(s.pool, enc))
    with pytest.raises(HistoryError, match="composition_refused"):
        HistoryAcceptance(tasks, s.history, s.projection, KEY)
    assert counts(s) == (0, 0, 0) and entries(s) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [1, 3])
async def test_pool_owned_outer_transaction_is_not_a_known_native_commit(scenario, target):
    s = scenario
    retained = []
    acquisitions = 0

    async def setup(conn):
        nonlocal acquisitions
        acquisitions += 1
        if acquisitions == target:
            transaction = conn.transaction()
            await transaction.start()
            retained.append(transaction)

    pool = await asyncpg.create_pool(
        host=str(s.db.host),
        user="md_acceptor",
        password="",
        database=s.db.name,
        ssl=False,
        min_size=0,
        max_size=1,
        setup=setup,
    )
    try:
        enc = s.tasks._enc
        tasks = TaskStore(pool, enc, MemoryStore(pool, enc), cover_new_inference=True)
        candidate = HistoryAcceptance(tasks, s.history, s.projection, KEY)
        try:
            result = await candidate.accept(deadline=deadline(), **submission(s.account))
        except (HistoryError, ObservationBlocked) as refused:
            assert refused.code == "acceptance_transaction_owner_refused"
            assert counts(s) == (0, 0, 0) and entries(s) == ()
        else:
            pytest.fail(
                f"savepoint promoted to ACK: created={result.created}, "
                f"SQL counts={counts(s)}, H entries={len(entries(s))}"
            )
        assert retained
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["attachments", "content_parts"])
async def test_received_inputs_cannot_bypass_uncomposed_c1(scenario, field):
    s = scenario
    item = (
        {"kind": "text", "text_content": "owned synthetic bytes"}
        if field == "attachments"
        else {"type": "text", "text": "owned synthetic bytes"}
    )
    fingerprint = chat_request_fingerprint(
        key="synthetic-pepper",
        conversation_id=None,
        message="synthetic",
        attachments=[item] if field == "attachments" else None,
        content_parts=[item] if field == "content_parts" else None,
        model=None,
        provider=None,
        metadata=None,
        disable_memory_write=False,
    )
    options = submission(s.account)
    options.update(request_hash=fingerprint.digest, request_canonical=fingerprint.canonical)
    with pytest.raises(HistoryError, match="resources_uncomposed"):
        await s.accept.accept(deadline=deadline(), **options)
    assert counts(s) == (0, 0, 0) and entries(s) == ()
    assert s.db.sql("SELECT count(*) FROM resource_references") == "0"
