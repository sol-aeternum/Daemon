"""POST /memories/import against a real disposable database.

Proves the reported counts agree with persisted rows when a store failure hits
part-way through a batch: items that committed are counted and saved, and the
failing item's writes roll back. Requires ``ENTITLEMENTS_TEST_DATABASE_URL``;
without it these tests skip before any connection (shared fixture contract).
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import asyncpg
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from orchestrator.auth import AuthenticatedDevice
from orchestrator.config import get_settings
from orchestrator.memory.embedding import EmbeddingConfigurationError
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.routes import memories as memories_router
from tests.benchmark_longmemeval.isolated_database import isolated_database_fixture

isolated_import_pool = isolated_database_fixture(
    "memory_import_", audited_tables=["users", "memories"]
)


def _crypto(monkeypatch: pytest.MonkeyPatch) -> ContentEncryption:
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", secrets.token_urlsafe(48))
    get_settings.cache_clear()
    return ContentEncryption(get_settings().daemon_encryption_key)


async def _user(pool: asyncpg.Pool) -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
        VALUES ($1, $2, 'import', 'import', '{}'::jsonb, NOW(), NOW())
        """,
        user_id,
        f"memory-import+{user_id.hex}@daemon.test",
    )
    return user_id


def _unqualified_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unqualified(_texts: list[str]) -> Any:
        raise EmbeddingConfigurationError("embeddings unqualified in this test")

    monkeypatch.setattr(memories_router, "embed_documents_with_metadata", unqualified)
    monkeypatch.setattr("orchestrator.memory.dedup.embed_documents_with_metadata", unqualified)


async def _import(store: MemoryStore, user_id: uuid.UUID, contents: list[str]) -> Any:
    app_state: Any = type("State", (), {"memory_store": store})()
    auth = AuthenticatedDevice(user_id=user_id, device_id=uuid.uuid4(), session_id=uuid.uuid4())
    request = memories_router.MemoryImportRequest(
        memories=[memories_router.ImportedMemory(content=c) for c in contents]
    )
    return await memories_router.import_memories(request, app_state=app_state, auth=auth)


async def _import_rows(pool: asyncpg.Pool, user_id: uuid.UUID) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, status FROM memories WHERE user_id = $1 AND source_type = 'import'",
        user_id,
    )


@pytest.mark.asyncio
async def test_failure_after_a_write_in_the_same_batch_reports_only_committed_items(
    monkeypatch: pytest.MonkeyPatch,
    isolated_import_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_import_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _user(pool)
    _unqualified_embeddings(monkeypatch)

    real_insert = store.insert_memory
    calls = 0

    async def insert_then_fail_second(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        row = await real_insert(*args, **kwargs)
        if calls == 2:
            # The second item's row is already written on its transaction
            # connection; failing now must roll that write back.
            raise RuntimeError("store failure after insert")
        return row

    monkeypatch.setattr(store, "insert_memory", insert_then_fail_second)

    with pytest.raises(HTTPException) as raised:
        await _import(store, user_id, ["First fact", "Second fact", "Third fact"])

    assert raised.value.status_code == 503
    assert raised.value.detail == {
        "message": "Import stopped because a memory service was unavailable.",
        "received": 3,
        "processed": 1,
        "created": 1,
        "merged": 0,
        "superseded": 0,
    }
    rows = await _import_rows(pool, user_id)
    assert len(rows) == 1, "only the committed first item may persist"


@pytest.mark.asyncio
async def test_merge_then_failure_keeps_counts_and_rows_in_agreement(
    monkeypatch: pytest.MonkeyPatch,
    isolated_import_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_import_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _user(pool)
    _unqualified_embeddings(monkeypatch)

    first = await _import(store, user_id, ["Already known"])
    assert first["created"] == 1 and first["processed"] == 1

    real_insert = store.insert_memory

    async def fail_on_insert(*args: Any, **kwargs: Any) -> Any:
        await real_insert(*args, **kwargs)
        raise RuntimeError("store failure after insert")

    monkeypatch.setattr(store, "insert_memory", fail_on_insert)

    with pytest.raises(HTTPException) as raised:
        await _import(store, user_id, ["Already known", "Brand new"])

    detail: dict[str, Any] = raised.value.detail  # pyright: ignore[reportAssignmentType]
    assert detail["processed"] == 1
    assert detail["merged"] == 1
    assert detail["created"] == 0
    rows = await _import_rows(pool, user_id)
    assert len(rows) == 1, "the merge adds no row and the failed insert rolls back"


@pytest.mark.asyncio
async def test_successful_import_counts_match_rows(
    monkeypatch: pytest.MonkeyPatch,
    isolated_import_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_import_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _user(pool)
    _unqualified_embeddings(monkeypatch)

    result = await _import(store, user_id, ["One", "Two", "Two"])

    assert result == {
        "received": 3,
        "processed": 3,
        "inserted": 2,
        "created": 2,
        "merged": 1,
        "superseded": 0,
    }
    rows = await _import_rows(pool, user_id)
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"active"}
