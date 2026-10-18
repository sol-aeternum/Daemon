"""Funded terminal-quarantine retry; actual synthetic SQL, never provider work."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from typing import Any, cast
from uuid import uuid4

import pytest
import asyncpg

from orchestrator.entitlements.errors import LimitExceeded
from orchestrator.entitlements.service import EntitlementService
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.transaction import (
    ObservationBlocked,
    TransactionUnavailable,
)
from orchestrator.tasks import runner
from orchestrator.tasks._invocation import InferenceInvocation, invocation
from orchestrator.tasks.inference import CODE, InferenceRecovery
from orchestrator.tasks.store import TaskStore, TaskQuarantined
from tests.test_inference_recovery_postgres import (
    db as _db,
    metadata_cluster as _metadata_cluster,
    seed,
)

# Reuse the owned separated-principal fixture and its ordinary teardown unchanged.
db = _db
metadata_cluster = _metadata_cluster


@pytest.mark.asyncio
async def test_funded_quarantine_hold_retried_by_existing_sweep_after_restart(db, monkeypatch):
    execute = AsyncMock(side_effect=AssertionError("terminal accounting must never execute"))
    monkeypatch.setattr(runner, "_execute", execute)
    async with db.connection() as owner:
        async with db.pool() as pool:
            tasks, recovery, claim, identity = await seed(db, owner, pool)
            service = EntitlementService(pool)
            hold = await service.reserve_for_attempt(
                identity, 100, operation="chat", provider="synthetic", model="synthetic"
            )
            await recovery.mark(identity, hold.id, InferenceInvocation())
            assert (
                await owner.fetchval(
                    "SELECT reserved_microusd FROM entitlement_usage_periods "
                    "WHERE user_id=$1 AND period_key=$2",
                    identity.account_id,
                    hold.period_key,
                )
                == 100
            )
            with pytest.raises(LimitExceeded) as blocked:
                await service.reserve(identity.account_id, 1, operation="chat", scope_id=uuid4())
            assert blocked.value.code == "concurrency_exceeded"
            await owner.execute(
                "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE id=$1",
                claim.task_id,
            )
            failure = ConnectionError("one settlement failure after terminal COMMIT")
            failed = []

            async def fail_after_terminal(self, reservation, amount, **kwargs):
                task = await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", claim.task_id)
                assert (task["status"], task["terminal_code"]) == ("needs_attention", CODE)
                assert task["finished_at"] is not None and task["lease_owner"] is None
                assert reservation == hold.id and amount == hold.reserved_microusd
                failed.append(reservation)
                raise failure

            with monkeypatch.context() as fault:
                fault.setattr(EntitlementService, "settle", fail_after_terminal)
                with pytest.raises(ConnectionError) as caught:
                    await runner.run_chat_task(
                        {"task_store": tasks, "db_pool": pool}, str(claim.task_id)
                    )
                assert caught.value is failure
            assert failed == [hold.id]
            assert (
                await owner.fetchval(
                    "SELECT status FROM entitlement_reservations WHERE id=$1", hold.id
                )
                == "open"
            )
            original_task = dict(
                await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", claim.task_id)
            )
            original_attempt = dict(
                await owner.fetchrow(
                    "SELECT * FROM task_attempts WHERE task_id=$1 AND epoch=$2",
                    claim.task_id,
                    claim.epoch,
                )
            )
            original_message = dict(
                await owner.fetchrow("SELECT * FROM messages WHERE id=$1", claim.result_message_id)
            )
            encryption = tasks._enc

        # Restart the owners, not the task or reservation. No old ContextVar/latch
        # is cleared to authorize an execution continuation.
        async with db.pool() as restarted_pool:
            restarted = TaskStore(
                restarted_pool,
                encryption,
                MemoryStore(restarted_pool, encryption),
                inference_recovery=InferenceRecovery(restarted_pool),
            )
            redis = AsyncMock()
            context = {"task_store": restarted, "db_pool": restarted_pool, "redis": redis}
            assert await runner.sweep_tasks(context) == 0
            assert (
                await runner.run_chat_task(context, str(claim.task_id)) == "task_already_terminal"
            )
            assert await restarted.due_for_wakeup() == []
            assert (
                await EntitlementService(restarted_pool).receipt_candidates(
                    settled_after=datetime.now(timezone.utc) - timedelta(hours=25), limit=50
                )
                == []
            )
            execute.assert_not_awaited()
            redis.enqueue_job.assert_not_awaited()
            assert (
                dict(await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", claim.task_id))
                == original_task
            )
            assert (
                dict(
                    await owner.fetchrow(
                        "SELECT * FROM task_attempts WHERE task_id=$1 AND epoch=$2",
                        claim.task_id,
                        claim.epoch,
                    )
                )
                == original_attempt
            )
            assert (
                dict(
                    await owner.fetchrow(
                        "SELECT * FROM messages WHERE id=$1", claim.result_message_id
                    )
                )
                == original_message
            )
            final = await owner.fetchrow(
                "SELECT * FROM entitlement_reservations WHERE id=$1", hold.id
            )
            assert final["status"] == "settled", (
                "funded original hold stranded after failed terminal settlement and restart; "
                "existing sweep/redelivery/receipt selection provided no durable retry"
            )
            assert final["actual_microusd"] == final["reserved_microusd"] == 100
            assert final["user_id"] == hold.user_id and final["period_key"] == hold.period_key
            assert final["scope_id"] == identity.financial_scope_id and final["operation"] == "chat"
            period = await owner.fetchrow(
                "SELECT * FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
                hold.user_id,
                hold.period_key,
            )
            assert period["reserved_microusd"] == 0 and period["spent_microusd"] == 100


async def funded_terminal(db, owner, pool, *, count=1, clock=None):
    tasks, recovery, claim, identity = await seed(db, owner, pool)
    service = EntitlementService(pool, clock=clock)
    holds = [
        await service.reserve_for_attempt(identity, 100, operation="chat") for _ in range(count)
    ]
    await recovery.mark(identity, holds[0].id, InferenceInvocation())
    await owner.execute(
        "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
        claim.task_id,
    )
    with pytest.raises(TaskQuarantined):
        await tasks.claim(claim.task_id, worker_id="quarantine", lease_s=45)
    return tasks, claim, identity, holds


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["before", "after"])
async def test_terminal_settlement_wire_commit_uncertainty_independent_retry(db, monkeypatch, mode):
    async with db.connection() as owner, db.pool() as pool:
        tasks, claim, identity, holds = await funded_terminal(db, owner, pool)
        original = EntitlementService.settle
        calls = []

        async def fault(self, reservation, amount, **kwargs):
            calls.append(reservation)
            assert (
                await owner.fetchval("SELECT status FROM tasks WHERE id=$1", claim.task_id)
                == "needs_attention"
            )
            db.bridge.commit_fault = mode  # ONLY the financial COMMIT, after observation ACK.
            return await original(self, reservation, amount, **kwargs)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(EntitlementService, "settle", fault)
                with pytest.raises(TimeoutError):
                    await runner.sweep_tasks({"task_store": tasks, "db_pool": pool})
                assert db.bridge.commit_seen.is_set()
        finally:
            db.bridge.commit_fault = None
        assert calls == [holds[0].id] and invocation.get() is None
        # Barrier observation, not transport completion, establishes actual money.
        async with owner.transaction():
            await owner.fetchrow(
                "SELECT * FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                identity.account_id,
            )
            actual = await owner.fetchval(
                "SELECT status FROM entitlement_reservations WHERE id=$1", holds[0].id
            )
        assert actual == ("open" if mode == "before" else "settled")
        db.cluster.record(
            "p2_financial_ack_barrier", mode=mode, status=actual, reservation=str(holds[0].id)
        )
        # Fresh owner, same durable rows: never reset an execution latch or resend.
        restarted = TaskStore(
            pool,
            tasks._enc,
            MemoryStore(pool, tasks._enc),
            inference_recovery=InferenceRecovery(pool),
        )
        for _ in range(2):
            assert await runner.sweep_tasks({"task_store": restarted, "db_pool": pool}) == 0
        period = await owner.fetchrow(
            "SELECT * FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
            identity.account_id,
            holds[0].period_key,
        )
        assert (period["spent_microusd"], period["reserved_microusd"]) == (100, 0)
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM entitlement_reservations WHERE user_id=$1",
                identity.account_id,
            )
            == 1
        )
        assert (
            await owner.fetchval("SELECT lease_epoch FROM tasks WHERE id=$1", claim.task_id)
            == identity.epoch
        )


@pytest.mark.asyncio
async def test_duplicate_concurrent_passes_preserve_original_period_and_labels(db):
    async with db.connection() as owner, db.pool() as pool:
        tasks, claim, identity, holds = await funded_terminal(
            db, owner, pool, count=2, clock=lambda: datetime(2026, 9, 30, tzinfo=timezone.utc)
        )
        assert holds[0].period_key == "2026-09"
        before = await owner.fetch(
            "SELECT * FROM entitlement_reservations WHERE user_id=$1 ORDER BY id",
            identity.account_id,
        )
        # Existing funded accounting remains legal after suspension/new execution generation.
        await owner.execute(
            "UPDATE entitlement_accounts SET status='suspended',content_generation=content_generation+1 WHERE user_id=$1",
            identity.account_id,
        )
        context = {"task_store": tasks, "db_pool": pool}
        assert await asyncio.gather(runner.sweep_tasks(context), runner.sweep_tasks(context)) == [
            0,
            0,
        ]
        assert await runner.sweep_tasks(context) == 0
        after = await owner.fetch(
            "SELECT * FROM entitlement_reservations WHERE user_id=$1 ORDER BY id",
            identity.account_id,
        )
        for old, new in zip(before, after, strict=True):
            assert new["status"] == "settled" and new["actual_microusd"] == 100
            for key in (
                "id",
                "user_id",
                "scope_id",
                "period_key",
                "operation",
                "reserved_microusd",
                "provider",
                "model",
                "route_id",
                "plan",
                "charge_kind",
            ):
                assert new[key] == old[key]
        period = await owner.fetchrow(
            "SELECT * FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
            identity.account_id,
            holds[0].period_key,
        )
        assert (period["spent_microusd"], period["reserved_microusd"]) == (200, 0)
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM task_attempts WHERE task_id=$1", claim.task_id
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "metadata",
        "wrong_metadata",
        "marker",
        "origin",
        "foreign_origin",
        "unreadable",
        "scope",
        "owner",
        "coverage",
        "other_terminal",
        "running",
        "account",
    ],
)
async def test_terminal_accounting_refuses_invalid_original_obligation(db, monkeypatch, fault):
    async with db.connection() as owner, db.pool() as pool:
        tasks, claim, identity, holds = await funded_terminal(db, owner, pool)
        if fault == "metadata":
            await owner.execute(
                "DELETE FROM compute_reservation_metadata WHERE reservation_id=$1", holds[0].id
            )
        elif fault == "wrong_metadata":
            await owner.execute(
                "UPDATE compute_reservation_metadata SET runtime_identity=$2 WHERE reservation_id=$1",
                holds[0].id,
                uuid4(),
            )
        elif fault == "marker":
            await owner.execute(
                "DELETE FROM task_inference_recovery WHERE task_id=$1", claim.task_id
            )
        elif fault in {"origin", "foreign_origin", "coverage", "unreadable"}:
            data = tasks._open(
                (await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", claim.task_id))[
                    "input_ciphertext"
                ]
            )
            if fault == "origin":
                # Summary provenance is intentionally NOT the general origin;
                # retaining it must not become a fallback for missing authority.
                data.pop("origin_account_id")
                data.pop("origin_account_generation")
            elif fault == "foreign_origin":
                data["origin_account_id"] = str(uuid4())
            elif fault == "coverage":
                data.pop("_inference_recovery_version")
            ciphertext = "unreadable" if fault == "unreadable" else tasks._seal(data)
            await owner.execute(
                "UPDATE tasks SET input_ciphertext=$2 WHERE id=$1", claim.task_id, ciphertext
            )
        elif fault == "scope":
            await owner.execute(
                "UPDATE task_attempts SET compute_scope_id=$2 WHERE task_id=$1",
                claim.task_id,
                uuid4(),
            )
        elif fault == "owner":
            other = await db.account(owner)
            await owner.execute(
                "UPDATE entitlement_reservations SET user_id=$2 WHERE id=$1", holds[0].id, other
            )
        elif fault == "other_terminal":
            await owner.execute("UPDATE tasks SET terminal_code='other' WHERE id=$1", claim.task_id)
        elif fault == "running":
            await owner.execute(
                "UPDATE tasks SET status='running',finished_at=NULL,lease_owner='fixture',lease_expires_at=clock_timestamp()+interval '1 minute' WHERE id=$1",
                claim.task_id,
            )
        elif fault == "account":
            await owner.execute(
                "DELETE FROM entitlement_accounts WHERE user_id=$1", identity.account_id
            )
        before = await db.snapshot(owner, identity.account_id)
        called = AsyncMock(side_effect=AssertionError("invalid obligation settled"))
        monkeypatch.setattr(EntitlementService, "settle", called)
        try:
            await runner.sweep_tasks({"task_store": tasks, "db_pool": pool})
        except (ObservationBlocked, ValueError):
            pass
        called.assert_not_awaited()
        assert await db.snapshot(owner, identity.account_id) == before
        assert invocation.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_native_financial_release_failure_never_reclaims_transferred_owner(
    db, monkeypatch, cancel
):
    async with db.connection() as owner, db.pool() as pool:
        tasks, _, identity, holds = await funded_terminal(db, owner, pool, count=2)
        release = asyncpg.Pool.release
        settle = EntitlementService.settle
        terminate = cast(Any, asyncpg.pool.PoolConnectionProxy).terminate
        armed = False
        terminations = []
        failure = (
            asyncio.CancelledError("after native release")
            if cancel
            else RuntimeError("after native release")
        )
        states = []

        async def financial(self, *args, **kwargs):
            nonlocal armed
            armed = True
            states.append(invocation.get())
            return await settle(self, *args, **kwargs)

        async def fail_release(releasing_pool, conn, **kwargs):
            nonlocal armed
            await release(releasing_pool, conn, **kwargs)
            if releasing_pool is pool and armed:
                armed = False
                raise failure

        def track_terminate(conn):
            terminations.append(conn)
            return terminate(conn)

        with monkeypatch.context() as patch:
            patch.setattr(EntitlementService, "settle", financial)
            patch.setattr(asyncpg.Pool, "release", fail_release)
            patch.setattr(asyncpg.pool.PoolConnectionProxy, "terminate", track_terminate)
            with pytest.raises(
                asyncio.CancelledError if cancel else TransactionUnavailable
            ) as caught:
                await runner.sweep_tasks({"task_store": tasks, "db_pool": pool})
            if cancel:
                assert caught.value is failure
            else:
                assert isinstance(caught.value, TransactionUnavailable)
                assert caught.value.__cause__ is failure and caught.value.commit_acknowledged
            assert not terminations
            async with pool.acquire() as lease:
                assert await lease.fetchval("SELECT 1") == 1
        assert len(states) == 1 and states[0].uncertainty is not None
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM entitlement_reservations WHERE user_id=$1 AND status='settled'",
                identity.account_id,
            )
            == 1
        )
        assert invocation.get() is None
        await runner.sweep_tasks({"task_store": tasks, "db_pool": pool})
        assert (
            await owner.fetchval(
                "SELECT spent_microusd FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
                identity.account_id,
                holds[0].period_key,
            )
            == 200
        )


@pytest.mark.asyncio
async def test_accounting_page_cap_wrap_and_partial_failure_fairness(db, monkeypatch):
    async with db.connection() as owner, db.pool() as pool:
        tasks, _, identity, holds = await funded_terminal(db, owner, pool, count=52)
        ordered = sorted(hold.id for hold in holds)
        original = EntitlementService.settle
        visited = []

        async def fail_first(self, reservation, amount, **kwargs):
            visited.append(reservation)
            if reservation == ordered[0]:
                raise ConnectionError("persistent first row refusal")
            return await original(self, reservation, amount, **kwargs)

        monkeypatch.setattr(EntitlementService, "settle", fail_first)
        context = {"task_store": tasks, "db_pool": pool}
        with pytest.raises(ConnectionError):
            await runner.sweep_tasks(context)
        assert visited == [ordered[0]]
        assert tasks._quarantine_hold_after is not None
        assert tasks._quarantine_hold_after[1] == ordered[0]
        await runner.sweep_tasks(context)
        assert visited[1:] == ordered[1:51]  # exactly50, no retry of failing first row
        with pytest.raises(ConnectionError):
            await runner.sweep_tasks(context)
        assert visited[-2:] == [ordered[51], ordered[0]]  # wrap; earlier success retained
        open_ids = await owner.fetch(
            "SELECT id FROM entitlement_reservations WHERE user_id=$1 AND status='open'",
            identity.account_id,
        )
        assert [row["id"] for row in open_ids] == [ordered[0]]
        assert (
            await owner.fetchval(
                "SELECT spent_microusd FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
                identity.account_id,
                holds[0].period_key,
            )
            == 5100
        )


@pytest.mark.asyncio
async def test_bounded_sql_settings_and_real_financial_lock_timeout(db, monkeypatch):
    from orchestrator.entitlements.store import EntitlementStore

    async with db.connection() as owner, db.pool() as pool:
        tasks, _, identity, holds = await funded_terminal(db, owner, pool)
        settings = []
        original_lock = EntitlementStore.lock_account
        original_observe = InferenceRecovery.lock

        async def observe(self, conn, *args):
            settings.append(
                (
                    "observe",
                    await conn.fetchval("SHOW lock_timeout"),
                    await conn.fetchval("SHOW statement_timeout"),
                )
            )
            return await original_observe(self, conn, *args)

        async def financial(self, conn, user_id):
            settings.append(
                (
                    "money",
                    await conn.fetchval("SHOW lock_timeout"),
                    await conn.fetchval("SHOW statement_timeout"),
                )
            )
            return await original_lock(self, conn, user_id)

        monkeypatch.setattr(InferenceRecovery, "lock", observe)
        monkeypatch.setattr(EntitlementStore, "lock_account", financial)
        await runner.sweep_tasks({"task_store": tasks, "db_pool": pool})
        assert settings == [("observe", "250ms", "1s"), ("money", "250ms", "1s")]
        async with pool.acquire() as next_lease:
            assert await next_lease.fetchval("SHOW lock_timeout") == "0"
            assert await next_lease.fetchval("SHOW statement_timeout") == "0"
        # Already-funded same-row replay still needs its account barrier. Real PG
        # lock timeout must occur inside _finish, not just outer2s cancellation.
        async with owner.transaction():
            await owner.fetchrow(
                "SELECT * FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                identity.account_id,
            )
            with pytest.raises(TransactionUnavailable) as caught:
                await EntitlementService(pool, _bounded_settlement=True).settle(
                    holds[0].id,
                    100,
                    usage={"estimated_cost": True, "recovered_after_timeout": True},
                )
            assert isinstance(caught.value.__cause__, asyncpg.LockNotAvailableError)
        assert invocation.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_financial_winner_after_observation_matching_replay_or_conflict(
    db, monkeypatch, conflict
):
    from orchestrator.entitlements.errors import SettlementConflict

    async with db.connection() as owner, db.pool() as pool:
        tasks, _, identity, holds = await funded_terminal(db, owner, pool)
        original = EntitlementService.settle
        replays = []

        async def competing_winner(self, reservation, amount, **kwargs):
            # Observation has returned and released all locks. Another actual
            # financial owner commits first through the existing money service.
            winner = await original(
                EntitlementService(pool),
                reservation,
                70 if conflict else amount,
                usage={"estimated_cost": False} if conflict else kwargs["usage"],
            )
            assert winner is not None and winner.applied
            replay = await original(self, reservation, amount, **kwargs)
            replays.append(replay)
            return replay

        monkeypatch.setattr(EntitlementService, "settle", competing_winner)
        if conflict:
            with pytest.raises(SettlementConflict):
                await runner.sweep_tasks({"task_store": tasks, "db_pool": pool})
            assert not replays
        else:
            await runner.sweep_tasks({"task_store": tasks, "db_pool": pool})
            assert len(replays) == 1 and replays[0].applied is False
        assert await owner.fetchval(
            "SELECT spent_microusd FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
            identity.account_id,
            holds[0].period_key,
        ) == (70 if conflict else 100)
        assert (
            await owner.fetchval(
                "SELECT reserved_microusd FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
                identity.account_id,
                holds[0].period_key,
            )
            == 0
        )
        assert invocation.get() is None
