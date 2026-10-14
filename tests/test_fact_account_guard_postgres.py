"""Production fact/checkpoint qualification on owned real PostgreSQL.

Only providers/compute are scripted; encryption, reads, SQL and commits are real.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from arq import Retry

from orchestrator.memory import dedup, equivalence, extraction, extraction_fencing as fencing
from orchestrator.memory.extraction import ExtractedFact, ExtractionOutcome
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal
from orchestrator.worker import jobs
from tests.durable_tasks_support import durable_env_fixture
from tests.test_account_write_guard import blocked
from tests.test_title_account_guard_postgres import bound_store
from tests.test_entity_enqueue_chain import entity_queue as entity_queue
from tests.test_entity_producer_postgres import queued

env = durable_env_fixture()


async def prepare(env, monkeypatch, *, facts=None):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "synthetic-fact-73c877-pepper")
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id,content_generation) VALUES($1,7),($2,7)",
        env.alice,
        env.bob,
    )
    conv = (await env.memory.create_conversation(env.alice, title="Synthetic facts"))["id"]
    message = await env.memory.insert_message(
        conv, env.alice, "user", "User likes fictional blue stars.", status="complete"
    )

    @asynccontextmanager
    async def compute(*args, **kwargs):
        async with env.pool.acquire() as conn, conn.transaction():
            await conn.fetchrow(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                env.alice,
            )
        yield

    monkeypatch.setattr(jobs, "account_compute", compute)
    monkeypatch.setattr(dedup, "prepare_memory_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(equivalence, "current_scope", lambda: SimpleNamespace(user_id=env.alice))
    judge = AsyncMock(
        return_value={
            "choices": [{"finish_reason": "stop", "message": {"content": '{"verdicts":[]}'}}]
        }
    )
    monkeypatch.setattr(equivalence, "guarded_completion", judge)
    facts = [ExtractedFact("User likes blue stars.", "fact", 0.9)] if facts is None else facts
    extractor = AsyncMock(return_value=ExtractionOutcome(facts, len(facts), len(facts), 0, 0))
    monkeypatch.setattr(extraction, "extract_facts_from_text", extractor)
    return conv, message["id"], extractor, judge


async def run(env, conv, *, generation=7, origin=None, payload=None):
    return await jobs.extract_memories(
        {"store": env.memory, "db_pool": env.pool},
        env.alice,
        conv,
        payload,
        origin_account_id=str(origin or env.alice),
        origin_account_generation=generation,
    )


async def counts(env):
    return tuple(
        [
            await env.pool.fetchval("SELECT count(*) FROM " + table)
            for table in ("memories", "memory_extraction_log")
        ]
    )


@pytest.mark.asyncio
async def test_real_fact_and_independent_checkpoint(env, monkeypatch):
    conv, mid, extractor, _ = await prepare(env, monkeypatch)
    assert (await run(env, conv))["status"] == "ok"
    assert await counts(env) == (1, 1)
    assert (await env.memory.get_last_extraction_cursor(conv))[1] == str(mid)
    assert (await run(env, conv))["processed_chunks"] == 0
    extractor.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [None, True, -1, 2**63, "7", 6])
async def test_invalid_legacy_stale_never_reads_provider_or_writes(env, monkeypatch, generation):
    conv, _, extractor, judge = await prepare(env, monkeypatch)
    result = await run(env, conv, generation=generation)
    assert result["status"] == "skipped"
    assert await counts(env) == (0, 0)
    extractor.assert_not_awaited()
    judge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["foreign", "local", "unknown", "suspended", "cause", "missing", "operation"]
)
async def test_owner_locality_and_account_state_before_poison_decrypt(
    env, monkeypatch, change, tmp_path
):
    conv, mid, extractor, _ = await prepare(env, monkeypatch)
    await env.pool.execute("UPDATE messages SET content='poison' WHERE id=$1", mid)
    generation = 7
    if change == "operation":
        from tests.test_entity_account_guard_postgres import restrict

        generation, reason = await restrict(env, "operation", tmp_path)
        assert reason == "open_operation"
    elif change == "foreign":
        await env.pool.execute("UPDATE conversations SET user_id=$2 WHERE id=$1", conv, env.bob)
    elif change in ("local", "unknown"):
        if change == "unknown":
            await env.pool.execute("ALTER TABLE conversations ALTER COLUMN pipeline DROP NOT NULL")
        await env.pool.execute(
            "UPDATE conversations SET pipeline=$2 WHERE id=$1",
            conv,
            "local" if change == "local" else None,
        )
    elif change == "suspended":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )
    elif change == "cause":
        await env.pool.execute(
            "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
        )
    else:
        await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", env.alice)
    result = await run(env, conv, generation=generation)
    assert result["status"] == "skipped"
    if change == "operation":
        assert result["reason"] == "open_operation"
    assert await counts(env) == (0, 0)
    extractor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["admission", "provider", "embedding", "judge"])
async def test_generation_races_stop_next_stage_and_checkpoint(env, monkeypatch, stage):
    conv, _, extractor, judge = await prepare(env, monkeypatch)
    if stage == "judge":
        await env.memory.insert_memory(env.alice, "User likes blue stars often.", "fact", "manual")

    async def fence():
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
        )

    if stage == "admission":

        @asynccontextmanager
        async def compute(*args, **kwargs):
            await fence()
            yield

        monkeypatch.setattr(jobs, "account_compute", compute)
    else:
        target, attr = (
            (extraction, "extract_facts_from_text")
            if stage == "provider"
            else (dedup, "prepare_memory_embedding")
            if stage == "embedding"
            else (equivalence, "guarded_completion")
        )
        original = getattr(target, attr)

        async def changed(*args, **kwargs):
            result = await original(*args, **kwargs)
            await fence()
            return result

        monkeypatch.setattr(target, attr, changed)
    assert (await run(env, conv))["status"] == "skipped"
    assert (await counts(env))[1] == 0
    assert (await counts(env))[0] == (1 if stage == "judge" else 0)
    if stage == "admission":
        extractor.assert_not_awaited()
    if stage != "judge":
        judge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("filtered", [False, True])
async def test_no_fact_and_filtered_page_checkpoint(env, monkeypatch, filtered):
    conv, mid, extractor, _ = await prepare(env, monkeypatch, facts=[])
    if filtered:
        await env.pool.execute(
            "UPDATE messages SET status='cancelled',content='poison' WHERE id=$1", mid
        )
    assert (await run(env, conv))["status"] == "ok"
    assert await counts(env) == (0, 1)
    record = json.loads(await env.pool.fetchval("SELECT dedup_results FROM memory_extraction_log"))
    assert record["filtered_page_checkpoint" if filtered else "no_fact_checkpoint"] is True
    if filtered:
        extractor.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkpoint_failure_preserves_fact_prefix_then_exact_retry(env, monkeypatch):
    conv, _, _, _ = await prepare(env, monkeypatch)
    original = env.memory.log_extraction
    monkeypatch.setattr(
        env.memory, "log_extraction", AsyncMock(side_effect=RuntimeError("synthetic"))
    )
    with pytest.raises(Retry):
        await run(env, conv)
    assert await counts(env) == (1, 0)
    monkeypatch.setattr(env.memory, "log_extraction", original)
    assert (await run(env, conv))["status"] == "ok"
    assert await counts(env) == (1, 1)
    record = json.loads(await env.pool.fetchval("SELECT dedup_results FROM memory_extraction_log"))
    assert record["merged"] == 1 and record["new"] == 0


@pytest.mark.asyncio
async def test_same_batch_paraphrase_sees_prior_commit(env, monkeypatch):
    conv, _, _, judge = await prepare(
        env,
        monkeypatch,
        facts=[
            ExtractedFact("User likes blue stars.", "fact", 0.9, "astronomy.blue"),
            ExtractedFact("User enjoys blue stars.", "fact", 0.9, "astronomy.blue"),
        ],
    )

    async def equivalent(**kwargs):
        assert await counts(env) == (1, 0)
        payload = json.loads(kwargs["messages"][1]["content"])
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "verdicts": [
                                    {"candidate_id": c["candidate_id"], "verdict": "equivalent"}
                                    for c in payload["candidates"]
                                ]
                            }
                        )
                    },
                }
            ]
        }

    judge.side_effect = equivalent
    assert (await run(env, conv))["status"] == "ok"
    assert await counts(env) == (1, 1)
    judge.assert_awaited_once()


@pytest.mark.asyncio
async def test_exact_ineligible_winner_never_decrypts_or_checkpoints(env, monkeypatch):
    conv, _, _, _ = await prepare(env, monkeypatch)
    foreign = (await env.memory.create_conversation(env.bob))["id"]
    canonical = await env.memory.insert_memory(
        env.alice, "User likes blue stars.", "fact", "extracted", source_conversation_id=foreign
    )
    await env.pool.execute("UPDATE memories SET content='poison' WHERE id=$1", canonical["id"])
    assert (await run(env, conv))["status"] == "skipped"
    assert await counts(env) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", ["fact", "checkpoint"])
@pytest.mark.parametrize("cause_only", [False, True])
async def test_control_first_writer_wait_refuses_and_reuses_pool(
    env, monkeypatch, seam, cause_only
):
    conv, _, _, _ = await prepare(env, monkeypatch)
    inputs, _ = await fencing.read_page(env.memory, CapturedGeneration(env.alice, 7), conv)
    _, plan = await empty_plan(env, conv)
    async with env.pool.acquire() as control, env.pool.acquire() as writer:
        tx = control.transaction()
        await tx.start()
        await control.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", env.alice
        )
        pid = await control.fetchval("SELECT pg_backend_pid()")
        task = asyncio.create_task(
            fencing.commit_fact(bound_store(env, writer), inputs, plan, None, {}, {})
            if seam == "fact"
            else fencing.checkpoint(
                bound_store(env, writer),
                inputs,
                user_id=env.alice,
                conversation_id=conv,
                input_snippet="",
            )
        )
        try:
            await blocked(env.pool, writer.get_server_pid(), pid)
            if cause_only:
                await control.execute(
                    "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')",
                    env.alice,
                    uuid4(),
                )
            else:
                await control.execute(
                    "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1",
                    env.alice,
                )
            await tx.commit()
            with pytest.raises(
                WriteGuardRefusal, match="inconsistent_state" if cause_only else "stale_generation"
            ):
                await task
        finally:
            if control.is_in_transaction():
                await tx.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await counts(env) == (0, 0)
    assert await env.pool.fetchval("SELECT 1") == 1


async def empty_plan(env, conv):
    inputs, _ = await fencing.read_page(env.memory, CapturedGeneration(env.alice, 7), conv)
    incoming = equivalence.IncomingMemory(
        env.alice, "User likes blue stars.", "fact", "extracted", conv, None
    )
    return inputs, equivalence.EquivalencePlan(incoming)


@pytest.mark.asyncio
async def test_concurrent_exact_writers_share_one_committed_winner(env, monkeypatch):
    conv, _, _, _ = await prepare(env, monkeypatch)
    inputs, plan = await empty_plan(env, conv)
    results = await asyncio.gather(
        *[fencing.commit_fact(env.memory, inputs, plan, None, {}, {}) for _ in range(2)]
    )
    assert {row["id"] for row, _ in results} == {results[0][0]["id"]}
    assert sum(inserted for _, inserted in results) == 1
    assert await counts(env) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", ["fact", "checkpoint"])
async def test_real_failed_commit_and_subsequent_pool_reuse(env, monkeypatch, seam):
    conv, _, _, _ = await prepare(env, monkeypatch)
    inputs, plan = await empty_plan(env, conv)
    table = "memories" if seam == "fact" else "memory_extraction_log"
    await env.pool.execute("""
        CREATE FUNCTION fact_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic fact commit failure'; END $$;
    """)
    await env.pool.execute(
        f"CREATE CONSTRAINT TRIGGER fact_commit_failure AFTER INSERT ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fact_commit_failure()"
    )
    with pytest.raises(Exception, match="synthetic fact commit failure"):
        if seam == "fact":
            await fencing.commit_fact(env.memory, inputs, plan, None, {}, {})
        else:
            await fencing.checkpoint(
                env.memory, inputs, user_id=env.alice, conversation_id=conv, input_snippet=""
            )
    assert await counts(env) == (0, 0)
    await env.pool.execute(f"DROP TRIGGER fact_commit_failure ON {table}")
    assert (await fencing.commit_fact(env.memory, inputs, plan, None, {}, {}))[1] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", ["fact", "checkpoint"])
async def test_writer_first_actual_account_lock_wait_through_commit(env, monkeypatch, seam):
    conv, mid, _, _ = await prepare(env, monkeypatch)
    inputs, plan = await empty_plan(env, conv)
    async with (
        env.pool.acquire() as writer,
        env.pool.acquire() as editor,
        env.pool.acquire() as control,
    ):
        tx = editor.transaction()
        await tx.start()
        await editor.fetchrow("SELECT id FROM messages WHERE id=$1 FOR UPDATE", mid)
        store = bound_store(env, writer)
        task = asyncio.create_task(
            fencing.commit_fact(store, inputs, plan, None, {}, {})
            if seam == "fact"
            else fencing.checkpoint(
                store, inputs, user_id=env.alice, conversation_id=conv, input_snippet=""
            )
        )
        fence = None
        try:
            await blocked(env.pool, writer.get_server_pid(), editor.get_server_pid())

            async def advance():
                async with control.transaction():
                    await control.fetchrow(
                        "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                        env.alice,
                    )
                    assert await counts(env) == ((1, 0) if seam == "fact" else (0, 1))
                    await control.execute(
                        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1",
                        env.alice,
                    )

            fence = asyncio.create_task(advance())
            await blocked(env.pool, control.get_server_pid(), writer.get_server_pid())
            await tx.commit()
            await asyncio.wait_for(task, 10)
            await asyncio.wait_for(fence, 10)
        finally:
            if editor.is_in_transaction():
                await tx.rollback()
            for t in (task, fence):
                if t is not None and not t.done():
                    t.cancel()
            await asyncio.gather(task, *([fence] if fence else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", ["fact", "checkpoint"])
async def test_cancel_actual_source_wait_and_reuse_connection(env, monkeypatch, seam):
    conv, mid, _, _ = await prepare(env, monkeypatch)
    inputs, plan = await empty_plan(env, conv)
    async with env.pool.acquire() as writer, env.pool.acquire() as holder:
        async with holder.transaction():
            await holder.fetchrow("SELECT id FROM messages WHERE id=$1 FOR UPDATE", mid)
            store = bound_store(env, writer)
            task = asyncio.create_task(
                fencing.commit_fact(store, inputs, plan, None, {}, {})
                if seam == "fact"
                else fencing.checkpoint(
                    store, inputs, user_id=env.alice, conversation_id=conv, input_snippet=""
                )
            )
            try:
                await blocked(env.pool, writer.get_server_pid(), holder.get_server_pid())
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not writer.is_in_transaction()
                assert await writer.fetchval("SELECT 1") == 1
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert await counts(env) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "summary"])
async def test_consumed_source_or_summary_change_refuses_no_fact_checkpoint(
    env, monkeypatch, change
):
    conv, mid, extractor, _ = await prepare(env, monkeypatch, facts=[])
    outcome = extractor.return_value

    async def changed(*args, **kwargs):
        if change == "source":
            await env.pool.execute(
                "UPDATE messages SET content=$2 WHERE id=$1",
                mid,
                env.memory.encryption.encrypt("Changed source"),
            )
        else:
            await env.pool.execute(
                "UPDATE conversations SET summary='Changed summary' WHERE id=$1", conv
            )
        return outcome

    extractor.side_effect = changed
    assert (await run(env, conv))["status"] == "skipped"
    assert await counts(env) == (0, 0)


@pytest.mark.asyncio
async def test_malformed_foreign_legacy_reference_jobs_do_not_write(env, monkeypatch):
    conv, _, extractor, _ = await prepare(env, monkeypatch)
    for payload in (
        "not json",
        "[]",
        '{"_encrypted_extraction_continuation":"poison"}',
        json.dumps({"_extraction_refs": [{"message_id": str(uuid4())}]}),
    ):
        assert (await run(env, conv, payload=payload))["status"] == "skipped"
    assert (await run(env, conv, origin=env.bob))["status"] == "skipped"
    assert await counts(env) == (0, 0)
    extractor.assert_not_awaited()


@pytest.mark.asyncio
async def test_reference_gap_cannot_checkpoint_past_omitted_finalized_source(env, monkeypatch):
    conv, _, extractor, _ = await prepare(env, monkeypatch)
    later = await env.memory.insert_message(
        conv, env.alice, "user", "Later fact", status="complete"
    )
    payload = json.dumps(
        {
            "_extraction_refs": [
                {"message_id": str(later["id"]), "source_version": "0" * 64, "fragment_index": 0}
            ]
        }
    )
    assert (await run(env, conv, payload=payload))["reason"] == "source_gap"
    assert await counts(env) == (0, 0)
    extractor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["semantic", "local", "provenance"])
async def test_canonical_stale_semantics_fallback_but_privacy_refuses(env, monkeypatch, change):
    conv, _, _, judge = await prepare(env, monkeypatch)
    canonical_conv = (await env.memory.create_conversation(env.alice))["id"]
    canonical = await env.memory.insert_memory(
        env.alice,
        "User likes blue stars often.",
        "fact",
        "extracted",
        source_conversation_id=canonical_conv,
    )
    inputs, plan = await empty_plan(env, conv)
    rows, privacy, dependencies = await fencing.discover(env.memory, inputs, plan.incoming, None)
    judge.return_value = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [
                                {"candidate_id": str(canonical["id"]), "verdict": "equivalent"}
                            ]
                        }
                    )
                },
            }
        ]
    }
    plan = await equivalence.plan_equivalence(plan.incoming, rows)
    assert plan.selected is not None
    if change == "semantic":
        await env.pool.execute(
            "UPDATE memories SET content=$2,updated_at=now() WHERE id=$1",
            canonical["id"],
            env.memory.encryption.encrypt("User likes red stars."),
        )
        row, inserted = await fencing.commit_fact(
            env.memory, inputs, plan, None, privacy, dependencies
        )
        assert inserted is True and row["id"] != canonical["id"]
    else:
        if change == "local":
            await env.pool.execute(
                "UPDATE memories SET local_only=TRUE,content='poison' WHERE id=$1", canonical["id"]
            )
        else:
            await env.pool.execute(
                "UPDATE conversations SET pipeline='local' WHERE id=$1", canonical_conv
            )
        with pytest.raises(fencing.ExtractionRefused):
            await fencing.commit_fact(env.memory, inputs, plan, None, privacy, dependencies)
        assert await counts(env) == (1, 0)


@pytest.mark.asyncio
async def test_cursor_cannot_cross_changed_filtered_prefix(env, monkeypatch):
    conv, mid, extractor, _ = await prepare(env, monkeypatch, facts=[])
    await env.pool.execute("UPDATE messages SET status='cancelled' WHERE id=$1", mid)
    await env.memory.insert_message(conv, env.alice, "user", "Finalized source", status="complete")
    outcome = extractor.return_value

    async def changed(*args, **kwargs):
        await env.pool.execute("UPDATE messages SET status='complete' WHERE id=$1", mid)
        return outcome

    extractor.side_effect = changed
    assert (await run(env, conv))["status"] == "skipped"
    assert await counts(env) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("account_stale", [False, True])
async def test_refused_suffix_schedules_only_committed_prefix_under_original_origin(
    env, monkeypatch, entity_queue, account_stale
):
    conv, _, extractor, _ = await prepare(env, monkeypatch)
    await env.pool.execute(
        "UPDATE messages SET content=$2 WHERE conversation_id=$1",
        conv,
        env.memory.encryption.encrypt("Synthetic source " * 160),
    )
    suffix = await env.memory.insert_message(
        conv, env.alice, "user", "Synthetic suffix " * 160, status="complete"
    )
    monkeypatch.setattr(jobs, "MAX_EXTRACTION_CHUNKS_PER_JOB", 8)
    outcome = extractor.return_value
    calls = 0

    async def changed_suffix(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if account_stale:
                await env.pool.execute(
                    "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1",
                    env.alice,
                )
            else:
                await env.pool.execute(
                    "UPDATE messages SET content=$2 WHERE id=$1",
                    suffix["id"],
                    env.memory.encryption.encrypt("Changed suffix"),
                )
        return outcome

    extractor.side_effect = changed_suffix
    queue, pipeline = entity_queue
    result = await jobs.extract_memories(
        {"store": env.memory, "db_pool": env.pool, "redis": queue},
        env.alice,
        conv,
        origin_account_id=str(env.alice),
        origin_account_generation=7,
    )
    assert result["status"] == "skipped" and calls == 2
    assert await counts(env) == (1, 1)
    enqueued = await queued(queue, pipeline, "resolve_entities_job")
    if account_stale:
        assert enqueued == []
        return
    wire = enqueued[0]
    saved = await env.pool.fetchval("SELECT id FROM memories")
    assert wire.args == (str(env.alice), json.dumps([str(saved)]))
    assert wire.kwargs == {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    assert (await env.memory.get_last_extraction_cursor(conv))[1] != str(suffix["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", ["exact", "canonical"])
async def test_actual_concurrent_writer_waits_on_exact_conflict_or_canonical_row(
    env, monkeypatch, seam
):
    conv, _, _, _ = await prepare(env, monkeypatch)
    inputs, plan = await empty_plan(env, conv)
    privacy, dependencies = {}, {}
    canonical = None
    if seam == "canonical":
        canonical = await env.memory.insert_memory(
            env.alice, plan.incoming.content, "fact", "manual"
        )
        plan = equivalence.EquivalencePlan(
            plan.incoming, (equivalence.CandidateSnapshot.capture(canonical),), canonical["id"]
        )
        privacy = {canonical["id"]: fencing._privacy(canonical)}
    key = uuid4().int % (2**63 - 1)
    await env.pool.execute(f"""
        CREATE FUNCTION fact_writer_pause() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_advisory_xact_lock({key}); RETURN NEW; END $$;
    """)
    if seam == "exact":
        await env.pool.execute(
            "CREATE CONSTRAINT TRIGGER fact_writer_pause AFTER INSERT ON memories "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fact_writer_pause()"
        )
    else:
        await env.pool.execute(
            "CREATE TRIGGER fact_writer_pause BEFORE UPDATE ON memories "
            "FOR EACH ROW EXECUTE FUNCTION fact_writer_pause()"
        )
    async with (
        env.pool.acquire() as first,
        env.pool.acquire() as second,
        env.pool.acquire() as holder,
    ):
        await holder.execute("SELECT pg_advisory_lock($1)", key)
        a = asyncio.create_task(
            fencing.commit_fact(bound_store(env, first), inputs, plan, None, privacy, dependencies)
        )
        b = None
        try:
            # First is blocked after its INSERT (at real commit) or canonical touch.
            await blocked(env.pool, first.get_server_pid(), holder.get_server_pid())
            b = asyncio.create_task(
                fencing.commit_fact(
                    bound_store(env, second), inputs, plan, None, privacy, dependencies
                )
            )
            await blocked(env.pool, second.get_server_pid(), first.get_server_pid())
            await holder.execute("SELECT pg_advisory_unlock($1)", key)
            results = await asyncio.wait_for(asyncio.gather(a, b), 10)
            assert results[0][0]["id"] == results[1][0]["id"]
            assert sum(inserted for _, inserted in results) == (1 if seam == "exact" else 0)
            if canonical is not None:
                assert (
                    await env.pool.fetchval(
                        "SELECT access_count FROM memories WHERE id=$1", canonical["id"]
                    )
                    == 2
                )
            assert not first.is_in_transaction() and not second.is_in_transaction()
        finally:
            await holder.execute("SELECT pg_advisory_unlock($1)", key)
            for task in (a, b):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(a, *([b] if b else []), return_exceptions=True)
    assert await counts(env) == (1, 0)


@pytest.mark.asyncio
async def test_failed_second_fact_commit_retains_prefix_and_retry_recovers_projection(
    env, monkeypatch, entity_queue
):
    from orchestrator.memory.store import compute_memory_content_hash

    first_text, second_text = "User likes blue stars.", "User likes red stars."
    conv, _, _, _ = await prepare(
        env,
        monkeypatch,
        facts=[ExtractedFact(first_text, "fact", 0.9), ExtractedFact(second_text, "fact", 0.9)],
    )
    bad_hash = compute_memory_content_hash(second_text)
    await env.pool.execute(f"""
        CREATE FUNCTION fact_suffix_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.content_hash='{bad_hash}' THEN RAISE EXCEPTION 'synthetic suffix commit failure';
        END IF; RETURN NEW; END $$;
        CREATE CONSTRAINT TRIGGER fact_suffix_failure AFTER INSERT ON memories
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fact_suffix_failure();
    """)
    queue, pipeline = entity_queue
    ctx = {"store": env.memory, "db_pool": env.pool, "redis": queue}
    origin = {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    with pytest.raises(Retry):
        await jobs.extract_memories(ctx, env.alice, conv, **origin)
    assert await counts(env) == (1, 0)
    first_id = await env.pool.fetchval("SELECT id FROM memories")
    wire = (await queued(queue, pipeline, "resolve_entities_job"))[0]
    assert wire.args == (str(env.alice), json.dumps([str(first_id)])) and wire.kwargs == origin
    await env.pool.execute("DROP TRIGGER fact_suffix_failure ON memories")
    assert (await jobs.extract_memories(ctx, env.alice, conv, **origin))["status"] == "ok"
    assert await counts(env) == (2, 1)
    record = json.loads(await env.pool.fetchval("SELECT dedup_results FROM memory_extraction_log"))
    assert record["merged"] == 1 and record["new"] == 1
    all_ids = {str(r["id"]) for r in await env.pool.fetch("SELECT id FROM memories")}
    wires = await queued(queue, pipeline, "resolve_entities_job")
    assert any(set(json.loads(w.args[1])) == all_ids and w.kwargs == origin for w in wires)


@pytest.mark.asyncio
@pytest.mark.parametrize("vector_count", [0, 2])
async def test_malformed_one_input_embedding_receipt_never_writes_or_checkpoints(
    env, monkeypatch, vector_count
):
    conv, _, _, judge = await prepare(env, monkeypatch)
    monkeypatch.setattr(
        dedup,
        "prepare_memory_embedding",
        AsyncMock(
            return_value=SimpleNamespace(
                embeddings=[[1.0] * 1024 for _ in range(vector_count)],
                storage_model="synthetic:vector",
            )
        ),
    )
    with pytest.raises(Retry):
        await run(env, conv)
    assert await counts(env) == (0, 0)
    judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_winner_edit_between_conflict_read_and_lock_retries_without_false_checkpoint(
    env, monkeypatch
):
    conv, _, _, _ = await prepare(env, monkeypatch)
    # Cross-category exact reuse is an established deterministic exception; this
    # row is not in the fact-category semantic discovery/lock set.
    canonical = await env.memory.insert_memory(
        env.alice, "User likes blue stars.", "preference", "manual"
    )
    original_insert = env.memory._insert_memory_with_outcome
    changed = False

    async def interleaved_insert(*args, **kwargs):
        qualify = kwargs["row_qualifier"]

        async def edit_before_lock(raw):
            nonlocal changed
            if raw["id"] == canonical["id"] and not changed:
                updated = await asyncio.wait_for(
                    env.memory.update_memory_content(
                        canonical["id"],
                        "User likes red stars.",
                        user_id=env.alice,
                        require_content_hash=True,
                        expected_content_hash=canonical["content_hash"],
                    ),
                    5,
                )
                assert updated is not None
                changed = True
            return await qualify(raw)

        kwargs["row_qualifier"] = edit_before_lock
        return await original_insert(*args, **kwargs)

    monkeypatch.setattr(env.memory, "_insert_memory_with_outcome", interleaved_insert)
    with pytest.raises(Retry):
        await run(env, conv)
    assert changed and await counts(env) == (1, 0)
    assert (await run(env, conv))["status"] == "ok"
    assert await counts(env) == (2, 1)
    row = await env.pool.fetchrow("SELECT content FROM memories WHERE category='fact'")
    assert env.memory.encryption.decrypt(row["content"]) == "User likes blue stars."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fragmented, changed_source", [(False, False), (True, False), (True, True)]
)
async def test_native_continuation_covers_filtered_boundary_and_drains_remaining_fragments(
    env, monkeypatch, entity_queue, fragmented, changed_source
):
    conv, first, extractor, _ = await prepare(env, monkeypatch)
    monkeypatch.setattr(
        "orchestrator.memory.summary._generate_or_update_summary_result",
        AsyncMock(return_value=None),
    )
    if fragmented:
        await env.pool.execute("UPDATE messages SET status='cancelled' WHERE id=$1", first)
        filtered = first
    else:
        await env.pool.execute(
            "UPDATE messages SET content=$2 WHERE id=$1",
            first,
            env.memory.encryption.encrypt("A" * 3000),
        )
        filtered = (
            await env.memory.insert_message(
                conv, env.alice, "user", "Filtered boundary", status="cancelled"
            )
        )["id"]
    remaining_content = (
        "".join(f"{index:09d}|" for index in range(1000)) if fragmented else "B" * 3000
    )
    last = (
        await env.memory.insert_message(
            conv, env.alice, "user", remaining_content, status="complete"
        )
    )["id"]
    monkeypatch.setattr(jobs, "MAX_EXTRACTION_CHUNKS_PER_JOB", 1)
    queue, pipeline = entity_queue
    ctx = {"store": env.memory, "db_pool": env.pool, "redis": queue}
    origin = {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    assert (await jobs.extract_memories(ctx, env.alice, conv, **origin))["status"] == "ok"
    wires = await queued(queue, pipeline, "extract_memories")
    refs = json.loads(wires[-1].args[2])["_extraction_refs"]
    assert [ref["message_id"] for ref in refs] == [str(filtered), str(last)]
    assert refs[-1]["fragment_index"] == (1 if fragmented else 0)
    texts = [call.args[0] for call in extractor.await_args_list]
    if changed_source:
        await env.pool.execute(
            "UPDATE messages SET content=$2 WHERE id=$1",
            last,
            env.memory.encryption.encrypt("RESTARTED|" + remaining_content[10:]),
        )
    for hop in range(5):
        wire = wires[-1]
        assert wire.kwargs == origin
        assert (await jobs.extract_memories(ctx, *wire.args, **wire.kwargs))["status"] == "ok"
        if (await env.memory.get_last_extraction_cursor(conv))[1] == str(last):
            break
        updated = await queued(queue, pipeline, "extract_memories")
        assert len(updated) == len(wires) + 1  # Different fragment cannot reuse its running job ID.
        next_refs = json.loads(updated[-1].args[2])["_extraction_refs"]
        if changed_source and hop == 0:
            assert next_refs[-1]["fragment_index"] == 1
            assert next_refs[-1]["source_version"] != refs[-1]["source_version"]
            assert extractor.await_args_list[-1].args[0].startswith("[User]: RESTARTED|")
        else:
            assert next_refs[-1]["fragment_index"] == refs[-1]["fragment_index"] + 1
        refs, wires = next_refs, updated
    else:
        pytest.fail("bounded continuation did not drain the last source")
    assert (await env.memory.get_last_extraction_cursor(conv))[1] == str(last)
    assert len(extractor.await_args_list) == (4 if changed_source else 3 if fragmented else 2)
    if fragmented:
        assert [call.args[0] for call in extractor.await_args_list].count(texts[0]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("capped", [False, True])
@pytest.mark.parametrize("restriction", [None, "generation", "cause", "local"])
async def test_post_loop_refusal_projects_only_independently_qualified_committed_facts(
    env, monkeypatch, entity_queue, capped, restriction
):
    conv, first, extractor, _ = await prepare(env, monkeypatch)
    monkeypatch.setattr(
        "orchestrator.memory.summary._generate_or_update_summary_result",
        AsyncMock(return_value=None),
    )
    await env.pool.execute(
        "UPDATE messages SET content=$2 WHERE id=$1",
        first,
        env.memory.encryption.encrypt("A" * 3000),
    )
    tail = await env.memory.insert_message(
        conv, env.alice, "user", "Filtered tail", status="cancelled"
    )
    if capped:
        await env.memory.insert_message(conv, env.alice, "user", "B" * 3000, status="complete")
        monkeypatch.setattr(jobs, "MAX_EXTRACTION_CHUNKS_PER_JOB", 1)
    outcome = extractor.return_value

    async def changed_tail(*args, **kwargs):
        await env.pool.execute(
            "UPDATE messages SET content=$2 WHERE id=$1",
            tail["id"],
            env.memory.encryption.encrypt("Changed tail"),
        )
        return outcome

    extractor.side_effect = changed_tail
    original_checkpoint = fencing.checkpoint

    async def checkpoint_then_restrict(*args, **kwargs):
        result = await original_checkpoint(*args, **kwargs)
        if restriction == "generation":
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )
        elif restriction == "cause":
            await env.pool.execute(
                "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
            )
        elif restriction == "local":
            await env.pool.execute("UPDATE conversations SET pipeline='local' WHERE id=$1", conv)
        return result

    monkeypatch.setattr(fencing, "checkpoint", checkpoint_then_restrict)
    queue, pipeline = entity_queue
    result = await jobs.extract_memories(
        {"store": env.memory, "db_pool": env.pool, "redis": queue},
        env.alice,
        conv,
        origin_account_id=str(env.alice),
        origin_account_generation=7,
    )
    assert result["status"] == "skipped"
    if restriction is None:
        assert result["reason"] == "source_changed"
    assert await counts(env) == (1, 1)
    assert (await env.memory.get_last_extraction_cursor(conv))[1] == str(first)
    wires = await queued(queue, pipeline, "resolve_entities_job")
    if restriction is not None:
        assert wires == []
    else:
        saved = await env.pool.fetchval("SELECT id FROM memories")
        assert len(wires) == 1
        assert wires[0].args == (str(env.alice), json.dumps([str(saved)]))
        assert wires[0].kwargs == {
            "origin_account_id": str(env.alice),
            "origin_account_generation": 7,
        }
    assert await queued(queue, pipeline, "extract_memories") == []
    assert await queued(queue, pipeline, "generate_summary_job") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_offline_benchmark_helper_runs_real_extraction_without_queued_snapshot_escape(
    env, monkeypatch, failure
):
    from tests.benchmark_extraction import build_benchmark_transcript, invoke_benchmark_extraction

    conv, _, extractor, _ = await prepare(env, monkeypatch)
    first = build_benchmark_transcript(["First synthetic turn"])
    assert (await invoke_benchmark_extraction(env.memory, str(env.alice), str(conv), first))[
        "status"
    ] == "ok"
    first_call = extractor.await_args
    assert first_call is not None and "[User]: First synthetic turn" in first_call.args[0]
    before = await counts(env)
    if failure:
        extractor.return_value = ExtractionOutcome([], 0, 0, 0, 0, succeeded=False)
    cumulative = build_benchmark_transcript(["First synthetic turn", "B" * 10000])
    result = await invoke_benchmark_extraction(env.memory, str(env.alice), str(conv), cumulative)
    assert result["status"] == ("failed" if failure else "ok")
    if failure:
        assert await counts(env) == before
    else:
        assert result["processed_chunks"] == 4
        assert len(extractor.await_args_list) == 5
        assert extractor.await_args_list[0].args[0] == extractor.await_args_list[1].args[0]
        assert all(
            len(call.args[0]) <= jobs.MAX_EXTRACTION_INPUT_CHARS
            for call in extractor.await_args_list
        )
    assert (await run(env, conv, payload=cumulative)) == {
        "status": "skipped",
        "reason": "invalid_source",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", [None, "suspended", "route"])
async def test_offline_benchmark_actual_extractor_uses_real_qualified_account_compute(
    env, monkeypatch, denial
):
    import litellm
    from orchestrator import compute_runtime
    from orchestrator.entitlements.service import EntitlementService
    from tests.benchmark_extraction import invoke_benchmark_extraction
    from tests.qualified_compute import install_qualified_compute
    from tests.test_compute_runtime import install_routing, routing_document
    from tests.durable_tasks_support import seed_entitlement

    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "synthetic-benchmark-admission-pepper")
    model = "openrouter/test/explicit-model"
    install_qualified_compute(monkeypatch, models=(model,))
    install_routing(monkeypatch, routing_document([model]))
    # Actual account admission, reservation, settlement, extractor and guarded
    # completion. Only approved route evidence and external transport are synthetic.
    monkeypatch.setattr(compute_runtime, "EntitlementService", EntitlementService)
    monkeypatch.setattr(extraction, "BENCHMARK_MODE", True)
    monkeypatch.setattr(extraction, "BENCHMARK_EXTRACTION_MODEL", model)
    await seed_entitlement(env.pool, env.alice)
    if denial == "route":
        policy = compute_runtime.load_inference_policy()
        assert isinstance(policy.routes, dict)  # This fixture's explicitly synthetic route map.
        policy.routes.clear()
    conv = (await env.memory.create_conversation(env.alice, title="Offline admission"))["id"]
    if denial == "suspended":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )

    async def transport(**kwargs):
        scope = compute_runtime.current_scope()
        assert scope.user_id == env.alice and scope.background and scope.profile == "background"
        assert scope.auto_route and kwargs["model"] == model
        assert kwargs["extra_body"]["provider"]["zdr"] is True
        assert kwargs["extra_body"]["provider"]["allow_fallbacks"] is False
        # No account/source locks are held while an external call runs.
        async with env.pool.acquire() as conn, conn.transaction():
            await conn.fetchrow(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                env.alice,
            )
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"facts":[]}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": model,
        }

    outbound = AsyncMock(side_effect=transport)
    monkeypatch.setattr(litellm, "acompletion", outbound)
    payload = [{"role": "user", "content": "Fictional short turn"}]
    if denial:
        with pytest.raises(extraction.BenchmarkProviderError) as refused:
            await invoke_benchmark_extraction(env.memory, str(env.alice), str(conv), payload)
        cause = refused.value.__cause__
        assert isinstance(cause, compute_runtime.ComputeUnavailable)
        assert cause.code == (
            "capability_unavailable" if denial == "suspended" else "route_unavailable"
        )
        outbound.assert_not_awaited()
        assert await counts(env) == (0, 0)
    else:
        assert (await invoke_benchmark_extraction(env.memory, str(env.alice), str(conv), payload))[
            "status"
        ] == "ok"
        outbound.assert_awaited_once()
        reservations = await env.pool.fetch(
            "SELECT * FROM entitlement_reservations WHERE user_id=$1", env.alice
        )
        assert len(reservations) == 1
        assert reservations[0]["scope_id"] is not None and reservations[0]["background"]
        assert reservations[0]["status"] == "settled" and reservations[0]["actual_microusd"] == 0
        assert await counts(env) == (0, 1)
    with pytest.raises(compute_runtime.ComputeUnavailable):
        compute_runtime.current_scope()  # Scope cannot leak out of the offline operation.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed", ["missing", "extra", "duplicate", "version", "negative", "boolean", "past_end"]
)
async def test_malformed_references_refuse_terminally_without_provider_or_checkpoint(
    env, monkeypatch, malformed
):
    conv, mid, extractor, judge = await prepare(env, monkeypatch)
    _, messages = await fencing.read_page(env.memory, CapturedGeneration(env.alice, 7), conv)
    reference = {
        "message_id": str(mid),
        "source_version": jobs._extraction_source_version(messages[0]),
        "fragment_index": 0,
    }
    if malformed == "missing":
        del reference["fragment_index"]
    elif malformed == "extra":
        reference["extra"] = "not permitted"
    elif malformed == "version":
        reference["source_version"] = "malformed"
    elif malformed in {"negative", "boolean", "past_end"}:
        reference["fragment_index"] = {"negative": -1, "boolean": True, "past_end": 100}[malformed]
    refs = [reference, dict(reference)] if malformed == "duplicate" else [reference]
    result = await run(env, conv, payload={"_extraction_refs": refs})
    assert result == {"status": "skipped", "reason": "invalid_source"}
    extractor.assert_not_awaited()
    judge.assert_not_awaited()
    assert await counts(env) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["source", "generation", "cause", "local", "foreign"])
async def test_committed_summary_recovery_ignores_unrelated_message_snapshot_not_account_provenance(
    env, monkeypatch, entity_queue, mutation
):
    conv, mid, extractor, _ = await prepare(env, monkeypatch)
    assert await env.memory.update_conversation_summary(
        conv,
        "Already committed synthetic summary",
        expected_summary_updated_at=None,
        summarized_message_count=0,
        summary_continuation_pending=True,
        captured_generation=CapturedGeneration(env.alice, 7),
    )
    original_read = env.memory.read_summary_continuation_pending

    async def mutate_after_guarded_state(*args, **kwargs):
        pending = await original_read(*args, **kwargs)
        assert pending is True
        if mutation == "source":
            await env.pool.execute(
                "UPDATE messages SET content=$2 WHERE id=$1",
                mid,
                env.memory.encryption.encrypt("Changed extraction source"),
            )
        elif mutation == "generation":
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )
        elif mutation == "cause":
            await env.pool.execute(
                "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
            )
        elif mutation == "local":
            await env.pool.execute("UPDATE conversations SET pipeline='local' WHERE id=$1", conv)
        else:
            await env.pool.execute("UPDATE conversations SET user_id=$2 WHERE id=$1", conv, env.bob)
        return pending

    monkeypatch.setattr(env.memory, "read_summary_continuation_pending", mutate_after_guarded_state)
    queue, pipeline = entity_queue
    result = await jobs.extract_memories(
        {"store": env.memory, "db_pool": env.pool, "redis": queue},
        env.alice,
        conv,
        origin_account_id=str(env.alice),
        origin_account_generation=7,
    )
    assert result["status"] == "skipped"
    wires = await queued(queue, pipeline, "generate_summary_job")
    if mutation == "source":
        assert result["reason"] == "source_changed"
        assert len(wires) == 1
        assert wires[0].args == (str(conv), True)
        assert wires[0].kwargs == {
            "origin_account_id": str(env.alice),
            "origin_account_generation": 7,
        }
    else:
        assert wires == []
    row = await env.pool.fetchrow("SELECT summary,metadata FROM conversations WHERE id=$1", conv)
    assert row["summary"] == "Already committed synthetic summary"
    assert json.loads(row["metadata"])["summary_continuation_pending"] is True
    assert await counts(env) == (0, 0)
    extractor.assert_not_awaited()
    assert await queued(queue, pipeline, "resolve_entities_job") == []
    assert await queued(queue, pipeline, "extract_memories") == []
