"""Account-owned reminder notes (#695).

Tool-boundary tests run everywhere. Store tests need a disposable PostgreSQL
(``TASKS_TEST_DATABASE_URL`` or ``ENTITLEMENTS_TEST_DATABASE_URL``); each uses
its own schema and drops only that schema.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.services import reminders as reminders_module
from orchestrator.services.reminders import ReminderError, ReminderStore
from orchestrator.tasks.fence import is_material, outcome_of_tool_result
from orchestrator.tools.builtin import create_default_registry
from orchestrator.tools.reminder import ReminderListTool, ReminderSetTool

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/067_account_reminders.sql"
ROLLBACK = ROOT / "migrations/rollback/067_account_reminders.down.sql"
MARKER = "reminder-plaintext-marker"


# --------------------------------------------------------------------------
# Tool boundary (no database)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("store", "owner"),
    [(None, None), (None, uuid.uuid4()), (AsyncMock(), None)],
)
async def test_tools_refuse_without_store_or_owner(store: Any, owner: uuid.UUID | None) -> None:
    set_result = json.loads(await ReminderSetTool(store, owner).execute(text="call"))
    list_result = json.loads(await ReminderListTool(store, owner).execute())
    assert "unavailable" in set_result["error"]
    assert "unavailable" in list_result["error"]
    if store is not None:
        assert store.mock_calls == []


@pytest.mark.asyncio
async def test_owner_comes_from_construction_not_arguments() -> None:
    owner, forged = uuid.uuid4(), uuid.uuid4()
    store = AsyncMock()
    store.add.return_value = {"id": str(uuid.uuid4()), "text": "call"}
    store.list.return_value = []

    await ReminderSetTool(store, owner).execute(text="call", user_id=str(forged), owner=forged)
    await ReminderListTool(store, owner).execute(user_id=str(forged), owner=forged)

    store.add.assert_awaited_once_with(owner, "call", None)
    store.list.assert_awaited_once_with(owner, None)


def test_schemas_expose_no_owner_field() -> None:
    for tool in (ReminderSetTool, ReminderListTool):
        assert not {"user_id", "owner", "account_id"} & set(tool.parameters["properties"])


@pytest.mark.asyncio
async def test_result_shapes_match_the_durable_fence_and_disclose_no_notification() -> None:
    owner = uuid.uuid4()
    store = AsyncMock()
    store.add.return_value = {"id": str(uuid.uuid4()), "text": "call"}
    store.list.return_value = [store.add.return_value]

    set_result = await ReminderSetTool(store, owner).execute(text="call")
    list_result = await ReminderListTool(store, owner).execute()

    assert is_material("reminder_set")
    assert outcome_of_tool_result("reminder_set", set_result) == "succeeded"
    assert outcome_of_tool_result("reminder_list", list_result) == "succeeded"
    assert "notifications are not enabled" in json.loads(set_result)["message"]


@pytest.mark.asyncio
async def test_storage_failure_reports_error_without_content() -> None:
    owner = uuid.uuid4()
    store = AsyncMock()
    store.add.side_effect = RuntimeError(MARKER)
    store.list.side_effect = RuntimeError(MARKER)

    set_result = await ReminderSetTool(store, owner).execute(text="call")
    list_result = await ReminderListTool(store, owner).execute()

    assert "error" in json.loads(set_result) and MARKER not in set_result
    assert "error" in json.loads(list_result) and MARKER not in list_result
    assert outcome_of_tool_result("reminder_list", list_result) != "succeeded"


@pytest.mark.asyncio
async def test_default_registry_without_account_keeps_tools_registered_but_refusing() -> None:
    registry = create_default_registry()
    tool = registry.get("reminder_set")
    assert tool is not None
    result = json.loads(await tool.execute(text="call"))
    assert "unavailable" in result["error"]


def test_legacy_shared_file_storage_is_gone() -> None:
    import orchestrator.tools.reminder as tool_module

    assert not hasattr(tool_module, "ReminderStorage")
    assert "reminders.json" not in Path(tool_module.__file__).read_text()


def test_migration_is_additive_account_owned_and_has_a_rollback() -> None:
    up, down = MIGRATION.read_text(), ROLLBACK.read_text()
    assert "REFERENCES users(id) ON DELETE CASCADE" in up
    assert "content_encrypted TEXT NOT NULL" in up
    assert "DROP TABLE" not in up and "ALTER TABLE" not in up
    assert "DROP TABLE IF EXISTS reminders" in down


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
    schema = f"reminders_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    created = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=8, server_settings={"search_path": schema}
        )
        assert created is not None
        async with created.acquire() as conn:
            await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")
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


def _store(pool: asyncpg.Pool, key: str | None = None) -> ReminderStore:
    return ReminderStore(pool, ContentEncryption(key or Fernet.generate_key().decode()))


@pytest.mark.asyncio
async def test_two_accounts_never_see_each_other(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    a, b = await _user(pool), await _user(pool)
    mine = await store.add(a, f"a {MARKER}", "2026-12-01T10:00:00Z")
    theirs = await store.add(b, "b note")

    assert [r["id"] for r in await store.list(a)] == [mine["id"]]
    assert [r["id"] for r in await store.list(b)] == [theirs["id"]]
    assert (await store.list(a))[0] == mine
    assert await store.list(uuid.uuid4()) == []

    # Through the tools: a forged owner argument still lists only the bound account.
    listed = json.loads(await ReminderListTool(store, b).execute(user_id=str(a)))
    assert listed["count"] == 1 and listed["reminders"][0]["text"] == "b note"


@pytest.mark.asyncio
async def test_text_and_due_time_are_ciphertext_at_rest(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    owner = await _user(pool)
    await store.add(owner, f"secret {MARKER}", f"due {MARKER}")
    row = await pool.fetchrow("SELECT * FROM reminders")
    assert row is not None
    assert all(MARKER not in str(value) for value in row.values())


@pytest.mark.asyncio
async def test_wrong_key_raises_instead_of_returning_an_empty_list(pool: asyncpg.Pool) -> None:
    owner = await _user(pool)
    await _store(pool).add(owner, "note")
    with pytest.raises(ValueError, match="Invalid ciphertext"):
        await _store(pool).list(owner)
    result = json.loads(await ReminderListTool(_store(pool), owner).execute())
    assert "error" in result and "reminders" not in result


@pytest.mark.asyncio
async def test_completed_filter_and_stable_order(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    owner = await _user(pool)
    first = await store.add(owner, "first")
    second = await store.add(owner, "second")
    await pool.execute(
        "UPDATE reminders SET completed = TRUE WHERE id = $1", uuid.UUID(first["id"])
    )

    assert [r["text"] for r in await store.list(owner)] == ["first", "second"]
    assert [r["id"] for r in await store.list(owner, True)] == [first["id"]]
    assert [r["id"] for r in await store.list(owner, False)] == [second["id"]]


@pytest.mark.asyncio
async def test_unknown_account_and_invalid_input_refuse(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    owner = await _user(pool)
    cases: list[tuple[uuid.UUID, Any, Any, str]] = [
        (uuid.uuid4(), "note", None, "account_unavailable"),
        (owner, "   ", None, "text_required"),
        (owner, "x" * (reminders_module.MAX_TEXT_CHARS + 1), None, "text_too_long"),
        (owner, "note", "x" * (reminders_module.MAX_DUE_TIME_CHARS + 1), "due_time_invalid"),
        (owner, "note", 5, "due_time_invalid"),
    ]
    for user_id, text, due_time, reason in cases:
        with pytest.raises(ReminderError) as raised:
            await store.add(user_id, text, due_time)
        assert raised.value.reason == reason
    assert await pool.fetchval("SELECT count(*) FROM reminders") == 0
    assert (await store.add(owner, "x" * reminders_module.MAX_TEXT_CHARS))["completed"] is False


@pytest.mark.asyncio
async def test_account_limit_holds_under_concurrency(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reminders_module, "MAX_REMINDERS_PER_ACCOUNT", 3)
    store = _store(pool)
    owner, other = await _user(pool), await _user(pool)

    results = await asyncio.gather(
        *(store.add(owner, f"note {i}") for i in range(8)), return_exceptions=True
    )
    refused = [r for r in results if isinstance(r, ReminderError)]
    assert len(results) - len(refused) == 3
    assert {r.reason for r in refused} == {"limit_reached"}
    assert await pool.fetchval("SELECT count(*) FROM reminders WHERE user_id=$1", owner) == 3
    assert (await store.add(other, "unaffected"))["text"] == "unaffected"


@pytest.mark.asyncio
async def test_deleting_the_account_removes_only_its_reminders(pool: asyncpg.Pool) -> None:
    store = _store(pool)
    a, b = await _user(pool), await _user(pool)
    await store.add(a, "a")
    kept = await store.add(b, "b")
    await pool.execute("DELETE FROM users WHERE id = $1", a)
    assert await store.list(a) == []
    assert [r["id"] for r in await store.list(b)] == [kept["id"]]
