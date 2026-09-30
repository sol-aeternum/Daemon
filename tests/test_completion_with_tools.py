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
async def test_source_manifest_is_fenced_without_replacing_last_user_request(monkeypatch):
    from types import SimpleNamespace
    from orchestrator.tools import completion

    class SourceListTool(Tool):
        name = "web_fetch"
        description = "Sources"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            assert kwargs == {"action": "list", "limit": 20}
            return '{"sources":[{"title":"untrusted source title"}]}'

    registry = ToolRegistry()
    registry.register(SourceListTool())

    async def budget(*args):
        return SimpleNamespace(fits=lambda *args, **kwargs: True)

    monkeypatch.setattr(completion, "tool_context_budget", budget)
    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)

        async def stream():
            yield {"choices": [{"delta": {"content": "Answer"}}]}

        return stream()

    monkeypatch.setattr(completion, "guarded_completion", fake_completion)
    original = [{"role": "user", "content": "My actual question"}]
    _ = [
        event
        async for event in completion_with_tools(
            Settings(),
            ProviderConfig(name="openrouter", model="openrouter/test", requires_auth=False),
            original,
            registry,
        )
    ]
    assert calls[0]["messages"][-1] == original[-1]
    assert 'trust="untrusted"' in calls[0]["messages"][-2]["content"]
    assert len(original) == 1


@pytest.mark.asyncio
async def test_oversized_tool_result_stops_batch_and_synthesizes_with_paired_results(monkeypatch):
    from types import SimpleNamespace
    from orchestrator.tools import completion

    executed = []

    class LargeTool(Tool):
        name = "large_source"
        description = "Return a page"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            executed.append(True)
            return "x" * 100000

    registry = ToolRegistry()
    registry.register(LargeTool())

    async def budget(*args):
        return SimpleNamespace(
            output_tokens=128, fits=lambda messages, **kwargs: len(json.dumps(messages)) < 4000
        )

    monkeypatch.setattr(completion, "tool_context_budget", budget)
    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)

        async def stream():
            if len(calls) == 1:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": i,
                                        "id": f"call_{i}",
                                        "function": {"name": "large_source", "arguments": "{}"},
                                    }
                                    for i in range(2)
                                ]
                            }
                        }
                    ]
                }
            else:
                yield {"choices": [{"delta": {"content": "Evidence is incomplete."}}]}

        return stream()

    monkeypatch.setattr(completion, "guarded_completion", fake_completion)
    events = [
        event
        async for event in completion_with_tools(
            Settings(),
            ProviderConfig(name="openrouter", model="openrouter/test", requires_auth=False),
            [{"role": "user", "content": "Read two pages"}],
            registry,
        )
    ]
    assert len(executed) == 1
    assert len(calls) == 2 and "tools" not in calls[-1]
    assert calls[-1]["max_tokens"] == 128
    results = [message for message in calls[-1]["messages"] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in results] == ["call_0", "call_1"]
    assert "tool ran" in results[0]["content"]
    assert "not executed" in results[1]["content"]
    assert "x" * 10000 not in json.dumps(calls[-1])
    assert any(event.get("content") == "Evidence is incomplete." for event in events)


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
            yield {"choices": [{"delta": {"content": "Here are the key findings from research. "}}]}
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

    monkeypatch.setattr("orchestrator.tools.completion.guarded_completion", fake_acompletion)

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
        str(event.get("content", "")) for event in events if event.get("type") == "content_delta"
    )
    assert "Here are the key findings from research" in combined_content
    assert "Samsung leads on display and camera" in combined_content


@pytest.mark.asyncio
async def test_retry_after_old_spawn_does_not_dispatch_unregistered_tool(monkeypatch):
    from orchestrator.tools.builtin import CalculateTool

    registry = ToolRegistry()
    registry.register(CalculateTool())

    async def fake_completion(**kwargs):
        async def stream():
            yield {"choices": [{"delta": {"content": "Subagent dispatch is unavailable."}}]}

        return stream()

    monkeypatch.setattr("orchestrator.tools.completion.guarded_completion", fake_completion)
    messages = [
        {
            "role": "tool",
            "name": "spawn_agent",
            "tool_call_id": "old",
            "content": json.dumps(
                {"agent_type": "research", "metadata": {"session_id": "old-session"}}
            ),
        },
        {"role": "user", "content": "try again"},
    ]
    events = [
        event
        async for event in completion_with_tools(
            settings=Settings(),
            provider_config=ProviderConfig(
                name="openrouter", model="openrouter/test-model", requires_auth=False
            ),
            messages=messages,
            registry=registry,
            actual_model="openrouter/test-model",
        )
    ]
    assert not any(event.get("type") == "tool_executing" for event in events)
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_direct_search_round_reaches_synthesis_with_untrusted_sources(monkeypatch):
    class SearchFixture(Tool):
        name = "web_search"
        description = "Search fixture"
        parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

        async def execute(self, **kwargs: Any) -> str:
            assert kwargs == {"query": "example facts"}
            return json.dumps(
                {
                    "results": [
                        {
                            "title": "Example",
                            "url": "https://example.com",
                            "description": "Evidence",
                        }
                    ]
                }
            )

    registry = ToolRegistry()
    registry.register(SearchFixture())
    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)

        async def stream():
            if len(calls) == 1:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "search-call",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query":"example facts"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            else:
                result = kwargs["messages"][-1]
                assert result["role"] == "tool"
                assert result["content"].startswith(
                    '<tool_result tool="web_search" trust="untrusted">'
                )
                from orchestrator.tools.completion import _unwrap_tool_result

                payload = json.loads(_unwrap_tool_result(result["content"]))
                assert payload["results"][0]["url"] == "https://example.com"
                yield {
                    "choices": [{"delta": {"content": "Evidence: [Example](https://example.com)."}}]
                }

        return stream()

    monkeypatch.setattr("orchestrator.tools.completion.guarded_completion", fake_completion)
    events = [
        event
        async for event in completion_with_tools(
            settings=Settings(),
            provider_config=ProviderConfig(
                name="openrouter", model="openrouter/test-model", requires_auth=False
            ),
            messages=[{"role": "user", "content": "Find current example facts"}],
            registry=registry,
            actual_model="openrouter/test-model",
        )
    ]
    assert len(calls) == 2
    assert [e["name"] for e in events if e["type"] == "tool_executing"] == ["web_search"]
    assert "".join(e.get("content", "") for e in events if e["type"] == "content_delta") == (
        "Evidence: [Example](https://example.com)."
    )
    assert events[-1]["type"] == "done"
