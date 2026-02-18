from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from zoneinfo import ZoneInfo

from orchestrator.tools.registry import Tool
from orchestrator.tools.web_search import WebSearchTool


class GetTimeTool(Tool):
    name = "get_time"
    description = "Get the current date and time (defaults to Australia/Adelaide)"
    parameters = {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "description": "Output format: 'iso' for ISO 8601, 'human' for readable format",
                "enum": ["iso", "human"],
            },
            "timezone": {
                "type": "string",
                "description": "IANA timezone name (e.g. 'Australia/Adelaide', 'UTC'). Default: Australia/Adelaide",
            },
        },
        "required": [],
    }

    async def execute(
        self,
        format: str = "human",
        timezone: str = "Australia/Adelaide",
        **kwargs: Any,
    ) -> str:
        from datetime import timezone as dt_timezone

        tz_input_name = timezone or "Australia/Adelaide"

        try:
            tz = ZoneInfo(tz_input_name)
            tz_name = tz_input_name
        except Exception:
            tz = dt_timezone.utc
            tz_name = "UTC"

        now_utc = datetime.now(dt_timezone.utc)
        now_local = now_utc.astimezone(tz)
        tz_abbr = now_local.strftime("%Z")
        tz_offset = now_local.strftime("%z")

        if format == "iso":
            return json.dumps(
                {
                    "time": now_local.isoformat(),
                    "timezone": tz_name,
                    "tz_abbr": tz_abbr,
                    "tz_offset": tz_offset,
                    "utc_time": now_utc.isoformat(),
                }
            )

        return json.dumps(
            {
                "time": now_local.strftime("%A, %B %d, %Y at %I:%M %p"),
                "timezone": tz_name,
                "tz_abbr": tz_abbr,
                "tz_offset": tz_offset,
                "utc_time": now_utc.isoformat(),
            }
        )


class CalculateTool(Tool):
    name = "calculate"
    description = "Perform a mathematical calculation"
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression to evaluate (e.g., '2 + 2', '15 * 23')",
            }
        },
        "required": ["expression"],
    }

    async def execute(self, **kwargs: Any) -> str:
        import ast
        import operator

        expression = kwargs.get("expression", "")

        ALLOWED_NODES = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Num,
            ast.Constant,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.Pow,
            ast.Mod,
            ast.USub,
            ast.UAdd,
            ast.Load,
        )

        try:
            tree = ast.parse(expression, mode="eval")
            for node in ast.walk(tree):
                if not isinstance(node, ALLOWED_NODES):
                    return json.dumps(
                        {"error": f"Disallowed expression: {type(node).__name__}"}
                    )
            ops = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
                ast.Pow: operator.pow,
                ast.Mod: operator.mod,
            }
            result = eval(compile(tree, "<string>", "eval"), {"__builtins__": {}}, ops)
            return json.dumps({"expression": expression, "result": result})
        except Exception as e:
            return json.dumps({"error": f"Calculation failed: {str(e)}"})


def create_default_registry(
    brave_api_key: str | None = None, memory_store: Any = None, user_id: Any = None
):
    from orchestrator.tools.registry import ToolRegistry
    from orchestrator.tools.web_search import WebSearchTool
    from orchestrator.tools.http_request import HttpRequestTool
    from orchestrator.tools.notification import NotificationSendTool
    from orchestrator.tools.reminder import ReminderSetTool, ReminderListTool
    from orchestrator.tools.spawn import SpawnAgentTool, SpawnMultipleTool

    registry = ToolRegistry()
    registry.register(GetTimeTool())
    registry.register(CalculateTool())
    registry.register(WebSearchTool(api_key=brave_api_key))
    registry.register(HttpRequestTool())
    registry.register(NotificationSendTool())
    registry.register(ReminderSetTool())
    registry.register(ReminderListTool())
    registry.register(SpawnAgentTool())
    registry.register(SpawnMultipleTool())

    if memory_store and user_id:
        from orchestrator.memory.tools import MemoryReadTool, MemoryWriteTool

        registry.register(MemoryReadTool(memory_store, user_id))
        registry.register(MemoryWriteTool(memory_store, user_id))

    return registry
