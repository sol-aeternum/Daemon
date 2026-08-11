"""Tests for the SQL contract of the per-user active-memory cap.

`MemoryStore.count_active_memories` backs the `memory_write` tool's
active-row cap. It excludes `valid_to IS NOT NULL` rows so historical
revisions do not consume current quota.

The store is constructed against an `AsyncMock()` pool (matching
`tests/test_worker_gc.py`'s pattern); the test inspects the SQL
string passed to `fetchrow`/`fetchval` rather than running real
asyncpg.
"""

from __future__ import annotations

import uuid
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
