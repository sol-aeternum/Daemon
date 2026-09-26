from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.council import engine
from orchestrator.tools.builtin import create_council_readonly_registry
from orchestrator.tools.executor import ToolExecutor


READONLY_COUNCIL_TOOL_NAMES = {
    "calculate",
    "get_time",
}
LOCAL_READONLY_TOOL_NAMES = READONLY_COUNCIL_TOOL_NAMES | {"web_fetch"}

FORBIDDEN_COUNCIL_TOOL_NAMES = {
    "generate_document",
    "memory_demote",
    "memory_promote",
    "memory_reflect",
    "memory_write",
    "notification_send",
    "reminder_list",
    "reminder_set",
    "skill_manage",
    "spawn_agent",
    "spawn_multiple",
    "web_search",
}


def _schema_names(schemas: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for schema in schemas:
        function = schema.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.add(function["name"])
    return names


def test_council_registry_is_strictly_readonly() -> None:
    registry = create_council_readonly_registry()

    names = _schema_names(registry.list_schemas())

    assert names == LOCAL_READONLY_TOOL_NAMES
    assert names.isdisjoint(FORBIDDEN_COUNCIL_TOOL_NAMES)


def test_council_registry_does_not_expose_unpriced_search_or_retain_account_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.tools.registry import ToolRegistry

    registries: list[ToolRegistry] = []
    original = engine.create_advisor_registry

    def capture_registry() -> ToolRegistry:
        registry = original()
        registries.append(registry)
        return registry

    monkeypatch.setattr(engine, "create_advisor_registry", capture_registry)
    first_schemas, _ = engine._get_council_tools()
    second_schemas, _ = engine._get_council_tools()

    assert registries[0] is not registries[1]
    assert (
        _schema_names(first_schemas) == _schema_names(second_schemas) == READONLY_COUNCIL_TOOL_NAMES
    )


@pytest.mark.asyncio
async def test_council_executor_cannot_call_side_effect_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemas, executor = engine._get_council_tools()
    names = _schema_names(schemas)

    assert names == READONLY_COUNCIL_TOOL_NAMES
    assert isinstance(executor, ToolExecutor)

    for tool_name in FORBIDDEN_COUNCIL_TOOL_NAMES:
        result = json.loads(await executor.execute(tool_name, "{}"))
        assert result == {"error": f"Unknown tool: {tool_name}"}
