from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore


def _memory_store(pool: Any) -> MemoryStore:
    encryption = MagicMock(spec=ContentEncryption)
    encryption.encrypt = MagicMock(side_effect=lambda value: value)
    encryption.decrypt = MagicMock(side_effect=lambda value: value)
    return MemoryStore(db_pool=pool, encryption=encryption)


def _async_context(value: Any) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


@pytest.mark.asyncio
async def test_get_summary_message_batch_selects_only_unincluded_messages() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock()
    conversation_id = uuid.uuid4()
    pool.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "conversation_id": conversation_id,
            "role": "user",
            "content": "first unsummarized",
            "created_at": datetime.now(timezone.utc),
            "reasoning_text": None,
            "advisor_traces": None,
        },
        {
            "id": uuid.uuid4(),
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": "second unsummarized",
            "created_at": datetime.now(timezone.utc),
            "reasoning_text": None,
            "advisor_traces": None,
        },
    ]
    store = _memory_store(pool)

    messages = await store.get_summary_message_batch(conversation_id, limit=500)

    query, actual_id, actual_limit = pool.fetch.await_args.args
    normalized_query = " ".join(query.split())
    assert "summary_included_at IS NULL" in normalized_query
    assert "ORDER BY created_at ASC, id ASC" in normalized_query
    assert "OFFSET" not in normalized_query
    assert (actual_id, actual_limit) == (conversation_id, 100)
    assert [message["content"] for message in messages] == [
        "first unsummarized",
        "second unsummarized",
    ]


@pytest.mark.asyncio
async def test_update_conversation_summary_marks_messages_in_same_transaction() -> None:
    pool = MagicMock()
    connection = MagicMock()
    connection.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    connection.execute = AsyncMock(return_value="UPDATE 2")
    connection.transaction.return_value = _async_context(None)
    pool.acquire.return_value = _async_context(connection)
    store = _memory_store(pool)
    conversation_id = uuid.uuid4()
    expected_updated_at = datetime.now(timezone.utc)
    message_ids = [uuid.uuid4(), uuid.uuid4()]

    updated = await store.update_conversation_summary(
        conversation_id,
        summary="new summary",
        expected_summary_updated_at=expected_updated_at,
        summarized_message_count=27,
        summarized_message_ids=message_ids,
    )

    update_query, actual_id, summary, expected, baseline = connection.fetchrow.await_args.args
    mark_query, marked_conversation, marked_ids = connection.execute.await_args.args
    assert "summary_updated_at IS NOT DISTINCT FROM $3" in " ".join(update_query.split())
    assert "last_summarized_msg_count" in update_query
    assert (actual_id, summary, expected, baseline) == (
        conversation_id,
        "new summary",
        expected_updated_at,
        27,
    )
    assert "summary_included_at = NOW()" in " ".join(mark_query.split())
    assert "summary_included_at IS NULL" in " ".join(mark_query.split())
    assert (marked_conversation, marked_ids) == (conversation_id, message_ids)
    assert updated is True
    pool.acquire.return_value.__aexit__.assert_awaited_once()
    connection.transaction.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_conversation_summary_reports_concurrent_change_without_marking() -> None:
    pool = MagicMock()
    connection = MagicMock()
    connection.fetchrow = AsyncMock(return_value=None)
    connection.execute = AsyncMock()
    connection.transaction.return_value = _async_context(None)
    pool.acquire.return_value = _async_context(connection)
    store = _memory_store(pool)

    updated = await store.update_conversation_summary(
        uuid.uuid4(),
        summary="new summary",
        expected_summary_updated_at=None,
        summarized_message_count=20,
        summarized_message_ids=[uuid.uuid4()],
    )

    assert updated is False
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_conversation_summary_rolls_back_partial_message_mark() -> None:
    pool = MagicMock()
    connection = MagicMock()
    connection.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    connection.execute = AsyncMock(return_value="UPDATE 1")
    transaction_context = _async_context(None)
    connection.transaction.return_value = transaction_context
    pool.acquire.return_value = _async_context(connection)
    store = _memory_store(pool)

    with pytest.raises(RuntimeError, match="expected to mark 2 summary messages"):
        await store.update_conversation_summary(
            uuid.uuid4(),
            summary="new summary",
            expected_summary_updated_at=None,
            summarized_message_count=20,
            summarized_message_ids=[uuid.uuid4(), uuid.uuid4()],
        )

    assert transaction_context.__aexit__.await_args.args[0] is RuntimeError


def test_summary_inclusion_migration_adds_marker_and_pending_index() -> None:
    migration = Path("migrations/039_summary_inclusion_tracking.sql").read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS summary_included_at TIMESTAMPTZ" in migration
    assert "CREATE INDEX IF NOT EXISTS idx_messages_pending_summary" in migration
    assert "WHERE summary_included_at IS NULL" in migration
