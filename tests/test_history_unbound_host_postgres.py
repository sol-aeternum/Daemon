"""#731/#733: actual unselected backend and worker startup on unbound H.

Real PostgreSQL (``--compute-metadata-postgres``) migrated by the runner through
the latest file (H and the 073 fence included). A and W are provisioned by
``scripts/provision_task_roles.apply`` from the exact maps, so the hosts run
with production-shaped restricted privileges and the real ``NativeFence``.
Synthetic roles, journal and database only; no live role, data or service.
"""

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlencode
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException, Response

from orchestrator import db as database
from orchestrator.entitlements import attestation
from orchestrator.routes import tasks as routes
from orchestrator.services.deletion_journal.store import initialize
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.native_fence import FENCE_KEY
from scripts import provision_task_roles as provision
from tests.test_compute_metadata_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_history_native_fence_postgres import BIND, HOLDERS
from tests.test_task_composition_postgres import settings
from tests.test_task_role_provisioning_chain_postgres import (
    PASSWORDS,
    apply,
    drop_roles,
    migrated,
    names,
)
from unittest.mock import AsyncMock

worker = importlib.import_module("orchestrator.worker.worker")


def dsn(db, role: str, password: str) -> str:
    return f"postgresql://{role}:{password}@/{db.name}?{urlencode({'host': str(db.host)})}"


async def holders(db) -> int:
    async with db.connection("postgres") as admin:
        return await admin.fetchval(HOLDERS, *FENCE_KEY)


async def sessions(db, *roles: str) -> int:
    async with db.connection("postgres") as admin:
        return await admin.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE usename = ANY($1::name[])", list(roles)
        )


async def until(probe, expected: int) -> None:
    """Wait for the server to observe session ends (a condition, not a sleep)."""
    async with asyncio.timeout(10):
        while await probe() != expected:
            await asyncio.sleep(0.05)


async def bind(db) -> None:
    async with db.connection() as owner:
        await owner.execute(BIND)


async def refused_bind(db) -> None:
    with pytest.raises(asyncpg.PostgresError, match="history_native_hosts_active"):
        await bind(db)
    async with db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM task_history_binding") == 0


@pytest.mark.asyncio
async def test_actual_hosts_start_on_unbound_h_fence_binding_and_refuse_once_bound(
    db, tmp_path, monkeypatch
):
    await migrated(db)  # the runner's complete chain, 073 included, unbound
    acceptor, worker_role = names()
    async with db.connection() as owner:
        # The metadata fixture (not any migration) grants its own md_app EXECUTE
        # on the compute_metadata functions; composition rightly refuses any
        # grantee besides A/W, so remove that fixture-only third principal.
        await owner.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM md_app")
    async with db.connection("postgres") as superuser:
        await apply(superuser, acceptor, worker_role)
        await provision.verify_deployment(superuser, acceptor, worker_role)
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    value = settings()
    value.daemon_environment = "development"
    value.database_url = dsn(db, worker_role, PASSWORDS[1])
    value.daemon_task_acceptor_database_url = dsn(db, acceptor, PASSWORDS[0])
    value.daemon_task_acceptor_role = acceptor
    value.daemon_task_worker_role = worker_role
    value.daemon_deletion_journal_dir = str(path)
    value.daemon_deletion_journal_id = str(identity)
    wvalue = value.model_copy(update={"daemon_task_acceptor_database_url": None})
    monkeypatch.setattr(worker, "get_settings", lambda: wvalue)
    monkeypatch.setattr(attestation, "start", AsyncMock())
    monkeypatch.setattr(attestation, "stop", AsyncMock())
    state: Any = None
    ctx: dict[str, Any] = {}
    try:
        # Backend alone: composition, J1 recovery barrier and a native by-key read.
        state = await database.init_app_state(value)
        assert state.native_fence is not None and state.native_fence.alive
        assert state.task_composition is not None
        assert await state.db_pool.fetchval("SELECT CURRENT_USER") == worker_role
        assert await state.task_acceptor_pool.fetchval("SELECT CURRENT_USER") == acceptor
        assert (
            await state.db_pool.fetchval("SELECT journal_id FROM public.deletion_journal_binding")
            == identity
        )  # the barrier ran and A created the first J1 binding
        with pytest.raises(HTTPException) as raised:
            await routes.get_task_by_key(
                "stable",
                Response(),
                app_state=state,
                auth=cast(Any, SimpleNamespace(user_id=uuid4())),
            )
        assert raised.value.status_code == 404  # answered natively, not 503
        await until(lambda: holders(db), 1)  # presence only; pooled locks released
        await refused_bind(db)

        # Worker joins: its own presence and native recovery qualification.
        await worker.on_startup(ctx)
        assert ctx["task_composition"] is not None
        assert ctx["native_fence"].alive
        assert await ctx["db_pool"].fetchval("SELECT CURRENT_USER") == worker_role
        await until(lambda: holders(db), 2)
        await refused_bind(db)

        # Backend shuts down: the worker alone still fences the binder.
        await database.close_app_state(state)
        state = None
        await until(lambda: holders(db), 1)
        await refused_bind(db)

        # Worker shuts down: every lock and session is released.
        await worker.on_shutdown(ctx)
        ctx = {}
        await until(lambda: holders(db), 0)
        await until(lambda: sessions(db, acceptor, worker_role), 0)
        await bind(db)

        # Bound H: neither actual host starts, and neither leaves a session.
        with pytest.raises(HistoryError, match="history_configuration_required"):
            await database.init_app_state(value)
        with pytest.raises(HistoryError, match="history_configuration_required"):
            await worker.on_startup({})
        await until(lambda: holders(db), 0)
        await until(lambda: sessions(db, acceptor, worker_role), 0)
    finally:
        if "db_pool" in ctx:
            await worker.on_shutdown(ctx)
        if state is not None:
            await database.close_app_state(state)
        await drop_roles(db, acceptor, worker_role)
