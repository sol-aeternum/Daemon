"""Execution-path startup negatives; no test-mode bypass in production."""

from __future__ import annotations

import asyncio
import importlib
import threading
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from orchestrator import db
from orchestrator.config import Settings
from orchestrator.services.deletion_journal import barrier
from orchestrator.services.deletion_journal.barrier import (
    configured_journal,
    require_recovery_barrier,
)
from orchestrator.services.deletion_journal.errors import JournalError

worker = importlib.import_module("orchestrator.worker.worker")


def settings(**kwargs):
    return cast(Any, Settings)(
        _env_file=None, daemon_encryption_key=Fernet.generate_key().decode(), **kwargs
    )


@pytest.mark.parametrize(
    "directory,identity",
    [
        ("/private/journal", None),
        (None, str(uuid4())),
        ("relative", str(uuid4())),
        ("/private/journal", "bad"),
    ],
)
def test_half_or_malformed_settings_fail(directory, identity):
    with pytest.raises(JournalError, match="invalid_config"):
        configured_journal(
            settings(daemon_deletion_journal_dir=directory, daemon_deletion_journal_id=identity)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_no_pool_cannot_claim_no_journal(enabled):
    value = settings()
    if enabled:
        value.daemon_deletion_journal_dir = "/private/journal"
        value.daemon_deletion_journal_id = str(uuid4())
    with pytest.raises(JournalError, match="database_required"):
        await require_recovery_barrier(None, value)


@pytest.mark.asyncio
async def test_backend_and_worker_without_dsn_refuse_before_connecting(monkeypatch):
    value = settings(database_url=None, redis_url=None)
    monkeypatch.setattr(db, "validate_redis_account_key", lambda _: None)
    monkeypatch.setattr(db, "resolve_database_url", lambda _: None)
    connect = AsyncMock()
    monkeypatch.setattr(db.asyncpg, "create_pool", connect)
    with pytest.raises(JournalError, match="database_required"):
        await db.init_app_state(value)
    monkeypatch.setattr(worker, "get_settings", lambda: value)
    monkeypatch.setattr(worker, "apply_resolved_database_url", lambda _: None)
    with pytest.raises(JournalError, match="database_required"):
        await worker.on_startup({})
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_actual_lifespan_never_yields_after_failed_barrier(monkeypatch):
    from orchestrator import main

    value, pool = settings(database_url="postgresql://fictional", redis_url=None), AsyncMock()
    monkeypatch.setattr(main, "get_settings", lambda: value)
    monkeypatch.setattr(main, "_validate_startup_config", lambda _: None)
    monkeypatch.setattr(db, "validate_redis_account_key", lambda _: None)
    monkeypatch.setattr(db.asyncpg, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(
        db, "require_recovery_barrier", AsyncMock(side_effect=JournalError("recovery_required"))
    )
    pepper = AsyncMock()
    monkeypatch.setattr(main, "initialize_development_pepper", pepper)
    with pytest.raises(JournalError, match="recovery_required"):
        async with main.lifespan(main.app):
            pytest.fail("lifespan yielded despite pending replay")
    pepper.assert_not_called()
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_backend_connection_failure_no_longer_degrades(monkeypatch, enabled):
    value = settings(database_url="postgresql://fictional", redis_url=None)
    if enabled:
        value.daemon_deletion_journal_dir = "/private/journal"
        value.daemon_deletion_journal_id = str(uuid4())
    monkeypatch.setattr(db, "validate_redis_account_key", lambda _: None)
    connect = AsyncMock(side_effect=OSError("fictional-sensitive-error"))
    redis = AsyncMock()
    monkeypatch.setattr(db.asyncpg, "create_pool", connect)
    monkeypatch.setattr(db, "arq_create_pool", redis)
    with pytest.raises(JournalError, match="database_required") as error:
        await db.init_app_state(value)
    assert "sensitive" not in str(error.value)
    redis.assert_not_called()


@pytest.mark.asyncio
async def test_backend_barrier_failure_closes_pool_before_consumers(monkeypatch):
    value = settings(database_url="postgresql://fictional", redis_url=None)
    pool = AsyncMock()
    monkeypatch.setattr(db, "validate_redis_account_key", lambda _: None)
    monkeypatch.setattr(db.asyncpg, "create_pool", AsyncMock(return_value=pool))
    guard = AsyncMock(side_effect=JournalError("recovery_required"))
    monkeypatch.setattr(db, "require_recovery_barrier", guard)
    store = AsyncMock()
    monkeypatch.setattr(db, "MemoryStore", store)
    with pytest.raises(JournalError, match="recovery_required"):
        await db.init_app_state(value)
    guard.assert_awaited_once_with(pool, value)
    pool.close.assert_awaited_once()
    store.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_worker_connection_failure_runs_no_pepper_or_job(monkeypatch, enabled):
    value = settings(database_url="postgresql://fictional")
    if enabled:
        value.daemon_deletion_journal_dir = "/private/journal"
        value.daemon_deletion_journal_id = str(uuid4())
    monkeypatch.setattr(worker, "get_settings", lambda: value)
    monkeypatch.setattr(worker.asyncpg, "create_pool", AsyncMock(side_effect=OSError("fictional")))
    pepper = AsyncMock()
    monkeypatch.setattr(worker, "initialize_development_pepper", pepper)
    with pytest.raises(JournalError, match="database_required"):
        await worker.on_startup({})
    pepper.assert_not_called()


@pytest.mark.asyncio
async def test_worker_failed_barrier_closes_pool_and_runs_no_sideeffects(monkeypatch):
    value, pool = settings(database_url="postgresql://fictional"), AsyncMock()
    monkeypatch.setattr(worker, "get_settings", lambda: value)
    monkeypatch.setattr(worker.asyncpg, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(
        worker, "require_recovery_barrier", AsyncMock(side_effect=JournalError("corrupt"))
    )
    pepper = AsyncMock()
    monkeypatch.setattr(worker, "initialize_development_pepper", pepper)
    with pytest.raises(JournalError, match="corrupt"):
        await worker.on_startup({})
    pepper.assert_not_called()
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_filesystem_thread_is_not_abandoned_on_repeated_cancellation():
    started, finish, released = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        assert finish.wait(5)
        released.set()
        return 1

    task = asyncio.create_task(barrier._io(work))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not released.is_set()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set()
