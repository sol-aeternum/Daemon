"""Owned synthetic 043→050 driver qualification; no application DSN or data.

The reference schema is built independently from the approved SQL; every test
clones a fresh 043 baseline. Existing wire relay faults drop actual COMMIT frames
or their replies. None of these helpers are an application upgrade/restore tool.
"""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import pytest
import pytest_asyncio

from scripts import migrate
from scripts import migrate_044_050 as driver
from tests.compute_metadata_support import Bridge, Database, metadata_cluster as metadata_cluster


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def templates(metadata_cluster):
    cluster = metadata_cluster
    baseline, reference = "md_base_" + uuid4().hex, "md_ref_" + uuid4().hex
    release = driver.load_release()
    cluster.sql(f'CREATE DATABASE "{baseline}"')
    cluster.databases.add(baseline)
    cluster.sql(
        "GRANT USAGE,CREATE ON SCHEMA public TO md_app;"
        "GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO md_app;"
        "SET ROLE md_app; CREATE TABLE public._migrations("
        "id SERIAL PRIMARY KEY,filename text NOT NULL UNIQUE,"
        "applied_at timestamptz NOT NULL DEFAULT now());",
        database=baseline,
    )
    script = ""
    for filename in release.previous:
        role = "postgres" if filename == "001_create_extensions.sql" else "md_app"
        sql = (driver.ROOT / "migrations" / filename).read_text()
        script += (
            f"SET ROLE {role}; BEGIN;"
            + sql
            + f"\nINSERT INTO public._migrations(filename) VALUES('{filename}'); COMMIT;\n"
        )
    cluster.sql(script, database=baseline)
    cluster.sql(f'CREATE DATABASE "{reference}" TEMPLATE "{baseline}"')
    cluster.databases.add(reference)
    for payload in release.payloads:
        cluster.sql(
            "BEGIN;"
            + payload.sql
            + "\nINSERT INTO public._migrations(filename) "
            + f"VALUES('{payload.filename}'); COMMIT;",
            database=reference,
            role="md_app",
        )
    cluster.record("migration_reference_built", prior=44, target=51, excluded=driver.EXCLUDED)
    yield SimpleNamespace(cluster=cluster, baseline=baseline, reference=reference)
    for name in (reference, baseline):
        cluster.sql(f'DROP DATABASE "{name}"')
        cluster.databases.remove(name)


@pytest_asyncio.fixture
async def upgrade(templates):
    cluster = templates.cluster
    name = "md_upgrade_" + uuid4().hex
    cluster.sql(f'CREATE DATABASE "{name}" TEMPLATE "{templates.baseline}"')
    cluster.databases.add(name)
    resource = cluster.resource / uuid4().hex[:6]
    resource.mkdir(mode=0o700)
    bridge = Bridge(cluster, resource / ".s.PGSQL.5432")
    await bridge.start()
    db = Database(cluster, name, resource, bridge)
    # This module's direct synthetic migrator is md_app for the existing
    # protocol relay's real COMMIT fault selector, not md_owner.
    db.sql = lambda sql, role="md_app", success=True: cluster.sql(
        sql, database=name, role=role, success=success
    )
    async with db.connection("md_app") as conn:
        identity = await conn.fetchrow(driver.IDENTITY_SQL)
        before = await driver.catalog_digest(conn)
    import asyncpg

    conn = await asyncpg.connect(
        host=str(resource),
        user="md_app",
        password="",
        database=templates.reference,
        ssl=False,
        timeout=2,
    )
    try:
        after = await driver.catalog_digest(conn)
        await conn.execute("SET search_path=pg_catalog,public,pg_temp")
        reference_sections = [
            [dict(r) for r in await conn.fetch(q)] for q in driver.CATALOG_QUERIES
        ]
    finally:
        await conn.close(timeout=2)
    target = driver.Target(name, identity["oid"], "md_app", identity["cluster_id"], before, after)
    url = f"postgresql://md_app:@/{name}?host={quote(str(resource))}&port=5432&sslmode=disable"
    value = SimpleNamespace(
        db=db,
        target=target,
        url=url,
        receipts=[],
        release=driver.load_release(),
        reference_sections=reference_sections,
    )

    async def run(target=None, connect=None):
        receipt = resource / (uuid4().hex + ".jsonl")
        value.receipts.append(receipt)
        kwargs = {} if connect is None else {"connect": connect}
        await driver.execute(
            value.release,
            expected_source=value.release.sha256,
            target=target or value.target,
            database_url=value.url,
            receipt=receipt,
            **kwargs,
        )

    value.run = run
    try:
        yield value
    finally:
        bridge.commit_fault = None
        await bridge.close()
        for receipt in value.receipts:
            if receipt.exists():
                dest = cluster.evidence / ("upgrade-" + name + "-" + receipt.name)
                dest.write_bytes(receipt.read_bytes())
        assert cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{name}'") == "0"
        cluster.sql(f'DROP DATABASE "{name}"')
        cluster.databases.remove(name)
        cluster.sql("CHECKPOINT")


def events(upgrade):
    return [json.loads(line) for line in upgrade.receipts[-1].read_text().splitlines()]


@pytest.mark.asyncio
async def test_exact_050_schema_and_bookkeeping_without_051(upgrade):
    try:
        await upgrade.run()
    except driver.Refused:
        async with upgrade.db.connection("md_app") as conn:
            await conn.execute("SET search_path=pg_catalog,public,pg_temp")
            actual = [[dict(r) for r in await conn.fetch(q)] for q in driver.CATALOG_QUERIES]
        differences = [
            (i, [r for r in a if r not in b], [r for r in b if r not in a])
            for i, (a, b) in enumerate(zip(actual, upgrade.reference_sections))
            if a != b
        ]
        assert not differences, differences
        raise
    assert events(upgrade)[-1]["event"] == "completed"
    assert len([e for e in events(upgrade) if e["event"] == "commit_acknowledged"]) == 7
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations", role="md_app") == "51"
    assert (
        upgrade.db.sql(
            "SELECT to_regclass('public.artifact_output_jobs') IS NULL "
            "AND to_regclass('public.artifact_output_intents') IS NULL"
        )
        == "t"
    )
    with pytest.raises(driver.Refused, match="migration_state_mismatch"):
        await upgrade.run()  # Even a completed batch is not an implicit rerun.


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["database", "oid", "principal", "cluster_id", "schema_before"])
async def test_wrong_target_or_baseline_refuses_without_migration(upgrade, field):
    changes = dict(
        database="wrong",
        oid=upgrade.target.oid + 1,
        principal="md_owner",
        cluster_id="1",
        schema_before="0" * 64,
    )
    with pytest.raises(
        driver.Refused,
        match="schema_before_mismatch" if field == "schema_before" else "target_mismatch",
    ):
        await upgrade.run(replace(upgrade.target, **{field: changes[field]}))
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "44"
    assert upgrade.db.sql("SELECT to_regclass('public.tasks') IS NULL") == "t"


@pytest.mark.asyncio
async def test_role_substitution_refuses_even_matching_current_user(upgrade):
    async def substituted(**options):
        import asyncpg

        options["user"] = "postgres"
        conn = await asyncpg.connect(**options)
        await conn.execute("SET ROLE md_app")
        return conn

    with pytest.raises(driver.Refused, match="target_mismatch"):
        await upgrade.run(connect=substituted)
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "44"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "extra", "051", "duplicate"])
async def test_unexpected_migration_state_never_creates_or_repairs_bookkeeping(upgrade, state):
    if state == "missing":
        upgrade.db.sql(
            "DELETE FROM public._migrations WHERE filename='036_memory_content_hash.sql'"
        )
    elif state == "duplicate":
        upgrade.db.sql(
            "ALTER TABLE public._migrations DROP CONSTRAINT _migrations_filename_key;"
            "INSERT INTO public._migrations(filename) VALUES('036_memory_content_hash.sql')"
        )
    else:
        name = driver.EXCLUDED if state == "051" else "052_unreviewed.sql"
        upgrade.db.sql(f"INSERT INTO public._migrations(filename) VALUES('{name}')")
    before = upgrade.db.sql(
        "SELECT string_agg(filename,',' ORDER BY filename) FROM public._migrations"
    )
    with pytest.raises(driver.Refused, match="migration_state_mismatch"):
        await upgrade.run()
    assert (
        upgrade.db.sql("SELECT string_agg(filename,',' ORDER BY filename) FROM public._migrations")
        == before
    )


@pytest.mark.asyncio
async def test_real_shared_advisory_lock_refuses_generic_and_dedicated_competitors(
    upgrade, monkeypatch
):
    async with upgrade.db.connection("md_app") as blocker:
        await migrate.acquire_migration_lock(blocker)
        with pytest.raises(driver.Refused, match="migration_lock_busy"):
            await upgrade.run()
    # The existing generic runner takes exactly this lock too, before DDL.
    async with upgrade.db.connection("md_app") as blocker:
        await migrate.acquire_migration_lock(blocker)
        monkeypatch.setattr(migrate, "resolve_database_url", lambda: upgrade.url)
        with pytest.raises(migrate.MigrationLockError):
            await migrate.run_migrations()
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "44"


@pytest.mark.asyncio
async def test_file_failure_keeps_earlier_commits_and_refuses_partial_resume(upgrade, monkeypatch):
    original = migrate.apply_migration

    async def fail(conn, path, **kwargs):
        if path.name.startswith("045_"):
            kwargs["sql_content"] += (
                "\nDO $$ BEGIN RAISE EXCEPTION 'synthetic-private-row' USING ERRCODE='23505'; END $$;"
            )
        await original(conn, path, **kwargs)

    monkeypatch.setattr(migrate, "apply_migration", fail)
    with pytest.raises(driver.Refused) as caught:
        await upgrade.run()
    assert caught.value.sqlstate == "23505" and "private" not in str(caught.value)
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "45"
    assert upgrade.db.sql("SELECT to_regclass('public.account_email_ownership') IS NULL") == "t"
    assert events(upgrade)[-1]["commit_uncertain"] is False
    assert "synthetic-private-row" not in upgrade.receipts[-1].read_text()
    with pytest.raises(driver.Refused, match="migration_state_mismatch"):
        await upgrade.run()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["before", "after"])
async def test_real_wire_commit_loss_stops_without_retry_or_resume(upgrade, monkeypatch, mode):
    original = migrate.apply_migration

    async def arm(conn, path, **kwargs):
        callback = kwargs["before_commit"]
        if path.name.startswith("045_"):

            def before():
                callback()
                upgrade.db.bridge.commit_fault = mode

            kwargs["before_commit"] = before
        await original(conn, path, **kwargs)

    monkeypatch.setattr(migrate, "apply_migration", arm)

    async def short_fault_deadline(**options):
        import asyncpg

        options["command_timeout"] = 1.0  # Test transport-fault cap; production remains 65s.
        return await asyncpg.connect(**options)

    with pytest.raises(driver.Refused):
        await upgrade.run(connect=short_fault_deadline)
    assert upgrade.db.bridge.commit_seen.is_set()
    upgrade.db.bridge.commit_fault = None
    assert events(upgrade)[-1]["commit_uncertain"] is True
    assert events(upgrade)[-1]["filename"].startswith("045_")
    # The connection was terminated; independently observe the actual server result.
    async with asyncio.timeout(3):
        while (
            upgrade.db.sql(
                f"SELECT count(*) FROM pg_stat_activity WHERE datname='{upgrade.db.name}' "
                "AND pid<>pg_backend_pid()"
            )
            != "0"
        ):
            await asyncio.sleep(0.01)
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == (
        "46" if mode == "after" else "45"
    )
    assert upgrade.db.sql("SELECT to_regclass('public.deletion_journal_binding') IS NULL") == "t"
    if mode == "after":
        assert upgrade.db.bridge.discarded_commit_bytes > 0
    with pytest.raises(driver.Refused, match="migration_state_mismatch"):
        await upgrade.run()


@pytest.mark.asyncio
async def test_lock_timeout_is_bounded_and_not_extended(upgrade):
    async with upgrade.db.connection("md_app") as blocker:
        transaction = blocker.transaction()
        await transaction.start()
        await blocker.execute("LOCK TABLE public.users IN ACCESS EXCLUSIVE MODE")
        try:
            with pytest.raises(driver.Refused) as caught:
                await upgrade.run()
            assert caught.value.sqlstate == "55P03"
        finally:
            await transaction.rollback()
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "44"


@pytest.mark.asyncio
async def test_stricter_048_statement_timeout_rolls_back_only_that_file(upgrade, monkeypatch):
    original = migrate.apply_migration

    async def stall(conn, path, **kwargs):
        if path.name.startswith("048_"):
            kwargs["sql_content"] += "\nSELECT pg_sleep(6);"
        await original(conn, path, **kwargs)

    monkeypatch.setattr(migrate, "apply_migration", stall)
    with pytest.raises(driver.Refused) as caught:
        await upgrade.run()
    assert caught.value.sqlstate == "57014"
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "48"
    assert upgrade.db.sql("SELECT to_regclass('public.compute_scope_metadata') IS NULL") == "t"


@pytest.mark.asyncio
async def test_post_schema_mismatch_never_reports_success_or_resumes(upgrade):
    with pytest.raises(driver.Refused, match="schema_after_mismatch"):
        await upgrade.run(replace(upgrade.target, schema_after="0" * 64))
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "51"
    assert events(upgrade)[-1]["event"] == "stopped"
    with pytest.raises(driver.Refused, match="migration_state_mismatch"):
        await upgrade.run()


@pytest.mark.asyncio
async def test_051_schema_without_bookkeeping_is_also_refused(upgrade):
    upgrade.db.sql("CREATE TABLE public.artifact_output_jobs(id integer)")
    with pytest.raises(driver.Refused, match="excluded_schema_present"):
        await upgrade.run()
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "44"


@pytest.mark.asyncio
async def test_missing_bookkeeping_is_never_recreated(upgrade):
    upgrade.db.sql("DROP TABLE public._migrations")
    with pytest.raises(driver.Refused) as caught:
        await upgrade.run()
    assert caught.value.sqlstate == "42P01"
    assert upgrade.db.sql("SELECT to_regclass('public._migrations') IS NULL") == "t"


@pytest.mark.asyncio
async def test_cancellation_preserves_original_exception_and_stops_partial_batch(
    upgrade, monkeypatch
):
    original = migrate.apply_migration
    reached = asyncio.Event()

    async def pause(conn, path, **kwargs):
        if path.name.startswith("045_"):
            reached.set()
            await asyncio.Event().wait()
        await original(conn, path, **kwargs)

    monkeypatch.setattr(migrate, "apply_migration", pause)
    job = asyncio.create_task(upgrade.run())
    await asyncio.wait_for(reached.wait(), 3)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert events(upgrade)[-1]["code"] == "operation_cancelled"
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "45"
    with pytest.raises(driver.Refused, match="migration_state_mismatch"):
        await upgrade.run()


@pytest.mark.asyncio
async def test_receipt_failure_after_commit_does_not_claim_rollback_or_continue(
    upgrade, monkeypatch
):
    original = driver.Receipt.append

    def unavailable(self, event, **facts):
        if event == "commit_acknowledged":
            raise OSError("synthetic-private-io")
        return original(self, event, **facts)

    monkeypatch.setattr(driver.Receipt, "append", unavailable)
    with pytest.raises(driver.Refused):
        await upgrade.run()
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "45"
    assert events(upgrade)[-1]["phase"] == "commit_acknowledged"
    assert events(upgrade)[-1]["commit_uncertain"] is False
    assert "private-io" not in upgrade.receipts[-1].read_text()


@pytest.mark.asyncio
async def test_unlock_ack_loss_is_not_success_and_never_masks_source_error(upgrade, monkeypatch):
    original = migrate.release_migration_lock

    async def lost(conn, **kwargs):
        await original(conn, **kwargs)
        raise TimeoutError("synthetic-private-release")

    monkeypatch.setattr(migrate, "release_migration_lock", lost)
    with pytest.raises(driver.Refused, match="operation_timeout"):
        await upgrade.run()
    assert upgrade.db.sql("SELECT count(*) FROM public._migrations") == "51"
    assert events(upgrade)[-1]["phase"] == "postflight"
    assert events(upgrade)[-1]["event"] == "stopped"
    assert "private-release" not in upgrade.receipts[-1].read_text()
