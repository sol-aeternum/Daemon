"""Opt-in real Redis contract checks: task-owned loopback instance ONLY.

Run with --p06-disposable-redis against a separately approved disposable Redis
container on 127.0.0.1:56379, persistence disabled, no live mounts. Database15
is cleared per test. No application DATABASE_URL, credentials or providers.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from arq.connections import RedisSettings, create_pool
from arq.constants import abort_jobs_ss, default_queue_name, job_key_prefix, result_key_prefix
from arq.jobs import deserialize_result, serialize_result
from arq.utils import timestamp_ms
from arq.worker import Retry, func

from orchestrator.redis_jobs import account_job_id, completion_key, enqueue_account_job
from orchestrator.services.fetch.cache import FetchCache, _PRUNE_INDEX, result_cache_key
from orchestrator.services.fetch.models import FetchResult
from orchestrator.worker import audit
from orchestrator.config import get_settings
from orchestrator.db import AppState
from orchestrator.auth import AdminOrDeviceAuth, AuthenticatedDevice
from orchestrator.routes.memories import consolidate_memories_endpoint, dream_memories_endpoint
from orchestrator.redis_account import account_prefix

OWNER = uuid.UUID("00000000-0000-4000-8000-000000000001")
MESSAGE = uuid.UUID("00000000-0000-4000-8000-000000000002")
CONTENT = "FICTIONAL_RESULT_CONTENT_SENTINEL"


async def unused_job(ctx):
    raise AssertionError("This function must never execute")


@pytest_asyncio.fixture
async def redis(request):
    if not request.config.getoption("--p06-disposable-redis"):
        pytest.skip("Requires explicitly approved disposable Redis")
    queue = await create_pool(RedisSettings(host="127.0.0.1", port=56379, database=15))
    try:
        await queue.flushdb()
        try:
            yield queue
        finally:
            await queue.flushdb()
    finally:
        await queue.aclose()


def original_result(job_id, function="generate_title", *, success=True, result=CONTENT):
    now = timestamp_ms()
    return serialize_result(
        function,
        (str(MESSAGE),),
        {},
        1,
        now,
        success,
        result,
        now,
        now,
        function,
        default_queue_name,
        job_id=job_id,
    )


@pytest.mark.asyncio
async def test_real_terminal_result_suppression_audit_and_completion_dedup(redis, monkeypatch):
    async def title(ctx, message_id):
        return CONTENT

    worker = audit.AuditedWorker(
        functions=[func(title, name="generate_title")], redis_pool=redis, handle_signals=False
    )
    observations = []

    async def observe(ctx, data, **kwargs):
        decoded = deserialize_result(data)
        assert decoded.result == CONTENT
        assert decoded.job_id is not None
        assert not await redis.exists(result_key_prefix + decoded.job_id)
        assert await redis.exists(completion_key(decoded.job_id))
        observations.append(decoded)

    monkeypatch.setattr(audit, "_run_audit_with_timeout", observe)
    first = await enqueue_account_job(
        redis, "generate_title", str(MESSAGE), user_id=OWNER, job_id="title:fictional"
    )
    assert first is not None
    await worker.run_job(first.job_id, int(await redis.zscore(default_queue_name, first.job_id)))
    assert len(observations) == 1
    assert not await redis.exists(job_key_prefix + first.job_id)
    ttl = await redis.pttl(completion_key(first.job_id))
    assert 3_590_000 <= ttl <= 3_600_000
    assert (
        await enqueue_account_job(
            redis, "generate_title", str(MESSAGE), user_id=OWNER, job_id="title:fictional"
        )
        is None
    )
    other = await enqueue_account_job(
        redis, "generate_title", str(MESSAGE), user_id=uuid.UUID(int=3), job_id="title:fictional"
    )
    assert other is not None and other.job_id != first.job_id


@pytest.mark.asyncio
async def test_real_simultaneous_producers_accept_only_one(redis):
    jobs = await asyncio.gather(
        *[
            enqueue_account_job(
                redis, "generate_title", str(MESSAGE), user_id=OWNER, job_id="title:race"
            )
            for _ in range(20)
        ]
    )
    assert sum(job is not None for job in jobs) == 1
    assert await redis.zcard(default_queue_name) == 1


@pytest.mark.asyncio
async def test_real_marker_publication_between_exists_and_exec_blocks_enqueue(redis, monkeypatch):
    identifier = account_job_id(OWNER, "title:expired-race")
    observed_empty, release = asyncio.Event(), asyncio.Event()
    native_pipeline = redis.pipeline

    class GatePipeline:
        def __init__(self, base):
            self.base = base

        async def __aenter__(self):
            await self.base.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.base.__aexit__(*args)

        def __getattr__(self, name):
            return getattr(self.base, name)

        async def exists(self, *keys):
            value = await self.base.exists(*keys)
            assert value == 0
            observed_empty.set()
            await release.wait()
            return value

    monkeypatch.setattr(redis, "pipeline", lambda **kwargs: GatePipeline(native_pipeline(**kwargs)))
    monkeypatch.setattr(audit, "_run_audit_with_timeout", AsyncMock())
    worker = audit.AuditedWorker(functions=[unused_job], redis_pool=redis, handle_signals=False)
    producer = asyncio.create_task(
        enqueue_account_job(
            redis, "generate_title", str(MESSAGE), user_id=OWNER, job_id="title:expired-race"
        )
    )
    await asyncio.wait_for(observed_empty.wait(), timeout=5)
    try:
        await worker.finish_job(
            identifier, True, original_result(identifier), 3600, False, None, None
        )
    finally:
        release.set()
    assert await asyncio.wait_for(producer, timeout=5) is None
    assert await redis.exists(completion_key(identifier))
    assert not await redis.exists(job_key_prefix + identifier, result_key_prefix + identifier)


@pytest.mark.asyncio
async def test_real_retry_keeps_job_and_updates_score_without_marker_or_audit(redis, monkeypatch):
    async def extraction(ctx, message_id):
        raise Retry(defer=5)

    worker = audit.AuditedWorker(
        functions=[func(extraction, name="extract_memories")],
        redis_pool=redis,
        handle_signals=False,
    )
    spy = AsyncMock()
    monkeypatch.setattr(audit, "_run_audit_with_timeout", spy)
    job = await enqueue_account_job(
        redis, "extract_memories", str(MESSAGE), user_id=OWNER, job_id="extract:retry"
    )
    assert job is not None
    before = await redis.zscore(default_queue_name, job.job_id)
    await worker.run_job(job.job_id, int(before))
    assert await redis.zscore(default_queue_name, job.job_id) >= before + 5000
    assert await redis.exists(job_key_prefix + job.job_id)
    assert not await redis.exists(result_key_prefix + job.job_id, completion_key(job.job_id))
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_preexecution_unknown_function_audits_but_never_stores_result(
    redis, monkeypatch
):
    worker = audit.AuditedWorker(functions=[unused_job], redis_pool=redis, handle_signals=False)
    spy = AsyncMock()
    monkeypatch.setattr(audit, "_run_audit_with_timeout", spy)
    job = await enqueue_account_job(
        redis, "not_registered", str(MESSAGE), user_id=OWNER, job_id="unknown:fixture"
    )
    assert job is not None
    await worker.run_job(job.job_id, int(await redis.zscore(default_queue_name, job.job_id)))
    spy.assert_awaited_once()
    assert spy.await_args is not None
    assert deserialize_result(spy.await_args.args[1]).success is False
    assert await redis.exists(completion_key(job.job_id))
    assert not await redis.exists(result_key_prefix + job.job_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expired", "decode", "max_try", "abort"])
async def test_real_other_preexecution_failures_never_store_results(redis, monkeypatch, failure):
    worker = audit.AuditedWorker(
        functions=[func(unused_job, name="run_chat_task", max_tries=1, keep_result=3600)],
        redis_pool=redis,
        handle_signals=False,
        allow_abort_jobs=True,
    )
    spy = AsyncMock()
    monkeypatch.setattr(audit, "_run_audit_with_timeout", spy)
    job = await enqueue_account_job(
        redis,
        "run_chat_task",
        str(MESSAGE),
        user_id=OWNER,
        job_id=f"task:{failure}",
        _job_try=2 if failure == "max_try" else None,
    )
    assert job is not None
    if failure == "expired":
        await redis.delete(job_key_prefix + job.job_id)
    elif failure == "decode":
        await redis.set(job_key_prefix + job.job_id, b"fictional-invalid-serialization")
    elif failure == "abort":
        await redis.zadd(abort_jobs_ss, {job.job_id: timestamp_ms()})
    await worker.run_job(job.job_id, int(await redis.zscore(default_queue_name, job.job_id)))
    spy.assert_awaited_once()
    assert spy.await_args is not None
    assert deserialize_result(spy.await_args.args[1]).success is False
    assert await redis.exists(completion_key(job.job_id))
    assert not await redis.exists(result_key_prefix + job.job_id, job_key_prefix + job.job_id)
    assert await redis.zscore(default_queue_name, job.job_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("function", sorted(audit.SERIALIZATION_ONLY_JOBS))
@pytest.mark.parametrize("failure", [False, True])
async def test_real_zero_retention_execution_policies(redis, monkeypatch, function, failure):
    async def outcome(ctx):
        if failure:
            raise RuntimeError(CONTENT)
        return CONTENT

    worker = audit.AuditedWorker(
        functions=[func(outcome, name=function, keep_result=3600)],
        redis_pool=redis,
        handle_signals=False,
    )
    spy = AsyncMock()
    monkeypatch.setattr(audit, "_run_audit_with_timeout", spy)
    job = await enqueue_account_job(redis, function, user_id=OWNER, job_id=f"fixture:{function}")
    assert job is not None
    await worker.run_job(job.job_id, int(await redis.zscore(default_queue_name, job.job_id)))
    spy.assert_awaited_once()
    assert not await redis.exists(result_key_prefix + job.job_id, job_key_prefix + job.job_id)
    assert bool(await redis.exists(completion_key(job.job_id))) == (
        failure and function == "extract_memories"
    )
    accepted = await enqueue_account_job(
        redis, function, user_id=OWNER, job_id=f"fixture:{function}"
    )
    assert (accepted is None) == (failure and function == "extract_memories")


@pytest.mark.asyncio
async def test_real_approved_endpoint_job_ids_and_unchanged_shared_dream(redis):
    device = AuthenticatedDevice(
        device_id=uuid.UUID(int=5), user_id=OWNER, session_id=uuid.UUID(int=6)
    )
    state = AppState(settings=get_settings(), redis=redis)
    consolidated = await consolidate_memories_endpoint(app_state=state, auth=device)
    dreamed = await dream_memories_endpoint(app_state=state, auth=AdminOrDeviceAuth(device, False))
    for result in (consolidated, dreamed):
        assert set(result) == {"status", "job_id", "user_id"}
        assert result["status"] == "enqueued" and result["user_id"] == str(OWNER)
        assert result["job_id"].startswith(account_prefix(OWNER) + ":job:")
        assert await redis.exists(job_key_prefix + result["job_id"])
    shared = await dream_memories_endpoint(app_state=state, auth=AdminOrDeviceAuth(device, True))
    assert shared["job_id"].startswith("dream:all:") and shared["user_id"] == "all"


@pytest.mark.asyncio
async def test_real_shared_cron_serializes_counts_only_but_audits_original(redis, monkeypatch):
    async def dreaming(ctx):
        return {"users_processed": 1, "error_count": 1, "errors": [CONTENT], "user_id": str(OWNER)}

    name = "cron:run_scheduled_dreaming_job"
    identifier = f"{name}:{timestamp_ms()}"
    worker = audit.AuditedWorker(
        functions=[func(dreaming, name=name)], redis_pool=redis, handle_signals=False
    )
    spy = AsyncMock()
    monkeypatch.setattr(audit, "_run_audit_with_timeout", spy)
    job = await redis.enqueue_job(name, _job_id=identifier)
    await worker.run_job(job.job_id, int(await redis.zscore(default_queue_name, job.job_id)))
    data = await redis.get(result_key_prefix + identifier)
    assert CONTENT.encode() not in data and str(OWNER).encode() not in data
    stored = deserialize_result(data)
    assert stored.result == {"users_processed": 1, "error_count": 1}
    assert stored.args == () and stored.kwargs == {}
    assert spy.await_args is not None
    assert CONTENT in deserialize_result(spy.await_args.args[1]).result["errors"]


@pytest.mark.asyncio
async def test_real_shared_cache_ownership_survives_renewal_and_prunes_only_absence(redis):
    caches = [FetchCache(user_id=uuid.UUID(int=owner)) for owner in (1, 2)]
    for cache in caches:
        cache.redis = redis
    value = FetchResult(
        url="https://example.invalid/fictional?input=URL_SENTINEL",
        content=CONTENT,
        title="Fictional",
        strategy_used="direct",
        cached=False,
        fetch_time_ms=1,
        content_length=len(CONTENT),
    )
    assert await caches[0].set(value.url, value, ttl=1)
    assert await caches[1].set(value.url, value, ttl=30)
    key = result_cache_key(value.url)
    for cache in caches:
        assert await redis.sismember(cache.owner_index, key)
        assert await redis.ttl(cache.owner_index) == -1
        assert await redis.eval(_PRUNE_INDEX, 2, cache.owner_index, cache.prune_cursor_key) == 0
    assert await redis.ttl(key) > 1
    await redis.delete(key)
    for cache in caches:
        assert await redis.eval(_PRUNE_INDEX, 2, cache.owner_index, cache.prune_cursor_key) == 1
        assert not await redis.exists(cache.prune_cursor_key)


@pytest.mark.asyncio
async def test_real_corrupt_cache_index_cannot_publish_unowned_content(redis):
    cache = FetchCache(user_id=OWNER)
    cache.redis = redis
    await redis.set(cache.owner_index, b"fictional-invalid-index-type")
    value = FetchResult(
        url="https://example.invalid/fictional",
        content=CONTENT,
        title="Fictional",
        strategy_used="direct",
        cached=False,
        fetch_time_ms=1,
        content_length=len(CONTENT),
    )
    assert await cache.set(value.url, value) is False
    assert not await redis.exists(result_cache_key(value.url))
