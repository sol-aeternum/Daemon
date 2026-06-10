from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
from typing import Any

from orchestrator.tools.registry import ToolRegistry


@dataclass
class ToolUsageState:
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    def add_snapshot(self, usage: dict[str, Any]) -> None:
        self.snapshots.append(dict(usage))

    def snapshot(self) -> dict[str, Any]:
        total: dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }
        for usage in self.snapshots:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int | float):
                    total[key] += int(value)
            cost_value = usage.get("cost_usd")
            if isinstance(cost_value, int | float):
                total["cost_usd"] += float(cost_value)
        return total


@dataclass
class ExecutionContext:
    request_id: str | None = None
    conversation_id: str | None = None
    trace_key: str | None = None
    parent_trace_key: str | None = None
    advisor_id: str | None = None
    event_scope: str = "assistant"
    text_event_type: str = "content_delta"
    budget_state: dict[str, Any] = field(default_factory=dict)
    gating_context: dict[str, Any] = field(default_factory=dict)
    registry_context: dict[str, Any] = field(default_factory=dict)
    event_tags: dict[str, Any] = field(default_factory=dict)
    emit_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    usage_state: ToolUsageState = field(default_factory=ToolUsageState)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, name: str, arguments: str | dict[str, Any]) -> str:
        tool = self._registry.get(name)
        if not tool:
            return json.dumps({"error": f"Unknown tool: {name}"})

        if isinstance(arguments, str):
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                return json.dumps({"error": f"Invalid JSON arguments: {arguments}"})
        else:
            args = arguments

        try:
            result = await tool.execute(**args)
            return result
        except Exception as e:
            return json.dumps({"error": f"Tool execution failed: {str(e)}"})
