"""Health endpoint regression tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.config import Settings
from orchestrator.db import AppState, check_db_health
import orchestrator.main as main_module

_SENSITIVE_DETAIL = "internal health exception: db-node-47"


@pytest.mark.asyncio
async def test_service_health_redacts_backend_exceptions() -> None:
    state = AppState(settings=Settings(daemon_environment="development"))

    state.db_pool = MagicMock()
    state.db_pool.acquire.side_effect = RuntimeError(_SENSITIVE_DETAIL)
    state.redis = AsyncMock()
    state.redis.ping.side_effect = RuntimeError(_SENSITIVE_DETAIL)

    result = await check_db_health(state)

    assert result == {"postgres": "error", "redis": "error"}
    assert _SENSITIVE_DETAIL not in repr(result)


@pytest.mark.asyncio
async def test_health_endpoint_redacts_unexpected_exceptions(monkeypatch) -> None:
    def get_failing_state(_request):
        raise RuntimeError(_SENSITIVE_DETAIL)

    monkeypatch.setattr(main_module, "get_app_state", get_failing_state)

    result = await main_module.health(MagicMock())

    assert result == {"status": "degraded", "error": "Health check unavailable"}
    assert _SENSITIVE_DETAIL not in repr(result)
