"""Both actual startup entry points enforce 063 without accessing any database."""

import importlib
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from orchestrator.config import Settings
from orchestrator.entitlements import attestation
from orchestrator.memory.startup import require_multi_query_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["http", "worker"])
@pytest.mark.parametrize("enabled,present", [(True, True), (True, False), (False, False)])
@pytest.mark.parametrize("logging", [False, True])
async def test_startup_schema_guard_in_both_services(
    monkeypatch, service, enabled, present, logging
):
    settings = cast(Any, Settings)(
        _env_file=None,
        database_url="postgresql://synthetic.invalid/fixture",
        daemon_encryption_key=Fernet.generate_key().decode(),
        memory_multi_query_enabled=enabled,
        retrieval_logging_enabled=logging,
    )
    pool = AsyncMock()
    pool.fetchval.return_value = present
    if service == "http":
        from orchestrator import main

        state = SimpleNamespace(db_pool=pool, history_runtime=object())
        monkeypatch.setattr(main, "get_settings", lambda: settings)
        monkeypatch.setattr(main, "apply_resolved_database_url", lambda _: None)
        monkeypatch.setattr(main, "_validate_startup_config", lambda _: None)
        monkeypatch.setattr(main, "init_app_state", AsyncMock(return_value=state))
        close = AsyncMock()
        monkeypatch.setattr(main, "close_app_state", close)
        monkeypatch.setattr(main, "initialize_development_pepper", AsyncMock())
        monkeypatch.setattr(main, "_check_first_boot_setup", AsyncMock())

        async def start():
            async with main.lifespan(main.app):
                assert not enabled or present

    else:
        from orchestrator.tasks import startup

        worker = importlib.import_module("orchestrator.worker.worker")
        monkeypatch.setattr(worker, "get_settings", lambda: settings)
        monkeypatch.setattr(worker, "apply_resolved_database_url", lambda _: None)
        monkeypatch.setattr(worker, "validate_database_credentials", lambda _: None)
        monkeypatch.setattr(worker.asyncpg, "create_pool", AsyncMock(return_value=pool))
        monkeypatch.setattr(startup, "require_legacy_task_schema", AsyncMock())
        monkeypatch.setattr(worker, "require_unselected_history", AsyncMock())
        monkeypatch.setattr(worker, "require_recovery_barrier", AsyncMock())
        monkeypatch.setattr(worker, "initialize_development_pepper", AsyncMock())
        monkeypatch.setattr(attestation, "start", AsyncMock())
        close = pool.close

        async def start():
            await worker.on_startup({})

    if enabled and not present:
        with pytest.raises(RuntimeError, match=r"migration 063 .*retrieval_log.query_role"):
            await start()
        close.assert_awaited_once()
    else:
        await start()
    if enabled:
        pool.fetchval.assert_awaited_once()
        assert "pg_catalog.pg_attribute" in pool.fetchval.await_args.args[0]
        assert "query_role" in pool.fetchval.await_args.args[0]
    else:
        pool.fetchval.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pool", [None, SimpleNamespace(fetchval=AsyncMock(return_value=None))])
async def test_unknown_schema_fails_closed(pool):
    with pytest.raises(RuntimeError, match="migration 063"):
        await require_multi_query_schema(
            pool, cast(Any, SimpleNamespace(memory_multi_query_enabled=True))
        )
