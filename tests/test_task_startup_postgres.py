"""Separate real HTTP/worker startup against an owned network-none database.

Uses the pre-existing cached-image harness and synthetic principals/journal only.
No application DSN, provider credential, migration or service is accessed.
"""

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import uuid4

import pytest
import pytest_asyncio

from orchestrator import db as database
from orchestrator.entitlements import attestation
from orchestrator.routes.tasks import task_store
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.store import initialize, Journal
from tests.deletion_journal_support import request
from tests.test_inference_recovery_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_task_composition_postgres import settings

worker = importlib.import_module("orchestrator.worker.worker")


def dsn(db, principal):
    return f"postgresql://{principal}@/{db.name}?{urlencode({'host': str(db.host)})}"


@pytest_asyncio.fixture
async def services(db, tmp_path, monkeypatch):
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    value = settings()
    value.daemon_environment = "development"
    value.database_url = dsn(db, "md_app")
    value.daemon_task_acceptor_database_url = dsn(db, "md_acceptor")
    value.daemon_task_acceptor_role = "md_acceptor"
    value.daemon_task_worker_role = "md_app"
    value.daemon_deletion_journal_dir = str(path)
    value.daemon_deletion_journal_id = str(identity)
    # Backend alone may initialize the J1 binding. W has no such SQL capability.
    db.sql("REVOKE INSERT,UPDATE,DELETE ON public.deletion_journal_binding FROM md_app")
    wvalue = value.model_copy(update={"daemon_task_acceptor_database_url": None})
    monkeypatch.setattr(worker, "get_settings", lambda: wvalue)
    start = AsyncMock()
    monkeypatch.setattr(attestation, "start", start)
    monkeypatch.setattr(attestation, "stop", AsyncMock())
    yield SimpleNamespace(
        db=db, path=path, identity=identity, settings=value, worker_settings=wvalue, start=start
    )


@pytest.mark.asyncio
async def test_worker_refuses_unbound_journal_with_no_first_binding_privilege(services):
    ctx = {}
    with pytest.raises(JournalError, match="binding_required"):
        await worker.on_startup(ctx)
    assert ctx["task_composition"] is None and "db_pool" not in ctx
    services.start.assert_not_awaited()
    assert (
        services.db.cluster.sql(
            "SELECT count(*) FROM public.deletion_journal_binding", database=services.db.name
        )
        == "0"
    )
    assert (
        services.db.cluster.sql(
            f"SELECT count(*) FROM pg_stat_activity WHERE datname='{services.db.name}'"
        )
        == "0"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("new_acceptance", [False, True])
async def test_actual_backend_pair_and_separate_worker_startup(services, new_acceptance):
    services.settings.durable_chat_enabled = new_acceptance
    services.worker_settings.durable_chat_enabled = new_acceptance
    state = await database.init_app_state(services.settings)
    ctx = {}
    try:
        await worker.on_startup(ctx)
        assert state.task_composition is not None
        assert state.memory_store is state.task_composition.tasks._memory
        assert ctx["task_composition"].pool is ctx["db_pool"]
        assert ctx["task_composition"].settings.daemon_task_acceptor_database_url is None
        assert not hasattr(ctx["task_composition"], "acceptor_pool")
        assert not hasattr(ctx["task_composition"], "acceptor_memory")
        assert ctx["db_pool"] is not state.db_pool
        assert await ctx["db_pool"].fetchval("SELECT CURRENT_USER") == "md_app"
        assert state.task_acceptor_pool is not None
        assert await state.task_acceptor_pool.fetchval("SELECT CURRENT_USER") == "md_acceptor"
        assert (
            await ctx["db_pool"].fetchval("SELECT journal_id FROM public.deletion_journal_binding")
            == services.identity
        )
        if not new_acceptance:
            with pytest.raises(ObservationBlocked, match="inference_composition_refused"):
                await state.task_composition.preflight()
        services.start.assert_awaited_once()
    finally:
        if "db_pool" in ctx:
            await worker.on_shutdown(ctx)
        await database.close_app_state(state)


@pytest.mark.asyncio
async def test_flag_off_restart_keeps_existing_observation_stop_and_no_new_acceptance(services):
    state = await database.init_app_state(services.settings)
    ctx = {}
    try:
        async with services.db.connection() as owner:
            account = await services.db.account(owner)
        assert state.task_composition is not None
        options: dict[str, Any] = dict(
            user_id=account,
            conversation_id=None,
            new_conversation_title="Synthetic",
            pipeline="cloud",
            user_message="Retain my task",
            task_input={"message": "synthetic"},
            request_hash="a" * 64,
            idempotency_key=uuid4().hex,
            assistant_model=None,
            captured_generation=CapturedGeneration(account, 0),
        )
        accepted = await state.task_composition.acceptor.accept(**options)
        services.settings.durable_chat_enabled = False
        services.worker_settings.durable_chat_enabled = False
        await worker.on_startup(ctx)
        snapshot = await ctx["task_store"].snapshot(account, accepted.task_id)
        assert snapshot is not None and snapshot.task_id == accepted.task_id
        assert await ctx["task_store"].snapshot(uuid4(), accepted.task_id) is None
        await ctx["task_store"].request_cancel(account, accepted.task_id)
        stopped = await task_store(state).snapshot(account, accepted.task_id)
        assert stopped is not None and stopped.status.value == "cancelled"
        with pytest.raises(ObservationBlocked, match="inference_composition_refused"):
            await state.task_composition.acceptor.accept(
                **{**options, "idempotency_key": uuid4().hex}
            )
        await worker.on_shutdown(ctx)
        ctx = {}
        await worker.on_startup(ctx)
        restored = await ctx["task_store"].snapshot(account, accepted.task_id)
        assert restored is not None and restored.status.value == "cancelled"
        assert (
            services.db.cluster.sql(
                "SELECT count(*) FROM public.task_inference_recovery", database=services.db.name
            )
            == "1"
        )
    finally:
        if "db_pool" in ctx:
            await worker.on_shutdown(ctx)
        await database.close_app_state(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["identity", "pending"])
async def test_worker_refuses_changed_or_pending_journal_after_backend_binding(services, damage):
    state = await database.init_app_state(services.settings)
    await database.close_app_state(state)
    if damage == "identity":
        services.worker_settings.daemon_deletion_journal_id = str(uuid4())
    else:
        with Journal(services.path, services.identity) as journal:
            journal.append(request(), uuid4(), None, None)
    with pytest.raises(JournalError, match="marker_mismatch|recovery_required"):
        await worker.on_startup({})
    assert (
        services.db.cluster.sql(
            "SELECT count(*) FROM public.deletion_journal_applied", database=services.db.name
        )
        == "0"
    )
    services.start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "GRANT SELECT ON public.task_inference_recovery TO md_app",
        "GRANT UPDATE ON public.task_inference_recovery TO md_acceptor",
        "REVOKE INSERT ON public.messages FROM md_acceptor",
        "ALTER ROLE md_app REPLICATION",
        "ALTER ROLE md_acceptor CREATEDB",
    ],
)
async def test_current_role_catalog_damage_refuses_before_any_backend_binding(services, sql):
    # Only the fixture cluster administrator may change role attributes.
    services.db.cluster.sql(sql, database=services.db.name)
    try:
        with pytest.raises(ObservationBlocked):
            await database.init_app_state(services.settings)
        assert (
            services.db.cluster.sql(
                "SELECT count(*) FROM public.deletion_journal_binding", database=services.db.name
            )
            == "0"
        )
    finally:
        services.db.cluster.sql("ALTER ROLE md_app NOREPLICATION")
        services.db.cluster.sql("ALTER ROLE md_acceptor NOCREATEDB")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "GRANT UPDATE ON public.task_inference_recovery TO md_acceptor",
        "REVOKE INSERT ON public.messages FROM md_acceptor",
        "GRANT SELECT ON public.task_inference_recovery TO md_app",
        "GRANT md_app TO md_acceptor",
    ],
)
async def test_worker_restart_rechecks_current_catalog_not_old_binding(services, sql):
    state = await database.init_app_state(services.settings)
    await database.close_app_state(state)
    services.db.cluster.sql(sql, database=services.db.name)
    try:
        with pytest.raises(ObservationBlocked):
            await worker.on_startup({})
        services.start.assert_not_awaited()
    finally:
        services.db.cluster.sql("REVOKE md_app FROM md_acceptor")
