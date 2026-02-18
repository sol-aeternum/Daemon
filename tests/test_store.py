"""Unit tests for MemoryStore - get_recent_messages with exclude_status filter."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore


class MockRecord:
    """Mock asyncpg Record that behaves like a dict."""

    def __init__(self, **kwargs):
        self._data = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data.keys())

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()


@pytest_asyncio.fixture
async def mock_db_pool():
    """Create a mock asyncpg pool for testing."""
    pool = AsyncMock()
    return pool


@pytest_asyncio.fixture
async def mock_encryption():
    """Create a mock encryption instance that passes through plaintext."""
    enc = MagicMock(spec=ContentEncryption)
    enc.encrypt = MagicMock(side_effect=lambda x: x)
    enc.decrypt = MagicMock(side_effect=lambda x: x)
    return enc


@pytest_asyncio.fixture
async def memory_store(mock_db_pool, mock_encryption):
    """Create a MemoryStore instance with mocked dependencies."""
    return MemoryStore(db_pool=mock_db_pool, encryption=mock_encryption)


@pytest.mark.asyncio
async def test_get_recent_messages_excludes_streaming_status(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    """Test that messages with status='streaming' are excluded when exclude_status=['streaming']."""
    conversation_id = uuid.uuid4()

    mock_rows = [
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="user",
            content="Hello",
            status=None,
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content="Hi there",
            status="complete",
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content="Processing...",
            status="streaming",
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
    ]

    filtered_rows = [r for r in mock_rows if r.status != "streaming"]
    mock_db_pool.fetch.return_value = filtered_rows

    results = await memory_store.get_recent_messages(
        conversation_id=conversation_id,
        limit=20,
        exclude_status=["streaming"],
    )

    mock_db_pool.fetch.assert_called_once()
    call_args = mock_db_pool.fetch.call_args

    assert call_args[0][1] == conversation_id
    assert call_args[0][2] == 20
    assert call_args[0][3] == ["streaming"]

    assert len(results) == 2
    statuses = [r.get("status") for r in results]
    assert None in statuses
    assert "complete" in statuses
    assert "streaming" not in statuses


@pytest.mark.asyncio
async def test_get_recent_messages_without_exclude_status_includes_all(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    """Test that all messages are included when exclude_status is None."""
    conversation_id = uuid.uuid4()

    mock_rows = [
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="user",
            content="Hello",
            status=None,
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content="Hi",
            status="complete",
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content="Processing",
            status="streaming",
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
    ]

    mock_db_pool.fetch.return_value = mock_rows

    results = await memory_store.get_recent_messages(
        conversation_id=conversation_id,
        limit=20,
        exclude_status=None,
    )

    call_args = mock_db_pool.fetch.call_args
    assert call_args[0][3] is None

    assert len(results) == 3
    statuses = [r.get("status") for r in results]
    assert None in statuses
    assert "complete" in statuses
    assert "streaming" in statuses


@pytest.mark.asyncio
async def test_get_recent_messages_includes_null_status(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    """Test that messages with status=NULL are included when exclude_status is set."""
    conversation_id = uuid.uuid4()

    mock_rows = [
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="user",
            content="Message with no status",
            status=None,
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
    ]

    mock_db_pool.fetch.return_value = mock_rows

    results = await memory_store.get_recent_messages(
        conversation_id=conversation_id,
        limit=20,
        exclude_status=["streaming"],
    )

    assert len(results) == 1
    assert results[0].get("status") is None


@pytest.mark.asyncio
async def test_get_recent_messages_includes_complete_status(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    """Test that messages with status='complete' are included when exclude_status is set."""
    conversation_id = uuid.uuid4()

    mock_rows = [
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content="Completed message",
            status="complete",
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
    ]

    mock_db_pool.fetch.return_value = mock_rows

    results = await memory_store.get_recent_messages(
        conversation_id=conversation_id,
        limit=20,
        exclude_status=["streaming"],
    )

    assert len(results) == 1
    assert results[0].get("status") == "complete"


@pytest.mark.asyncio
async def test_get_recent_messages_excludes_multiple_statuses(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    """Test that multiple statuses can be excluded."""
    conversation_id = uuid.uuid4()

    mock_rows = [
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="user",
            content="Hello",
            status="complete",
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content="Streaming...",
            status="streaming",
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
        MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content="Pending...",
            status="pending",
            created_at=datetime.now(),
            tool_calls="[]",
            tool_results="[]",
            metadata="{}",
        ),
    ]

    filtered_rows = [r for r in mock_rows if r.status not in ("streaming", "pending")]
    mock_db_pool.fetch.return_value = filtered_rows

    results = await memory_store.get_recent_messages(
        conversation_id=conversation_id,
        limit=20,
        exclude_status=["streaming", "pending"],
    )

    call_args = mock_db_pool.fetch.call_args
    assert call_args[0][3] == ["streaming", "pending"]

    assert len(results) == 1
    assert results[0].get("status") == "complete"


@pytest.mark.asyncio
async def test_get_recent_messages_returns_normalized_messages(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    """Test that returned messages are properly normalized with decrypted content."""
    conversation_id = uuid.uuid4()
    message_id = uuid.uuid4()

    mock_row = MockRecord(
        id=message_id,
        conversation_id=conversation_id,
        role="assistant",
        content="encrypted_content",
        status="complete",
        created_at=datetime.now(),
        tool_calls='[{"id": "1", "function": {"name": "test"}}]',
        tool_results='[{"result": "success"}]',
        metadata='{"key": "value"}',
    )

    mock_db_pool.fetch.return_value = [mock_row]

    results = await memory_store.get_recent_messages(
        conversation_id=conversation_id,
        limit=20,
        exclude_status=["streaming"],
    )

    assert len(results) == 1
    result = results[0]

    memory_store._enc.decrypt.assert_called_with("encrypted_content")

    assert result["id"] == message_id
    assert result["role"] == "assistant"
    assert result["status"] == "complete"

    assert isinstance(result["tool_calls"], list)
    assert len(result["tool_calls"]) == 1
    assert isinstance(result["tool_results"], list)
    assert isinstance(result["metadata"], dict)
    assert result["metadata"]["key"] == "value"
