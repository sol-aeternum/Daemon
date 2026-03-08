from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.config import ProviderConfig, Settings
from orchestrator.tools.completion import completion_with_tools
from orchestrator.tools.registry import Tool, ToolRegistry


class DummySpawnTool(Tool):
    name = "spawn_agent"
    description = "Spawn a research agent"
    parameters = {
        "type": "object",
        "properties": {
            "agent_type": {"type": "string"},
            "task": {"type": "string"},
        },
        "required": ["agent_type", "task"],
    }

    async def execute(self, **kwargs: Any) -> str:
        return json.dumps(
            {
                "agent_type": kwargs.get("agent_type", "research"),
                "success": True,
                "data": {
                    "synthesis": "Collected detailed findings from research agent.",
                },
                "error": None,
                "metadata": {
                    "session_id": "ses_test",
                },
            }
        )


@pytest.mark.asyncio
async def test_completion_with_tools_forces_synthesis_after_max_rounds(monkeypatch):
    provider_config = ProviderConfig(
        name="openrouter",
        model="openrouter/test-model",
        requires_auth=False,
        timeout_s=30.0,
    )
    settings = Settings()

    registry = ToolRegistry()
    registry.register(DummySpawnTool())

    call_count = {"value": 0}

    async def fake_acompletion(**kwargs):
        call_count["value"] += 1

        async def stream_tool_round():
            yield {
                "choices": [
                    {
                        "delta": {
                            "content": "Let me gather more details. ",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call_{call_count['value']}",
                                    "function": {
                                        "name": "spawn_agent",
                                        "arguments": '{"agent_type":"research","task":"compare phones"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

        async def stream_synthesis_round():
            yield {
                "choices": [
                    {"delta": {"content": "Here are the key findings from research. "}}
                ]
            }
            yield {
                "choices": [
                    {
                        "delta": {
                            "content": "Samsung leads on display and camera; OnePlus is better value."
                        }
                    }
                ]
            }

        if kwargs.get("tools") is None:
            return stream_synthesis_round()
        return stream_tool_round()

    monkeypatch.setattr(
        "orchestrator.tools.completion.litellm.acompletion", fake_acompletion
    )

    messages = [{"role": "user", "content": "Compare Galaxy S26 Ultra vs OnePlus 15"}]
    events = [
        event
        async for event in completion_with_tools(
            settings=settings,
            provider_config=provider_config,
            messages=messages,
            registry=registry,
            actual_model="openrouter/test-model",
            max_tool_rounds=2,
        )
    ]

    event_types = [event.get("type") for event in events]

    assert event_types.count("tool_executing") == 2
    assert event_types.count("tool_result") == 2
    assert "content_delta" in event_types
    assert event_types[-1] == "done"

    combined_content = "".join(
        str(event.get("content", ""))
        for event in events
        if event.get("type") == "content_delta"
    )
    assert "Here are the key findings from research" in combined_content
    assert "Samsung leads on display and camera" in combined_content
