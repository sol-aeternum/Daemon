"""Actual synthetic terminal-v3 transaction, restore and physical cleanup proof."""

import asyncio
from pathlib import Path
import threading
import time

import asyncpg
from asyncpg.transaction import Transaction
import pytest
import pytest_asyncio

from orchestrator.services.restore_history.artifact_projection import ArtifactHistoryProjection
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.model import HistoryError, TerminalOutputPrepare
from orchestrator.services.restore_history.store import HistoryStore
from orchestrator.services.restore_history import store as history_store
from orchestrator.services.restore_history.terminal_projection import TerminalHistoryProjection
from orchestrator.tasks.states import TaskStatus
from orchestrator.services.account_control.transaction import (
    TransactionUnavailable,
    ObservationBlocked,
)
from orchestrator.tasks._invocation import invocation
from tests import test_artifact_history_postgres as b
from tests import test_history_output_phases_postgres as q
from tests.history_cleanup_support import held_reset
from tests.restore_history_support import KEY
from tests.test_artifact_restore_boundary_postgres import (
    checkpoint,
    restored,
    parity,
    data_facts,
    file_facts,
)
from tests.test_history_coordination import available

metadata_cluster = b.metadata_cluster
db = b.db
projection_db = b.projection_db
scenario = b.scenario
effects = b.effects
reserved = b.reserved
presenter = b.presenter
reservation_owner = b.reservation_owner
preparer = b.preparer
phase_owner = b.phase_owner
artifact_owner = b.artifact_owner
SQL = Path(__file__).resolve().parents[1] / "migrations/065_terminal_output_prepare.sql"


@pytest_asyncio.fixture
async def terminal_owner(artifact_owner):
    s = artifact_owner
    await s.history.aclose(deadline=time.monotonic() + 3)
    s.db.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    s.db.sql(
        "GRANT EXECUTE ON FUNCTION task_history_terminal_project(bytea) TO md_app;"
        "GRANT EXECUTE ON FUNCTION task_history_terminal_page(uuid,bigint,text) TO md_app,md_acceptor;"
    )
    s.history = HistoryCoordinator(s.path, s.identity, s.ownership, profile="terminal-v3")
    await s.history.open(deadline=time.monotonic() + 3)
    s.projection = TerminalHistoryProjection(
        s.identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    s.effects = HistoryEffects(s.worker, s.history, s.projection)
    try:
        yield s
    finally:
        await s.history.aclose(deadline=time.monotonic() + 3)


async def finish(s):
    return await s.effects.complete(
        s.claim.task_id,
        s.claim.epoch,
        binding=s.claim,
        content="synthetic protected final",
        message_fields={"metadata": {"synthetic_output": "kept"}},
        deadline=time.monotonic() + 2,
    )


async def history(s):
    if s.history._blocked:
        # Read-only test evidence after physical drain and complete writer close;
        # never replace/reset a failed writer or use readback as an outcome.
        await s.history.aclose(deadline=time.monotonic() + 3)
        with HistoryStore(
            s.path, s.identity, s.ownership, deadline=time.monotonic() + 2, profile="terminal-v3"
        ) as reader:
            return reader.verify(deadline=time.monotonic() + 2)
    async with s.history.operation(deadline=time.monotonic() + 2) as op:
        return await op.verify()


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog", [False, True])
async def test_terminal_text_and_projection_share_native_top_level_commit(terminal_owner, catalog):
    s = terminal_owner
    if catalog:
        await q.phase(s, "catalog")
    assert await finish(s) is TaskStatus.COMPLETED
    state = await parity(s)
    assert len(state.entries) == (3 if catalog else 2)
    assert type(state.entries[-1].command.data) is TerminalOutputPrepare
    snapshot = await s.worker.task_for_key(s.account, "stable-client-key")
    assert snapshot.task_id == s.claim.task_id and snapshot.status is TaskStatus.COMPLETED
    assert snapshot.content == "synthetic protected final"
    assert len(await b.read_facts(s, "md_acceptor")) == len(state.entries)
    assert available(s.path, "OP_LOCK") and not s.effects._blocked


@pytest.mark.asyncio
async def test_legacy_profile_refuses_expanded_sql(terminal_owner):
    s = terminal_owner
    old = ArtifactHistoryProjection(
        s.identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    async with s.db.connection("md_app") as conn, conn.transaction():
        with pytest.raises(HistoryError, match="projection_catalog_refused"):
            await old.qualify(conn, writing=False, deadline=time.monotonic() + 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog", [False, True])
async def test_actual_restore_missing_terminal_fact_refuses_without_replay(
    terminal_owner, tmp_path, monkeypatch, catalog
):
    s = terminal_owner
    if catalog:
        await q.phase(s, "catalog")
    saved = await checkpoint(s, tmp_path)
    assert await finish(s) is TaskStatus.COMPLETED
    async with restored(s, saved, projection=s.projection) as fresh:
        before, files = data_facts(fresh), file_facts(fresh.root)

        async def forbidden(*args, **kwargs):
            pytest.fail("native completion ran after terminal projection rollback")

        monkeypatch.setattr(fresh.worker, "_stage_completed_body", forbidden)
        with pytest.raises(HistoryError, match="effects_reconciliation_required"):
            await finish(fresh)
        assert fresh.effects._blocked and fresh.history._blocked
        with pytest.raises(HistoryError, match="blocked"):
            await finish(fresh)
        assert data_facts(fresh) == before and file_facts(fresh.root) == files


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_unknown_terminal_commit_never_becomes_success_or_new_work(
    terminal_owner, monkeypatch, fault
):
    s = terminal_owner
    native_stage = s.projection.stage_terminal
    native_commit = Transaction.commit
    ready = False
    fired = 0

    async def stage(*args, **kwargs):
        nonlocal ready
        result = await native_stage(*args, **kwargs)
        ready = True
        return result

    async def commit(tx):
        nonlocal fired
        if ready and tx._connection._params.user == "md_app":
            fired += 1
            s.db.bridge.commit_fault_role = "md_app"
            s.db.bridge.commit_fault = fault
        try:
            return await native_commit(tx)
        finally:
            s.db.bridge.commit_fault = None

    monkeypatch.setattr(s.projection, "stage_terminal", stage)
    monkeypatch.setattr(Transaction, "commit", commit)
    try:
        with pytest.raises((TransactionUnavailable, TimeoutError)) as caught:
            await finish(s)
        if isinstance(caught.value, TransactionUnavailable):
            assert caught.value.commit_attempted and not caught.value.commit_acknowledged
    finally:
        s.db.bridge.commit_fault = None
    assert fired == 1 and s.effects._blocked and s.history._blocked
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == (
        "2" if fault == "after" else "1"
    )
    assert s.db.sql("SELECT status FROM tasks") == ("completed" if fault == "after" else "running")
    assert available(s.path, "OP_LOCK")
    with pytest.raises(HistoryError, match="blocked"):
        await finish(s)
    assert fired == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_terminal_actual_reset_drains_before_h_release(terminal_owner, monkeypatch, mode):
    s = terminal_owner
    async with held_reset(s.worker._pool, monkeypatch, 2, mode == "failure") as (entered, release):
        task = asyncio.create_task(finish(s))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert s.db.sql("SELECT status FROM tasks") == "completed"
            if mode == "deadline":
                await asyncio.sleep(2.05)
                task.cancel("repeated after terminal deadline")
            elif mode == "cancel":
                task.cancel("original terminal cancellation")
            await asyncio.sleep(0.03)
            if mode == "cancel":
                task.cancel("repeated terminal cancellation")
            assert not task.done() and not available(s.path, "OP_LOCK")
            release.set()
            with pytest.raises(
                {
                    "cancel": asyncio.CancelledError,
                    "deadline": TimeoutError,
                    "failure": TransactionUnavailable,
                }[mode]
            ) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original terminal cancellation",)
            elif mode == "failure":
                assert caught.value.commit_attempted and caught.value.commit_acknowledged
                assert isinstance(caught.value.__cause__, RuntimeError)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    with pytest.raises(HistoryError, match="blocked"):
        await finish(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["lost_ack", "cancel"])
async def test_terminal_prepare_uncertainty_never_reaches_text(terminal_owner, monkeypatch, mode):
    s = terminal_owner
    native = HistoryStore.append
    entered = threading.Event()
    release = threading.Event()

    def append(store, command, **kwargs):
        result = native(store, command, **kwargs)
        if type(command.data) is TerminalOutputPrepare:
            entered.set()
            if mode == "lost_ack":
                raise HistoryError("durable_result_unavailable", uncertain=True)
            assert release.wait(5)
        return result

    async def forbidden(*args, **kwargs):
        pytest.fail("terminal body ran after uncertain prepare")

    monkeypatch.setattr(HistoryStore, "append", append)
    monkeypatch.setattr(s.worker, "_stage_completed_body", forbidden)
    task = asyncio.create_task(finish(s))
    try:
        if mode == "cancel":
            async with asyncio.timeout(5):
                while not entered.is_set():
                    await asyncio.sleep(0.01)
            task.cancel("prepare cancelled")
            await asyncio.sleep(0.03)
            task.cancel("prepare repeated")
            assert not task.done() and not available(s.path, "OP_LOCK")
            release.set()
        with pytest.raises(
            TransactionUnavailable if mode == "lost_ack" else asyncio.CancelledError
        ) as caught:
            await task
        if mode == "lost_ack":
            assert isinstance(caught.value, TransactionUnavailable)
            assert not caught.value.commit_attempted and not caught.value.commit_acknowledged
            assert isinstance(caught.value.__cause__, HistoryError)
            assert caught.value.__cause__.code == "durable_result_unavailable"
        else:
            assert caught.value.args == ("prepare cancelled",)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert (
        s.db.sql("SELECT status FROM tasks") == "running"
        and s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
    )
    assert s.effects._blocked and s.history._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    ["preparation", "reservation", "catalog", "presentation", "partial", "terminal", "send"],
)
async def test_historical_terminal_refuses_all_positive_effects(terminal_owner, phase):
    s = terminal_owner
    assert await finish(s) is TaskStatus.COMPLETED
    before = data_facts(s)
    files = file_facts(s.root)
    with pytest.raises(HistoryError, match="historical_terminal"):
        if phase == "partial":
            await s.effects.write_partial(
                s.claim.task_id,
                s.claim.epoch,
                binding=s.claim,
                content="late",
                delta_seq=9,
                deadline=time.monotonic() + 2,
            )
        elif phase == "send":
            await s.effects.mark(
                s.attempt,
                s.reservation.id,
                s.invocation,
                effect_kind="completion",
                deadline=time.monotonic() + 2,
            )
        else:
            await q.phase(s, phase)
    assert data_facts(s) == before and file_facts(s.root) == files


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "control", ["stop", "suspended", "generation", "quarantine", "missing_coverage"]
)
async def test_control_outcomes_do_not_create_completed_text_facts(terminal_owner, control):
    s = terminal_owner
    if control == "stop":
        assert (
            await s.effects.request_cancel(
                s.account, s.claim.task_id, key=KEY, deadline=time.monotonic() + 2
            )
        ).accepted
        with pytest.raises(HistoryError, match="historical_stop"):
            await finish(s)
    else:
        if control == "suspended":
            s.db.sql("UPDATE entitlement_accounts SET status='suspended'")
        elif control == "generation":
            s.db.sql("UPDATE entitlement_accounts SET content_generation=1")
        else:
            if control == "quarantine":
                await s.effects.mark(
                    s.attempt,
                    s.reservation.id,
                    s.invocation,
                    effect_kind="completion",
                    deadline=time.monotonic() + 2,
                )
            s.invocation.call_failed = True
        token = invocation.set(s.invocation)
        try:
            if control == "missing_coverage":
                with pytest.raises(ObservationBlocked, match="inference_coverage_unavailable"):
                    await finish(s)
            else:
                result = await finish(s)
                assert result in (
                    TaskStatus.FAILED,
                    TaskStatus.NEEDS_ATTENTION,
                    TaskStatus.CANCELLED,
                )
                if control == "quarantine":
                    assert result is TaskStatus.NEEDS_ATTENTION
        finally:
            invocation.reset(token)
    assert not any(
        type(e.command.data) is TerminalOutputPrepare for e in (await history(s)).entries
    )
    assert s.db.sql("SELECT status FROM tasks") != "completed"


@pytest.mark.asyncio
async def test_terminal_capacity_refuses_before_completed_body(terminal_owner, monkeypatch):
    s = terminal_owner
    size = (s.path / "LOG").stat().st_size
    monkeypatch.setattr(history_store, "MAX_LOG", size)
    with pytest.raises(TransactionUnavailable) as caught:
        await finish(s)
    assert not caught.value.commit_attempted and not caught.value.commit_acknowledged
    assert (
        isinstance(caught.value.__cause__, HistoryError)
        and caught.value.__cause__.code == "capacity"
    )
    assert (
        s.db.sql("SELECT status FROM tasks") == "running"
        and (s.path / "LOG").stat().st_size == size
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_acceptor", "reserved_p", "md_owner"])
async def test_terminal_stager_rejects_every_other_principal(terminal_owner, role):
    s = terminal_owner
    async with s.db.connection(role) as conn, conn.transaction():
        with pytest.raises(asyncpg.PostgresError):
            await conn.fetch("SELECT * FROM task_history_terminal_project($1)", b"invalid")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["PUBLIC", "md_acceptor"])
async def test_extra_terminal_stage_grant_fails_closed_before_prepare(terminal_owner, target):
    s = terminal_owner
    s.db.sql("GRANT EXECUTE ON FUNCTION task_history_terminal_project(bytea) TO " + target)
    try:
        with pytest.raises(HistoryError):
            await finish(s)
        assert (
            s.db.sql("SELECT status FROM tasks") == "running"
            and (s.path / "LOG").stat().st_size > 0
        )
        assert not any(
            type(e.command.data) is TerminalOutputPrepare for e in (await history(s)).entries
        )
    finally:
        s.db.sql("REVOKE EXECUTE ON FUNCTION task_history_terminal_project(bytea) FROM " + target)


@pytest.mark.asyncio
async def test_committed_terminal_cannot_be_backfilled(terminal_owner):
    s = terminal_owner
    assert await finish(s) is TaskStatus.COMPLETED
    entry = (await history(s)).entries[-1]
    s.db.sql(
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;"
        "DELETE FROM task_history_projection WHERE kind='TERMINAL_OUTPUT_PREPARE';"
        "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable;"
    )
    async with s.db.connection("md_app") as conn, conn.transaction():
        with pytest.raises(HistoryError):
            await s.projection.stage_terminal(conn, entry, deadline=time.monotonic() + 2)
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
    assert s.db.sql("SELECT status FROM tasks") == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["account_id", "account_generation", "result_message_id", "acceptance_record_id", "epoch"],
)
async def test_terminal_stager_validates_original_binding_in_native_transaction(
    terminal_owner, monkeypatch, field
):
    from dataclasses import replace
    from uuid import UUID
    from orchestrator.services.restore_history.model import Command, decode_entry, encode_entry

    s = terminal_owner
    native = s.projection.stage_terminal

    async def wrong(conn, entry, **kwargs):
        value = 1 if field == "account_generation" else 2 if field == "epoch" else UUID(int=999)
        command = Command(entry.command.record_id, replace(entry.command.data, **{field: value}))
        changed = decode_entry(
            encode_entry(
                entry.history_id, entry.seq, entry.prev_digest, command, profile="terminal-v3"
            ),
            profile="terminal-v3",
        )
        return await native(conn, changed, **kwargs)

    monkeypatch.setattr(s.projection, "stage_terminal", wrong)
    with pytest.raises(TransactionUnavailable) as caught:
        await finish(s)
    assert not caught.value.commit_attempted and not caught.value.commit_acknowledged
    assert isinstance(caught.value.__cause__, HistoryError)
    assert caught.value.__cause__.code == "projection_unavailable"
    assert s.effects._blocked and s.history._blocked
    assert s.db.sql("SELECT status FROM tasks") == "running"
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
    assert len((await history(s)).entries) == 2


@pytest.mark.asyncio
async def test_terminal_reader_key_and_service_mutation_permissions_are_closed(terminal_owner):
    s = terminal_owner
    for role in ("md_app", "md_acceptor"):
        async with s.db.connection(role) as conn:
            with pytest.raises(asyncpg.PostgresError):
                await conn.fetch(
                    "SELECT * FROM task_history_terminal_page($1,0,$2)",
                    s.identity.history_id,
                    "0" * 64,
                )
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute("DELETE FROM task_history_projection")
            with pytest.raises(asyncpg.PostgresError):
                await conn.fetch("SELECT task_history_decode_terminal($1)", b"bad")


@pytest.mark.asyncio
async def test_p_cannot_inherit_worker_terminal_capability(terminal_owner):
    s = terminal_owner
    s.db.sql("GRANT md_app TO reserved_p", role="postgres")
    try:
        with pytest.raises(HistoryError):
            await finish(s)
        assert s.db.sql("SELECT status FROM tasks") == "running"
    finally:
        s.db.sql("REVOKE md_app FROM reserved_p", role="postgres")


@pytest.mark.asyncio
async def test_h_release_loss_cannot_promote_known_terminal_commit(terminal_owner, monkeypatch):
    s = terminal_owner
    native = HistoryCoordinator._exit

    def fail(owner, original):
        native(owner, original)
        if original is None:
            raise OSError("synthetic terminal release acknowledgement")

    # Only the completed-output operation exits after the transaction; _parity
    # borrows that operation and does not open another H lifetime.
    monkeypatch.setattr(HistoryCoordinator, "_exit", fail)
    with pytest.raises(OSError, match="terminal release"):
        await finish(s)
    assert s.db.sql("SELECT status FROM tasks") == "completed"
    assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    with pytest.raises(HistoryError, match="blocked"):
        await finish(s)
