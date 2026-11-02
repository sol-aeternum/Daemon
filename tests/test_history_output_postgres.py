"""Synthetic native text publication under H; no runtime/transport activation."""

import asyncio
import json

import asyncpg
from asyncpg.transaction import Transaction

import pytest

from orchestrator.services.restore_history.model import HistoryError
from orchestrator.tasks.states import TaskStatus
from orchestrator.services.account_control.transaction import (
    ObservationBlocked,
    TransactionUnavailable,
)
from orchestrator.tasks.store import LeaseLost
from tests.history_cleanup_support import held_reset
from tests.test_history_coordination import available
from tests.restore_history_support import KEY
from tests.test_history_effects_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    scenario as scenario,
    effects as effects,
    deadline,
    running,
    entries,
    mark,
)


async def partial(s, text="synthetic partial"):
    return await s.effects.write_partial(
        s.claim.task_id,
        s.claim.epoch,
        binding=s.claim,
        content=text,
        delta_seq=3,
        deadline=deadline(),
    )


@pytest.mark.asyncio
async def test_partial_and_terminal_fields_use_original_native_commit(effects):
    s = effects
    await running(s)
    assert await partial(s) is None
    async with s.db.connection() as conn:
        assert (
            await conn.fetchval("SELECT content_delta_seq FROM tasks WHERE id=$1", s.claim.task_id)
            == 3
        )
        text = await conn.fetchval(
            "SELECT content FROM messages WHERE id=$1", s.claim.result_message_id
        )
        assert s.tasks._enc.decrypt(text) == "synthetic partial"
    fields = {"metadata": {"synthetic_output": "kept"}}
    assert (
        await s.effects.complete(
            s.claim.task_id,
            s.claim.epoch,
            binding=s.claim,
            content="synthetic final",
            message_fields=fields,
            deadline=deadline(),
        )
        is TaskStatus.COMPLETED
    )
    async with s.db.connection() as conn:
        row = await conn.fetchrow(
            "SELECT content,metadata FROM messages WHERE id=$1", s.claim.result_message_id
        )
        assert s.tasks._enc.decrypt(row["content"]) == "synthetic final"
        assert json.loads(row["metadata"])["synthetic_output"] == "kept"
    assert len(entries(s)) == 1  # No output record, plaintext store or projection added.


@pytest.mark.asyncio
async def test_durable_stop_blocks_subsequent_partial_before_native_write(effects, monkeypatch):
    s = effects
    await running(s)
    assert (
        await s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=deadline())
    ).accepted

    async def forbidden(*args, **kwargs):
        pytest.fail("partial reached native write after H Stop")

    monkeypatch.setattr(s.worker, "write_partial", forbidden)
    with pytest.raises(HistoryError, match="historical_stop"):
        await partial(s)


@pytest.mark.asyncio
async def test_stop_wins_gate_before_partial_and_retains_original_result(effects, monkeypatch):
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
        s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=deadline())
    )
    output = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        output = asyncio.create_task(partial(s))
        await asyncio.sleep(0.02)
        assert not output.done()
        proceed.set()
        assert (await stop).accepted
        with pytest.raises(HistoryError, match="historical_stop"):
            await output
        async with s.db.connection() as conn:
            assert (
                await conn.fetchval(
                    "SELECT content_delta_seq FROM tasks WHERE id=$1", s.claim.task_id
                )
                == 0
            )
    finally:
        proceed.set()
        await asyncio.gather(stop, *([output] if output else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_partial_commit_before_stop_remains_available_without_late_mutation(
    effects, monkeypatch
):
    s = effects
    await running(s)
    entered, proceed = asyncio.Event(), asyncio.Event()
    native = s.worker.write_partial

    async def held(*args, **kwargs):
        await native(*args, **kwargs)
        entered.set()
        await proceed.wait()

    monkeypatch.setattr(s.worker, "write_partial", held)
    output = asyncio.create_task(partial(s))
    stop = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        stop = asyncio.create_task(
            s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=deadline())
        )
        await asyncio.sleep(0.02)
        assert not stop.done() and not available(s.path, "OP_LOCK")
        proceed.set()
        assert await output is None
        assert (await stop).accepted
        with pytest.raises(HistoryError, match="historical_stop"):
            await partial(s, "late")
        async with s.db.connection() as conn:
            text = await conn.fetchval(
                "SELECT content FROM messages WHERE id=$1", s.claim.result_message_id
            )
            assert s.tasks._enc.decrypt(text) == "synthetic partial"
    finally:
        proceed.set()
        await asyncio.gather(output, *([stop] if stop else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["accept", "send", "stop"])
async def test_missing_projection_refuses_before_native_partial(effects, monkeypatch, kind):
    s = effects
    await running(s)
    if kind == "send":
        await mark(s)
    elif kind == "stop":
        await s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=deadline())
    # Owned fixture owner models an older SQL snapshot; immutable guards restored.
    s.db.sql(
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable; "
        "DELETE FROM task_history_projection WHERE seq=" + ("1" if kind == "accept" else "2") + "; "
        "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable;"
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("missing H projection reached native output")

    monkeypatch.setattr(s.worker, "write_partial", forbidden)
    with pytest.raises(
        HistoryError,
        match="projection_unavailable" if kind == "accept" else "effects_reconciliation_required",
    ):
        await partial(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "suspended", "lease"])
async def test_native_output_authority_is_not_replaced_by_history(effects, change):
    s = effects
    await running(s)
    if change == "generation":
        s.db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    elif change == "suspended":
        s.db.sql("UPDATE entitlement_accounts SET status='suspended'")
    else:
        s.db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises((ObservationBlocked, LeaseLost)):
        await partial(s)
    async with s.db.connection() as conn:
        assert (
            await conn.fetchval("SELECT content_delta_seq FROM tasks WHERE id=$1", s.claim.task_id)
            == 0
        )
    assert len(entries(s)) == 1


async def output_call(s, kind):
    if kind == "partial":
        return await partial(s)
    return await s.effects.complete(
        s.claim.task_id,
        s.claim.epoch,
        binding=s.claim,
        content="synthetic final",
        deadline=deadline(),
        message_fields={"metadata": {"synthetic_output": "kept"}},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["partial", "terminal"])
@pytest.mark.parametrize("committed", [False, True])
async def test_lost_output_commit_ack_never_returns_success_or_retries(
    effects, monkeypatch, kind, committed
):
    s = effects
    await running(s)
    native_write, native_commit = s.worker._memory.update_message, Transaction.commit
    armed, fired = False, False

    async def writing(*args, **kwargs):
        nonlocal armed
        await native_write(*args, **kwargs)
        armed = True

    async def commit(tx):
        nonlocal fired
        if armed and not fired:
            fired = True
            if committed:
                await native_commit(tx)
            else:
                await tx.rollback()
            raise asyncpg.ConnectionDoesNotExistError(
                "synthetic output COMMIT acknowledgement lost"
            )
        return await native_commit(tx)

    monkeypatch.setattr(s.worker._memory, "update_message", writing)
    monkeypatch.setattr(Transaction, "commit", commit)
    with pytest.raises(TransactionUnavailable) as caught:
        await output_call(s, kind)
    assert caught.value.commit_attempted is True and caught.value.commit_acknowledged is False
    assert isinstance(caught.value.__cause__, asyncpg.ConnectionDoesNotExistError)
    assert fired and s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    with pytest.raises(HistoryError, match="blocked"):
        await output_call(s, kind)
    async with s.db.connection() as conn:
        row = await conn.fetchrow(
            "SELECT t.status,t.content_delta_seq,m.content FROM tasks t JOIN messages m ON m.id=t.result_message_id WHERE t.id=$1",
            s.claim.task_id,
        )
        assert row["content_delta_seq"] == (3 if committed and kind == "partial" else 0)
        assert row["status"] == ("completed" if committed and kind == "terminal" else "running")
        assert s.tasks._enc.decrypt(row["content"]) == (
            ("synthetic partial" if kind == "partial" else "synthetic final") if committed else ""
        )
    assert len(entries(s)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["partial", "terminal"])
@pytest.mark.parametrize("target", [1, 2])
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_actual_output_holder_reset_retains_history_until_settled(
    effects, monkeypatch, kind, target, mode
):
    s = effects
    await running(s)
    async with held_reset(s.worker_pool, monkeypatch, target, mode == "failure") as (
        entered,
        proceed,
    ):
        task = asyncio.create_task(output_call(s, kind))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original output cancellation")
                await asyncio.sleep(0.02)
                task.cancel("repeated output cancellation")
            elif mode == "deadline":
                await asyncio.sleep(2.1)  # Existing total operation budget, unchanged.
                task.cancel("repeated after deadline")
            await asyncio.sleep(0.02)
            assert not task.done() and not available(s.path, "OP_LOCK")
            proceed.set()
            expected = (
                asyncio.CancelledError
                if mode == "cancel"
                else (
                    TimeoutError
                    if mode == "deadline"
                    else (TransactionUnavailable if target == 2 else RuntimeError)
                )
            )
            with pytest.raises(expected) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original output cancellation",)
            elif mode == "failure" and target == 2:
                assert isinstance(caught.value, TransactionUnavailable)
                assert (
                    caught.value.commit_attempted is True
                    and caught.value.commit_acknowledged is True
                )
                assert isinstance(caught.value.__cause__, RuntimeError)
            assert available(s.path, "OP_LOCK") and s.effects._blocked and s.history._blocked
            with pytest.raises(HistoryError, match="blocked"):
                await output_call(s, kind)
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["partial", "terminal"])
async def test_output_history_release_failure_is_not_success(effects, monkeypatch, kind):
    s = effects
    await running(s)
    native = s.history._exit

    def lost(original):
        native(original)
        raise RuntimeError("synthetic output H release acknowledgement lost")

    monkeypatch.setattr(s.history, "_exit", lost)
    with pytest.raises(RuntimeError, match="output H release acknowledgement lost"):
        await output_call(s, kind)
    assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    with pytest.raises(HistoryError, match="blocked"):
        await output_call(s, kind)
