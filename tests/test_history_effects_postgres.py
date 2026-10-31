"""Owned synthetic Send/Stop boundary, never a runtime/provider qualification."""

import asyncio
import threading
from pathlib import Path
from uuid import uuid4

import asyncpg
from asyncpg.transaction import Transaction
import pytest
import pytest_asyncio

from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.entitlements.service import EntitlementService
from orchestrator.memory.store import MemoryStore
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.services.restore_history.model import HistoryError, SendPossible, StopIntent
from orchestrator.services.restore_history.model import Command, encode_entry
from orchestrator.services.restore_history.store import HistoryStore
from orchestrator.tasks._invocation import InferenceInvocation
from orchestrator.tasks.inference import InferenceRecovery
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import KEY
from tests.test_history_composition_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    scenario as scenario,
    submission,
    deadline,
    entries,
)
from tests.test_history_coordination import available
from tests.history_cleanup_support import held_reset

SQL = Path(__file__).resolve().parents[1] / "migrations/057_restore_history_effects.sql"


def install(db):
    db.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    db.sql(
        "GRANT EXECUTE ON FUNCTION task_history_send_project(bytea),task_history_stop_project(bytea) TO md_app;"
        "GRANT EXECUTE ON FUNCTION task_history_page(uuid,bigint,text) TO md_app,md_acceptor;"
    )


@pytest_asyncio.fixture
async def effects(scenario):
    s = scenario
    install(s.db)
    s.projection = HistoryProjection(s.identity, acceptor="md_acceptor", worker="md_app")
    s.accept = HistoryAcceptance(s.tasks, s.history, s.projection, KEY)
    s.accepted = await s.accept.accept(deadline=deadline(), **submission(s.account))
    async with s.db.pool() as pool:
        s.worker_pool = pool
        s.recovery = InferenceRecovery(pool)
        s.recovery.principals = ("md_app", "md_acceptor")
        s.worker = TaskStore(
            pool, s.tasks._enc, MemoryStore(pool, s.tasks._enc), inference_recovery=s.recovery
        )
        s.effects = HistoryEffects(s.worker, s.history, s.projection)
        s.invocation = InferenceInvocation()
        yield s


async def running(s):
    s.claim = await s.worker.claim(s.accepted.task_id, worker_id="synthetic", lease_s=45)
    assert s.claim is not None
    s.attempt = AttemptIdentity(
        uuid4(), s.account, 0, s.accepted.task_id, s.claim.epoch, uuid4(), uuid4(), uuid4()
    )
    await s.worker.record_compute_scope(
        s.claim.task_id, s.claim.epoch, s.attempt.financial_scope_id, binding=s.claim
    )
    await s.recovery.register(s.attempt, s.invocation)
    s.reservation = await EntitlementService(s.worker_pool).reserve_for_attempt(
        s.attempt, 100, operation="chat"
    )


async def mark(s):
    return await s.effects.mark(
        s.attempt, s.reservation.id, s.invocation, effect_kind="completion", deadline=deadline()
    )


@pytest.mark.asyncio
async def test_fresh_send_uses_original_mark_same_transaction_and_releases_before_transport(
    effects,
):
    s = effects
    await running(s)
    assert await mark(s) is None
    assert available(s.path, "OP_LOCK") and available(s.path, "APPEND_LOCK")
    assert isinstance(entries(s)[1].command.data, SendPossible)
    async with s.db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 2
        assert (
            await conn.fetchval(
                "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
                s.accepted.task_id,
            )
            is True
        )
    # Synthetic transport starts only here; no provider is invoked by the join.
    assert not s.history._gate.locked()


@pytest.mark.asyncio
async def test_stop_before_send_prevents_mark_and_late_terminal_output(effects):
    s = effects
    await running(s)
    result = await s.effects.request_cancel(
        s.account, s.accepted.task_id, key=KEY, deadline=deadline()
    )
    assert result.accepted and result.status is TaskStatus.RUNNING
    assert isinstance(entries(s)[1].command.data, StopIntent)
    with pytest.raises(HistoryError, match="historical_stop"):
        await mark(s)
    assert s.invocation.uncertainty is not None
    async with s.db.connection() as conn:
        assert (
            await conn.fetchval(
                "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
                s.accepted.task_id,
            )
            is False
        )
        assert (
            await conn.fetchval("SELECT status FROM tasks WHERE id=$1", s.accepted.task_id)
            == "running"
        )


@pytest.mark.asyncio
async def test_send_before_stop_preserves_possible_effect_and_complete_refuses(effects):
    s = effects
    await running(s)
    await mark(s)
    assert (
        await s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=deadline())
    ).accepted
    assert [type(e.command.data) for e in entries(s)][1:] == [SendPossible, StopIntent]
    with pytest.raises(HistoryError, match="historical_stop"):
        await s.effects.complete(
            s.claim.task_id,
            s.claim.epoch,
            binding=s.claim,
            content="late success",
            deadline=deadline(),
        )
    async with s.db.connection() as conn:
        assert (
            await conn.fetchval(
                "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
                s.accepted.task_id,
            )
            is True
        )
        assert (
            await conn.fetchval("SELECT status FROM tasks WHERE id=$1", s.accepted.task_id)
            != "completed"
        )


@pytest.mark.asyncio
async def test_queued_stop_is_atomic_and_terminal_repeat_does_not_ack(effects):
    s = effects
    result = await s.effects.request_cancel(
        s.account, s.accepted.task_id, key=KEY, deadline=deadline()
    )
    assert result.accepted and result.status is TaskStatus.CANCELLED
    assert len(entries(s)) == 2
    with pytest.raises(HistoryError, match="historical_stop"):
        await s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=deadline())


@pytest.mark.asyncio
async def test_combined_reader_allows_subsequent_fresh_acceptance_with_send_and_stop(effects):
    s = effects
    await running(s)
    await mark(s)
    await s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=deadline())
    # Another account avoids native running-task fairness; full history is still
    # compared, so neither send nor Stop is filtered away from SQL parity.
    async with s.db.connection() as conn:
        account = await s.db.account(conn)
    result = await s.accept.accept(deadline=deadline(), **submission(account, key="second"))
    assert result.created and len(entries(s)) == 4


@pytest.mark.asyncio
async def test_populated_056_upgrade_copies_only_valid_existing_frames(scenario):
    s = scenario
    await s.accept.accept(deadline=deadline(), **submission(s.account))
    install(s.db)
    async with s.db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_app", "md_acceptor", "md_third"])
async def test_no_direct_projection_or_private_helper_privilege(effects, role):
    async with effects.db.connection(role) as conn:
        for query in [
            "SELECT * FROM task_history_projection",
            "DELETE FROM task_history_projection",
            "SELECT task_history_project_frame(''::bytea,true)",
            "SELECT task_history_decode_effect(''::bytea)",
        ]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SEND_POSSIBLE", "STOP_INTENT"])
async def test_restored_sql_missing_effect_refuses_without_repair(effects, kind):
    s = effects
    await running(s)
    if kind == "SEND_POSSIBLE":
        await mark(s)
    else:
        await s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=deadline())
    # Owner-only synthetic restore simulation. No application database involved.
    s.db.sql(
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable; DELETE FROM task_history_projection WHERE kind='"
        + kind
        + "'; ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable;"
    )
    async with s.db.connection() as conn:
        account = await s.db.account(conn)
    with pytest.raises(HistoryError, match="acceptance_reconciliation_required"):
        await s.accept.accept(deadline=deadline(), **submission(account, key="after-restore"))
    assert len(entries(s)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "stop"])
@pytest.mark.parametrize(
    "fault",
    ["append_ack", "stage_before", "stage_after", "commit_before", "commit_after", "release_after"],
)
async def test_lost_acknowledgement_never_succeeds_or_retries(
    effects, monkeypatch, operation, fault
):
    s = effects
    await running(s)
    append, stage, commit, release = (
        HistoryStore.append,
        s.projection.stage_effect,
        Transaction.commit,
        asyncpg.Pool.release,
    )
    armed = False
    fired = []

    def append_fault(owner, *args, **kwargs):
        result = append(owner, *args, **kwargs)
        if fault == "append_ack":
            fired.append(True)
            raise HistoryError("synthetic_append_ack_lost", uncertain=True)
        return result

    async def staged(*args, **kwargs):
        nonlocal armed
        if fault == "stage_before":
            fired.append(True)
            raise RuntimeError("synthetic stage refused")
        result = await stage(*args, **kwargs)
        armed = True
        if fault == "stage_after":
            fired.append(True)
            raise RuntimeError("synthetic stage ack lost")
        return result

    async def committed(tx):
        if armed and fault.startswith("commit_") and not fired:
            fired.append(True)
            if fault == "commit_after":
                await commit(tx)
            raise asyncpg.ConnectionDoesNotExistError("synthetic COMMIT acknowledgement lost")
        return await commit(tx)

    async def released(pool, conn, **kwargs):
        result = await release(pool, conn, **kwargs)
        if pool is s.worker_pool and armed and fault == "release_after" and not fired:
            fired.append(True)
            raise RuntimeError("synthetic release acknowledgement lost")
        return result

    monkeypatch.setattr(HistoryStore, "append", append_fault)
    monkeypatch.setattr(s.projection, "stage_effect", staged)
    monkeypatch.setattr(Transaction, "commit", committed)
    monkeypatch.setattr(asyncpg.Pool, "release", released)

    def call():
        return (
            mark(s)
            if operation == "send"
            else s.effects.request_cancel(
                s.account, s.accepted.task_id, key=KEY, deadline=deadline()
            )
        )

    with pytest.raises(Exception):
        await call()
    assert fired == [True] and len(entries(s)) == 2
    assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    with pytest.raises(Exception):
        await call()
    assert fired == [True]  # No fresh native call, append, retry or readback promotion.
    committed_outcome = fault in ("commit_after", "release_after")
    async with s.db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == (
            2 if committed_outcome else 1
        )
        row = await conn.fetchrow(
            "SELECT cancel_requested_at FROM tasks WHERE id=$1", s.accepted.task_id
        )
        possible = await conn.fetchval(
            "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
            s.accepted.task_id,
        )
        assert possible == (operation == "send" and committed_outcome)
        assert (row["cancel_requested_at"] is not None) == (
            operation == "stop" and committed_outcome
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "stop"])
@pytest.mark.parametrize("phase", ["append", "stage", "commit"])
async def test_cancellation_retains_history_until_underlying_stage_or_commit_settles(
    effects, monkeypatch, operation, phase
):
    s = effects
    await running(s)
    entered, proceed = asyncio.Event(), asyncio.Event()
    physical_entered, physical_proceed = threading.Event(), threading.Event()
    original_append, original_stage, original_commit = (
        HistoryStore.append,
        s.projection.stage_effect,
        Transaction.commit,
    )
    staged = False

    def held_append(owner, *args, **kwargs):
        result = original_append(owner, *args, **kwargs)
        if phase == "append":
            physical_entered.set()
            assert physical_proceed.wait(5)
        return result

    async def held_stage(*args, **kwargs):
        nonlocal staged
        result = await original_stage(*args, **kwargs)
        staged = True
        if phase == "stage":
            entered.set()
            await proceed.wait()
        return result

    async def held_commit(tx):
        if staged and phase == "commit":
            await original_commit(tx)
            entered.set()
            await proceed.wait()
            return
        return await original_commit(tx)

    monkeypatch.setattr(HistoryStore, "append", held_append)
    monkeypatch.setattr(s.projection, "stage_effect", held_stage)
    monkeypatch.setattr(Transaction, "commit", held_commit)
    call = (
        mark(s)
        if operation == "send"
        else s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=deadline())
    )
    task = asyncio.create_task(call)
    try:
        if phase == "append":
            assert await asyncio.to_thread(physical_entered.wait, 5)
        else:
            await asyncio.wait_for(entered.wait(), 5)
        task.cancel("original effect cancellation")
        await asyncio.sleep(0.02)
        task.cancel("repeated effect cancellation")
        await asyncio.sleep(0.02)
        if phase == "append":
            assert not task.done() and not available(s.path, "OP_LOCK")
        physical_proceed.set()
        proceed.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("original effect cancellation",)
        assert available(s.path, "OP_LOCK") and s.effects._blocked
        assert len(entries(s)) == 2
        async with s.db.connection() as conn:
            assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == (
                2 if phase == "commit" else 1
            )
    finally:
        physical_proceed.set()
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "stop"])
@pytest.mark.parametrize("target", [1, 2])
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_actual_holder_reset_is_retained_through_effect_cleanup(
    effects, monkeypatch, operation, target, mode
):
    s = effects
    await running(s)
    async with held_reset(s.worker_pool, monkeypatch, target, mode == "failure") as (
        entered,
        proceed,
    ):
        limit = asyncio.get_running_loop().time() + 1.5
        call = (
            s.effects.mark(
                s.attempt, s.reservation.id, s.invocation, effect_kind="completion", deadline=limit
            )
            if operation == "send"
            else s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=limit)
        )
        task = asyncio.create_task(call)
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original actual-reset cancellation")
            elif mode == "deadline":
                await asyncio.sleep(max(0, limit - asyncio.get_running_loop().time()) + 0.02)
            await asyncio.sleep(0.02)
            if mode != "failure":
                task.cancel("repeated actual-reset cancellation")
                await asyncio.sleep(0.02)
            assert not task.done() and not available(s.path, "OP_LOCK")
            proceed.set()
            error = (
                asyncio.CancelledError
                if mode == "cancel"
                else TimeoutError
                if mode == "deadline"
                else Exception
            )
            with pytest.raises(error) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original actual-reset cancellation",)
            assert available(s.path, "OP_LOCK") and s.effects._blocked
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_wins_output_race_before_terminal_mutation(effects, monkeypatch):
    s = effects
    await running(s)
    entered, proceed = asyncio.Event(), asyncio.Event()
    native = s.worker._request_cancel_with_stage

    async def held(*args, **kwargs):
        result = await native(*args, **kwargs)
        entered.set()
        await proceed.wait()
        return result

    monkeypatch.setattr(s.worker, "_request_cancel_with_stage", held)
    stop = asyncio.create_task(
        s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=deadline())
    )
    output = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        output = asyncio.create_task(
            s.effects.complete(
                s.claim.task_id,
                s.claim.epoch,
                binding=s.claim,
                content="late synthetic success",
                deadline=deadline(),
            )
        )
        await asyncio.sleep(0.02)
        assert not output.done()
        proceed.set()
        assert (await stop).accepted
        with pytest.raises(HistoryError, match="historical_stop"):
            await output
        async with s.db.connection() as conn:
            assert (
                await conn.fetchval("SELECT status FROM tasks WHERE id=$1", s.claim.task_id)
                != "completed"
            )
    finally:
        proceed.set()
        await asyncio.gather(stop, *([output] if output else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_reconstructed_invocation_cannot_adopt_a_historical_send(effects):
    s = effects
    await running(s)
    await mark(s)
    fresh = HistoryEffects(s.worker, s.history, s.projection)
    with pytest.raises(HistoryError, match="historical_send"):
        await fresh.mark(
            s.attempt, uuid4(), InferenceInvocation(), effect_kind="completion", deadline=deadline()
        )
    assert len(entries(s)) == 2


@pytest.mark.asyncio
async def test_new_effect_facts_survive_native_account_cascade(effects):
    s = effects
    await running(s)
    await mark(s)
    await s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=deadline())
    async with s.db.connection() as conn:
        await conn.execute("DELETE FROM users WHERE id=$1", s.account)
        assert await conn.fetchval("SELECT count(*) FROM tasks") == 0
        assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 3
    async with s.db.connection("md_app") as conn, conn.transaction():
        facts = await s.projection.page(conn, after_seq=0, deadline=deadline())
        assert len(facts) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "stop"])
async def test_scoped_native_transaction_refuses_savepoint_as_commit(
    effects, monkeypatch, operation
):
    s = effects
    await running(s)
    original = asyncpg.Pool.acquire
    calls = 0

    async def inherited(pool, *args, **kwargs):
        nonlocal calls
        conn = await original(pool, *args, **kwargs)
        if pool is s.worker_pool:
            calls += 1
            if calls == 2:
                await conn.execute("BEGIN")
        return conn

    monkeypatch.setattr(asyncpg.Pool, "acquire", inherited)
    with pytest.raises(Exception):
        if operation == "send":
            await mark(s)
        else:
            await s.effects.request_cancel(
                s.account, s.accepted.task_id, key=KEY, deadline=deadline()
            )
    assert calls == 2 and len(entries(s)) == 1
    assert s.effects._blocked and available(s.path, "OP_LOCK")


@pytest.mark.asyncio
async def test_effect_decoder_is_closed_and_canonical(effects):
    from tests.test_history_acceptance_postgres import frame_with
    import json

    s = effects
    await s.effects.request_cancel(s.account, s.accepted.task_id, key=KEY, deadline=deadline())
    entry = entries(s)[1]
    frame = encode_entry(entry.history_id, entry.seq, entry.prev_digest, entry.command)
    payload = json.loads(frame[4:-32])
    async with s.db.connection() as conn:
        assert (
            json.loads(await conn.fetchval("SELECT task_history_decode_effect($1)", frame))
            == payload
        )
        for field, value in [
            ("reason", "operator"),
            ("account_generation", True),
            ("task_id", str(uuid4()).upper()),
            ("extra", "no"),
        ]:
            changed = json.loads(json.dumps(payload))
            changed["data"][field] = value
            with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                await conn.fetchval("SELECT task_history_decode_effect($1)", frame_with(changed))
        for damaged in [b"", frame[:-1], frame + b"x", frame[:-1] + bytes([frame[-1] ^ 1])]:
            with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                await conn.fetchval("SELECT task_history_decode_effect($1)", damaged)


@pytest.mark.asyncio
async def test_global_sequence_conflict_cannot_overwrite_acceptance(effects):
    s = effects
    await s.worker.request_cancel(s.account, s.accepted.task_id)
    accept = entries(s)[0]
    frame = encode_entry(
        accept.history_id,
        1,
        bytes(32),
        Command(uuid4(), StopIntent(s.account, 0, s.accepted.task_id, "owner")),
    )
    async with s.db.connection("md_app") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetchrow("SELECT * FROM task_history_stop_project($1)", frame)
    async with s.db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 1


@pytest.mark.asyncio
async def test_terminal_completion_before_stop_is_not_retroactively_cancelled(effects):
    s = effects
    await running(s)
    assert (
        await s.effects.complete(
            s.claim.task_id,
            s.claim.epoch,
            binding=s.claim,
            content="synthetic completed",
            deadline=deadline(),
        )
        is TaskStatus.COMPLETED
    )
    result = await s.effects.request_cancel(
        s.account, s.accepted.task_id, key=KEY, deadline=deadline()
    )
    assert not result.accepted and result.status is TaskStatus.COMPLETED
    assert len(entries(s)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    [
        "GRANT EXECUTE ON FUNCTION task_history_send_project(bytea) TO md_acceptor",
        "GRANT EXECUTE ON FUNCTION task_history_stop_project(bytea) TO PUBLIC",
        "GRANT SELECT(frame) ON task_history_projection TO md_app",
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable",
    ],
)
async def test_new_catalog_drift_refuses_before_any_send(effects, drift):
    s = effects
    await running(s)
    s.db.sql(drift)
    with pytest.raises(HistoryError, match="projection_catalog_refused"):
        await mark(s)
    assert len(entries(s)) == 1


@pytest.mark.asyncio
async def test_send_holds_original_account_guard_until_history_and_commit(effects, monkeypatch):
    s = effects
    await running(s)
    entered, proceed = threading.Event(), threading.Event()
    native = HistoryStore.append

    def held(owner, *args, **kwargs):
        result = native(owner, *args, **kwargs)
        entered.set()
        assert proceed.wait(5)
        return result

    monkeypatch.setattr(HistoryStore, "append", held)
    task = asyncio.create_task(mark(s))
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
            proceed.set()
            assert await task is None
            await reset_task
        assert len(entries(s)) == 2
    finally:
        proceed.set()
        await asyncio.gather(task, *([reset_task] if reset_task else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "stop"])
async def test_history_release_ack_loss_cannot_acknowledge_committed_effect(
    effects, monkeypatch, operation
):
    s = effects
    await running(s)
    native = s.history._exit

    def lost(original):
        native(original)
        raise RuntimeError("synthetic H release acknowledgement lost")

    monkeypatch.setattr(s.history, "_exit", lost)
    with pytest.raises(RuntimeError, match="H release acknowledgement lost"):
        if operation == "send":
            await mark(s)
        else:
            await s.effects.request_cancel(
                s.account, s.accepted.task_id, key=KEY, deadline=deadline()
            )
    assert s.effects._blocked and available(s.path, "OP_LOCK") and len(entries(s)) == 2
    async with s.db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "stop"])
@pytest.mark.parametrize("structured", [False, True])
async def test_cancelled_queued_effect_child_settles_before_its_history_owner(
    effects, monkeypatch, operation, structured
):
    s = effects
    await running(s)
    queued = asyncio.Event()
    acquire = s.history._gate.acquire
    child = None
    original = RuntimeError("synthetic owner failure")

    async def observed_acquire():
        queued.set()
        return await acquire()

    def call():
        return (
            mark(s)
            if operation == "send"
            else s.effects.request_cancel(
                s.account, s.accepted.task_id, key=KEY, deadline=deadline()
            )
        )

    async def owner():
        nonlocal child
        async with s.history.operation(deadline=deadline()):
            monkeypatch.setattr(s.history._gate, "acquire", observed_acquire)
            if structured:
                async with asyncio.TaskGroup() as group:
                    child = group.create_task(call())
                    await queued.wait()
                    raise original
            else:
                child = asyncio.create_task(call())
                await queued.wait()
                child.cancel("original queued effect cancellation")
                with pytest.raises(asyncio.CancelledError) as caught:
                    await child
                assert caught.value.args == ("original queued effect cancellation",)
                assert not available(s.path, "OP_LOCK")

    task = asyncio.create_task(owner())
    try:
        await queued.wait()
        done, _ = await asyncio.wait({task}, timeout=0.5)
        assert task in done, "queued effect cancellation deadlocked behind its joining owner"
        if structured:
            with pytest.raises(ExceptionGroup) as caught:
                await task
            assert caught.value.exceptions == (original,)
        else:
            with pytest.raises(HistoryError, match="coordinator_blocked"):
                await task
        assert not s.history._gate.locked() and not s.history._gate._waiters
        assert len(entries(s)) == 1
    finally:
        if child is not None and not child.done():
            child.cancel()
        await asyncio.gather(task, *([child] if child is not None else []), return_exceptions=True)
