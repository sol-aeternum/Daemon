"""Disposable source SQL catalog and original native execution checks."""

from pathlib import Path

import asyncpg
import pytest

from orchestrator.services.restore_history.model import HistoryError, Identity, key_check
from orchestrator.services.restore_history.terminal_projection import (
    InboxSourcesHistoryProjection,
    InboxResearchHistoryProjection,
)
from tests.restore_history_support import HISTORY, KEY
from tests.test_history_acceptance_host_postgres import install_terminal
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_composition_postgres import deadline
from tests.test_history_effects_postgres import install


SQL = Path(__file__).parent / "fixtures" / "inbox_sources_v1.sql"


def install_sources(database):
    database.sql(
        "BEGIN;"
        + SQL.read_text()
        + "GRANT EXECUTE ON FUNCTION public.task_history_source_project(bytea) TO md_app;"
        + "GRANT EXECUTE ON FUNCTION public.task_history_source_page(uuid,bigint,text) TO md_app,md_acceptor;"
        + "COMMIT;"
    )


@pytest.mark.asyncio
async def test_source_acl_and_closed_catalog(projection_db):
    database = projection_db
    install(database)
    install_terminal(database)
    install_sources(database)
    identity = Identity(HISTORY, key_check(KEY, HISTORY))
    projection = InboxSourcesHistoryProjection(
        identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    old = InboxResearchHistoryProjection(
        identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    for role in ("md_app", "md_acceptor"):
        async with database.connection(role) as conn:
            async with conn.transaction():
                assert await projection.page(conn, after_seq=0, deadline=deadline()) == ()
            async with conn.transaction():
                with pytest.raises(HistoryError):
                    await old.qualify(conn, writing=False, deadline=deadline())
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchval("SELECT public.task_history_decode_source($1)", b"invalid")
    async with database.connection("md_acceptor") as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchrow("SELECT * FROM public.task_history_source_project($1)", b"invalid")
    async with database.connection("md_app") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetchrow("SELECT * FROM public.task_history_source_project($1)", b"invalid")
    assert database.sql("SELECT count(*) FROM task_history_projection") == "0"
    database.sql(
        "GRANT EXECUTE ON FUNCTION public.task_history_source_project(bytea) TO md_acceptor"
    )
    async with database.connection("md_app") as conn:
        async with conn.transaction():
            with pytest.raises(HistoryError, match="projection_catalog_refused"):
                await projection.page(conn, after_seq=0, deadline=deadline())
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetch(
                "SELECT * FROM public.task_history_source_page($1,0,$2)",
                HISTORY,
                identity.key_check,
            )


@pytest.mark.asyncio
async def test_source_install_refuses_predecessor_acl_drift(projection_db):
    database = projection_db
    install(database)
    install_terminal(database)
    database.sql(
        "GRANT EXECUTE ON FUNCTION public.task_history_terminal_project(bytea) TO md_acceptor"
    )
    with pytest.raises(AssertionError, match="task_history_refused"):
        install_sources(database)
    assert (
        database.sql("SELECT count(*) FROM pg_proc WHERE proname='task_history_source_project'")
        == "0"
    )
