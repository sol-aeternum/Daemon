"""UI-only SQL regressions in an explicitly owned, finite synthetic database.

Opt in with --history-postgres. Reuses the existing network-none harness, never
an application DSN; creates only synthetic tables, not application migrations.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import uuid

from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator.history_pagination import HistoryBoundary
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.compute_metadata_support import Bridge, Cluster, Database


AT = datetime(2026, 10, 28, 0, 0, 0, 123456, tzinfo=timezone.utc)
OWNER = uuid.UUID(int=1)
OTHER = uuid.UUID(int=2)
ENC = ContentEncryption(Fernet.generate_key().decode())


@pytest.fixture(scope="module")
def history_cluster(request):
    if not request.config.getoption("--history-postgres"):
        pytest.skip("requires explicit --history-postgres (no application DSN fallback)")
    root = Path(os.environ["OPENCODE_WORK_TEST_DIR"])
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    resource = root / ("hp" + uuid.uuid4().hex[:4])
    assert len(os.fsencode(resource / ".s.PGSQL.5432")) < 108, "owned socket path too long"
    resource.mkdir(mode=0o700)
    evidence.mkdir(parents=True, exist_ok=True)
    cluster = Cluster(resource, evidence)
    try:
        cluster.start()
        yield cluster
    finally:
        cluster.stop()


@pytest_asyncio.fixture
async def history_db(history_cluster):
    cluster = history_cluster
    name = "md_" + uuid.uuid4().hex
    cluster.sql(f'CREATE DATABASE "{name}"')
    cluster.databases.add(name)
    cluster.sql(
        """
        CREATE TABLE conversations (
            id uuid PRIMARY KEY, user_id uuid NOT NULL, title text, pinned boolean,
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            last_activity_at timestamptz, message_count int DEFAULT 0
        );
        CREATE TABLE messages (
            id uuid PRIMARY KEY, conversation_id uuid NOT NULL, user_id uuid NOT NULL,
            role text, content text, status text, created_at timestamptz NOT NULL
        );
        GRANT SELECT, INSERT, UPDATE, DELETE ON conversations, messages TO md_app;
        """,
        database=name,
    )
    # Tests are serialized; reuse this exact private socket after Bridge.close.
    # A second directory level exceeds AF_UNIX's limit for long task names.
    resource = cluster.resource
    bridge = Bridge(cluster, resource / ".s.PGSQL.5432")
    await bridge.start()
    db = Database(cluster, name, resource, bridge)
    try:
        async with db.pool() as pool:
            yield pool, MemoryStore(pool, ENC)
    finally:
        await bridge.close()
        assert cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{name}'") == "0"
        assert name in cluster.databases
        cluster.sql(f'DROP DATABASE "{name}"')
        cluster.sql("CHECKPOINT")
        cluster.databases.remove(name)
        cluster.record("history_database_dropped", database=name, connections=0)


async def seed_conversation(
    pool, number, *, owner=OWNER, pinned: bool | None = False, at=AT, title="synthetic"
):
    identity = uuid.UUID(int=number)
    await pool.execute(
        "INSERT INTO conversations(id,user_id,title,pinned,created_at,updated_at,last_activity_at) "
        "VALUES($1,$2,$3,$4,$5,$5,$5)",
        identity,
        owner,
        title,
        pinned,
        at,
    )
    return identity


async def seed_message(pool, conversation, number, *, owner=OWNER, at=AT, status="complete"):
    identity = uuid.UUID(int=number)
    await pool.execute(
        "INSERT INTO messages(id,conversation_id,user_id,role,content,status,created_at) "
        "VALUES($1,$2,$3,'assistant',$4,$5,$6)",
        identity,
        conversation,
        owner,
        ENC.encrypt(f"synthetic {number}"),
        status,
        at,
    )
    return identity


def conversation_boundary(row):
    return HistoryBoundary(row["last_activity_at"], row["id"], bool(row["pinned"]))


def message_boundary(row):
    return HistoryBoundary(row["created_at"], row["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, 1, 100, 101, 200])
async def test_real_message_boundaries_and_exact_multiples(history_db, size):
    pool, store = history_db
    conversation = await seed_conversation(pool, 10)
    expected = [await seed_message(pool, conversation, n) for n in range(1000, 1000 + size)]
    before, collected = None, []
    while True:
        rows, more = await store.get_message_history_page(
            conversation, user_id=OWNER, before=before
        )
        assert len(rows) <= 100
        assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
        collected = [r["id"] for r in rows] + collected
        if not more:
            break
        assert len(rows) == 100
        before = message_boundary(rows[0])
    assert collected == expected


@pytest.mark.asyncio
async def test_real_deleted_message_anchor_insertion_and_inclusive_refresh(history_db):
    pool, store = history_db
    conversation = await seed_conversation(pool, 10)
    for n in range(100, 106):
        await seed_message(pool, conversation, n, status="streaming" if n == 104 else "cancelled")
    rows, more = await store.get_message_history_page(conversation, user_id=OWNER, limit=2)
    assert more and [r["id"].int for r in rows] == [104, 105]
    anchor = message_boundary(rows[0])
    await pool.execute("DELETE FROM messages WHERE id=$1", anchor.id)
    await seed_message(pool, conversation, 106, at=AT + timedelta(seconds=1))
    rows, more = await store.get_message_history_page(
        conversation, user_id=OWNER, limit=2, before=anchor
    )
    assert more and [r["id"].int for r in rows] == [102, 103]
    # A later commit in a previously traversed range is found by a range refresh.
    await seed_message(pool, conversation, 107)
    await seed_message(pool, conversation, 108, owner=OTHER)
    rows, more = await store.get_message_history_page(
        conversation,
        user_id=OWNER,
        through=HistoryBoundary(AT, uuid.UUID(int=103)),
    )
    assert not more and [r["id"].int for r in rows] == [103, 105, 107, 106]
    assert all(r["status"] in {"complete", "cancelled"} for r in rows)
    assert await store.get_message_history_page(conversation, user_id=OTHER) == ([], False)


@pytest.mark.asyncio
async def test_real_list_order_count_search_and_deleted_cursor(history_db):
    pool, store = history_db
    for n in range(10, 112):
        await seed_conversation(
            pool,
            n,
            title="Council" if n % 2 else "Draft",
            pinned=True if n == 10 else (None if n == 11 else False),
        )
    await seed_conversation(pool, 900, owner=OTHER)
    await seed_message(pool, uuid.UUID(int=12), 1000, at=AT + timedelta(seconds=1))
    # A foreign owner's inconsistent row must not affect derived count/activity.
    await seed_message(pool, uuid.UUID(int=11), 1001, owner=OTHER, at=AT + timedelta(days=1))
    rows, total, more = await store.list_conversations_page(OWNER)
    assert total == 102 and more and len(rows) == 100
    assert [r["id"].int for r in rows[:3]] == [10, 12, 111]
    anchor = conversation_boundary(rows[-1])
    await pool.execute("DELETE FROM conversations WHERE id=$1", anchor.id)
    tail, total, more = await store.list_conversations_page(OWNER, before=anchor)
    assert total == 101 and not more and len(tail) == 2
    assert len({r["id"] for r in rows + tail}) == 102
    empty, total, more = await store.list_conversations_page(OWNER, offset=1000)
    assert empty == [] and total == 101 and not more
    found, total, more = await store.list_conversations_page(OWNER, search=" council ", limit=50)
    assert total == 51 and len(found) == 50 and more
    tail, total, more = await store.list_conversations_page(
        OWNER, search="council", offset=50, limit=50
    )
    assert total == 51 and len(tail) == 1 and not more


@pytest.mark.asyncio
async def test_real_list_mutability_is_not_a_snapshot(history_db):
    pool, store = history_db
    for n in range(10, 15):
        await seed_conversation(pool, n)
    head, _, _ = await store.list_conversations_page(OWNER, limit=2)
    boundary = conversation_boundary(head[-1])
    await pool.execute("UPDATE conversations SET pinned=TRUE WHERE id=$1", uuid.UUID(int=10))
    tail, _, _ = await store.list_conversations_page(OWNER, before=boundary)
    assert uuid.UUID(int=10) not in [r["id"] for r in tail]
    refreshed, _, _ = await store.list_conversations_page(OWNER, limit=2)
    assert refreshed[0]["id"] == uuid.UUID(int=10)
    await pool.execute("UPDATE conversations SET pinned=FALSE WHERE id=$1", uuid.UUID(int=10))
    # Values survive deleted cursor rows; all returned keys are strictly below.
    await pool.execute("DELETE FROM conversations WHERE id=$1", boundary.id)
    tail, total, _ = await store.list_conversations_page(OWNER, before=boundary)
    assert total == 4 and [r["id"].int for r in tail] == [12, 11, 10]
