"""Approved disposable Redis only: actual Lua progress and bounded scan work."""

import asyncio
import json
import uuid

import pytest

from orchestrator.services.fetch.cache import FetchCache, _PRUNE_INDEX, result_cache_key
from tests.test_fetch_ownership import result
from tests.test_redis_jobs_live import redis as redis

OWNER = uuid.UUID(int=111)


def fresh_cache(queue):
    cache = FetchCache(user_id=OWNER)
    cache.redis = queue
    return cache


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_real_multi_page_progress_shared_by_fresh_instances(redis, concurrent):
    cache = fresh_cache(redis)
    stale = {f"fetch:v3:{n:064x}" for n in range(1024)}
    live = {f"fetch:v3:{n:064x}" for n in range(1024, 1280)}
    await redis.sadd(cache.owner_index, *stale, *live)
    for key in live:
        await redis.set(key, b"fictional-live", ex=60)
    value = result()

    async def write():
        return await fresh_cache(redis).set(value.url, value)

    assert await write()
    cursor = await redis.get(cache.prune_cursor_key)
    assert cursor is not None and cursor.isdigit() and cursor != b"0"
    assert await redis.ttl(cache.prune_cursor_key) == -1
    assert cache.prune_cursor_key is not None
    assert str(OWNER) not in cache.prune_cursor_key and "http" not in cache.prune_cursor_key
    assert (await redis.smembers(cache.owner_index)) & {key.encode() for key in stale}
    if concurrent:
        assert all(await asyncio.gather(*(write() for _ in range(100))))
    else:
        for _ in range(100):
            assert await write()
    assert await redis.smembers(cache.owner_index) == {
        key.encode() for key in live | {result_cache_key(value.url)}
    }
    # Finish a cycle on the now-stable live index and remove completed progress.
    for _ in range(30):
        assert await write()
        if not await redis.exists(cache.prune_cursor_key):
            break
    else:
        pytest.fail("stable index scan never completed")


@pytest.mark.asyncio
async def test_oversized_and_empty_nonterminal_pages_are_consumed_without_losing_work(redis):
    cache = fresh_cache(redis)
    stale = [f"fetch:v3:{n:064x}" for n in range(70)]
    renewed = f"fetch:v3:{71:064x}"
    await redis.sadd(cache.owner_index, *stale, renewed)
    # Another owner has renewed the shared value before atomic maintenance.
    other = FetchCache(user_id=uuid.UUID(int=112))
    await redis.set(renewed, b"fictional-renewed", ex=60)
    await redis.sadd(other.owner_index, renewed)
    # Shadow only SSCAN's return boundary; execute all actual production Lua
    # cursor/type checks, prune EXISTS/SREM and progress publication in Redis.
    shim = """
local native = redis
local redis = {call = native.call, pcall = function(command, ...)
  if command == 'SSCAN' then
    local args = {...}
    local pages = cjson.decode(ARGV[1])
    return pages[args[2]]
  end
  return native.pcall(command, ...)
end}
"""
    pages = json.dumps({"0": ["7", stale[:69] + [renewed]], "7": ["9", []], "9": ["0", stale[69:]]})
    script = shim + _PRUNE_INDEX
    assert await redis.eval(script, 2, cache.owner_index, cache.prune_cursor_key, pages) == 69
    assert await redis.get(cache.prune_cursor_key) == b"7"
    assert await redis.smembers(cache.owner_index) == {renewed.encode(), stale[-1].encode()}
    assert await redis.eval(script, 2, cache.owner_index, cache.prune_cursor_key, pages) == 0
    assert await redis.get(cache.prune_cursor_key) == b"9"
    assert await redis.eval(script, 2, cache.owner_index, cache.prune_cursor_key, pages) == 1
    assert not await redis.exists(cache.prune_cursor_key)
    assert await redis.sismember(cache.owner_index, renewed)
    assert await redis.sismember(other.owner_index, renewed)
    assert await redis.exists(renewed)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["garbage", "18446744073709551616", "wrong-type"])
async def test_corrupt_cursor_recovers_without_resetting_live_ownership(redis, state):
    cache = fresh_cache(redis)
    if state == "wrong-type":
        await redis.sadd(cache.prune_cursor_key, b"fictional-control-corruption")
    else:
        await redis.set(cache.prune_cursor_key, state)
    value = result()
    assert await cache.set(value.url, value)
    assert await redis.sismember(cache.owner_index, result_cache_key(value.url))
    assert await redis.exists(result_cache_key(value.url))
    assert not await redis.exists(cache.prune_cursor_key)  # single-page cycle completed


@pytest.mark.asyncio
async def test_empty_index_clears_only_its_progress_and_wrong_index_type_is_fail_closed(redis):
    cache = fresh_cache(redis)
    await redis.set(cache.prune_cursor_key, "7")
    unrelated = "fetch:v3:fictional-unrelated"
    await redis.set(unrelated, b"fictional-retained")
    assert await redis.eval(_PRUNE_INDEX, 2, cache.owner_index, cache.prune_cursor_key) == 0
    assert not await redis.exists(cache.prune_cursor_key)
    assert await redis.exists(unrelated)
    await redis.set(cache.owner_index, b"fictional-corrupt-index")
    await redis.set(cache.prune_cursor_key, "7")
    assert await redis.eval(_PRUNE_INDEX, 2, cache.owner_index, cache.prune_cursor_key) == -1
    assert await redis.get(cache.owner_index) == b"fictional-corrupt-index"
    assert await redis.get(cache.prune_cursor_key) == b"7"


@pytest.mark.asyncio
async def test_real_entity_suffix_dedup_and_owner_partition_without_raw_owner(redis):
    from orchestrator.worker.jobs import enqueue_with_debounce

    conversation, memory = uuid.UUID(int=222), uuid.UUID(int=333)
    suffix = f"resolve_entities_{conversation}_{memory}"
    owners = [OWNER, uuid.UUID(int=112)]
    jobs = []
    for owner in owners:
        args = (str(owner), json.dumps([str(memory)]))
        job = await enqueue_with_debounce(
            redis, "resolve_entities_job", user_id=owner, job_id=suffix, args=args
        )
        assert job is not None and str(owner) not in job.job_id
        assert (
            await enqueue_with_debounce(
                redis, "resolve_entities_job", user_id=owner, job_id=suffix, args=args
            )
            is None
        )
        jobs.append(job)
    assert jobs[0].job_id != jobs[1].job_id
