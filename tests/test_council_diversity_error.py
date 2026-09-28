"""Council cannot report success when the served roster loses independence."""

from unittest.mock import AsyncMock

import pytest

from orchestrator.commands import council
from orchestrator.council.models import CouncilConfig, CouncilDiversityError
from orchestrator.council import engine
from orchestrator.council.models import PerspectiveResponse, PerspectiveType


@pytest.mark.asyncio
async def test_diversity_failure_uses_existing_error_result(monkeypatch) -> None:
    next_round = AsyncMock()
    monkeypatch.setattr(
        council,
        "run_round_1",
        AsyncMock(side_effect=CouncilDiversityError("Not enough developers")),
    )
    monkeypatch.setattr(council, "run_round_2", next_round)
    result = await council.run_council("Compare options")
    assert result == {"type": "error", "content": "Not enough developers"}
    next_round.assert_not_awaited()


@pytest.mark.asyncio
async def test_assigned_roster_is_revalidated_before_execution(monkeypatch) -> None:
    first_round = AsyncMock()
    monkeypatch.setattr(council, "run_round_1", first_round)
    config = CouncilConfig().model_copy(update={"roster": {"analyst": "openrouter/one/model"}})
    result = await council.run_council("Compare options", config=config)
    assert result["type"] == "error"
    first_round.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_excludes_actual_fallback_developer(monkeypatch) -> None:
    call = AsyncMock(
        return_value=("auditor", "No findings.", None, None, {}, "openrouter/other/model")
    )
    monkeypatch.setattr(engine, "_call_model", call)
    config = CouncilConfig()
    actual_fallback = "openrouter/deepseek/deepseek-v4.1-flash"
    await engine.run_audit_round(
        "Compare options",
        config.roster,
        [PerspectiveResponse(PerspectiveType.ANALYST, "Position", 0.8, model_id=actual_fallback)],
    )
    kwargs = call.call_args.kwargs
    assert actual_fallback in kwargs["excluded_models"]
    assert "deepseek" in kwargs["excluded_developers"]
    assert "[{Agent-ID}] {Finding}" in kwargs["prompt"]
