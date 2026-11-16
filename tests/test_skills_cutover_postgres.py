"""Cutover of every skill reader and writer to the account-owned store.

docs/ACCOUNT_OWNED_SKILLS_DESIGN.md step 2, tests T1-T3, T7-T9 and T11. The
HTTP routes, the ``skill_manage`` tool, the evaluator, consolidation and the
prompt index all run here against real PostgreSQL (``TASKS_TEST_DATABASE_URL``
or ``ENTITLEMENTS_TEST_DATABASE_URL``), each test in its own schema.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI

from orchestrator.auth import (
    AdminOrDeviceAuth,
    AuthenticatedDevice,
    require_admin_or_device_auth,
    require_device_auth,
)
from orchestrator.memory.store import MemoryStore
from orchestrator.routes import skills as skills_routes
from orchestrator.services import skill_format, skill_index
from orchestrator.services.skill_format import (
    MAX_SKILL_FILE_CHARS,
    parse_skill_markdown,
    render_skill_markdown,
)
from orchestrator.services.skill_index import build_skill_index
from orchestrator.services.shipped_skills import (
    SHIPPED_SKILLS_DIR,
    MANIFEST_FILE,
    Manifest,
    ManifestError,
    read_catalogue,
    read_manifest,
    skill_file_digest,
    sync_shipped_skills,
)
from orchestrator.services.skills import EMBEDDING_DIMENSIONS, SkillError, SkillStore
from orchestrator.skill_evaluator import SkillEvaluationRequest, SkillEvaluator
from orchestrator.tools import skill_manage as skill_manage_module
from orchestrator.tools.skill_manage import SkillManageTool
from orchestrator.worker import jobs

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [
    ROOT / "migrations/068_account_owned_skills.sql",
    ROOT / "migrations/029_skill_consolidation_nudge.sql",
    ROOT / "migrations/036_skill_consolidation_pending_status.sql",
    ROOT / "migrations/070_shipped_skill_catalogue.sql",
]
LOG_MIGRATION = ROOT / "migrations/069_skill_consolidation_log_content_free.sql"
LOG_ROLLBACK = ROOT / "migrations/rollback/069_skill_consolidation_log_content_free.down.sql"

KEY = Fernet.generate_key().decode()
SKILL_TABLES = ("skills", "skill_revisions", "skill_consolidation_log")

# Strings that must never be readable in the database.
MARKER_NAME = "Zephyrquill Ledger Reconciliation"
MARKER_DESCRIPTION = "Reconcile the quaggaflux ledger exports"
MARKER_BODY = "always confirm the vortexplum totals"
MARKER_TRIGGER = "when the user mentions snargleby invoices"
MARKERS = (
    "zephyrquill",
    "quaggaflux",
    "vortexplum",
    "snargleby",
    "Zephyrquill",
)


def _database_url() -> str | None:
    return os.environ.get("TASKS_TEST_DATABASE_URL") or os.environ.get(
        "ENTITLEMENTS_TEST_DATABASE_URL"
    )


@pytest_asyncio.fixture
async def bare_pool() -> AsyncIterator[asyncpg.Pool]:
    """A schema with users and the skill tables, before migration 069."""
    dsn = _database_url()
    if not dsn:
        pytest.skip("requires an isolated TASKS_TEST_DATABASE_URL")
    schema = f"skills_cutover_test_{uuid.uuid4().hex}"
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
            for migration in MIGRATIONS:
                async with conn.transaction():
                    await conn.execute(migration.read_text(encoding="utf-8"))
        yield created
    finally:
        if created is not None:
            await created.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


@pytest_asyncio.fixture
async def pool(bare_pool: asyncpg.Pool) -> asyncpg.Pool:
    async with bare_pool.acquire() as conn, conn.transaction():
        await conn.execute(LOG_MIGRATION.read_text(encoding="utf-8"))
    return bare_pool


@pytest.fixture
def store(pool: asyncpg.Pool) -> SkillStore:
    return SkillStore(pool, KEY)


@pytest.fixture(autouse=True)
def _one_key_and_fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every surface builds its store with the test key; embeddings are local."""

    def build(db_pool: asyncpg.Pool, settings: Any = None) -> SkillStore:
        return SkillStore(db_pool, KEY)

    for module in (skills_routes, skill_index):
        monkeypatch.setattr(module, "create_skill_store", build)
    monkeypatch.setattr("orchestrator.services.skills.create_skill_store", build)
    monkeypatch.setattr("orchestrator.skill_evaluator.create_skill_store", build)

    async def embed_documents(texts: list[str], **_: Any) -> list[list[float]]:
        return [_vector(text) for text in texts]

    monkeypatch.setattr("orchestrator.memory.embedding.embed_documents", embed_documents)


def _vector(text: str) -> list[float]:
    """A unit vector chosen by the text's first word: same word, same vector."""
    word = text.split()[0].lower() if text.split() else ""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[sum(word.encode()) % EMBEDDING_DIMENSIONS] = 1.0
    return vector


async def _user(pool: asyncpg.Pool) -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute("INSERT INTO users (id) VALUES ($1)", user_id)
    return user_id


async def _dump(pool: asyncpg.Pool, tables: tuple[str, ...] = SKILL_TABLES) -> str:
    """Every row of the skill tables as raw text, embeddings included."""
    parts: list[str] = []
    for table in tables:
        rows = await pool.fetch(f"SELECT row_to_json(t)::text AS row FROM {table} t")
        parts.extend(f"{table}:{row['row']}" for row in rows)
    return "\n".join(sorted(parts))


def _assert_no_markers(dump: str) -> None:
    for marker in MARKERS:
        assert marker not in dump, marker


# --------------------------------------------------------------------------
# HTTP routes
# --------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _client(
    pool: asyncpg.Pool | None, user_id: uuid.UUID
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(skills_routes.router)
    app.state.app_state = SimpleNamespace(db_pool=pool, memory_store=None)
    app.dependency_overrides[require_device_auth] = lambda: AuthenticatedDevice(
        user_id=user_id, device_id=uuid.uuid4(), session_id=uuid.uuid4()
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _create(client: httpx.AsyncClient, name: str = "Deploy Checklist") -> dict[str, Any]:
    response = await client.post(
        "/skills", json={"name": name, "description": "How to deploy", "content": "# Steps"}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_routes_return_only_the_callers_skills(pool: asyncpg.Pool) -> None:
    """T1: another account's skill is absent from every route, by guessed slug too."""
    alice, bob = await _user(pool), await _user(pool)
    async with _client(pool, alice) as a, _client(pool, bob) as b:
        created = await _create(a)
        slug = created["id"]

        assert (await b.get("/skills")).json() == {"skills": []}
        assert (await b.get(f"/skills/{slug}")).status_code == 404
        assert (await b.get(f"/skills/{slug}/download")).status_code == 404
        assert (await b.put(f"/skills/{slug}", json={"content": "stolen"})).status_code == 404
        assert (
            await b.patch(f"/skills/{slug}/enabled", json={"enabled": False})
        ).status_code == 404
        assert (
            await b.patch(f"/skills/{slug}/autonomous-edit", json={"allow_autonomous_edit": True})
        ).status_code == 404
        assert (await b.delete(f"/skills/{slug}")).status_code == 404
        # The internal id is not an address either.
        internal_id = await pool.fetchval("SELECT id::text FROM skills")
        assert (await b.get(f"/skills/{internal_id}")).status_code == 404

        # The same name is free in the other account and stays separate.
        theirs = await _create(b)
        assert theirs["id"] == slug
        await b.put(f"/skills/{slug}", json={"content": "bob's own"})

        mine = (await a.get(f"/skills/{slug}")).json()
        assert mine["content"] == "# Steps"
        assert mine["revision"] == 1
        assert [s["id"] for s in (await a.get("/skills")).json()["skills"]] == [slug]


@pytest.mark.asyncio
async def test_route_response_contract(pool: asyncpg.Pool) -> None:
    alice = await _user(pool)
    async with _client(pool, alice) as a:
        created = await _create(a)
        assert created["scope"] == "account"
        assert created["revision"] == 1
        assert created["source_type"] == "manual"
        assert created["allow_autonomous_edit"] is False
        assert created["enabled"] is True
        # The exact response shape; the retired upgrade and provenance fields are gone.
        assert set(created) == {
            "id", "name", "description", "enabled", "updated_at", "scope", "revision",
            "source_type", "allow_autonomous_edit", "use_count", "last_used_at", "content",
        }  # fmt: skip

        listed = (await a.get("/skills")).json()["skills"][0]
        assert "content" not in listed
        assert listed["scope"] == "account" and listed["revision"] == 1

        assert (await _create_raw(a, "Deploy Checklist")).status_code == 409


async def _create_raw(client: httpx.AsyncClient, name: str) -> httpx.Response:
    return await client.post("/skills", json={"name": name, "description": "", "content": ""})


@pytest.mark.asyncio
async def test_put_expected_revision_conflict_changes_nothing(pool: asyncpg.Pool) -> None:
    alice = await _user(pool)
    async with _client(pool, alice) as a:
        slug = (await _create(a))["id"]
        saved = await a.put(f"/skills/{slug}", json={"content": "v2", "expected_revision": 1})
        assert saved.status_code == 200 and saved.json()["revision"] == 2

        before = await _dump(pool)
        stale = await a.put(f"/skills/{slug}", json={"content": "v3", "expected_revision": 1})
        assert stale.status_code == 409
        assert "changed elsewhere" in stale.json()["detail"]
        assert await _dump(pool) == before

        # Without an expected revision the save applies, as before.
        assert (await a.put(f"/skills/{slug}", json={"content": "v3"})).json()["revision"] == 3
        # The enabled switch is not a revision.
        switched = await a.put(f"/skills/{slug}", json={"enabled": False})
        assert switched.json()["enabled"] is False and switched.json()["revision"] == 3


async def _visible(pool: asyncpg.Pool, user_id: uuid.UUID) -> list[tuple[str, int, bool]]:
    """What any other reader sees right now: committed state only."""
    return [
        (skill["body"], skill["revision"], skill["enabled"])
        for skill in await SkillStore(pool, KEY).list(user_id)
    ]


async def _wait_for_lock_wait(pool: asyncpg.Pool) -> None:
    """Return once some backend in this database is blocked on a lock."""
    for _ in range(400):
        if await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no backend reached a lock wait")


@pytest.mark.asyncio
async def test_disabled_create_is_never_visible_enabled(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skill requested disabled is one insert: no reader ever sees it enabled."""
    alice = await _user(pool)

    async def no_second_write(*_: Any, **__: Any) -> Any:
        raise AssertionError("enabled must not be a second transaction")

    monkeypatch.setattr(SkillStore, "set_enabled", no_second_write)
    async with _client(pool, alice) as a:
        # Hold the account row the create serializes on, so the create is
        # in flight while a reader looks.
        async with pool.acquire() as blocker:
            transaction = blocker.transaction()
            await transaction.start()
            await blocker.execute("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", alice)
            pending = asyncio.create_task(
                a.post(
                    "/skills",
                    json={"name": "Quiet", "description": "d", "content": "x", "enabled": False},
                )
            )
            await _wait_for_lock_wait(pool)
            assert await _visible(pool, alice) == []
            assert await build_skill_index(pool, alice) == ""
            await transaction.commit()
        created = await pending
        assert created.status_code == 201 and created.json()["enabled"] is False
        assert await _visible(pool, alice) == [("x", 1, False)]
        assert await build_skill_index(pool, alice) == ""

        uploaded = await a.post(
            "/skills/upload",
            files={
                "file": (
                    "off.md",
                    "---\nname: Off\ndescription: d\nenabled: false\n---\nbody\n",
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 201 and uploaded.json()["enabled"] is False
        # Overwriting with an enabled file flips content and switch together.
        replaced = await a.post(
            "/skills/upload",
            files={
                "file": ("off.md", "---\nname: Off\ndescription: d\n---\nnew\n", "text/markdown")
            },
            data={"overwrite": "true"},
        )
        assert (replaced.json()["enabled"], replaced.json()["content"]) == (True, "new")
    assert "Off" in await build_skill_index(pool, alice)
    assert "Quiet" not in await build_skill_index(pool, alice)


@pytest.mark.asyncio
async def test_failed_create_leaves_nothing_and_content_with_switch_is_atomic(
    pool: asyncpg.Pool, store: SkillStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected failures and an interleaved reader never see half a write."""
    alice = await _user(pool)
    real_append = SkillStore._append_revision

    async def failing_append(self: SkillStore, *args: Any, **kwargs: Any) -> None:
        await real_append(self, *args, **kwargs)
        raise RuntimeError("injected failure after the row is written")

    async with _client(pool, alice) as a:
        # A create that fails after inserting the row leaves no skill at all,
        # enabled or otherwise.
        monkeypatch.setattr(SkillStore, "_append_revision", failing_append)
        with pytest.raises(RuntimeError, match="injected"):
            await a.post(
                "/skills",
                json={"name": "Doomed", "description": "", "content": "x", "enabled": False},
            )
        assert await _visible(pool, alice) == []
        monkeypatch.setattr(SkillStore, "_append_revision", real_append)

        slug = (await _create(a))["id"]
        before = await _dump(pool)

        # A save that fails after the switch was applied rolls both back.
        real_move = SkillStore._move_head

        async def failing_move(self: SkillStore, *args: Any, **kwargs: Any) -> Any:
            await real_move(self, *args, **kwargs)
            raise RuntimeError("injected failure after content and switch")

        monkeypatch.setattr(SkillStore, "_move_head", failing_move)
        with pytest.raises(RuntimeError, match="injected"):
            await a.put(f"/skills/{slug}", json={"content": "v2", "enabled": False})
        assert await _dump(pool) == before
        assert await _visible(pool, alice) == [("# Steps", 1, True)]
        monkeypatch.setattr(SkillStore, "_move_head", real_move)

        # A stale expected revision changes neither content nor the switch.
        stale = await a.put(
            f"/skills/{slug}", json={"content": "v2", "enabled": False, "expected_revision": 9}
        )
        assert stale.status_code == 409 and await _dump(pool) == before

        # Interleave a reader while the save is in flight: it sees the old
        # content and switch, then both new, never one without the other.
        async def no_second_write(*_: Any, **__: Any) -> Any:
            raise AssertionError("enabled must not be a second transaction")

        monkeypatch.setattr(SkillStore, "set_enabled", no_second_write)
        async with pool.acquire() as blocker:
            transaction = blocker.transaction()
            await transaction.start()
            await blocker.execute("SELECT 1 FROM skills WHERE user_id = $1 FOR UPDATE", alice)
            pending = asyncio.create_task(
                a.put(f"/skills/{slug}", json={"content": "v2", "enabled": False})
            )
            await _wait_for_lock_wait(pool)
            assert await _visible(pool, alice) == [("# Steps", 1, True)]
            await transaction.commit()
        saved = await pending
        assert saved.status_code == 200
        assert (saved.json()["content"], saved.json()["enabled"]) == ("v2", False)
        assert await _visible(pool, alice) == [("v2", 2, False)]

    # The switch is the owner's: an unattended actor cannot pass it.
    with pytest.raises(SkillError, match="enabled_invalid"):
        await store.save(alice, slug, body="x", actor="autonomous", enabled=True)
    with pytest.raises(SkillError, match="enabled_invalid"):
        await store.create(alice, name="N", description="", body="", enabled="no")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_routes_refuse_every_write_to_a_shipped_skill(
    pool: asyncpg.Pool, store: SkillStore
) -> None:
    """T3 over HTTP: shipped skills are readable and never writable."""
    alice = await _user(pool)
    await store.publish_shipped("house-style", name="House Style", description="d", body="b")
    before = await _dump(pool)
    async with _client(pool, alice) as a:
        shown = await a.get("/skills/house-style")
        assert shown.status_code == 200
        assert shown.json()["scope"] == "shipped" and shown.json()["source_type"] == "system"
        assert [s["scope"] for s in (await a.get("/skills")).json()["skills"]] == ["shipped"]
        assert (await a.get("/skills/house-style/download")).status_code == 200

        refused = [
            await a.put("/skills/house-style", json={"content": "x"}),
            await a.put("/skills/house-style", json={"enabled": False}),
            await a.patch("/skills/house-style/enabled", json={"enabled": False}),
            await a.patch(
                "/skills/house-style/autonomous-edit", json={"allow_autonomous_edit": True}
            ),
            await a.delete("/skills/house-style"),
        ]
        assert [r.status_code for r in refused] == [403] * 5
        assert {r.json()["detail"] for r in refused} == {"Shipped skills are read-only"}
    assert await _dump(pool) == before


@pytest.mark.asyncio
async def test_revision_list_and_revert_routes(pool: asyncpg.Pool, store: SkillStore) -> None:
    """Step 3, T6 over HTTP: history is the owner's, and a revert is a new revision."""
    alice, bob = await _user(pool), await _user(pool)
    await store.publish_shipped("house-style", name="House Style", description="d", body="b")
    async with _client(pool, alice) as a, _client(pool, bob) as b:
        slug = (await _create(a))["id"]
        await a.put(f"/skills/{slug}", json={"name": "Deploy v2", "content": "second"})
        await store.set_autonomous_edit(alice, slug, True)
        await store.patch(alice, slug, "second", "third", actor="autonomous")

        listed = await a.get(f"/skills/{slug}/revisions")
        assert listed.status_code == 200
        revisions = listed.json()["revisions"]
        assert [(r["revision"], r["actor"], r["name"], r["content"]) for r in revisions] == [
            (3, "autonomous", "Deploy v2", "third"),
            (2, "user", "Deploy v2", "second"),
            (1, "user", "Deploy Checklist", "# Steps"),
        ]
        assert set(revisions[0]) == {
            "revision", "name", "description", "content", "actor", "created_at"
        }  # fmt: skip

        # Another account, a guessed slug and a shipped skill have no history here.
        assert (await b.get(f"/skills/{slug}/revisions")).status_code == 404
        assert (await a.get("/skills/house-style/revisions")).status_code == 404
        assert (await b.post(f"/skills/{slug}/revert", json={"revision": 1})).status_code == 404
        shipped = await a.post("/skills/house-style/revert", json={"revision": 1})
        assert shipped.status_code == 403

        before = await _dump(pool)
        stale = await a.post(f"/skills/{slug}/revert", json={"revision": 1, "expected_revision": 2})
        assert stale.status_code == 409
        missing = await a.post(f"/skills/{slug}/revert", json={"revision": 99})
        assert missing.status_code == 404 and missing.json()["detail"] == "Revision not found"
        assert (await a.post(f"/skills/{slug}/revert", json={"revision": 0})).status_code == 422
        assert await _dump(pool) == before

        reverted = await a.post(
            f"/skills/{slug}/revert", json={"revision": 1, "expected_revision": 3}
        )
        assert reverted.status_code == 200
        body = reverted.json()
        # The old content comes back as a new revision; history only grows.
        assert (body["revision"], body["name"], body["content"]) == (
            4,
            "Deploy Checklist",
            "# Steps",
        )
        assert body["id"] == slug
        after = (await a.get(f"/skills/{slug}/revisions")).json()["revisions"]
        assert [(r["revision"], r["actor"]) for r in after] == [
            (4, "user"),
            (3, "autonomous"),
            (2, "user"),
            (1, "user"),
        ]


@pytest.mark.asyncio
async def test_upgrade_endpoints_are_removed(pool: asyncpg.Pool) -> None:
    alice = await _user(pool)
    async with _client(pool, alice) as a:
        slug = (await _create(a))["id"]
        pending = await a.post(f"/skills/{slug}/pending-update", json={"action": "apply"})
        assert pending.status_code in (404, 405)
        assert (await a.post("/skills/admin/sync")).status_code in (404, 405)


@pytest.mark.asyncio
async def test_routes_fail_closed_without_a_database() -> None:
    async with _client(None, uuid.uuid4()) as a:
        assert (await a.get("/skills")).status_code == 503
        assert (await _create_raw(a, "Anything")).status_code == 503


@pytest.mark.asyncio
async def test_import_ignores_authority_and_round_trips(
    pool: asyncpg.Pool,
) -> None:
    """T8: authority in a file is ignored; export then import keeps the content."""
    alice, bob = await _user(pool), await _user(pool)
    forged = (
        "---\n"
        "name: Forged Authority\n"
        "description: Claims to be a system skill\n"
        "source_type: system\n"
        "allow_autonomous_edit: true\n"
        f"user_id: {bob}\n"
        "scope: shipped\n"
        "---\n"
        "Body line one\n\nBody line two\n"
    )
    async with _client(pool, alice) as a, _client(pool, bob) as b:
        uploaded = await a.post(
            "/skills/upload", files={"file": ("../../etc/forged.md", forged, "text/markdown")}
        )
        assert uploaded.status_code == 201, uploaded.text
        skill = uploaded.json()
        assert skill["id"] == "forged-authority"
        assert skill["source_type"] == "imported"
        assert skill["scope"] == "account"
        assert skill["allow_autonomous_edit"] is False
        assert await pool.fetchval("SELECT user_id FROM skills") == alice
        assert (await b.get("/skills")).json() == {"skills": []}

        # Same name again: refused, then a new revision with overwrite.
        again = {"file": ("forged.md", forged.replace("line two", "line three"), "text/markdown")}
        assert (await a.post("/skills/upload", files=again)).status_code == 409
        replaced = await a.post("/skills/upload", files=again, data={"overwrite": "true"})
        assert replaced.status_code == 201
        assert replaced.json()["revision"] == 2
        assert replaced.json()["source_type"] == "imported"
        assert "line three" in replaced.json()["content"]

        exported = await a.get("/skills/forged-authority/download")
        assert (
            exported.headers["content-disposition"] == 'attachment; filename="forged-authority.md"'
        )
        reimported = await b.post(
            "/skills/upload", files={"file": ("copy.md", exported.text, "text/markdown")}
        )
        assert reimported.status_code == 201, reimported.text
        for field in ("name", "description", "content", "enabled"):
            assert reimported.json()[field] == replaced.json()[field], field

        oversized = "---\nname: Big\ndescription: d\n---\n" + "x" * MAX_SKILL_FILE_CHARS
        too_big = await a.post(
            "/skills/upload", files={"file": ("big.md", oversized, "text/markdown")}
        )
        assert too_big.status_code == 400
        assert too_big.json()["detail"] == "The skill file is too large"
        for bad_name, bad_text in (
            ("notes.txt", forged),
            ("empty.md", "   \n"),
            ("untitled.md", "no heading and no frontmatter"),
            ("nameless.md", "---\ndescription: d\n---\nbody\n"),
            ("bodyless.md", "---\nname: N\ndescription: d\n---\n\n"),
            ("flag.md", "---\nname: N\ndescription: d\nenabled: maybe\n---\nbody\n"),
        ):
            response = await a.post(
                "/skills/upload", files={"file": (bad_name, bad_text, "text/markdown")}
            )
            assert response.status_code == 400, bad_name


def test_format_bounds_frontmatter_and_renders_one_line_fields() -> None:
    with pytest.raises(SkillError, match="frontmatter_too_long"):
        parse_skill_markdown("a.md", "---\n" + "k: v\n" * 65 + "---\nbody\n")
    rendered = render_skill_markdown(
        {
            "name": "Two\nLines",
            "description": "a\n---\nb",
            "body": "text",
            "revision": 3,
            "enabled": False,
        }
    )
    parsed = parse_skill_markdown("a.md", rendered)
    assert (parsed.name, parsed.description, parsed.body, parsed.enabled) == (
        "Two Lines",
        "a --- b",
        "text",
        False,
    )
    assert "version: 3" in rendered


def test_cutover_modules_touch_no_files() -> None:
    """T7: no reader or writer of skills has a filesystem path left."""
    for module in (skills_routes, skill_manage_module, skill_index, skill_format):
        source = Path(str(module.__file__)).read_text()
        for needle in ("skills_store", "SKILLS_DIR", "read_text", "write_text", "open("):
            assert needle not in source, (module.__name__, needle)


# --------------------------------------------------------------------------
# Shipped-skill loader
# --------------------------------------------------------------------------


def _release(root: Path, name: str, version: int, skills: dict[str, str]) -> Path:
    """One application image's shipped-skills directory."""
    directory = root / name
    directory.mkdir()
    (directory / "README.md").write_text("not a skill", encoding="utf-8")
    digests: dict[str, str] = {}
    for slug, body in skills.items():
        (directory / slug).mkdir()
        title = slug.replace("-", " ").title()
        data = f"---\nname: {title}\ndescription: {title} description\n---\n{body}\n".encode()
        (directory / slug / "SKILL.md").write_bytes(data)
        digests[slug] = skill_file_digest(data)
    _write_manifest(directory, version, digests)
    return directory


def _write_manifest(directory: Path, version: Any, digests: Any) -> None:
    (directory / MANIFEST_FILE).write_text(
        json.dumps({"version": version, "skills": digests}), encoding="utf-8"
    )


def _redigest(directory: Path) -> dict[str, str]:
    """Digests of whatever skill files the directory holds now."""
    return {
        entry.name: skill_file_digest((entry / "SKILL.md").read_bytes())
        for entry in directory.iterdir()
        if (entry / "SKILL.md").is_file()
    }


async def _shipped_state(store: SkillStore) -> dict[str, tuple[int, str]]:
    return {s["slug"]: (s["revision"], s["body"]) for s in await store.list_shipped()}


@pytest.mark.asyncio
async def test_loader_applies_a_newer_catalogue_whole(
    pool: asyncpg.Pool, store: SkillStore, tmp_path: Path
) -> None:
    alice = await _user(pool)
    mine = await store.create(alice, name="House Style", description="mine", body="my own")
    v1 = _release(tmp_path, "v1", 1, {"house-style": "shipped v1", "citations": "cite sources"})

    first = await sync_shipped_skills(store, v1)
    assert (first.applied, first.version) == (True, 1)
    assert (first.published, first.unchanged, first.removed) == (
        ["citations", "house-style"],
        [],
        [],
    )
    assert await store.shipped_catalogue_version() == 1
    assert await _shipped_state(store) == {
        "citations": (1, "cite sources"),
        "house-style": (1, "shipped v1"),
    }
    shipped = await pool.fetch("SELECT source_type, user_id FROM skills WHERE user_id IS NULL")
    assert {(row["source_type"], row["user_id"]) for row in shipped} == {("system", None)}
    # Every account sees shipped skills; its own same-named skill shadows one.
    index = await build_skill_index(pool, alice)
    assert "- Citations: Citations description [system]" in index
    assert "- House Style: mine [manual]" in index and "House Style description" not in index

    # The same release starting again writes nothing.
    before = await _dump(pool)
    again = await sync_shipped_skills(store, v1)
    assert (again.applied, again.reason) == (False, "not_newer")
    assert await _dump(pool) == before

    # A newer release: one skill changed, one removed, one added, one kept.
    v2 = _release(tmp_path, "v2", 2, {"house-style": "shipped v2", "glossary": "terms"})
    second = await sync_shipped_skills(store, v2)
    assert (second.applied, second.version) == (True, 2)
    assert (second.published, second.unchanged, second.removed) == (
        ["glossary", "house-style"],
        [],
        ["citations"],
    )
    assert await _shipped_state(store) == {
        "glossary": (1, "terms"),
        "house-style": (2, "shipped v2"),
    }
    # The account's own skill with the same slug is never touched.
    assert (await store.get(alice, "house-style"))["body"] == "my own"
    assert (await store.get(alice, "house-style"))["id"] == mine["id"]


@pytest.mark.asyncio
async def test_an_older_release_cannot_delete_or_roll_back_newer_shipped_skills(
    pool: asyncpg.Pool, store: SkillStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A deploy and a rollback: old instances start after the new release applied."""
    empty_old = _release(tmp_path, "empty-old", 0, {})
    v1 = _release(tmp_path, "v1", 1, {"house-style": "shipped v1", "citations": "cite sources"})
    v2 = _release(tmp_path, "v2", 2, {"house-style": "shipped v2", "glossary": "terms"})
    no_version = tmp_path / "unversioned"
    (no_version / "stray").mkdir(parents=True)

    await sync_shipped_skills(store, v2)
    newest = await _dump(pool, ("skills", "skill_revisions", "shipped_skill_catalogue"))

    # Every older or unversioned image, started or restarted in any order.
    for directory in (empty_old, v1, no_version, tmp_path / "absent", v1, empty_old):
        outcome = await sync_shipped_skills(store, directory)
        assert outcome.applied is False
        assert (outcome.published, outcome.removed) == ([], [])
    assert await _dump(pool, ("skills", "skill_revisions", "shipped_skill_catalogue")) == newest
    assert await store.shipped_catalogue_version() == 2

    # The store refuses a stale version itself, not only the loader's pre-check.
    stale = await store.apply_shipped_catalogue(
        1, [{"slug": "house-style", "name": "N", "description": "d", "body": "old"}]
    )
    assert stale["applied"] is False and stale["version"] == 2
    same = await store.apply_shipped_catalogue(2, [])
    assert same["applied"] is False
    assert await _dump(pool, ("skills", "skill_revisions", "shipped_skill_catalogue")) == newest

    # Content changed without a new version is not applied, and says so.
    forgot = _release(tmp_path, "forgot", 2, {"house-style": "edited in place"})
    with caplog.at_level("WARNING"):
        outcome = await sync_shipped_skills(store, forgot)
    assert outcome.applied is False
    assert "without a new catalogue version" in caplog.text
    assert await _dump(pool, ("skills", "skill_revisions", "shipped_skill_catalogue")) == newest


@pytest.mark.asyncio
async def test_releases_starting_together_end_on_the_newest_catalogue(
    pool: asyncpg.Pool, tmp_path: Path
) -> None:
    """Mixed-version startup in every interleaving ends the same way."""
    old = _release(tmp_path, "old", 0, {})
    v1 = _release(tmp_path, "v1", 1, {"house-style": "shipped v1", "citations": "cite sources"})
    v2 = _release(tmp_path, "v2", 2, {"house-style": "shipped v2", "glossary": "terms"})

    for round_number in range(4):
        await pool.execute("DELETE FROM skills")
        await pool.execute("DELETE FROM shipped_skill_catalogue")
        instances = [old, v1, v2, v1, v2, old, v2, v1]
        if round_number % 2:
            instances.reverse()
        outcomes = await asyncio.gather(
            *(sync_shipped_skills(SkillStore(pool, KEY), directory) for directory in instances)
        )
        state = await _shipped_state(SkillStore(pool, KEY))
        assert set(state) == {"house-style", "glossary"}, round_number
        assert state["glossary"] == (1, "terms")
        assert state["house-style"][1] == "shipped v2"
        assert await pool.fetchval("SELECT version FROM shipped_skill_catalogue") == 2
        assert await pool.fetchval("SELECT count(*) FROM shipped_skill_catalogue") == 1
        # Exactly one v2 instance applied it; v1 applied at most once, before v2.
        applied = [outcome.version for outcome in outcomes if outcome.applied]
        assert applied.count(2) == 1 and applied.count(1) <= 1, applied
        assert state["house-style"][0] == len(applied)

    # Forced order with an observed lock wait: the older release holds the
    # catalogue row while the newer one waits behind it, then both apply.
    await pool.execute("DELETE FROM skills")
    await pool.execute("DELETE FROM shipped_skill_catalogue")
    store = SkillStore(pool, KEY)
    async with pool.acquire() as blocker:
        transaction = blocker.transaction()
        await transaction.start()
        await blocker.execute("INSERT INTO shipped_skill_catalogue (version) VALUES (0)")
        newer = asyncio.create_task(sync_shipped_skills(store, v2))
        await _wait_for_lock_wait(pool)
        older = asyncio.create_task(sync_shipped_skills(store, v1))
        await asyncio.sleep(0.05)
        await transaction.commit()
    await asyncio.gather(newer, older)
    assert (await _shipped_state(store))["house-style"][1] == "shipped v2"
    assert await store.shipped_catalogue_version() == 2


@pytest.mark.asyncio
async def test_a_catalogue_is_applied_whole_or_not_at_all(
    pool: asyncpg.Pool,
    store: SkillStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    v1 = _release(tmp_path, "v1", 1, {"house-style": "shipped v1", "citations": "cite sources"})
    await sync_shipped_skills(store, v1)
    tables = ("skills", "skill_revisions", "shipped_skill_catalogue")
    before = await _dump(pool, tables)

    # A newer release whose manifest matches its files, but one skill does not
    # parse, one directory has no file and one file is oversized: nothing of
    # that release is applied.
    secret = "vortexplum body that must not be logged"
    broken = _release(tmp_path, "broken", 2, {"house-style": "shipped v2", "glossary": "terms"})
    (broken / "house-style" / "SKILL.md").write_text("no frontmatter, no title", "utf-8")
    (broken / "empty-dir").mkdir()
    (broken / "oversized").mkdir()
    (broken / "oversized" / "SKILL.md").write_text(
        f"---\nname: Big\ndescription: d\n---\n{secret}" + "x" * MAX_SKILL_FILE_CHARS, "utf-8"
    )
    _write_manifest(broken, 2, {**_redigest(broken), "empty-dir": "0" * 64})
    with caplog.at_level("INFO"):
        partial = await sync_shipped_skills(store, broken)
    assert (partial.applied, partial.reason) == (False, "incomplete_catalogue")
    assert partial.failed == ["empty-dir", "house-style", "oversized"]
    assert await _dump(pool, tables) == before
    assert "vortexplum" not in caplog.text

    # A failure in the middle of applying a good release rolls all of it back,
    # including the removal and the version.
    v2 = _release(tmp_path, "v2", 2, {"house-style": "shipped v2", "glossary": "terms"})
    real_append = SkillStore._append_revision
    calls = 0

    async def fail_on_second(self: SkillStore, *args: Any, **kwargs: Any) -> None:
        nonlocal calls
        await real_append(self, *args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("injected failure mid-catalogue")

    monkeypatch.setattr(SkillStore, "_append_revision", fail_on_second)
    with pytest.raises(RuntimeError, match="injected"):
        await sync_shipped_skills(store, v2)
    assert await _dump(pool, tables) == before
    assert await store.shipped_catalogue_version() == 1
    monkeypatch.setattr(SkillStore, "_append_revision", real_append)

    # Invalid input never reaches the database.
    for version, skills in (
        (0, []),
        (-1, []),
        (True, []),
        (3, [{"slug": "a", "name": "A", "description": "", "body": ""}] * 2),
        (3, [{"slug": "Bad Slug", "name": "A", "description": "", "body": ""}]),
    ):
        with pytest.raises(SkillError):
            await store.apply_shipped_catalogue(version, skills)  # type: ignore[arg-type]
    assert await _dump(pool, tables) == before

    healed = await sync_shipped_skills(store, v2)
    assert (healed.applied, healed.removed) == (True, ["citations"])


@pytest.mark.asyncio
async def test_startup_publication_never_raises(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from orchestrator import main
    from orchestrator.memory.encryption import EncryptionKeyMissing

    # The repository's own directory is version 0 with no skill: startup
    # writes nothing, records no catalogue and does not bind the store's key.
    await main._publish_shipped_skills(pool)
    for table in ("skills", "skill_store_key", "shipped_skill_catalogue"):
        assert await pool.fetchval(f"SELECT count(*) FROM {table}") == 0, table

    # A database without migration 070 publishes nothing and does not raise.
    await pool.execute("ALTER TABLE shipped_skill_catalogue RENAME TO catalogue_away")
    monkeypatch.setattr(
        "orchestrator.services.shipped_skills.read_manifest", lambda _: Manifest(1, {})
    )
    with caplog.at_level("WARNING"):
        await main._publish_shipped_skills(pool)
    await pool.execute("ALTER TABLE catalogue_away RENAME TO shipped_skill_catalogue")
    assert "Shipped skills not published" in caplog.text
    assert await pool.fetchval("SELECT count(*) FROM skills") == 0

    def no_key(*_: Any, **__: Any) -> SkillStore:
        raise EncryptionKeyMissing("DAEMON_ENCRYPTION_KEY is required")

    monkeypatch.setattr("orchestrator.services.skills.create_skill_store", no_key)
    with caplog.at_level("WARNING"):
        await main._publish_shipped_skills(pool)
        await main._publish_shipped_skills(None)
    assert "no content encryption key" in caplog.text


def test_no_skill_ships_before_accounts_can_switch_one_off() -> None:
    """Owner decision O2: the per-account switch comes with the first shipped skill."""
    shipped = [entry.name for entry in SHIPPED_SKILLS_DIR.iterdir() if entry.is_dir()]
    assert shipped == [], "add the per-account disable setting (O2) with the first shipped skill"
    assert (SHIPPED_SKILLS_DIR / "README.md").is_file()
    # Version 0 means "no catalogue": it can never outrank a database.
    assert read_manifest(SHIPPED_SKILLS_DIR) == Manifest(0, {})


def test_repository_shipped_skills_match_their_manifest() -> None:
    """The directory that ships must be one the loader would accept."""
    manifest = read_manifest(SHIPPED_SKILLS_DIR)
    assert manifest is not None, f"{MANIFEST_FILE} is missing"
    expected = {
        entry.name: skill_file_digest((entry / "SKILL.md").read_bytes())
        for entry in SHIPPED_SKILLS_DIR.iterdir()
        if entry.is_dir()
    }
    assert manifest.skills == expected, f"set 'skills' in {MANIFEST_FILE} to {expected}"
    skills, incomplete, mismatched = read_catalogue(SHIPPED_SKILLS_DIR, manifest)
    assert (incomplete, mismatched) == ([], [])
    assert (manifest.version > 0) or not skills


def test_manifest_is_strict(tmp_path: Path) -> None:
    assert read_manifest(tmp_path) is None
    good = "a" * 64
    for text in (
        "",
        "3",
        "[]",
        '{"version": 1}',
        '{"version": 1, "skills": {}, "extra": 1}',
        '{"version": "1", "skills": {}}',
        '{"version": true, "skills": {}}',
        '{"version": -1, "skills": {}}',
        '{"version": 1.0, "skills": {}}',
        '{"version": 1, "skills": []}',
        '{"version": 1, "skills": {"Bad Name": "%s"}}' % good,
        '{"version": 1, "skills": {"a": "%s"}}' % good.upper(),
        '{"version": 1, "skills": {"a": "abc"}}',
        '{"version": 1, "skills": {"a": 5}}',
        '{"version": 1, "skills": {"a": "%s", "a": "%s"}}' % (good, good),
        '{"version": 1, "version": 2, "skills": {}}',
        '{"version": 0, "skills": {"a": "%s"}}' % good,
        '{"version": 1, "skills": {}}' + " " * 70000,
    ):
        (tmp_path / MANIFEST_FILE).write_text(text, encoding="utf-8")
        with pytest.raises(ManifestError):
            read_manifest(tmp_path)
    (tmp_path / MANIFEST_FILE).write_bytes(b"\xff\xfe")
    with pytest.raises(ManifestError):
        read_manifest(tmp_path)
    (tmp_path / MANIFEST_FILE).write_text('{"version": 4, "skills": {"a-b": "%s"}}' % good)
    assert read_manifest(tmp_path) == Manifest(4, {"a-b": good})


@pytest.mark.asyncio
async def test_an_incomplete_package_cannot_remove_shipped_skills(
    pool: asyncpg.Pool, store: SkillStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Absence is never read as removal: the manifest must say so."""
    v1 = _release(tmp_path, "v1", 1, {"house-style": "shipped v1", "citations": "cite sources"})
    await sync_shipped_skills(store, v1)
    tables = ("skills", "skill_revisions", "shipped_skill_catalogue")
    before = await _dump(pool, tables)
    full = {"house-style": "shipped v2", "citations": "cite sources"}

    def package(name: str) -> Path:
        return _release(tmp_path, name, 2, full)

    # A newer manifest whose skill directories were all left out of the image.
    only_manifest = package("only-manifest")
    for slug in full:
        (only_manifest / slug / "SKILL.md").unlink()
        (only_manifest / slug).rmdir()
    # One directory left out; one file left out; one extra directory.
    one_missing = package("one-missing")
    (one_missing / "citations" / "SKILL.md").unlink()
    (one_missing / "citations").rmdir()
    file_missing = package("file-missing")
    (file_missing / "citations" / "SKILL.md").unlink()
    extra = package("extra")
    (extra / "glossary").mkdir()
    (extra / "glossary" / "SKILL.md").write_text("---\nname: G\ndescription: d\n---\nb\n")
    # A truncated file, and an altered one of the same length.
    truncated = package("truncated")
    data = (truncated / "house-style" / "SKILL.md").read_bytes()
    (truncated / "house-style" / "SKILL.md").write_bytes(data[:-4])
    altered = package("altered")
    (altered / "house-style" / "SKILL.md").write_bytes(data.replace(b"v2", b"v9"))
    # The old format, a bumped manifest alone, and unusable manifests.
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "VERSION").write_text("2\n")
    bare = tmp_path / "bare"
    bare.mkdir()
    _write_manifest(bare, 2, full | {"house-style": "0" * 64, "citations": "1" * 64})
    garbled = package("garbled")
    (garbled / MANIFEST_FILE).write_text('{"version": 2', encoding="utf-8")
    lying_zero = package("lying-zero")
    _write_manifest(lying_zero, 0, _redigest(lying_zero))

    expected = {
        "only-manifest": ("incomplete_catalogue", ["citations", "house-style"]),
        "one-missing": ("incomplete_catalogue", ["citations"]),
        "file-missing": ("incomplete_catalogue", ["citations"]),
        "extra": ("incomplete_catalogue", ["glossary"]),
        "truncated": ("checksum_mismatch", ["house-style"]),
        "altered": ("checksum_mismatch", ["house-style"]),
        "legacy": ("no_catalogue", []),
        "bare": ("incomplete_catalogue", ["citations", "house-style"]),
        "garbled": ("invalid_manifest", []),
        "lying-zero": ("invalid_manifest", []),
    }
    for name, (reason, failed) in expected.items():
        caplog.clear()
        with caplog.at_level("INFO"):
            outcome = await sync_shipped_skills(store, tmp_path / name)
        assert (outcome.applied, outcome.reason, outcome.failed) == (False, reason, failed), name
        assert (outcome.published, outcome.removed) == ([], []), name
        assert await _dump(pool, tables) == before, name
        assert await store.shipped_catalogue_version() == 1, name
        assert "shipped v" not in caplog.text and "cite sources" not in caplog.text, name

    # The refused version was not consumed: the complete v2 still applies.
    healed = await sync_shipped_skills(store, package("complete"))
    assert (healed.applied, healed.published, healed.removed) == (True, ["house-style"], [])

    # Removing every shipped skill is possible only by declaring it.
    remove_all = _release(tmp_path, "remove-all", 3, {})
    assert read_manifest(remove_all) == Manifest(3, {})
    cleared = await sync_shipped_skills(store, remove_all)
    assert (cleared.applied, cleared.removed) == (True, ["citations", "house-style"])
    assert await _shipped_state(store) == {}
    assert await store.shipped_catalogue_version() == 3


# --------------------------------------------------------------------------
# Admin consolidation audit route
# --------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _audit_client(is_admin: bool, memory_store: Any) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(skills_routes.router)
    app.state.app_state = SimpleNamespace(db_pool=None, memory_store=memory_store)
    app.dependency_overrides[require_admin_or_device_auth] = lambda: AdminOrDeviceAuth(
        authenticated_device=AuthenticatedDevice(
            user_id=uuid.uuid4(), device_id=uuid.uuid4(), session_id=uuid.uuid4()
        ),
        is_admin=is_admin,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_admin_consolidation_audit_returns_ids_and_codes(pool: asyncpg.Pool) -> None:
    alice = await _user(pool)
    memory_store = _NudgeStore(pool)
    skill_id = uuid.uuid4()
    await memory_store.log_consolidation_nudge_action(
        user_id=alice,
        run_id=uuid.uuid4(),
        action_type="delete",
        skill_id=skill_id,
        target_skill_id=None,
        reason="delete_failed",
        similarity=None,
        status="failed",
        skill_use_count=3,
    )
    async with _audit_client(True, memory_store) as admin:
        response = await admin.get(
            "/skills/admin/consolidation-audit",
            params={"user_id": str(alice), "action_type": "delete", "status": "failed"},
        )
    assert response.status_code == 200
    (action,) = response.json()["actions"]
    assert action["skill_id"] == str(skill_id)
    assert action["target_skill_id"] is None
    assert (action["reason"], action["status"]) == ("delete_failed", "failed")
    assert "skill_name" not in action and "skill_description" not in action

    async with _audit_client(False, memory_store) as device:
        assert (await device.get("/skills/admin/consolidation-audit")).status_code == 403
    async with _audit_client(True, None) as admin:
        assert (await admin.get("/skills/admin/consolidation-audit")).status_code == 503


# --------------------------------------------------------------------------
# Prompt index
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_is_scoped_to_the_account_and_shipped_skills(
    pool: asyncpg.Pool, store: SkillStore
) -> None:
    alice, bob = await _user(pool), await _user(pool)
    await store.create(alice, name="Alice Skill", description="a", body="x")
    hidden = await store.create(alice, name="Alice Disabled", description="a", body="x")
    await store.set_enabled(alice, hidden["slug"], False)
    await store.create(bob, name="Bob Skill", description="b", body="x")
    await store.publish_shipped("shared", name="Shipped Skill", description="s", body="x")
    await store.publish_shipped("alice-skill", name="Shadowed Shipped", description="s", body="x")

    index = await build_skill_index(pool, alice)
    assert index.startswith("Skill Index (L0):")
    assert "- Alice Skill: a [manual]" in index
    assert "- Shipped Skill: s [system]" in index
    for absent in ("Bob Skill", "Alice Disabled", "Shadowed Shipped"):
        assert absent not in index
    assert "Bob Skill" in await build_skill_index(pool, str(bob))


@pytest.mark.asyncio
async def test_index_is_empty_without_an_account_or_a_working_database(
    pool: asyncpg.Pool, store: SkillStore, caplog: pytest.LogCaptureFixture
) -> None:
    """T7: a database failure gives an empty index, logged, with no fallback."""
    alice = await _user(pool)
    await store.create(alice, name="Alice Skill", description="a", body="x")
    assert await build_skill_index(pool, None) == ""
    assert await build_skill_index(pool, "not-a-uuid") == ""
    assert await build_skill_index(None, alice) == ""

    class _Broken:
        def acquire(self) -> Any:
            raise ConnectionError("database down")

    with caplog.at_level("ERROR"):
        assert await build_skill_index(_Broken(), alice) == ""
    assert "Skill index unavailable" in caplog.text
    # A store under another key is a failure too, never an empty-looking success.
    other = SkillStore(pool, Fernet.generate_key().decode())
    assert await build_skill_index(pool, alice, store=other) == ""


# --------------------------------------------------------------------------
# skill_manage tool
# --------------------------------------------------------------------------


async def _call(tool: SkillManageTool, **kwargs: Any) -> Any:
    return json.loads(await tool.execute(**kwargs))


@pytest.mark.asyncio
async def test_tool_is_bound_to_one_account(pool: asyncpg.Pool, store: SkillStore) -> None:
    """T1 for the tool: list, view, patch and delete reach only the caller's skills."""
    alice, bob = await _user(pool), await _user(pool)
    a_tool, b_tool = SkillManageTool(store, alice), SkillManageTool(store, bob)

    created = await _call(a_tool, action="create", name="Runbook", description="d", content="one")
    assert created == {
        "skill_id": "runbook",
        "name": "Runbook",
        "description": "d",
        "source_type": "autonomous",
        "created": True,
    }
    assert (await store.get(alice, "runbook"))["allow_autonomous_edit"] is True

    assert await _call(b_tool, action="list") == []
    for action in ("view", "delete"):
        assert await _call(b_tool, action=action, skill_id="runbook") == {
            "error": "Skill not found"
        }
    assert await _call(
        b_tool, action="patch", skill_id="runbook", old_text="one", new_text="x"
    ) == {"error": "Skill not found"}

    listed = await _call(a_tool, action="list")
    assert [s["id"] for s in listed] == ["runbook"] and "content" not in listed[0]
    viewed = await _call(a_tool, action="view", skill_id="runbook")
    assert viewed["content"] == "one"
    assert (await store.get(alice, "runbook"))["use_count"] == 1

    patched = await _call(
        a_tool, action="patch", skill_id="runbook", old_text="one", new_text="two"
    )
    assert patched["patched"] is True
    current = await store.get(alice, "runbook")
    assert current["body"] == "two" and current["revision"] == 2
    assert (await store.list_revisions(alice, "runbook"))[0]["actor"] == "autonomous"
    missing = await _call(a_tool, action="patch", skill_id="runbook", old_text="nope", new_text="")
    assert "old_text not found" in missing["error"]

    assert await _call(a_tool, action="delete", skill_id="runbook") == {
        "skill_id": "runbook",
        "deleted": True,
    }
    assert await _call(a_tool, action="list") == []


@pytest.mark.asyncio
async def test_tool_respects_the_grant_and_shipped_skills(
    pool: asyncpg.Pool, store: SkillStore
) -> None:
    """T3 for the tool, and the #697 grant boundary."""
    alice = await _user(pool)
    tool = SkillManageTool(store, alice)
    await store.create(alice, name="Manual Skill", description="d", body="keep me")
    await store.publish_shipped("house-style", name="House Style", description="d", body="shipped")
    before = await _dump(pool)

    for kwargs in (
        {"action": "patch", "skill_id": "manual-skill", "old_text": "keep", "new_text": "lose"},
        {"action": "delete", "skill_id": "manual-skill"},
        {"action": "patch", "skill_id": "house-style", "old_text": "shipped", "new_text": "x"},
        {"action": "delete", "skill_id": "house-style"},
    ):
        refused = await _call(tool, **kwargs)
        assert "protected" in refused["error"], kwargs
    # Authority is never an argument.
    for forged in ({"allow_autonomous_edit": True}, {"caller_autonomous": False}):
        refused = await _call(
            tool, action="patch", skill_id="manual-skill", old_text="keep", new_text="x", **forged
        )
        assert "Authority is not an input" in refused["error"]
    refused = await _call(tool, action="create", name="X", source_type="system")
    assert "source_type" in refused["error"]
    assert await _dump(pool) == before

    # A shipped skill can be viewed; its usage is not counted per account.
    assert (await _call(tool, action="view", skill_id="house-style"))["scope"] == "shipped"

    await store.set_autonomous_edit(alice, "manual-skill", True)
    assert (
        await _call(tool, action="patch", skill_id="manual-skill", old_text="keep", new_text="kept")
    )["patched"] is True
    await store.set_autonomous_edit(alice, "manual-skill", False)
    refused = await _call(
        tool, action="patch", skill_id="manual-skill", old_text="kept", new_text="x"
    )
    assert "protected" in refused["error"]
    assert (await store.get(alice, "manual-skill"))["body"] == "kept me"


@pytest.mark.asyncio
async def test_tool_refuses_without_an_account_or_store(store: SkillStore) -> None:
    for tool in (SkillManageTool(None, uuid.uuid4()), SkillManageTool(store, None)):
        assert "unavailable" in (await _call(tool, action="list"))["error"]


# --------------------------------------------------------------------------
# Evaluator
# --------------------------------------------------------------------------

_SKILL_MARKDOWN = f"""# {MARKER_NAME}

## Purpose
{MARKER_DESCRIPTION}.

## When To Use
{MARKER_TRIGGER}.

## Workflow
1. Export the ledger and {MARKER_BODY}.

## Verification
Totals match.

## Guardrails
Never post without review.
"""


class _Conversations:
    def __init__(self) -> None:
        self.assistant_id = uuid.uuid4()

    async def get_messages(
        self, conversation_id: uuid.UUID, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return [
            {"id": uuid.uuid4(), "role": "user", "content": f"Please {MARKER_BODY}"},
            {
                "id": self.assistant_id,
                "role": "assistant",
                "content": "Done.",
                "status": "complete",
                "tool_calls": [],
                "tool_results": [],
            },
        ]

    async def get_conversation(self, conversation_id: uuid.UUID) -> dict[str, Any] | None:
        return None


def _completion(*payloads: dict[str, Any]) -> Any:
    queue = list(payloads)

    async def complete(**_: Any) -> Any:
        return SimpleNamespace(choices=[{"message": {"content": json.dumps(queue.pop(0))}}])

    return complete


_DRAFT = {
    "name": MARKER_NAME,
    "description": MARKER_DESCRIPTION,
    "trigger_conditions": MARKER_TRIGGER,
    "skill_markdown": _SKILL_MARKDOWN,
}


async def _embed_query(text: str) -> list[float]:
    return _vector(text)


async def _evaluate(
    pool: asyncpg.Pool, user_id: uuid.UUID, *payloads: dict[str, Any], tool_calls: int = 6
) -> Any:
    conversations = _Conversations()
    evaluator = SkillEvaluator(
        store=conversations,
        db_pool=pool,
        completion_callable=_completion(*payloads),
        query_embedder=_embed_query,
    )
    return await evaluator.evaluate_completed_turn(
        SkillEvaluationRequest(
            user_id=user_id,
            conversation_id=uuid.uuid4(),
            assistant_message_id=conversations.assistant_id,
            tool_call_count=tool_calls,
        )
    )


@pytest.mark.asyncio
async def test_evaluator_writes_only_into_the_evaluated_account(
    pool: asyncpg.Pool, store: SkillStore
) -> None:
    """T2: output of A's turn is stored under A; B's and shipped skills never match."""
    alice, bob = await _user(pool), await _user(pool)
    # Bob and the application already hold a skill with the same name and vector.
    theirs = await store.create(
        bob, name=MARKER_NAME, description="bob's", body="bob's body", actor="autonomous"
    )
    await store.store_embedding(uuid.UUID(theirs["id"]), 1, _vector(MARKER_NAME))
    shipped = await store.publish_shipped(
        "zephyrquill-ledger-reconciliation", name=MARKER_NAME, description="s", body="shipped"
    )
    await store.store_embedding(uuid.UUID(shipped["id"]), 1, _vector(MARKER_NAME))

    created = await _evaluate(pool, alice, _DRAFT)
    assert created.classification == "created"
    assert created.created_skill_id == "zephyrquill-ledger-reconciliation"
    mine = await store.get(alice, created.created_skill_id)
    assert mine["scope"] == "account" and mine["source_type"] == "autonomous"
    assert mine["trigger_conditions"] == MARKER_TRIGGER
    assert mine["complexity_origin"] == 6 and mine["has_current_embedding"] is True
    assert await pool.fetchval("SELECT count(*) FROM skills WHERE user_id = $1", alice) == 1
    assert (await store.get(bob, theirs["slug"]))["body"] == "bob's body"
    assert (await store.list_revisions(bob, theirs["slug"]))[0]["revision"] == 1

    # A second turn in A's account refines A's skill, as a revision.
    patched = await _evaluate(
        pool,
        alice,
        _DRAFT,
        {
            "decision": "PATCH",
            "reason": "tighter wording",
            "trigger_conditions": "",
            "old_text": "Totals match.",
            "new_text": "Totals match the vortexplum export.",
        },
        tool_calls=9,
    )
    assert patched.classification == "patched"
    assert patched.patched_skill_id == created.created_skill_id
    assert patched.complexity_origin == 9
    mine = await store.get(alice, created.created_skill_id)
    assert mine["revision"] == 2 and "vortexplum export" in mine["body"]
    assert (await store.list_revisions(alice, mine["slug"]))[0]["actor"] == "autonomous"
    assert (await store.get(bob, theirs["slug"]))["revision"] == 1
    shipped_now = await pool.fetchval("SELECT revision FROM skills WHERE user_id IS NULL")
    assert shipped_now == 1


@pytest.mark.asyncio
async def test_evaluator_never_refines_without_the_grant(
    pool: asyncpg.Pool, store: SkillStore
) -> None:
    """T3/T4 for the evaluator: a revoked grant stops refinement and changes nothing."""
    alice = await _user(pool)
    created = await _evaluate(pool, alice, _DRAFT)
    await store.set_autonomous_edit(alice, created.created_skill_id, False)
    before = await _dump(pool)

    skipped = await _evaluate(pool, alice, _DRAFT)
    assert skipped.classification == "skipped_protected_match"
    assert skipped.protected is True
    assert await _dump(pool) == before

    # A grant revoked after the match was read is still enforced by the store.
    class _StaleGrant:
        def __init__(self, inner: SkillStore) -> None:
            self._inner = inner

        async def search_by_embedding(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {**match, "allow_autonomous_edit": True}
                for match in await self._inner.search_by_embedding(*args, **kwargs)
            ]

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    conversations = _Conversations()
    evaluator = SkillEvaluator(
        store=conversations,
        db_pool=pool,
        skill_store=_StaleGrant(store),  # type: ignore[arg-type]
        completion_callable=_completion(
            _DRAFT,
            {"decision": "PATCH", "reason": "r", "old_text": "Totals match.", "new_text": "x"},
        ),
        query_embedder=_embed_query,
    )
    raced = await evaluator.evaluate_completed_turn(
        SkillEvaluationRequest(alice, uuid.uuid4(), conversations.assistant_id, 6)
    )
    assert raced.classification == "skipped_protected_match"
    assert raced.reason == "autonomous_edit_not_granted"
    assert await _dump(pool) == before


# --------------------------------------------------------------------------
# Consolidation
# --------------------------------------------------------------------------


class _NudgeStore(MemoryStore):
    """The real audit-log writers over the test pool; counters are fixed."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_user_conversation_count_since_last_nudge(self, user_id: uuid.UUID) -> int:
        return 99

    async def get_total_conversation_count(self, user_id: uuid.UUID) -> int:
        return 99

    async def record_consolidation_nudge_run(
        self, user_id: uuid.UUID, conversation_count: int
    ) -> None:
        return None

    async def get_recent_memories_for_user(
        self, user_id: uuid.UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        return []


async def _autonomous(
    store: SkillStore, user_id: uuid.UUID, name: str, body: str
) -> dict[str, Any]:
    skill = await store.create(
        user_id, name=name, description=f"{name} description", body=body, actor="autonomous"
    )
    await store.store_embedding(uuid.UUID(skill["id"]), 1, _vector(name))
    return skill


async def _consolidate(
    pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    user_id: uuid.UUID,
    model_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    prompts: list[str] = []

    async def model(prompt: str) -> list[dict[str, Any]]:
        prompts.append(prompt)
        return model_actions

    @contextlib.asynccontextmanager
    async def compute(*_: Any, **__: Any) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(jobs, "_call_consolidation_model", model)
    monkeypatch.setattr(jobs, "account_compute", compute)
    result = await jobs._process_user_consolidation_nudge(
        user_id, _NudgeStore(pool), 1, 30, 3, pool
    )
    return {**result, "prompt": prompts[0] if prompts else ""}


@pytest.mark.asyncio
async def test_consolidation_stays_inside_one_account(
    pool: asyncpg.Pool, store: SkillStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2/T3 for consolidation, and the audit log holds ids and codes only."""
    alice, bob = await _user(pool), await _user(pool)
    kept = await _autonomous(store, alice, "Zephyrquill Kept", MARKER_BODY)
    absorbed = await _autonomous(store, alice, "Quaggaflux Absorbed", "absorbed vortexplum body")
    doomed = await _autonomous(store, alice, "Snargleby Doomed", "doomed body")
    stale = await _autonomous(store, alice, "Stale One", "stale body")
    manual = await store.create(alice, name="Manual One", description="d", body="manual")
    bobs = await _autonomous(store, bob, "Bob Autonomous", "bob body")
    await store.publish_shipped("house-style", name="House Style", description="d", body="shipped")

    result = await _consolidate(
        pool,
        monkeypatch,
        alice,
        [
            {
                "type": "merge",
                "skill_id": kept["slug"],
                "target_skill_id": absorbed["slug"],
                "similarity": 0.93,
                "reason": f"free text about {MARKER_NAME}",
            },
            {"type": "delete", "skill_id": doomed["slug"], "reason": "snargleby is obsolete"},
            {"type": "flag_stale", "skill_id": stale["slug"], "reason": "unused"},
            # None of these are the account's autonomous skills.
            {"type": "delete", "skill_id": bobs["slug"], "reason": "x"},
            {"type": "delete", "skill_id": manual["slug"], "reason": "x"},
            {"type": "delete", "skill_id": "house-style", "reason": "x"},
            {"type": "merge", "skill_id": kept["slug"], "target_skill_id": bobs["slug"]},
            {"type": "merge", "skill_id": kept["slug"], "target_skill_id": absorbed["slug"]},
        ],
    )

    # The prompt offered only Alice's autonomous skills.
    assert "Bob Autonomous" not in result["prompt"]
    assert "Manual One" not in result["prompt"] and "House Style" not in result["prompt"]

    merged = await store.get(alice, kept["slug"])
    assert "absorbed vortexplum body" in merged["body"] and merged["revision"] == 2
    assert (await store.list_revisions(alice, kept["slug"]))[0]["actor"] == "consolidation"
    remaining = {skill["slug"] for skill in await store.list(alice)}
    assert remaining == {kept["slug"], stale["slug"], manual["slug"], "house-style"}
    assert (await store.get(bob, bobs["slug"]))["body"] == "bob body"
    assert result["duplicates_merged"] == 1 and result["stale_flagged"] == 1

    outcomes = [(a["action_type"], a["reason"], a["status"]) for a in result["actions"]]
    assert outcomes == [
        ("merge", "model_merge", "applied"),
        ("delete", "merged_into", "applied"),
        ("delete", "model_delete", "applied"),
        ("flag_stale", "model_stale", "recorded"),
        ("delete", "not_candidate", "skipped"),
        ("delete", "not_candidate", "skipped"),
        ("delete", "not_candidate", "skipped"),
        ("merge", "target_not_candidate", "skipped"),
        ("merge", "target_not_candidate", "skipped"),
    ]

    rows = await pool.fetch(
        "SELECT user_id, action_type, skill_id, target_skill_id, reason, status "
        "FROM skill_consolidation_log ORDER BY run_at, action_type, reason"
    )
    assert len(rows) == 9 and {row["user_id"] for row in rows} == {alice}
    applied_merge = next(row for row in rows if row["reason"] == "model_merge")
    assert applied_merge["skill_id"] == uuid.UUID(kept["id"])
    assert applied_merge["target_skill_id"] == uuid.UUID(absorbed["id"])
    deleted = next(row for row in rows if row["reason"] == "model_delete")
    assert deleted["skill_id"] == uuid.UUID(doomed["id"]) and deleted["status"] == "applied"
    # Slugs that were not the account's candidates are recorded without an id.
    assert all(row["skill_id"] is None for row in rows if row["reason"] == "not_candidate")
    _assert_no_markers(await _dump(pool, ("skill_consolidation_log",)))


@pytest.mark.asyncio
async def test_a_committed_merge_always_has_its_audit_rows(
    pool: asyncpg.Pool, store: SkillStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure right after the merge commits cannot lose the audit record."""
    alice = await _user(pool)
    kept = await _autonomous(store, alice, "Kept", "kept body")
    absorbed = await _autonomous(store, alice, "Absorbed", "absorbed body")
    actions = [
        {"type": "merge", "skill_id": kept["slug"], "target_skill_id": absorbed["slug"],
         "similarity": 0.91}
    ]  # fmt: skip

    # The job dies after the merge and before its audit loop.
    async def crash(*_: Any, **__: Any) -> None:
        raise RuntimeError("worker stopped after the merge")

    monkeypatch.setattr(_NudgeStore, "record_consolidation_nudge_run", crash)
    with pytest.raises(RuntimeError, match="worker stopped"):
        await _consolidate(pool, monkeypatch, alice, actions)

    assert {skill["slug"] for skill in await store.list(alice)} == {kept["slug"]}
    rows = await pool.fetch(
        "SELECT action_type, skill_id, target_skill_id, reason, status, similarity, run_id "
        "FROM skill_consolidation_log ORDER BY action_type DESC"
    )
    assert [(r["action_type"], r["reason"], r["status"]) for r in rows] == [
        ("merge", "model_merge", "applied"),
        ("delete", "merged_into", "applied"),
    ]
    assert (rows[0]["skill_id"], rows[0]["target_skill_id"]) == (
        uuid.UUID(kept["id"]),
        uuid.UUID(absorbed["id"]),
    )
    assert (rows[1]["skill_id"], rows[1]["target_skill_id"]) == (
        uuid.UUID(absorbed["id"]),
        uuid.UUID(kept["id"]),
    )
    assert rows[0]["similarity"] == 0.91 and rows[0]["run_id"] == rows[1]["run_id"]


@pytest.mark.asyncio
async def test_a_merge_whose_audit_cannot_be_written_does_not_happen(
    pool: asyncpg.Pool, store: SkillStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await _user(pool)
    kept = await _autonomous(store, alice, "Kept", "kept body")
    absorbed = await _autonomous(store, alice, "Absorbed", "absorbed body")
    before = await _dump(pool, ("skills", "skill_revisions"))

    # The audit insert fails inside the merge transaction.
    await pool.execute("ALTER TABLE skill_consolidation_log RENAME TO audit_log_away")
    try:
        result = await _consolidate(
            pool,
            monkeypatch,
            alice,
            [{"type": "merge", "skill_id": kept["slug"], "target_skill_id": absorbed["slug"]}],
        )
    except asyncpg.UndefinedTableError:
        # The job's own later audit write for the skipped action also fails.
        result = None
    finally:
        await pool.execute("ALTER TABLE audit_log_away RENAME TO skill_consolidation_log")

    assert await _dump(pool, ("skills", "skill_revisions")) == before
    assert await pool.fetchval("SELECT count(*) FROM skill_consolidation_log") == 0
    if result is not None:
        assert [(a["reason"], a["status"]) for a in result["actions"]] == [
            ("merge_failed", "skipped")
        ]

    # Run to completion: the two rows exist exactly once, with no duplicates
    # from the job's closing audit loop.
    done = await _consolidate(
        pool,
        monkeypatch,
        alice,
        [{"type": "merge", "skill_id": kept["slug"], "target_skill_id": absorbed["slug"]}],
    )
    assert done["duplicates_merged"] == 1
    assert await pool.fetchval("SELECT count(*) FROM skill_consolidation_log") == 2
    logged = {row["id"] for row in await pool.fetch("SELECT id FROM skill_consolidation_log")}
    assert {action["audit_id"] for action in done["actions"]} == logged


@pytest.mark.asyncio
async def test_consolidation_respects_a_revoked_grant(
    pool: asyncpg.Pool, store: SkillStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await _user(pool)
    kept = await _autonomous(store, alice, "Kept", "kept body")
    closed = await _autonomous(store, alice, "Closed", "closed body")
    other = await _autonomous(store, alice, "Other", "other body")
    await store.set_autonomous_edit(alice, closed["slug"], False)
    before = {skill["slug"]: skill for skill in await store.list(alice)}

    result = await _consolidate(
        pool,
        monkeypatch,
        alice,
        [
            {"type": "merge", "skill_id": kept["slug"], "target_skill_id": closed["slug"]},
            {"type": "merge", "skill_id": closed["slug"], "target_skill_id": other["slug"]},
            {"type": "delete", "skill_id": closed["slug"]},
        ],
    )
    assert [(a["reason"], a["status"]) for a in result["actions"]] == [
        ("not_granted", "skipped"),
        ("not_granted", "skipped"),
        ("not_granted", "skipped"),
    ]
    assert {skill["slug"]: skill for skill in await store.list(alice)} == before
    failed = await pool.fetchrow(
        "SELECT status, reason FROM skill_consolidation_log WHERE action_type = 'delete'"
    )
    assert (failed["status"], failed["reason"]) == ("failed", "not_granted")


# --------------------------------------------------------------------------
# At rest, and erasure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_skill_content_is_readable_at_rest_and_erasure_removes_everything(
    pool: asyncpg.Pool, store: SkillStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T11 and T9 across the evaluator, consolidation, export and revert."""
    alice, bob = await _user(pool), await _user(pool)
    created = await _evaluate(pool, alice, _DRAFT)
    slug = created.created_skill_id
    await _evaluate(
        pool,
        alice,
        _DRAFT,
        {
            "decision": "PATCH",
            "reason": f"mention {MARKER_BODY}",
            "trigger_conditions": MARKER_TRIGGER,
            "old_text": "Totals match.",
            "new_text": f"Totals match; {MARKER_BODY}.",
        },
    )
    second = await _autonomous(store, alice, "Quaggaflux Twin", f"twin: {MARKER_BODY}")
    await _consolidate(
        pool,
        monkeypatch,
        alice,
        [
            {
                "type": "merge",
                "skill_id": slug,
                "target_skill_id": second["slug"],
                "reason": MARKER_DESCRIPTION,
            },
            {"type": "flag_stale", "skill_id": slug, "reason": MARKER_TRIGGER},
        ],
    )
    async with _client(pool, alice) as a:
        exported = await a.get(f"/skills/{slug}/download")
        assert MARKER_BODY in exported.text
    await store.revert(alice, slug, 1)
    await _autonomous(store, bob, "Bob Keeps This", "bob body")

    dump = await _dump(pool)
    assert dump.count("skills:") >= 2 and "skill_revisions:" in dump
    assert "skill_consolidation_log:" in dump
    _assert_no_markers(dump)
    # No unkeyed digest of the content is stored either.
    import hashlib

    for text in (MARKER_NAME, slug, MARKER_DESCRIPTION, _SKILL_MARKDOWN.strip()):
        for digest in (hashlib.sha256, hashlib.md5, hashlib.sha1):
            assert digest(text.encode()).hexdigest() not in dump

    await pool.execute("DELETE FROM users WHERE id = $1", alice)
    assert await pool.fetchval("SELECT count(*) FROM skills WHERE user_id = $1", alice) == 0
    assert await pool.fetchval("SELECT count(*) FROM skill_consolidation_log") == 0
    owners = await pool.fetch(
        "SELECT DISTINCT s.user_id FROM skill_revisions r JOIN skills s ON s.id = r.skill_id"
    )
    assert [row["user_id"] for row in owners] == [bob]
    assert await pool.fetchval("SELECT count(*) FROM skills WHERE embedding IS NOT NULL") == 1


# --------------------------------------------------------------------------
# Migration 069
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_069_blanks_legacy_content_and_enforces_codes(
    bare_pool: asyncpg.Pool,
) -> None:
    pool = bare_pool
    alice = await _user(pool)
    await pool.execute(
        "INSERT INTO skill_consolidation_log (user_id, action_type, skill_id, target_skill_id, "
        "reason, status, skill_name, skill_description, skill_use_count) "
        "VALUES ($1, 'merge', 'zephyrquill-ledger', 'quaggaflux-twin', "
        "'merged because vortexplum', 'applied', 'Zephyrquill Ledger', 'snargleby', 4)",
        alice,
    )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(LOG_MIGRATION.read_text(encoding="utf-8"))

    row = await pool.fetchrow("SELECT * FROM skill_consolidation_log")
    assert set(row.keys()) == {
        "id", "user_id", "run_id", "run_at", "action_type", "skill_id", "target_skill_id",
        "reason", "similarity", "status", "skill_use_count", "skill_last_used_at",
    }  # fmt: skip
    assert (row["action_type"], row["status"], row["skill_use_count"]) == ("merge", "applied", 4)
    assert row["skill_id"] is None and row["target_skill_id"] is None
    assert row["reason"] == "legacy"
    _assert_no_markers(await _dump(pool, ("skill_consolidation_log",)))

    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO skill_consolidation_log (user_id, action_type, reason) "
            "VALUES ($1, 'merge', 'free text from a model')",
            alice,
        )
    with pytest.raises(asyncpg.DataError):
        await pool.execute(
            "INSERT INTO skill_consolidation_log (user_id, action_type, skill_id) "
            "VALUES ($1, 'merge', 'a-slug')",
            alice,
        )
    await pool.execute(
        "INSERT INTO skill_consolidation_log (user_id, action_type, skill_id, reason) "
        "VALUES ($1, 'delete', $2, 'model_delete')",
        alice,
        uuid.uuid4(),
    )

    # The rollback restores the old shape, and the migration applies again.
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(LOG_ROLLBACK.read_text(encoding="utf-8"))
    kinds = {
        row["column_name"]: row["data_type"]
        for row in await pool.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'skill_consolidation_log'"
        )
    }
    assert kinds["skill_id"] == "text" and "skill_name" in kinds
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(LOG_MIGRATION.read_text(encoding="utf-8"))
    assert await pool.fetchval("SELECT count(*) FROM skill_consolidation_log") == 2
