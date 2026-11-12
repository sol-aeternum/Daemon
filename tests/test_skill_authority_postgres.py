"""#696 revocation boundary on real PostgreSQL.

Needs a disposable database (``TASKS_TEST_DATABASE_URL`` or
``ENTITLEMENTS_TEST_DATABASE_URL``); each test uses and drops its own schema.
Ordering is forced with gates and observed lock waits, never with sleeps alone.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.skills_projection import SkillProjectionStore
from orchestrator.skills_sync import SkillSyncService
from orchestrator.tools.skill_manage import SkillManageTool

MIGRATION = Path(__file__).resolve().parents[1] / "migrations/028_skill_projection.sql"
SKILL_ID = "guarded-skill"
BODY = "---\nname: Guarded Skill\ndescription: d\nenabled: true\n---\nOriginal body."


def _database_url() -> str | None:
    return os.environ.get("TASKS_TEST_DATABASE_URL") or os.environ.get(
        "ENTITLEMENTS_TEST_DATABASE_URL"
    )


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    dsn = _database_url()
    if not dsn:
        pytest.skip("requires an isolated TASKS_TEST_DATABASE_URL")
    schema = f"skill_authority_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    created = None
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=8, server_settings={"search_path": f"{schema},public"}
        )
        assert created is not None
        await created.execute(MIGRATION.read_text(encoding="utf-8"))
        yield created
    finally:
        if created is not None:
            await created.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


@pytest.fixture
def skills_dir(tmp_path: Path):
    (tmp_path / f"{SKILL_ID}.md").write_text(BODY)
    with (
        patch("orchestrator.skills_store.SKILLS_DIR", tmp_path),
        patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path),
    ):
        yield tmp_path


async def _seed(pool: asyncpg.Pool, *, source_type: str = "system", allow: bool) -> None:
    await pool.execute(
        "INSERT INTO skill_projections (skill_id, name, source_file_path, source_hash, "
        "source_type, allow_autonomous_edit, created_by, trigger_conditions) "
        "VALUES ($1, 'Guarded Skill', 'x', 'h', $2::skill_source_type, $3, 'owner', 'kept')",
        SKILL_ID,
        source_type,
        allow,
    )


async def _grant(pool: asyncpg.Pool) -> bool:
    return await pool.fetchval(
        "SELECT allow_autonomous_edit FROM skill_projections WHERE skill_id=$1", SKILL_ID
    )


async def _wait_for_lock_waiter(pool: asyncpg.Pool) -> None:
    """Return once some backend in this database is blocked on a row lock."""
    for _ in range(400):
        waiting = await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock'"
        )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no backend ever waited on a lock")


@pytest.mark.asyncio
async def test_slow_sync_cannot_restore_a_revoked_grant(pool: asyncpg.Pool, skills_dir: Path):
    await _seed(pool, allow=True)
    store = SkillProjectionStore(pool)
    embedding_started, release = asyncio.Event(), asyncio.Event()

    async def slow_embed(*_args, **_kwargs):
        embedding_started.set()
        await release.wait()
        return None

    with patch("orchestrator.skills_sync.embed_skill_content", slow_embed):
        sync = asyncio.create_task(SkillSyncService(store).sync_skill(SKILL_ID))
        await embedding_started.wait()  # the sync is past any read it could make
        assert await store.update_autonomous_edit(SKILL_ID, False) is True
        release.set()
        assert (await sync).success is True

    row = await pool.fetchrow("SELECT * FROM skill_projections WHERE skill_id=$1", SKILL_ID)
    assert row is not None
    assert row["allow_autonomous_edit"] is False
    # Sync still records the file, and leaves stored authority and provenance alone.
    assert row["source_file_path"] == str(skills_dir / f"{SKILL_ID}.md")
    assert (row["source_type"], row["created_by"], row["trigger_conditions"]) == (
        "system",
        "owner",
        "kept",
    )


@pytest.mark.asyncio
async def test_upsert_with_a_stale_grant_copy_cannot_restore_it(pool: asyncpg.Pool):
    await _seed(pool, allow=False)
    await SkillProjectionStore(pool).upsert_projection(
        skill_id=SKILL_ID,
        name="Guarded Skill",
        description="",
        source_file_path="x",
        source_hash="h2",
        source_type="system",
        allow_autonomous_edit=True,  # a copy read before the owner revoked
    )
    assert await _grant(pool) is False


@pytest.mark.asyncio
async def test_sync_of_an_unknown_file_creates_a_protected_row(
    pool: asyncpg.Pool, skills_dir: Path
):
    async def no_embed(*_args, **_kwargs):
        return None

    with patch("orchestrator.skills_sync.embed_skill_content", no_embed):
        await SkillSyncService(SkillProjectionStore(pool)).sync_skill(SKILL_ID)
    row = await pool.fetchrow("SELECT * FROM skill_projections WHERE skill_id=$1", SKILL_ID)
    assert row is not None
    assert (row["source_type"], row["allow_autonomous_edit"]) == ("manual", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["patch", "delete"])
async def test_revocation_committed_while_mutation_waits_refuses_it(
    pool: asyncpg.Pool, skills_dir: Path, action: str
):
    """Revoke first: the tool call that raced it sees the revocation and refuses."""
    await _seed(pool, allow=True)
    tool = SkillManageTool(db_pool=pool)
    skill_file = skills_dir / f"{SKILL_ID}.md"

    async with pool.acquire() as owner:
        revoke = owner.transaction()
        await revoke.start()
        await owner.execute(
            "UPDATE skill_projections SET allow_autonomous_edit=FALSE WHERE skill_id=$1", SKILL_ID
        )
        call = asyncio.create_task(
            tool.execute(
                action=action, skill_id=SKILL_ID, old_text="Original body.", new_text="Changed."
            )
        )
        await _wait_for_lock_waiter(pool)  # the tool is parked on the row lock
        assert not call.done()
        assert skill_file.read_text() == BODY
        await revoke.commit()

    assert "protected" in json.loads(await call)["error"]
    assert skill_file.read_text() == BODY
    assert await _grant(pool) is False


@pytest.mark.asyncio
async def test_revocation_waits_for_an_admitted_mutation(pool: asyncpg.Pool, skills_dir: Path):
    """Mutation first: the revocation is not acknowledged until the mutation ends."""
    await _seed(pool, allow=True)
    store = SkillProjectionStore(pool)

    async with store.locked_projection(SKILL_ID) as projection:
        assert projection is not None and projection["allow_autonomous_edit"] is True
        revoke = asyncio.create_task(store.update_autonomous_edit(SKILL_ID, False))
        await _wait_for_lock_waiter(pool)
        assert not revoke.done()

    assert await revoke is True
    assert await _grant(pool) is False
    result = await SkillManageTool(db_pool=pool).execute(
        action="patch", skill_id=SKILL_ID, old_text="Original body.", new_text="Changed."
    )
    assert "protected" in json.loads(result)["error"]
    assert (skills_dir / f"{SKILL_ID}.md").read_text() == BODY


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["patch", "delete"])
async def test_granted_mutation_succeeds_and_keeps_the_grant(
    pool: asyncpg.Pool, skills_dir: Path, action: str
):
    await _seed(pool, allow=True)

    async def no_embed(*_args, **_kwargs):
        return None

    with patch("orchestrator.skills_sync.embed_skill_content", no_embed):
        result = json.loads(
            await SkillManageTool(db_pool=pool).execute(
                action=action, skill_id=SKILL_ID, old_text="Original body.", new_text="Changed."
            )
        )
    skill_file = skills_dir / f"{SKILL_ID}.md"
    if action == "patch":
        assert result["patched"] is True and "Changed." in skill_file.read_text()
        assert await _grant(pool) is True
    else:
        assert result["deleted"] is True and not skill_file.exists()
        assert await pool.fetchval("SELECT count(*) FROM skill_projections") == 0
