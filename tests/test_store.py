"""Unit tests for MemoryStore - get_recent_messages with exclude_status filter."""

from __future__ import annotations

import json
import uuid
import asyncio
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
from tests.memory_store_sql_support import RegisteredStorePool, normalized


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


def _registered_store(pool, encryption) -> MemoryStore:
    registered = pool if isinstance(pool, RegisteredStorePool) else RegisteredStorePool(pool)
    return MemoryStore(cast(asyncpg.Pool, registered), encryption)


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
    return _registered_store(mock_db_pool, mock_encryption)


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
        self._rows_by_hash: dict[tuple, MockRecord] = {}
        self._lock = asyncio.Lock()
        self.insert_attempts = 0

    async def fetchrow(self, sql: str, *args):
        if "INSERT INTO memories" in sql:
            content_hash = args[2]
            local_only = bool(args[8])
            assert "ON CONFLICT DO NOTHING" in sql and len(args) == 18
            assert args[17] is False  # These uniqueness fixtures have known manual origin.
            key = (args[0], content_hash, local_only, args[15])
            async with self._lock:
                self.insert_attempts += 1
                existing = self._rows_by_hash.get(key)
                if existing is not None:
                    return None  # PostgreSQL ON CONFLICT disposition, not a failed transaction.
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
            assert "AND temporal_scope_hash=$4" in sql and "AND NOT provenance_unknown" in sql
            return self._rows_by_hash.get((args[0], args[1], bool(args[2]), args[3]))

        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_insert_memory_recovers_existing_row_on_content_hash_conflict(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    pool = UniqueMemoryPool()
    encryption = MagicMock(spec=ContentEncryption)
    encryption.encrypt = MagicMock(side_effect=lambda value: value)
    encryption.decrypt = MagicMock(side_effect=lambda value: value)
    store = _registered_store(pool, encryption)
    user_id = uuid.uuid4()

    first = await store.insert_memory(
        user_id=user_id,
        content="User drives a blue car",
        category="fact",
        source_type="manual",
        embedding=[0.1] * 1024,
    )
    second = await store.insert_memory(
        user_id=user_id,
        content="User drives a blue car",
        category="fact",
        source_type="manual",
        embedding=[0.1] * 1024,
    )

    assert first["id"] == second["id"]
    assert len(pool._rows_by_hash) == 1
    assert pool.insert_attempts == 2


@pytest.mark.asyncio
async def test_internal_insert_disposition_comes_from_returning_not_timestamps(
    memory_store,
    mock_db_pool,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    owner = uuid.uuid4()
    existing = MockRecord(
        id=uuid.uuid4(),
        user_id=owner,
        content="User likes pizza",
        updated_at=datetime.now(),
    )
    raw = AsyncMock()
    raw.fetchrow.side_effect = [existing, None, existing]
    conn = cast(RegisteredStorePool, memory_store._database_pool).connection(raw)
    async with conn.transaction():
        first, inserted = await memory_store._insert_memory_with_outcome(
            owner,
            "User likes pizza",
            "fact",
            "manual",
            conn=conn,
        )
        reused, inserted_again = await memory_store._insert_memory_with_outcome(
            owner,
            "  User   likes pizza  ",
            "preference",
            "user_created",
            conn=conn,
        )
    assert inserted is True and inserted_again is False
    assert first == reused  # Even identical timestamps are not disposition evidence.
    assert raw.fetchrow.await_count == 3
    assert "ON CONFLICT DO NOTHING" in raw.fetchrow.await_args_list[0].args[0]
    assert "content_hash = $2" in raw.fetchrow.await_args_list[2].args[0]
    assert conn.transaction_entries == 1  # Caller-owned transaction; no hidden savepoint.
    raw.transaction.assert_not_called()
    mock_db_pool.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_insert_unexpected_database_failure_propagates(
    memory_store,
    monkeypatch,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    raw = AsyncMock()
    raw.fetchrow.side_effect = asyncpg.UniqueViolationError("mock cannot emulate ON CONFLICT")
    conn = cast(RegisteredStorePool, memory_store._database_pool).connection(raw)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with conn.transaction():
            await memory_store._insert_memory_with_outcome(
                uuid.uuid4(),
                "User likes pizza",
                "fact",
                "manual",
                conn=conn,
            )
    assert conn.transaction_entries == 1
    assert cast(RegisteredStorePool, memory_store._database_pool).events[-1] == "ROLLBACK"
    raw.transaction.assert_not_called()


@pytest.mark.asyncio
async def test_equivalence_discovery_is_owner_cloud_active_l1_and_bounded(
    memory_store,
    mock_db_pool,
) -> None:
    owner, excluded = uuid.uuid4(), uuid.uuid4()
    mock_db_pool.fetch.return_value = []
    await memory_store._discover_equivalence_candidates(
        owner,
        "User likes pizza",
        "preference",
        "food.favorite_pizza",
        excluded_memory_ids={excluded},
    )
    sql, *args = mock_db_pool.fetch.await_args.args
    assert "user_id = $1 AND category = $2" in sql
    assert "status = 'active' AND tier = 'l1' AND valid_to IS NULL" in sql
    assert "local_only = FALSE AND source_type != 'dream'" in sql
    assert "LIMIT 6" in sql and "NOT (id = ANY($7::uuid[]))" in sql
    assert args == [owner, "preference", "User likes pizza", "food", None, None, [excluded]]


@pytest.mark.asyncio
async def test_concurrent_same_content_inserts_create_one_memory(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    pool = UniqueMemoryPool()
    encryption = MagicMock(spec=ContentEncryption)
    encryption.encrypt = MagicMock(side_effect=lambda value: value)
    encryption.decrypt = MagicMock(side_effect=lambda value: value)
    store = _registered_store(pool, encryption)
    user_id = uuid.uuid4()

    async def insert_one() -> uuid.UUID:
        row = await store.insert_memory(
            user_id=user_id,
            content="User drives a blue car",
            category="fact",
            source_type="manual",
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
    store = _registered_store(pool, encryption)
    user_id = uuid.uuid4()

    global_memory = await store.insert_memory(
        user_id=user_id,
        content="User drives a blue car",
        category="fact",
        source_type="manual",
        embedding=[0.1] * 1024,
        local_only=False,
    )
    local_memory = await store.insert_memory(
        user_id=user_id,
        content="User drives a blue car",
        category="fact",
        source_type="manual",
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
@pytest.mark.parametrize("completed_elsewhere", [False, True])
async def test_backfill_memory_content_hashes_closes_legacy_duplicates(
    memory_store: MemoryStore,
    mock_db_pool,
    monkeypatch,
    completed_elsewhere,
) -> None:
    _patch_memory_hash_settings(monkeypatch)
    duplicate_id = uuid.uuid4()
    mock_db_pool.fetch.return_value = [
        MockRecord(id=duplicate_id, content="encrypted legacy duplicate")
    ]
    closed = False

    async def conflicting_backfill_then_conditional_close(sql, *args):
        nonlocal closed
        if "SET content_hash = $2" in sql:
            raise asyncpg.UniqueViolationError("duplicate memory content_hash")
        assert "AND content_hash IS NULL" in sql and "AND valid_to IS NULL" in sql
        assert args == (duplicate_id,)
        closed = not completed_elsewhere
        return "UPDATE 1" if closed else "UPDATE 0"

    mock_db_pool.execute.side_effect = conflicting_backfill_then_conditional_close

    backfilled = await memory_store.backfill_memory_content_hashes()

    assert backfilled == 0
    assert mock_db_pool.execute.await_count == 2
    close_sql = mock_db_pool.execute.await_args_list[1].args[0]
    assert "SET valid_to = NOW()" in close_sql
    assert "content_hash IS NULL" in close_sql
    assert mock_db_pool.execute.await_args_list[1].args[1] == duplicate_id
    assert closed is (not completed_elsewhere)
    assert "COMMIT" in cast(RegisteredStorePool, memory_store._database_pool).events


class SupersedeConflictConn:
    def __init__(self, duplicate_row: MockRecord) -> None:
        self.duplicate_row = duplicate_row
        self.closed_memory_id: uuid.UUID | None = None
        self.head_revision_id = uuid.uuid4()

    async def fetchrow(self, sql: str, *args):
        if "SELECT head_revision_id FROM memories" in sql:
            return {"head_revision_id": self.head_revision_id}
        if "UPDATE memories" in sql and "SET content" in sql:
            assert args[12] == self.head_revision_id
            raise asyncpg.UniqueViolationError("duplicate memory content_hash")
        raise AssertionError(sql)

    async def execute(self, sql: str, *args):
        raise AssertionError(sql)


class SupersedeConflictPool:
    def __init__(self, conn: SupersedeConflictConn) -> None:
        self.conn = conn

    async def fetchrow(self, sql, *args):
        return await self.conn.fetchrow(sql, *args)

    async def execute(self, sql, *args):
        return await self.conn.execute(sql, *args)


@pytest.mark.asyncio
async def test_supersede_memory_refuses_cross_lineage_hash_conflict_without_closing_old_row(
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
    store = _registered_store(SupersedeConflictPool(conn), encryption)

    with pytest.raises(MemoryContentConflictError):
        await store.supersede_memory(
            old_memory_id=old_memory_id,
            new_content="duplicate replacement",
            new_category="fact",
            new_source_type="extracted",
            user_id=duplicate_row["user_id"],
            embedding=[0.1] * 1024,
        )
    assert duplicate_row["id"] == duplicate_id
    assert conn.closed_memory_id is None
    assert cast(RegisteredStorePool, store._database_pool).events[-1] == "ROLLBACK"


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
    assert "FROM memory_current_assertions" in mock_db_pool.fetch.call_args.args[0]
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
    assert "FROM memory_current_assertions" in mock_db_pool.fetch.call_args.args[0]


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
    assert "FROM memory_current_assertions" in call_args[0]
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
# ``insert_memory`` serializes omitted/None metadata as a real empty JSON
# object, never SQL NULL. Stable-ID ``supersede_memory`` preserves the existing
# nonnull object when metadata is absent and merges a supplied JSON object.
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
    store = _registered_store(pool, _pass_through_encryption())

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
    store = _registered_store(pool, _pass_through_encryption())

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
    store = _registered_store(pool, _pass_through_encryption())

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
    store = _registered_store(pool, _pass_through_encryption())

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
    raw = AsyncMock()
    raw.fetchrow = AsyncMock(return_value=MockRecord(id=uuid.uuid4(), content="fresh note"))
    store = _registered_store(pool, _pass_through_encryption())
    conn = cast(RegisteredStorePool, store._database_pool).connection(raw)

    async with conn.transaction():
        await store.insert_memory(
            user_id=uuid.uuid4(),
            content="fresh note",
            category="fact",
            source_type="import",
            conn=cast(Any, conn),
        )

    assert pool.calls == 0
    assert raw.fetchrow.await_count == 1
    # AsyncMock records SQL at args[0]; metadata is PostgreSQL parameter $13.
    assert raw.fetchrow.await_args.args[13] == "{}"
    assert conn.transaction_entries == 1


def _supersede_conn(*, update_result: MockRecord | BaseException) -> MagicMock:
    """Model stable-ID correction SQL, retaining all non-target metadata."""
    conn = MagicMock()
    row = (
        dict(update_result)
        if isinstance(update_result, MockRecord)
        else dict(_supersede_new_row(metadata_json="{}"))
    )

    async def fetchrow(sql, *args):
        q = normalized(sql)
        if q.startswith("SELECT head_revision_id FROM memories"):
            assert "user_id=$1 AND id=$2 AND status!='deleted'" in q
            assert args == (row["user_id"], row["id"])
            return {"head_revision_id": row["head_revision_id"]}
        if q.startswith("UPDATE memories SET content = $2,"):
            assert args[0] == row["id"] and args[9] == row["user_id"]
            assert args[12] == row["head_revision_id"]
            if isinstance(update_result, BaseException):
                raise update_result
            row.update(
                content=args[1],
                content_hash=args[5],
                category=args[8],
                head_revision_id=uuid.uuid4(),
            )
            return dict(row)
        assert q.startswith("SELECT * FROM memories WHERE id = $1 AND status!='deleted'")
        assert "memory_tombstones" in q and "user_id=$2" in q
        assert args == (row["id"], row["user_id"])
        return dict(row)

    async def execute(sql, *args):
        assert normalized(sql).startswith("UPDATE memories SET metadata = COALESCE")
        assert args[0] == row["id"]
        row["metadata"] = json.dumps({**json.loads(row["metadata"]), **json.loads(args[1])})
        return "UPDATE 1"

    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    conn.execute = AsyncMock(side_effect=execute)
    conn.current_row = row
    return conn


def _supersede_pool_with_conn(conn: MagicMock) -> RegisteredStorePool:
    return RegisteredStorePool(conn)


def _supersede_new_row(*, metadata_json: str | None) -> MockRecord:
    return MockRecord(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        content="encrypted-new-content",
        content_hash=compute_memory_content_hash("superseded content"),
        metadata=metadata_json,
        head_revision_id=uuid.uuid4(),
        status="active",
    )


@pytest.mark.asyncio
async def test_supersede_memory_omitted_metadata_preserves_nonnull_object(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    row = _supersede_new_row(metadata_json="{}")
    pool = _supersede_pool_with_conn(_supersede_conn(update_result=row))
    store = _registered_store(pool, _pass_through_encryption())

    result = await store.supersede_memory(
        row["id"], "new content", "fact", "import", row["user_id"]
    )

    assert result["id"] == row["id"] and result["head_revision_id"] != row["head_revision_id"]
    assert result["metadata"] == "{}"
    pool.executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_supersede_memory_none_metadata_preserves_existing_object(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    row = _supersede_new_row(metadata_json='{"retained": true}')
    pool = _supersede_pool_with_conn(_supersede_conn(update_result=row))
    store = _registered_store(pool, _pass_through_encryption())

    result = await store.supersede_memory(
        row["id"],
        "new content",
        "fact",
        "import",
        row["user_id"],
        metadata=None,
    )

    assert result["metadata"] == '{"retained": true}'
    pool.executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_supersede_memory_nested_metadata_object_is_preserved(monkeypatch) -> None:
    _patch_memory_hash_settings(monkeypatch)
    row = _supersede_new_row(metadata_json="{}")
    pool = _supersede_pool_with_conn(_supersede_conn(update_result=row))
    store = _registered_store(pool, _pass_through_encryption())

    nested = {"evidence": {"detected_by": "dedup", "score": 0.9}}

    await store.supersede_memory(
        row["id"],
        "new content",
        "fact",
        "import",
        row["user_id"],
        metadata=nested,
    )

    conn = pool.executor
    assert conn.execute.await_args.args[2] == json.dumps(nested)
    assert json.loads(conn.current_row["metadata"]) == nested


@pytest.mark.asyncio
async def test_supersede_memory_failure_runs_no_implicit_update(monkeypatch) -> None:
    """A failing correction aborts without any implicit metadata mutation."""
    _patch_memory_hash_settings(monkeypatch)
    pool = _supersede_pool_with_conn(_supersede_conn(update_result=RuntimeError("update exploded")))
    store = _registered_store(pool, _pass_through_encryption())
    row = dict(pool.executor.current_row)

    with pytest.raises(RuntimeError, match="update exploded"):
        await store.supersede_memory(row["id"], "new content", "fact", "import", row["user_id"])

    conn = pool.executor
    assert conn.execute.await_count == 0
    assert conn.current_row == row and pool.events[-1] == "ROLLBACK"
