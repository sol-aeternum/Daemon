"""Actual migration helpers on owned synthetic databases, including merged C1.

No application DSN, runtime composition, role setup or live upgrade authority.
"""

import asyncpg
import pytest

from scripts import migrate
from tests.test_compute_metadata_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_history_acceptance_postgres import CANDIDATE


def files_through(name):
    return [p for p in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")) if p.name <= name]


async def upgrade_through(conn, name):
    await migrate.acquire_migration_lock(conn)
    try:
        applied = await migrate.get_applied_migrations(conn)
        for path in files_through(name):
            if path.name not in applied:
                await migrate.apply_migration(conn, path)
    finally:
        await migrate.release_migration_lock(conn, require_owned=True)


@pytest.mark.asyncio
async def test_complete_source_upgrade_through_056_preserves_c1_and_owner_only_acl(db):
    async with db.connection() as owner:
        assert await migrate.get_applied_migrations(owner) == {
            p.name for p in files_through("048_compute_metadata.sql")
        }
        await upgrade_through(owner, CANDIDATE.name)
        assert await migrate.get_applied_migrations(owner) == {
            p.name for p in files_through(CANDIDATE.name)
        }
        assert (
            await owner.fetchval(
                "SELECT bool_and(to_regclass('public.'||n) IS NOT NULL) FROM unnest(ARRAY["
                "'artifact_output_jobs','artifact_namespaces','resources','resource_versions',"
                "'resource_input_batches','resource_input_intents','resource_presentations',"
                "'task_history_binding','task_history_acceptance']) n"
            )
            is True
        )
        assert await owner.fetchval("SELECT count(*) FROM task_history_binding") == 0
        assert await owner.fetchval("SELECT count(*) FROM task_history_acceptance") == 0
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM pg_catalog.pg_class c "
                "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
                "pg_catalog.acldefault('r',c.relowner))) a "
                "WHERE c.oid IN ('public.task_history_binding'::regclass,"
                "'public.task_history_acceptance'::regclass) AND a.grantee<>c.relowner"
            )
            == 0
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
                "pg_catalog.acldefault('f',p.proowner))) a "
                "WHERE n.nspname='public' AND p.proname LIKE 'task_history_%' "
                "AND a.grantee<>p.proowner"
            )
            == 0
        )
        assert (
            await owner.fetchval(
                "SELECT has_function_privilege('md_app',"
                "'public.task_history_acceptance_project(bytea)','EXECUTE')"
            )
            is False
        )
        assert (
            await owner.fetchval(
                "SELECT has_function_privilege('md_app',"
                "'public.task_history_acceptance_page(uuid,bigint,text)','EXECUTE')"
            )
            is False
        )
        # Same reviewed inventory is a no-op; no H identity is initialized.
        await upgrade_through(owner, CANDIDATE.name)
        assert await owner.fetchval("SELECT count(*) FROM _migrations") == len(
            files_through(CANDIDATE.name)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["TABLES", "FUNCTIONS"])
async def test_056_failure_is_atomic_after_committed_c1_upgrade(db, kind):
    async with db.connection() as owner:
        await upgrade_through(owner, "055_resource_presentations.sql")
        permission = "SELECT" if kind == "TABLES" else "EXECUTE"
        await owner.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {permission} ON {kind} TO md_app"
        )
        with pytest.raises(asyncpg.PostgresError, match="task_history_unsafe_default_privileges"):
            await upgrade_through(owner, CANDIDATE.name)
        assert not owner.is_in_transaction()
        assert await migrate.get_applied_migrations(owner) == {
            p.name for p in files_through("055_resource_presentations.sql")
        }
        assert await owner.fetchval("SELECT to_regclass('public.task_history_binding')") is None
        assert await owner.fetchval("SELECT to_regclass('public.task_history_acceptance')") is None
        assert (
            await owner.fetchval("SELECT to_regclass('public.resource_input_batches')") is not None
        )
