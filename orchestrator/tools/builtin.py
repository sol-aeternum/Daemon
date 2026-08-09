from __future__ import annotations

from datetime import datetime
import json
from typing import Any, TypeAlias

from zoneinfo import ZoneInfo

from orchestrator.tools.registry import Tool
from orchestrator.tools.web_search import WebSearchTool
from orchestrator.tools.web_fetch import WebFetchTool


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
        expression = kwargs.get("expression", "")
        if not isinstance(expression, str):
            return json.dumps({"error": "expression must be a string"})
        try:
            result = _evaluate_math_expression(expression)
            if type(result) is complex:
                raise _MathError("complex results are not supported")
            return json.dumps({"expression": expression, "result": result})
        except RecursionError:
            return json.dumps({"error": "Calculation failed: expression is too deeply nested"})
        except (_MathError, ArithmeticError, TypeError, ValueError) as e:
            return json.dumps({"error": f"Calculation failed: {e}"})


class _MathError(ValueError):
    pass


Number: TypeAlias = int | float


class _MathParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.length = len(text)

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < self.length else ""

    def consume(self) -> str:
        ch = self.text[self.pos]
        self.pos += 1
        return ch

    def skip_whitespace(self) -> None:
        while self.pos < self.length and self.text[self.pos].isspace():
            self.pos += 1

    def parse_expression(self) -> Number:
        value = self.parse_term()
        while True:
            self.skip_whitespace()
            ch = self.peek()
            if ch == "+":
                self.consume()
                value = value + self.parse_term()
            elif ch == "-":
                self.consume()
                value = value - self.parse_term()
            else:
                return value

    def parse_term(self) -> Number:
        value = self.parse_factor()
        while True:
            self.skip_whitespace()
            ch = self.peek()
            if ch == "*":
                self.consume()
                value = value * self.parse_factor()
            elif ch == "/":
                self.consume()
                divisor = self.parse_factor()
                if divisor == 0:
                    raise _MathError("division by zero")
                value = value / divisor
            elif ch == "%":
                self.consume()
                divisor = self.parse_factor()
                if divisor == 0:
                    raise _MathError("division by zero")
                value = value % divisor
            else:
                return value

    def parse_factor(self) -> Number:
        sign = 1
        while True:
            self.skip_whitespace()
            ch = self.peek()
            if ch == "+":
                self.consume()
            elif ch == "-":
                self.consume()
                sign *= -1
            else:
                value = self.parse_power()
                return value if sign == 1 else -value

    def parse_power(self) -> Number:
        base = self.parse_primary()
        self.skip_whitespace()
        if self.peek() == "*" and self.pos + 1 < self.length and self.text[self.pos + 1] == "*":
            self.consume()
            self.consume()
            exponent = self.parse_factor()
            return base**exponent
        return base

    def parse_primary(self) -> Number:
        self.skip_whitespace()
        if self.peek() == "(":
            self.consume()
            value = self.parse_expression()
            self.skip_whitespace()
            if self.peek() != ")":
                raise _MathError("unmatched '('")
            self.consume()
            return value
        return self.parse_number()

    def parse_number(self) -> Number:
        self.skip_whitespace()
        start = self.pos
        if self.peek() == ".":
            self.consume()
        while self.pos < self.length and self.text[self.pos].isdigit():
            self.pos += 1
        if self.pos < self.length and self.text[self.pos] == ".":
            self.pos += 1
            while self.pos < self.length and self.text[self.pos].isdigit():
                self.pos += 1
        if self.pos < self.length and self.text[self.pos] in ("e", "E"):
            self.pos += 1
            if self.pos < self.length and self.text[self.pos] in ("+", "-"):
                self.pos += 1
            while self.pos < self.length and self.text[self.pos].isdigit():
                self.pos += 1
        literal = self.text[start : self.pos]
        if not literal or literal == ".":
            raise _MathError(f"unexpected character {self.peek()!r}")
        try:
            if "." not in literal and "e" not in literal.lower():
                return int(literal)
            return float(literal)
        except ValueError as e:
            raise _MathError(f"invalid number {literal!r}") from e


def _evaluate_math_expression(expression: str) -> Number:
    parser = _MathParser(expression)
    result = parser.parse_expression()
    parser.skip_whitespace()
    if parser.pos != parser.length:
        raise _MathError(f"unexpected trailing input at position {parser.pos}")
    return result


def create_default_registry(
    brave_api_key: str | None = None,
    memory_store: Any = None,
    user_id: Any = None,
    db_pool: Any = None,
    trusted_spawn_context: dict[str, Any] | None = None,
    disable_memory_write: bool = False,
):
    from orchestrator.tools.registry import ToolRegistry
    from orchestrator.tools.http_request import HttpRequestTool
    from orchestrator.tools.notification import NotificationSendTool
    from orchestrator.tools.reminder import ReminderSetTool, ReminderListTool
    from orchestrator.tools.spawn import SpawnAgentTool, SpawnMultipleTool

    registry = ToolRegistry()
    registry.register(GetTimeTool())
    registry.register(CalculateTool())
    registry.register(WebSearchTool(api_key=brave_api_key))
    registry.register(WebFetchTool())
    registry.register(HttpRequestTool())
    registry.register(NotificationSendTool())
    registry.register(ReminderSetTool())
    registry.register(ReminderListTool())
    registry.register(SpawnAgentTool(db_pool=db_pool, trusted_spawn_context=trusted_spawn_context))
    registry.register(
        SpawnMultipleTool(db_pool=db_pool, trusted_spawn_context=trusted_spawn_context)
    )

    from orchestrator.tools.skill_manage import SkillManageTool
    from orchestrator.tools.document import GenerateDocumentTool

    registry.register(SkillManageTool(db_pool=db_pool))
    registry.register(GenerateDocumentTool())

    if memory_store and user_id:
        from orchestrator.memory.tools import MemoryReadTool, MemoryWriteTool
        from orchestrator.tools.memory_promote import MemoryPromoteTool
        from orchestrator.tools.memory_demote import MemoryDemoteTool
        from orchestrator.tools.memory_reflect import MemoryReflectTool

        registry.register(MemoryReadTool(memory_store, user_id))
        if not disable_memory_write:
            registry.register(MemoryWriteTool(memory_store, user_id))
        registry.register(MemoryPromoteTool(memory_store, user_id))
        registry.register(MemoryDemoteTool(memory_store, user_id))
        registry.register(MemoryReflectTool(memory_store, user_id))

    return registry


def create_council_readonly_registry(brave_api_key: str | None = None):
    """Create the read-only tool registry available inside council deliberations."""
    from orchestrator.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(GetTimeTool())
    registry.register(CalculateTool())
    registry.register(WebSearchTool(api_key=brave_api_key))
    registry.register(WebFetchTool())
    return registry


def create_advisor_registry():
    """Create the constrained tool registry available inside advisor calls."""
    from orchestrator.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(GetTimeTool())
    registry.register(CalculateTool())
    return registry
