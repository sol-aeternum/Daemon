"""Durable task store against a real disposable PostgreSQL schema.

Covers the acceptance, claim, fencing, retry, cancellation and tenant
isolation contracts in docs/DURABLE_REQUEST_DESIGN.md §4–§6, §9, §11 and §17.
Leases are expired with SQL, never by sleeping. No inference is involved.

Requires ``TASKS_TEST_DATABASE_URL`` pointing at a disposable database; every
test replays all migrations into its own schema and drops it afterwards. The
application ``DATABASE_URL`` is never read.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from orchestrator.tasks.states import RetryCause, TaskStatus
from orchestrator.tasks.store import (
    ConversationBusy,
    EffectRefused,
    IdempotencyConflict,
    LeaseLost,
    TaskNotFound,
)
from tests.durable_tasks_support import LEASE_S, Env, durable_env_fixture

env = durable_env_fixture()


async def _accept(env: Env, user=None, *, key=None, message="hello", conversation_id=None, h="a"):
    return await env.tasks.accept(
        user_id=user or env.alice,
        conversation_id=conversation_id,
        new_conversation_title="t",
        pipeline="cloud",
        user_message=message,
        task_input={"message": message},
        request_hash=h * 64,
        idempotency_key=key,
        assistant_model=None,
    )


async def _execute(env: Env, claim) -> int:
    """The attempt starts inference work: only now does it count."""
    return await env.tasks.begin_execution(claim.task_id, claim.epoch, uuid.uuid4())


async def _expire_lease(env: Env, task_id: uuid.UUID) -> None:
    await env.pool.execute(
        "UPDATE tasks SET lease_expires_at = now() - interval '1 second' WHERE id = $1", task_id
    )


async def _make_due(env: Env, task_id: uuid.UUID) -> None:
    await env.pool.execute(
        "UPDATE tasks SET next_wakeup_at = now() - interval '1 second' WHERE id = $1", task_id
    )


async def _task(env: Env, task_id: uuid.UUID) -> asyncpg.Record:
    row = await env.pool.fetchrow("SELECT * FROM tasks WHERE id = $1", task_id)
    assert row is not None
    return row


async def _message(env: Env, message_id: uuid.UUID) -> dict:
    row = await env.pool.fetchrow("SELECT * FROM messages WHERE id = $1", message_id)
    assert row is not None
    result = dict(row)
    result["content"] = env.tasks._enc.decrypt(result["content"])
    return result


# --------------------------------------------------------------------------- #
# Acceptance and idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_acceptance_records_turn_and_task_atomically(env: Env):
    accepted = await _accept(env, key="k1")
    assert accepted.created and accepted.status is TaskStatus.QUEUED
    row = await _task(env, accepted.task_id)
    assert row["user_id"] == env.alice and row["status"] == "queued"
    assert (await _message(env, accepted.user_message_id))["status"] == "complete"
    placeholder = await _message(env, accepted.result_message_id)
    assert placeholder["status"] == "streaming" and placeholder["content"] == ""
    # History reads exclude the placeholder, as for any streaming row.
    history = await env.memory.get_recent_messages(
        accepted.conversation_id, exclude_status=["streaming", "error", "cancelled"]
    )
    assert [m["role"] for m in history] == ["user"]
    # Both rows come from one transaction; the answer still sorts after its prompt.
    ordered = await env.pool.fetch(
        "SELECT id FROM messages WHERE conversation_id = $1 ORDER BY created_at",
        accepted.conversation_id,
    )
    assert [r["id"] for r in ordered] == [accepted.user_message_id, accepted.result_message_id]
    events = await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)
    assert [e.kind for e in events] == ["accepted"]


@pytest.mark.asyncio
async def test_failure_before_commit_leaves_nothing(env: Env):
    with pytest.raises(asyncpg.CheckViolationError):
        await env.tasks.accept(
            user_id=env.alice,
            conversation_id=None,
            new_conversation_title="t",
            pipeline="cloud",
            user_message="hello",
            task_input={},
            request_hash="not-a-hash",
            idempotency_key="k",
            assistant_model=None,
        )
    assert await env.pool.fetchval("SELECT count(*) FROM messages") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM conversations") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM tasks") == 0
    # The client's retry with the same key now creates exactly one task.
    accepted = await _accept(env, key="k")
    assert accepted.created
    assert await env.pool.fetchval("SELECT count(*) FROM tasks") == 1


@pytest.mark.asyncio
async def test_replay_with_same_key_returns_same_task(env: Env):
    first = await _accept(env, key="k1")
    replay = await _accept(env, key="k1")
    assert replay.task_id == first.task_id and not replay.created
    assert await env.pool.fetchval("SELECT count(*) FROM tasks") == 1
    assert await env.pool.fetchval("SELECT count(*) FROM messages") == 2


@pytest.mark.asyncio
async def test_same_key_different_request_conflicts(env: Env):
    await _accept(env, key="k1", h="a")
    with pytest.raises(IdempotencyConflict):
        await _accept(env, key="k1", h="b")
    assert await env.pool.fetchval("SELECT count(*) FROM tasks") == 1


@pytest.mark.asyncio
async def test_concurrent_replays_create_one_task(env: Env):
    results = await asyncio.gather(*(_accept(env, key="race") for _ in range(5)))
    assert len({r.task_id for r in results}) == 1
    assert sum(r.created for r in results) == 1
    assert await env.pool.fetchval("SELECT count(*) FROM messages") == 2


@pytest.mark.asyncio
async def test_idempotency_keys_are_scoped_per_account(env: Env):
    a = await _accept(env, env.alice, key="shared")
    b = await _accept(env, env.bob, key="shared", h="b")
    assert a.task_id != b.task_id and b.created


@pytest.mark.asyncio
async def test_second_submission_while_active_is_busy(env: Env):
    first = await _accept(env, key="k1")
    with pytest.raises(ConversationBusy) as busy:
        await _accept(env, key="k2", conversation_id=first.conversation_id, h="b")
    assert busy.value.active_task_id == first.task_id
    # The rejected submission left no messages behind.
    assert await env.pool.fetchval("SELECT count(*) FROM messages") == 2
    claim = await env.tasks.claim(first.task_id, worker_id="w1", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.complete(first.task_id, claim.epoch, content="done")
    second = await _accept(env, key="k2", conversation_id=first.conversation_id, h="b")
    assert second.created


@pytest.mark.asyncio
async def test_accepting_into_another_accounts_conversation_is_not_found(env: Env):
    alice_task = await _accept(env)
    with pytest.raises(TaskNotFound):
        await _accept(env, env.bob, conversation_id=alice_task.conversation_id, h="b")
    assert await env.pool.fetchval("SELECT count(*) FROM tasks") == 1


# --------------------------------------------------------------------------- #
# Claims, leases and fencing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_duplicate_delivery_yields_one_claim(env: Env):
    accepted = await _accept(env)
    claims = await asyncio.gather(
        *(env.tasks.claim(accepted.task_id, worker_id=f"w{i}", lease_s=LEASE_S) for i in range(6))
    )
    winners = [c for c in claims if c is not None]
    assert len(winners) == 1 and winners[0].epoch == 1
    row = await _task(env, accepted.task_id)
    # Claiming alone consumes no attempt; execution does.
    assert row["attempt_count"] == 0 and row["status"] == "running"
    assert await _execute(env, winners[0]) == 1
    assert winners[0].task_input == {"message": "hello"}
    assert winners[0].user_id == env.alice


@pytest.mark.asyncio
async def test_task_not_yet_due_is_not_claimed(env: Env):
    accepted = await _accept(env)
    await env.pool.execute(
        "UPDATE tasks SET next_wakeup_at = now() + interval '1 hour' WHERE id = $1",
        accepted.task_id,
    )
    assert await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S) is None


@pytest.mark.asyncio
async def test_live_lease_is_not_taken_over(env: Env):
    accepted = await _accept(env)
    assert await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S) is None


@pytest.mark.asyncio
async def test_lost_attempt_is_regenerated_with_partial_preserved(env: Env):
    accepted = await _accept(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    await _execute(env, first)
    await env.tasks.write_partial(accepted.task_id, first.epoch, content="half an ans", delta_seq=3)
    await _expire_lease(env, accepted.task_id)

    second = await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S)
    assert second is not None and second.epoch == 2 and second.attempt_count == 1
    assert await _execute(env, second) == 2
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    assert snapshot.content_generation == 2 and snapshot.content_delta_seq == 0
    assert snapshot.content == ""
    lost = await env.pool.fetchrow(
        "SELECT * FROM task_attempts WHERE task_id = $1 AND epoch = 1", accepted.task_id
    )
    assert lost["outcome"] == "lost" and lost["ended_at"] is not None
    assert env.tasks._enc.decrypt(lost["partial_ciphertext"]) == "half an ans"

    await env.tasks.complete(accepted.task_id, second.epoch, content="full answer")
    final = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert final is not None and final.status is TaskStatus.COMPLETED
    assert final.content == "full answer"


@pytest.mark.asyncio
async def test_stale_worker_is_fenced_out_of_every_write(env: Env):
    accepted = await _accept(env)
    stale = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert stale is not None
    await _expire_lease(env, accepted.task_id)
    fresh = await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S)
    assert fresh is not None
    await env.tasks.write_partial(accepted.task_id, fresh.epoch, content="new", delta_seq=1)

    with pytest.raises(LeaseLost):
        await env.tasks.heartbeat(accepted.task_id, stale.epoch, lease_s=LEASE_S)
    with pytest.raises(LeaseLost):
        await env.tasks.write_partial(accepted.task_id, stale.epoch, content="old", delta_seq=9)
    with pytest.raises(LeaseLost):
        await env.tasks.begin_operation(
            accepted.task_id, stale.epoch, tool_name="notify", target=None, min_lease_margin_s=1
        )
    with pytest.raises(LeaseLost):
        await env.tasks.complete(accepted.task_id, stale.epoch, content="old final")
    with pytest.raises(LeaseLost):
        await env.tasks.fail_attempt(
            accepted.task_id, stale.epoch, cause=RetryCause.RETRYABLE_ERROR, error_code="x"
        )

    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    assert snapshot.content == "new" and snapshot.content_generation == fresh.epoch
    assert await env.pool.fetchval("SELECT count(*) FROM task_operations") == 0


@pytest.mark.asyncio
async def test_expired_lease_is_fenced_before_any_takeover(env: Env):
    """Review of #466: a worker paused past its lease must not write or renew it,
    even while no other worker has claimed the task yet."""
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert claim is not None
    await _expire_lease(env, accepted.task_id)
    expired_at = (await _task(env, accepted.task_id))["lease_expires_at"]

    with pytest.raises(LeaseLost):
        await env.tasks.heartbeat(accepted.task_id, claim.epoch, lease_s=LEASE_S)
    with pytest.raises(LeaseLost):
        await env.tasks.write_partial(accepted.task_id, claim.epoch, content="late", delta_seq=1)
    with pytest.raises(LeaseLost):
        await env.tasks.begin_operation(
            accepted.task_id, claim.epoch, tool_name="notify", target=None, min_lease_margin_s=0
        )
    with pytest.raises(LeaseLost):
        await env.tasks.complete(accepted.task_id, claim.epoch, content="late final")
    with pytest.raises(LeaseLost):
        await env.tasks.fail_attempt(
            accepted.task_id, claim.epoch, cause=RetryCause.RETRYABLE_ERROR, error_code="x"
        )

    row = await _task(env, accepted.task_id)
    assert row["status"] == "running" and row["lease_expires_at"] == expired_at  # not revived
    assert await env.pool.fetchval("SELECT count(*) FROM task_operations") == 0
    takeover = await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S)
    assert takeover is not None and takeover.epoch == claim.epoch + 1


@pytest.mark.asyncio
async def test_heartbeat_extends_lease_and_reports_cancel(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=5)
    assert claim is not None
    before = (await _task(env, accepted.task_id))["lease_expires_at"]
    beat = await env.tasks.heartbeat(accepted.task_id, claim.epoch, lease_s=LEASE_S)
    assert not beat.cancel_requested
    assert (await _task(env, accepted.task_id))["lease_expires_at"] > before
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    beat = await env.tasks.heartbeat(accepted.task_id, claim.epoch, lease_s=LEASE_S)
    assert beat.cancel_requested


@pytest.mark.asyncio
async def test_effect_fence_refuses_near_lease_expiry(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=2)
    assert claim is not None
    with pytest.raises(EffectRefused):
        await env.tasks.begin_operation(
            accepted.task_id, claim.epoch, tool_name="notify", target=None, min_lease_margin_s=10
        )
    assert await env.pool.fetchval("SELECT count(*) FROM task_operations") == 0


# --------------------------------------------------------------------------- #
# Retry rules and uncertain effects
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lost_attempt_after_material_operation_needs_attention(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert claim is not None
    await _execute(env, claim)
    op = await env.tasks.begin_operation(
        accepted.task_id,
        claim.epoch,
        tool_name="notification_send",
        target={"channel": "ntfy"},
        min_lease_margin_s=1,
    )
    # Crash before the outcome is saved: the effect may or may not have happened.
    await _expire_lease(env, accepted.task_id)
    assert await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S) is None
    row = await _task(env, accepted.task_id)
    assert row["status"] == "needs_attention" and row["terminal_code"] == "uncertain_effect"
    assert row["attempt_count"] == 1
    assert (await _message(env, accepted.result_message_id))["status"] == "error"
    operation = await env.pool.fetchrow("SELECT * FROM task_operations WHERE id = $1", op)
    assert operation["outcome"] == "started"


@pytest.mark.asyncio
async def test_retryable_error_after_material_operation_never_replays(env: Env):
    """A tool succeeds, then the next provider round fails retryably (review of #461)."""
    calls = []
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert claim is not None
    op = await env.tasks.begin_operation(
        accepted.task_id, claim.epoch, tool_name="notify", target=None, min_lease_margin_s=1
    )
    calls.append("notify")
    await env.tasks.finish_operation(op, outcome="succeeded")
    status = await env.tasks.fail_attempt(
        accepted.task_id, claim.epoch, cause=RetryCause.RETRYABLE_ERROR, error_code="rate_limited"
    )
    assert status is TaskStatus.NEEDS_ATTENTION
    await _make_due(env, accepted.task_id)
    assert await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S) is None
    assert calls == ["notify"]


@pytest.mark.asyncio
async def test_retryable_error_requeues_with_backoff_then_caps(env: Env):
    accepted = await _accept(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    await _execute(env, first)
    status = await env.tasks.fail_attempt(
        accepted.task_id, first.epoch, cause=RetryCause.RETRYABLE_ERROR, error_code="upstream_busy"
    )
    assert status is TaskStatus.QUEUED
    row = await _task(env, accepted.task_id)
    assert row["status"] == "queued" and row["lease_owner"] is None
    # Backoff: not claimable until due.
    assert await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S) is None
    await _make_due(env, accepted.task_id)
    second = await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S)
    assert second is not None and second.epoch == 2
    await _execute(env, second)
    status = await env.tasks.fail_attempt(
        accepted.task_id, second.epoch, cause=RetryCause.RETRYABLE_ERROR, error_code="upstream_busy"
    )
    assert status is TaskStatus.FAILED
    assert (await _task(env, accepted.task_id))["terminal_code"] == "interrupted"


@pytest.mark.asyncio
async def test_repeated_loss_exhausts_attempts(env: Env):
    accepted = await _accept(env)
    for worker in ("w1", "w2"):
        claim = await env.tasks.claim(accepted.task_id, worker_id=worker, lease_s=LEASE_S)
        assert claim is not None
        await _execute(env, claim)
        await _expire_lease(env, accepted.task_id)
    assert await env.tasks.claim(accepted.task_id, worker_id="w3", lease_s=LEASE_S) is None
    row = await _task(env, accepted.task_id)
    assert row["status"] == "failed" and row["terminal_code"] == "interrupted"
    outcomes = await env.pool.fetch(
        "SELECT outcome FROM task_attempts WHERE task_id = $1 ORDER BY epoch", accepted.task_id
    )
    assert [r["outcome"] for r in outcomes] == ["lost", "failed_terminal"]


@pytest.mark.asyncio
async def test_capacity_denial_is_terminal_with_its_code(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    status = await env.tasks.fail_attempt(
        accepted.task_id, claim.epoch, cause=RetryCause.CAPACITY, error_code="budget_exhausted"
    )
    assert status is TaskStatus.FAILED
    assert (await _task(env, accepted.task_id))["terminal_code"] == "budget_exhausted"


# --------------------------------------------------------------------------- #
# Completion and cancellation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_completion_publishes_result_once(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    status = await env.tasks.complete(
        accepted.task_id,
        claim.epoch,
        content="the answer",
        message_fields={"model": "mock-model", "metadata": {"finish_reason": "stop"}},
    )
    assert status is TaskStatus.COMPLETED
    message = await _message(env, accepted.result_message_id)
    assert message["status"] == "complete" and message["content"] == "the answer"
    assert message["model"] == "mock-model"
    with pytest.raises(LeaseLost):
        await env.tasks.complete(accepted.task_id, claim.epoch, content="again")
    history = await env.memory.get_recent_messages(
        accepted.conversation_id, exclude_status=["streaming", "error", "cancelled"]
    )
    assert [m["role"] for m in history] == ["user", "assistant"]
    kinds = [e.kind for e in await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)]
    assert kinds == ["accepted", "attempt_started", "finished"]


@pytest.mark.asyncio
async def test_cancel_queued_task_is_immediate(env: Env):
    accepted = await _accept(env)
    outcome = await env.tasks.request_cancel(env.alice, accepted.task_id)
    assert outcome.accepted and outcome.status is TaskStatus.CANCELLED
    assert await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S) is None
    assert (await _message(env, accepted.result_message_id))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_running_task_is_acknowledged_by_worker(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    outcome = await env.tasks.request_cancel(env.alice, accepted.task_id)
    assert outcome.accepted and outcome.status is TaskStatus.RUNNING
    await env.tasks.acknowledge_cancel(accepted.task_id, claim.epoch)
    assert (await _task(env, accepted.task_id))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_before_completion_commit_wins(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    status = await env.tasks.complete(accepted.task_id, claim.epoch, content="late answer")
    assert status is TaskStatus.CANCELLED
    message = await _message(env, accepted.result_message_id)
    assert message["status"] == "cancelled" and message["content"] == "late answer"


@pytest.mark.asyncio
async def test_cancel_after_completion_changes_nothing(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.complete(accepted.task_id, claim.epoch, content="answer")
    outcome = await env.tasks.request_cancel(env.alice, accepted.task_id)
    assert not outcome.accepted and outcome.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancel_with_dead_worker_resolves_on_reclaim(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    await _expire_lease(env, accepted.task_id)
    assert await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S) is None
    assert (await _task(env, accepted.task_id))["status"] == "cancelled"


# --------------------------------------------------------------------------- #
# Dispatch sweep and queue loss
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sweep_recovers_accepted_work_after_queue_loss(env: Env):
    # Accepted, but the post-commit enqueue never happened (Redis down / flushed).
    accepted = await _accept(env)
    woken = await env.tasks.due_for_wakeup()
    assert [task_id for task_id, _ in woken] == [accepted.task_id]
    # Recently woken tasks are not flooded with duplicate wake-ups.
    assert await env.tasks.due_for_wakeup() == []
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.complete(accepted.task_id, claim.epoch, content="answer")
    assert (await _task(env, accepted.task_id))["status"] == "completed"


@pytest.mark.asyncio
async def test_sweep_finds_expired_leases_not_live_ones(env: Env):
    live = await _accept(env, message="live")
    dead = await _accept(env, message="dead", h="b")
    for accepted in (live, dead):
        assert await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    await _expire_lease(env, dead.task_id)
    woken = await env.tasks.due_for_wakeup(rewake_after_s=0)
    assert [task_id for task_id, _ in woken] == [dead.task_id]


@pytest.mark.asyncio
async def test_wake_sequence_increases_per_wakeup(env: Env):
    accepted = await _accept(env)
    first = await env.tasks.mark_woken(accepted.task_id)
    [(task_id, second)] = await env.tasks.due_for_wakeup(rewake_after_s=0)
    assert task_id == accepted.task_id and second == first + 1


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_other_account_cannot_read_control_or_replay(env: Env):
    accepted = await _accept(env, key="mine")
    assert await env.tasks.snapshot(env.bob, accepted.task_id) is None
    assert await env.tasks.events_since(env.bob, accepted.task_id, after_seq=0) == []
    assert await env.tasks.active_for_conversation(env.bob, accepted.conversation_id) is None
    with pytest.raises(TaskNotFound):
        await env.tasks.request_cancel(env.bob, accepted.task_id)
    assert (await _task(env, accepted.task_id))["status"] == "queued"
    # Bob's identical key is his own, and reveals nothing about Alice's task.
    bobs = await _accept(env, env.bob, key="mine", h="c")
    assert bobs.created and bobs.task_id != accepted.task_id


@pytest.mark.asyncio
async def test_owner_snapshot_tracks_generation_and_delta_seq(env: Env):
    accepted = await _accept(env)
    active = await env.tasks.active_for_conversation(env.alice, accepted.conversation_id)
    assert active is not None and active.task_id == accepted.task_id
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.write_partial(accepted.task_id, claim.epoch, content="Hel", delta_seq=3)
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    assert (snapshot.status, snapshot.content, snapshot.content_generation) == (
        TaskStatus.RUNNING,
        "Hel",
        claim.epoch,
    )
    assert snapshot.content_delta_seq == 3


async def _suspend(env: Env, user_id: uuid.UUID) -> None:
    await env.pool.execute(
        "INSERT INTO entitlement_accounts (user_id, status) VALUES ($1, 'suspended') "
        "ON CONFLICT (user_id) DO UPDATE SET status = 'suspended'",
        user_id,
    )


@pytest.mark.asyncio
async def test_no_material_effect_starts_after_cancel_is_requested(env: Env):
    """Stop between heartbeats must still block the next effect (review of #461)."""
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    with pytest.raises(EffectRefused):
        await env.tasks.begin_operation(
            accepted.task_id, claim.epoch, tool_name="notify", target=None, min_lease_margin_s=1
        )
    assert await env.pool.fetchval("SELECT count(*) FROM task_operations") == 0


@pytest.mark.asyncio
async def test_suspension_mid_attempt_blocks_effects_and_publication(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await _suspend(env, env.alice)
    beat = await env.tasks.heartbeat(accepted.task_id, claim.epoch, lease_s=LEASE_S)
    assert beat.account_suspended and not beat.cancel_requested
    with pytest.raises(EffectRefused):
        await env.tasks.begin_operation(
            accepted.task_id, claim.epoch, tool_name="notify", target=None, min_lease_margin_s=1
        )
    status = await env.tasks.complete(accepted.task_id, claim.epoch, content="should not publish")
    assert status is TaskStatus.FAILED
    row = await _task(env, accepted.task_id)
    assert row["status"] == "failed" and row["terminal_code"] == "account_suspended"
    assert (await _message(env, accepted.result_message_id))["status"] == "error"


@pytest.mark.asyncio
async def test_other_accounts_suspension_does_not_affect_this_task(env: Env):
    accepted = await _accept(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await _suspend(env, env.bob)
    beat = await env.tasks.heartbeat(accepted.task_id, claim.epoch, lease_s=LEASE_S)
    assert not beat.account_suspended
    assert await env.tasks.complete(accepted.task_id, claim.epoch, content="ok") is (
        TaskStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_latest_task_stays_discoverable_after_it_finishes(env: Env):
    """Review of #461: another device can find a cancelled task's id and state."""
    accepted = await _accept(env)
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    assert await env.tasks.active_for_conversation(env.alice, accepted.conversation_id) is None
    latest = await env.tasks.latest_for_conversation(env.alice, accepted.conversation_id)
    assert latest is not None and latest.task_id == accepted.task_id
    assert latest.status is TaskStatus.CANCELLED
    assert await env.tasks.latest_for_conversation(env.bob, accepted.conversation_id) is None


@pytest.mark.asyncio
async def test_ended_compute_scopes_never_include_the_running_attempt(env: Env):
    accepted = await _accept(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    lost_scope, live_scope = uuid.uuid4(), uuid.uuid4()
    await env.tasks.begin_execution(accepted.task_id, first.epoch, lost_scope)
    await _expire_lease(env, accepted.task_id)
    second = await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S)
    assert second is not None
    await env.tasks.begin_execution(accepted.task_id, second.epoch, live_scope)
    assert await env.tasks.ended_compute_scopes(accepted.task_id) == [lost_scope]


@pytest.mark.asyncio
async def test_queued_task_with_pending_cancel_is_never_claimed(env: Env):
    accepted = await _accept(env)
    await env.pool.execute(
        "UPDATE tasks SET cancel_requested_at = now() WHERE id = $1", accepted.task_id
    )
    assert await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S) is None
    assert (await _task(env, accepted.task_id))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_requeued_task_is_rewoken_at_its_backoff_not_after_suppression(env: Env):
    """Codex review of #466: a just-consumed wake-up must not delay the retry."""
    accepted = await _accept(env)
    assert await env.tasks.due_for_wakeup() == [(accepted.task_id, 1)]  # wake recorded
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.fail_attempt(
        accepted.task_id, claim.epoch, cause=RetryCause.RETRYABLE_ERROR, error_code="busy"
    )
    await _make_due(env, accepted.task_id)
    woken = await env.tasks.due_for_wakeup()
    assert [task_id for task_id, _ in woken] == [accepted.task_id]


@pytest.mark.asyncio
async def test_claims_lost_before_execution_never_exhaust_the_task(env: Env):
    """Agent review of #466: preparation failures must not consume retries."""
    accepted = await _accept(env)
    for worker in ("w1", "w2", "w3", "w4"):
        claim = await env.tasks.claim(accepted.task_id, worker_id=worker, lease_s=LEASE_S)
        assert claim is not None and claim.attempt_count == 0
        await _expire_lease(env, accepted.task_id)  # died before executing
    final = await env.tasks.claim(accepted.task_id, worker_id="w5", lease_s=LEASE_S)
    assert final is not None
    assert await _execute(env, final) == 1
    assert await env.tasks.complete(accepted.task_id, final.epoch, content="done") is (
        TaskStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_replay_survives_a_rotated_digest_key(env: Env):
    """Codex review of #461: a pepper rotation must not turn a replay into a conflict."""
    from orchestrator.tasks.inputs import chat_request_fingerprint

    def fingerprint(key: str, message: str):
        return chat_request_fingerprint(
            key=key,
            conversation_id=None,
            message=message,
            attachments=None,
            model=None,
            provider=None,
            metadata=None,
            disable_memory_write=False,
        )

    before = fingerprint("pepper-before-rotation", "hello")
    first = await env.tasks.accept(
        user_id=env.alice,
        conversation_id=None,
        new_conversation_title="t",
        pipeline="cloud",
        user_message="hello",
        task_input={"message": "hello"},
        request_hash=before.digest,
        request_canonical=before.canonical,
        idempotency_key="rotated",
        assistant_model=None,
    )
    after = fingerprint("pepper-after-rotation", "hello")
    assert after.digest != before.digest
    replay = await env.tasks.find_by_key(env.alice, "rotated", after.digest, after.canonical)
    assert replay is not None and replay.task_id == first.task_id
    # The stored digest follows the current key from now on.
    assert (await _task(env, first.task_id))["request_hash"] == after.digest
    different = fingerprint("pepper-after-rotation", "something else")
    with pytest.raises(IdempotencyConflict):
        await env.tasks.find_by_key(env.alice, "rotated", different.digest, different.canonical)
