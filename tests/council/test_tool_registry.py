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
    "web_fetch",
    "web_search",
}

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

    assert names == READONLY_COUNCIL_TOOL_NAMES
    assert names.isdisjoint(FORBIDDEN_COUNCIL_TOOL_NAMES)


def test_council_registry_threads_brave_api_key_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for #83 / PR #264 Codex P2 #1.

    ``_get_council_tools`` must pass ``get_settings().brave_api_key`` into
    ``create_council_readonly_registry`` so the registered WebSearchTool
    receives the same key the chat path uses. Without this, when
    ``BRAVE_API_KEY`` is configured but the env var is not separately read
    in ``WebSearchTool.__init__`` (which is the whole point of routing env
    access through Settings), the council's web_search tool reports the
    key is missing.

    Regression for the round-5 Codex P2: a sentinel ``brave_api_key``
    value is patched onto ``get_settings()`` and the test asserts the
    captured kwargs equal that sentinel exactly. A previous version of
    this test only checked the keyword was present, which let
    ``brave_api_key=None`` (or any other incorrect value) silently pass
    while the council's web_search tool still reported an unconfigured
    key at runtime.
    """
    from orchestrator.tools import builtin as builtin_module

    sentinel_key = "sentinel-brave-key-from-settings-12345"

    monkeypatch.setattr(engine, "_council_tool_registry", None)
    monkeypatch.setattr(engine, "_council_tool_executor", None)

    # Patch the ``get_settings`` that the engine module imported so it
    # returns a stub with a known configured ``brave_api_key``. Without
    # this the test would rely on whatever value the runtime Settings
    # cache happens to hold.
    class _StubSettings:
        def __init__(self, key: str) -> None:
            self.brave_api_key = key

    monkeypatch.setattr(engine, "get_settings", lambda: _StubSettings(sentinel_key))

    captured_kwargs: dict[str, Any] = {}

    def _fake_registry(**kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        # Return a stub registry object with the minimum surface the
        # engine exercises after construction (list_schemas is the only
        # call we need; ToolExecutor is built from it).
        stub = type("StubRegistry", (), {"list_schemas": staticmethod(lambda: [])})()
        return stub

    monkeypatch.setattr(engine, "create_council_readonly_registry", _fake_registry)
    monkeypatch.setattr(builtin_module, "create_council_readonly_registry", _fake_registry)

    engine._get_council_tools()

    assert captured_kwargs.get("brave_api_key") == sentinel_key, (
        "council engine must thread the configured Settings.brave_api_key "
        "into the registry factory unchanged; "
        f"captured kwargs: {captured_kwargs!r}"
    )


@pytest.mark.asyncio
async def test_council_executor_cannot_call_side_effect_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "_council_tool_registry", None)
    monkeypatch.setattr(engine, "_council_tool_executor", None)

    schemas, executor = engine._get_council_tools()
    names = _schema_names(schemas)

    assert names == READONLY_COUNCIL_TOOL_NAMES
    assert isinstance(executor, ToolExecutor)

    for tool_name in FORBIDDEN_COUNCIL_TOOL_NAMES:
        result = json.loads(await executor.execute(tool_name, "{}"))
        assert result == {"error": f"Unknown tool: {tool_name}"}
