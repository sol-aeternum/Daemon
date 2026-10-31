"""Real database isolation and clean migration replay for the fast fixture."""

import os

import asyncpg
import pytest

from tests.durable_database import DatabaseTemplate, database_dsn, migrated_template
from tests.durable_tasks_support import DSN_ENV


def test_database_selector_cannot_override_clone():
    assert (
        database_dsn(
            "postgresql://tasks:synthetic@localhost/control?database=foreign&dbname=other&sslmode=disable",
            "tasks_clone_" + "a" * 32,
        )
        == "postgresql://tasks:synthetic@localhost/tasks_clone_" + "a" * 32 + "?sslmode=disable"
    )
    with pytest.raises(ValueError, match="generated"):
        DatabaseTemplate("unused", "public", "tasks_test_" + "a" * 32)


@pytest.mark.asyncio
async def test_clean_migrations_and_clone_commit_rollback_ddl_isolation():
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"requires disposable {DSN_ENV}")
    # Deliberately build from template0 here, not from a previously migrated
    # fixture: this remains a dedicated clean migration-application regression.
    admin = await asyncpg.connect(dsn)
    names = []
    try:
        async with migrated_template(dsn) as template:
            names.append(template.name)
            async with template.clone() as first_dsn, template.clone() as second_dsn:
                first = await asyncpg.connect(first_dsn)
                second = await asyncpg.connect(second_dsn)
                peer = await asyncpg.connect(first_dsn)
                try:
                    for conn in (first, second, peer):
                        await conn.execute(f'SET search_path TO "{template.schema}", public')
                    names.extend(
                        [
                            await first.fetchval("SELECT current_database()"),
                            await second.fetchval("SELECT current_database()"),
                        ]
                    )
                    assert len(set(names)) == 3
                    assert await first.fetchval("SELECT to_regclass('tasks') IS NOT NULL")
                    assert await first.fetchval(
                        "SELECT to_regclass('compute_scope_metadata') IS NOT NULL"
                    )
                    extensions = await first.fetch(
                        "SELECT extname,extnamespace::regnamespace::text AS home "
                        "FROM pg_extension WHERE extname IN ('pgcrypto','vector')"
                    )
                    assert {(r["extname"], r["home"]) for r in extensions} == {
                        ("pgcrypto", "public"),
                        ("vector", "public"),
                    }
                    for conn in (first, second):
                        for table in ("task_history_binding", "task_history_acceptance"):
                            assert await conn.fetchval(
                                "SELECT to_regclass($1) IS NOT NULL", f"{template.schema}.{table}"
                            )
                            assert await conn.fetchval(
                                "SELECT to_regclass($1) IS NULL", f"public.{table}"
                            )
                        functions = await conn.fetch(
                            "SELECT p.proname,p.proconfig,pg_get_functiondef(p.oid) AS definition "
                            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                            "WHERE n.nspname=$1 AND p.proname LIKE 'task_history_%'",
                            template.schema,
                        )
                        assert {r["proname"] for r in functions} == {
                            "task_history_immutable",
                            "task_history_decode_acceptance",
                            "task_history_check_caller",
                            "task_history_acceptance_project",
                            "task_history_acceptance_page",
                        }
                        assert all(r["proconfig"] == ["search_path=pg_catalog"] for r in functions)
                        assert all("public." not in r["definition"] for r in functions)
                        assert (
                            await conn.fetchval(
                                "SELECT count(*) FROM pg_proc p "
                                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                                "CROSS JOIN LATERAL aclexplode("
                                "coalesce(p.proacl,acldefault('f',p.proowner))) a "
                                "WHERE n.nspname=$1 AND p.proname LIKE 'task_history_%' "
                                "AND a.grantee<>p.proowner",
                                template.schema,
                            )
                            == 0
                        )
                        assert (
                            await conn.fetchval(
                                "SELECT count(*) FROM pg_class c "
                                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                                "CROSS JOIN LATERAL aclexplode("
                                "coalesce(c.relacl,acldefault('r',c.relowner))) a "
                                "WHERE n.nspname=$1 "
                                "AND c.relname IN ('task_history_binding','task_history_acceptance') "
                                "AND a.grantee<>c.relowner",
                                template.schema,
                            )
                            == 0
                        )
                    await first.execute("CREATE TABLE fixture_isolation(value integer)")
                    async with first.transaction():
                        await first.execute("INSERT INTO fixture_isolation VALUES(1)")
                        assert await peer.fetchval("SELECT count(*) FROM fixture_isolation") == 0
                    assert await peer.fetchval("SELECT sum(value) FROM fixture_isolation") == 1
                    with pytest.raises(RuntimeError, match="rollback"):
                        async with first.transaction():
                            await first.execute("INSERT INTO fixture_isolation VALUES(2)")
                            raise RuntimeError("rollback")
                    assert await peer.fetchval("SELECT sum(value) FROM fixture_isolation") == 1
                    assert await second.fetchval("SELECT to_regclass('fixture_isolation')") is None
                    await first.execute("ALTER TABLE tasks ADD COLUMN fixture_only integer")
                    assert not await second.fetchval(
                        "SELECT EXISTS(SELECT FROM information_schema.columns "
                        "WHERE table_schema=$1 AND table_name='tasks' AND column_name='fixture_only')",
                        template.schema,
                    )
                finally:
                    await peer.close()
                    await second.close()
                    await first.close()
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM pg_database WHERE datname=ANY($1::text[])", names
            )
            == 0
        )
    finally:
        await admin.close()
