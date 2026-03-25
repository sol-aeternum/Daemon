"""Integration tests for council module."""

import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from orchestrator.council.models import (
    CouncilConfig,
    PerspectiveType,
    PerspectiveResponse,
)
from orchestrator.council.config import load_roster
from orchestrator.council.output import build_disagreement_map
from orchestrator.commands.council import run_council
from orchestrator.council.sse import _emit_council_output_events
from orchestrator.main import _extract_council_config_response


class TestCouncilIntegration:
    def test_disagreement_map_empty(self):
        result = build_disagreement_map([])
        assert result["agreement_zones"] == []
        assert result["dissent_zones"] == []
        assert result["signals"] == []

    def test_disagreement_map_single_response(self):
        responses = [
            PerspectiveResponse(
                perspective=PerspectiveType.ANALYST,
                content="I agree with selling",
                confidence=7.0,
            )
        ]
        result = build_disagreement_map(responses)
        assert "agreement_zones" in result
        assert "dissent_zones" in result
        assert "signals" in result

    def test_disagreement_map_multiple_responses(self):
        responses = [
            PerspectiveResponse(
                perspective=PerspectiveType.ANALYST,
                content="I agree with selling the property now",
                confidence=8.0,
            ),
            PerspectiveResponse(
                perspective=PerspectiveType.SKEPTIC,
                content="However, I think there are risks",
                confidence=6.0,
            ),
        ]
        result = build_disagreement_map(responses)
        assert len(result["agreement_zones"]) >= 0
        assert len(result["dissent_zones"]) >= 0

    def test_disagreement_map_low_confidence_signals(self):
        responses = [
            PerspectiveResponse(
                perspective=PerspectiveType.SKEPTIC,
                content="I'm not sure",
                confidence=3.0,
            )
        ]
        result = build_disagreement_map(responses)
        assert len(result["signals"]) > 0


class TestInterviewParsing:
    def test_default_bypass(self):
        from orchestrator.council.interview import parse_interview_response

        config = CouncilConfig()
        result = parse_interview_response("default", config)
        assert result.interview_bypass is True

    def test_go_bypass(self):
        from orchestrator.council.interview import parse_interview_response

        config = CouncilConfig()
        result = parse_interview_response("go", config)
        assert result.interview_bypass is True

    def test_lean_preset(self):
        from orchestrator.council.interview import parse_interview_response

        config = CouncilConfig()
        result = parse_interview_response("lean", config)
        assert result.preset_name == "lean"

    def test_audit_enabled(self):
        from orchestrator.council.interview import parse_interview_response

        config = CouncilConfig()
        result = parse_interview_response("audit on", config)
        assert result.audit_enabled is True


class TestRouter:
    def test_council_command_detection(self):
        from orchestrator.router import route_message

        decision = route_message("/council Should I sell?", None)
        assert decision.command == "council"
        assert decision.user_message == "Should I sell?"

    def test_council_with_default_flag(self):
        from orchestrator.router import route_message

        decision = route_message("/council --default Should I sell?", None)
        assert decision.command == "council"
        assert "Should I sell?" in decision.user_message

    def test_regular_message_no_command(self):
        from orchestrator.router import route_message

        decision = route_message("Hello world", None)
        assert decision.command is None
        assert decision.user_message == "Hello world"


class TestCouncilRegressionFixes:
    def test_config_response_not_classified_as_command(self):
        message = "/council config: preset=lean, rounds=2, audit=false"
        config_response = _extract_council_config_response(message)
        is_council_config_response = config_response is not None
        is_council_command = (
            message.lstrip().startswith("/council") and not is_council_config_response
        )
        assert is_council_config_response is True
        assert is_council_command is False

    @pytest.mark.asyncio
    async def test_run_council_metadata_and_progress(self):
        config = CouncilConfig(round_count=2, audit_enabled=True)
        round_1 = [
            PerspectiveResponse(
                perspective=PerspectiveType.ANALYST,
                content="Round 1 answer",
                confidence=7.0,
                reasoning="Round 1 chain",
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "cost_usd": 0.01,
                },
                model_id="resolved-model-a",
            )
        ]
        round_2 = [
            PerspectiveResponse(
                perspective=PerspectiveType.ANALYST,
                content="Round 2 answer",
                confidence=8.0,
                reasoning="Round 2 chain",
                usage={
                    "prompt_tokens": 120,
                    "completion_tokens": 60,
                    "total_tokens": 180,
                    "cost_usd": 0.02,
                },
                model_id="resolved-model-a",
            )
        ]

        with (
            patch(
                "orchestrator.commands.council.run_round_1",
                new=AsyncMock(return_value=round_1),
            ),
            patch(
                "orchestrator.commands.council.run_round_2",
                new=AsyncMock(return_value=round_2),
            ),
            patch(
                "orchestrator.commands.council.run_audit_round",
                new=AsyncMock(
                    return_value=(
                        [],
                        {
                            "prompt_tokens": 80,
                            "completion_tokens": 20,
                            "total_tokens": 100,
                            "cost_usd": 0.005,
                        },
                        "resolved-auditor-model",
                    )
                ),
            ),
        ):
            result = await run_council(
                prompt="Should I hold or sell?",
                conversation_id="conv_test",
                config=config,
            )

        assert result["type"] == "council_output"
        output = result["output"]
        assert output.metadata["total_tokens"] == 430
        assert output.metadata["total_cost_usd"] > 0.0
        assert output.metadata["models_used"]
        assert "resolved-model-a" in output.metadata["models_used"]
        assert "resolved-auditor-model" in output.metadata["models_used"]
        assert output.metadata["progress_events"]
        assert output.metadata["raw_reasoning"]

    @pytest.mark.asyncio
    async def test_preset_lean_executes_lean_roster(self):
        config = CouncilConfig(preset_name="lean", round_count=1, audit_enabled=False)
        with patch(
            "orchestrator.commands.council.run_round_1",
            new=AsyncMock(return_value=[]),
        ) as mock_round_1:
            result = await run_council(
                prompt="Test lean roster",
                conversation_id="conv_test",
                config=config,
            )

        assert result["type"] == "council_output"
        assert mock_round_1.await_count == 1
        assert mock_round_1.await_args is not None
        lean_roster = load_roster("lean")
        called_roster = mock_round_1.await_args.args[1]
        assert called_roster == lean_roster

    @pytest.mark.asyncio
    async def test_sse_emits_progress_and_raw_sections(self):
        output = {
            "consensus": "Consensus text",
            "perspectives_summary": {"analyst": "Perspective text"},
            "findings": [],
            "metadata": {
                "total_tokens": 42,
                "total_cost_usd": 0.1234,
                "models_used": ["openrouter/anthropic/claude-opus-4.6"],
                "raw_reasoning": "Full reasoning body",
                "progress_events": [
                    {
                        "stage": "round_1",
                        "current_round": 1,
                        "total_rounds": 2,
                        "models_complete": 1,
                        "models_total": 4,
                    }
                ],
            },
        }

        frames = []
        async for frame in _emit_council_output_events(
            output=output,
            session_id="ses_123",
            conversation_id="conv_123",
            request_id="req_123",
        ):
            frames.append(frame)

        payloads = []
        for frame in frames:
            for line in frame.splitlines():
                if line.startswith("data: "):
                    payloads.append(json.loads(line[len("data: ") :]))

        progress_events = [p for p in payloads if p.get("type") == "council_progress"]
        raw_events = [
            p
            for p in payloads
            if p.get("type") == "council_output"
            and p.get("data", {}).get("section") == "raw"
        ]
        done_events = [p for p in payloads if p.get("type") == "council_done"]

        assert progress_events
        assert progress_events[0]["data"]["total_rounds"] == 2
        assert raw_events
        assert done_events
        assert done_events[0]["data"]["total_tokens"] == 42

    @pytest.mark.asyncio
    async def test_sse_progress_fallback_never_emits_zero_totals(self):
        output = {
            "consensus": "Consensus text",
            "perspectives_summary": {"analyst": "Perspective text"},
            "findings": [],
            "metadata": {
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "models_used": [],
                "raw_reasoning": "",
            },
        }

        frames = []
        async for frame in _emit_council_output_events(
            output=output,
            session_id="ses_123",
            conversation_id="conv_123",
            request_id="req_123",
        ):
            frames.append(frame)

        payloads = []
        for frame in frames:
            for line in frame.splitlines():
                if line.startswith("data: "):
                    payloads.append(json.loads(line[len("data: ") :]))

        progress_events = [p for p in payloads if p.get("type") == "council_progress"]
        assert progress_events
        event_data = progress_events[0]["data"]
        assert event_data["models_total"] >= 1
        assert event_data["models_complete"] >= 1
        assert event_data["total_rounds"] >= 1
