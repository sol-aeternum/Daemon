"""NEW durable acceptance only: actual SQL/commit/HTTP and owned Redis proofs.

No worker-output or complete account-fence claim. Providers are scripted by the
existing API fixture. PostgreSQL waits, transactions, ciphertext and rows are real.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from orchestrator import main
from orchestrator.config import get_settings
from orchestrator.redis_jobs import enqueue_account_job
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    WriteGuardRefusal,
)
from orchestrator.tasks import store as store_module
from orchestrator.tasks.store import IdempotencyConflict
from tests.durable_tasks_support import durable_env_fixture
from tests.test_account_write_guard import blocked
from tests.test_entity_enqueue_chain import entity_queue as entity_queue
from tests.test_entity_producer_postgres import queued
from tests.test_task_api_postgres import api as api

env = durable_env_fixture(accounts=True)


async def accept(env, capture, *, key=None, conversation=None, task_input=None, digest="a" * 64):
    return await env.tasks.accept(
        user_id=env.alice,
        conversation_id=conversation,
        new_conversation_title="Fictional acceptance",
        pipeline="cloud",
        user_message="Fictional input",
        task_input={"message": "Fictional input"} if task_input is None else task_input,
        request_hash=digest,
        request_canonical="fictional canonical request",
        idempotency_key=key,
        assistant_model=None,
        captured_generation=capture,
    )


async def counts(env):
    row = await env.pool.fetchrow(
        "SELECT (SELECT count(*) FROM conversations) AS conversations, "
        "(SELECT count(*) FROM messages) AS messages, "
        "(SELECT count(*) FROM tasks) AS tasks, "
        "(SELECT count(*) FROM task_events) AS events"
    )
    return tuple(row.values())


async def ciphertext(env, task):
    return await env.pool.fetchval("SELECT input_ciphertext FROM tasks WHERE id=$1", task)


async def reusable(env, expected_pid=None):
    async with env.pool.acquire() as conn, conn.transaction():
        if expected_pid is not None:
            assert conn.get_server_pid() == expected_pid
        assert await conn.fetchval("SELECT 1") == 1
        await conn.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
            env.alice,
        )


async def restrict(conn, owner, kind):
    """Test-only control simulation; account UPDATE must precede subordinate writes."""
    await conn.fetchrow(
        "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", owner
    )
    if kind == "generation":
        await conn.execute(
            "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", owner
        )
    elif kind == "suspended":
        await conn.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", owner
        )
    elif kind == "cause":
        await conn.execute(
            "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", owner, uuid4()
        )
    elif kind == "operation":
        journal = uuid4()
        await conn.execute(
            "INSERT INTO deletion_journal_binding(singleton,journal_id) VALUES(true,$1)", journal
        )
        await conn.execute(
            "INSERT INTO account_deletions(id,account_id,kind,journal_id,request_seq,"
            "request_event_id,request_digest,command_digest,requested_at,prior_status,"
            "fence_generation,drain_deadline,revocation_deadline,billing_deadline) "
            "VALUES($1,$2,'reset',$3,1,$4,$5,$5,now(),'active',8,now(),now(),now())",
            uuid4(),
            owner,
            journal,
            uuid4(),
            b"x" * 32,
        )
    else:
        raise AssertionError(kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [0, 7])
async def test_origin_is_authoritative_encrypted_and_atomic(env, generation):
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=$2 WHERE user_id=$1",
        env.alice,
        generation,
    )
    caller = {
        "message": "Fictional input",
        "origin_account_id": str(env.bob),
        "origin_account_generation": 999,
        "summary_origin_account_id": str(env.bob),
        "summary_origin_account_generation": True,
    }
    original = caller.copy()
    result = await accept(env, CapturedGeneration(env.alice, generation), task_input=caller)
    assert result.created and caller == original
    assert await counts(env) == (1, 2, 1, 1)
    sealed = await ciphertext(env, result.task_id)
    assert "Fictional" not in sealed and str(env.alice) not in sealed
    assert env.tasks._open(sealed) == {
        "message": "Fictional input",
        "origin_account_id": str(env.alice),
        "origin_account_generation": generation,
        "summary_origin_account_id": str(env.alice),
        "summary_origin_account_generation": generation,
        "_request": "fictional canonical request",
    }
    assert (
        await env.pool.fetchval("SELECT content_generation FROM tasks WHERE id=$1", result.task_id)
        == 0
    )  # still the attempt-output epoch
    events = await env.tasks.events_since(env.alice, result.task_id, after_seq=0)
    assert [(e.kind, e.payload) for e in events] == [("accepted", {})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformation",
    ["missing", "dictionary", "foreign", "uuid_string", "unset", True, -1, 2**63, "7", 7.0],
)
async def test_new_invalid_capture_never_writes_or_uses_dictionary_authority(env, malformation):
    capture = CapturedGeneration(env.alice, 7)
    if malformation == "missing":
        capture = None
    elif malformation == "dictionary":
        capture = {"account_id": env.alice, "generation": 7}
    elif malformation == "foreign":
        capture = CapturedGeneration(env.bob, 7)
    elif malformation == "uuid_string":
        object.__setattr__(capture, "account_id", str(env.alice))
    elif malformation == "unset":
        capture = object.__new__(CapturedGeneration)
    else:
        object.__setattr__(capture, "generation", malformation)
    with pytest.raises(WriteGuardRefusal, match="invalid_capture"):
        await accept(
            env,
            capture,
            task_input={"origin_account_id": str(env.alice), "origin_account_generation": 0},
        )
    assert await counts(env) == (0, 0, 0, 0)
    await reusable(env)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["missing", "generation", "suspended", "cause", "operation", "schema"]
)
async def test_new_account_or_database_refusal_is_atomic(env, kind):
    if kind == "missing":
        await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", env.alice)
    elif kind == "schema":
        await env.pool.execute("ALTER TABLE account_suspension_causes RENAME TO hidden_causes")
    else:
        async with env.pool.acquire() as conn, conn.transaction():
            await restrict(conn, env.alice, kind)
    error = asyncpg.UndefinedTableError if kind == "schema" else WriteGuardRefusal
    with pytest.raises(error):
        await accept(env, CapturedGeneration(env.alice, 0))
    assert await counts(env) == (0, 0, 0, 0)
    await reusable(env)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_replay_ignores_capture_and_state_without_resealing_even_after_pepper_rotation(
    env, monkeypatch, legacy
):
    first = await accept(env, CapturedGeneration(env.alice, 0), key="replay")
    if legacy:
        await env.pool.execute(
            "UPDATE tasks SET input_ciphertext=$2 WHERE id=$1",
            first.task_id,
            env.tasks._seal(
                {"message": "Fictional input", "_request": "fictional canonical request"}
            ),
        )
    original = await ciphertext(env, first.task_id)
    async with env.pool.acquire() as conn, conn.transaction():
        await restrict(conn, env.alice, "generation")
        await restrict(conn, env.alice, "suspended")
    guard = AsyncMock(side_effect=AssertionError("replay cannot guard or rebind"))
    monkeypatch.setattr(store_module, "guard_content_write", guard)
    for capture in (None, CapturedGeneration(env.alice, 0), CapturedGeneration(env.bob, 0)):
        replay = await accept(env, capture, key="replay", digest="b" * 64)
        assert not replay.created and replay.task_id == first.task_id
        assert await ciphertext(env, first.task_id) == original
    assert await counts(env) == (1, 2, 1, 1)
    guard.assert_not_called()
    assert await env.pool.fetchval("SELECT request_hash FROM tasks") == "b" * 64
    with pytest.raises(IdempotencyConflict):
        await env.tasks.accept(
            user_id=env.alice,
            conversation_id=None,
            new_conversation_title=None,
            pipeline="cloud",
            user_message="different",
            task_input={},
            request_hash="c" * 64,
            request_canonical="different canonical",
            idempotency_key="replay",
            assistant_model=None,
        )
    assert await ciphertext(env, first.task_id) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["generation", "suspended", "cause", "operation"])
async def test_fence_first_acceptance_waits_then_refuses_actual_commit(env, monkeypatch, kind):
    writer_seen = asyncio.Event()
    writer_pid = None
    actual_guard = store_module.guard_content_write

    @asynccontextmanager
    async def observed_guard(conn, captured):
        nonlocal writer_pid
        writer_pid = conn.get_server_pid()
        writer_seen.set()
        async with actual_guard(conn, captured) as scope:
            yield scope

    monkeypatch.setattr(store_module, "guard_content_write", observed_guard)
    async with env.pool.acquire() as fence:
        tx = fence.transaction()
        await tx.start()
        await restrict(fence, env.alice, kind)
        task = asyncio.create_task(accept(env, CapturedGeneration(env.alice, 0)))
        try:
            await asyncio.wait_for(writer_seen.wait(), 10)
            await blocked(env.pool, writer_pid, fence.get_server_pid())
            assert await counts(env) == (0, 0, 0, 0)
            await tx.commit()
            with pytest.raises(WriteGuardRefusal):
                await asyncio.wait_for(task, 10)
        finally:
            if fence.is_in_transaction():
                await tx.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await counts(env) == (0, 0, 0, 0)
    await reusable(env)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["generation", "suspended", "cause", "operation"])
async def test_writer_first_holds_share_through_real_commit_after_guard_exit(
    env, monkeypatch, kind
):
    # Deferred trigger stalls the real COMMIT, after Python guard scope exit.
    key = uuid4().int % (2**63 - 1)
    await env.pool.execute(
        "CREATE FUNCTION acceptance_commit_wait() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"BEGIN PERFORM pg_advisory_xact_lock({key}); RETURN NEW; END $$; "
        "CREATE CONSTRAINT TRIGGER acceptance_commit_wait AFTER INSERT ON task_events "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION acceptance_commit_wait()"
    )
    seen = asyncio.Event()
    exited = asyncio.Event()
    writer_pid = None
    actual_guard = store_module.guard_content_write

    @asynccontextmanager
    async def observed_guard(conn, captured):
        nonlocal writer_pid
        async with actual_guard(conn, captured) as scope:
            writer_pid = conn.get_server_pid()
            seen.set()
            yield scope
        exited.set()

    monkeypatch.setattr(store_module, "guard_content_write", observed_guard)
    async with env.pool.acquire() as commit_holder, env.pool.acquire() as fence:
        hold = commit_holder.transaction()
        await hold.start()
        await commit_holder.execute("SELECT pg_advisory_xact_lock($1)", key)
        writer = asyncio.create_task(accept(env, CapturedGeneration(env.alice, 0)))
        controller = None
        try:
            await asyncio.wait_for(seen.wait(), 10)
            await blocked(env.pool, writer_pid, commit_holder.get_server_pid())
            assert exited.is_set() and not writer.done()
            assert await counts(env) == (0, 0, 0, 0)

            async def control():
                async with fence.transaction():
                    await restrict(fence, env.alice, kind)
                    # The writer's complete atomic acceptance preceded our lock.
                    assert await counts(env) == (1, 2, 1, 1)

            controller = asyncio.create_task(control())
            await blocked(env.pool, fence.get_server_pid(), writer_pid)
            await hold.commit()
            assert (await asyncio.wait_for(writer, 10)).created
            await asyncio.wait_for(controller, 10)
        finally:
            if commit_holder.is_in_transaction():
                await hold.rollback()
            for task in (writer, controller):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(t for t in (writer, controller) if t is not None), return_exceptions=True
            )
    assert await counts(env) == (1, 2, 1, 1)


@pytest.mark.asyncio
async def test_account_share_is_acquired_before_existing_conversation_lock(env, monkeypatch):
    conv = await env.memory.create_conversation(env.alice)
    pid = None
    seen = asyncio.Event()
    actual_guard = store_module.guard_content_write

    @asynccontextmanager
    async def observed_guard(conn, captured):
        nonlocal pid
        async with actual_guard(conn, captured) as scope:
            pid = conn.get_server_pid()
            seen.set()
            yield scope

    monkeypatch.setattr(store_module, "guard_content_write", observed_guard)
    async with env.pool.acquire() as holder, holder.transaction():
        await holder.fetchrow("SELECT id FROM conversations WHERE id=$1 FOR UPDATE", conv["id"])
        task = asyncio.create_task(
            accept(env, CapturedGeneration(env.alice, 0), conversation=conv["id"])
        )
        try:
            await asyncio.wait_for(seen.wait(), 10)
            await blocked(env.pool, pid, holder.get_server_pid())
            async with env.pool.acquire() as fence:
                with pytest.raises(asyncpg.LockNotAvailableError):
                    async with fence.transaction():
                        await fence.fetchrow(
                            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                            env.alice,
                        )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await counts(env) == (1, 0, 0, 0)
    await reusable(env)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "cancellation", "commit"])
async def test_partial_turn_failures_rollback_and_release_connection(env, monkeypatch, failure):
    real_append = env.tasks._append_event
    entered = asyncio.Event()
    writer_pid = None

    async def failing_append(conn, *args, **kwargs):
        nonlocal writer_pid
        writer_pid = conn.get_server_pid()
        result = await real_append(conn, *args, **kwargs)
        assert await conn.fetchval("SELECT count(*) FROM messages") == 2
        entered.set()
        if failure == "rollback":
            raise RuntimeError("synthetic rollback")
        if failure == "cancellation":
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(env.tasks, "_append_event", failing_append)
    if failure == "commit":
        await env.pool.execute(
            "CREATE FUNCTION acceptance_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'synthetic acceptance commit failure'; END $$; "
            "CREATE CONSTRAINT TRIGGER acceptance_commit_failure AFTER INSERT ON task_events "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION acceptance_commit_failure()"
        )
    task = asyncio.create_task(accept(env, CapturedGeneration(env.alice, 0)))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if failure == "cancellation":
            task.cancel()
        error = {
            "rollback": RuntimeError,
            "cancellation": asyncio.CancelledError,
            "commit": asyncpg.RaiseError,
        }[failure]
        with pytest.raises(error):
            await asyncio.wait_for(task, 10)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert await counts(env) == (0, 0, 0, 0)
    await reusable(env, writer_pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("change_generation", [False, True])
async def test_actual_busy_conflict_ended_retry_keeps_identical_original_capture(
    env, monkeypatch, change_generation
):
    captured = CapturedGeneration(env.alice, 0)
    first = await accept(env, captured, key="first")
    calls = 0
    seen = []
    actual_lookup, actual_once = env.tasks._by_key, env.tasks._accept_once

    async def lookup(*args):
        nonlocal calls
        calls += 1
        if calls == 2:  # only AFTER real uniqueness failure and full rollback
            await env.tasks.request_cancel(env.alice, first.task_id)
            if change_generation:
                async with env.pool.acquire() as conn, conn.transaction():
                    await restrict(conn, env.alice, "generation")
        return await actual_lookup(*args)

    async def once(**kwargs):
        seen.append(kwargs["captured_generation"])
        return await actual_once(**kwargs)

    monkeypatch.setattr(env.tasks, "_by_key", lookup)
    monkeypatch.setattr(env.tasks, "_accept_once", once)
    if change_generation:
        with pytest.raises(WriteGuardRefusal, match="stale_generation"):
            await accept(env, captured, key="second", conversation=first.conversation_id)
        assert await counts(env) == (1, 2, 1, 2)
    else:
        assert (
            await accept(env, captured, key="second", conversation=first.conversation_id)
        ).created
        assert await counts(env) == (1, 4, 2, 3)
    assert len(seen) == 2 and all(value is captured for value in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_concurrent_commit_between_lookup_and_guard_refusal_resolves_existing_only(
    env, monkeypatch, conflict
):
    missed, resume = asyncio.Event(), asyncio.Event()
    real_lookup = env.tasks._by_key
    calls = 0

    async def lookup(*args):
        nonlocal calls
        calls += 1
        found = await real_lookup(*args)
        if calls == 1:
            assert found is None
            missed.set()
            await resume.wait()
        return found

    monkeypatch.setattr(env.tasks, "_by_key", lookup)
    loser = asyncio.create_task(accept(env, CapturedGeneration(env.alice, 0), key="race"))
    try:
        await asyncio.wait_for(missed.wait(), 10)
        winner = await accept(env, CapturedGeneration(env.alice, 0), key="race")
        original = await ciphertext(env, winner.task_id)
        if conflict:
            await env.pool.execute(
                "UPDATE tasks SET request_hash=$2,input_ciphertext=$3 WHERE id=$1",
                winner.task_id,
                "c" * 64,
                env.tasks._seal({"_request": "different"}),
            )
            original = await ciphertext(env, winner.task_id)
        async with env.pool.acquire() as conn, conn.transaction():
            await restrict(conn, env.alice, "suspended")
        resume.set()
        if conflict:
            with pytest.raises(IdempotencyConflict):
                await asyncio.wait_for(loser, 10)
        else:
            replay = await asyncio.wait_for(loser, 10)
            assert not replay.created and replay.task_id == winner.task_id
        assert await ciphertext(env, winner.task_id) == original
        assert await counts(env) == (1, 2, 1, 1)
    finally:
        resume.set()
        if not loser.done():
            loser.cancel()
        await asyncio.gather(loser, return_exceptions=True)


@pytest.mark.asyncio
async def test_acceptance_uses_read_committed_even_with_stronger_pool_default(env, monkeypatch):
    real_guard = store_module.guard_content_write

    @asynccontextmanager
    async def observed(conn, capture):
        assert await conn.fetchval("SHOW default_transaction_isolation") == "repeatable read"
        assert await conn.fetchval("SHOW transaction_isolation") == "read committed"
        async with real_guard(conn, capture) as scope:
            yield scope

    # Use a dedicated real pool with the same fixture schema, never an ambient DB.
    import os
    from tests.durable_tasks_support import DSN_ENV

    search = await env.pool.fetchval("SHOW search_path")
    pool = await asyncpg.create_pool(
        os.environ[DSN_ENV],
        min_size=1,
        max_size=2,
        server_settings={"search_path": search, "default_transaction_isolation": "repeatable read"},
    )
    monkeypatch.setattr(env.tasks, "_pool", pool)
    monkeypatch.setattr(store_module, "guard_content_write", observed)
    try:
        assert (await accept(env, CapturedGeneration(env.alice, 0))).created
    finally:
        await pool.close()
    assert await counts(env) == (1, 2, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "capture_failure",
        "missing_account",
        "foreign",
        "malformed",
        "generation",
        "suspended",
        "cause",
        "operation",
        "schema",
        "commit_failure",
    ],
)
async def test_http_refusal_never_enqueues_or_starts_request_bound_work(api, monkeypatch, kind):
    env = api.env
    stream = AsyncMock(side_effect=AssertionError("durable refusal cannot fall back"))
    enqueue = AsyncMock(side_effect=AssertionError("refusal cannot enqueue task/title"))
    monkeypatch.setattr(main, "stream_sse_chat", stream)
    monkeypatch.setattr(main, "enqueue_account_job", enqueue)
    original_capture = main.capture_generation
    captures = []

    async def capture(conn, owner):
        if kind == "capture_failure":
            raise RuntimeError("synthetic capture failure")
        value = await original_capture(conn, owner)
        captures.append(value)
        if kind == "foreign":
            return CapturedGeneration(env.bob, 0)
        if kind == "malformed":
            object.__setattr__(value, "generation", True)
        if kind == "schema":
            await env.pool.execute("ALTER TABLE account_suspension_causes RENAME TO hidden_causes")
        if kind == "commit_failure":
            await env.pool.execute(
                "CREATE FUNCTION http_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'synthetic HTTP commit failure'; END $$; "
                "CREATE CONSTRAINT TRIGGER http_commit_failure AFTER INSERT ON task_events "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION http_commit_failure()"
            )
        if kind in ("generation", "suspended", "cause", "operation"):
            async with env.pool.acquire() as fence, fence.transaction():
                await restrict(fence, owner, kind)
        return value

    if kind == "missing_account":
        await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", env.alice)
    spy = AsyncMock(side_effect=capture)
    monkeypatch.setattr(main, "capture_generation", spy)
    response = await api.client.post("/chat", json={"message": "Fictional refusal"})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "task_unavailable"
    spy.assert_awaited_once()
    if kind in ("capture_failure", "missing_account"):
        assert captures == []
    else:
        assert len(captures) == 1 and captures[0].account_id == env.alice
        assert captures[0].generation == (True if kind == "malformed" else 0)
    assert await counts(env) == (0, 0, 0, 0)
    stream.assert_not_called()
    enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_native_owned_redis_lost_wakeup_reply_and_replay_never_duplicate_acceptance(
    api, monkeypatch, entity_queue
):
    env = api.env
    queue, pipeline = entity_queue
    monkeypatch.setattr(main.app.state.app_state, "redis", queue)
    monkeypatch.setattr(main, "enqueue_account_job", enqueue_account_job)
    original_enqueue = queue.enqueue_job
    observer_entered = asyncio.Event()
    original_observe = main._observe_task_response

    async def lost_reply(function, *args, **kwargs):
        result = await original_enqueue(function, *args, **kwargs)
        if function == "run_chat_task":
            raise RedisConnectionError("synthetic lost wake-up reply")
        return result

    def observe(*args, **kwargs):
        observer_entered.set()
        return original_observe(*args, **kwargs)

    monkeypatch.setattr(queue, "enqueue_job", lost_reply)
    monkeypatch.setattr(main, "_observe_task_response", observe)
    capture = AsyncMock(wraps=main.capture_generation)
    monkeypatch.setattr(main, "capture_generation", capture)
    request = asyncio.create_task(
        api.client.post(
            "/chat",
            json={"message": "Fictional Redis outage"},
            headers={"Idempotency-Key": "redis-lost"},
        )
    )
    try:
        await asyncio.wait_for(observer_entered.wait(), 10)
        request.cancel()
        with suppress(asyncio.CancelledError):
            await request
        assert await counts(env) == (1, 2, 1, 1)
        row = await env.pool.fetchrow("SELECT id,input_ciphertext FROM tasks")
        stored = env.tasks._open(row["input_ciphertext"])
        assert "origin_account" not in stored["_request"]
        assert stored["origin_account_id"] == str(env.alice)
        assert stored["origin_account_generation"] == 0
        wakeups = await queued(queue, pipeline, "run_chat_task")
        assert len(wakeups) == 1 and wakeups[0].args == (str(row["id"]),)
        assert await env.pool.fetchval("SELECT wake_seq FROM tasks") == 1
        # Terminalize only to bound actual HTTP replay/observation; no worker runs.
        await env.tasks.request_cancel(env.alice, row["id"])
        async with env.pool.acquire() as fence, fence.transaction():
            await restrict(fence, env.alice, "generation")
            await restrict(fence, env.alice, "suspended")
        replay = await api.client.post(
            "/chat",
            json={"message": "Fictional Redis outage"},
            headers={"Idempotency-Key": "redis-lost"},
        )
        assert replay.status_code == 200
        assert await ciphertext(env, row["id"]) == row["input_ciphertext"]
        assert await counts(env) == (1, 2, 1, 2)
        assert len(await queued(queue, pipeline, "run_chat_task")) == 1
        capture.assert_awaited_once()  # HTTP replay happens before capture
        public = await api.client.get(f"/tasks/{row['id']}")
        assert public.status_code == 200
        for body in (replay.text, public.text):
            assert "origin_account" not in body and str(env.alice) not in body
    finally:
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["/council Fictional question", "/council config: preset=lean, rounds=2, audit=false"],
)
@pytest.mark.parametrize("capture_failure", [False, True])
async def test_durable_capable_council_stays_request_bound(
    api, monkeypatch, message, capture_failure
):
    accept_spy = AsyncMock(side_effect=AssertionError("council cannot accept a durable task"))
    monkeypatch.setattr(type(api.env.tasks), "accept", accept_spy)
    calls = []

    async def scripted_council(**kwargs):
        calls.append(kwargs)
        yield 'event: council_status\ndata: {"data":{"status":"fictional"}}\n\n'

    # Script only the council provider boundary; retain real request persistence,
    # account adapter and council-output finalizer. Never execute a live council.
    monkeypatch.setattr(main, "stream_council", scripted_council)
    monkeypatch.setattr(main, "stream_council_interview_response", scripted_council)
    if capture_failure:
        monkeypatch.setattr(
            main,
            "capture_generation",
            AsyncMock(side_effect=RuntimeError("synthetic capture outage")),
        )
    response = await api.client.post("/chat", json={"message": message})
    assert response.status_code == 200 and "X-Daemon-Task-Id" not in response.headers
    assert len(calls) == 1 and calls[0]["user_message"] == message
    accept_spy.assert_not_called()
    assert await api.env.pool.fetchval("SELECT count(*) FROM tasks") == 0
    assert await api.env.pool.fetchval("SELECT count(*) FROM messages WHERE role='user'") == 1
    assert not any(name == "run_chat_task" for name, _, _ in api.redis.enqueued)


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_failure", [False, True])
async def test_durable_capable_bound_home_followup_retains_request_bound_context(
    api, monkeypatch, capture_failure
):
    env = api.env
    conv = await env.memory.create_conversation(env.alice, title="Fictional home")
    await env.pool.execute(
        "UPDATE conversations SET metadata=$2::jsonb WHERE id=$1",
        conv["id"],
        '{"home_suggestion":1}',
    )
    context = {
        "version": 1,
        "suggestion_id": "fictional",
        "sources": [
            {
                "conversation_id": str(uuid4()),
                "title": "Fictional source",
                "messages": [
                    {"id": str(uuid4()), "role": "user", "content": "Fictional copied context"}
                ],
            }
        ],
    }
    await env.memory.insert_message(
        conv["id"],
        env.alice,
        "user",
        "Fictional original prompt",
        status="complete",
        metadata={"home_suggestion": context},
    )
    accept_spy = AsyncMock(side_effect=AssertionError("bound home cannot accept a durable task"))
    monkeypatch.setattr(type(env.tasks), "accept", accept_spy)
    calls = []
    original_stream = main._account_chat_frames

    async def observed_stream(*args, **kwargs):
        calls.append(kwargs)
        async for frame in original_stream(*args, **kwargs):
            yield frame

    monkeypatch.setattr(main, "_account_chat_frames", observed_stream)
    if capture_failure:
        monkeypatch.setattr(
            main,
            "capture_generation",
            AsyncMock(side_effect=RuntimeError("synthetic capture outage")),
        )
    response = await api.client.post(
        "/chat", json={"message": "hello", "conversation_id": str(conv["id"])}
    )
    assert response.status_code == 200 and "X-Daemon-Task-Id" not in response.headers
    assert len(calls) == 1
    assert "Fictional copied context" in calls[0]["history_messages"][0]["content"]
    assert calls[0]["history_messages"][-1]["content"] == "hello"
    accept_spy.assert_not_called()
    assert await counts(env) == (1, 3, 0, 0)
    assert not any(name == "run_chat_task" for name, _, _ in api.redis.enqueued)


@pytest.mark.asyncio
@pytest.mark.parametrize("eligibility", ["flag_off", "unsupported"])
async def test_missing_account_capture_does_not_expand_new_acceptance_to_other_paths(
    api, monkeypatch, eligibility
):
    await api.env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", api.env.alice)
    if eligibility == "flag_off":
        monkeypatch.setenv("DURABLE_CHAT_ENABLED", "false")
        get_settings.cache_clear()
    else:
        api.client.headers.pop("X-Daemon-Client-Features")
    accept_spy = AsyncMock(side_effect=AssertionError("ineligible clients stay request bound"))
    monkeypatch.setattr(type(api.env.tasks), "accept", accept_spy)
    response = await api.client.post("/chat", json={"message": "hello"})
    assert response.status_code == 200 and "X-Daemon-Task-Id" not in response.headers
    assert await counts(api.env) == (1, 2, 0, 0)
    accept_spy.assert_not_called()
    assert not any(name == "run_chat_task" for name, _, _ in api.redis.enqueued)
