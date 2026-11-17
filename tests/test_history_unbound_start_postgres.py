"""#731: unselected hosts accept an exactly shaped, provably unbound H schema.

Owned synthetic databases only; no live DSN, role provisioning or H binding
authority. md_app holds no H privilege, like the split A/W roles.
"""

from typing import Any, cast

import asyncpg
import pytest

from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.runtime import (
    history_unselected_compatible,
    require_unselected_history,
)
from scripts import migrate
from tests.test_compute_metadata_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_history_acceptance_upgrade_postgres import upgrade_through

LATEST = sorted(migrate.MIGRATIONS_DIR.glob("*.sql"))[-1].name
BIND = (
    "INSERT INTO task_history_binding(history_id,version,key_id,key_check,request_schema) "
    "VALUES (gen_random_uuid(),1,1,repeat('a',64),'chat-canonical-3715-v1')"
)


async def unprivileged(db) -> bool:
    async with db.connection("md_app") as app:
        assert (
            await app.fetchval("SELECT has_table_privilege('public.task_history_binding','SELECT')")
            is False
        )
        return await history_unselected_compatible(app)


@pytest.mark.asyncio
async def test_pre_h_and_unbound_h_are_compatible_for_unprivileged_roles(db):
    async with db.connection() as owner:
        assert await history_unselected_compatible(owner) is True
        await upgrade_through(owner, LATEST)
        assert await owner.fetchval("SELECT count(*) FROM task_history_binding") == 0
        assert await history_unselected_compatible(owner) is True
        await require_unselected_history(cast(Any, owner))
    assert await unprivileged(db) is True


@pytest.mark.asyncio
async def test_committed_binding_refuses_permanently(db):
    async with db.connection() as owner:
        await upgrade_through(owner, LATEST)
        await owner.execute(BIND)
        with pytest.raises(HistoryError, match="history_configuration_required"):
            await require_unselected_history(cast(Any, owner))
        # Immutable triggers keep the binding; VACUUM cannot reopen the path.
        with pytest.raises(asyncpg.PostgresError):
            await owner.execute("DELETE FROM task_history_binding")
        await owner.execute("VACUUM task_history_binding")
        assert await history_unselected_compatible(owner) is False
    assert await unprivileged(db) is False


@pytest.mark.asyncio
async def test_aborted_insert_refuses_until_vacuum_truncates(db):
    async with db.connection() as owner:
        await upgrade_through(owner, LATEST)
        with pytest.raises(asyncpg.PostgresError):
            async with owner.transaction():
                await owner.execute(BIND)
                raise asyncpg.PostgresError("abort")
        assert await owner.fetchval("SELECT count(*) FROM task_history_binding") == 0
        assert await history_unselected_compatible(owner) is False
        await owner.execute("VACUUM task_history_binding")
        assert await history_unselected_compatible(owner) is True


@pytest.mark.asyncio
async def test_uncommitted_binding_refuses_concurrent_check(db):
    async with db.connection() as owner, db.connection() as other:
        await upgrade_through(owner, LATEST)
        async with owner.transaction():
            await owner.execute(BIND)
            assert await history_unselected_compatible(other) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "ALTER TABLE task_history_binding DISABLE TRIGGER task_history_binding_immutable",
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_no_truncate",
        "ALTER TABLE task_history_acceptance ENABLE REPLICA TRIGGER task_history_acceptance_immutable",
        "ALTER TABLE task_history_projection DROP CONSTRAINT task_history_projection_history_id_fkey",
        "ALTER TABLE task_history_projection SET UNLOGGED",
        "CREATE TABLE public.shadow_binding () INHERITS (public.task_history_binding)",
        "CREATE TABLE public.task_history_extra (i int)",
        "CREATE VIEW public.task_history_view AS SELECT 1 AS i",
        "ALTER TABLE task_history_binding RENAME TO task_history_binding_old;"
        "CREATE VIEW public.task_history_binding AS SELECT * FROM task_history_binding_old",
    ],
)
async def test_changed_h_shape_refuses(db, damage):
    async with db.connection() as owner:
        await upgrade_through(owner, LATEST)
        assert await history_unselected_compatible(owner) is True
        tx = owner.transaction()
        await tx.start()
        try:
            await owner.execute(damage)
            assert await history_unselected_compatible(owner) is False
        finally:
            await tx.rollback()
        assert await history_unselected_compatible(owner) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "partial",
    [
        "CREATE FUNCTION public.task_history_stray() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "CREATE TABLE public.task_history_binding (singleton boolean)",
    ],
)
async def test_partial_or_stray_h_before_migration_refuses(db, partial):
    async with db.connection() as owner:
        await owner.execute(partial)
        assert await history_unselected_compatible(owner) is False


@pytest.mark.asyncio
async def test_acceptance_only_schema_refuses(db):
    async with db.connection() as owner:
        await upgrade_through(owner, "056_restore_history_acceptance.sql")
        assert await history_unselected_compatible(owner) is False
