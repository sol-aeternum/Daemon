"""The video tool must refuse unqualified routes before spending credits."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from orchestrator.compute_runtime import ComputeScope, _scope
from orchestrator.entitlements import EntitlementService
from orchestrator.subagents.image import ImageSubagent


@pytest.mark.asyncio
async def test_video_missing_account_scope_does_not_charge_or_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_pool = object()
    agent = ImageSubagent({"openrouter_api_key": "test-key", "db_pool": db_pool})
    debit = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr("db.video_credits.VideoCreditsDAL.debit_credits", debit)
    monkeypatch.setattr("orchestrator.subagents.image.FalKlingProvider.generate_video", dispatch)

    result = await agent.execute(
        "private prompt",
        {"mode": "video", "user_id": str(uuid.uuid4()), "video_provider": "fal", "duration": 5},
    )
    assert result.success is False
    assert "Account compute unavailable" in (result.error or "")
    debit.assert_not_awaited()
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_user_id_cannot_charge_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = uuid.uuid4()
    agent = ImageSubagent({"openrouter_api_key": "test-key", "db_pool": object()})
    debit = AsyncMock()
    monkeypatch.setattr("db.video_credits.VideoCreditsDAL.debit_credits", debit)
    token = _scope.set(ComputeScope(account, cast(EntitlementService, SimpleNamespace())))
    try:
        result = await agent.execute(
            "private prompt",
            {"mode": "video", "user_id": str(uuid.uuid4()), "duration": 5},
        )
    finally:
        _scope.reset(token)
    assert result.success is False
    assert "Account identity mismatch" in (result.error or "")
    debit.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_capability_does_not_override_unqualified_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    account = uuid.uuid4()
    agent = ImageSubagent({"openrouter_api_key": "test-key", "db_pool": object()})
    entitlement = SimpleNamespace(
        resolve=AsyncMock(return_value=SimpleNamespace(capabilities={"video_generation"}))
    )
    monkeypatch.setattr("orchestrator.entitlements.EntitlementService", lambda pool: entitlement)
    debit = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr("db.video_credits.VideoCreditsDAL.debit_credits", debit)
    monkeypatch.setattr("orchestrator.subagents.image.FalKlingProvider.generate_video", dispatch)
    token = _scope.set(ComputeScope(account, cast(EntitlementService, entitlement)))
    try:
        result = await agent.execute(
            "private prompt",
            {"mode": "video", "user_id": str(account), "duration": 5, "video_provider": "fal"},
        )
    finally:
        _scope.reset(token)
    assert result.success is False
    assert "Approved video route unavailable" in (result.error or "")
    debit.assert_not_awaited()
    dispatch.assert_not_awaited()
