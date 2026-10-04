"""Memory edits against a real disposable database (#395, #318).

Covers the persisted result of ``update_memory_content`` and the stale-vector
guard on ``update_memory_embedding``. Requires ``ENTITLEMENTS_TEST_DATABASE_URL``;
skips before any connection without it.
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
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import (
    MemoryContentConflictError,
    MemoryStore,
    compute_memory_content_hash,
)
from orchestrator.routes import memories as memories_router
from tests.benchmark_longmemeval.isolated_database import isolated_database_fixture

isolated_edit_pool = isolated_database_fixture("memory_edit_", audited_tables=["users", "memories"])

DIMENSION = 1024


def _vector(value: float) -> list[float]:
    return [value] * DIMENSION


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
        VALUES ($1, $2, 'edit', 'edit', '{}'::jsonb, NOW(), NOW())
        """,
        user_id,
        f"memory-edit+{user_id.hex}@daemon.test",
    )
    return user_id


async def _seed(store: MemoryStore, user_id: uuid.UUID, content: str) -> uuid.UUID:
    memory = await store.insert_memory(
        user_id=user_id,
        content=content,
        category="fact",
        source_type="user_created",
        embedding=_vector(0.1),
        embedding_model="old-model",
    )
    assert memory is not None
    return memory["id"]


async def _raw(pool: asyncpg.Pool, memory_id: uuid.UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        SELECT category, content_hash, embedding IS NULL AS no_vector, embedding_model,
               embedding::text AS vector_text,
               content_tsv @@ plainto_tsquery('english', 'bicycle') AS matches_new,
               content_tsv @@ plainto_tsquery('english', 'tram') AS matches_old
        FROM memories WHERE id = $1
        """,
        memory_id,
    )
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_edit_with_new_vector_persists_text_category_vector_and_search(
    monkeypatch: pytest.MonkeyPatch,
    isolated_edit_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_edit_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _user(pool)
    memory_id = await _seed(store, user_id, "I commute by tram")

    updated = await store.update_memory_content(
        memory_id,
        "I commute by bicycle",
        embedding=_vector(0.7),
        embedding_model="new-model",
        category="preference",
        user_id=user_id,
    )

    assert updated is not None and updated["content"] == "I commute by bicycle"
    reloaded = await store.get_memory(memory_id)
    assert reloaded is not None
    assert reloaded["content"] == "I commute by bicycle"
    raw = await _raw(pool, memory_id)
    assert raw["category"] == "preference"
    assert raw["content_hash"] == compute_memory_content_hash("I commute by bicycle")
    assert raw["no_vector"] is False
    assert raw["embedding_model"] == "new-model"
    assert raw["vector_text"].startswith("[0.7")
    assert raw["matches_new"] is True and raw["matches_old"] is False
    stored = await pool.fetchval("SELECT content FROM memories WHERE id = $1", memory_id)
    assert "bicycle" not in str(stored), "content must stay encrypted at rest"


@pytest.mark.asyncio
async def test_edit_that_cannot_reembed_clears_the_obsolete_vector(
    monkeypatch: pytest.MonkeyPatch,
    isolated_edit_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_edit_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _user(pool)
    memory_id = await _seed(store, user_id, "I commute by tram")

    await store.update_memory_content(
        memory_id, "I commute by bicycle", clear_embedding=True, user_id=user_id
    )

    raw = await _raw(pool, memory_id)
    assert raw["no_vector"] is True
    assert raw["embedding_model"] is None
    assert raw["category"] == "fact", "category is unchanged unless supplied"


@pytest.mark.asyncio
async def test_edit_is_scoped_to_the_owner(
    monkeypatch: pytest.MonkeyPatch,
    isolated_edit_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_edit_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    owner = await _user(pool)
    other = await _user(pool)
    memory_id = await _seed(store, owner, "Owner's memory")

    result = await store.update_memory_content(
        memory_id, "Hijacked", clear_embedding=True, user_id=other
    )

    assert result is None
    reloaded = await store.get_memory(memory_id)
    assert reloaded is not None and reloaded["content"] == "Owner's memory"
    assert (await _raw(pool, memory_id))["no_vector"] is False


@pytest.mark.asyncio
async def test_edit_to_duplicate_active_content_conflicts_without_changes(
    monkeypatch: pytest.MonkeyPatch,
    isolated_edit_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_edit_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _user(pool)
    await _seed(store, user_id, "Already remembered")
    memory_id = await _seed(store, user_id, "Something else")

    with pytest.raises(MemoryContentConflictError):
        await store.update_memory_content(
            memory_id, "Already remembered", clear_embedding=True, user_id=user_id
        )

    reloaded = await store.get_memory(memory_id)
    assert reloaded is not None and reloaded["content"] == "Something else"
    assert (await _raw(pool, memory_id))["no_vector"] is False


@pytest.mark.asyncio
async def test_background_reembed_of_old_text_cannot_overwrite_an_edit(
    monkeypatch: pytest.MonkeyPatch,
    isolated_edit_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_edit_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _user(pool)
    memory_id = await _seed(store, user_id, "I commute by tram")
    old_hash = compute_memory_content_hash("I commute by tram")

    # A re-embed reads the old text, then the person edits before it writes.
    await store.update_memory_content(
        memory_id,
        "I commute by bicycle",
        embedding=_vector(0.7),
        embedding_model="new-model",
        user_id=user_id,
    )
    stale = await store.update_memory_embedding(
        memory_id, _vector(0.2), embedding_model="stale", expected_content_hash=old_hash
    )
    current = await store.update_memory_embedding(
        memory_id,
        _vector(0.9),
        embedding_model="fresh",
        expected_content_hash=compute_memory_content_hash("I commute by bicycle"),
    )

    assert stale is False
    assert current is True
    raw = await _raw(pool, memory_id)
    assert raw["embedding_model"] == "fresh"
    assert raw["vector_text"].startswith("[0.9")


@pytest.mark.asyncio
@pytest.mark.parametrize("a_changes_text", [False, True], ids=["category-only", "text"])
async def test_concurrent_edit_cannot_mix_text_and_vector_or_be_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    isolated_edit_pool: tuple[asyncpg.Pool, str],
    a_changes_text: bool,
) -> None:
    """A reads X; B commits Y with its vector; A resumes and must not write."""
    pool, _dsn = isolated_edit_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _user(pool)
    memory_id = await _seed(store, user_id, "I commute by tram")

    async def embed(_texts: list[str]):
        from orchestrator.memory.embedding import EmbeddingBatchResult

        return EmbeddingBatchResult(
            embeddings=[_vector(0.3)], provider="voyage", model="a-model", storage_model="a-model"
        )

    monkeypatch.setattr(memories_router, "embed_documents_with_metadata", embed)
    real_get = store.get_memory
    reads = 0

    async def get_then_let_b_commit(memory_id_arg: uuid.UUID):
        nonlocal reads
        reads += 1
        row = await real_get(memory_id_arg)
        if reads == 1:
            # B's edit commits between A's read and A's write.
            await store.update_memory_content(
                memory_id_arg,
                "I commute by bicycle",
                embedding=_vector(0.7),
                embedding_model="b-model",
                user_id=user_id,
                require_content_hash=True,
                expected_content_hash=row["content_hash"] if row else None,
            )
        return row

    monkeypatch.setattr(store, "get_memory", get_then_let_b_commit)
    app_state: Any = type("State", (), {"memory_store": store})()
    auth = AuthenticatedDevice(user_id=user_id, device_id=uuid.uuid4(), session_id=uuid.uuid4())
    request = memories_router.MemoryUpdate(
        content="I commute by bus" if a_changes_text else "I commute by tram",
        category="project",
    )

    with pytest.raises(HTTPException) as raised:
        await memories_router.update_memory(memory_id, request, app_state=app_state, auth=auth)

    assert raised.value.status_code == 412
    monkeypatch.setattr(store, "get_memory", real_get)
    reloaded = await store.get_memory(memory_id)
    assert reloaded is not None
    assert reloaded["content"] == "I commute by bicycle"
    raw = await _raw(pool, memory_id)
    assert raw["content_hash"] == compute_memory_content_hash("I commute by bicycle")
    assert raw["embedding_model"] == "b-model"
    assert raw["vector_text"].startswith("[0.7")
    assert raw["category"] == "fact"
