from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from orchestrator.config import ProviderConfig
from orchestrator.tools.advisor import ConsultAdvisorTool, create_advisor_registry
from orchestrator.tools.executor import ExecutionContext


@dataclass
class _FakeAdvisorSlot:
    model: str
    system_prompt_override: str | None = None
    timeout_s: float | None = None


class _FakeAdvisorRoster:
    def resolve(self, domain: str, difficulty: str) -> _FakeAdvisorSlot:
        return _FakeAdvisorSlot(model=f"openrouter/{domain}-{difficulty}")


class _FakeSettings:
    advisor_budget_per_conversation = 10
    default_provider = "openrouter"

    def get_advisor_roster_config(self) -> _FakeAdvisorRoster:
        return _FakeAdvisorRoster()

    def resolve_advisor_model(self, domain: str, difficulty: str) -> str:
        return f"openrouter/{domain}-{difficulty}"

    def get_provider_config(self, provider_name: str | None = None) -> ProviderConfig:
        return ProviderConfig(
            name=provider_name or "openrouter",
            base_url="https://openrouter.example/api/v1",
            api_key="test-key",
            model="openrouter/default",
            requires_auth=True,
            timeout_s=30.0,
        )


class _FakeMemoryStore:
    def __init__(self, count: int = 0) -> None:
        self.count = count
        self.increment_calls = 0

    async def get_advisor_call_count(self, _conversation_id: Any) -> int:
        return self.count

    async def increment_advisor_call_count(self, _conversation_id: Any) -> int:
        self.increment_calls += 1
        self.count += 1
        return self.count


def _runtime_context(
    *,
    event_scope: str = "assistant",
) -> tuple[ExecutionContext, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    async def emit_event(event: dict[str, Any]) -> None:
        events.append(event)

    context = ExecutionContext(
        request_id="req_root",
        conversation_id="conv_root",
        trace_key="req_root:assistant",
        event_scope=event_scope,
        text_event_type="content_delta",
        budget_state={
            "conversation_uuid": "00000000-0000-0000-0000-000000000123",
            "budget_limit": 10,
        },
        gating_context={
            "surface": "chat",
            "provider": "openrouter",
            "advisor_eligible": True,
        },
        registry_context={
            "surface": "chat",
            "registry_scope": "chat",
            "allowlist_scope": None,
        },
        event_tags={"surface": "chat"},
        emit_event=emit_event,
        tool_name="consult_advisor",
        tool_call_id="call_consult_advisor",
    )
    return context, events


@pytest.mark.asyncio
async def test_consult_advisor_executes_nested_completion_and_emits_lifecycle_events(
    monkeypatch,
):
    settings = _FakeSettings()
    memory_store = _FakeMemoryStore(count=2)
    tool = ConsultAdvisorTool(settings=settings, memory_store=memory_store)
    runtime_context, captured_events = _runtime_context()

    async def fake_completion_with_tools(**kwargs: Any):
        execution_context = kwargs["execution_context"]
        assert execution_context.event_scope == "advisor"
        assert execution_context.advisor_id is not None
        assert execution_context.parent_trace_key == "req_root:assistant"

        registry_names = {
            schema["function"]["name"] for schema in kwargs["registry"].list_schemas()
        }
        assert registry_names == {"calculate", "get_time"}
        assert kwargs["actual_model"] == "openrouter/coding-high"

        yield {
            "type": "advisor_text_delta",
            "content": '{"answer":"Review the auth boundary and keep session checks centralized.",',
            "advisor_id": execution_context.advisor_id,
            "trace_key": execution_context.trace_key,
            "parent_trace_key": execution_context.parent_trace_key,
            "event_scope": "advisor",
            "event_tags": dict(execution_context.event_tags),
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "Review the auth boundary and keep session checks centralized.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": {
                        "agent_type": "code",
                        "task": "Implement the auth-module cleanup once the plan is approved.",
                    },
                }
            ),
            "advisor_id": execution_context.advisor_id,
            "trace_key": execution_context.trace_key,
            "parent_trace_key": execution_context.parent_trace_key,
            "event_scope": "advisor",
            "event_tags": dict(execution_context.event_tags),
        }
        execution_context.usage_state.add_snapshot(
            {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "cost_usd": 0.42,
            }
        )
        yield {
            "type": "done",
            "advisor_id": execution_context.advisor_id,
            "trace_key": execution_context.trace_key,
            "parent_trace_key": execution_context.parent_trace_key,
            "event_scope": "advisor",
            "event_tags": dict(execution_context.event_tags),
            "usage": execution_context.usage_state.snapshot(),
        }

    monkeypatch.setattr(
        "orchestrator.tools.advisor.completion_with_tools",
        fake_completion_with_tools,
    )

    result = await tool.execute(
        domain="coding",
        difficulty="high",
        question="review this auth module",
        context_summary="Current auth logic is spread across handlers and middleware.",
        _runtime_context=runtime_context,
    )

    parsed = json.loads(result)
    assert parsed["advisor_id"].startswith("advisor_coding_")
    assert parsed["answer"] == "Review the auth boundary and keep session checks centralized."
    assert parsed["sufficient"] is True
    assert parsed["escalate"] is False
    assert parsed["spawn_recommended"]["agent_type"] == "code"
    assert parsed["budget"] == {"current_count": 3, "limit": 10}
    assert memory_store.increment_calls == 1

    event_types = [event["type"] for event in captured_events]
    assert event_types == [
        "advisor_start",
        "advisor_text_delta",
        "advisor_text_done",
        "done",
        "advisor_end",
    ]
    assert all(event.get("advisor_id") == parsed["advisor_id"] for event in captured_events)

    advisor_end = captured_events[-1]
    assert captured_events[0]["tool_call_id"] == "call_consult_advisor"
    assert advisor_end["usage"]["total_tokens"] == 18
    assert advisor_end["tool_call_id"] == "call_consult_advisor"
    assert advisor_end["tokens_in"] == 11
    assert advisor_end["tokens_out"] == 7
    assert advisor_end["parent_trace_key"] == "req_root:assistant"


@pytest.mark.asyncio
async def test_consult_advisor_returns_budget_error_without_model_call(monkeypatch):
    settings = _FakeSettings()
    memory_store = _FakeMemoryStore(count=10)
    tool = ConsultAdvisorTool(settings=settings, memory_store=memory_store)
    runtime_context, captured_events = _runtime_context()

    called = False

    async def fake_completion_with_tools(**_kwargs: Any):
        nonlocal called
        called = True
        yield {"type": "done"}

    monkeypatch.setattr(
        "orchestrator.tools.advisor.completion_with_tools",
        fake_completion_with_tools,
    )

    result = await tool.execute(
        domain="coding",
        difficulty="high",
        question="review this auth module",
        _runtime_context=runtime_context,
    )

    parsed = json.loads(result)
    assert parsed["status"] == "budget_exhausted"
    assert "Advisor budget exhausted" in parsed["answer"]
    assert parsed["budget"] == {"current_count": 10, "limit": 10}
    assert called is False
    assert memory_store.increment_calls == 0
    assert captured_events == []


@pytest.mark.asyncio
async def test_consult_advisor_rejects_nested_advisor_depth(monkeypatch):
    settings = _FakeSettings()
    memory_store = _FakeMemoryStore(count=0)
    tool = ConsultAdvisorTool(settings=settings, memory_store=memory_store)
    runtime_context, _captured_events = _runtime_context(event_scope="advisor")

    called = False

    async def fake_completion_with_tools(**_kwargs: Any):
        nonlocal called
        called = True
        yield {"type": "done"}

    monkeypatch.setattr(
        "orchestrator.tools.advisor.completion_with_tools",
        fake_completion_with_tools,
    )

    result = await tool.execute(
        domain="coding",
        difficulty="high",
        question="review this auth module",
        _runtime_context=runtime_context,
    )

    parsed = json.loads(result)
    assert parsed["status"] == "depth_cap"
    assert "depth cap" in parsed["answer"].lower()
    assert called is False


def test_create_advisor_registry_excludes_spawn_and_recursion_tools():
    registry = create_advisor_registry()
    tool_names = {schema["function"]["name"] for schema in registry.list_schemas()}

    assert tool_names == {"calculate", "get_time"}
    assert "consult_advisor" not in tool_names
    assert "spawn_agent" not in tool_names
    assert "spawn_multiple" not in tool_names


@pytest.mark.asyncio
async def test_consult_advisor_handles_nested_completion_error_without_raising(
    monkeypatch,
):
    settings = _FakeSettings()
    memory_store = _FakeMemoryStore(count=1)
    tool = ConsultAdvisorTool(settings=settings, memory_store=memory_store)
    runtime_context, captured_events = _runtime_context()

    async def fake_completion_with_tools(**kwargs: Any):
        execution_context = kwargs["execution_context"]
        yield {
            "type": "error",
            "error": "provider timeout",
            "advisor_id": execution_context.advisor_id,
            "trace_key": execution_context.trace_key,
            "parent_trace_key": execution_context.parent_trace_key,
            "event_scope": "advisor",
            "event_tags": dict(execution_context.event_tags),
        }

    monkeypatch.setattr(
        "orchestrator.tools.advisor.completion_with_tools",
        fake_completion_with_tools,
    )

    result = await tool.execute(
        domain="research",
        difficulty="mid",
        question="What sources should I trust here?",
        _runtime_context=runtime_context,
    )

    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["error"] == "provider timeout"
    assert parsed["answer"] == "provider timeout"
    assert memory_store.increment_calls == 0

    event_types = [event["type"] for event in captured_events]
    assert event_types == ["advisor_start", "error", "advisor_end"]
    assert captured_events[-1]["status"] == "error"
