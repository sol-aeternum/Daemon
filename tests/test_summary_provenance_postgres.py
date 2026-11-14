"""Direct-source closure on owned disposable PostgreSQL; scripted inference only."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from secrets import token_hex
from types import SimpleNamespace
from unittest.mock import Mock

import asyncpg
import pytest

from orchestrator.memory import summary, summary_provenance as sp
from orchestrator.memory.revisions import capture, mutation
from orchestrator.memory.startup import require_revision_schema
from orchestrator.memory.temporal import TemporalRefusal
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.worker import jobs
from tests.durable_tasks_support import durable_env_fixture

env = durable_env_fixture(initialized=True)


async def seed(env, count=3):
    conv = await env.memory.create_conversation(env.alice, title="Synthetic source")
    now = await env.pool.fetchval("SELECT clock_timestamp()")
    rows = []
    for i in range(count):
        row = await env.memory.insert_message(
            conv["id"], env.alice, "user", f"Fictional message {i}.", status="complete"
        )
        await env.pool.execute(
            "UPDATE messages SET created_at=$2 WHERE id=$1",
            row["id"],
            now - timedelta(seconds=count - i),
        )
        rows.append(row)
    return conv["id"], rows


async def prepare(env, cid, limit=2, render_limit=None):
    return await env.memory.read_conversation_summary_inputs(
        cid,
        captured_generation=CapturedGeneration(env.alice, 0),
        limit=limit,
        render_limit=render_limit,
    )


async def publish(env, inputs, text="Synthetic summary."):
    return await env.memory.update_conversation_summary(
        inputs["id"],
        text,
        expected_summary_updated_at=inputs["summary_updated_at"],
        summarized_message_count=inputs["summary_snapshot"].claimed,
        captured_generation=CapturedGeneration(env.alice, 0),
        expected_memory_erasure_epoch=inputs["memory_erasure_epoch"],
        summary_snapshot=inputs["summary_snapshot"],
        summary_continuation_pending=True,
    )


@pytest.mark.asyncio
async def test_incremental_closure_encrypted_payload_and_no_legacy_parent(env):
    cid, messages = await seed(env)
    await env.pool.execute(
        "UPDATE conversations SET summary='UNQUALIFIED LEGACY',metadata=$2::jsonb WHERE id=$1",
        cid,
        '{"last_summarized_msg_count":2}',
    )
    first = await prepare(env, cid, render_limit=500)
    assert first["summary"] is None and first["persisted_baseline"] == 0
    assert await publish(env, first)
    head = (await env.memory.get_conversation(cid))["summary_head_version_id"]
    payload = await env.pool.fetchval(
        "SELECT ciphertext FROM conversation_summary_payloads WHERE version_id=$1", head
    )
    assert "Synthetic summary" not in payload
    assert env.memory.encryption.decrypt(payload) == "Synthetic summary."
    second = await prepare(env, cid)
    assert second["summary"] == "Synthetic summary." and second["persisted_baseline"] == 2
    assert await publish(env, second, "Synthetic updated summary.")
    row = await env.pool.fetchrow(
        "SELECT * FROM conversation_summary_versions WHERE id=(SELECT summary_head_version_id FROM conversations WHERE id=$1)",
        cid,
    )
    assert row["parent_version_id"] == head and row["source_count"] == 3
    assert {s.message_id for s in (await sp.read(env.memory, env.alice, cid))[0].sources} == {
        m["id"] for m in messages
    }
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM conversation_summary_payloads WHERE version_id=$1", head
        )
        == 0
    )
    assert await env.pool.fetchval("SELECT summary FROM conversations WHERE id=$1", cid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["content", "aba", "status", "backdated", "head", "erase"])
async def test_frozen_publish_refuses_changes(env, monkeypatch, change):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    cid, messages = await seed(env)
    inputs = await prepare(env, cid)
    if change in ("content", "aba"):
        await env.memory.update_message(messages[0]["id"], content="Edited fictional source.")
        if change == "aba":
            await env.memory.update_message(messages[0]["id"], content=messages[0]["content"])
    elif change == "status":
        await env.pool.execute(
            "UPDATE messages SET status='cancelled' WHERE id=$1", messages[0]["id"]
        )
    elif change == "backdated":
        added = await env.memory.insert_message(
            cid, env.alice, "user", "Backdated fiction.", status="complete"
        )
        await env.pool.execute(
            "UPDATE messages SET created_at='1999-01-01' WHERE id=$1", added["id"]
        )
    elif change == "head":
        assert await publish(env, inputs, "Winning summary.")
    else:
        root = await env.memory.insert_memory(
            env.alice, "Synthetic unrelated memory.", "fact", "user_created"
        )
        await env.memory.delete_memory(root["id"], user_id=env.alice)
    before = await env.pool.fetchval("SELECT count(*) FROM conversation_summary_versions")
    if change == "erase":
        with pytest.raises(TemporalRefusal, match="erasure"):
            await publish(env, inputs)
    else:
        assert not await publish(env, inputs)
    assert await env.pool.fetchval("SELECT count(*) FROM conversation_summary_versions") == before


@pytest.mark.asyncio
async def test_append_and_tokens_preserve_progress_but_source_edit_revokes_all_reads(env):
    cid, messages = await seed(env)
    inputs = await prepare(env, cid)
    await env.pool.execute("UPDATE messages SET tokens_in=99 WHERE id=$1", messages[0]["id"])
    await env.memory.insert_message(
        cid,
        env.alice,
        "assistant",
        "Original assistant prose remains a direct source.",
        status="complete",
    )
    assert await publish(env, inputs)
    await env.memory.update_message(messages[0]["id"], content="Changed original.")
    assert (await env.memory.get_conversation(cid))["summary"] is None
    assert (await env.memory.list_conversations(env.alice))[0]["summary"] is None
    assert (await env.memory.list_conversations_page(env.alice))[0][0]["summary"] is None
    rebuilt = await prepare(env, cid)
    assert rebuilt["summary"] is None and rebuilt["persisted_baseline"] == 0


@pytest.mark.asyncio
async def test_owner_local_cloud_and_poison_cipher_before_decrypt(env, monkeypatch):
    cid, _ = await seed(env)
    assert await publish(env, await prepare(env, cid))
    await env.pool.execute("UPDATE conversations SET pipeline='local' WHERE id=$1", cid)
    assert (await env.memory.get_conversation(cid))["summary"] == "Synthetic summary."
    cipher = Mock(side_effect=AssertionError("cannot decrypt unqualified sources"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", cipher)
    with pytest.raises(TemporalRefusal):
        await sp.read(env.memory, env.alice, cid)
    assert await prepare(env, cid) is None
    with pytest.raises(TemporalRefusal):
        await sp.read(env.memory, env.bob, cid, cloud=False)
    cipher.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("task_path", [False, True])
async def test_source_delete_and_scope_a_purge_payloads_preserve_metadata(
    env, monkeypatch, task_path
):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    cid, _ = await seed(env)
    assert await publish(env, await prepare(env, cid))
    assert await publish(env, await prepare(env, cid))
    root = await env.memory.insert_memory(env.alice, "Fictional memory.", "fact", "user_created")
    original_count = await env.memory.count_messages(cid)
    await env.memory.delete_memory(root["id"], user_id=env.alice)
    assert await env.memory.count_messages(cid) == original_count
    assert await env.pool.fetchval("SELECT count(*) FROM conversation_summary_payloads") == 0
    assert await env.pool.fetchval(
        "SELECT bool_and(revoked_at IS NOT NULL) FROM conversation_summary_versions"
    )
    assert await publish(env, await prepare(env, cid))
    if task_path:
        assert await env.tasks.delete_conversation(env.alice, cid) == (True, None)
    else:
        assert await env.memory.delete_conversation(cid, user_id=env.alice)
    assert await env.pool.fetchval("SELECT count(*) FROM conversation_summary_payloads") == 0
    assert await env.pool.fetchval(
        "SELECT bool_and(revoked_at IS NOT NULL) FROM conversation_summary_versions"
    )
    assert await env.pool.fetchval("SELECT count(*) FROM conversation_summary_sources") > 0
    await env.pool.execute("DELETE FROM users WHERE id=$1", env.alice)
    assert await env.pool.fetchval("SELECT count(*) FROM conversation_summary_versions") == 0


@pytest.mark.asyncio
async def test_erased_sources_never_decrypted_or_in_rendered_dependencies(env, monkeypatch):
    cid, messages = await seed(env)
    async with mutation(env.memory, await capture(env.memory, env.alice)) as conn:
        await conn.execute(
            "INSERT INTO memory_erased_sources VALUES($1,'message',$2,$3)",
            env.alice,
            messages[0]["id"],
            messages[0]["id"],
        )
    await env.pool.execute("UPDATE messages SET content='poison' WHERE id=$1", messages[0]["id"])
    inputs = await prepare(env, cid)
    assert inputs["messages"][0]["content"] == ""
    assert [s.message_id for s in inputs["summary_snapshot"].rendered] == [messages[1]["id"]]
    assert await publish(env, inputs)


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["inline", "queued"])
async def test_actual_producers_preserve_prior_context_without_locks(env, monkeypatch, family):
    from orchestrator.memory import summarization

    cid, _ = await seed(env)
    assert await publish(env, await prepare(env, cid))
    prompts = []

    async def model(*args, **kwargs):
        async with env.pool.acquire() as conn, conn.transaction():
            await conn.fetchrow("SELECT id FROM conversations WHERE id=$1 FOR UPDATE NOWAIT", cid)
        prompts.append((args, kwargs))
        return "Updated synthetic summary."

    @asynccontextmanager
    async def compute(*args, **kwargs):
        yield

    monkeypatch.setattr(jobs, "account_compute", compute)
    monkeypatch.setattr(summarization, "generate_summary", model)

    async def inline(**kwargs):
        return SimpleNamespace(choices=[{"message": {"content": await model(**kwargs)}}])

    monkeypatch.setattr(summary, "guarded_completion", inline)
    if family == "inline":
        assert (
            await summary._generate_or_update_summary_result(
                cid, env.memory, captured_generation=CapturedGeneration(env.alice, 0)
            )
        ).summary
        assert "Synthetic summary." in prompts[0][1]["messages"][0]["content"]
    else:
        assert (
            await jobs.generate_summary_job(
                {"store": env.memory, "db_pool": env.pool},
                str(cid),
                True,
                origin_account_id=env.alice,
                origin_account_generation=0,
            )
        )["status"] == "success"
        assert prompts[0][0][1] == "Synthetic summary."
    assert (await env.memory.get_conversation(cid))["summary"] == "Updated synthetic summary."


@pytest.mark.asyncio
async def test_immutable_closure_raw_writer_and_startup_guards(env):
    cid, _ = await seed(env)
    inputs = await prepare(env, cid)
    assert await publish(env, inputs)
    head = (await env.memory.get_conversation(cid))["summary_head_version_id"]
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await env.pool.execute(
            "UPDATE conversation_summary_versions SET source_count=1 WHERE id=$1", head
        )
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await env.pool.execute("DELETE FROM conversation_summary_sources WHERE version_id=$1", head)
    with pytest.raises(ValueError, match="snapshot"):
        await env.memory.update_conversation(cid, summary="Untracked summary")
    await env.pool.execute("UPDATE conversations SET summary='Legacy raw writer' WHERE id=$1", cid)
    assert (await env.memory.get_conversation(cid))["summary"] is None
    await require_revision_schema(env.pool)
    await env.pool.execute(
        "ALTER TABLE conversation_summary_sources DISABLE TRIGGER summary_source_guard"
    )
    with pytest.raises(RuntimeError, match="072"):
        await require_revision_schema(env.pool)


@pytest.mark.asyncio
async def test_extraction_frozen_summary_rechecks_sources(env):
    from orchestrator.memory import extraction_fencing as fencing

    cid, messages = await seed(env)
    assert await publish(env, await prepare(env, cid))
    inputs, _ = await fencing.read_page(env.memory, CapturedGeneration(env.alice, 0), cid)
    inputs, text = await fencing.with_summary(env.memory, inputs)
    assert text == "Synthetic summary."
    await env.memory.update_message(messages[0]["id"], content="Fictional source edit.")
    with pytest.raises(fencing.ExtractionRefused):
        await fencing.check(env.memory, inputs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,trigger",
    [
        ("conversations", "conversation_summary_head_guard"),
        ("conversation_summary_versions", "summary_version_guard"),
        ("conversation_summary_sources", "summary_source_guard"),
        ("conversation_summary_payloads", "summary_payload_guard"),
    ],
)
@pytest.mark.parametrize("operation", ["DISABLE", "DROP"])
async def test_startup_requires_every_summary_guard(env, table, trigger, operation):
    await require_revision_schema(env.pool)
    if operation == "DISABLE":
        await env.pool.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    else:
        await env.pool.execute(f"DROP TRIGGER {trigger} ON {table}")
    with pytest.raises(RuntimeError, match="072"):
        await require_revision_schema(env.pool)


@pytest.mark.asyncio
async def test_extraction_locks_summary_sources_outside_chunk_until_commit(env):
    from orchestrator.memory import extraction_fencing as fencing
    from tests.test_account_write_guard import blocked

    cid, messages = await seed(env, 1)
    assert await publish(env, await prepare(env, cid))
    newest = await env.memory.insert_message(
        cid, env.alice, "user", "A separate fictional input.", status="complete"
    )
    inputs, _ = await fencing.read_page(env.memory, CapturedGeneration(env.alice, 0), cid)
    inputs, _ = await fencing.with_summary(env.memory, inputs.subset({newest["id"]}))
    async with env.pool.acquire() as writer, env.pool.acquire() as editor:
        transaction = writer.transaction()
        await transaction.start()
        await fencing.validate(writer, inputs)
        edit = asyncio.create_task(
            editor.execute(
                "UPDATE messages SET content=$2 WHERE id=$1",
                messages[0]["id"],
                env.memory.encryption.encrypt("Concurrent fictional source edit."),
            )
        )
        try:
            await blocked(env.pool, editor.get_server_pid(), writer.get_server_pid())
            await transaction.commit()
            await asyncio.wait_for(edit, 10)
            with pytest.raises(fencing.ExtractionRefused):
                await fencing.check(env.memory, inputs)
        finally:
            if writer.is_in_transaction():
                await transaction.rollback()
            if not edit.done():
                edit.cancel()
            await asyncio.gather(edit, return_exceptions=True)


@pytest.mark.asyncio
async def test_wholly_suppressed_checkpoint_is_not_independent_summary(env):
    cid, messages = await seed(env, 1)
    async with mutation(env.memory, await capture(env.memory, env.alice)) as conn:
        await conn.execute(
            "INSERT INTO memory_erased_sources VALUES($1,'message',$2,$3)",
            env.alice,
            messages[0]["id"],
            messages[0]["id"],
        )
    inputs = await prepare(env, cid)
    assert inputs["summary_snapshot"].rendered == ()
    assert await publish(env, inputs, "")
    assert (await env.memory.get_conversation(cid))["summary"] is None
    assert await env.pool.fetchval(
        "SELECT bool_and(checkpoint_only AND source_count=0) FROM conversation_summary_versions"
    )
    assert await env.pool.fetchval("SELECT count(*) FROM conversation_summary_payloads") == 0
    next_inputs = await prepare(env, cid)
    assert next_inputs["persisted_baseline"] == 1 and not next_inputs["messages"]


@pytest.mark.asyncio
async def test_noop_stale_head_and_clear_aba(env):
    cid, _ = await seed(env, 1)
    assert await publish(env, await prepare(env, cid))
    observed = await prepare(env, cid)
    async with mutation(env.memory, await capture(env.memory, env.alice)) as conn:
        await sp.invalidate(conn, env.alice)
    assert not await env.memory.clear_finished_summary_continuation(
        cid,
        captured_generation=CapturedGeneration(env.alice, 0),
        expected_summary_updated_at=observed["summary_updated_at"],
        expected_baseline=1,
        summary_snapshot=observed["summary_snapshot"],
    )
    assert not await publish(env, observed)
    rebuilt = await prepare(env, cid)
    assert rebuilt["persisted_baseline"] == 0


@pytest.mark.asyncio
async def test_publish_deferred_commit_rollback_is_atomic(env):
    cid, _ = await seed(env)
    inputs = await prepare(env, cid)
    await env.pool.execute("""
        CREATE FUNCTION synthetic_summary_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic summary rollback'; END $$;
        CREATE CONSTRAINT TRIGGER synthetic_summary_failure AFTER UPDATE ON conversations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION synthetic_summary_failure();
    """)
    with pytest.raises(asyncpg.RaiseError):
        await publish(env, inputs)
    for table in (
        "conversation_summary_versions",
        "conversation_summary_sources",
        "conversation_summary_payloads",
    ):
        assert await env.pool.fetchval("SELECT count(*) FROM " + table) == 0
    assert (
        await env.pool.fetchval(
            "SELECT summary_head_version_id FROM conversations WHERE id=$1", cid
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["inline", "queued"])
@pytest.mark.parametrize("phase", ["before_dispatch", "during_provider"])
async def test_actual_producer_source_edit_races(env, monkeypatch, family, phase):
    from orchestrator.memory import summarization
    from arq.worker import Retry

    cid, messages = await seed(env, 1)
    model_called = False
    original_check = env.memory.check_conversation_summary_generation

    async def check(*args, **kwargs):
        if phase == "before_dispatch":
            await env.memory.update_message(messages[0]["id"], content="New fictional revision.")
        return await original_check(*args, **kwargs)

    async def model(*args, **kwargs):
        nonlocal model_called
        model_called = True
        if phase == "during_provider":
            await env.memory.update_message(messages[0]["id"], content="New fictional revision.")
        return "Stale generated summary."

    @asynccontextmanager
    async def compute(*args, **kwargs):
        yield

    async def inline(**kwargs):
        return SimpleNamespace(choices=[{"message": {"content": await model(**kwargs)}}])

    monkeypatch.setattr(env.memory, "check_conversation_summary_generation", check)
    monkeypatch.setattr(jobs, "account_compute", compute)
    monkeypatch.setattr(summarization, "generate_summary", model)
    monkeypatch.setattr(summary, "guarded_completion", inline)
    if family == "inline":
        result = await summary._generate_or_update_summary_result(
            cid, env.memory, captured_generation=CapturedGeneration(env.alice, 0)
        )
        assert result.summary is None and result.continuation_needed
    else:
        with pytest.raises(Retry):
            await jobs.generate_summary_job(
                {"store": env.memory, "db_pool": env.pool},
                str(cid),
                True,
                origin_account_id=env.alice,
                origin_account_generation=0,
            )
    assert model_called == (phase == "during_provider")
    assert await env.pool.fetchval("SELECT count(*) FROM conversation_summary_versions") == 0


@pytest.mark.asyncio
async def test_source_row_lock_wait_rechecks_committed_version(env):
    from tests.test_account_write_guard import blocked
    from tests.test_title_account_guard_postgres import bound_store

    cid, messages = await seed(env, 1)
    inputs = await prepare(env, cid)
    async with env.pool.acquire() as writer, env.pool.acquire() as editor:
        transaction = editor.transaction()
        await transaction.start()
        await editor.execute(
            "UPDATE messages SET content=$2 WHERE id=$1",
            messages[0]["id"],
            env.memory.encryption.encrypt("Concurrent fictional source."),
        )
        store = bound_store(env, writer)
        task = asyncio.create_task(
            sp.publish(store, inputs["summary_snapshot"], "Stale summary.", continuation=False)
        )
        try:
            await blocked(env.pool, writer.get_server_pid(), editor.get_server_pid())
            await transaction.commit()
            assert not await asyncio.wait_for(task, 10)
            assert not writer.is_in_transaction()
            assert (
                await env.pool.fetchval("SELECT count(*) FROM conversation_summary_payloads") == 0
            )
        finally:
            if editor.is_in_transaction():
                await transaction.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
