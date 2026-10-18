"""J1c summary-family proofs on disposable PostgreSQL; no paid inference.

Account/owner qualification, encryption, cursor SQL, transactions and wait-for
edges are real. Only compute/model boundaries are scripted.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import asyncpg
import pytest
from arq.jobs import deserialize_job, serialize_job
from arq.worker import Retry

from orchestrator.memory import summary, summarization
from orchestrator.memory.extraction import ExtractedFact, ExtractionOutcome, process_extraction
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal
from orchestrator.worker import jobs
from tests.durable_tasks_support import durable_env_fixture
from tests.test_account_write_guard import blocked
from tests.test_title_account_guard_postgres import bound_store
from tests.test_task_api_postgres import api as api

env = durable_env_fixture()
FAMILIES = ["inline", "queued"]


async def prepare(env, monkeypatch, count=1):
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id,content_generation) VALUES($1,7),($2,7)",
        env.alice,
        env.bob,
    )
    conv = await env.memory.create_conversation(env.alice, title="Summary test")
    for index in range(count):
        await env.pool.execute(
            "INSERT INTO messages(conversation_id,user_id,role,content,status,created_at) "
            "VALUES($1,$2,'user',$3,'complete','2000-01-01'::timestamptz+$4*interval '1 second')",
            conv["id"],
            env.alice,
            env.memory.encryption.encrypt(f"Fictional input {index}"),
            index,
        )

    @asynccontextmanager
    async def compute(pool, owner, **kwargs):
        assert pool is env.pool and owner == env.alice
        # Preflight must release account SHARE before admission.
        async with pool.acquire() as conn, conn.transaction():
            await conn.fetchrow(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT", owner
            )
        yield

    generator = AsyncMock(return_value="Updated summary.")
    monkeypatch.setattr(jobs, "account_compute", compute)
    monkeypatch.setattr(summarization, "generate_summary", generator)

    async def inline_model(**kwargs):
        await generator(kwargs)
        return SimpleNamespace(choices=[{"message": {"content": "Updated summary."}}])

    monkeypatch.setattr(summary, "guarded_completion", inline_model)
    return conv["id"], generator


async def run(env, family, conv, *, generation=7, owner=None, queue=None) -> Any:
    captured = CapturedGeneration(owner or env.alice, generation)
    if family == "inline":
        return await summary._generate_or_update_summary_result(
            conv, env.memory, captured_generation=captured
        )
    return await jobs.generate_summary_job(
        {"store": env.memory, "db_pool": env.pool, "redis": queue},
        str(conv),
        True,
        origin_account_id=str(captured.account_id),
        origin_account_generation=captured.generation,
    )


async def row(env, conv):
    return dict(
        await env.pool.fetchrow(
            "SELECT summary,summary_updated_at,metadata,updated_at,last_activity_at "
            "FROM conversations WHERE id=$1",
            conv,
        )
    )


async def metadata(env, conv):
    value = (await row(env, conv))["metadata"]
    return json.loads(value) if isinstance(value, str) else value


def refused(result, family, reason):
    assert result == (
        summary.SummaryUpdateResult(None, False)
        if family == "inline"
        else {"status": "skipped", "reason": reason}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_current_nonzero_origin_commits_summary_and_cursor(env, monkeypatch, family):
    conv, model = await prepare(env, monkeypatch, 3)
    result = await run(env, family, conv)
    assert (result.summary if family == "inline" else result["status"]) == (
        "Updated summary." if family == "inline" else "success"
    )
    saved = await row(env, conv)
    assert saved["summary"] == "Updated summary." and saved["summary_updated_at"] is not None
    assert (await metadata(env, conv))["last_summarized_msg_count"] == 3
    assert (await metadata(env, conv))["summary_continuation_pending"] is False
    model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prior,cursor",
    [
        (None, 2),
        ("", 2),
        ("   ", 2),
        ("Prior summary", 4),
        ("Prior summary", -1),
        ("Prior summary", True),
        ("Prior summary", 1.5),
        ("Prior summary", "2"),
        ("Prior summary", None),
    ],
)
async def test_invalid_baseline_replays_from_zero_through_actual_guarded_reader(
    env, monkeypatch, prior, cursor
):
    conv, _ = await prepare(env, monkeypatch, 3)
    await env.pool.execute(
        "UPDATE conversations SET summary=$2,metadata=$3::jsonb WHERE id=$1",
        conv,
        prior,
        json.dumps({"last_summarized_msg_count": cursor, "summary_continuation_pending": True}),
    )
    inputs = await env.memory.read_conversation_summary_inputs(
        conv, captured_generation=CapturedGeneration(env.alice, 7), limit=20
    )
    assert inputs is not None
    assert inputs["persisted_baseline"] == 0
    assert [message["content"] for message in inputs["messages"]] == [
        f"Fictional input {index}" for index in range(3)
    ]
    await run(env, "inline", conv)
    assert (await metadata(env, conv))["last_summarized_msg_count"] == 3
    assert (await row(env, conv))["summary"] == "Updated summary."


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("restriction", ["stale", "suspended", "cause", "operation", "missing"])
async def test_restricted_origin_never_decrypts_or_writes(
    env, monkeypatch, tmp_path, family, restriction
):
    conv, model = await prepare(env, monkeypatch)
    generation = 7
    if restriction == "stale":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
        )
        reason = "stale_generation"
    elif restriction == "suspended":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )
        reason = "account_restricted"
    elif restriction == "cause":
        await env.pool.execute(
            "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
        )
        reason = "inconsistent_state"
    elif restriction == "operation":
        from tests.test_account_control_persistence import persist, settings
        from tests.deletion_journal_support import request
        from orchestrator.services.deletion_journal.store import initialize
        from orchestrator.services.deletion_journal.barrier import require_recovery_barrier

        directory = (tmp_path / "journal", uuid4())
        initialize(*directory)
        await require_recovery_barrier(env.pool, settings(*directory))
        command = request(env.alice)
        command = replace(command, fields=replace(command.fields, generation=8))
        await persist(env, directory, command, uuid4())
        generation = 8
        reason = "open_operation"
    else:
        await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", env.alice)
        reason = "missing_account"
    before = await row(env, conv)
    decrypt = Mock(side_effect=AssertionError("Refused summaries cannot decrypt"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    refused(await run(env, family, conv, generation=generation), family, reason)
    model.assert_not_awaited()
    decrypt.assert_not_called()
    assert await row(env, conv) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_foreign_conversation_and_foreign_message_ranks_are_excluded(
    env, monkeypatch, family
):
    conv, model = await prepare(env, monkeypatch, 3)
    before = await row(env, conv)
    result = await run(env, family, conv, owner=env.bob)
    assert (result.summary if family == "inline" else result["status"]) == (
        None if family == "inline" else "not_found"
    )
    assert await row(env, conv) == before
    model.assert_not_awaited()
    # A foreign streaming row must neither leak text nor block/shift owner ranks.
    await env.pool.execute(
        "INSERT INTO messages(conversation_id,user_id,role,content,status,created_at) "
        "VALUES($1,$2,'user','not encrypted','streaming','1999-01-01')",
        conv,
        env.bob,
    )
    await run(env, family, conv)
    assert (await metadata(env, conv))["last_summarized_msg_count"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_streaming_hole_and_terminal_rows_do_not_skip_or_replay(env, monkeypatch, family):
    conv, model = await prepare(env, monkeypatch, 5)
    ids = await env.pool.fetch(
        "SELECT id FROM messages WHERE conversation_id=$1 ORDER BY created_at,id", conv
    )
    await env.pool.execute("UPDATE messages SET status='cancelled' WHERE id=$1", ids[1]["id"])
    await env.pool.execute("UPDATE messages SET status='error' WHERE id=$1", ids[2]["id"])
    await env.pool.execute("UPDATE messages SET status='streaming' WHERE id=$1", ids[3]["id"])
    await run(env, family, conv)
    assert (await metadata(env, conv))["last_summarized_msg_count"] == 1
    assert (await metadata(env, conv))["summary_continuation_pending"] is True
    await run(env, family, conv)  # hole is not proof of drainage
    assert (await metadata(env, conv))["summary_continuation_pending"] is True
    assert model.await_count == 1
    await env.pool.execute("UPDATE messages SET status='complete' WHERE id=$1", ids[3]["id"])
    await run(env, family, conv)
    assert (await metadata(env, conv))["last_summarized_msg_count"] == 3
    assert (await metadata(env, conv))["summary_continuation_pending"] is False
    last = model.await_args
    text = str(last)
    assert "Fictional input 3" in text and "Fictional input 4" in text
    assert "Fictional input 0" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_serialized_retry_never_recaptures_after_reset(env, monkeypatch, family):
    conv, model = await prepare(env, monkeypatch, 22)
    payload = {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    wire = serialize_job("generate_summary_job", (str(conv), True), payload, 1, 1700000000000)
    model.side_effect = Retry(defer=0)
    if family == "queued":
        with pytest.raises(Retry):
            await run(env, family, conv)
    else:
        assert (await run(env, family, conv)).summary is None
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
    )
    before = await row(env, conv)
    decoded = deserialize_job(wire)
    retry = deserialize_job(
        serialize_job(decoded.function, decoded.args, decoded.kwargs, 2, 1700000000000)
    )
    assert retry.kwargs == payload
    decrypt = Mock(side_effect=AssertionError("Stale retry cannot decrypt"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    model.reset_mock()
    refused(
        await run(env, family, conv, generation=retry.kwargs["origin_account_generation"]),
        family,
        "stale_generation",
    )
    model.assert_not_awaited()
    decrypt.assert_not_called()
    assert await row(env, conv) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reset", "suspend", "owner"])
async def test_post_compute_admission_recheck_prevents_model(env, monkeypatch, change):
    conv, model = await prepare(env, monkeypatch)

    @asynccontextmanager
    async def admission(*args, **kwargs):
        if change == "owner":
            await env.pool.execute("UPDATE conversations SET user_id=$2 WHERE id=$1", conv, env.bob)
        else:
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8,status=$2 WHERE user_id=$1",
                env.alice,
                "suspended" if change == "suspend" else "active",
            )
        yield

    monkeypatch.setattr(jobs, "account_compute", admission)
    if change == "owner":
        with pytest.raises(Retry):
            await run(env, "queued", conv)
    else:
        refused(
            await run(env, "queued", conv),
            "queued",
            "account_restricted" if change == "suspend" else "stale_generation",
        )
    model.assert_not_awaited()
    assert (await row(env, conv))["summary"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("change", ["reset", "suspend", "competing", "cancel"])
async def test_inflight_compute_cannot_publish_stale_or_competing_result(
    env, monkeypatch, family, change
):
    conv, model = await prepare(env, monkeypatch)
    entered, resume = asyncio.Event(), asyncio.Event()

    async def paused(*args, **kwargs):
        # No account/content locks may span provider work.
        async with env.pool.acquire() as conn, conn.transaction():
            await conn.fetchrow(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                env.alice,
            )
        entered.set()
        await resume.wait()
        return "Updated summary."

    model.side_effect = paused
    task = asyncio.create_task(run(env, family, conv))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if change == "cancel":
            task.cancel()
        elif change == "competing":
            assert await env.memory.update_conversation_summary(
                conv,
                "Winner",
                expected_summary_updated_at=None,
                summarized_message_count=1,
                summary_continuation_pending=True,
                captured_generation=CapturedGeneration(env.alice, 7),
            )
        else:
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8,status=$2 WHERE user_id=$1",
                env.alice,
                "suspended" if change == "suspend" else "active",
            )
        before = await row(env, conv)
        resume.set()
        if change == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        elif change == "competing":
            if family == "queued":
                with pytest.raises(Retry):
                    await task
            else:
                assert await task == summary.SummaryUpdateResult(None, True)
        else:
            refused(
                await task,
                family,
                "account_restricted" if change == "suspend" else "stale_generation",
            )
        assert await row(env, conv) == before
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_blank_compute_result_never_advances_real_summary_cursor(env, monkeypatch, family):
    conv, model = await prepare(env, monkeypatch, 22)
    before = await row(env, conv)
    model.return_value = "   "
    monkeypatch.setattr(
        summary,
        "guarded_completion",
        AsyncMock(return_value=SimpleNamespace(choices=[{"message": {"content": "   "}}])),
    )
    if family == "queued":
        with pytest.raises(Retry):
            await run(env, family, conv)
    else:
        assert (await run(env, family, conv)).summary is None
    assert await row(env, conv) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_failed_real_commit_preserves_every_summary_field(env, monkeypatch, family):
    conv, model = await prepare(env, monkeypatch, 101)
    before = await row(env, conv)
    await env.pool.execute("""
        CREATE FUNCTION summary_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic summary commit failure'; END $$;
        CREATE CONSTRAINT TRIGGER summary_commit_failure AFTER UPDATE ON conversations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION summary_commit_failure();
    """)
    if family == "queued":
        with pytest.raises(asyncpg.RaiseError):
            await run(env, family, conv)
    else:
        assert (await run(env, family, conv)).summary is None
    assert await row(env, conv) == before
    model.assert_awaited_once()
    async with env.pool.acquire() as conn, conn.transaction():
        await conn.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT", env.alice
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cause_only", [False, True])
async def test_fence_first_real_summary_writer_waits_then_refuses(env, monkeypatch, cause_only):
    conv, _ = await prepare(env, monkeypatch)
    before = await row(env, conv)
    async with env.pool.acquire() as writer, env.pool.acquire() as fence:
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
            bound_store(env, writer).update_conversation_summary(
                conv,
                "Stale",
                expected_summary_updated_at=None,
                summarized_message_count=1,
                summary_continuation_pending=True,
                captured_generation=CapturedGeneration(env.alice, 7),
            )
        )
        try:
            await blocked(env.pool, writer.get_server_pid(), fence.get_server_pid())
            await tx.commit()
            with pytest.raises(
                WriteGuardRefusal, match="inconsistent_state" if cause_only else "stale_generation"
            ):
                await task
            assert not writer.is_in_transaction()
            assert await row(env, conv) == before
        finally:
            if fence.is_in_transaction():
                await tx.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_writer_first_lock_is_account_before_conversation_through_commit(env, monkeypatch):
    conv, _ = await prepare(env, monkeypatch)
    async with (
        env.pool.acquire() as writer,
        env.pool.acquire() as editor,
        env.pool.acquire() as fence,
    ):
        tx = editor.transaction()
        await tx.start()
        await editor.fetchrow("SELECT id FROM conversations WHERE id=$1 FOR UPDATE", conv)
        task = asyncio.create_task(
            bound_store(env, writer).update_conversation_summary(
                conv,
                "Winner",
                expected_summary_updated_at=None,
                summarized_message_count=1,
                summary_continuation_pending=True,
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
                    saved = await fence.fetchrow(
                        "SELECT summary,metadata FROM conversations WHERE id=$1", conv
                    )
                    assert saved["summary"] == "Winner"
                    assert json.loads(saved["metadata"])["summary_continuation_pending"] is True
                    await fence.execute(
                        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1",
                        env.alice,
                    )

            control = asyncio.create_task(fence_after_commit())
            await blocked(env.pool, fence.get_server_pid(), writer.get_server_pid())
            await tx.commit()
            assert await task is True
            await control
        finally:
            if editor.is_in_transaction():
                await tx.rollback()
            for pending in (task, control):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if control is not None:
                await asyncio.gather(control, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["inputs", "write", "clear"])
async def test_cancel_real_lock_wait_rolls_back_and_connection_reusable(env, monkeypatch, phase):
    conv, _ = await prepare(env, monkeypatch)
    before = await row(env, conv)
    async with env.pool.acquire() as writer, env.pool.acquire() as holder:
        tx = holder.transaction()
        await tx.start()
        await holder.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", env.alice
        )
        store = bound_store(env, writer)
        capture = CapturedGeneration(env.alice, 7)
        operation = (
            store.read_conversation_summary_inputs(conv, captured_generation=capture, limit=20)
            if phase == "inputs"
            else store.update_conversation_summary(
                conv,
                "X",
                expected_summary_updated_at=None,
                summarized_message_count=1,
                captured_generation=capture,
            )
            if phase == "write"
            else store.clear_finished_summary_continuation(
                conv,
                captured_generation=capture,
                expected_summary_updated_at=None,
                expected_baseline=0,
            )
        )
        task = asyncio.create_task(operation)
        try:
            await blocked(env.pool, writer.get_server_pid(), holder.get_server_pid())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not writer.is_in_transaction()
            assert await writer.fetchval("SELECT 1") == 1
            assert await row(env, conv) == before
        finally:
            await tx.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_actual_commit_rolls_back_summary_cursor_and_pending(env, monkeypatch):
    from orchestrator.memory.store import MemoryStore

    conv, _ = await prepare(env, monkeypatch)
    before = await row(env, conv)
    await env.pool.execute("""
        CREATE FUNCTION summary_commit_wait() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_advisory_xact_lock(91234567); RETURN NEW; END $$;
        CREATE CONSTRAINT TRIGGER summary_commit_wait AFTER UPDATE ON conversations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION summary_commit_wait();
    """)
    acquired = asyncio.get_running_loop().create_future()

    class ObservedPool:
        @asynccontextmanager
        async def acquire(self):
            # Retain the REAL pool release/cancellation cleanup. A borrowed
            # BoundPool omits reset and cannot prove the production commit path.
            async with env.pool.acquire() as connection:
                acquired.set_result(connection.get_server_pid())
                yield connection

    store = MemoryStore(cast(asyncpg.Pool, ObservedPool()), env.memory.encryption)
    async with env.pool.acquire() as holder:
        await holder.execute("SELECT pg_advisory_lock(91234567)")
        task = asyncio.create_task(
            store.update_conversation_summary(
                conv,
                "Uncommitted",
                expected_summary_updated_at=None,
                summarized_message_count=1,
                summary_continuation_pending=True,
                captured_generation=CapturedGeneration(env.alice, 7),
            )
        )
        try:
            writer_pid = await acquired
            await blocked(env.pool, writer_pid, holder.get_server_pid())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with env.pool.acquire() as reused:
                assert reused.get_server_pid() == writer_pid
                assert not reused.is_in_transaction()
                assert await reused.fetchval("SELECT 1") == 1
            assert await row(env, conv) == before
            async with holder.transaction():
                await holder.fetchrow(
                    "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                    env.alice,
                )
        finally:
            await holder.execute("SELECT pg_advisory_unlock(91234567)")
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_full_batch_and_repeated_enqueue_failure_preserve_recovery_flag(env, monkeypatch):
    conv, _ = await prepare(env, monkeypatch, 101)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    enqueue = AsyncMock(side_effect=RuntimeError("synthetic enqueue failure"))
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    with pytest.raises(Retry):
        await run(env, "queued", conv, queue=queue)
    assert (await metadata(env, conv))["last_summarized_msg_count"] == 100
    assert (await metadata(env, conv))["summary_continuation_pending"] is True
    # Extraction retry has no fact inputs, yet must repeatedly recover pending.
    for _ in range(2):
        with pytest.raises(Retry):
            await jobs.extract_memories(
                {"store": env.memory, "db_pool": env.pool, "redis": queue},
                env.alice,
                conv,
                origin_account_id=str(env.alice),
                origin_account_generation=7,
            )
        assert (await metadata(env, conv))["summary_continuation_pending"] is True
    enqueue.side_effect = None
    enqueue.return_value = object()
    # Enqueue itself does not clear recovery; only the actual continuation does.
    last = await env.pool.fetchrow(
        "SELECT id,created_at FROM messages WHERE conversation_id=$1 ORDER BY created_at DESC,id DESC LIMIT 1",
        conv,
    )
    await env.memory.log_extraction(
        env.alice,
        "",
        conversation_id=conv,
        dedup_results={"last_message_id": str(last["id"]), "cursor_checkpoint": True},
        last_message_observed_at=last["created_at"],
    )
    previous_enqueues = enqueue.await_count
    result = await jobs.extract_memories(
        {"store": env.memory, "db_pool": env.pool, "redis": queue},
        env.alice,
        conv,
        origin_account_id=str(env.alice),
        origin_account_generation=7,
    )
    assert result["status"] == "ok" and enqueue.await_count == previous_enqueues + 1
    assert (await metadata(env, conv))["summary_continuation_pending"] is True
    assert enqueue.await_args is not None
    payload = enqueue.await_args.kwargs["kwargs"]
    assert payload == {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    await jobs.generate_summary_job(
        {"store": env.memory, "db_pool": env.pool}, str(conv), True, **payload
    )
    assert (await metadata(env, conv))["last_summarized_msg_count"] == 101
    assert (await metadata(env, conv))["summary_continuation_pending"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", ["absent", "malformed", "foreign", "stale"])
async def test_legacy_and_refused_extraction_preserve_summary_without_fact_checkpoint(
    env, monkeypatch, capture
):
    conv, _ = await prepare(env, monkeypatch)
    await env.memory.update_conversation_summary(
        conv,
        "Existing",
        expected_summary_updated_at=None,
        summarized_message_count=0,
        summary_continuation_pending=True,
        captured_generation=CapturedGeneration(env.alice, 7),
    )
    before = await row(env, conv)
    if capture == "stale":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
        )
    payload = (
        {}
        if capture == "absent"
        else {
            "origin_account_id": str(env.bob if capture == "foreign" else env.alice),
            "origin_account_generation": True if capture == "malformed" else 7,
        }
    )
    monkeypatch.setattr(
        "orchestrator.memory.extraction.extract_facts_from_text",
        AsyncMock(
            return_value=ExtractionOutcome(
                facts=[],
                raw_count=0,
                calibrated_count=0,
                rejected_count=0,
                slot_coverage=0,
                succeeded=True,
            )
        ),
    )
    # Invalid original captures now refuse the entire queued extraction family.
    result = await jobs.extract_memories(
        {"store": env.memory, "db_pool": env.pool},
        env.alice,
        conv,
        messages_json=json.dumps([{"role": "user", "content": "Fictional short fact input"}]),
        **payload,
    )
    assert result["status"] == "skipped"
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_extraction_log WHERE conversation_id=$1", conv
        )
        == 0
    )
    assert await row(env, conv) == before


@pytest.mark.asyncio
async def test_noop_clear_cannot_erase_competing_summary_version(env, monkeypatch):
    conv, _ = await prepare(env, monkeypatch)
    capture = CapturedGeneration(env.alice, 7)
    assert await env.memory.update_conversation_summary(
        conv,
        "First",
        expected_summary_updated_at=None,
        summarized_message_count=1,
        summary_continuation_pending=True,
        captured_generation=capture,
    )
    observed = await row(env, conv)
    assert await env.memory.update_conversation_summary(
        conv,
        "Second",
        expected_summary_updated_at=observed["summary_updated_at"],
        summarized_message_count=1,
        summary_continuation_pending=True,
        captured_generation=capture,
    )
    before = await row(env, conv)
    assert not await env.memory.clear_finished_summary_continuation(
        conv,
        captured_generation=capture,
        expected_summary_updated_at=observed["summary_updated_at"],
        expected_baseline=1,
    )
    assert await row(env, conv) == before
    await run(env, "queued", conv)
    assert (await metadata(env, conv))["summary_continuation_pending"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_actual_fact_write_preserved_and_inline_receives_capture(env, monkeypatch, legacy):
    conv, model = await prepare(env, monkeypatch, 22)
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "synthetic-summary-pepper")
    monkeypatch.setattr(
        "orchestrator.memory.extraction.extract_facts_from_text",
        AsyncMock(
            return_value=ExtractionOutcome(
                facts=[ExtractedFact("User enjoys fictional purple kites.", "preference", 0.95)],
                raw_count=1,
                calibrated_count=1,
                rejected_count=0,
                slot_coverage=0,
            )
        ),
    )
    result = await process_extraction(
        env.memory,
        env.alice,
        conv,
        "Fictional short fact input",
        summary_generation=None if legacy else CapturedGeneration(env.alice, 7),
    )
    assert result[0] is True and len(result[1]) == 1
    assert await env.pool.fetchval("SELECT count(*) FROM memories WHERE user_id=$1", env.alice) == 1
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_extraction_log WHERE conversation_id=$1", conv
        )
        == 1
    )
    if legacy:
        model.assert_not_awaited()
        assert (await row(env, conv))["summary"] is None and result[2] is False
    else:
        model.assert_awaited_once()
        assert (await metadata(env, conv))["last_summarized_msg_count"] == 20
        assert (await metadata(env, conv))["summary_continuation_pending"] is True and result[
            2
        ] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [7, None])
async def test_extraction_chunk_continuation_preserves_origin_without_recapture(
    env, monkeypatch, generation
):
    conv, _ = await prepare(env, monkeypatch, 2)
    # Force two bounded chunks, preserving existing reference/source-version payload.
    for index in range(2):
        await env.pool.execute(
            "UPDATE messages SET content=$2 WHERE id=(SELECT id FROM messages WHERE conversation_id=$1 ORDER BY created_at,id OFFSET $3 LIMIT 1)",
            conv,
            env.memory.encryption.encrypt("Fictional data " * 30),
            index,
        )
    original_chunker = jobs._chunk_messages_for_extraction
    monkeypatch.setattr(
        jobs,
        "_chunk_messages_for_extraction",
        lambda parsed, raw: original_chunker(parsed, raw, max_chars=500),
    )
    proc = AsyncMock(return_value=(True, [], False))
    monkeypatch.setattr(jobs, "process_extraction", proc)
    enqueue = AsyncMock(return_value=object())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    payload = {
        "origin_account_id": str(env.alice) if generation is not None else None,
        "origin_account_generation": generation,
    }
    result = await jobs.extract_memories(
        {"store": env.memory, "db_pool": env.pool, "redis": queue}, env.alice, conv, **payload
    )
    if generation is None:
        assert result["status"] == "skipped"
        proc.assert_not_awaited()
        enqueue.assert_not_awaited()
        return
    assert proc.await_count == 1
    assert proc.await_args is not None
    assert proc.await_args.kwargs["summary_generation"] == (
        CapturedGeneration(env.alice, 7) if generation is not None else None
    )
    assert enqueue.await_args is not None
    assert enqueue.await_args.args[1] == "extract_memories"
    assert enqueue.await_args.kwargs["kwargs"] == payload
    refs = json.loads(enqueue.await_args.kwargs["args"][2])
    assert "_extraction_refs" in refs


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [None, True, -1, 2**63, "7"])
async def test_malformed_queued_capture_refuses_before_any_input_or_compute(
    env, monkeypatch, generation
):
    conv, model = await prepare(env, monkeypatch)
    decrypt = Mock(side_effect=AssertionError("Invalid capture cannot decrypt"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    compute = Mock(side_effect=AssertionError("Invalid capture cannot admit"))
    monkeypatch.setattr(jobs, "account_compute", compute)
    result = await jobs.generate_summary_job(
        {"store": env.memory, "db_pool": env.pool},
        str(conv),
        True,
        origin_account_id=str(env.alice),
        origin_account_generation=generation,
    )
    assert result == {"status": "skipped", "reason": "invalid_capture"}
    decrypt.assert_not_called()
    compute.assert_not_called()
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("reset_at", ["never", "acceptance", "completion"])
async def test_http_acceptance_and_actual_stream_preserve_original_summary_origin(
    api, monkeypatch, durable, reset_at
):
    from orchestrator import main
    from orchestrator.tasks import runner

    env = api.env
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id,content_generation) VALUES($1,7)", env.alice
    )
    captures = []
    original_capture = main.capture_generation

    async def capture(conn, owner):
        value = await original_capture(conn, owner)
        captures.append(value)
        return value

    monkeypatch.setattr(main, "capture_generation", capture)
    source_target = type(env.tasks) if durable else env.memory
    source_name = "accept" if durable else "insert_message"
    original_source = getattr(source_target, source_name)

    async def source(*args, **kwargs):
        assert captures == [CapturedGeneration(env.alice, 7)]
        result = await original_source(*args, **kwargs)
        if reset_at == "acceptance":
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )
        return result

    monkeypatch.setattr(source_target, source_name, source)
    request_task = asyncio.create_task(
        api.client.post(
            "/chat",
            json={"message": "Fictional summary source"},
            headers={"X-Daemon-Client-Features": "task-cancel, task-reset" if durable else ""},
        )
    )
    try:
        if durable:
            async with asyncio.timeout(10):
                while not any(name == "run_chat_task" for name, _, _ in api.redis.enqueued):
                    await asyncio.sleep(0.01)
            task_id = next(
                args[0] for name, args, _ in api.redis.enqueued if name == "run_chat_task"
            )
            task_row = await env.pool.fetchrow(
                "SELECT input_ciphertext,content_generation FROM tasks WHERE id=$1", UUID(task_id)
            )
            stored = env.tasks._open(task_row["input_ciphertext"])
            assert (
                stored["origin_account_id"] == stored["summary_origin_account_id"] == str(env.alice)
            )
            assert (
                stored["origin_account_generation"]
                == stored["summary_origin_account_generation"]
                == 7
            )
            assert task_row["content_generation"] == 0
            if reset_at == "acceptance":

                async def forbidden(*args, **kwargs):
                    pytest.fail("revoked queued work cannot execute or settle lost holds")

                monkeypatch.setattr(runner, "_execute", forbidden)
                monkeypatch.setattr(runner, "settle_lost_attempt_holds", forbidden)
                assert await runner.run_chat_task(api.ctx(), task_id) == "failed"
                saved = await env.pool.fetchrow("SELECT * FROM tasks WHERE id=$1", UUID(task_id))
                assert saved["terminal_code"] == "task_generation_stale"
                assert saved["attempt_count"] == saved["lease_epoch"] == 0
                assert saved["input_ciphertext"] == task_row["input_ciphertext"]
                assert (await asyncio.wait_for(request_task, 15)).status_code == 200
                assert captures == [CapturedGeneration(env.alice, 7)]
                assert not any(name == "extract_memories" for name, _, _ in api.redis.enqueued)
                return
            # Inspect the actual stored input via the same decrypt method as claims.
            claim = await env.tasks.claim(UUID(task_id), worker_id="abandoned", lease_s=45)
            assert claim is not None
            assert claim.task_input["summary_origin_account_id"] == str(env.alice)
            assert claim.task_input["summary_origin_account_generation"] == 7
            assert task_row["content_generation"] == 0
            # Simulate abandoned pre-compute claim, then real recovery/retry.
            from tests.durable_tasks_support import expire_lease

            await expire_lease(env, claim.task_id)
            assert await runner.run_chat_task(api.ctx(), task_id) == "completed"
        response = await asyncio.wait_for(request_task, 15)
        assert response.status_code == 200
        if reset_at == "completion":
            # Preserve the downstream stale-summary guard proof: this work was
            # legitimately queued BEFORE revocation, unlike an old queued task.
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )
        extracts = [
            (args, kwargs)
            for name, args, kwargs in api.redis.enqueued
            if name == "extract_memories"
        ]
        assert len(extracts) == 1
        args, kwargs = extracts[0]
        payload = {key: kwargs[key] for key in ("origin_account_id", "origin_account_generation")}
        assert payload == {"origin_account_id": str(env.alice), "origin_account_generation": 7}
        assert captures == [CapturedGeneration(env.alice, 7)]
        decoded = deserialize_job(
            serialize_job("extract_memories", args, payload, 2, 1700000000000)
        )
        # Stub fact extraction only; actual summary guard observes the original payload.
        await env.pool.execute(
            "UPDATE conversations SET metadata=jsonb_build_object('summary_continuation_pending',true) WHERE id=$1",
            UUID(args[1]),
        )
        pending = await env.memory.read_summary_continuation_pending(
            UUID(args[1]),
            captured_generation=CapturedGeneration(env.alice, 7 if reset_at == "never" else 8),
        )
        assert pending is True
        proc = AsyncMock(return_value=(True, [], False))
        monkeypatch.setattr(jobs, "process_extraction", proc)
        enqueue = AsyncMock(return_value=object())
        monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
        await jobs.extract_memories(api.ctx(), *decoded.args, **decoded.kwargs)
        assert (await metadata(env, UUID(args[1])))["summary_continuation_pending"] is True
        assert enqueue.await_count == (1 if reset_at == "never" else 0)
        if enqueue.await_count:
            assert enqueue.await_args is not None
            assert enqueue.await_args.kwargs["kwargs"] == payload
    finally:
        if not request_task.done():
            request_task.cancel()
        await asyncio.gather(request_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_actual_stream_duplicate_followup_carries_origin_or_legacy_skip(
    env, monkeypatch, legacy
):
    from orchestrator.config import Settings, ProviderConfig
    from orchestrator.daemon import stream_sse_chat
    from tests.redis_jobs_support import install_fake_enqueue
    from tests.test_streaming_message_persistence import FakeDedupQueue

    conv, _ = await prepare(env, monkeypatch)
    install_fake_enqueue(monkeypatch, "orchestrator.daemon.enqueue_account_job")

    async def completion(**kwargs):
        yield {"type": "content_delta", "content": "Fictional reply"}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", completion)

    async def connected():
        return False

    queue = FakeDedupQueue()
    for _ in range(2):
        async for _frame in stream_sse_chat(
            settings=cast(Any, Settings)(_env_file=None, mock_llm=False),
            provider_config=ProviderConfig(name="openrouter", model="synthetic"),
            system_prompt="Synthetic",
            user_message="Synthetic",
            request_id="req_summary",
            conversation_id=f"conv_{conv}",
            is_disconnected=connected,
            memory_store=env.memory,
            user_id=env.alice,
            conversation_uuid=conv,
            queue=queue,
            summary_generation=None if legacy else CapturedGeneration(env.alice, 7),
        ):
            pass
    attempts = [item for item in queue.attempts if item["args"][0] == "extract_memories"]
    assert len(attempts) == 3
    assert attempts[-1]["job_id"].endswith(":followup")
    for item in attempts:
        assert item["kwargs"] == (
            {} if legacy else {"origin_account_id": str(env.alice), "origin_account_generation": 7}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input_origin",
    [
        {},
        {"summary_origin_account_generation": True},
        {"summary_origin_account_id": "bad", "summary_origin_account_generation": 7},
    ],
)
async def test_resumed_legacy_task_blocks_without_upgrading_summary_origin(
    api, monkeypatch, input_origin
):
    from orchestrator.tasks import runner
    from tests.durable_tasks_support import accept_task, seed_entitlement

    # This module's generic env is intentionally uninitialized. Only this legacy
    # runner fixture needs historical initialized state; never repair via capture.
    await seed_entitlement(api.env.pool, api.env.alice)
    accepted = await accept_task(api.env)
    # Historical ciphertext fixture, not a NEW acceptance with caller authority.
    await api.env.pool.execute(
        "UPDATE tasks SET input_ciphertext=$2 WHERE id=$1",
        accepted.task_id,
        api.env.tasks._seal({"message": "hello", **input_origin}),
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("missing general origin cannot recapture, execute or settle")

    monkeypatch.setattr("orchestrator.main.capture_generation", forbidden)
    monkeypatch.setattr(runner, "_execute", forbidden)
    monkeypatch.setattr(runner, "settle_lost_attempt_holds", forbidden)

    async def persisted():
        return (
            await api.env.pool.fetch("SELECT * FROM tasks WHERE id=$1", accepted.task_id),
            await api.env.pool.fetch(
                "SELECT * FROM messages WHERE conversation_id=$1 ORDER BY id",
                accepted.conversation_id,
            ),
            await api.env.pool.fetch(
                "SELECT * FROM task_attempts WHERE task_id=$1", accepted.task_id
            ),
            await api.env.pool.fetch(
                "SELECT * FROM task_events WHERE task_id=$1 ORDER BY seq", accepted.task_id
            ),
        )

    before = await persisted()
    assert await runner.run_chat_task(api.ctx(), str(accepted.task_id)) == "task_origin_missing"
    assert await persisted() == before  # exact ciphertext, counters, events and status unchanged
    extracts = [kwargs for name, _, kwargs in api.redis.enqueued if name == "extract_memories"]
    assert extracts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [False, True])
async def test_durable_acceptance_keeps_origin_across_queue_outage(api, monkeypatch, revoke):
    from orchestrator import main
    from orchestrator.tasks import runner

    env = api.env
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id,content_generation) VALUES($1,7)", env.alice
    )
    monkeypatch.setattr(main.app.state.app_state, "redis", None)
    capture = AsyncMock(wraps=main.capture_generation)
    monkeypatch.setattr(main, "capture_generation", capture)
    request_task = asyncio.create_task(
        api.client.post("/chat", json={"message": "Fictional queue outage source"})
    )
    try:
        async with asyncio.timeout(10):
            task_row = None
            while task_row is None:
                task_row = await env.pool.fetchrow("SELECT id,input_ciphertext FROM tasks LIMIT 1")
                if task_row is None:
                    await asyncio.sleep(0.01)
        stored = env.tasks._open(task_row["input_ciphertext"])
        assert stored["summary_origin_account_id"] == str(env.alice)
        assert stored["summary_origin_account_generation"] == 7
        assert stored["origin_account_id"] == str(env.alice)
        assert stored["origin_account_generation"] == 7
        assert api.redis.enqueued == []
        if revoke:
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )

            async def forbidden(*args, **kwargs):
                pytest.fail("revoked queue-outage task cannot execute or settle")

            monkeypatch.setattr(runner, "_execute", forbidden)
            monkeypatch.setattr(runner, "settle_lost_attempt_holds", forbidden)
        # Queue/worker comes back: old capture is preserved, not permission to run.
        assert await runner.run_chat_task(api.ctx(), str(task_row["id"])) == (
            "failed" if revoke else "completed"
        )
        assert (await asyncio.wait_for(request_task, 15)).status_code == 200
        assert (
            await env.pool.fetchval(
                "SELECT input_ciphertext FROM tasks WHERE id=$1", task_row["id"]
            )
            == task_row["input_ciphertext"]
        )
        extracts = [kwargs for name, _, kwargs in api.redis.enqueued if name == "extract_memories"]
        if revoke:
            assert extracts == []
            row = await env.pool.fetchrow("SELECT * FROM tasks WHERE id=$1", task_row["id"])
            assert row["status"] == "failed" and row["terminal_code"] == "task_generation_stale"
            assert row["attempt_count"] == row["lease_epoch"] == 0
        else:
            assert len(extracts) == 1
            assert extracts[0]["origin_account_id"] == str(env.alice)
            assert extracts[0]["origin_account_generation"] == 7
        capture.assert_awaited_once()
    finally:
        if not request_task.done():
            request_task.cancel()
        await asyncio.gather(request_task, return_exceptions=True)
