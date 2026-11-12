"""Account-owned skill store (docs/ACCOUNT_OWNED_SKILLS_DESIGN.md, step 1).

The store tests run against real PostgreSQL and need a disposable database
(``TASKS_TEST_DATABASE_URL`` or ``ENTITLEMENTS_TEST_DATABASE_URL``); each uses
and drops its own schema. Ordering in the race tests is forced with observed
lock waits, never with sleeps alone.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from orchestrator.memory.encryption import ContentEncryption, EncryptionKeyMissing
from orchestrator.services import skills as skills_module
from orchestrator.services.skills import (
    EMBEDDING_DIMENSIONS,
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    MAX_SKILLS_PER_ACCOUNT,
    REVISION_RETENTION,
    SkillError,
    SkillStore,
    derive_slug_lookup_key,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/068_account_owned_skills.sql"
ROLLBACK = ROOT / "migrations/rollback/068_account_owned_skills.down.sql"

# --------------------------------------------------------------------------
# No database
# --------------------------------------------------------------------------


def test_migration_is_additive_account_owned_and_has_a_rollback() -> None:
    up, down = MIGRATION.read_text(), ROLLBACK.read_text()
    assert "user_id UUID REFERENCES users(id) ON DELETE CASCADE" in up
    assert "REFERENCES skills(id) ON DELETE CASCADE" in up
    assert "DROP TABLE" not in up and "ALTER TABLE" not in up
    assert "DROP TABLE IF EXISTS skill_revisions" in down
    assert "DROP TABLE IF EXISTS skills" in down


def test_migration_stores_no_plaintext_text_column_and_no_content_hash() -> None:
    """Every text-typed column is an envelope or the enumerated actor."""
    columns = [
        line.split()[0]
        for line in MIGRATION.read_text().splitlines()
        if line.startswith("    ") and " TEXT " in f"{line} "
    ]
    assert columns, "expected text columns in the migration"
    assert all(name.endswith("_encrypted") or name == "actor" for name in columns), columns
    statements = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    assert "hash" not in statements.lower()


def test_lookup_key_is_derived_not_the_encryption_key() -> None:
    key = Fernet.generate_key().decode()
    derived = derive_slug_lookup_key(key)
    assert len(derived) == 32
    assert derived == derive_slug_lookup_key(key)
    assert derived != derive_slug_lookup_key(Fernet.generate_key().decode())
    assert derived.hex() not in key and derived not in __import__("base64").urlsafe_b64decode(key)


@pytest.mark.parametrize("bad", ["", "not-base64!!", "c2hvcnQ="])
def test_lookup_key_refuses_a_malformed_encryption_key(bad: str) -> None:
    with pytest.raises(EncryptionKeyMissing):
        derive_slug_lookup_key(bad)


def test_store_module_touches_no_files() -> None:
    source = Path(skills_module.__file__).read_text()
    for needle in ("open(", "write_text", "read_text", "SKILLS_DIR", "Path("):
        assert needle not in source, needle


# --------------------------------------------------------------------------
# Store (real PostgreSQL)
# --------------------------------------------------------------------------


def _database_url() -> str | None:
    return os.environ.get("TASKS_TEST_DATABASE_URL") or os.environ.get(
        "ENTITLEMENTS_TEST_DATABASE_URL"
    )


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    dsn = _database_url()
    if not dsn:
        pytest.skip("requires an isolated TASKS_TEST_DATABASE_URL")
    schema = f"account_skills_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    created = None
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=8, server_settings={"search_path": f"{schema},public"}
        )
        assert created is not None
        async with created.acquire() as conn:
            await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")
            await conn.execute(
                "CREATE TYPE skill_source_type AS ENUM "
                "('system', 'imported', 'manual', 'autonomous')"
            )
            async with conn.transaction():
                await conn.execute(MIGRATION.read_text(encoding="utf-8"))
        yield created
    finally:
        if created is not None:
            await created.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _user(pool: asyncpg.Pool) -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute("INSERT INTO users (id) VALUES ($1)", user_id)
    return user_id


def _store(pool: asyncpg.Pool, key: str | None = None) -> SkillStore:
    resolved = key or Fernet.generate_key().decode()
    return SkillStore(pool, ContentEncryption(resolved), derive_slug_lookup_key(resolved))


async def _create(store: SkillStore, user_id: uuid.UUID, name: str = "Deploy Notes", **kw: Any):
    fields: dict[str, Any] = {"description": "how we deploy", "body": "Step one."}
    fields.update(kw)
    return await store.create(user_id, name=name, **fields)


async def _reason(awaitable: Any) -> str:
    with pytest.raises(SkillError) as caught:
        await awaitable
    return caught.value.reason


async def _wait_for_lock_wait(pool: asyncpg.Pool, fragment: str) -> None:
    """Block until some backend is waiting on a lock while running ``fragment``."""
    for _ in range(400):
        waiting = await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
            "AND query LIKE '%' || $1 || '%' AND pid <> pg_backend_pid()",
            fragment,
        )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"no backend blocked on: {fragment}")


# ---- T1: isolation --------------------------------------------------------


@pytest.mark.asyncio
async def test_accounts_never_see_or_change_each_others_skills(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice, bob = await _user(pool), await _user(pool)
    created = await _create(store, alice)
    assert created["slug"] == "deploy-notes" and created["scope"] == "account"

    assert await store.list(bob) == []
    assert await _reason(store.get(bob, "deploy-notes")) == "not_found"
    assert await _reason(store.list_revisions(bob, "deploy-notes")) == "not_found"
    assert await _reason(store.save(bob, "deploy-notes", body="x")) == "not_found"
    assert await _reason(store.revert(bob, "deploy-notes", 1)) == "not_found"
    assert await _reason(store.delete(bob, "deploy-notes")) == "not_found"
    assert await _reason(store.set_enabled(bob, "deploy-notes", False)) == "not_found"
    assert await _reason(store.set_autonomous_edit(bob, "deploy-notes", True)) == "not_found"
    assert await store.touch_usage(bob, "deploy-notes") is False

    # The same slug is free in another account and stays separate.
    bobs = await _create(store, bob, body="Bob's own.")
    assert bobs["id"] != created["id"]
    assert (await store.get(alice, "deploy-notes"))["body"] == "Step one."
    assert (await store.get(bob, "deploy-notes"))["body"] == "Bob's own."
    assert await _reason(_create(store, alice)) == "slug_taken"


@pytest.mark.asyncio
async def test_equal_slugs_have_different_lookup_digests_per_owner(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice, bob = await _user(pool), await _user(pool)
    await _create(store, alice)
    await _create(store, bob)
    await store.publish_shipped("deploy-notes", name="Deploy Notes", description="", body="s")
    digests = [r["slug_lookup"] for r in await pool.fetch("SELECT slug_lookup FROM skills")]
    assert len(digests) == 3 and len(set(digests)) == 3
    assert hashlib.sha256(b"deploy-notes").digest() not in digests


# ---- T3: shipped skills ---------------------------------------------------


@pytest.mark.asyncio
async def test_shipped_skills_are_readable_and_refuse_every_account_write(
    pool: asyncpg.Pool,
) -> None:
    store = _store(pool)
    alice = await _user(pool)
    shipped = await store.publish_shipped(
        "pdf-export", name="PDF Export", description="d", body="b"
    )
    assert shipped["scope"] == "shipped" and shipped["source_type"] == "system"
    assert shipped["allow_autonomous_edit"] is False

    assert (await store.get(alice, "pdf-export"))["body"] == "b"
    assert [s["slug"] for s in await store.list(alice)] == ["pdf-export"]
    assert await store.list(alice, include_shipped=False) == []

    for actor in ("user", "autonomous", "consolidation"):
        assert (
            await _reason(store.save(alice, "pdf-export", body="x", actor=actor))  # type: ignore[arg-type]
            == "shipped_read_only"
        )
        assert (
            await _reason(store.delete(alice, "pdf-export", actor=actor))  # type: ignore[arg-type]
            == "shipped_read_only"
        )
    assert await _reason(store.revert(alice, "pdf-export", 1)) == "shipped_read_only"
    assert await _reason(store.set_enabled(alice, "pdf-export", False)) == "shipped_read_only"
    assert (
        await _reason(store.set_autonomous_edit(alice, "pdf-export", True)) == "shipped_read_only"
    )
    assert await _reason(store.list_revisions(alice, "pdf-export")) == "not_found"
    assert await store.touch_usage(alice, "pdf-export") is False
    assert (await store.get(alice, "pdf-export"))["revision"] == 1


@pytest.mark.asyncio
async def test_account_skill_shadows_a_shipped_skill_with_the_same_slug(
    pool: asyncpg.Pool,
) -> None:
    store = _store(pool)
    alice, bob = await _user(pool), await _user(pool)
    await store.publish_shipped("deploy-notes", name="Deploy Notes", description="", body="shipped")
    await _create(store, alice, body="mine")

    assert (await store.get(alice, "deploy-notes"))["body"] == "mine"
    assert [(s["scope"], s["body"]) for s in await store.list(alice)] == [("account", "mine")]
    assert (await store.get(bob, "deploy-notes"))["scope"] == "shipped"
    # Writing through the slug reaches the account's own skill, never the shipped one.
    await store.save(alice, "deploy-notes", body="mine v2")
    assert (await store.get(bob, "deploy-notes"))["body"] == "shipped"


@pytest.mark.asyncio
async def test_publish_shipped_is_idempotent_and_revisions_on_change(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    first = await store.publish_shipped("pdf-export", name="PDF", description="d", body="b")
    again = await store.publish_shipped("pdf-export", name="PDF", description="d", body="b")
    assert again["revision"] == 1 and again["id"] == first["id"]
    changed = await store.publish_shipped("pdf-export", name="PDF", description="d", body="b2")
    assert changed["revision"] == 2
    actors = await pool.fetch("SELECT actor FROM skill_revisions ORDER BY revision")
    assert [r["actor"] for r in actors] == ["shipped", "shipped"]

    results = await asyncio.gather(
        *(store.publish_shipped("new-one", name="New", description="", body="b") for _ in range(6))
    )
    assert len({r["id"] for r in results}) == 1
    assert await store.remove_shipped("pdf-export") is True
    assert await store.remove_shipped("pdf-export") is False


@pytest.mark.asyncio
async def test_database_refuses_a_shipped_row_that_is_not_system_or_holds_a_grant(
    pool: asyncpg.Pool,
) -> None:
    insert = (
        "INSERT INTO skills (id, user_id, slug_encrypted, slug_lookup, name_encrypted, "
        "description_encrypted, body_encrypted, trigger_conditions_encrypted, source_type, "
        "allow_autonomous_edit) VALUES ($1, $2, 'e', $3, 'e', 'e', 'e', 'e', "
        "$4::skill_source_type, $5)"
    )
    owner = await _user(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(insert, uuid.uuid4(), None, b"a", "manual", False)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(insert, uuid.uuid4(), None, b"b", "system", True)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(insert, uuid.uuid4(), owner, b"c", "system", False)


# ---- Authority ------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_type_and_grant_are_derived_from_the_actor(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    manual = await _create(store, alice, "Manual")
    imported = await _create(store, alice, "Imported", imported=True)
    learned = await _create(store, alice, "Learned", actor="autonomous", complexity_origin=7)

    assert (manual["source_type"], manual["allow_autonomous_edit"]) == ("manual", False)
    assert (imported["source_type"], imported["allow_autonomous_edit"]) == ("imported", False)
    assert (learned["source_type"], learned["allow_autonomous_edit"]) == ("autonomous", True)
    assert learned["complexity_origin"] == 7
    assert await _reason(_create(store, alice, "Nope", actor="shipped")) == "actor_invalid"


@pytest.mark.asyncio
async def test_autonomous_writers_need_the_owners_grant(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice)
    for actor in ("autonomous", "consolidation"):
        assert (
            await _reason(store.save(alice, "deploy-notes", body="x", actor=actor))  # type: ignore[arg-type]
            == "autonomous_edit_not_granted"
        )
        assert (
            await _reason(store.delete(alice, "deploy-notes", actor=actor))  # type: ignore[arg-type]
            == "autonomous_edit_not_granted"
        )
    assert (
        await _reason(store.save(alice, "deploy-notes", body="x", actor="shipped"))
        == "actor_invalid"
    )

    await store.set_autonomous_edit(alice, "deploy-notes", True)
    saved = await store.save(alice, "deploy-notes", body="refined", actor="autonomous")
    assert saved["revision"] == 2 and saved["source_type"] == "manual"

    # Revoking stops the evaluator even on a skill it created itself.
    await _create(store, alice, "Learned", actor="autonomous")
    await store.set_autonomous_edit(alice, "learned", False)
    assert (
        await _reason(store.save(alice, "learned", body="x", actor="autonomous"))
        == "autonomous_edit_not_granted"
    )


# ---- T4: revocation races -------------------------------------------------


@pytest.mark.asyncio
async def test_revocation_that_commits_first_stops_a_waiting_autonomous_save(
    pool: asyncpg.Pool,
) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice, actor="autonomous")

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await conn.execute(
            "UPDATE skills SET allow_autonomous_edit = FALSE WHERE user_id = $1", alice
        )
        save = asyncio.create_task(
            store.save(alice, "deploy-notes", body="late", actor="autonomous")
        )
        await _wait_for_lock_wait(pool, "FROM skills WHERE user_id = $1 AND slug_lookup")
        await tx.commit()

    assert await _reason(save) == "autonomous_edit_not_granted"
    current = await store.get(alice, "deploy-notes")
    assert (current["body"], current["revision"]) == ("Step one.", 1)


@pytest.mark.asyncio
async def test_autonomous_save_that_commits_first_is_kept_and_revocation_then_holds(
    pool: asyncpg.Pool,
) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice, actor="autonomous")

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await conn.fetchval("SELECT id FROM skills WHERE user_id = $1 FOR UPDATE", alice)
        revoke = asyncio.create_task(store.set_autonomous_edit(alice, "deploy-notes", False))
        await _wait_for_lock_wait(pool, "FROM skills WHERE user_id = $1 AND slug_lookup")
        await conn.execute("UPDATE skills SET revision = revision WHERE user_id = $1", alice)
        await tx.commit()

    assert (await revoke)["allow_autonomous_edit"] is False
    assert (
        await _reason(store.save(alice, "deploy-notes", body="x", actor="autonomous"))
        == "autonomous_edit_not_granted"
    )


@pytest.mark.asyncio
async def test_delete_and_save_race_leaves_no_orphan_revision(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice)
    outcomes = await asyncio.gather(
        store.delete(alice, "deploy-notes"),
        store.save(alice, "deploy-notes", body="x"),
        return_exceptions=True,
    )
    assert outcomes[0] is None
    assert outcomes[1] is None or isinstance(outcomes[1], (dict, SkillError))
    assert await pool.fetchval("SELECT count(*) FROM skills") == 0
    assert await pool.fetchval("SELECT count(*) FROM skill_revisions") == 0


# ---- T5/T6: revisions -----------------------------------------------------


@pytest.mark.asyncio
async def test_expected_revision_mismatch_changes_nothing(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice)
    await store.save(alice, "deploy-notes", body="two", expected_revision=1)
    assert (
        await _reason(store.save(alice, "deploy-notes", body="stale", expected_revision=1))
        == "revision_conflict"
    )
    assert (
        await _reason(store.revert(alice, "deploy-notes", 1, expected_revision=1))
        == "revision_conflict"
    )
    current = await store.get(alice, "deploy-notes")
    assert (current["body"], current["revision"]) == ("two", 2)
    assert await pool.fetchval("SELECT count(*) FROM skill_revisions") == 2


@pytest.mark.asyncio
async def test_concurrent_saves_produce_consecutive_revisions(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice)
    await asyncio.gather(*(store.save(alice, "deploy-notes", body=f"b{i}") for i in range(12)))
    revisions = [
        r["revision"]
        for r in await pool.fetch("SELECT revision FROM skill_revisions ORDER BY revision")
    ]
    assert revisions == list(range(1, 14))
    head = await store.get(alice, "deploy-notes")
    newest = (await store.list_revisions(alice, "deploy-notes"))[0]
    assert head["revision"] == 13 and newest["revision"] == 13
    assert newest["body"] == head["body"]


@pytest.mark.asyncio
async def test_partial_save_keeps_other_fields_and_slug_is_stable_on_rename(
    pool: asyncpg.Pool,
) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice, trigger_conditions="when deploying")
    saved = await store.save(alice, "deploy-notes", name="Release Notes")
    assert saved["slug"] == "deploy-notes" and saved["name"] == "Release Notes"
    assert saved["body"] == "Step one." and saved["trigger_conditions"] == "when deploying"
    assert await _reason(store.get(alice, "release-notes")) == "not_found"


@pytest.mark.asyncio
async def test_revert_writes_a_new_revision_and_never_moves_backward(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice)
    await store.save(alice, "deploy-notes", body="two", name="Second")
    reverted = await store.revert(alice, "deploy-notes", 1)
    assert (reverted["revision"], reverted["body"], reverted["name"]) == (
        3,
        "Step one.",
        "Deploy Notes",
    )
    history = await store.list_revisions(alice, "deploy-notes")
    assert [(r["revision"], r["actor"], r["body"]) for r in history] == [
        (3, "user", "Step one."),
        (2, "user", "two"),
        (1, "user", "Step one."),
    ]
    assert await _reason(store.revert(alice, "deploy-notes", 99)) == "revision_not_found"


@pytest.mark.asyncio
async def test_retention_trims_old_revisions_and_keeps_the_current_one(
    pool: asyncpg.Pool,
) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice)
    total = REVISION_RETENTION + 5
    for index in range(2, total + 1):
        await store.save(alice, "deploy-notes", body=f"b{index}")
    history = await store.list_revisions(alice, "deploy-notes")
    assert [r["revision"] for r in history] == list(range(total, total - REVISION_RETENTION, -1))
    assert history[0]["body"] == (await store.get(alice, "deploy-notes"))["body"] == f"b{total}"
    assert await _reason(store.revert(alice, "deploy-notes", 1)) == "revision_not_found"


# ---- T10: limits and input ------------------------------------------------


@pytest.mark.asyncio
async def test_content_limits_at_and_above_the_bound(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    at_limit = await store.create(
        alice,
        name="n" * MAX_NAME_CHARS,
        description="d" * MAX_DESCRIPTION_CHARS,
        body="b" * MAX_BODY_CHARS,
    )
    assert len(at_limit["body"]) == MAX_BODY_CHARS
    slug = at_limit["slug"]

    cases = {
        "name_too_long": {"name": "n" * (MAX_NAME_CHARS + 1)},
        "description_too_long": {"description": "d" * (MAX_DESCRIPTION_CHARS + 1)},
        "body_too_long": {"body": "b" * (MAX_BODY_CHARS + 1)},
        "trigger_conditions_too_long": {"trigger_conditions": "t" * 1537},
        "name_required": {"name": "   "},
        "body_invalid": {"body": 5},
    }
    for reason, fields in cases.items():
        assert await _reason(store.save(alice, slug, **fields)) == reason
        create_fields = {"name": "Other", "description": "", "body": "", **fields}
        assert await _reason(store.create(alice, **create_fields)) == reason
    assert (await store.get(alice, slug))["revision"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["../etc", "a/b", "UPPER", "", "x" * 3 + "..", 7])
async def test_invalid_slugs_refuse_before_any_query(pool: asyncpg.Pool, slug: Any) -> None:
    store = _store(pool)
    alice = await _user(pool)
    assert await _reason(store.get(alice, slug)) == "slug_invalid"
    assert await _reason(store.save(alice, slug, body="x")) == "slug_invalid"
    assert await _reason(store.delete(alice, slug)) == "slug_invalid"


@pytest.mark.asyncio
async def test_unknown_account_and_unsluggable_name_refuse(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    assert await _reason(_create(store, uuid.uuid4())) == "account_unavailable"
    alice = await _user(pool)
    assert await _reason(_create(store, alice, "../../etc/passwd")) == "slug_invalid"
    assert await _reason(_create(store, alice, "!!!")) == "slug_invalid"


@pytest.mark.asyncio
async def test_account_limit_holds_under_concurrency(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skills_module, "MAX_SKILLS_PER_ACCOUNT", 3)
    assert MAX_SKILLS_PER_ACCOUNT > 3
    store = _store(pool)
    alice, bob = await _user(pool), await _user(pool)
    outcomes = await asyncio.gather(
        *(_create(store, alice, f"Skill {i}") for i in range(8)), return_exceptions=True
    )
    created = [o for o in outcomes if isinstance(o, dict)]
    refused = [o for o in outcomes if isinstance(o, SkillError)]
    assert len(created) == 3 and len(refused) == 5
    assert {e.reason for e in refused} == {"limit_reached"}
    # Shipped skills and other accounts do not count against the limit.
    await store.publish_shipped("shipped-one", name="S", description="", body="")
    assert (await _create(store, bob))["revision"] == 1
    await store.delete(alice, created[0]["slug"])
    assert (await _create(store, alice, "Replacement"))["revision"] == 1


# ---- T9: erasure ----------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_the_account_removes_only_its_skills_and_revisions(
    pool: asyncpg.Pool,
) -> None:
    store = _store(pool)
    alice, bob = await _user(pool), await _user(pool)
    await _create(store, alice)
    await store.save(alice, "deploy-notes", body="two")
    await _create(store, bob)
    await store.publish_shipped("pdf-export", name="PDF", description="", body="b")

    await pool.execute("DELETE FROM users WHERE id = $1", alice)

    assert await pool.fetchval("SELECT count(*) FROM skills WHERE user_id = $1", alice) == 0
    assert await pool.fetchval("SELECT count(*) FROM skills") == 2
    assert await pool.fetchval("SELECT count(*) FROM skill_revisions") == 2
    assert (await store.get(bob, "deploy-notes"))["body"] == "Step one."


# ---- T11: at rest ---------------------------------------------------------

MARKERS = {
    "name": "Zebrafish Ledger",
    "slug": "zebrafish-ledger",
    "description": "MARKER-DESC-quarterly-payroll",
    "body": "MARKER-BODY-account-4417",
    "trigger": "MARKER-TRIGGER-when-asked-about-payroll",
    "second_body": "MARKER-BODY2-refined",
}


async def _raw_text(pool: asyncpg.Pool) -> str:
    """Every column of both tables, as the database stores it."""
    parts: list[str] = []
    for table in ("skills", "skill_revisions"):
        for row in await pool.fetch(f"SELECT * FROM {table}"):
            for value in row.values():
                parts.append(value.hex() if isinstance(value, bytes) else str(value))
                if isinstance(value, bytes):
                    parts.append(value.decode("latin-1"))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_no_content_or_unkeyed_digest_is_stored_in_plaintext(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    created = await store.create(
        alice,
        name=MARKERS["name"],
        description=MARKERS["description"],
        body=MARKERS["body"],
        trigger_conditions=MARKERS["trigger"],
        actor="autonomous",
    )
    await store.save(alice, MARKERS["slug"], body=MARKERS["second_body"], actor="autonomous")
    await store.save(alice, MARKERS["slug"], description="d2", actor="consolidation")
    await store.revert(alice, MARKERS["slug"], 1)
    await store.store_embedding(uuid.UUID(created["id"]), 4, [0.25] * EMBEDDING_DIMENSIONS)
    await store.publish_shipped(
        MARKERS["slug"], name=MARKERS["name"], description=MARKERS["description"], body="b"
    )

    raw = await _raw_text(pool)
    for marker in MARKERS.values():
        assert marker not in raw, marker
        assert marker.lower() not in raw.lower(), marker
        for digest in (hashlib.sha256, hashlib.sha1, hashlib.md5):
            assert digest(marker.encode()).hexdigest() not in raw, (digest.__name__, marker)

    assert (await store.get(alice, MARKERS["slug"]))["body"] == MARKERS["body"]

    await pool.execute("DELETE FROM users WHERE id = $1", alice)
    assert await pool.fetchval("SELECT count(*) FROM skills WHERE user_id IS NOT NULL") == 0
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM skill_revisions r JOIN skills s "
            "ON s.id = r.skill_id WHERE s.user_id IS NOT NULL"
        )
        == 0
    )
    assert await pool.fetchval("SELECT count(*) FROM skills WHERE embedding IS NOT NULL") == 0


@pytest.mark.asyncio
async def test_wrong_key_raises_and_finds_nothing_by_slug(pool: asyncpg.Pool) -> None:
    alice = await _user(pool)
    await _create(_store(pool), alice)
    other = _store(pool)
    assert await _reason(other.get(alice, "deploy-notes")) == "not_found"
    with pytest.raises(ValueError, match="decryption failed"):
        await other.list(alice)


# ---- Derived state --------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_is_stored_only_for_the_current_revision(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    created = await _create(store, alice)
    skill_id = uuid.UUID(created["id"])
    vector = [0.5] * EMBEDDING_DIMENSIONS
    assert created["has_current_embedding"] is False

    await store.save(alice, "deploy-notes", body="two")
    assert await store.store_embedding(skill_id, 1, vector) is False
    assert await pool.fetchval("SELECT embedding IS NULL FROM skills") is True

    assert await store.store_embedding(skill_id, 2, vector) is True
    assert (await store.get(alice, "deploy-notes"))["has_current_embedding"] is True
    await store.save(alice, "deploy-notes", body="three")
    assert (await store.get(alice, "deploy-notes"))["has_current_embedding"] is False
    assert await _reason(store.store_embedding(skill_id, 3, [0.5] * 3)) == "embedding_invalid"


@pytest.mark.asyncio
async def test_usage_and_switches_are_not_revisions(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    alice = await _user(pool)
    await _create(store, alice)
    assert await store.touch_usage(alice, "deploy-notes") is True
    disabled = await store.set_enabled(alice, "deploy-notes", False)
    assert (disabled["enabled"], disabled["use_count"], disabled["revision"]) == (False, 1, 1)
    assert disabled["last_used_at"] is not None
    assert await store.list(alice, enabled=True) == []
    assert [s["slug"] for s in await store.list(alice, enabled=False)] == ["deploy-notes"]
    assert await _reason(store.set_enabled(alice, "deploy-notes", "yes")) == "enabled_invalid"  # type: ignore[arg-type]
    assert await pool.fetchval("SELECT count(*) FROM skill_revisions") == 1
