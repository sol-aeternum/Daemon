"""Trust compare-and-set against real PostgreSQL JSONB (#601).

The in-memory doubles in ``test_multi_query_trust`` model JSONB array equality.
These tests use the suite's migrated-template clone so the stored representation,
order sensitivity and last_retrieved_at writes are verified by the database itself.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import asyncpg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from orchestrator.memory.store import MemoryStore
from orchestrator.memory.encryption import ContentEncryption
from tests.durable_tasks_support import seed_entitlement
from orchestrator.memory.trust_signals import (
    _record_added_retrieved_memories,
    record_retrieved_memories,
)


@pytest_asyncio.fixture
async def db(
    durable_database_template: Any,
) -> AsyncIterator[tuple[asyncpg.Pool, uuid.UUID, uuid.UUID]]:
    """Isolated clone; skips with the session template when its DSN is absent."""
    async with durable_database_template.clone() as dsn:
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=4,
            server_settings={"search_path": f"{durable_database_template.schema}, public"},
        )
        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        try:
            await pool.execute(
                "INSERT INTO users (id, email, name, username) VALUES ($1, $2, $3, $3)",
                user_id,
                f"trust-{user_id}@example.invalid",
                f"trust-{user_id.hex[:8]}",
            )
            await pool.execute(
                "INSERT INTO conversations (id, user_id, pipeline) VALUES ($1, $2, 'cloud')",
                conversation_id,
                user_id,
            )
            await seed_entitlement(pool, user_id)
            yield pool, user_id, conversation_id
        finally:
            await asyncio.wait_for(pool.close(), timeout=10)


def _store(pool: asyncpg.Pool) -> MemoryStore:
    # Only ``_pool`` is exercised by the trust functions under test.
    return cast(MemoryStore, SimpleNamespace(_pool=pool))


async def _seed_memories(pool: asyncpg.Pool, user_id: uuid.UUID, count: int) -> list[uuid.UUID]:
    store = MemoryStore(pool, ContentEncryption(Fernet.generate_key().decode()))
    memory_ids = []
    for index in range(count):
        row = await store.insert_memory(
            user_id, f"synthetic memory {index} {uuid.uuid4()}", "fact", "manual"
        )
        memory_ids.append(row["id"])
    return memory_ids


async def _stored_ids(pool: asyncpg.Pool, conversation_id: uuid.UUID) -> list[str]:
    raw = await pool.fetchval(
        "SELECT last_retrieved_memory_ids FROM conversations WHERE id = $1",
        conversation_id,
    )
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw or [])


async def _retrieved_at(
    pool: asyncpg.Pool, memory_ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime | None]:
    rows = await pool.fetch(
        "SELECT id, last_retrieved_at FROM memories WHERE id = ANY($1::uuid[])",
        memory_ids,
    )
    return {row["id"]: row["last_retrieved_at"] for row in rows}


@pytest.mark.asyncio
async def test_added_trust_appends_to_the_unsorted_baseline_that_was_stored(
    db: tuple[asyncpg.Pool, uuid.UUID, uuid.UUID],
) -> None:
    pool, user_id, conversation_id = db
    store = _store(pool)
    seeded = await _seed_memories(pool, user_id, 5)
    baseline = [seeded[2], seeded[0], seeded[1]]  # deliberately unsorted
    added = [seeded[3], seeded[4]]

    assert await record_retrieved_memories(conversation_id, baseline, store) is True
    assert await _record_added_retrieved_memories(conversation_id, baseline, added, store) is True

    assert await _stored_ids(pool, conversation_id) == [
        str(memory_id) for memory_id in [*baseline, *added]
    ]
    stamps = await _retrieved_at(pool, [*baseline, *added])
    assert all(value is not None for value in stamps.values()), stamps


@pytest.mark.asyncio
async def test_cas_mismatch_writes_nothing(
    db: tuple[asyncpg.Pool, uuid.UUID, uuid.UUID],
) -> None:
    pool, user_id, conversation_id = db
    store = _store(pool)
    seeded = await _seed_memories(pool, user_id, 4)
    baseline = [seeded[0], seeded[1]]
    await record_retrieved_memories(conversation_id, [seeded[0]], store)
    stored_before = await _stored_ids(pool, conversation_id)
    added = [seeded[2]]

    assert await _record_added_retrieved_memories(conversation_id, baseline, added, store) is False

    assert await _stored_ids(pool, conversation_id) == stored_before == [str(seeded[0])]
    stamps = await _retrieved_at(pool, added)
    assert stamps[added[0]] is None


@pytest.mark.asyncio
async def test_reordered_baseline_does_not_match(
    db: tuple[asyncpg.Pool, uuid.UUID, uuid.UUID],
) -> None:
    pool, user_id, conversation_id = db
    store = _store(pool)
    seeded = await _seed_memories(pool, user_id, 4)
    stored = [seeded[0], seeded[1]]
    await record_retrieved_memories(conversation_id, stored, store)
    added = [seeded[2]]

    assert (
        await _record_added_retrieved_memories(
            conversation_id, list(reversed(stored)), added, store
        )
        is False
    )

    assert await _stored_ids(pool, conversation_id) == [str(m) for m in stored]
    stamps = await _retrieved_at(pool, added)
    assert stamps[added[0]] is None


@pytest.mark.asyncio
async def test_empty_baseline_snapshot_replaces_stale_ids(
    db: tuple[asyncpg.Pool, uuid.UUID, uuid.UUID],
) -> None:
    pool, user_id, conversation_id = db
    store = _store(pool)
    seeded = await _seed_memories(pool, user_id, 5)
    stale = [seeded[0], seeded[1]]
    await record_retrieved_memories(conversation_id, stale, store)

    raw = await pool.fetchval(
        "SELECT last_retrieved_memory_ids FROM conversations WHERE id = $1",
        conversation_id,
    )
    assert isinstance(raw, str)
    decoded = json.loads(raw)
    assert decoded == [str(m) for m in stale]

    # Decoded (list) form asyncpg callers build from the column.
    assert (
        await _record_added_retrieved_memories(
            conversation_id, [], [seeded[2]], store, empty_baseline_snapshot=decoded
        )
        is True
    )
    assert await _stored_ids(pool, conversation_id) == [str(seeded[2])]

    # Raw (JSON text) form asyncpg actually returns for JSONB.
    await record_retrieved_memories(conversation_id, stale, store)
    raw = await pool.fetchval(
        "SELECT last_retrieved_memory_ids FROM conversations WHERE id = $1",
        conversation_id,
    )
    assert isinstance(raw, str)
    assert (
        await _record_added_retrieved_memories(
            conversation_id, [], [seeded[3]], store, empty_baseline_snapshot=raw
        )
        is True
    )
    stored = await _stored_ids(pool, conversation_id)
    assert stored == [str(seeded[3])]
    assert not any(str(memory_id) in stored for memory_id in stale), "stale IDs must not remain"

    # A snapshot that no longer matches the column writes nothing.
    assert (
        await _record_added_retrieved_memories(
            conversation_id, [], [seeded[4]], store, empty_baseline_snapshot=decoded
        )
        is False
    )
    assert await _stored_ids(pool, conversation_id) == stored


@pytest.mark.asyncio
async def test_stored_representation_matches_the_helper_json(
    db: tuple[asyncpg.Pool, uuid.UUID, uuid.UUID],
) -> None:
    pool, user_id, conversation_id = db
    store = _store(pool)
    seeded = await _seed_memories(pool, user_id, 3)
    retrieved = [seeded[1], seeded[2], seeded[0]]
    await record_retrieved_memories(conversation_id, retrieved, store)

    expected = json.dumps([str(memory_id) for memory_id in retrieved])
    matched = await pool.fetchval(
        "SELECT last_retrieved_memory_ids = $2::jsonb FROM conversations WHERE id = $1",
        conversation_id,
        expected,
    )
    assert matched is True, expected
    assert await _stored_ids(pool, conversation_id) == json.loads(expected)
