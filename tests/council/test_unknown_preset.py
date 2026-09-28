"""Unknown presets fail clearly before inference instead of changing the roster."""

from unittest.mock import AsyncMock

import pytest

from orchestrator.commands import council
from orchestrator.council import sse
from orchestrator.council.config import UnknownCouncilPreset, load_role_timeouts, load_roster
from orchestrator.council.models import CouncilConfig


@pytest.mark.parametrize("loader", [load_roster, load_role_timeouts])
def test_unknown_preset_rejected(loader):
    with pytest.raises(UnknownCouncilPreset, match="Unknown council preset: typo"):
        loader("typo")


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_roster", [False, True])
async def test_unknown_preset_does_not_run_default_or_explicit_roster(monkeypatch, explicit_roster):
    first_round = AsyncMock()
    monkeypatch.setattr(council, "run_round_1", first_round)
    config = (
        CouncilConfig(preset_name="typo", roster=load_roster())
        if explicit_roster
        else CouncilConfig(preset_name="typo")
    )
    result = await council.run_council("Compare options", config=config)
    assert result == {"type": "error", "content": "Unknown council preset: typo"}
    first_round.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("config", ["preset=typo,rounds=99", "rounds=99"])
async def test_invalid_companion_field_never_runs_default_config(monkeypatch, config):
    run = AsyncMock()
    interview = AsyncMock()
    monkeypatch.setattr(sse, "run_council", run)
    monkeypatch.setattr(sse, "handle_council_interview_response", interview)
    frames = [
        frame
        async for frame in sse.stream_council_interview_response(
            f"/council config:{config}",
            "conversation",
            stored_config={"_prompt": "Compare options"},
        )
    ]
    assert len(frames) == 1
    assert "event: council_error" in frames[0]
    assert "Could not parse council config" in frames[0]
    run.assert_not_awaited()
    interview.assert_not_awaited()


@pytest.mark.asyncio
async def test_interview_preset_typo_emits_existing_error_event(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(sse, "run_council", run)
    frames = [
        frame
        async for frame in sse.stream_council_interview_response(
            "/council config:preset=typo",
            "conversation",
            stored_config={"_prompt": "Compare options"},
        )
    ]
    assert len(frames) == 1
    assert "event: council_error" in frames[0]
    assert "Unknown council preset: typo" in frames[0]
    run.assert_not_awaited()
