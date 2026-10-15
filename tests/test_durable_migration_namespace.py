"""Fixture namespace compatibility, not production metadata qualification."""

import os
from uuid import uuid4

import asyncpg
import pytest

from tests.durable_tasks_support import (
    DSN_ENV,
    MIGRATIONS,
    _provision,
    migration_sql_for_schema,
)


@pytest.mark.parametrize("schema", ["public", "tasks_test_wrong", "other_" + "a" * 32])
def test_migration_adapter_refuses_non_owned_namespace(schema):
    with pytest.raises(ValueError, match="owned durable-test schema"):
        migration_sql_for_schema(MIGRATIONS / "048_compute_metadata.sql", schema)


def test_namespace_adapter_leaves_previous_migrations_exact():
    path = MIGRATIONS / "047_account_control_persistence.sql"
    assert migration_sql_for_schema(path, "tasks_test_" + uuid4().hex) == path.read_text()


@pytest.mark.asyncio
async def test_full_fixture_replay_keeps_metadata_and_dependencies_private():
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"requires disposable {DSN_ENV}")
    admin = await asyncpg.connect(dsn)

    async def public_snapshot():
        return await admin.fetchval("""SELECT jsonb_build_object(
          'tables',(SELECT jsonb_agg(jsonb_build_object('oid',c.oid,'acl',c.relacl::text) ORDER BY c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN ('compute_scope_metadata','compute_reservation_metadata')),
          'functions',(SELECT jsonb_agg(jsonb_build_object('oid',p.oid,'acl',p.proacl::text,'definition',pg_get_functiondef(p.oid)) ORDER BY p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname LIKE 'compute_metadata_%'))""")

    fixture = _provision()
    schema = None
    canaries_created = False
    try:
        # Nonempty public canaries discriminate accidental public DDL/revokes.
        # Never replace existing objects, even in a disposable test database.
        assert await admin.fetchval("SELECT to_regclass('public.compute_scope_metadata')") is None
        assert (
            await admin.fetchval(
                "SELECT to_regprocedure('public.compute_metadata_read_scope(uuid,uuid)')"
            )
            is None
        )
        async with admin.transaction():
            await admin.execute("CREATE TABLE public.compute_scope_metadata(fixture_canary uuid)")
            await admin.execute(
                "CREATE FUNCTION public.compute_metadata_read_scope(uuid,uuid) RETURNS uuid "
                "LANGUAGE sql AS $$ SELECT $1 $$"
            )
        canaries_created = True
        before = await public_snapshot()
        env = await anext(fixture)
        async with env.pool.acquire() as conn:
            schema = await conn.fetchval("SELECT current_schema()")
            assert schema.startswith("tasks_test_")
            tables = await conn.fetchval(
                "SELECT count(*) FROM pg_class WHERE relnamespace=$1::regnamespace "
                "AND relname IN ('compute_scope_metadata','compute_reservation_metadata')",
                schema,
            )
            assert tables == 2
            edges = await conn.fetch(
                """SELECT c.relname AS child,p.relname AS parent,
                cn.nspname AS child_schema,pn.nspname AS parent_schema
                FROM pg_constraint f JOIN pg_class c ON c.oid=f.conrelid
                JOIN pg_class p ON p.oid=f.confrelid
                JOIN pg_namespace cn ON cn.oid=c.relnamespace
                JOIN pg_namespace pn ON pn.oid=p.relnamespace
                WHERE f.contype='f' AND cn.nspname=$1
                AND c.relname IN ('compute_scope_metadata','compute_reservation_metadata')""",
                schema,
            )
            assert {(r["child"], r["parent"]) for r in edges} == {
                ("compute_scope_metadata", "entitlement_accounts"),
                ("compute_reservation_metadata", "compute_scope_metadata"),
                ("compute_reservation_metadata", "entitlement_reservations"),
            }
            assert all(r["child_schema"] == r["parent_schema"] == schema for r in edges)
            functions = await conn.fetch(
                """SELECT p.prosecdef,p.proconfig,pg_get_functiondef(p.oid) AS definition,
                EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                  WHERE a.grantee=0) AS public_grant
                FROM pg_proc p WHERE p.pronamespace=$1::regnamespace
                AND p.proname LIKE 'compute_metadata_%'""",
                schema,
            )
            assert len(functions) == 5
            assert all(
                r["prosecdef"]
                and r["proconfig"] == ["search_path=pg_catalog"]
                and not r["public_grant"]
                and schema + "." in r["definition"]
                and "public." not in r["definition"]
                for r in functions
            )
        assert await public_snapshot() == before
        await fixture.aclose()
        assert await admin.fetchval("SELECT to_regnamespace($1)", schema) is None
        assert await public_snapshot() == before
    finally:
        try:
            await fixture.aclose()
        finally:
            try:
                if canaries_created:
                    await admin.execute(
                        "DROP FUNCTION public.compute_metadata_read_scope(uuid,uuid); "
                        "DROP TABLE public.compute_scope_metadata"
                    )
            finally:
                await admin.close()
