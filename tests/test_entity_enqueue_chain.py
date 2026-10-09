"""Nonempty extraction must reach entity projection through both real adapters."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from arq import Retry
from arq.connections import RedisSettings, create_pool
from arq.constants import default_queue_name, job_key_prefix
from arq.jobs import deserialize_job

from orchestrator.memory.store import MemoryStore
from orchestrator.redis_account import account_prefix
from orchestrator.worker import jobs
from tests.qualified_compute import install_qualified_compute
from tests.test_redis_jobs import EnqueuePipeline, _queue


@pytest_asyncio.fixture(params=["recording", "live"])
async def entity_queue(request, monkeypatch):
    if request.param == "recording":
        pipeline = EnqueuePipeline()
        queue = _queue(monkeypatch, pipeline)
        try:
            yield queue, pipeline
        finally:
            await queue.aclose()
        return
    if not request.config.getoption("--p06-disposable-redis"):
        pytest.skip("Requires explicitly approved disposable Redis")
    queue = await create_pool(RedisSettings(host="127.0.0.1", port=56379, database=15))
    try:
        await queue.flushdb()
        try:
            yield queue, None
        finally:
            await queue.flushdb()
    finally:
        await queue.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_suffix", [False, True])
async def test_nonempty_extraction_native_entity_payload_executes_once(
    entity_queue, monkeypatch, failed_suffix
):
    queue, pipeline = entity_queue
    install_qualified_compute(monkeypatch)
    owner, conversation, memory_id = (uuid.UUID(int=n) for n in (11, 12, 13))
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(return_value={"user_id": owner, "pipeline": "cloud"})
    store.consume_summary_continuation_pending = AsyncMock(return_value=False)
    store.log_extraction = AsyncMock()
    store.get_memory = AsyncMock(return_value={"content": "User likes fictional astronomy"})
    ctx = {"store": store, "db_pool": object(), "redis": queue}
    fact = SimpleNamespace(
        content="User likes fictional astronomy", category="fact", confidence=0.9, slot=None
    )
    outcome = SimpleNamespace(
        facts=[fact],
        succeeded=True,
        raw_count=1,
        calibrated_count=1,
        rejected_count=0,
        slot_coverage={},
        model_used=None,
    )
    failure = SimpleNamespace(facts=[], succeeded=False)
    extractor = AsyncMock(side_effect=[outcome, failure, failure] if failed_suffix else [outcome])
    monkeypatch.setattr("orchestrator.memory.extraction.extract_facts_from_text", extractor)
    dedup = AsyncMock(
        return_value=SimpleNamespace(new=[{"id": memory_id}], merged=[], superseded=[])
    )
    monkeypatch.setattr("orchestrator.memory.dedup.deduplicate_facts", dedup)
    monkeypatch.setattr(
        "orchestrator.memory.summary._generate_or_update_summary_result",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(jobs, "MAX_EXTRACTION_CHUNKS_PER_JOB", 8)
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    messages = [
        {
            "id": str(uuid.UUID(int=20 + i)),
            "role": "user",
            "content": "x" * 3000,
            "created_at": (base + timedelta(seconds=i)).isoformat(),
        }
        for i in range(2 if failed_suffix else 1)
    ]
    if failed_suffix:
        with pytest.raises(Retry):
            await jobs.extract_memories(ctx, owner, conversation, json.dumps(messages))
    else:
        result = await jobs.extract_memories(ctx, owner, conversation, json.dumps(messages))
        assert result["status"] == "ok"
    dedup.assert_awaited_once()
    store.log_extraction.assert_awaited_once()
    assert extractor.await_count == (3 if failed_suffix else 1)

    if pipeline is not None:
        writes = [args for command, args in pipeline.commands if command == "psetex"]
        assert len(writes) == 1
        key, _, payload = writes[0]
    else:
        identifiers = await queue.zrange(default_queue_name, 0, -1)
        assert len(identifiers) == 1
        key = job_key_prefix + identifiers[0].decode()
        payload = await queue.get(key)
    assert key.startswith(job_key_prefix + account_prefix(owner) + ":job:")
    decoded = deserialize_job(payload)
    assert decoded.function == "resolve_entities_job"
    assert decoded.args == (str(owner), json.dumps([str(memory_id)]))
    assert decoded.kwargs == {}

    async def project(uid, actual_store, contents, *, use_spacy):
        assert uid == owner and actual_store is store and use_spacy is False
        assert contents == [(fact.content, memory_id, None)]
        return SimpleNamespace(resolutions=[object()])

    projection = AsyncMock(side_effect=project)
    persisted = AsyncMock(return_value=[uuid.UUID(int=30)])
    monkeypatch.setattr(jobs, "extract_and_resolve_entities", projection)
    monkeypatch.setattr(jobs, "persist_extraction_result", persisted)
    result = await jobs.resolve_entities_job(ctx, *decoded.args, **decoded.kwargs)
    assert result["status"] == "ok" and result["memories_processed"] == 1
    assert result["entities_created"] == 1
    projection.assert_awaited_once()
    persisted.assert_awaited_once()
    store.get_memory.assert_awaited_once_with(memory_id)
