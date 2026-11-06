"""064 fixture relocation preserves production bytes and privilege grantees."""

from uuid import uuid4

import pytest

from tests.durable_tasks_support import MIGRATIONS, migration_sql_for_schema


@pytest.mark.parametrize("custom", [False, True])
def test_artifact_prepare_namespace_adapter_keeps_security_properties(custom):
    prefix = "bench_restore_" if custom else "tasks_test_"
    schema = prefix + uuid4().hex
    namespace = f'"{schema}"' if custom else schema
    path = MIGRATIONS / "064_artifact_restore_prepare.sql"
    original = path.read_text()
    sql = migration_sql_for_schema(path, schema, schema_prefix=prefix if custom else None)
    assert "public." not in sql
    assert f"ALTER TABLE {namespace}.task_history_projection" in sql
    assert (
        f"'{namespace}.task_history_artifact_project(pg_catalog.bytea)'::pg_catalog.regprocedure"
        in sql
    )
    assert f"n.nspname='{schema}'" in sql and "REVOKE ALL ON FUNCTION %s FROM PUBLIC" in sql
    assert "pg_xact_status" in sql and "SET search_path=pg_catalog" in sql
    assert sql.count("SET search_path=pg_catalog") == original.count("SET search_path=pg_catalog")
    assert path.read_text() == original
