"""Memory listing pages and counts against a real disposable database (#249).

Checks that ``count_listed_memories`` agrees with the rows ``list_memories``
pages through for each filter, and that offset paging visits every matching
row exactly once even when created_at values tie. Requires
``ENTITLEMENTS_TEST_DATABASE_URL``; skips before any connection without it.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import asyncpg
import pytest
from cryptography.fernet import Fernet

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.routes.memories import NON_DELETED_STATUSES
from tests.benchmark_longmemeval.isolated_database import isolated_database_fixture

isolated_listing_pool = isolated_database_fixture(
    "memory_listing_", audited_tables=["users", "memories"]
)


def _crypto(monkeypatch: pytest.MonkeyPatch) -> ContentEncryption:
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", secrets.token_urlsafe(48))
    get_settings.cache_clear()
    return ContentEncryption(get_settings().daemon_encryption_key)


async def _seed(pool: asyncpg.Pool, store: MemoryStore) -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
        VALUES ($1, $2, 'listing', 'listing', '{}'::jsonb, NOW(), NOW())
        """,
        user_id,
        f"memory-listing+{user_id.hex}@daemon.test",
    )
    plan = (
        [("active", "extracted")] * 14
        + [("active", "user_created")] * 5
        + [("active", "import")] * 3
        + [("superseded", "extracted")] * 2
        + [("rejected", "extracted")]
        + [("deleted", "extracted")] * 2
    )
    for index, (status, source) in enumerate(plan):
        memory = await store.insert_memory(
            user_id=user_id,
            content=f"Listing memory {index}",
            category="fact",
            source_type=source,
            embedding=None,
            status=status,
        )
        assert memory is not None
    # Force created_at ties so paging must rely on the id tie-breaker.
    await pool.execute(
        "UPDATE memories SET created_at = '2026-01-01T00:00:00Z' WHERE user_id = $1",
        user_id,
    )
    return user_id


async def _page_through(store: MemoryStore, user_id: uuid.UUID, **filters: Any) -> list[Any]:
    seen: list[Any] = []
    offset = 0
    while True:
        page = await store.list_memories(user_id, limit=4, offset=offset, **filters)
        seen.extend(row["id"] for row in page)
        if len(page) < 4:
            return seen
        offset += 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"status": "active"}, 22),
        ({"status": NON_DELETED_STATUSES}, 25),
        ({"status": "superseded"}, 2),
        ({"status": "active", "source_type": "user_created"}, 5),
        ({"status": "active", "source_type": "import"}, 3),
        ({"status": "active", "search": "memory 1"}, None),
    ],
    ids=["active", "all-non-deleted", "superseded", "added-by-you", "imported", "search"],
)
async def test_count_matches_every_row_paged_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    isolated_listing_pool: tuple[asyncpg.Pool, str],
    filters: dict[str, Any],
    expected: int | None,
) -> None:
    pool, _dsn = isolated_listing_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _seed(pool, store)

    total = await store.count_listed_memories(user_id, **filters)
    seen = await _page_through(store, user_id, **filters)

    assert len(seen) == len(set(seen)), "a row appeared on two pages"
    assert len(seen) == total
    if expected is not None:
        assert total == expected


@pytest.mark.asyncio
async def test_all_status_count_matches_clear_all_scope(
    monkeypatch: pytest.MonkeyPatch,
    isolated_listing_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _dsn = isolated_listing_pool
    store = MemoryStore(pool, _crypto(monkeypatch))
    user_id = await _seed(pool, store)

    shown = await store.count_listed_memories(user_id, status=NON_DELETED_STATUSES)
    affected = await store.delete_all_memories(user_id)

    assert shown == affected == 25
