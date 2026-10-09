from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
from typing import Any
import uuid

from orchestrator.tools.registry import ToolRegistry


@dataclass(frozen=True)
class OperationIdentity:
    task_id: uuid.UUID
    epoch: int
    operation_id: uuid.UUID
    tool_name: str
    outcome: str


@dataclass(frozen=True)
class ToolExecution:
    """One invocation's result and fence evidence, never shared mutable state."""

    result: str
    operation: OperationIdentity | None = None

    def event_metadata(self, *, suppressed: bool = False) -> dict[str, Any]:
        if self.operation is None:
            return {}
        return {
            "task_id": str(self.operation.task_id),
            "operation_id": str(self.operation.operation_id),
            "lifecycle_epoch": self.operation.epoch,
            "outcome": self.operation.outcome,
            "payload_state": "summary" if suppressed else "full",
        }


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
        return (await self.execute_invocation(name, arguments)).result

    async def execute_invocation(self, name: str, arguments: str | dict[str, Any]) -> ToolExecution:
        from orchestrator.tasks.fence import FencedTool

        tool = self._registry.get(name)
        if not tool:
            return ToolExecution(json.dumps({"error": f"Unknown tool: {name}"}))

        if isinstance(arguments, str):
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                return ToolExecution(json.dumps({"error": f"Invalid JSON arguments: {arguments}"}))
        else:
            args = arguments

        try:
            if isinstance(tool, FencedTool):
                return await tool.execute_invocation(**args)
            result = await tool.execute(**args)
            return ToolExecution(result)
        except Exception as e:
            # Budget and settlement failures must stop the operation, not become
            # ordinary tool text that invites another paid attempt.
            from orchestrator.compute_runtime import compute_error

            refusal = compute_error(e)
            if refusal is not None:
                raise refusal from None
            return ToolExecution(json.dumps({"error": f"Tool execution failed: {str(e)}"}))
