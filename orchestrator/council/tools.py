from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, TypedDict, cast

import litellm

from orchestrator.tools.completion import _wrap_tool_result_untrusted


MessagePayload = dict[str, object]
ToolExecutorCallable = Callable[[str, str | dict[str, object]], Awaitable[str] | str]
CompletionCallable = Callable[..., Awaitable[object]]


class UsagePayload(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class ToolFunctionPayload(TypedDict):
    name: str
    arguments: str


class ToolCallPayload(TypedDict):
    id: str
    type: str
    function: ToolFunctionPayload


class ResponseObject(Protocol):
    choices: list[object]
    usage: object
    _hidden_params: Mapping[str, object] | None


class ChoiceObject(Protocol):
    message: object


class MessageObject(Protocol):
    content: object
    tool_calls: object


class ToolCallObject(Protocol):
    id: object
    type: object
    function: object


class FunctionObject(Protocol):
    name: object
    arguments: object


class UsageObject(Protocol):
    prompt_tokens: object
    completion_tokens: object
    total_tokens: object


class ToolExecutorObject(Protocol):
    async def execute(self, name: str, arguments: str | dict[str, object]) -> str: ...


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return None


def _as_sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return None


def _string_value(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _float_value(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _get_message_content(message: object) -> str:
    mapping = _as_mapping(message)
    if mapping is not None:
        content = mapping.get("content")
    elif hasattr(message, "content"):
        content = cast(MessageObject, message).content
    else:
        content = None

    if isinstance(content, str):
        return content

    blocks = _as_sequence(content)
    if blocks is None:
        return ""

    parts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
            continue

        block_mapping = _as_mapping(block)
        if block_mapping is None:
            continue

        text = block_mapping.get("text") or block_mapping.get("content")
        if isinstance(text, str):
            parts.append(text)

    return "\n".join(part for part in parts if part)


def _extract_usage(response: object) -> UsagePayload:
    usage_payload: UsagePayload = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }

    response_mapping = _as_mapping(response)
    if response_mapping is not None:
        usage = response_mapping.get("usage")
        hidden = response_mapping.get("_hidden_params")
    else:
        response_object = cast(ResponseObject, response)
        usage = response_object.usage if hasattr(response, "usage") else None
        hidden = _get_hidden_params(response)

    usage_mapping = _as_mapping(usage)
    if usage_mapping is not None:
        usage_payload["prompt_tokens"] = _int_value(usage_mapping.get("prompt_tokens"))
        usage_payload["completion_tokens"] = _int_value(usage_mapping.get("completion_tokens"))
        usage_payload["total_tokens"] = _int_value(usage_mapping.get("total_tokens"))
    elif usage is not None:
        usage_object = cast(UsageObject, usage)
        usage_payload["prompt_tokens"] = _int_value(usage_object.prompt_tokens)
        usage_payload["completion_tokens"] = _int_value(usage_object.completion_tokens)
        usage_payload["total_tokens"] = _int_value(usage_object.total_tokens)

    hidden_mapping = _as_mapping(hidden)
    if hidden_mapping is not None:
        usage_payload["cost_usd"] = _float_value(hidden_mapping.get("response_cost", 0))

    return usage_payload


def _get_hidden_params(response: object) -> object | None:
    try:
        return cast(object, object.__getattribute__(response, "_hidden_params"))
    except AttributeError:
        return None


def _accumulate_usage(total: UsagePayload, response: object) -> None:
    usage = _extract_usage(response)
    total["prompt_tokens"] += usage["prompt_tokens"]
    total["completion_tokens"] += usage["completion_tokens"]
    total["total_tokens"] += usage["total_tokens"]
    total["cost_usd"] = round(total["cost_usd"] + usage["cost_usd"], 12)


def _normalize_arguments(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "{}"
    try:
        return json.dumps(value)
    except TypeError:
        return str(value)


def _normalize_tool_call(tool_call: object) -> ToolCallPayload:
    tool_call_mapping = _as_mapping(tool_call)
    if tool_call_mapping is not None:
        function = tool_call_mapping.get("function")
        tool_call_id = tool_call_mapping.get("id")
        tool_call_type = tool_call_mapping.get("type")
    else:
        tool_call_object = cast(ToolCallObject, tool_call)
        function = tool_call_object.function if hasattr(tool_call, "function") else None
        tool_call_id = tool_call_object.id if hasattr(tool_call, "id") else None
        tool_call_type = tool_call_object.type if hasattr(tool_call, "type") else None

    function_mapping = _as_mapping(function)
    if function_mapping is not None:
        name = _string_value(function_mapping.get("name"))
        arguments = _normalize_arguments(function_mapping.get("arguments"))
    elif function is not None:
        function_object = cast(FunctionObject, function)
        name = _string_value(function_object.name) if hasattr(function, "name") else ""
        arguments = (
            _normalize_arguments(function_object.arguments)
            if hasattr(function, "arguments")
            else "{}"
        )
    else:
        name = ""
        arguments = "{}"

    return {
        "id": _string_value(tool_call_id, str(uuid.uuid4())),
        "type": _string_value(tool_call_type, "function"),
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def _extract_tool_calls(message: object) -> list[ToolCallPayload]:
    message_mapping = _as_mapping(message)
    if message_mapping is not None:
        tool_calls = message_mapping.get("tool_calls")
    elif hasattr(message, "tool_calls"):
        tool_calls = cast(MessageObject, message).tool_calls
    else:
        tool_calls = None

    tool_call_items = _as_sequence(tool_calls)
    if tool_call_items is None:
        return []

    return [_normalize_tool_call(tool_call) for tool_call in tool_call_items]


def _extract_message(response: object) -> object | None:
    response_mapping = _as_mapping(response)
    if response_mapping is not None:
        choices = response_mapping.get("choices")
    elif hasattr(response, "choices"):
        choices = cast(ResponseObject, response).choices
    else:
        choices = None

    choice_items = _as_sequence(choices)
    if not choice_items:
        return None

    first_choice = choice_items[0]
    first_choice_mapping = _as_mapping(first_choice)
    if first_choice_mapping is not None:
        return first_choice_mapping.get("message")
    if hasattr(first_choice, "message"):
        return cast(ChoiceObject, first_choice).message
    return None


def _warning_text(max_tool_rounds: int) -> str:
    return f"Warning: council tool loop stopped after reaching max_tool_rounds={max_tool_rounds}."


async def _run_tool_executor(
    tool_executor: ToolExecutorObject | ToolExecutorCallable,
    func_name: str,
    func_args: str | dict[str, object],
) -> str:
    if hasattr(tool_executor, "execute"):
        result: Awaitable[str] | str = cast(ToolExecutorObject, tool_executor).execute(
            func_name, func_args
        )
    elif callable(tool_executor):
        result = tool_executor(func_name, func_args)
    else:
        return json.dumps({"error": "Tool executor is not callable"})

    if inspect.isawaitable(result):
        return await cast(Awaitable[str], result)

    return result


async def council_completion_with_tools(
    model: str,
    messages: list[MessagePayload],
    tools: list[MessagePayload],
    tool_executor: ToolExecutorObject | ToolExecutorCallable,
    timeout: int = 90,
    max_tool_rounds: int = 5,
) -> tuple[str, UsagePayload]:
    current_messages: list[MessagePayload] = list(messages)
    usage_metadata: UsagePayload = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }
    tool_rounds = 0
    acompletion = cast(CompletionCallable, litellm.acompletion)

    while True:
        try:
            response = await acompletion(
                model=model,
                messages=current_messages,
                tools=tools,
                timeout=timeout,
            )
        except Exception as exc:
            return (f"Error: {exc}", usage_metadata)

        _accumulate_usage(usage_metadata, response)

        message = _extract_message(response)
        if message is None:
            return ("Error: No choices returned", usage_metadata)

        response_text = _get_message_content(message)
        tool_calls = _extract_tool_calls(message)
        if not tool_calls:
            return (response_text, usage_metadata)

        if tool_rounds >= max_tool_rounds:
            warning_text = _warning_text(max_tool_rounds)
            if response_text:
                return (f"{response_text}\n\n{warning_text}", usage_metadata)
            return (warning_text, usage_metadata)

        current_messages.append(
            {
                "role": "assistant",
                "content": response_text or None,
                "tool_calls": cast(object, tool_calls),
            }
        )

        for tool_call in tool_calls:
            function = tool_call["function"]
            func_name = function["name"]
            func_args = function["arguments"]

            try:
                result = await _run_tool_executor(tool_executor, func_name, func_args)
            except Exception as exc:
                result = json.dumps({"error": f"Tool execution failed: {exc}"})

            current_messages.append(
                {
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "name": func_name,
                    "content": _wrap_tool_result_untrusted(func_name, result),
                }
            )

        tool_rounds += 1


__all__ = ["council_completion_with_tools"]
