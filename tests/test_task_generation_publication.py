"""Approved #477 generation/reinstatement contract, real SQL + scripted provider.

Fault wrappers below replace driver response delivery, not the SQL transaction.
Requires disposable TASKS_TEST_DATABASE_URL; no production settings or inference.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from typing import Any, cast

import pytest

from orchestrator.entitlements.service import EntitlementService
from orchestrator.services.account_control.transaction import (
    ObservationBlocked,
    TransactionUnavailable,
)
from orchestrator.tasks import runner
from orchestrator.tasks.states import RetryCause, TaskStatus
from orchestrator.tasks.store import Claim, TaskControlled, TaskStore
from tests.durable_tasks_support import (
    LEASE_S,
    Env,
    FakeRedis,
    accept_task,
    durable_env_fixture,
    expire_lease,
)
from tests import test_task_runner_postgres as worker_tests
from tests.test_task_runner_postgres import MOCK_TEXT, _ctx

env = durable_env_fixture(initialized=True)
mock_llm = worker_tests.mock_llm
pytestmark = pytest.mark.asyncio


async def claimed(env: Env, *, execute: bool = False) -> Claim:
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id=runner.WORKER_ID, lease_s=LEASE_S)
    assert isinstance(claim, Claim)
    if execute:
        await env.tasks.begin_execution(claim.task_id, claim.epoch, uuid.uuid4(), binding=claim)
    return claim


async def raw(env: Env, claim: Claim) -> tuple[Any, Any, Any, Any]:
    return (
        dict(await env.pool.fetchrow("SELECT * FROM tasks WHERE id=$1", claim.task_id)),
        dict(
            await env.pool.fetchrow("SELECT * FROM messages WHERE id=$1", claim.result_message_id)
        ),
        [
            dict(r)
            for r in await env.pool.fetch(
                "SELECT * FROM task_attempts WHERE task_id=$1 ORDER BY epoch", claim.task_id
            )
        ],
        [
            dict(r)
            for r in await env.pool.fetch(
                "SELECT * FROM task_operations WHERE task_id=$1 ORDER BY id", claim.task_id
            )
        ],
    )


async def revoke(env: Env) -> None:
    await env.pool.execute(
        "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
    )
    assert await EntitlementService(env.pool).reinstate(
        env.alice, reason="synthetic quote reconciliation", expected_generation=0
    )


@pytest.mark.parametrize("fault", ["current_attempt", "metadata", "ciphertext", "material_read"])
async def test_unavailable_required_facts_never_repair_or_terminalize(env: Env, monkeypatch, fault):
    claim = await claimed(env)
    if fault == "current_attempt":
        await env.pool.execute("DELETE FROM task_attempts WHERE task_id=$1", claim.task_id)
    elif fault == "metadata":
        await env.pool.execute(
            "UPDATE messages SET metadata='[]'::jsonb WHERE id=$1", claim.result_message_id
        )
    elif fault == "ciphertext":
        await env.pool.execute(
            "UPDATE tasks SET input_ciphertext='invalid-synthetic-ciphertext' WHERE id=$1",
            claim.task_id,
        )
    else:

        async def unreadable(*args, **kwargs):
            raise OSError("synthetic material observation unavailable")

        monkeypatch.setattr(env.tasks, "_material_possible", unreadable)
    await revoke(env)
    before = await raw(env, claim)
    with pytest.raises(ObservationBlocked):
        await env.tasks.complete(claim.task_id, claim.epoch, content="never copy", binding=claim)
    assert await raw(env, claim) == before


async def test_new_generation_live_foreign_attempt_blocks_without_settlement(env: Env, monkeypatch):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="other-worker", lease_s=LEASE_S)
    assert isinstance(claim, Claim)
    await revoke(env)
    before = await raw(env, claim)

    async def forbidden(*args, **kwargs):
        raise AssertionError("revocation observation grants no recovery/settlement")

    monkeypatch.setattr(runner, "settle_lost_attempt_holds", forbidden)
    result = await runner.run_chat_task({"task_store": env.tasks}, str(claim.task_id))
    assert result == "task_generation_stale"
    assert await raw(env, claim) == before


async def test_revoked_controller_preserves_original_financial_hold(env: Env, monkeypatch):
    claim = await claimed(env)
    hold = await EntitlementService(env.pool).reserve(
        env.alice, 10, operation="chat", scope_id=uuid.uuid4()
    )
    financial = await env.pool.fetchrow(
        "SELECT * FROM entitlement_reservations WHERE id=$1", hold.id
    )
    await revoke(env)
    await expire_lease(env, claim.task_id)

    async def forbidden(*args, **kwargs):
        raise AssertionError("controller refusal cannot settle even an existing conservative hold")

    monkeypatch.setattr(runner, "settle_lost_attempt_holds", forbidden)
    assert await runner.run_chat_task({"task_store": env.tasks}, str(claim.task_id)) == "failed"
    assert (
        await env.pool.fetchrow("SELECT * FROM entitlement_reservations WHERE id=$1", hold.id)
        == financial
    )


@pytest.mark.parametrize("generation", [0, 7])
async def test_actual_worker_sink_sql_and_authoritative_snapshot(env: Env, mock_llm, generation):
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=$2 WHERE user_id=$1",
        env.alice,
        generation,
    )
    accepted = await accept_task(env)
    redis = FakeRedis()
    assert await runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)) == "completed"
    snap = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snap is not None and snap.status is TaskStatus.COMPLETED and snap.content == MOCK_TEXT
    assert (
        await runner.run_chat_task(_ctx(env, redis), str(accepted.task_id))
        == "task_already_terminal"
    )
    saved = await env.pool.fetchval(
        "SELECT input_ciphertext FROM tasks WHERE id=$1", accepted.task_id
    )
    assert env.tasks._open(saved)["origin_account_generation"] == generation


async def test_own_completion_receipt_is_not_replaced_by_failed_observation(
    env: Env, mock_llm, monkeypatch
):
    """#518 receipt precedence: read failure occurs AFTER real SQL ACK, before return."""
    race = worker_tests._HeldCompletion(env, monkeypatch)
    accepted = await accept_task(env)
    failed_read = asyncio.Event()
    real_observe = env.tasks.observe_attempt
    real_resolve = runner._resolve
    redis = FakeRedis()

    async def resolve(store, state):
        assert state.result is TaskStatus.COMPLETED
        assert state.blocked == "task_observation_indeterminate"
        assert not state.completing and state.deferred_stop is None
        with pytest.raises(ObservationBlocked):
            async with runner._mutation(state):
                pytest.fail("own receipt must not reopen mutation admission")
        return await real_resolve(store, state)

    async def observe(binding):
        if race.committed:
            failed_read.set()
            raise OSError("synthetic read-only observation failure after own SQL commit")
        return await real_observe(binding)

    async def after():
        await asyncio.wait_for(failed_read.wait(), 5)

    monkeypatch.setattr(env.tasks, "observe_attempt", observe)
    monkeypatch.setattr(runner, "_resolve", resolve)
    race.after = after
    result = await asyncio.wait_for(
        runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)), 10
    )
    assert failed_read.is_set() and race.committed == [TaskStatus.COMPLETED]
    assert race.calls == ["completed"]  # no replacement content/control transaction
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None and snapshot.status is TaskStatus.COMPLETED
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM task_events WHERE task_id=$1 AND kind='finished'",
            accepted.task_id,
        )
        == 1
    )
    assert result == "completed"
    assert redis.enqueued == []  # no success-only extraction/evaluation jobs
    assert sum(payload.get("t") == "terminal" for _, payload in redis.published) == 1


@pytest.mark.parametrize("executed", [False, True])
async def test_real_suspension_crash_reinstatement_sweep_and_duplicate(
    env: Env, monkeypatch, executed
):
    from tests.redis_jobs_support import install_fake_enqueue

    install_fake_enqueue(monkeypatch, "orchestrator.tasks.runner.enqueue_account_job")
    claim = await claimed(env, execute=executed)
    await env.tasks.write_partial(
        claim.task_id, claim.epoch, content="committed partial", delta_seq=1, binding=claim
    )
    service = EntitlementService(env.pool)
    hold = await service.reserve(env.alice, 1, operation="chat", scope_id=uuid.uuid4())
    await service.settle(hold, 2)  # actual overrun suspension, no generation bump
    assert (
        await env.tasks.heartbeat(claim.task_id, claim.epoch, lease_s=LEASE_S, binding=claim)
    ).account_suspended
    before = await raw(env, claim)
    # Synthetic worker death boundary: no finish_refusal call; expire the lease.
    await expire_lease(env, claim.task_id)
    assert await service.reinstate(env.alice, reason="quote corrected", expected_generation=0)
    redis = FakeRedis()
    assert await runner.sweep_tasks(_ctx(env, redis)) == 1
    assert len(redis.enqueued) == 1
    expected = "needs_attention" if executed else "failed"
    assert await runner.run_chat_task(_ctx(env, redis), str(claim.task_id)) == expected
    assert (
        await runner.run_chat_task(_ctx(env, redis), str(claim.task_id)) == "task_already_terminal"
    )
    after = await raw(env, claim)
    assert after[0]["lease_epoch"] == 1 and after[0]["attempt_count"] == int(executed)
    assert after[1]["content"] == before[1]["content"]
    assert after[2][0]["partial_ciphertext"] == before[2][0]["partial_ciphertext"]
    assert after[3] == before[3]
    assert redis.published == []
    assert (
        await env.pool.fetchval("SELECT status FROM entitlement_reservations WHERE id=$1", hold.id)
        == "settled"
    )


@pytest.mark.parametrize(
    "method",
    [
        "write_partial",
        "complete",
        "fail_attempt",
        "defer_claim",
        "defer_execution",
        "record_compute_scope",
        "begin_execution",
        "begin_operation",
        "record_event",
    ],
)
async def test_every_worker_write_refuses_before_content_epoch_or_archive(env: Env, method):
    claim = await claimed(env, execute=True)
    await env.tasks.write_partial(
        claim.task_id, claim.epoch, content="previous evidence", delta_seq=2, binding=claim
    )
    before = await raw(env, claim)
    await revoke(env)
    kwargs = {
        "write_partial": {"content": "stale", "delta_seq": 99},
        "complete": {"content": "stale", "message_fields": {"model": "unpublished"}},
        "fail_attempt": {"cause": RetryCause.RETRYABLE_ERROR, "error_code": "internal_error"},
        "defer_claim": {"delay_s": 0, "reason": "recovery_pending"},
        "defer_execution": {"delay_s": 0, "reason": "rate_limited", "max_wait_s": 900},
        "begin_operation": {
            "tool_name": "notification_send",
            "target": None,
            "min_lease_margin_s": 0,
        },
    }.get(method, {})
    args = [claim.task_id, claim.epoch]
    if method in {"record_compute_scope", "begin_execution"}:
        args.append(uuid.uuid4())
    if method == "record_event":
        args.extend(["tool_call", {"name": "must_not_copy"}])
    if method == "complete":
        assert (
            await env.tasks.complete(claim.task_id, claim.epoch, binding=claim, **kwargs)
            is TaskStatus.NEEDS_ATTENTION
        )
    else:
        with pytest.raises(TaskControlled) as controlled:
            await getattr(env.tasks, method)(*args, binding=claim, **kwargs)
        assert controlled.value.status is TaskStatus.NEEDS_ATTENTION
    after = await raw(env, claim)
    assert (
        after[0]["status"] == "needs_attention" and after[0]["terminal_code"] == "uncertain_effect"
    )
    for column in (
        "lease_epoch",
        "attempt_count",
        "content_generation",
        "content_delta_seq",
        "input_ciphertext",
        "wake_seq",
        "next_wakeup_at",
    ):
        assert after[0][column] == before[0][column]
    for column in ("content", "reasoning_text", "tool_calls", "tool_results", "model"):
        assert after[1][column] == before[1][column]
    assert after[2][0]["partial_ciphertext"] == before[2][0]["partial_ciphertext"]
    assert after[3] == before[3]
    events = await env.tasks.events_since(
        env.alice, claim.task_id, after_seq=before[0]["event_seq"]
    )
    assert [(e.kind, e.payload) for e in events] == [
        ("finished", {"status": "needs_attention", "code": "uncertain_effect"})
    ]
    history = await env.memory.get_recent_messages(
        claim.conversation_id, exclude_status=["streaming", "error", "cancelled"]
    )
    assert history[-1]["content"] == (
        "This request stopped; available evidence cannot rule out an external action. "
        "Do not repeat it automatically; inspect the recorded task operations first."
    )


@pytest.mark.parametrize(
    "payload,code",
    [
        ({}, "task_origin_missing"),
        (
            {
                "summary_origin_account_id": str(uuid.uuid4()),
                "summary_origin_account_generation": 0,
            },
            "task_origin_missing",
        ),
        ({"origin_account_id": "bad", "origin_account_generation": 0}, "task_origin_invalid"),
        (
            {"origin_account_id": str(uuid.uuid4()), "origin_account_generation": 0},
            "task_origin_foreign",
        ),
        ([], "task_origin_invalid"),
    ],
)
async def test_authenticated_legacy_never_claims_or_terminalizes(env: Env, payload, code):
    accepted = await accept_task(env)
    await env.pool.execute(
        "UPDATE tasks SET input_ciphertext=$2 WHERE id=$1",
        accepted.task_id,
        env.tasks._enc.encrypt(json.dumps(payload)),
    )
    before = await env.pool.fetchrow("SELECT * FROM tasks WHERE id=$1", accepted.task_id)
    with pytest.raises(ObservationBlocked) as error:
        await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert error.value.code == code
    assert await env.pool.fetchrow("SELECT * FROM tasks WHERE id=$1", accepted.task_id) == before
    assert await env.pool.fetchval("SELECT count(*) FROM task_attempts") == 0
    assert (
        await env.tasks.request_cancel(env.alice, accepted.task_id)
    ).status is TaskStatus.CANCELLED


async def test_lower_generation_and_foreign_worker_are_not_control_authority(env: Env):
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=2 WHERE user_id=$1", env.alice
    )
    claim = await claimed(env)
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1", env.alice
    )
    before = await raw(env, claim)
    with pytest.raises(ObservationBlocked):
        await env.tasks.complete(claim.task_id, claim.epoch, content="no", binding=claim)
    assert await raw(env, claim) == before
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=2 WHERE user_id=$1", env.alice
    )
    with pytest.raises(ObservationBlocked):
        await env.tasks.complete(
            claim.task_id, claim.epoch, content="no", binding=replace(claim, worker_id="foreign")
        )
    assert await raw(env, claim) == before


@pytest.mark.parametrize("outcome", ["started", "succeeded", "failed", "unknown"])
async def test_all_persisted_material_outcomes_remain_possible(env: Env, outcome):
    claim = await claimed(env)
    operation = await env.tasks.begin_operation(
        claim.task_id,
        claim.epoch,
        tool_name="notification_send",
        target={"topic": "fictional"},
        min_lease_margin_s=0,
        binding=claim,
    )
    if outcome != "started":
        await env.tasks.finish_operation(operation, outcome=outcome)
    before = await raw(env, claim)
    await revoke(env)
    assert (
        await env.tasks.complete(claim.task_id, claim.epoch, content="no", binding=claim)
        is TaskStatus.NEEDS_ATTENTION
    )
    assert (await raw(env, claim))[3] == before[3]


async def test_reinstatement_cas_duplicate_concurrency_stale_and_max(env: Env):
    service = EntitlementService(env.pool)
    await env.pool.execute(
        "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
    )
    outcomes = await asyncio.gather(
        *(service.reinstate(env.alice, reason="fixture", expected_generation=0) for _ in range(2))
    )
    assert sorted(outcomes) == [False, True]
    row = await env.pool.fetchrow("SELECT * FROM entitlement_accounts WHERE user_id=$1", env.alice)
    assert row["content_generation"] == 1
    assert not await service.reinstate(env.alice, reason="duplicate", expected_generation=0)
    assert (
        await env.pool.fetchrow("SELECT * FROM entitlement_accounts WHERE user_id=$1", env.alice)
        == row
    )
    await env.pool.execute(
        "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
    )
    with pytest.raises(ValueError, match="account_generation_mismatch"):
        await service.reinstate(env.alice, reason="old command", expected_generation=0)
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=$2 WHERE user_id=$1",
        env.alice,
        2**63 - 1,
    )
    with pytest.raises(ValueError, match="account_generation_exhausted"):
        await service.reinstate(env.alice, reason="no wrap", expected_generation=2**63 - 1)
    with pytest.raises(ValueError, match="invalid expected"):
        await service.reinstate(env.alice, reason="boolean", expected_generation=True)


async def test_old_idempotent_origin_unchanged_new_origin_runs(env: Env, mock_llm):
    from tests.test_task_store_postgres import _accept

    accepted = await _accept(env, key="original")
    sealed = await env.pool.fetchval(
        "SELECT input_ciphertext FROM tasks WHERE id=$1", accepted.task_id
    )
    await revoke(env)
    with pytest.raises(TaskControlled) as controlled:
        await env.tasks.claim(accepted.task_id, worker_id="new", lease_s=LEASE_S)
    assert controlled.value.status is TaskStatus.FAILED
    replay = await _accept(env, key="original")
    assert replay.task_id == accepted.task_id and not replay.created
    assert (
        await env.pool.fetchval("SELECT input_ciphertext FROM tasks WHERE id=$1", accepted.task_id)
        == sealed
    )
    fresh = await _accept(env, key="fresh")
    redis = FakeRedis()
    assert await runner.run_chat_task(_ctx(env, redis), str(fresh.task_id)) == "completed"
    assert (
        env.tasks._open(
            await env.pool.fetchval("SELECT input_ciphertext FROM tasks WHERE id=$1", fresh.task_id)
        )["origin_account_generation"]
        == 1
    )


@pytest.mark.parametrize("first", ["writer", "reinstater"])
async def test_both_real_account_lock_orderings(env: Env, monkeypatch, first):
    claim = await claimed(env, execute=True)
    await env.pool.execute(
        "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
    )
    service = EntitlementService(env.pool)
    entered, release = asyncio.Event(), asyncio.Event()
    first_pid = None
    if first == "writer":
        real = env.tasks._control_end

        async def paused(conn, *args, **kwargs):
            nonlocal first_pid
            await real(conn, *args, **kwargs)
            first_pid = conn.get_server_pid()
            entered.set()
            await release.wait()

        monkeypatch.setattr(env.tasks, "_control_end", paused)
    else:
        real = service._store.reinstate_account

        async def paused(conn, *args, **kwargs):
            nonlocal first_pid
            await real(conn, *args, **kwargs)
            first_pid = conn.get_server_pid()
            entered.set()
            await release.wait()

        monkeypatch.setattr(service._store, "reinstate_account", paused)

    async def write():
        return await env.tasks.complete(
            claim.task_id, claim.epoch, content="must not publish", binding=claim
        )

    async def reinstate():
        return await service.reinstate(env.alice, reason="fixture", expected_generation=0)

    one = asyncio.create_task(write() if first == "writer" else reinstate())
    two = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        two = asyncio.create_task(reinstate() if first == "writer" else write())
        async with asyncio.timeout(3):
            while not await env.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1::int=ANY(pg_blocking_pids(pid)))",
                first_pid,
            ):
                await asyncio.sleep(0.01)
        assert not two.done()  # an observed PostgreSQL wait, not task scheduling alone
        release.set()
        outcomes = await asyncio.gather(one, two)
        expected = TaskStatus.FAILED if first == "writer" else TaskStatus.NEEDS_ATTENTION
        assert outcomes == ([expected, True] if first == "writer" else [True, expected])
        snap = await env.tasks.snapshot(env.alice, claim.task_id)
        assert snap is not None and snap.status is expected and snap.content == ""
        assert (
            await env.pool.fetchval(
                "SELECT content_generation FROM entitlement_accounts WHERE user_id=$1", env.alice
            )
            == 1
        )
    finally:
        release.set()
        for task in (one, two):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (one, two) if t is not None), return_exceptions=True)


@pytest.mark.parametrize(
    "state", ["configured", "invalid_config", "bound", "cause", "missing_schema"]
)
async def test_operator_control_journal_restrictions_fail_closed(env: Env, monkeypatch, state):
    from orchestrator.config import get_settings
    from orchestrator.services.deletion_journal.errors import JournalError

    if state in {"configured", "invalid_config"}:
        monkeypatch.setenv("DAEMON_DELETION_JOURNAL_DIR", "/nonexistent-synthetic-journal")
        if state == "configured":
            monkeypatch.setenv("DAEMON_DELETION_JOURNAL_ID", str(uuid.uuid4()))
        get_settings.cache_clear()
    elif state == "bound":
        await env.pool.execute(
            "INSERT INTO deletion_journal_binding(singleton,journal_id) VALUES(true,$1)",
            uuid.uuid4(),
        )
    elif state == "cause":
        await env.pool.execute(
            "INSERT INTO account_suspension_causes(account_id,cause_id,cause_code) VALUES($1,$2,'operator')",
            env.alice,
            uuid.uuid4(),
        )
    else:
        await env.pool.execute("DROP TABLE account_suspension_causes")
    before = await env.pool.fetchrow(
        "SELECT * FROM entitlement_accounts WHERE user_id=$1", env.alice
    )
    try:
        with pytest.raises((ValueError, JournalError, TransactionUnavailable)):
            # Also refuse an active no-op under ineligible control context.
            await EntitlementService(env.pool).reinstate(
                env.alice, reason="fixture", expected_generation=0
            )
        assert (
            await env.pool.fetchrow(
                "SELECT * FROM entitlement_accounts WHERE user_id=$1", env.alice
            )
            == before
        )
    finally:
        get_settings.cache_clear()


class FaultPool:
    """Synthetic response fault around a REAL transaction; never a fake SQL pass."""

    def __init__(self, pool, phase):
        self.pool, self.phase = pool, phase
        self.commits = self.releases = self.terminates = 0

    async def acquire(self):
        return FaultConnection(self, await self.pool.acquire())

    async def release(self, conn):
        self.releases += 1
        await self.pool.release(conn.real)
        if self.phase == "release":
            raise OSError("synthetic release response loss")


class FaultConnection:
    def __init__(self, pool, real):
        self.owner, self.real = pool, real

    def __getattr__(self, name):
        return getattr(self.real, name)

    def terminate(self):
        self.owner.terminates += 1
        self.real.terminate()

    def transaction(self, **kwargs):
        real, owner = self.real.transaction(**kwargs), self.owner

        class Transaction:
            async def start(self):
                await real.start()

            async def rollback(self):
                await real.rollback()

            async def commit(self):
                owner.commits += 1
                if owner.phase == "before_commit":
                    raise OSError("synthetic pre-commit response failure")
                await real.commit()
                if owner.phase == "after_commit":
                    raise OSError("synthetic lost COMMIT acknowledgement")

        return Transaction()


@pytest.mark.parametrize("phase", ["before_commit", "after_commit", "release"])
@pytest.mark.parametrize("kind", ["content", "control", "reinstatement"])
async def test_real_commit_with_synthetic_response_loss_no_second_transition(env: Env, phase, kind):
    claim = await claimed(env)
    fault = FaultPool(env.pool, phase)
    store = TaskStore(fault, env.tasks._enc, env.memory)  # type: ignore[arg-type]
    if kind == "reinstatement":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )
        call = EntitlementService(cast(Any, fault)).reinstate(
            env.alice, reason="fixture", expected_generation=0
        )  # type: ignore[arg-type]
    elif kind == "control":
        await env.tasks.request_cancel(env.alice, claim.task_id)
        call = store.finish_refusal(claim.task_id, claim.epoch, binding=claim, reason="cancelled")
    else:
        call = store.complete(
            claim.task_id, claim.epoch, content="acknowledgement lost", binding=claim
        )
    with pytest.raises(TransactionUnavailable) as error:
        await call
    assert error.value.code == (
        "task_committed_result_unavailable"
        if phase == "release"
        else "task_transition_indeterminate"
    )
    assert fault.commits == 1
    if phase == "release":
        assert fault.terminates == 0 and fault.releases == 1
    if kind == "reinstatement":
        row = await env.pool.fetchrow(
            "SELECT status,content_generation FROM entitlement_accounts WHERE user_id=$1", env.alice
        )
        assert tuple(row) == (("suspended", 0) if phase == "before_commit" else ("active", 1))
    else:
        row = await env.pool.fetchrow("SELECT status FROM tasks WHERE id=$1", claim.task_id)
        assert row["status"] == (
            "running"
            if phase == "before_commit"
            else "cancelled"
            if kind == "control"
            else "completed"
        )
        assert await env.pool.fetchval(
            "SELECT count(*) FROM task_events WHERE task_id=$1 AND kind='finished'", claim.task_id
        ) == int(phase != "before_commit")


@pytest.mark.parametrize("phase", ["before_commit", "after_commit", "release"])
async def test_actual_worker_unknown_final_commit_never_resolves_or_notifies_again(
    env: Env, mock_llm, monkeypatch, phase
):
    accepted = await accept_task(env)
    redis = FakeRedis()
    fault = FaultPool(env.pool, phase)
    real = env.tasks._complete

    async def forbidden(*args, **kwargs):
        raise AssertionError("unknown commit cannot authorize another transition or settlement")

    async def armed(*args, **kwargs):
        # Synthetic response fault starts only at the ACTUAL final transaction,
        # after the actual worker/engine/provider/partial persistence ran.
        env.tasks._pool = fault  # type: ignore[assignment]
        for method in (
            "fail_attempt",
            "defer_claim",
            "defer_execution",
            "acknowledge_cancel",
            "finish_refusal",
        ):
            monkeypatch.setattr(env.tasks, method, forbidden)
        monkeypatch.setattr(runner, "settle_lost_attempt_holds", forbidden)
        return await real(*args, **kwargs)

    monkeypatch.setattr(env.tasks, "_complete", armed)
    result = await runner.run_chat_task(_ctx(env, redis), str(accepted.task_id))
    assert result == (
        "task_committed_result_unavailable"
        if phase == "release"
        else "task_transition_indeterminate"
    )
    assert fault.commits == 1
    assert not any(m[1]["t"] == "terminal" for m in redis.published)
    # Use the unwrapped authoritative pool, not a synthetic observer response.
    assert await env.pool.fetchval("SELECT status FROM tasks WHERE id=$1", accepted.task_id) == (
        "running" if phase == "before_commit" else "completed"
    )
    assert await env.pool.fetchval(
        "SELECT count(*) FROM task_events WHERE task_id=$1 AND kind='finished'", accepted.task_id
    ) == int(phase != "before_commit")
