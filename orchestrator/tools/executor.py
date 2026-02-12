from __future__ import annotations

import json
from typing import Any

from orchestrator.tools.registry import ToolRegistry


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
