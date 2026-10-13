"""J1c-1b actual title writers on disposable PostgreSQL, never paid inference.

Only compute/provider seams are scripted. SQL guards, ownership predicates,
encryption, transactions, lock waits and commits are real. Wait-for edges prove
ordering; deadlines merely prevent a broken proof hanging the test runner.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import asyncpg
import pytest
from arq.jobs import deserialize_job, serialize_job
from arq.worker import Retry
from starlette.responses import StreamingResponse

from orchestrator import main
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal
from orchestrator.services.deletion_journal.model import RequestFields
from orchestrator.worker import jobs
from tests.durable_tasks_support import durable_env_fixture
from tests.test_account_write_guard import blocked
from tests.test_task_api_postgres import api as api

env = durable_env_fixture()
FAMILIES = ["generate_title", "generate_conversation_title_job"]


async def prepare(env, monkeypatch):
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id,content_generation) VALUES($1,7),($2,7)",
        env.alice,
        env.bob,
    )
    conversation = await env.memory.create_conversation(env.alice, title="New conversation")
    message = await env.memory.insert_message(
        conversation["id"], env.alice, "user", "Fictional title input", status="complete"
    )

    @asynccontextmanager
    async def compute(pool, account_id, **kwargs):
        assert pool is env.pool and account_id == env.alice
        assert kwargs == {
            "operation": "agent",
            "auto_route": True,
            "background": True,
            "profile": "background",
        }
        # If preflight SHARE leaked past input reads this UPDATE NOWAIT fails.
        async with pool.acquire() as conn, conn.transaction():
            await conn.fetchrow(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                account_id,
            )
        yield

    monkeypatch.setattr(jobs, "account_compute", compute)
    generator = AsyncMock(return_value="Generated title")
    monkeypatch.setattr(jobs, "generate_conversation_title", generator)
    return conversation["id"], message["id"], generator


async def run(env, family, conversation, message, *, owner=None, generation=7):
    ctx = {"store": env.memory, "db_pool": env.pool}
    args = (conversation, message) if family == "generate_title" else (conversation,)
    return await getattr(jobs, family)(
        ctx, *args, origin_account_id=str(owner or env.alice), origin_account_generation=generation
    )


def assert_refused(result, family, reason):
    assert result == (
        None if family == "generate_title" else {"status": "skipped", "reason": reason}
    )


async def row(env, conversation):
    return dict(
        await env.pool.fetchrow(
            "SELECT title,title_locked,updated_at,last_activity_at FROM conversations WHERE id=$1",
            conversation,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_current_nonzero_account_generation_writes_actual_title(env, monkeypatch, family):
    conversation, message, generator = await prepare(env, monkeypatch)
    result = await run(env, family, conversation, message)
    assert result == (
        "Generated title"
        if family == "generate_title"
        else {
            "status": "ok",
            "title": "Generated title",
        }
    )
    assert (await row(env, conversation))["title"] == "Generated title"
    generator.assert_awaited_once_with([{"role": "user", "content": "Fictional title input"}])


@pytest.mark.asyncio
async def test_history_title_preserves_last50_order_and_role_filter_without_foreign_rows(
    env, monkeypatch
):
    conversation, message, generator = await prepare(env, monkeypatch)
    await env.pool.execute("UPDATE messages SET created_at='2000-01-01' WHERE id=$1", message)
    for index in range(1, 53):
        role = "system" if index == 30 else "assistant" if index % 2 == 0 else "user"
        await env.pool.execute(
            "INSERT INTO messages(conversation_id,user_id,role,content,created_at) "
            "VALUES($1,$2,$3,$4,'2000-01-01'::timestamptz + $5 * interval '1 second')",
            conversation,
            env.bob if index == 52 else env.alice,
            role,
            env.memory.encryption.encrypt(f"Fictional input {index}"),
            index,
        )
    assert await run(env, "generate_conversation_title_job", conversation, message) == {
        "status": "ok",
        "title": "Generated title",
    }
    generator.assert_awaited_once_with(
        [
            {
                "role": "assistant" if index % 2 == 0 else "user",
                "content": f"Fictional input {index}",
            }
            for index in range(2, 52)
            if index != 30
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_serialized_retry_keeps_original_generation_and_never_reads_inputs(
    env, monkeypatch, family
):
    conversation, message, generator = await prepare(env, monkeypatch)
    before = await row(env, conversation)
    args = (str(conversation), str(message)) if family == "generate_title" else (str(conversation),)
    payload = {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    wire = serialize_job(family, args, payload, 1, 1_700_000_000_000)
    decoded = deserialize_job(wire)
    generator.side_effect = Retry(defer=0)
    with pytest.raises(Retry):
        await getattr(jobs, decoded.function)(
            {"store": env.memory, "db_pool": env.pool}, *decoded.args, **decoded.kwargs
        )
    assert await row(env, conversation) == before
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
    )
    decrypt = Mock(side_effect=AssertionError("stale payload must not decrypt"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    generator.reset_mock()
    # Retry count changes, NOT the payload origin. Match native ARQ serialization.
    retry = deserialize_job(
        serialize_job(decoded.function, decoded.args, decoded.kwargs, 2, 1_700_000_000_000)
    )
    assert retry.kwargs == payload
    result = await getattr(jobs, retry.function)(
        {"store": env.memory, "db_pool": env.pool}, *retry.args, **retry.kwargs
    )
    assert_refused(result, family, "stale_generation")
    generator.assert_not_awaited()
    decrypt.assert_not_called()
    assert await row(env, conversation) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "restriction,reason",
    [
        ("suspended", "account_restricted"),
        ("cause", "inconsistent_state"),
        ("operation", "open_operation"),
        ("missing", "missing_account"),
    ],
)
async def test_account_refusal_precedes_input_decryption_and_compute(
    env, monkeypatch, tmp_path, family, restriction, reason
):
    conversation, message, generator = await prepare(env, monkeypatch)
    if restriction == "operation":
        # Use the actual J1b provenance path, not an invented operation shape.
        from tests.test_account_control_persistence import persist, settings
        from tests.deletion_journal_support import request
        from orchestrator.services.deletion_journal.store import initialize
        from orchestrator.services.deletion_journal.barrier import require_recovery_barrier

        directory = (tmp_path / "journal", uuid4())
        initialize(*directory)
        await require_recovery_barrier(env.pool, settings(*directory))
        command = request(env.alice)
        assert isinstance(command.fields, RequestFields)
        command = replace(command, fields=replace(command.fields, generation=8))
        await persist(env, directory, command, uuid4())
        generation = 8
    else:
        generation = 7
        if restriction == "suspended":
            await env.pool.execute(
                "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
            )
        elif restriction == "cause":
            await env.pool.execute(
                "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
            )
        else:
            await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", env.alice)
    decrypt = Mock(side_effect=AssertionError("refused account must not decrypt"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    compute = Mock(side_effect=AssertionError("refused account must not admit compute"))
    monkeypatch.setattr(jobs, "account_compute", compute)
    result = await run(env, family, conversation, message, generation=generation)
    assert_refused(result, family, reason)
    compute.assert_not_called()
    generator.assert_not_awaited()
    decrypt.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_cross_account_conversation_never_decrypts_or_charges_either_owner(
    env, monkeypatch, family
):
    conversation, message, generator = await prepare(env, monkeypatch)
    before = await row(env, conversation)
    decrypt = Mock(side_effect=AssertionError("wrong-owner conversation must not decrypt"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    compute = Mock(side_effect=AssertionError("wrong-owner job must not admit compute"))
    monkeypatch.setattr(jobs, "account_compute", compute)
    result = await run(env, family, conversation, message, owner=env.bob)
    assert result == (None if family == "generate_title" else {"status": "not_found"})
    assert (
        await env.memory.save_generated_conversation_title(
            conversation,
            title="Forbidden title",
            expected_title="New conversation",
            captured_generation=CapturedGeneration(env.bob, 7),
        )
        is False
    )
    assert await row(env, conversation) == before
    compute.assert_not_called()
    generator.assert_not_awaited()
    decrypt.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_cross_account_message_in_owned_conversation_never_decrypts(env, monkeypatch, family):
    conversation, message, generator = await prepare(env, monkeypatch)
    # Independent FKs allow this historical corruption; predicates must not.
    await env.pool.execute("UPDATE messages SET user_id=$2 WHERE id=$1", message, env.bob)
    decrypt = Mock(side_effect=AssertionError("foreign message must not decrypt"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    result = await run(env, family, conversation, message)
    assert_refused(result, family, "no_messages")
    generator.assert_not_awaited()
    decrypt.assert_not_called()


@pytest.mark.asyncio
async def test_first_message_from_other_owned_conversation_is_not_a_title_source(env, monkeypatch):
    conversation, message, generator = await prepare(env, monkeypatch)
    other = await env.memory.create_conversation(env.alice)
    await env.pool.execute(
        "UPDATE messages SET conversation_id=$2 WHERE id=$1", message, other["id"]
    )
    result = await run(env, "generate_title", conversation, message)
    assert result is None
    generator.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_fence_during_compute_admission_refuses_before_model(env, monkeypatch, family):
    conversation, message, generator = await prepare(env, monkeypatch)

    @asynccontextmanager
    async def changed_compute(*args, **kwargs):
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
        )
        yield

    monkeypatch.setattr(jobs, "account_compute", changed_compute)
    result = await run(env, family, conversation, message)
    assert_refused(result, family, "stale_generation")
    generator.assert_not_awaited()
    assert (await row(env, conversation))["title"] == "New conversation"


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("change", ["generation", "suspend", "rename", "lock", "owner"])
async def test_inflight_model_cannot_commit_past_account_or_title_change(
    env, monkeypatch, family, change
):
    conversation, message, _ = await prepare(env, monkeypatch)
    started, resume = asyncio.Event(), asyncio.Event()

    async def model(messages):
        started.set()
        await resume.wait()
        return "Generated title"

    monkeypatch.setattr(jobs, "generate_conversation_title", model)
    task = asyncio.create_task(run(env, family, conversation, message))
    try:
        await asyncio.wait_for(started.wait(), 10)
        if change in {"generation", "suspend"}:
            async with env.pool.acquire() as fence, fence.transaction():
                await fence.fetchrow(
                    "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                    env.alice,
                )
                await fence.execute(
                    "UPDATE entitlement_accounts SET content_generation=8,status=$2 WHERE user_id=$1",
                    env.alice,
                    "suspended" if change == "suspend" else "active",
                )
            reason = "account_restricted" if change == "suspend" else "stale_generation"
        else:
            if change == "owner":
                await env.pool.execute(
                    "UPDATE conversations SET user_id=$2 WHERE id=$1", conversation, env.bob
                )
            else:
                await env.memory.update_conversation(
                    conversation,
                    title="Manual title" if change == "rename" else None,
                    title_locked=change == "lock",
                )
            reason = "title_changed_or_locked"
        before = await row(env, conversation)
        resume.set()
        assert_refused(await asyncio.wait_for(task, 10), family, reason)
        assert await row(env, conversation) == before
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cause_only", [False, True])
async def test_fence_first_actual_writer_waits_then_refuses_with_fresh_state(
    env, monkeypatch, cause_only
):
    conversation, _, _ = await prepare(env, monkeypatch)
    before = await row(env, conversation)
    async with env.pool.acquire() as writer, env.pool.acquire() as fence:
        saver = bound_store(env, writer)
        tx = fence.transaction()
        await tx.start()
        await fence.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", env.alice
        )
        if cause_only:
            await fence.execute(
                "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
            )
        else:
            await fence.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )
        task = asyncio.create_task(
            saver.save_generated_conversation_title(
                conversation,
                title="Generated title",
                expected_title="New conversation",
                captured_generation=CapturedGeneration(env.alice, 7),
            )
        )
        try:
            await blocked(env.pool, writer.get_server_pid(), fence.get_server_pid())
            await tx.commit()
            with pytest.raises(
                WriteGuardRefusal, match="inconsistent_state" if cause_only else "stale_generation"
            ):
                await asyncio.wait_for(task, 10)
            assert not writer.is_in_transaction()
            assert await row(env, conversation) == before
        finally:
            if fence.is_in_transaction():
                await tx.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def bound_store(env, connection):
    class BoundPool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    return MemoryStore(cast(asyncpg.Pool, BoundPool()), env.memory.encryption)


@pytest.mark.asyncio
async def test_writer_first_account_lock_precedes_content_wait_and_survives_commit(
    env, monkeypatch
):
    conversation, _, _ = await prepare(env, monkeypatch)
    async with (
        env.pool.acquire() as writer,
        env.pool.acquire() as editor,
        env.pool.acquire() as fence,
    ):
        edit_tx = editor.transaction()
        await edit_tx.start()
        await editor.fetchrow("SELECT id FROM conversations WHERE id=$1 FOR UPDATE", conversation)
        saver = bound_store(env, writer)
        saved = asyncio.create_task(
            saver.save_generated_conversation_title(
                conversation,
                title="Generated title",
                expected_title="New conversation",
                captured_generation=CapturedGeneration(env.alice, 7),
            )
        )
        control = None
        try:
            await blocked(env.pool, writer.get_server_pid(), editor.get_server_pid())

            async def fence_after_commit():
                async with fence.transaction():
                    await fence.fetchrow(
                        "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                        env.alice,
                    )
                    # Fence can acquire only after actual title commit, not scope exit.
                    assert (
                        await fence.fetchval(
                            "SELECT title FROM conversations WHERE id=$1", conversation
                        )
                        == "Generated title"
                    )
                    await fence.execute(
                        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1",
                        env.alice,
                    )

            control = asyncio.create_task(fence_after_commit())
            await blocked(env.pool, fence.get_server_pid(), writer.get_server_pid())
            await edit_tx.commit()
            assert await asyncio.wait_for(saved, 10) is True
            await asyncio.wait_for(control, 10)
        finally:
            if editor.is_in_transaction():
                await edit_tx.rollback()
            for task in (saved, control):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(saved, return_exceptions=True)
            if control is not None:
                await asyncio.gather(control, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["input", "save"])
@pytest.mark.parametrize("family", FAMILIES)
async def test_cancel_actual_guard_wait_rolls_back_and_connection_is_reusable(
    env, monkeypatch, phase, family
):
    conversation, message, _ = await prepare(env, monkeypatch)
    before = await row(env, conversation)
    async with env.pool.acquire() as writer, env.pool.acquire() as holder:
        store = bound_store(env, writer)
        monkeypatch.setattr(env, "memory", store)
        tx = holder.transaction()
        await tx.start()
        if phase == "input":
            await holder.fetchrow(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", env.alice
            )
        else:
            await holder.fetchrow(
                "SELECT id FROM conversations WHERE id=$1 FOR UPDATE", conversation
            )
        task = asyncio.create_task(run(env, family, conversation, message))
        try:
            await blocked(env.pool, writer.get_server_pid(), holder.get_server_pid())
            task.cancel()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)
            assert not writer.is_in_transaction()
            assert await writer.fetchval("SELECT 1") == 1
            await tx.rollback()
            async with holder.transaction():
                await holder.fetchrow(
                    "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                    env.alice,
                )
                await holder.fetchrow(
                    "SELECT id FROM conversations WHERE id=$1 FOR UPDATE NOWAIT", conversation
                )
            assert await row(env, conversation) == before
        finally:
            if holder.is_in_transaction():
                await tx.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_failed_real_commit_is_not_reported_as_saved(env, monkeypatch, family):
    conversation, message, _ = await prepare(env, monkeypatch)
    before = await row(env, conversation)
    await env.pool.execute("""
        CREATE FUNCTION title_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic title commit failure'; END $$;
        CREATE CONSTRAINT TRIGGER title_commit_failure AFTER UPDATE ON conversations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION title_commit_failure();
    """)
    result = await run(env, family, conversation, message)
    assert result == (
        None if family == "generate_title" else {"status": "error", "reason": "persist_failed"}
    )
    assert await row(env, conversation) == before
    async with env.pool.acquire() as conn, conn.transaction():
        await conn.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT", env.alice
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("fence_after_source", [False, True])
async def test_both_http_producers_capture_before_source_and_carry_original_generation(
    api, monkeypatch, durable, fence_after_source
):
    env = api.env
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id,content_generation) VALUES($1,7)", env.alice
    )
    captured = []
    original_capture = main.capture_generation

    async def capture(conn, owner):
        value = await original_capture(conn, owner)
        captured.append(value)
        return value

    monkeypatch.setattr(main, "capture_generation", capture)
    source_target = type(env.tasks) if durable else env.memory
    source_name = "accept" if durable else "insert_message"
    original_source = getattr(source_target, source_name)

    async def source(*args, **kwargs):
        assert captured == [CapturedGeneration(env.alice, 7)]
        result = await original_source(*args, **kwargs)
        if fence_after_source:
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )
        return result

    monkeypatch.setattr(source_target, source_name, source)

    async def stream(**kwargs):
        yield 'event: done\ndata: {"ok":true}\n\n'

    # Observe/execute other chat output is outside this family. Keep real title
    # producers, account capture, source persistence and worker SQL end to end.
    monkeypatch.setattr(main, "stream_sse_chat", stream)
    monkeypatch.setattr(main, "_observe_task_response", lambda *a, **k: StreamingResponse(stream()))
    headers = {"X-Daemon-Client-Features": "task-cancel, task-reset" if durable else ""}
    response = await api.client.post(
        "/chat", json={"message": "Fictional title source"}, headers=headers
    )
    assert response.status_code == 200
    titles = [
        (args, kwargs) for name, args, kwargs in api.redis.enqueued if name == "generate_title"
    ]
    assert len(titles) == 1
    args, kwargs = titles[0]
    payload = {name: kwargs[name] for name in ("origin_account_id", "origin_account_generation")}
    assert payload == {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    assert captured == [CapturedGeneration(env.alice, 7)]
    if durable:
        # Account capture is independent of the accepted task's attempt counter.
        assert await env.pool.fetchval("SELECT content_generation FROM tasks LIMIT 1") == 0

    @asynccontextmanager
    async def compute(*args, **kwargs):
        yield

    generator = AsyncMock(return_value="Generated title")
    monkeypatch.setattr(jobs, "account_compute", compute)
    monkeypatch.setattr(jobs, "generate_conversation_title", generator)
    decoded = deserialize_job(serialize_job("generate_title", args, payload, 1, 1_700_000_000_000))
    result = await jobs.generate_title(api.ctx(), *decoded.args, **decoded.kwargs)
    assert result == (None if fence_after_source else "Generated title")
    if fence_after_source:
        generator.assert_not_awaited()
    else:
        generator.assert_awaited_once()
