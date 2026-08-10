"""Tests for the SQL contracts of the per-user write-quota counters.

The `MemoryStore.count_memories_created_since` and
`count_active_memories` helpers back the `memory_write` tool's
per-user rate limit and active-row cap. They were corrected to
count `updated_at` (not `created_at`) for the rate counter and to
exclude `valid_to IS NOT NULL` rows from the active counter; this
test pins the SQL at the pool-call level so a future refactor
doesn't silently roll the fix back.

The store is constructed against an `AsyncMock()` pool (matching
`tests/test_worker_gc.py`'s pattern); the test inspects the SQL
string passed to `fetchrow`/`fetchval` rather than running real
asyncpg.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore


def _memory_store(pool: AsyncMock) -> MemoryStore:
    """Build a `MemoryStore` against an `AsyncMock` pool — the SQL
    is captured via the pool's `await_args`, no real DB needed.
    """
    enc = MagicMock(spec=ContentEncryption)
    enc.encrypt = MagicMock(side_effect=lambda value: value)
    enc.decrypt = MagicMock(side_effect=lambda value: value)
    return MemoryStore(db_pool=pool, encryption=enc)


@pytest.mark.asyncio
async def test_count_memories_created_since_counts_updated_at_not_created_at() -> None:
    """Rate counter must count `updated_at`, not `created_at`.

    A dedup call that merges into an existing row does not bump
    `created_at`, but it does bump `updated_at` and still triggers
    a billed embedding request. Pin the SQL so the fix doesn't
    regress.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"count": 7})
    store = _memory_store(pool)

    user_id = uuid.uuid4()
    result = await store.count_memories_created_since(user_id, since=datetime.now(timezone.utc))

    assert result == 7
    sql = pool.fetchrow.await_args.args[0]
    assert "created_at" not in sql
    assert "updated_at" in sql
    assert "user_id = $1" in sql
    assert "updated_at >= $2" in sql


@pytest.mark.asyncio
async def test_count_active_memories_excludes_valid_to() -> None:
    """Active counter must exclude rows with `valid_to IS NOT NULL`.

    `close_memory` and `supersede_memory` set `valid_to = NOW()`
    without changing `status`, so a `status = 'active'` filter alone
    inflates the count with historical revisions. The corrected
    query is `status = 'active' AND valid_to IS NULL`.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"count": 42})
    store = _memory_store(pool)

    user_id = uuid.uuid4()
    result = await store.count_active_memories(user_id)

    assert result == 42
    sql = pool.fetchrow.await_args.args[0]
    assert "status = 'active'" in sql
    assert "valid_to IS NULL" in sql
    assert "FROM memories" in sql
