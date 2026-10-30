"""#317 owner-approved lexical search on an owned network-none synthetic database.

Opt in with --history-postgres; no application DSN or shipped migrations.
"""

from datetime import datetime, timezone
import uuid

from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.compute_metadata_support import Bridge, Database
from tests.test_history_pagination_postgres import history_cluster as history_cluster


OWNER = uuid.UUID(int=1)
OTHER = uuid.UUID(int=2)
ENC = ContentEncryption(Fernet.generate_key().decode())


@pytest_asyncio.fixture
async def search_database(history_cluster):
    cluster = history_cluster
    name = "md_" + uuid.uuid4().hex
    cluster.sql(f'CREATE DATABASE "{name}"')
    cluster.databases.add(name)
    cluster.sql(
        "CREATE TABLE memories (id uuid PRIMARY KEY, user_id uuid NOT NULL, content text, "
        "content_tsv tsvector, local_only boolean, category text, source_type text, "
        "status text, valid_to timestamptz, created_at timestamptz); "
        "CREATE INDEX ON memories USING GIN(content_tsv); "
        "GRANT SELECT, INSERT ON memories TO md_app;",
        database=name,
    )
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
        cluster.sql(f'DROP DATABASE "{name}"')
        cluster.databases.remove(name)
        cluster.record("memory_search_database_dropped", database=name, connections=0)


async def seed(
    pool,
    number,
    text,
    *,
    owner=OWNER,
    local=False,
    status="active",
    indexed=True,
    category="fact",
    source="user_created",
    valid_to=None,
):
    identity = uuid.UUID(int=number)
    await pool.execute(
        "INSERT INTO memories VALUES($1,$2,$3,to_tsvector('english',$4),$5,$6,$7,$8,$9,$10)",
        identity,
        owner,
        ENC.encrypt(text),
        text if indexed else None,
        local,
        category,
        source,
        status,
        valid_to,
        datetime(2026, 10, 29, tzinfo=timezone.utc),
    )
    return identity


@pytest.mark.asyncio
async def test_encrypted_matches_are_owner_filtered_counted_and_paged(search_database):
    pool, store = search_database
    expected = [await seed(pool, n, f"I cycle to the bicycle shop {n}") for n in range(10, 15)]
    await seed(pool, 20, "bicycle shop", owner=OTHER)
    await seed(pool, 21, "bicycle shop", local=True)
    await seed(pool, 22, "bicycle shop", status="deleted")
    await seed(pool, 23, "bicycle shop", indexed=False)
    filters = {"search": "bicycle shop", "include_local": False, "source_type": "user_created"}
    total = await store.count_listed_memories(OWNER, **filters)
    pages = [await store.list_memories(OWNER, limit=2, offset=n, **filters) for n in (0, 2, 4)]
    rows = [row for page in pages for row in page]
    assert total == 5
    assert [row["id"] for row in rows] == list(reversed(expected))
    assert all("I cycle" in row["content"] for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("bicycles", 1),
        ("bicycle shop", 1),
        ("shop bicycle", 1),
        ("the bicycle", 1),
        ("bicycle | tram", 0),
        ("bicycle tram", 0),
        ("bicy", 0),
        ("the", 0),
        ("%_!", 0),
    ],
)
async def test_explicit_english_all_word_semantics(search_database, query, expected):
    pool, store = search_database
    await seed(pool, 10, "I visit the bicycle shop")
    assert await store.count_listed_memories(OWNER, search=query) == expected
    assert len(await store.list_memories(OWNER, search=query)) == expected


@pytest.mark.asyncio
async def test_index_missing_stays_missing_without_plaintext_fallback_or_backfill(search_database):
    pool, store = search_database
    identity = await seed(pool, 10, "I visit the bicycle shop", indexed=False)
    before = dict(await pool.fetchrow("SELECT * FROM memories WHERE id=$1", identity))
    assert before["content"] != "I visit the bicycle shop"
    assert before["content_tsv"] is None
    assert await store.count_listed_memories(OWNER, search="bicycle") == 0
    assert await store.list_memories(OWNER, search="bicycle") == []
    after = dict(await pool.fetchrow("SELECT * FROM memories WHERE id=$1", identity))
    assert after == before
    assert (await store.list_memories(OWNER))[0]["content"] == "I visit the bicycle shop"


@pytest.mark.asyncio
async def test_empty_search_remains_unfiltered_including_missing_index(search_database):
    pool, store = search_database
    identity = await seed(pool, 10, "I visit the bicycle shop", indexed=False)
    assert await store.count_listed_memories(OWNER, search="") == 1
    assert [row["id"] for row in await store.list_memories(OWNER, search="")] == [identity]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"category": "preference", "source_type": "import"}, [11]),
        ({"status": "superseded"}, [12]),
        ({"confirmed": True, "include_local": False}, [11, 10]),
        ({"confirmed": False}, [12]),
        ({"created_after": datetime(2026, 10, 30, tzinfo=timezone.utc)}, []),
    ],
)
async def test_search_preserves_other_filters_and_page_totals(search_database, filters, expected):
    pool, store = search_database
    await seed(pool, 10, "bicycle shop")
    await seed(pool, 11, "bicycle shop", category="preference", source="import")
    await seed(
        pool,
        12,
        "bicycle shop",
        status="superseded",
        valid_to=datetime(2026, 10, 29, tzinfo=timezone.utc),
    )
    await seed(pool, 13, "bicycle shop", local=True)
    await seed(pool, 14, "bicycle shop", owner=OTHER)
    total = await store.count_listed_memories(OWNER, search="bicycle shop", **filters)
    rows = []
    for offset in range(total + 1):
        page = await store.list_memories(
            OWNER, search="bicycle shop", limit=1, offset=offset, **filters
        )
        rows.extend(row["id"].int for row in page)
    assert total == len(expected)
    assert rows == expected
