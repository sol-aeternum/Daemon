"""Ownership publication and failure boundaries; all content is fictional."""

import uuid
from unittest.mock import AsyncMock

import pytest

from orchestrator.services.fetch.cache import (
    FetchCache,
    _PRUNE_INDEX,
    _PUBLISH_OWNED,
    result_cache_key,
)
from orchestrator.services.fetch.models import FetchResult


def result() -> FetchResult:
    return FetchResult(
        url="https://example.invalid/fictional?query=FICTIONAL_URL_SENTINEL",
        content="FICTIONAL_PAGE_SENTINEL",
        title="Fictional page",
        strategy_used="direct",
        cached=False,
        fetch_time_ms=1,
        content_length=22,
    )


@pytest.mark.asyncio
async def test_ownerless_cache_bypasses_without_connecting() -> None:
    cache = FetchCache()
    cache._ensure_connection = AsyncMock(side_effect=AssertionError("must not connect"))
    assert await cache.get(result().url) is None
    assert await cache.set(result().url, result()) is False
    cache._ensure_connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_value_identity_and_separate_owner_indexes() -> None:
    caches = [FetchCache(user_id=uuid.UUID(int=owner)) for owner in (1, 2)]
    keys = []
    for cache in caches:
        cache.redis = AsyncMock()
        cache._ensure_connection = AsyncMock(return_value=True)
        cache.redis.eval.return_value = 1
        cache.redis.sscan.return_value = (0, [])
        assert await cache.set(result().url, result()) is True
        assert cache.redis.eval.await_count == 2
        call = cache.redis.eval.await_args_list[0].args
        assert call[:2] == (_PUBLISH_OWNED, 2)
        assert call[2] == result_cache_key(result().url)
        assert call[3] == cache.owner_index
        assert "FICTIONAL_URL_SENTINEL" not in call[2] + call[3]
        assert "FICTIONAL_PAGE_SENTINEL" in call[4]
        assert cache.redis.eval.await_args_list[1].args == (
            _PRUNE_INDEX,
            2,
            cache.owner_index,
            cache.prune_cursor_key,
        )
        assert cache.prune_cursor_key is not None
        assert str(uuid.UUID(int=1)) not in cache.prune_cursor_key
        keys.append(call[2])
        cache.redis.set.assert_not_awaited()
    assert caches[0].owner_index != caches[1].owner_index
    assert keys[0] == keys[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [0, RuntimeError("fictional transport failure")])
async def test_failed_atomic_publication_is_not_a_separate_content_write(failure) -> None:
    cache = FetchCache(user_id=uuid.UUID(int=1))
    cache.redis = AsyncMock()
    cache._ensure_connection = AsyncMock(return_value=True)
    if isinstance(failure, Exception):
        cache.redis.eval.side_effect = failure
    else:
        cache.redis.eval.return_value = failure
    assert await cache.set(result().url, result()) is False
    cache.redis.set.assert_not_awaited()
    cache.redis.sscan.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [-1, RuntimeError("fictional pruning failure")])
async def test_pruning_failure_keeps_successful_publication_and_index_metadata(failure) -> None:
    cache = FetchCache(user_id=uuid.UUID(int=1))
    cache.redis = AsyncMock()
    cache._ensure_connection = AsyncMock(return_value=True)
    cache.redis.eval.side_effect = [1, failure]
    assert await cache.set(result().url, result()) is True
    cache.redis.expire.assert_not_awaited()
    cache.redis.delete.assert_not_awaited()
