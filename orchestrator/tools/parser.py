from __future__ import annotations

import json
import re
from typing import Any


def parse_function_calls_from_content(
    content: str | None,
) -> list[dict[str, Any]] | None:
    """Parse XML-like function calls from model content.

    Some models return function calls in various formats:
    - <function=name[]>{}</function> (XML-like with closing tag)
    - <function=name>{"key": "value"}</function> (JSON in content)
    - <function=name{"key": "value"}> (compact format, no closing tag)

    This parser extracts them and converts to OpenAI tool_calls format.
    """
    if not content:
        return None

    tool_calls = []

    pattern1 = r"<function=([^\[<>]+)(\[\])?((\{[^}]*\})|(\([^)]*\)))</function>"
    matches = re.findall(pattern1, content, re.DOTALL)

    for i, (func_name, brackets, args_str) in enumerate(matches):
        func_name = func_name.strip()

        if not args_str:
            args_dict = {}
        else:
            try:
                args_dict = json.loads(args_str.strip())
            except json.JSONDecodeError:
                args_dict = {"raw": args_str.strip()}

        tool_calls.append(
            {
                "id": f"call_{i}_{func_name}",
                "type": "function",
                "function": {"name": func_name, "arguments": json.dumps(args_dict)},
            }
        )

    pattern2 = r"<function=([^{>]+)(\{[^}]*\})>"
    matches2 = re.findall(pattern2, content)

    for i, (func_name, args_str) in enumerate(matches2, start=len(tool_calls)):
        func_name = func_name.strip()

        try:
            args_dict = json.loads(args_str)
        except json.JSONDecodeError:
            args_dict = {"raw": args_str}

        tool_calls.append(
            {
                "id": f"call_{i}_{func_name}",
                "type": "function",
                "function": {"name": func_name, "arguments": json.dumps(args_dict)},
            }
        )

    return tool_calls if tool_calls else None


def extract_tool_calls(message: Any) -> tuple[str | None, list[dict[str, Any]] | None]:
    """Extract tool calls from a message, handling both formats.

    Returns: (content, tool_calls_list)
    """
    content = getattr(message, "content", None)
    if content is None and hasattr(message, "get"):
        content = message.get("content", "")

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls is None and hasattr(message, "get"):
        tool_calls = message.get("tool_calls")

    if tool_calls:
        formatted_calls = []
        for tc in tool_calls:
            tc_id = getattr(tc, "id", None) or tc.get("id", "")
            func = getattr(tc, "function", None) or tc.get("function", {})
            func_name = getattr(func, "name", None) or func.get("name", "")
            func_args = getattr(func, "arguments", None) or func.get("arguments", "")

            formatted_calls.append(
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": func_name,
                        "arguments": func_args
                        if isinstance(func_args, str)
                        else json.dumps(func_args),
                    },
                }
            )
        return content, formatted_calls

    parsed = parse_function_calls_from_content(content)
    if parsed and isinstance(content, str):
        clean_content = content
        for pattern in [
            r"<function=[^\[>]+(\[\])?>\s*(\{[^}]*\})?\s*</function>",
            r"<function=[^{>]+\{[^}]*\}>",
        ]:
            clean_content = re.sub(pattern, "", clean_content, flags=re.DOTALL)
        clean_content = clean_content.strip()
        return clean_content if clean_content else None, parsed

    return content, None
