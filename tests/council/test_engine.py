"""Tests for council engine."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from orchestrator.council.engine import (
    generate_agent_ids,
    _parse_confidence,
    _get_message_reasoning,
)


class TestGenerateAgentIds:
    def test_generate_agent_ids(self):
        roster = {
            "analyst": "anthropic/claude-opus-4.6",
            "strategist": "google/gemini-3.1-pro-preview",
            "skeptic": "moonshotai/kimi-k2-thinking",
        }
        agent_ids = generate_agent_ids(roster)
        assert len(agent_ids) == 3
        for role in roster.keys():
            assert role in agent_ids
            assert agent_ids[role].startswith("Agent-")

    def test_different_sessions_different_ids(self):
        roster = {"analyst": "model-a", "strategist": "model-b"}
        ids1 = generate_agent_ids(roster)
        ids2 = generate_agent_ids(roster)
        assert ids1 != ids2


class TestParseConfidence:
    def test_parse_confidence_from_content(self):
        content = """**Position**: Sell the property now

**Confidence**: 8/10

The market conditions are favorable."""
        confidence = _parse_confidence(content)
        assert confidence == 8.0

    def test_parse_confidence_default(self):
        content = "Some response without confidence"
        confidence = _parse_confidence(content)
        assert confidence == 5.0

    def test_parse_confidence_out_of_bounds(self):
        content = "**Confidence**: 15"
        confidence = _parse_confidence(content)
        assert confidence == 10.0


class TestFanOut:
    @pytest.mark.asyncio
    async def test_fan_out_excludes_auditor(self):
        roster = {
            "analyst": "anthropic/claude-opus-4.6",
            "auditor": "deepseek/deepseek-r1",
        }
        with patch("orchestrator.council.engine.litellm") as mock_litellm:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="test"))]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            from orchestrator.council.engine import fan_out

            results = await fan_out("test prompt", roster)

            assert len(results) == 1
            assert results[0][0] == "analyst"


class TestReasoningExtraction:
    def test_extracts_reasoning_from_thinking_field(self):
        message = {"thinking": "step-by-step rationale"}
        assert _get_message_reasoning(message) == "step-by-step rationale"

    def test_extracts_reasoning_from_reasoning_details_list(self):
        message = {
            "reasoning_details": [
                {"text": "first reason"},
                {"content": "second reason"},
            ]
        }
        result = _get_message_reasoning(message)
        assert result is not None
        assert "first reason" in result
        assert "second reason" in result
