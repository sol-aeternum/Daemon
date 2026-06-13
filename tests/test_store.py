"""Unit tests for MemoryStore - get_recent_messages with exclude_status filter."""

from __future__ import annotations

import uuid
import asyncio
from datetime import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import (
    MemoryContentConflictError,
    MemoryStore,
    compute_memory_content_hash,
)


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


HASH_TEST_PEPPER = "test-pepper-for-memory-content-hash-12345678901234567890"


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


def _patch_memory_hash_settings(monkeypatch) -> None:
    settings = Settings(
        daemon_environment="development",
        daemon_auth_pepper=HASH_TEST_PEPPER,
    )
    monkeypatch.setattr("orchestrator.memory.store.get_settings", lambda: settings)


def test_compute_memory_content_hash_is_keyed_and_normalized(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)

    first = compute_memory_content_hash("User drives a blue car")
    second = compute_memory_content_hash("  User   drives a blue car  ")

    assert first == second
    assert len(first) == 64
    assert first != compute_memory_content_hash("User drives a red car")


class UniqueMemoryPool:
    def __init__(self) -> None:
        self._rows_by_hash: dict[tuple[str, bool], MockRecord] = {}
        self._lock = asyncio.Lock()
        self.insert_attempts = 0

    async def fetchrow(self, sql: str, *args):
        if "INSERT INTO memories" in sql:
            content_hash = args[2]
            local_only = bool(args[8])
            key = (content_hash, local_only)
            async with self._lock:
                self.insert_attempts += 1
                existing = self._rows_by_hash.get(key)
                if existing is not None:
                    raise asyncpg.UniqueViolationError("duplicate memory content_hash")
                row = MockRecord(
                    id=uuid.uuid4(),
                    user_id=args[0],
                    content=args[1],
                    content_hash=content_hash,
                    category=args[5],
                    source_type=args[6],
                    local_only=local_only,
                    status=args[10],
                    valid_to=None,
                    created_at=datetime.now(),
                )
                self._rows_by_hash[key] = row
                return row

        if "content_hash = $2" in sql:
            return self._rows_by_hash.get((args[1], bool(args[2])))

        return None


@pytest.mark.asyncio
async def test_insert_memory_recovers_existing_row_on_content_hash_conflict(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    pool = UniqueMemoryPool()
    encryption = MagicMock(spec=ContentEncryption)
    encryption.encrypt = MagicMock(side_effect=lambda value: value)
    encryption.decrypt = MagicMock(side_effect=lambda value: value)
    store = MemoryStore(cast(asyncpg.Pool, pool), encryption)
    user_id = uuid.uuid4()

    first = await store.insert_memory(
        user_id=user_id,
        content="User drives a blue car",
        category="fact",
        source_type="extracted",
        embedding=[0.1] * 1024,
    )
    second = await store.insert_memory(
        user_id=user_id,
        content="User drives a blue car",
        category="fact",
        source_type="extracted",
        embedding=[0.1] * 1024,
    )

    assert first["id"] == second["id"]
    assert len(pool._rows_by_hash) == 1
    assert pool.insert_attempts == 2


@pytest.mark.asyncio
async def test_concurrent_same_content_inserts_create_one_memory(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    pool = UniqueMemoryPool()
    encryption = MagicMock(spec=ContentEncryption)
    encryption.encrypt = MagicMock(side_effect=lambda value: value)
    encryption.decrypt = MagicMock(side_effect=lambda value: value)
    store = MemoryStore(cast(asyncpg.Pool, pool), encryption)
    user_id = uuid.uuid4()

    async def insert_one() -> uuid.UUID:
        row = await store.insert_memory(
            user_id=user_id,
            content="User drives a blue car",
            category="fact",
            source_type="extracted",
            embedding=[0.1] * 1024,
        )
        return row["id"]

    inserted_ids = await asyncio.gather(*(insert_one() for _ in range(100)))

    assert len(set(inserted_ids)) == 1
    assert len(pool._rows_by_hash) == 1


@pytest.mark.asyncio
async def test_same_content_local_and_global_memories_do_not_conflict(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    pool = UniqueMemoryPool()
    encryption = MagicMock(spec=ContentEncryption)
    encryption.encrypt = MagicMock(side_effect=lambda value: value)
    encryption.decrypt = MagicMock(side_effect=lambda value: value)
    store = MemoryStore(cast(asyncpg.Pool, pool), encryption)
    user_id = uuid.uuid4()

    global_memory = await store.insert_memory(
        user_id=user_id,
        content="User drives a blue car",
        category="fact",
        source_type="extracted",
        embedding=[0.1] * 1024,
        local_only=False,
    )
    local_memory = await store.insert_memory(
        user_id=user_id,
        content="User drives a blue car",
        category="fact",
        source_type="extracted",
        embedding=[0.1] * 1024,
        local_only=True,
    )

    assert global_memory["id"] != local_memory["id"]
    assert len(pool._rows_by_hash) == 2


@pytest.mark.asyncio
async def test_update_memory_content_conflict_raises_controlled_error(
    memory_store: MemoryStore,
    mock_db_pool,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    mock_db_pool.fetchrow.side_effect = asyncpg.UniqueViolationError(
        "duplicate memory content_hash"
    )

    with pytest.raises(MemoryContentConflictError):
        await memory_store.update_memory_content(uuid.uuid4(), "Duplicate content")


@pytest.mark.asyncio
async def test_backfill_memory_content_hashes_updates_active_null_hashes(
    memory_store: MemoryStore,
    mock_db_pool,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    memory_id = uuid.uuid4()
    mock_db_pool.fetch.return_value = [MockRecord(id=memory_id, content="encrypted legacy content")]
    mock_db_pool.execute.return_value = "UPDATE 1"

    backfilled = await memory_store.backfill_memory_content_hashes()

    assert backfilled == 1
    expected_hash = compute_memory_content_hash("encrypted legacy content")
    mock_db_pool.execute.assert_awaited_once()
    assert mock_db_pool.execute.await_args.args[1:] == (memory_id, expected_hash)


@pytest.mark.asyncio
async def test_backfill_memory_content_hashes_skips_legacy_duplicates(
    memory_store: MemoryStore,
    mock_db_pool,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    mock_db_pool.fetch.return_value = [
        MockRecord(id=uuid.uuid4(), content="encrypted legacy content")
    ]
    mock_db_pool.execute.side_effect = asyncpg.UniqueViolationError("duplicate memory content_hash")

    backfilled = await memory_store.backfill_memory_content_hashes()

    assert backfilled == 0


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
