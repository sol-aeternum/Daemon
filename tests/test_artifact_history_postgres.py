"""Owned PostgreSQL/POSIX artifact prepare qualification; never an app DSN."""

from pathlib import Path
import time

import pytest
import pytest_asyncio

from orchestrator.services.restore_history.artifact_projection import ArtifactHistoryProjection
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.model import ArtifactPrepare, HistoryError
from orchestrator.services.restore_history.projection import compare_projections

from tests import test_history_output_phases_postgres as q

metadata_cluster = q.metadata_cluster
db = q.db
projection_db = q.projection_db
scenario = q.scenario
effects = q.effects
reserved = q.reserved
presenter = q.presenter
reservation_owner = q.reservation_owner
preparer = q.preparer
phase_owner = q.phase_owner

SQL = Path(__file__).resolve().parents[1] / "migrations/064_artifact_restore_prepare.sql"


@pytest_asyncio.fixture
async def artifact_owner(phase_owner):
    s = phase_owner
    await q.phase(s, "preparation")
    await q.phase(s, "reservation")
    assert not s.effects._blocked and not s.history._blocked
    await s.history.aclose(deadline=time.monotonic() + 3)
    s.db.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    s.db.sql(
        "GRANT EXECUTE ON FUNCTION task_history_artifact_project(bytea) TO reserved_p;"
        "GRANT EXECUTE ON FUNCTION task_history_output_page(uuid,bigint,text) TO md_app,md_acceptor;"
    )
    s.history = HistoryCoordinator(s.path, s.identity, s.ownership, profile="artifact-v2")
    await s.history.open(deadline=time.monotonic() + 3)
    try:
        s.projection = ArtifactHistoryProjection(
            s.identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
        )
        s.effects = HistoryEffects(s.worker, s.history, s.projection)
        yield s
    finally:
        await s.history.aclose(deadline=time.monotonic() + 3)


async def read_facts(s, role="md_app"):
    async with s.db.connection(role) as conn:
        async with conn.transaction():
            return await s.projection.page(conn, after_seq=0, deadline=time.monotonic() + 2)


@pytest.mark.asyncio
async def test_artifact_prepare_and_catalog_projection_commit_together(artifact_owner):
    s = artifact_owner
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
    result = await q.phase(s, "catalog")
    assert result is not None and result.version_id == s.command.binding.version_id
    async with s.history.operation(deadline=time.monotonic() + 2) as operation:
        state = await operation.verify()
    assert len(state.entries) == 2 and type(state.entries[-1].command.data) is ArtifactPrepare
    facts = await read_facts(s)
    assert len(facts) == 2
    report = compare_projections(s.identity.history_id, state, facts, profile="artifact-v2")
    assert not (report.missing or report.extra or report.mismatched or report.restore_required)
    assert len(await read_facts(s, "md_acceptor")) == 2
    assert q.r.p.r.rows(s) == "1:1:1:0"


@pytest.mark.asyncio
async def test_historical_catalog_cannot_be_refreshed_as_new_work(artifact_owner):
    s = artifact_owner
    await q.phase(s, "catalog")
    before = q.state(s)
    with pytest.raises(HistoryError, match="historical_artifact"):
        await q.phase(s, "catalog")
    assert q.state(s) == before and s.effects._blocked and s.history._blocked


@pytest.mark.asyncio
async def test_visible_native_subtransaction_rows_are_uncommitted_until_top_commit(db):
    db.sql("""
    CREATE TABLE synthetic_native_insert(value integer);
    CREATE FUNCTION synthetic_native_stage() RETURNS void LANGUAGE plpgsql AS $$
    BEGIN INSERT INTO synthetic_native_insert VALUES(1);
    EXCEPTION WHEN unique_violation THEN RAISE; END $$;
    """)
    async with db.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT synthetic_native_stage()")
            status = await conn.fetchval("""
            SELECT pg_xact_status((pg_current_xact_id()::text::numeric +
            mod(xmin::text::numeric - mod(pg_current_xact_id()::text::numeric,4294967296)
            +4294967296,4294967296))::text::xid8) FROM synthetic_native_insert
            """)
            assert status == "in progress"
        assert (
            await conn.fetchval(
                "SELECT pg_xact_status(xmin::text::xid8) FROM synthetic_native_insert"
            )
            == "committed"
        )
