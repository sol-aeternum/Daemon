"""Actual owned restore, uncertainty and cleanup boundaries for v2 catalog."""

import asyncio
import threading
import time

import asyncpg
from asyncpg.transaction import Transaction
import pytest

from orchestrator import resource_store as registry
from orchestrator.services.restore_history.model import ArtifactPrepare, HistoryError
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.store import HistoryStore
from orchestrator.services.restore_history import store as history_store
from orchestrator.tasks.states import TaskStatus
from tests import test_artifact_history_postgres as base
from tests import test_history_output_phases_postgres as q
from tests.history_cleanup_support import held_reset
from tests.restore_history_support import KEY
from tests.test_artifact_restore_boundary_postgres import (
    checkpoint,
    restored,
    parity,
    file_facts,
    data_facts,
)
from tests.test_history_coordination import available

metadata_cluster = base.metadata_cluster
db = base.db
projection_db = base.projection_db
scenario = base.scenario
effects = base.effects
reserved = base.reserved
presenter = base.presenter
reservation_owner = base.reservation_owner
preparer = base.preparer
phase_owner = base.phase_owner
artifact_owner = base.artifact_owner


@pytest.mark.asyncio
@pytest.mark.parametrize("older_files", [False, True])
async def test_actual_sql_and_file_rollback_refuses_before_catalog_effects(
    artifact_owner, tmp_path, monkeypatch, older_files
):
    s = artifact_owner
    saved = await checkpoint(s, tmp_path)
    original = await q.phase(s, "catalog")
    assert original is not None
    async with restored(s, saved, older_files=older_files, projection=s.projection) as fresh:
        files, data = file_facts(fresh.root), data_facts(fresh)

        async def forbidden(*args, **kwargs):
            pytest.fail("native publication ran after losing the protected artifact fact")

        monkeypatch.setattr(fresh.publisher, "_publish_reserved", forbidden)
        with pytest.raises(HistoryError, match="effects_reconciliation_required"):
            await q.phase(fresh, "catalog")
        assert fresh.effects._blocked and fresh.history._blocked
        assert file_facts(fresh.root) == files and data_facts(fresh) == data


@pytest.mark.asyncio
async def test_catalog_fact_does_not_claim_later_terminal_restore_continuity(
    artifact_owner, tmp_path
):
    s = artifact_owner
    await q.phase(s, "catalog")
    saved = await checkpoint(s, tmp_path)
    await q.phase(s, "presentation")
    await q.phase(s, "terminal")
    async with restored(s, saved, projection=s.projection) as fresh:
        assert len((await parity(fresh)).entries) == 2
        original = await fresh.worker.task_for_key(s.account, "stable-client-key")
        assert original is not None and original.task_id == s.claim.task_id
        assert original.status is TaskStatus.RUNNING and q.r.p.r.rows(fresh) == "1:1:1:0"
        async with fresh.db.connection() as conn:
            value = await conn.fetchval(
                "SELECT content FROM messages WHERE id=$1", s.claim.result_message_id
            )
        assert s.tasks._enc.decrypt(value) != "synthetic final"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_lost_catalog_commit_ack_keeps_history_and_blocks_replacement(
    artifact_owner, monkeypatch, fault
):
    s = artifact_owner
    native, fired = Transaction.commit, 0
    s.db.bridge.commit_fault_role = "reserved_p"

    async def commit(tx):
        nonlocal fired
        if tx._connection._params.user == "reserved_p":
            fired += 1
            s.db.bridge.commit_fault = fault
        try:
            return await native(tx)
        finally:
            s.db.bridge.commit_fault = None

    monkeypatch.setattr(Transaction, "commit", commit)
    try:
        with pytest.raises((asyncpg.PostgresError, TimeoutError)):
            await q.phase(s, "catalog")
    finally:
        s.db.bridge.commit_fault = None
    assert fired == 1 and s.db.bridge.commit_seen.is_set()
    assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == (
        "2" if fault == "after" else "1"
    )
    assert q.r.p.r.rows(s) == ("1:1:1:0" if fault == "after" else "0:0:0:0")
    before = q.state(s)
    with pytest.raises(HistoryError, match="blocked"):
        await q.phase(s, "catalog")
    assert q.state(s) == before and fired == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_actual_catalog_pool_reset_retains_h_until_physical_completion(
    artifact_owner, monkeypatch, mode
):
    s = artifact_owner
    async with held_reset(s.publisher._pool, monkeypatch, 1, mode == "failure") as (
        entered,
        release,
    ):
        task = asyncio.create_task(q.phase(s, "catalog"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original artifact cleanup cancellation")
                await asyncio.sleep(0.03)
                task.cancel("repeated artifact cancellation")
            elif mode == "deadline":
                await asyncio.sleep(2.05)
                task.cancel("repeated after logical deadline")
            await asyncio.sleep(0.03)
            assert not task.done() and not available(s.path, "OP_LOCK")
            release.set()
            error = (
                asyncio.CancelledError
                if mode == "cancel"
                else TimeoutError
                if mode == "deadline"
                else RuntimeError
            )
            with pytest.raises(error) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original artifact cleanup cancellation",)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    assert available(s.path, "OP_LOCK") and s.effects._blocked and s.history._blocked
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "2"
    with pytest.raises(HistoryError, match="blocked"):
        await q.phase(s, "catalog")


@pytest.mark.asyncio
async def test_cancelled_physical_artifact_write_drains_before_h_and_sql_release(
    artifact_owner, monkeypatch
):
    s = artifact_owner
    entered, release = threading.Event(), threading.Event()
    native = registry.prepare_verified

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return native(*args, **kwargs)

    monkeypatch.setattr(registry, "prepare_verified", held)
    task = asyncio.create_task(q.phase(s, "catalog"))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        task.cancel("original physical artifact cancellation")
        await asyncio.sleep(0.03)
        task.cancel("repeated physical cancellation")
        assert not task.done() and not available(s.path, "OP_LOCK")
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("original physical artifact cancellation",)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert available(s.path, "OP_LOCK") and s.effects._blocked
    assert q.r.p.r.rows(s) == "0:0:0:0"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["append", "stage"])
async def test_failed_prepare_or_projection_cannot_publish_catalog(
    artifact_owner, monkeypatch, phase
):
    s = artifact_owner
    if phase == "append":
        native = HistoryStore._write

        def fail(store, fd, data, deadline):
            if fd == store._fds["LOG"]:
                raise OSError("synthetic append failure")
            return native(store, fd, data, deadline)

        monkeypatch.setattr(HistoryStore, "_write", fail)
    else:

        async def fail_stage(*args, **kwargs):
            raise RuntimeError("synthetic stage failure")

        monkeypatch.setattr(s.projection, "stage_artifact", fail_stage)
    with pytest.raises((HistoryError, RuntimeError)):
        await q.phase(s, "catalog")
    assert q.r.p.r.rows(s) == "0:0:0:0" and s.effects._blocked and s.history._blocked
    if phase == "append":
        assert not q.r.p.r.files(s)
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["stop", "generation", "suspension"])
async def test_current_control_fences_refuse_before_artifact_prepare(artifact_owner, control):
    s = artifact_owner
    if control == "stop":
        assert (
            await s.effects.request_cancel(
                s.account, s.claim.task_id, key=KEY, deadline=time.monotonic() + 2
            )
        ).accepted
    elif control == "generation":
        s.db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    else:
        s.db.sql("UPDATE entitlement_accounts SET status='suspended'")
    before = s.db.sql("SELECT count(*) FROM task_history_projection")
    with pytest.raises((HistoryError, asyncpg.PostgresError)):
        await q.phase(s, "catalog")
    assert q.r.p.r.rows(s) == "0:0:0:0" and not q.r.p.r.files(s)
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_app", "md_acceptor", "reserved_r", "md_third"])
async def test_only_p_can_execute_artifact_projection(role, artifact_owner):
    s = artifact_owner
    async with s.db.connection(role) as conn:
        assert (
            await conn.fetchval(
                "SELECT has_function_privilege(current_user,'public.task_history_artifact_project(bytea)','EXECUTE')"
            )
            is False
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM public.task_history_artifact_project($1)", b"invalid")


@pytest.mark.asyncio
async def test_extra_history_grantee_refuses_before_artifact_prepare(artifact_owner):
    s = artifact_owner
    s.db.sql("GRANT EXECUTE ON FUNCTION task_history_artifact_project(bytea) TO md_third")
    with pytest.raises(HistoryError):
        await q.phase(s, "catalog")
    assert s.effects._blocked and q.r.p.r.rows(s) == "0:0:0:0" and not q.r.p.r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("conflicting", [False, True])
async def test_competing_original_intent_never_runs_second_catalog(
    artifact_owner, monkeypatch, conflicting
):
    s = artifact_owner
    entered, release = threading.Event(), threading.Event()
    native, calls = registry.prepare_verified, 0

    def held(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5)
        return native(*args, **kwargs)

    monkeypatch.setattr(registry, "prepare_verified", held)
    first = asyncio.create_task(q.phase(s, "catalog"))
    second = None
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        second = asyncio.create_task(
            s.effects.publish_reserved_output(
                s.publisher,
                s.attempt,
                s.command.binding.intent_id,
                b"conflicting" if conflicting else s.prepared_content,
                deadline=time.monotonic() + 2,
            )
        )
        await asyncio.sleep(0.03)
        assert calls == 1 and not second.done()
        release.set()
        values = await asyncio.gather(first, second, return_exceptions=True)
        assert sum(not isinstance(v, BaseException) for v in values) <= 1
        assert isinstance(values[1], HistoryError)
    finally:
        release.set()
        await asyncio.gather(*[t for t in (first, second) if t is not None], return_exceptions=True)
    assert calls == 1 and q.r.p.r.rows(s) == "1:1:1:0"
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "2"


@pytest.mark.asyncio
async def test_committed_catalog_cannot_be_backfilled_with_historical_prepare(artifact_owner):
    s = artifact_owner
    await q.phase(s, "catalog")
    async with s.history.operation(deadline=time.monotonic() + 2) as operation:
        original = (await operation.verify()).entries[-1]
    before = file_facts(s.root)
    s.db.sql(
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;"
        "DELETE FROM task_history_projection WHERE kind='ARTIFACT_PREPARE';"
        "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable"
    )
    with pytest.raises(HistoryError):
        async with s.history.operation(deadline=time.monotonic() + 2):
            async with s.db.connection("reserved_p") as conn, conn.transaction():
                await s.projection.stage_artifact(conn, original, deadline=time.monotonic() + 2)
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
    assert q.r.p.r.rows(s) == "1:1:1:0" and file_facts(s.root) == before


@pytest.mark.asyncio
async def test_history_capacity_refuses_before_any_file_or_catalog_mutation(
    artifact_owner, monkeypatch
):
    s = artifact_owner
    size = (s.path / "LOG").stat().st_size
    monkeypatch.setattr(history_store, "MAX_LOG", size)
    with pytest.raises(HistoryError, match="capacity"):
        await q.phase(s, "catalog")
    assert (s.path / "LOG").stat().st_size == size and not q.r.p.r.files(s)
    assert q.r.p.r.rows(s) == "0:0:0:0" and s.effects._blocked


@pytest.mark.asyncio
async def test_stop_after_catalog_keeps_original_output_but_blocks_terminal(artifact_owner):
    s = artifact_owner
    await q.phase(s, "catalog")
    before = file_facts(s.root)
    assert (
        await s.effects.request_cancel(
            s.account, s.claim.task_id, key=KEY, deadline=time.monotonic() + 2
        )
    ).accepted
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "3"
    with pytest.raises(HistoryError, match="historical_stop"):
        await q.phase(s, "terminal")
    assert q.r.p.r.rows(s) == "1:1:1:0" and file_facts(s.root) == before
    assert s.db.sql("SELECT status FROM tasks") != "completed"


@pytest.mark.asyncio
async def test_noninherited_forbidden_capability_is_not_hidden_by_p_exception(artifact_owner):
    s = artifact_owner
    s.db.sql("ALTER ROLE reserved_p NOINHERIT", role="postgres")
    s.db.sql("GRANT md_app TO reserved_p", role="postgres")
    try:
        with pytest.raises(HistoryError):
            await q.phase(s, "catalog")
        assert not q.r.p.r.files(s) and q.r.p.r.rows(s) == "0:0:0:0"
    finally:
        s.db.sql("REVOKE md_app FROM reserved_p; ALTER ROLE reserved_p INHERIT", role="postgres")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["lost_ack", "cancel"])
async def test_durable_prepare_without_append_ack_never_reaches_artifact_files(
    artifact_owner, monkeypatch, mode
):
    s = artifact_owner
    native = HistoryStore.append
    entered, release = threading.Event(), threading.Event()

    def held(store, command, **kwargs):
        result = native(store, command, **kwargs)
        if type(command.data) is ArtifactPrepare:
            entered.set()
            if mode == "lost_ack":
                raise HistoryError("durable_result_unavailable", uncertain=True)
            assert release.wait(5)
        return result

    monkeypatch.setattr(HistoryStore, "append", held)
    task = asyncio.create_task(q.phase(s, "catalog"))
    try:
        if mode == "cancel":
            async with asyncio.timeout(5):
                while not entered.is_set():
                    await asyncio.sleep(0.01)
            task.cancel("original durable prepare cancellation")
            await asyncio.sleep(0.03)
            task.cancel("repeated durable prepare cancellation")
            assert not task.done() and not available(s.path, "OP_LOCK")
            release.set()
        error = HistoryError if mode == "lost_ack" else asyncio.CancelledError
        with pytest.raises(error):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert s.effects._blocked and s.history._blocked and not q.r.p.r.files(s)
    assert (
        q.r.p.r.rows(s) == "0:0:0:0"
        and s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
    )
    # Explicit test-only read after full draining/closure, never a writer reset.
    await s.history.aclose(deadline=time.monotonic() + 3)
    with HistoryStore(
        s.path, s.identity, s.ownership, deadline=time.monotonic() + 2, profile="artifact-v2"
    ) as reader:
        assert len(reader.verify(deadline=time.monotonic() + 2).entries) == 2


@pytest.mark.asyncio
async def test_h_release_failure_cannot_promote_committed_catalog(artifact_owner, monkeypatch):
    s = artifact_owner
    native = HistoryCoordinator._exit

    def fail(owner, original):
        native(owner, original)
        if original is None:
            raise OSError("synthetic H release acknowledgement failure")

    monkeypatch.setattr(HistoryCoordinator, "_exit", fail)
    with pytest.raises(OSError, match="H release"):
        await q.phase(s, "catalog")
    assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    assert (
        q.r.p.r.rows(s) == "1:1:1:0"
        and s.db.sql("SELECT count(*) FROM task_history_projection") == "2"
    )
    before = q.state(s)
    with pytest.raises(HistoryError, match="blocked"):
        await q.phase(s, "catalog")
    assert q.state(s) == before
