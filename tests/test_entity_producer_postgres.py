"""Original capture through actual extraction enqueue/entity worker on real PG.

The native queue adapters run with recording pipelines AND opt-in owned Redis.
Only providers/compute are scripted; facts, checkpoints and projection SQL are real.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock

import pytest
from arq import Retry
from arq.constants import default_queue_name, job_key_prefix
from arq.jobs import deserialize_job

from orchestrator.memory import entities
from orchestrator.memory.extraction import ExtractedFact, ExtractionOutcome
from orchestrator.memory.embedding import EmbeddingConfigurationError
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.worker import jobs
from tests.durable_tasks_support import expire_lease
from tests.test_entity_account_guard_postgres import env as env, prepare, rows
from tests.test_entity_enqueue_chain import entity_queue as entity_queue
from tests.test_task_api_postgres import api as api


async def queued(queue, pipeline, function):
    if pipeline is not None:
        wires = [args[2] for command, args in pipeline.commands if command == "psetex"]
    else:
        keys = await queue.zrange(default_queue_name, 0, -1)
        wires = [await queue.get(job_key_prefix + key.decode()) for key in keys]
    return [job for wire in wires if (job := deserialize_job(wire)).function == function]


def script_facts(monkeypatch, *, failed_suffix=False):
    outcome = ExtractionOutcome(
        facts=[ExtractedFact("User likes Alice.", "fact", 0.95)],
        raw_count=1,
        calibrated_count=1,
        rejected_count=0,
        slot_coverage=0,
    )
    failure = SimpleNamespace(facts=[], succeeded=False)
    extractor = AsyncMock(
        side_effect=[outcome, failure, failure] if failed_suffix else None, return_value=outcome
    )
    monkeypatch.setattr("orchestrator.memory.extraction.extract_facts_from_text", extractor)
    monkeypatch.setattr(
        "orchestrator.memory.dedup.embed_documents_with_metadata",
        AsyncMock(side_effect=EmbeddingConfigurationError("scripted lexical persistence")),
    )
    # Semantic judge requests are conservatively declined, never sent externally.
    monkeypatch.setattr(
        "orchestrator.memory.equivalence.guarded_completion",
        AsyncMock(side_effect=EmbeddingConfigurationError("scripted judge unavailable")),
    )
    monkeypatch.setattr(
        "orchestrator.memory.summary.guarded_completion",
        AsyncMock(side_effect=AssertionError("short fixture needs no summary model")),
    )
    return extractor


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_suffix", [False, True])
async def test_actual_fact_checkpoint_native_enqueue_worker_preserves_origin(
    env, monkeypatch, entity_queue, failed_suffix
):
    queue, pipeline = entity_queue
    _, conv, _, _ = await prepare(env, monkeypatch)
    await env.pool.execute("DELETE FROM memories WHERE user_id=$1", env.alice)
    for _ in range(2 if failed_suffix else 1):
        await env.memory.insert_message(conv, env.alice, "user", "x" * 3000, status="complete")
    extractor = script_facts(monkeypatch, failed_suffix=failed_suffix)
    monkeypatch.setattr(jobs, "MAX_EXTRACTION_CHUNKS_PER_JOB", 8)
    ctx = {"store": env.memory, "db_pool": env.pool, "redis": queue}
    origin = {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    if failed_suffix:
        with pytest.raises(Retry):
            await jobs.extract_memories(ctx, env.alice, conv, **origin)
    else:
        assert (await jobs.extract_memories(ctx, env.alice, conv, **origin))["status"] == "ok"
    assert extractor.await_count == (3 if failed_suffix else 1)
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_extraction_log WHERE conversation_id=$1", conv
        )
        == 1
    )
    memories = await env.pool.fetch("SELECT id FROM memories WHERE user_id=$1", env.alice)
    assert len(memories) == 1
    wire = (await queued(queue, pipeline, "resolve_entities_job"))[0]
    assert wire.kwargs == origin and wire.args == (
        str(env.alice),
        json.dumps([str(memories[0]["id"])]),
    )
    result = await jobs.resolve_entities_job(ctx, *wire.args, **wire.kwargs)
    assert result["status"] == "ok" and result["memories_processed"] == 1
    saved = await rows(env)
    assert any(env.memory.encryption.decrypt(row["canonical_name"]) == "Alice" for row in saved)
    assert all(row["source_memory_id"] == memories[0]["id"] for row in saved)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin_kind", ["legacy", "malformed", "foreign"])
async def test_invalid_origin_suppresses_only_entities_and_preserves_actual_fact_checkpoint(
    env, monkeypatch, entity_queue, origin_kind
):
    queue, pipeline = entity_queue
    _, conv, _, _ = await prepare(env, monkeypatch)
    await env.pool.execute("DELETE FROM memories WHERE user_id=$1", env.alice)
    await env.memory.insert_message(
        conv, env.alice, "user", "Fictional short fact source", status="complete"
    )
    script_facts(monkeypatch)
    payload = (
        {}
        if origin_kind == "legacy"
        else {
            "origin_account_id": str(env.bob if origin_kind == "foreign" else env.alice),
            "origin_account_generation": True if origin_kind == "malformed" else 7,
        }
    )
    result = await jobs.extract_memories(
        {"store": env.memory, "db_pool": env.pool, "redis": queue}, env.alice, conv, **payload
    )
    assert result["status"] == "ok"
    assert await env.pool.fetchval("SELECT count(*) FROM memories WHERE user_id=$1", env.alice) == 1
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_extraction_log WHERE conversation_id=$1", conv
        )
        == 1
    )
    assert await queued(queue, pipeline, "resolve_entities_job") == []
    assert await rows(env) == []


@pytest.mark.asyncio
async def test_chunk_continuation_and_entity_jobs_share_original_capture(
    env, monkeypatch, entity_queue
):
    queue, pipeline = entity_queue
    _, conv, _, _ = await prepare(env, monkeypatch)
    await env.pool.execute("DELETE FROM memories WHERE user_id=$1", env.alice)
    for _ in range(2):
        await env.memory.insert_message(conv, env.alice, "user", "x" * 3000, status="complete")
    script_facts(monkeypatch)
    monkeypatch.setattr(jobs, "MAX_EXTRACTION_CHUNKS_PER_JOB", 1)
    origin = {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    ctx = {"store": env.memory, "db_pool": env.pool, "redis": queue}
    await jobs.extract_memories(ctx, env.alice, conv, **origin)
    entity = (await queued(queue, pipeline, "resolve_entities_job"))[0]
    continuation = (await queued(queue, pipeline, "extract_memories"))[0]
    assert entity.kwargs == continuation.kwargs == origin
    # A resumed continuation cannot recapture after a reset. Facts are outside
    # this fence; entity scheduling still carries 7, and the actual worker refuses.
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
    )
    await jobs.extract_memories(ctx, *continuation.args, **continuation.kwargs)
    result = await jobs.resolve_entities_job(ctx, *entity.args, **entity.kwargs)
    assert result["status"] == "skipped" and result["errors"] == ["stale_generation"]
    assert await rows(env) == []
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_extraction_log WHERE conversation_id=$1", conv
        )
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("reset_after_accept", [False, True])
async def test_http_capture_through_native_queue_extraction_and_entity_worker(
    api, monkeypatch, entity_queue, durable, reset_after_accept
):
    from orchestrator import main, redis_jobs
    from orchestrator.tasks import runner
    from tests.qualified_compute import install_qualified_compute

    env = api.env
    queue, pipeline = entity_queue
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "synthetic-entity-producer-pepper")
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
    target = type(env.tasks) if durable else env.memory
    name = "accept" if durable else "insert_message"
    original_source = getattr(target, name)

    async def acceptance(*args, **kwargs):
        assert captures == [CapturedGeneration(env.alice, 7)]
        result = await original_source(*args, **kwargs)
        if reset_after_accept:
            await env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )
        return result

    monkeypatch.setattr(target, name, acceptance)

    async def native_enqueue(_queue, function, *args, **kwargs):
        return await redis_jobs.enqueue_account_job(queue, function, *args, **kwargs)

    monkeypatch.setattr("orchestrator.daemon.enqueue_account_job", native_enqueue)
    request = asyncio.create_task(
        api.client.post(
            "/chat",
            json={"message": "Fictional entity source"},
            headers={"X-Daemon-Client-Features": "task-cancel, task-reset" if durable else ""},
        )
    )
    try:
        if durable:
            async with asyncio.timeout(10):
                while not any(name == "run_chat_task" for name, _, _ in api.redis.enqueued):
                    await asyncio.sleep(0)
            tid = next(args[0] for name, args, _ in api.redis.enqueued if name == "run_chat_task")
            claim = await env.tasks.claim(UUID(tid), worker_id="abandoned", lease_s=45)
            assert claim is not None and claim.task_input["summary_origin_account_generation"] == 7
            await expire_lease(env, claim.task_id)
            assert await runner.run_chat_task(api.ctx(), tid) == "completed"
            assert (
                await env.pool.fetchval(
                    "SELECT content_generation FROM tasks WHERE id=$1", UUID(tid)
                )
                != 7
            )
        assert (await asyncio.wait_for(request, 15)).status_code == 200
        assert captures == [CapturedGeneration(env.alice, 7)]
        extract = (await queued(queue, pipeline, "extract_memories"))[0]
        assert extract.kwargs == {
            "origin_account_id": str(env.alice),
            "origin_account_generation": 7,
        }
        script_facts(monkeypatch)
        # Script existing scopes/providers, not any SQL or writer guard.
        install_qualified_compute(monkeypatch)
        monkeypatch.setattr(
            entities,
            "embed_query",
            AsyncMock(side_effect=EmbeddingConfigurationError("scripted lexical entity path")),
        )
        monkeypatch.setattr(
            entities,
            "guarded_completion",
            AsyncMock(side_effect=AssertionError("no canonical confirmation expected")),
        )
        ctx = {**api.ctx(), "redis": queue}
        await jobs.extract_memories(ctx, *extract.args, **extract.kwargs)
        entity = (await queued(queue, pipeline, "resolve_entities_job"))[0]
        assert entity.kwargs == extract.kwargs
        result = await jobs.resolve_entities_job(ctx, *entity.args, **entity.kwargs)
        assert result["status"] == ("skipped" if reset_after_accept else "ok")
        assert bool(await rows(env)) is not reset_after_accept
    finally:
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_before_extraction", [False, True])
async def test_native_stream_duplicate_followup_retains_origin_to_entity_worker(
    env, monkeypatch, entity_queue, reset_before_extraction
):
    from typing import Any, cast
    from orchestrator.config import Settings, ProviderConfig
    from orchestrator.daemon import stream_sse_chat

    queue, pipeline = entity_queue
    _, conv, _, _ = await prepare(env, monkeypatch)
    await env.pool.execute("DELETE FROM memories WHERE user_id=$1", env.alice)
    if pipeline is not None:
        # Model key existence faithfully while running the actual native adapter;
        # never open a socket to an ambient Redis for the recording variant.
        async def exists(*keys):
            present = {args[0] for command, args in pipeline.commands if command == "psetex"}
            return int(any(key in present for key in keys))

        monkeypatch.setattr(pipeline, "exists", exists)
        monkeypatch.setattr(queue, "delete", AsyncMock(return_value=0))

    async def provider(**kwargs):
        yield {"type": "content_delta", "content": "Fictional reply"}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", provider)

    async def connected():
        return False

    for _ in range(2):
        async for _frame in stream_sse_chat(
            settings=cast(Any, Settings)(_env_file=None, mock_llm=False),
            provider_config=ProviderConfig(name="openrouter", model="synthetic"),
            system_prompt="Synthetic",
            user_message="Synthetic",
            request_id="req_entity_followup",
            conversation_id=f"conv_{conv}",
            is_disconnected=connected,
            memory_store=env.memory,
            user_id=env.alice,
            conversation_uuid=conv,
            queue=queue,
            summary_generation=CapturedGeneration(env.alice, 7),
        ):
            pass
    extracts = await queued(queue, pipeline, "extract_memories")
    assert len(extracts) == 2  # base plus actual duplicate-induced follow-up
    origin = {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    assert all(job.kwargs == origin for job in extracts)
    if reset_before_extraction:
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
        )
    script_facts(monkeypatch)
    ctx = {"store": env.memory, "db_pool": env.pool, "redis": queue}
    await jobs.extract_memories(ctx, *extracts[-1].args, **extracts[-1].kwargs)
    entity = (await queued(queue, pipeline, "resolve_entities_job"))[0]
    assert entity.kwargs == origin
    result = await jobs.resolve_entities_job(ctx, *entity.args, **entity.kwargs)
    assert result["status"] == ("skipped" if reset_before_extraction else "ok")
    assert bool(await rows(env)) is not reset_before_extraction
