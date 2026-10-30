"""Fixture namespace compatibility, not production metadata qualification."""

import os
from uuid import uuid4

import asyncpg
import pytest

from tests.benchmark_longmemeval.isolated_database import provision_isolated_database
from tests.test_conversation_listing_metadata import listing_database
from tests.durable_tasks_support import (
    DSN_ENV,
    Env,
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


def test_namespace_inventory_adapter_keeps_acl_and_binding():
    schema = "tasks_test_" + uuid4().hex
    sql = migration_sql_for_schema(MIGRATIONS / "052_artifact_namespaces.sql", schema)
    assert "public." not in sql
    assert f"REFERENCES {schema}.deletion_journal_binding" in sql
    assert f"REVOKE ALL ON {schema}.artifact_namespaces FROM PUBLIC" in sql
    assert f"'{schema}.artifact_namespaces'::regclass" in sql


def test_resource_registry_adapter_keeps_owned_binding_and_acl():
    schema = "tasks_test_" + uuid4().hex
    sql = migration_sql_for_schema(MIGRATIONS / "053_resource_registry.sql", schema)
    assert "public." not in sql
    assert f"ALTER TABLE {schema}.artifact_namespaces" in sql
    assert f"REFERENCES {schema}.resource_versions" in sql
    assert f"'{schema}.resource_publications'::regclass" in sql
    assert "FROM PUBLIC" in sql and "registry_unsafe_default_privileges" in sql


def test_input_batch_adapter_keeps_marker_constraints_and_acl():
    schema = "tasks_test_" + uuid4().hex
    sql = migration_sql_for_schema(MIGRATIONS / "054_resource_input_batches.sql", schema)
    assert "public." not in sql
    assert f"REFERENCES {schema}.resource_input_batches" in sql
    assert f"'{schema}.resource_input_intents'::regclass" in sql
    assert "FROM PUBLIC" in sql and "lookup_key_id" in sql and "source_deleted" in sql


def test_presentation_adapter_preserves_source_detachment_and_acl():
    schema = "tasks_test_" + uuid4().hex
    sql = migration_sql_for_schema(MIGRATIONS / "055_resource_presentations.sql", schema)
    assert "public." not in sql
    assert f"REFERENCES {schema}.messages(id) DEFERRABLE INITIALLY DEFERRED" in sql
    assert f"'{schema}.resource_presentations'::regclass" in sql
    assert "resource_presentation_unsafe_default_privileges" in sql and "FROM PUBLIC" in sql


def test_output_reservation_namespace_adapter_keeps_grantee_and_search_path_exact():
    schema = "tasks_test_" + uuid4().hex
    sql = migration_sql_for_schema(MIGRATIONS / "051_artifact_output_reservations.sql", schema)
    assert "public." not in sql
    assert f"REFERENCES {schema}.users(id)" in sql
    assert f"n.nspname='{schema}'" in sql
    assert f"'{schema}.artifact_output_jobs'::regclass" in sql
    assert "SET search_path=pg_catalog" in sql
    assert (
        f"REVOKE ALL ON {schema}.artifact_output_jobs,{schema}.artifact_output_intents FROM PUBLIC"
        in sql
    )
    assert "REVOKE ALL ON FUNCTION %s FROM PUBLIC" in sql


@pytest.mark.parametrize(
    ("prefix", "schema"),
    [
        ("", "a" * 32),
        ("unsafe;", "unsafe;" + "a" * 32),
        ("x" * 32, "x" * 32 + "a" * 32),
        ("bench_", "public"),
        ("bench_", "other_" + "a" * 32),
    ],
)
def test_custom_namespace_requires_valid_prefix_and_generated_suffix(prefix, schema):
    with pytest.raises(ValueError, match="owned durable-test schema"):
        migration_sql_for_schema(
            MIGRATIONS / "048_compute_metadata.sql", schema, schema_prefix=prefix
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_extension", ["pgcrypto", "vector"])
async def test_listing_refuses_foreign_extension_home_before_private_ddl(
    monkeypatch, foreign_extension
):
    statements = []

    class Admin:
        closed = False

        async def execute(self, sql):
            statements.append(sql)

        async def fetchval(self, sql, extension):
            return "foreign_fixture" if extension == foreign_extension else "public"

        async def close(self):
            self.closed = True

    admin = Admin()

    async def connect(_dsn):
        return admin

    async def forbidden_pool(*args, **kwargs):
        pytest.fail("foreign extension home reached pool/private schema provisioning")

    monkeypatch.setenv("ENTITLEMENTS_TEST_DATABASE_URL", "synthetic-never-connected")
    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(asyncpg, "create_pool", forbidden_pool)
    fixture = getattr(listing_database, "__wrapped__")()
    with pytest.raises(RuntimeError, match="extensions must be installed in public"):
        await anext(fixture)
    assert admin.closed
    assert statements and all(s.startswith("CREATE EXTENSION IF NOT EXISTS ") for s in statements)
    assert all("WITH SCHEMA public" in s for s in statements)


@pytest.mark.asyncio
async def test_listing_close_timeout_retains_schema_and_reports_failure(monkeypatch, tmp_path):
    import asyncio

    from tests import test_conversation_listing_metadata as listing

    statements = []

    class Admin:
        closed = False

        async def execute(self, sql):
            statements.append(sql)

        async def fetchval(self, sql, extension):
            return "public"

        async def close(self):
            self.closed = True

    class Pool:
        terminated = False

        def acquire(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def close(self):
            await asyncio.Event().wait()

        def terminate(self):
            self.terminated = True

    admin, pool = Admin(), Pool()

    async def connect(_dsn):
        return admin

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setenv("ENTITLEMENTS_TEST_DATABASE_URL", "synthetic-never-connected")
    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(listing, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(listing, "_LISTING_POOL_CLOSE_TIMEOUT_S", 0.01)
    fixture = getattr(listing_database, "__wrapped__")()
    assert await anext(fixture) is pool
    with pytest.raises(TimeoutError):
        await fixture.aclose()
    assert pool.terminated and admin.closed
    assert any(s.startswith("CREATE SCHEMA ") for s in statements)
    assert not any("DROP SCHEMA" in s for s in statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_kind", ["durable", "benchmark", "listing"])
async def test_full_fixture_replay_keeps_metadata_and_dependencies_private(fixture_kind):
    dsn_env = DSN_ENV if fixture_kind == "durable" else "ENTITLEMENTS_TEST_DATABASE_URL"
    dsn = os.environ.get(dsn_env, "").strip()
    if not dsn:
        pytest.skip(f"requires disposable {dsn_env}")
    admin = await asyncpg.connect(dsn)

    async def public_snapshot():
        return await admin.fetchval("""SELECT jsonb_build_object(
          'tables',(SELECT jsonb_agg(jsonb_build_object('oid',c.oid,'acl',c.relacl::text) ORDER BY c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN ('compute_scope_metadata','compute_reservation_metadata')),
          'functions',(SELECT jsonb_agg(jsonb_build_object('oid',p.oid,'acl',p.proacl::text,'definition',pg_get_functiondef(p.oid)) ORDER BY p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname LIKE 'compute_metadata_%'))""")

    if fixture_kind == "durable":
        fixture = _provision()
    elif fixture_kind == "benchmark":
        fixture = provision_isolated_database("bench_metadata_")
    else:
        # Drive the real pytest fixture's preserved generator so the same
        # canary/namespace/ACL and explicit aclose teardown probes cover it.
        fixture = getattr(listing_database, "__wrapped__")()
    schema = None
    canaries_created = False
    try:
        for extension in ("pgcrypto", "vector"):
            await admin.execute(f"CREATE EXTENSION IF NOT EXISTS {extension} WITH SCHEMA public")
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
        pool = (
            env.pool if isinstance(env, Env) else env if isinstance(env, asyncpg.Pool) else env[0]
        )
        async with pool.acquire() as conn:
            schema = await conn.fetchval("SELECT current_schema()")
            prefix = {
                "durable": "tasks_test_",
                "benchmark": "bench_metadata_",
                "listing": "history_listing_test_",
            }[fixture_kind]
            assert schema.startswith(prefix)
            namespace = schema if fixture_kind == "durable" else f'"{schema}"'
            tables = await conn.fetchval(
                "SELECT count(*) FROM pg_class WHERE relnamespace=$1::regnamespace "
                "AND relname IN ('compute_scope_metadata','compute_reservation_metadata')",
                schema,
            )
            assert tables == 2
            assert not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL "
                "aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
                "WHERE c.relnamespace=$1::regnamespace AND "
                "c.relname IN ('compute_scope_metadata','compute_reservation_metadata') "
                "AND a.grantee=0)",
                schema,
            )
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
                and namespace + "." in r["definition"]
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
