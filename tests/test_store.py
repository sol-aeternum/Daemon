"""Unit tests for MemoryStore - get_recent_messages with exclude_status filter."""

from __future__ import annotations

import json
import uuid
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.embedding import EmbeddingVector
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
async def test_backfill_memory_content_hashes_includes_non_active_current_rows(
    memory_store: MemoryStore,
    mock_db_pool,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    mock_db_pool.fetch.return_value = []

    await memory_store.backfill_memory_content_hashes()

    query = mock_db_pool.fetch.await_args.args[0]
    assert "content_hash IS NULL" in query
    assert "valid_to IS NULL" in query
    assert "status = 'active'" not in query


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
    mock_db_pool.execute.side_effect = [
        asyncpg.UniqueViolationError("duplicate memory content_hash"),
        "UPDATE 1",
    ]

    backfilled = await memory_store.backfill_memory_content_hashes()

    assert backfilled == 0


@pytest.mark.asyncio
async def test_backfill_memory_content_hashes_closes_legacy_duplicates(
    memory_store: MemoryStore,
    mock_db_pool,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    duplicate_id = uuid.uuid4()
    mock_db_pool.fetch.return_value = [
        MockRecord(id=duplicate_id, content="encrypted legacy duplicate")
    ]
    mock_db_pool.execute.side_effect = [
        asyncpg.UniqueViolationError("duplicate memory content_hash"),
        "UPDATE 1",
    ]

    backfilled = await memory_store.backfill_memory_content_hashes()

    assert backfilled == 0
    assert mock_db_pool.execute.await_count == 2
    close_sql = mock_db_pool.execute.await_args_list[1].args[0]
    assert "SET valid_to = NOW()" in close_sql
    assert "content_hash IS NULL" in close_sql
    assert mock_db_pool.execute.await_args_list[1].args[1] == duplicate_id


class SupersedeConflictConn:
    def __init__(self, duplicate_row: MockRecord) -> None:
        self.duplicate_row = duplicate_row
        self.closed_memory_id: uuid.UUID | None = None

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, sql: str, *args):
        if "INSERT INTO memories" in sql:
            raise asyncpg.UniqueViolationError("duplicate memory content_hash")
        if "content_hash = $2" in sql:
            return self.duplicate_row
        return None

    async def execute(self, sql: str, *args):
        if "SET valid_to = NOW()" in sql:
            self.closed_memory_id = args[0]
            return "UPDATE 1"
        return "UPDATE 0"


class SupersedeConflictPool:
    def __init__(self, conn: SupersedeConflictConn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.mark.asyncio
async def test_supersede_memory_recovers_existing_row_on_content_hash_conflict(
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    old_memory_id = uuid.uuid4()
    duplicate_id = uuid.uuid4()
    duplicate_row = MockRecord(
        id=duplicate_id,
        user_id=uuid.uuid4(),
        content="duplicate replacement",
        category="fact",
        source_type="extracted",
        status="active",
        valid_to=None,
        created_at=datetime.now(),
    )
    conn = SupersedeConflictConn(duplicate_row)
    encryption = MagicMock(spec=ContentEncryption)
    encryption.encrypt = MagicMock(side_effect=lambda value: value)
    encryption.decrypt = MagicMock(side_effect=lambda value: value)
    store = MemoryStore(cast(asyncpg.Pool, SupersedeConflictPool(conn)), encryption)

    result = await store.supersede_memory(
        old_memory_id=old_memory_id,
        new_content="duplicate replacement",
        new_category="fact",
        new_source_type="extracted",
        user_id=duplicate_row["user_id"],
        embedding=[0.1] * 1024,
    )

    assert result["id"] == duplicate_id
    assert conn.closed_memory_id == old_memory_id


@pytest.mark.asyncio
async def test_update_memory_status_active_hashes_legacy_row(
    memory_store: MemoryStore,
    mock_db_pool,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    memory_id = uuid.uuid4()
    mock_db_pool.fetchrow.return_value = MockRecord(
        content="encrypted legacy content",
        content_hash=None,
    )
    mock_db_pool.execute.return_value = "UPDATE 1"

    updated = await memory_store.update_memory_status(memory_id, "active")

    assert updated is True
    expected_hash = compute_memory_content_hash("encrypted legacy content")
    mock_db_pool.execute.assert_awaited_once()
    assert mock_db_pool.execute.await_args.args[1:] == (memory_id, "active", expected_hash)


@pytest.mark.asyncio
async def test_update_memory_status_active_conflict_raises_controlled_error(
    memory_store: MemoryStore,
    mock_db_pool,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    mock_db_pool.fetchrow.return_value = MockRecord(
        content="encrypted duplicate content",
        content_hash=None,
    )
    mock_db_pool.execute.side_effect = asyncpg.UniqueViolationError("duplicate memory content_hash")

    with pytest.raises(MemoryContentConflictError):
        await memory_store.update_memory_status(uuid.uuid4(), "active")


@pytest.mark.asyncio
async def test_search_memories_infers_embedding_model_from_vector_metadata(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    mock_db_pool.fetch.return_value = []
    query_embedding = EmbeddingVector(
        [0.1, 0.2],
        provider="openai",
        model="openai:text-embedding-3-small",
        storage_model="openai:text-embedding-3-small",
    )

    await memory_store.search_memories(
        user_id=uuid.uuid4(),
        query_embedding=query_embedding,
    )

    mock_db_pool.fetch.assert_called_once()
    assert mock_db_pool.fetch.call_args.args[10] == "openai:text-embedding-3-small"


@pytest.mark.asyncio
async def test_search_memories_defaults_plain_vectors_to_primary_model(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    mock_db_pool.fetch.return_value = []

    await memory_store.search_memories(
        user_id=uuid.uuid4(),
        query_embedding=[0.1, 0.2],
    )

    assert mock_db_pool.fetch.call_args.args[10] == "voyage-4-large"


@pytest.mark.asyncio
async def test_search_memories_bm25_filters_enabled_models_and_can_exclude_l0(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    mock_db_pool.fetch.return_value = []
    enabled_models = ["voyage-4-large", "openrouter:voyageai/voyage-4-large"]

    await memory_store.search_memories_bm25(
        user_id=uuid.uuid4(),
        query="shellfish allergy",
        embedding_models=enabled_models,
        include_l0=False,
    )

    call_args = mock_db_pool.fetch.call_args.args
    assert "embedding_model = ANY($9::text[])" in call_args[0]
    assert "($10::bool OR tier != 'l0')" in call_args[0]
    assert call_args[9] == sorted(enabled_models)
    assert call_args[10] is False


@pytest.mark.asyncio
async def test_list_memories_by_slot_family_excludes_dream_observations(
    memory_store: MemoryStore,
    mock_db_pool: AsyncMock,
) -> None:
    mock_db_pool.fetch.return_value = []

    await memory_store.list_memories_by_slot_family(
        user_id=uuid.uuid4(),
        slot_family="vehicle",
    )

    assert "source_type != 'dream'" in mock_db_pool.fetch.call_args.args[0]


@pytest.mark.asyncio
async def test_insert_memory_without_vector_does_not_claim_embedding_model(
    memory_store: MemoryStore, mock_db_pool: AsyncMock
) -> None:
    mock_db_pool.fetchrow.return_value = {"content": "User plays guitar"}
    await memory_store.insert_memory(
        uuid.uuid4(),
        "User plays guitar",
        "fact",
        "user_created",
        embedding=None,
        embedding_model="voyage-4-large",
    )
    assert mock_db_pool.fetchrow.await_args.args[4] is None  # vector
    assert mock_db_pool.fetchrow.await_args.args[5] is None  # provenance


@pytest.mark.asyncio
async def test_lexical_search_includes_unembedded_rows_in_model_scoped_query(
    memory_store: MemoryStore, mock_db_pool: AsyncMock
) -> None:
    mock_db_pool.fetch.return_value = []
    await memory_store.search_memories_bm25(
        uuid.uuid4(), "guitar", embedding_models=["openrouter:voyageai/voyage-4-large"]
    )

    sql = mock_db_pool.fetch.await_args.args[0]
    assert "embedding_model = ANY($9::text[]) OR embedding_model IS NULL" in sql
    assert mock_db_pool.fetch.await_args.args[9] == ["openrouter:voyageai/voyage-4-large"]


@pytest.mark.asyncio
async def test_imported_memory_without_vector_remains_lexically_searchable(
    memory_store: MemoryStore, mock_db_pool: AsyncMock
) -> None:
    await memory_store.import_memories(
        uuid.uuid4(), [{"content": "User plays guitar", "category": "fact"}]
    )

    sql, *args = mock_db_pool.execute.await_args.args
    assert "to_tsvector('english', $12)" in sql
    assert args[3] is None  # no vector
    assert args[4] is None  # no unearned embedding provenance
    assert args[11] == "User plays guitar"  # plaintext used only to build tsvector


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


@pytest.mark.asyncio
async def test_list_conversations_returns_derived_metadata(
    memory_store: MemoryStore, mock_db_pool: AsyncMock
) -> None:
    """Derived listing metadata replaces the stored cache in the response."""
    stale_updated_at = datetime(2025, 1, 1, tzinfo=None)
    derived_activity_at = datetime(2026, 9, 30, tzinfo=None)
    mock_db_pool.fetch.return_value = [
        MockRecord(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            title="legacy stale chat",
            message_count=0,
            updated_at=stale_updated_at,
            last_activity_at=stale_updated_at,
            actual_message_count=3,
            effective_last_activity_at=derived_activity_at,
        )
    ]

    results = await memory_store.list_conversations(uuid.uuid4())

    assert [c["message_count"] for c in results] == [3]
    assert [c["last_activity_at"] for c in results] == [derived_activity_at]
    assert results[0]["updated_at"] == stale_updated_at
    assert "actual_message_count" not in results[0]
    assert "effective_last_activity_at" not in results[0]


@pytest.mark.asyncio
async def test_get_conversation_returns_derived_metadata(
    memory_store: MemoryStore, mock_db_pool: AsyncMock
) -> None:
    stale_time = datetime(2025, 6, 1, tzinfo=None)
    derived_time = datetime(2026, 9, 30, tzinfo=None)
    mock_db_pool.fetchrow.return_value = MockRecord(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="legacy stale chat",
        message_count=0,
        updated_at=stale_time,
        last_activity_at=stale_time,
        actual_message_count=2,
        effective_last_activity_at=derived_time,
    )

    conversation = await memory_store.get_conversation(uuid.uuid4())

    assert conversation is not None
    assert conversation["message_count"] == 2
    assert conversation["last_activity_at"] == derived_time
    assert conversation["updated_at"] == stale_time
    assert "actual_message_count" not in conversation


@pytest.mark.asyncio
async def test_get_conversation_returns_none_when_missing(
    memory_store: MemoryStore, mock_db_pool: AsyncMock
) -> None:
    mock_db_pool.fetchrow.return_value = None

    assert await memory_store.get_conversation(uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# metadata JSONB NOT NULL DEFAULT '{}' regressions (migration 040 contract)
#
# ``MemoryStore.insert_memory`` and ``MemoryStore.supersede_memory`` must
# serialize an omitted or ``None`` ``metadata`` as a real empty JSON object
# (``'{}'``) instead of SQL NULL, because the column is NOT NULL. Provided
# metadata — including nested objects — must pass through ``json.dumps``.
# Verifying the exact ``metadata_json`` argument keeps these tests strict:
# any regression back to ``json.dumps(metadata)`` re-raises
# ``NotNullViolationError`` on real PostgreSQL.
# ---------------------------------------------------------------------------


class MetadataCapturePool:
    """Minimal pool double that records the ``metadata_json`` insert argument."""

    def __init__(self, *, row: MockRecord | None | BaseException) -> None:
        self.row_result = row
        self.metadata_json: str | None = None
        self.calls = 0

    async def fetchrow(self, sql: str, *args) -> MockRecord | None:
        if "INSERT INTO memories" in sql:
            self.calls += 1
            self.metadata_json = args[12]
            if isinstance(self.row_result, BaseException):
                raise self.row_result
            return self.row_result
        return None


def _pass_through_encryption() -> MagicMock:
    enc = MagicMock(spec=ContentEncryption)
    enc.encrypt = MagicMock(side_effect=lambda value: value)
    enc.decrypt = MagicMock(side_effect=lambda value: value)
    return enc


@pytest.mark.asyncio
async def test_insert_memory_omitted_metadata_becomes_empty_object(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    returned = MockRecord(id=uuid.uuid4(), content="fresh note", metadata="{}")
    pool = MetadataCapturePool(row=returned)
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    result = await store.insert_memory(
        user_id=uuid.uuid4(),
        content="fresh note",
        category="fact",
        source_type="import",
    )

    assert pool.metadata_json == "{}"
    assert result["metadata"] == "{}"


@pytest.mark.asyncio
async def test_insert_memory_none_metadata_becomes_empty_object(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    pool = MetadataCapturePool(row=MockRecord(id=uuid.uuid4(), content="fresh note"))
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    await store.insert_memory(
        user_id=uuid.uuid4(),
        content="fresh note",
        category="fact",
        source_type="import",
        metadata=None,
    )

    assert pool.metadata_json == "{}"


@pytest.mark.asyncio
async def test_insert_memory_empty_metadata_dict_serializes_to_empty_object(
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    pool = MetadataCapturePool(row=MockRecord(id=uuid.uuid4(), content="fresh note"))
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    await store.insert_memory(
        user_id=uuid.uuid4(),
        content="fresh note",
        category="fact",
        source_type="import",
        metadata={},
    )

    assert pool.metadata_json == "{}"


@pytest.mark.asyncio
async def test_insert_memory_nested_metadata_object_is_preserved(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    pool = MetadataCapturePool(row=MockRecord(id=uuid.uuid4(), content="fresh note"))
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    nested = {"key": "value", "nested": {"list": [1, 2, 3], "bool": True}}

    await store.insert_memory(
        user_id=uuid.uuid4(),
        content="fresh note",
        category="fact",
        source_type="import",
        metadata=nested,
    )

    assert pool.metadata_json == json.dumps(nested)


@pytest.mark.asyncio
async def test_insert_memory_uses_caller_conn_on_conn_path(monkeypatch) -> None:
    """On the atomic-cap path every call must go through the provided conn."""
    _patch_memory_hash_settings(monkeypatch)
    pool = MetadataCapturePool(row=MockRecord(id=uuid.uuid4(), content="fresh note"))
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=MockRecord(id=uuid.uuid4(), content="fresh note"))
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    await store.insert_memory(
        user_id=uuid.uuid4(),
        content="fresh note",
        category="fact",
        source_type="import",
        conn=cast(Any, conn),
    )

    assert pool.calls == 0
    assert conn.fetchrow.await_count == 1
    # AsyncMock records SQL at args[0]; metadata is PostgreSQL parameter $13.
    assert conn.fetchrow.await_args.args[13] == "{}"


def _supersede_conn(*, insert_result: MockRecord | None | BaseException) -> MagicMock:
    """Build a mock conn modelling the supersede transaction surface."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    def make_transaction() -> MagicMock:
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=None)
        tx.__aexit__ = AsyncMock(return_value=False)
        return tx

    conn.transaction = MagicMock(side_effect=make_transaction)
    if isinstance(insert_result, BaseException):
        conn.fetchrow.side_effect = insert_result
    else:
        conn.fetchrow.return_value = insert_result
    return conn


def _supersede_pool_with_conn(conn: MagicMock) -> MagicMock:
    pool = MagicMock()
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool


def _supersede_new_row(*, metadata_json: str | None) -> MockRecord:
    return MockRecord(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        content="encrypted-new-content",
        content_hash=compute_memory_content_hash("superseded content"),
        metadata=metadata_json,
    )


@pytest.mark.asyncio
async def test_supersede_memory_omitted_metadata_becomes_empty_object(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    row = _supersede_new_row(metadata_json="{}")
    pool = _supersede_pool_with_conn(_supersede_conn(insert_result=row))
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    await store.supersede_memory(uuid.uuid4(), "new content", "fact", "import", uuid.uuid4())

    conn = pool.acquire.return_value.__aenter__.return_value
    metadata_json = conn.fetchrow.await_args.args[12]
    assert metadata_json == "{}"


@pytest.mark.asyncio
async def test_supersede_memory_none_metadata_becomes_empty_object(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    row = _supersede_new_row(metadata_json="{}")
    pool = _supersede_pool_with_conn(_supersede_conn(insert_result=row))
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    await store.supersede_memory(
        uuid.uuid4(),
        "new content",
        "fact",
        "import",
        uuid.uuid4(),
        metadata=None,
    )

    conn = pool.acquire.return_value.__aenter__.return_value
    assert conn.fetchrow.await_args.args[12] == "{}"


@pytest.mark.asyncio
async def test_supersede_memory_nested_metadata_object_is_preserved(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    row = _supersede_new_row(metadata_json="{}")
    pool = _supersede_pool_with_conn(_supersede_conn(insert_result=row))
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    nested = {"evidence": {"detected_by": "dedup", "score": 0.9}}

    await store.supersede_memory(
        uuid.uuid4(),
        "new content",
        "fact",
        "import",
        uuid.uuid4(),
        metadata=nested,
    )

    conn = pool.acquire.return_value.__aenter__.return_value
    assert conn.fetchrow.await_args.args[12] == json.dumps(nested)


@pytest.mark.asyncio
async def test_supersede_memory_failure_runs_no_implicit_update(monkeypatch) -> None:
    """A failing insert must abort the transaction without updating old row."""
    _patch_memory_hash_settings(monkeypatch)
    pool = _supersede_pool_with_conn(_supersede_conn(insert_result=RuntimeError("insert exploded")))
    store = MemoryStore(cast(asyncpg.Pool, pool), _pass_through_encryption())

    with pytest.raises(RuntimeError, match="insert exploded"):
        await store.supersede_memory(uuid.uuid4(), "new content", "fact", "import", uuid.uuid4())

    conn = pool.acquire.return_value.__aenter__.return_value
    assert conn.execute.await_count == 0
