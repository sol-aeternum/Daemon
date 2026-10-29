"""Approved startup host regressions; all connections and journal I/O are mocked.

Actual principals, SQL privileges and journal identity are qualified separately by
network-none PostgreSQL tests. There are no expected failures or application DSNs.
"""

import importlib
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
import pytest

from orchestrator import db
from orchestrator.config import Settings
from orchestrator.entitlements import attestation
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.services.deletion_journal.errors import JournalError

worker = importlib.import_module("orchestrator.worker.worker")


@pytest.fixture
def host(monkeypatch):
    value = cast(Any, Settings)(
        _env_file=None,
        database_url="postgresql://synthetic@synthetic.invalid/fixture",
        redis_url=None,
        daemon_environment="development",
        daemon_auth_pepper="p" * 64,
        daemon_encryption_key=Fernet.generate_key().decode(),
    )
    pool, acceptor = AsyncMock(), AsyncMock()
    pool.fetchval.return_value = True  # positive pre-050 catalog evidence
    connect = AsyncMock(return_value=pool)
    barrier = AsyncMock()
    monkeypatch.setattr(db.asyncpg, "create_pool", connect)
    monkeypatch.setattr(db, "resolve_database_url", lambda _: value.database_url)
    monkeypatch.setattr(db, "validate_redis_account_key", lambda _: None)
    monkeypatch.setattr(db, "require_recovery_barrier", barrier)
    monkeypatch.setattr(worker, "get_settings", lambda: value)
    monkeypatch.setattr(worker, "apply_resolved_database_url", lambda _: None)
    monkeypatch.setattr(worker, "validate_database_credentials", lambda _: None)
    monkeypatch.setattr(worker, "require_recovery_barrier", barrier)
    pepper, start, stop = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(worker, "initialize_development_pepper", pepper)
    monkeypatch.setattr(attestation, "start", start)
    monkeypatch.setattr(attestation, "stop", stop)
    return SimpleNamespace(
        settings=value,
        pool=pool,
        acceptor=acceptor,
        connect=connect,
        barrier=barrier,
        pepper=pepper,
        start=start,
    )


def select(host, *, backend=False):
    host.settings.daemon_task_acceptor_role = "md_acceptor"
    host.settings.daemon_task_worker_role = "md_app"
    host.settings.daemon_deletion_journal_dir = "/synthetic/journal"
    host.settings.daemon_deletion_journal_id = "00000000-0000-0000-0000-000000000001"
    if backend:
        host.settings.daemon_task_acceptor_database_url = "postgresql://synthetic-A"


async def initialize(host, service):
    if service == "http":
        return await db.init_app_state(host.settings)
    ctx: dict[str, Any] = {}
    await worker.on_startup(ctx)
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["http", "worker"])
async def test_flag_on_startup_refuses_without_qualified_composition(host, service):
    host.settings.durable_chat_enabled = True
    with pytest.raises(ObservationBlocked, match="inference_configuration_required"):
        await initialize(host, service)
    host.connect.assert_not_awaited()
    host.barrier.assert_not_awaited()
    host.start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["http", "worker"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("daemon_task_acceptor_role", "md_acceptor"),
        ("daemon_task_worker_role", "md_app"),
        ("daemon_task_acceptor_role", "invalid role"),
    ],
)
async def test_partial_configuration_refuses_even_when_new_acceptance_off(
    host, service, field, value
):
    setattr(host.settings, field, value)
    with pytest.raises(ObservationBlocked):
        await initialize(host, service)
    host.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_refuses_acceptor_credential_without_opening_any_pool(host):
    select(host, backend=True)
    with pytest.raises(ObservationBlocked, match="inference_worker_acceptor_credentials"):
        await worker.on_startup({})
    host.connect.assert_not_awaited()
    assert "synthetic-A" not in repr(host.settings)


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["http", "worker"])
async def test_removed_configuration_cannot_downgrade_migrated_host(host, service):
    host.pool.fetchval.return_value = False  # 050 exists, no task content queried
    with pytest.raises(ObservationBlocked, match="inference_configuration_required"):
        await initialize(host, service)
    host.pool.close.assert_awaited_once()
    host.barrier.assert_not_awaited()
    host.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_backend_flag_off_keeps_both_owned_pools_and_qualified_controls(host, monkeypatch):
    select(host, backend=True)
    host.connect.side_effect = [host.pool, host.acceptor]
    bundle = SimpleNamespace(tasks=SimpleNamespace(_memory=object()))
    qualify = AsyncMock(return_value=bundle)
    monkeypatch.setattr(db, "qualify_task_composition", qualify)
    state = await db.init_app_state(host.settings)
    try:
        assert state.task_composition is bundle
        assert state.db_pool is host.pool and state.task_acceptor_pool is host.acceptor
        assert state.memory_store is bundle.tasks._memory
        assert host.settings.durable_chat_enabled is False
        assert qualify.await_args is not None
        assert qualify.await_args.kwargs["worker_pool"] is host.pool
        assert qualify.await_args.kwargs["acceptor_pool"] is host.acceptor
        host.barrier.assert_not_awaited()  # pair factory owns barrier ordering
    finally:
        await db.close_app_state(state)
    host.pool.close.assert_awaited_once()
    host.acceptor.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connect", "qualify", "cancel"])
async def test_backend_partial_failure_unwinds_every_created_pool(host, monkeypatch, failure):
    import asyncio

    select(host, backend=True)
    if failure == "connect":
        host.connect.side_effect = [host.pool, OSError("synthetic-sensitive-DSN")]
        error = JournalError
    else:
        host.connect.side_effect = [host.pool, host.acceptor]
        exception = asyncio.CancelledError() if failure == "cancel" else ObservationBlocked()
        monkeypatch.setattr(db, "qualify_task_composition", AsyncMock(side_effect=exception))
        error = asyncio.CancelledError if failure == "cancel" else ObservationBlocked
    with pytest.raises(error) as exc:
        await db.init_app_state(host.settings)
    assert "sensitive" not in str(exc.value)
    host.pool.close.assert_awaited_once()
    if failure != "connect":
        host.acceptor.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_flag_off_uses_only_w_and_admits_jobs_after_qualification(host, monkeypatch):
    from orchestrator.tasks import composition

    select(host)
    bundle = SimpleNamespace(memory=object(), tasks=object())
    qualify = AsyncMock(return_value=bundle)
    monkeypatch.setattr(composition, "qualify_worker_composition", qualify)
    ctx: dict[str, Any] = {}
    await worker.on_startup(ctx)
    try:
        assert ctx["task_composition"] is bundle
        assert ctx["store"] is bundle.memory and ctx["task_store"] is bundle.tasks
        assert host.settings.durable_chat_enabled is False
        assert not host.settings.daemon_task_acceptor_database_url
        host.connect.assert_awaited_once()
        assert qualify.await_args is not None
        assert set(qualify.await_args.kwargs) == {
            "enabled",
            "worker_pool",
            "encryption",
            "settings",
            "acceptor_role",
            "worker_role",
        }
        assert qualify.await_args is not None
        assert qualify.await_args.kwargs["worker_pool"] is host.pool
        host.barrier.assert_not_awaited()
        host.start.assert_awaited_once_with(host.pool)
    finally:
        await worker.on_shutdown(ctx)
    host.pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_unbound_failure_closes_w_before_any_job_admission(host, monkeypatch):
    from orchestrator.tasks import composition

    select(host)
    monkeypatch.setattr(
        composition,
        "qualify_worker_composition",
        AsyncMock(side_effect=JournalError("binding_required")),
    )
    ctx: dict[str, Any] = {}
    with pytest.raises(JournalError, match="binding_required"):
        await worker.on_startup(ctx)
    assert ctx["task_store"] is None and ctx["task_composition"] is None
    assert "db_pool" not in ctx
    host.pool.close.assert_awaited_once()
    host.start.assert_not_awaited()
    host.pepper.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["http", "worker"])
async def test_only_positive_pre050_catalog_evidence_allows_unconfigured_legacy_startup(
    host, service
):
    result = await initialize(host, service)
    try:
        host.barrier.assert_awaited_once_with(host.pool, host.settings)
        if service == "http":
            assert isinstance(result, db.AppState)
            assert result.task_composition is None
            assert result.task_acceptor_pool is None
        else:
            assert isinstance(result, dict)
            assert result["task_composition"] is None
            host.pepper.assert_awaited_once_with(host.settings, host.pool)
            host.start.assert_awaited_once_with(host.pool)
    finally:
        if isinstance(result, db.AppState):
            await db.close_app_state(result)
        else:
            await worker.on_shutdown(result)
    host.pool.close.assert_awaited_once()
