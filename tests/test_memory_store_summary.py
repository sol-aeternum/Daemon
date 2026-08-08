from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator.memory.store import MemoryStore


class _IdentityEncryption:
    def decrypt(self, value: str) -> str:
        return value


@pytest.mark.asyncio
async def test_summary_batch_excludes_streaming_messages_and_applies_offset() -> None:
    conversation_id = uuid4()
    pool = AsyncMock()
    pool.fetch.return_value = []
    store = object.__new__(MemoryStore)
    store._pool = pool
    store._enc = cast(Any, _IdentityEncryption())

    assert await store.get_summary_message_batch(conversation_id, offset=12, limit=100) == []

    query, actual_id, limit, offset = pool.fetch.await_args.args
    assert actual_id == conversation_id
    assert limit == 100
    assert offset == 12
    assert "status IS NULL OR status = 'complete'" in " ".join(query.split())
    assert "ORDER BY created_at ASC, id ASC" in " ".join(query.split())


@pytest.mark.asyncio
async def test_summary_batch_pins_snapshot_upper_bound() -> None:
    """``snapshot_at`` must filter out rows that finalized after the snapshot."""
    from datetime import datetime, timezone

    conversation_id = uuid4()
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetch.return_value = []
    store = object.__new__(MemoryStore)
    store._pool = pool
    store._enc = cast(Any, _IdentityEncryption())

    assert (
        await store.get_summary_message_batch(
            conversation_id,
            offset=0,
            limit=20,
            snapshot_at=snapshot,
        )
        == []
    )

    query, actual_id, limit, offset, snapshot_arg = pool.fetch.await_args.args
    assert actual_id == conversation_id
    assert limit == 20
    assert offset == 0
    assert snapshot_arg == snapshot
    assert "created_at <= $4" in " ".join(query.split())


@pytest.mark.asyncio
async def test_count_summary_messages_at_applies_snapshot_bound() -> None:
    from datetime import datetime, timezone

    conversation_id = uuid4()
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetchrow.return_value = {"count": 7}
    store = object.__new__(MemoryStore)
    store._pool = pool

    assert await store.count_summary_messages_at(conversation_id, snapshot_at=snapshot) == 7

    query, actual_id, snapshot_arg = pool.fetchrow.await_args.args
    assert actual_id == conversation_id
    assert snapshot_arg == snapshot
    assert "created_at <= $2" in " ".join(query.split())


@pytest.mark.asyncio
async def test_update_conversation_summary_persists_baseline_atomically() -> None:
    conversation_id = uuid4()
    summary_time = datetime.now(timezone.utc)
    pool = AsyncMock()
    pool.fetchrow.return_value = {"id": conversation_id}
    store = object.__new__(MemoryStore)
    store._pool = pool

    updated = await store.update_conversation_summary(
        conversation_id,
        summary="summary",
        expected_summary_updated_at=summary_time,
        summarized_message_count=42,
    )

    assert updated is True
    query, actual_id, summary, actual_time, baseline, snapshot = pool.fetchrow.await_args.args
    normalized_query = " ".join(query.split())
    assert actual_id == conversation_id
    assert summary == "summary"
    assert actual_time == summary_time
    assert baseline == 42
    assert snapshot is None
    assert "last_summarized_msg_count" in normalized_query
    assert "summary_updated_at IS NOT DISTINCT FROM $3" in normalized_query
    assert "updated_at = NOW()" in normalized_query
    assert "last_activity_at = NOW()" in normalized_query


@pytest.mark.asyncio
async def test_update_conversation_summary_persists_snapshot_timestamp() -> None:
    conversation_id = uuid4()
    summary_time = datetime.now(timezone.utc)
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetchrow.return_value = {"id": conversation_id}
    store = object.__new__(MemoryStore)
    store._pool = pool

    updated = await store.update_conversation_summary(
        conversation_id,
        summary="summary",
        expected_summary_updated_at=summary_time,
        summarized_message_count=42,
        summary_snapshot_at=snapshot,
    )

    assert updated is True
    query, _actual_id, _summary, _time, _baseline, snapshot_arg = pool.fetchrow.await_args.args
    assert snapshot_arg == "2026-08-08T12:00:00+00:00"
    assert "last_summarized_at_time" in " ".join(query.split())
