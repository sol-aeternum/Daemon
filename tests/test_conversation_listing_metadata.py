"""Real-PostgreSQL checks for authoritative conversation listing metadata.

User-approved fix (2026-09-30): the conversation list/get paths derive
``message_count`` and ``last_activity_at`` from actual saved messages instead
of the stale stored cache, without backfilling or mutating any stored row and
without hiding genuinely empty drafts.

Set ENTITLEMENTS_TEST_DATABASE_URL to an isolated disposable database (the
local docker-compose Postgres is fine); each test replays the shipped
migrations into its own throwaway schema and drops only that schema. No
provider requests are made and production rows are never touched.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import asyncpg
from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.worker import jobs

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _store(pool: asyncpg.Pool) -> MemoryStore:
    # Listing metadata never decrypts message content, but the store
    # constructor requires a fail-closed encryption instance.
    return MemoryStore(pool, ContentEncryption(Fernet.generate_key().decode()))


_LISTING_ENCRYPTION = ContentEncryption(Fernet.generate_key().decode())


@pytest_asyncio.fixture
async def listing_database() -> AsyncIterator[asyncpg.Pool]:
    dsn = os.environ.get("ENTITLEMENTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated ENTITLEMENTS_TEST_DATABASE_URL")
    schema = f"history_listing_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=6,
            server_settings={"search_path": f"{schema}, public"},
        )
        async with pool.acquire() as conn:
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                async with conn.transaction():
                    await conn.execute(migration.read_text())
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _add_user(pool: asyncpg.Pool) -> uuid.UUID:
    user_id = uuid.uuid4()
    # ``users.username`` is NOT NULL without a default (migrations 002/010);
    # everything else is nullable or defaulted.
    await pool.execute(
        "INSERT INTO users (id, username) VALUES ($1, $2)",
        user_id,
        f"user_{user_id.hex[:16]}",
    )
    return user_id


async def _add_conversation(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
    title: str | None = None,
    pinned: bool = False,
    age: timedelta = timedelta(0),
) -> uuid.UUID:
    stale_time = datetime.now(tz=timezone.utc) - age
    conversation_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO conversations (id, user_id, pipeline, title, pinned,
                                   updated_at, last_activity_at)
        VALUES ($1, $2, 'cloud', $3, $4, $5, $5)
        """,
        conversation_id,
        user_id,
        title,
        pinned,
        stale_time,
    )
    return conversation_id


async def _add_message(
    pool: asyncpg.Pool,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "user",
    status: str | None = "complete",
    age: timedelta = timedelta(0),
) -> datetime:
    created_at = datetime.now(tz=timezone.utc) - age
    await pool.execute(
        """
        INSERT INTO messages (conversation_id, user_id, role, content, status, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        conversation_id,
        user_id,
        role,
        _LISTING_ENCRYPTION.encrypt(f"fixture message for {conversation_id.hex} {role}"),
        status,
        created_at,
    )
    return created_at


async def _stored_row(pool: asyncpg.Pool, conversation_id: uuid.UUID) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT message_count, last_activity_at, updated_at
        FROM conversations
        WHERE id = $1
        """,
        conversation_id,
    )


@pytest.mark.asyncio
async def test_legacy_stale_metadata_returns_actual_message_counts(listing_database):
    """Regression: a stale cache (message_count=0) must not hide saved chats.

    The frontend drops rows with message counts <= 0 and default titles unless
    they are the open conversation, so a fresh fetch of this row must carry the
    derived count for the list to retain it.
    """
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)
    conversation_id = await _add_conversation(
        pool,
        user_id,
        title=None,
        age=timedelta(days=30),
    )
    await _add_message(pool, conversation_id, user_id, role="user", age=timedelta(hours=1))
    assistant_at = await _add_message(
        pool, conversation_id, user_id, role="assistant", age=timedelta(minutes=1)
    )

    conversations = await store.list_conversations(user_id)
    assert [c["id"] for c in conversations] == [conversation_id]
    assert conversations[0]["message_count"] == 2
    assert conversations[0]["last_activity_at"] == assistant_at

    # The stored row must stay untouched: no silent backfill/write-back.
    stored = await _stored_row(pool, conversation_id)
    assert stored is not None
    assert stored["message_count"] == 0
    assert stored["last_activity_at"] <= datetime.now(tz=timezone.utc) - timedelta(days=29)


@pytest.mark.asyncio
async def test_empty_draft_returns_zero_and_stays_in_list(listing_database):
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)
    conversation_id = await _add_conversation(pool, user_id)

    conversations = await store.list_conversations(user_id)
    assert [c["id"] for c in conversations] == [conversation_id]
    assert conversations[0]["message_count"] == 0
    # No messages: the stored updated_at is the effective activity.
    assert conversations[0]["last_activity_at"] == conversations[0]["updated_at"]


@pytest.mark.asyncio
async def test_ordering_uses_actual_activity_before_pagination(listing_database):
    """An old conversation with fresh messages must sort (and page) first."""
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)

    revived = await _add_conversation(pool, user_id, title="old", age=timedelta(days=30))
    recent = await _add_conversation(pool, user_id, title="recent", age=timedelta(days=1))
    middle = await _add_conversation(pool, user_id, title="middle", age=timedelta(days=10))
    await _add_message(pool, middle, user_id, age=timedelta(hours=5))
    await _add_message(pool, recent, user_id, age=timedelta(hours=12))
    await _add_message(pool, revived, user_id, age=timedelta(minutes=1))

    conversations = await store.list_conversations(user_id)
    assert [c["title"] for c in conversations] == ["old", "middle", "recent"]

    # Pagination slices the effective-activity order, not the stored recency.
    page = await store.list_conversations(user_id, limit=2, offset=2)
    assert [c["title"] for c in page] == ["recent"]


@pytest.mark.asyncio
async def test_pinned_conversations_take_precedence(listing_database):
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)

    active = await _add_conversation(pool, user_id, title="active", age=timedelta(days=2))
    await _add_message(pool, active, user_id, age=timedelta(minutes=1))
    await _add_conversation(pool, user_id, title="pinned", pinned=True, age=timedelta(days=40))

    conversations = await store.list_conversations(user_id)
    assert [c["title"] for c in conversations] == ["pinned", "active"]

    grouped = await store.list_conversations(user_id, limit=1, offset=1)
    assert [c["title"] for c in grouped] == ["active"]


@pytest.mark.asyncio
async def test_title_search_returns_stale_rows_with_derived_counts(listing_database):
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)
    target = await _add_conversation(pool, user_id, title="Council debrief")
    other = await _add_conversation(pool, user_id, title="Scratchpad")
    await _add_message(pool, target, user_id, age=timedelta(days=3))

    conversations = await store.list_conversations(user_id, search="council")
    assert [c["id"] for c in conversations] == [target]
    assert conversations[0]["message_count"] == 1

    empty = await store.list_conversations(user_id, search="scratchpad")
    assert [c["id"] for c in empty] == [other]
    assert empty[0]["message_count"] == 0


@pytest.mark.asyncio
async def test_foreign_owner_messages_are_excluded_from_aggregates(listing_database):
    pool = listing_database
    store = _store(pool)
    owner = await _add_user(pool)
    intruder = await _add_user(pool)
    conversation_id = await _add_conversation(pool, owner, title="mine")
    await _add_message(pool, conversation_id, owner, role="user")
    # A cross-owner row must not inflate the owner's derived count.
    await _add_message(pool, conversation_id, intruder, role="assistant")

    conversations = await store.list_conversations(owner)
    assert conversations[0]["message_count"] == 1

    intruder_list = await store.list_conversations(intruder)
    assert intruder_list == []


@pytest.mark.asyncio
async def test_count_covers_all_roles_and_statuses(listing_database):
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)
    conversation_id = await _add_conversation(pool, user_id, title="roles")
    for role in ("user", "assistant", "system", "tool"):
        for status in ("complete", "streaming", None):
            await _add_message(pool, conversation_id, user_id, role=role, status=status)

    conversations = await store.list_conversations(user_id)
    assert conversations[0]["message_count"] == 12


@pytest.mark.asyncio
async def test_deleted_messages_are_reflected_in_the_derived_count(listing_database):
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)
    conversation_id = await _add_conversation(pool, user_id, title="churn")
    for _ in range(3):
        await _add_message(pool, conversation_id, user_id)
    await pool.execute("DELETE FROM messages WHERE conversation_id = $1", conversation_id)
    await _add_message(pool, conversation_id, user_id)

    conversations = await store.list_conversations(user_id)
    assert conversations[0]["message_count"] == 1


@pytest.mark.asyncio
async def test_newer_updated_at_wins_for_post_message_edits(listing_database):
    """A rename/pin that postsdates the last message keeps its newer time."""
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)
    conversation_id = await _add_conversation(pool, user_id, title="first", age=timedelta(days=5))
    last_message_at = await _add_message(pool, conversation_id, user_id, age=timedelta(hours=2))
    renamed_at = datetime.now(tz=timezone.utc)
    await pool.execute(
        """
        UPDATE conversations
        SET title = 'renamed', updated_at = $2, last_activity_at = $2
        WHERE id = $1
        """,
        conversation_id,
        renamed_at,
    )

    conversation = await store.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation["message_count"] == 1
    assert conversation["last_activity_at"] == renamed_at
    assert conversation["last_activity_at"] > last_message_at


@pytest.mark.asyncio
async def test_get_conversation_returns_coherent_derived_metadata(listing_database):
    pool = listing_database
    store = _store(pool)
    user_id = await _add_user(pool)
    conversation_id = await _add_conversation(pool, user_id, age=timedelta(days=30))
    last_message_at = await _add_message(
        pool, conversation_id, user_id, role="assistant", age=timedelta(minutes=1)
    )

    conversation = await store.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation["message_count"] == 1
    assert conversation["last_activity_at"] == last_message_at
    # Stored cache fields remain untouched by reads.
    stored = await _stored_row(pool, conversation_id)
    assert stored is not None
    assert stored["message_count"] == 0

    missing = await store.get_conversation(uuid.uuid4())
    assert missing is None


@pytest.mark.asyncio
@pytest.mark.parametrize("job_name", ["generate_title", "generate_conversation_title_job"])
@pytest.mark.parametrize("edit", ["rename_locked", "rename_unlocked", "lock_only", "unchanged"])
@pytest.mark.parametrize("initial_title, initial_lock", [(None, None), ("New conversation", False)])
async def test_title_generation_preserves_in_flight_manual_edits(
    listing_database, monkeypatch, job_name, edit, initial_title, initial_lock
):
    """Pause the worker after reading, edit the real row, then resume its save."""
    pool = listing_database
    store = MemoryStore(pool, _LISTING_ENCRYPTION)
    user_id = await _add_user(pool)
    conversation_id = await _add_conversation(
        pool, user_id, title=initial_title, age=timedelta(days=1)
    )
    # Legacy drafts have nullable titles and locks; unchanged ones still title normally.
    await pool.execute(
        "UPDATE conversations SET title_locked = $2 WHERE id = $1", conversation_id, initial_lock
    )
    await _add_message(pool, conversation_id, user_id)
    started, resume = asyncio.Event(), asyncio.Event()

    async def paused_generator(messages):
        assert messages and messages[0]["role"] == "user"
        started.set()
        await resume.wait()
        return "Generated title"

    @asynccontextmanager
    async def checked_scope(scope_pool, account_id, **kwargs):
        assert scope_pool is pool
        assert account_id == user_id
        assert kwargs == {
            "operation": "agent",
            "auto_route": True,
            "background": True,
            "profile": "background",
        }
        yield

    monkeypatch.setattr(jobs, "generate_conversation_title", paused_generator)
    monkeypatch.setattr(jobs, "account_compute", checked_scope)
    ctx = {"store": store, "db_pool": pool}
    coroutine = (
        jobs.generate_title(ctx, conversation_id, "hello")
        if job_name == "generate_title"
        else jobs.generate_conversation_title_job(ctx, conversation_id)
    )
    task = asyncio.create_task(coroutine)
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        if edit != "unchanged":
            await store.update_conversation(
                conversation_id,
                title="Manual title" if edit.startswith("rename") else None,
                title_locked=edit != "rename_unlocked",
            )
        before_save = await _stored_row(pool, conversation_id)
        assert before_save is not None
        resume.set()
        result = await asyncio.wait_for(task, timeout=5)
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    row = await pool.fetchrow("SELECT * FROM conversations WHERE id = $1", conversation_id)
    assert row is not None
    if edit == "unchanged":
        assert row["title"] == "Generated title"
        assert row["title_locked"] is initial_lock
        assert row["updated_at"] > before_save["updated_at"]
        assert row["last_activity_at"] == row["updated_at"]
        assert result == (
            "Generated title"
            if job_name == "generate_title"
            else {"status": "ok", "title": "Generated title"}
        )
    else:
        assert row["title"] == ("Manual title" if edit.startswith("rename") else initial_title)
        assert row["title_locked"] is (edit != "rename_unlocked")
        assert row["updated_at"] == before_save["updated_at"]
        assert row["last_activity_at"] == before_save["last_activity_at"]
        assert result == (
            None
            if job_name == "generate_title"
            else {"status": "skipped", "reason": "title_changed_or_locked"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("lock_title", [False, True])
async def test_generated_title_save_rechecks_after_concurrent_row_lock(
    listing_database, lock_title
):
    """The SQL predicate is re-evaluated after a concurrent rename commits."""
    pool = listing_database
    user_id = await _add_user(pool)
    conversation_id = await _add_conversation(pool, user_id)
    saver_connection = await pool.acquire()
    save_task = None
    try:
        # Force the save onto this connection so the test can observe its lock wait.
        # The helper uses fetchrow only, shared by Pool and Connection.
        saver = MemoryStore(cast(asyncpg.Pool, saver_connection), _LISTING_ENCRYPTION)
        async with pool.acquire() as editor:
            async with editor.transaction():
                await editor.execute(
                    "UPDATE conversations SET title = 'Manual title', title_locked = $2 "
                    "WHERE id = $1",
                    conversation_id,
                    lock_title,
                )
                save_task = asyncio.create_task(
                    saver.save_generated_conversation_title(
                        conversation_id, title="Generated title", expected_title=None
                    )
                )
                # A pending row lock means the UPDATE has started before commit;
                # a sleep alone could pass without exercising that interleaving.
                async with asyncio.timeout(5):
                    while not await pool.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = $1 AND NOT granted)",
                        saver_connection.get_server_pid(),
                    ):
                        await asyncio.sleep(0.01)
        assert await asyncio.wait_for(save_task, timeout=5) is False
        row = await pool.fetchrow(
            "SELECT title, title_locked FROM conversations WHERE id = $1", conversation_id
        )
        assert row is not None
        assert row["title"] == "Manual title"
        assert row["title_locked"] is lock_title
    finally:
        if save_task is not None:
            if not save_task.done():
                save_task.cancel()
            await asyncio.gather(save_task, return_exceptions=True)
        await pool.release(saver_connection)
