from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

import litellm

from orchestrator.config import ProviderConfig, Settings
from orchestrator.tools.registry import ToolRegistry
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.parser import extract_tool_calls


def _looks_like_tools_unsupported_error(err: Exception) -> bool:
    msg = str(err)
    needles = [
        "tool_choice is not supported",
        "tools is not supported",
        'unsupported"}]}',  # common provider error payloads
    ]
    return any(n in msg for n in needles)


def _extract_last_session_id(messages: list[dict[str, Any]]) -> str | None:
    for msg in reversed(messages):
        role = msg.get("role")
        name = msg.get("name")
        content = msg.get("content")
        parsed: dict[str, Any] | None = None

        if role == "tool" and name == "spawn_agent":
            if not content:
                continue
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
            except Exception:
                parsed = None
        elif role == "assistant" and isinstance(content, str):
            if "Tool spawn_agent result:" in content:
                payload = content.split("Tool spawn_agent result:", 1)[-1].strip()
                try:
                    parsed = json.loads(payload)
                except Exception:
                    parsed = None
        else:
            continue

        if not isinstance(parsed, dict):
            continue

        metadata = parsed.get("metadata")
        if isinstance(metadata, dict):
            session_id = metadata.get("session_id")
            if session_id:
                return session_id
        session_id = parsed.get("session_id")
        if session_id:
            return session_id
    return None


def _extract_last_spawn_result(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for msg in reversed(messages):
        role = msg.get("role")
        name = msg.get("name")
        content = msg.get("content")
        parsed: dict[str, Any] | None = None

        if role == "tool" and name == "spawn_agent":
            if not content:
                continue
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
            except Exception:
                parsed = None
        elif role == "assistant" and isinstance(content, str):
            if "tool_name: spawn_agent" in content and "tool_result:" in content:
                payload = content.split("tool_result:", 1)[-1].strip()
                try:
                    parsed = json.loads(payload)
                except Exception:
                    parsed = None
        else:
            continue

        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_last_user_message(messages: list[dict[str, Any]]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


def _is_retry_request(text: str) -> bool:
    lowered = text.lower()
    if "try again" in lowered or "retry" in lowered or "redo" in lowered:
        return True
    if "again" in lowered:
        return True
    if "different" in lowered or "another" in lowered or "variation" in lowered:
        return True
    # Comparative words that suggest a variation
    if any(
        word in lowered
        for word in [
            "bigger",
            "smaller",
            "larger",
            "louder",
            "quieter",
            "faster",
            "slower",
        ]
    ):
        return True
    if "not " in lowered and ("that" in lowered or "this" in lowered):
        return True
    return False


def _prepare_call_params(
    settings: Settings,
    provider_config: ProviderConfig,
    messages: list[dict[str, Any]],
    actual_model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = True,
) -> dict[str, Any]:
    model_to_use = actual_model if actual_model else provider_config.model

    call_params: dict[str, Any] = {
        "model": model_to_use,
        "messages": messages,
        "stream": stream,
        "timeout": provider_config.timeout_s,
    }

    if tools:
        call_params["tools"] = tools
        call_params["tool_choice"] = "auto"

    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url

    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    elif provider_config.requires_auth:
        raise RuntimeError(
            f"{provider_config.name} requires an API key but none was provided"
        )

    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    if provider_config.name == "opencode_zen":
        call_params["model"] = f"openai/{model_to_use}"

    return call_params


async def _accumulate_stream_with_tools(
    stream: AsyncIterator[Any],
) -> tuple[str, list[dict[str, Any]]]:
    content_parts: list[str] = []
    tool_calls_buffer: dict[int, dict[str, Any]] = {}

    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or chunk.get("choices", [])
        if not choices:
            continue

        delta = getattr(choices[0], "delta", None) or choices[0].get("delta", {})
        if not delta:
            continue

        if hasattr(delta, "content") and delta.content:
            content_parts.append(delta.content)
        elif isinstance(delta, dict) and delta.get("content"):
            content_parts.append(delta["content"])

        tool_calls = getattr(delta, "tool_calls", None) or delta.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                idx = (
                    getattr(tc, "index", 0)
                    if hasattr(tc, "index")
                    else tc.get("index", 0)
                )

                if idx not in tool_calls_buffer:
                    tc_id = getattr(tc, "id", None) or tc.get("id", "")
                    tool_calls_buffer[idx] = {
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }

                func = getattr(tc, "function", None) or tc.get("function", {})
                func_name = getattr(func, "name", None) or func.get("name")
                func_args = getattr(func, "arguments", None) or func.get("arguments")

                if func_name:
                    tool_calls_buffer[idx]["function"]["name"] = func_name
                if func_args:
                    tool_calls_buffer[idx]["function"]["arguments"] += func_args

    tool_calls_list = list(tool_calls_buffer.values())
    return "".join(content_parts), tool_calls_list


async def completion_with_tools(
    settings: Settings,
    provider_config: ProviderConfig,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
    actual_model: str | None = None,
    max_tool_rounds: int = 5,
) -> AsyncIterator[dict[str, Any]]:
    executor = ToolExecutor(registry)
    tools = registry.list_schemas() if len(registry) > 0 else None
    current_messages = list(messages)

    native_tools_enabled = tools is not None
    last_spawn_session_id: str | None = None

    for round_num in range(max_tool_rounds):
        call_params = _prepare_call_params(
            settings,
            provider_config,
            current_messages,
            actual_model,
            tools if native_tools_enabled else None,
            stream=True,
        )

        # Buffer for accumulating tool calls across stream chunks
        tool_calls_buffer: dict[int, dict[str, Any]] = {}
        content_buffer: list[str] = []

        try:
            response_stream = await litellm.acompletion(**call_params)

            async for chunk in response_stream:
                choices = getattr(chunk, "choices", None) or chunk.get("choices", [])
                if not choices:
                    continue

                delta = getattr(choices[0], "delta", None) or choices[0].get(
                    "delta", {}
                )
                if not delta:
                    continue

                # 1. Handle Thinking/Reasoning (if present)
                # Some models put it in 'reasoning_content', others in 'thinking'
                reasoning = (
                    getattr(delta, "reasoning_content", None)
                    or delta.get("reasoning_content")
                    or getattr(delta, "thinking", None)
                    or delta.get("thinking")
                )
                if reasoning:
                    yield {
                        "type": "thinking",
                        "content": reasoning,
                        "id": str(uuid.uuid4()),
                    }

                # 2. Handle Content
                content_chunk = getattr(delta, "content", None) or delta.get("content")
                if content_chunk:
                    content_buffer.append(content_chunk)
                    yield {
                        "type": "content",
                        "content": content_chunk,
                        "id": str(uuid.uuid4()),
                    }

                # 3. Handle Tool Calls
                tool_calls_chunk = getattr(delta, "tool_calls", None) or delta.get(
                    "tool_calls"
                )
                if tool_calls_chunk:
                    for tc in tool_calls_chunk:
                        idx = (
                            getattr(tc, "index", 0)
                            if hasattr(tc, "index")
                            else tc.get("index", 0)
                        )

                        if idx not in tool_calls_buffer:
                            tc_id = getattr(tc, "id", None) or tc.get("id", "")
                            tool_calls_buffer[idx] = {
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }

                        func = getattr(tc, "function", None) or tc.get("function", {})
                        func_name = getattr(func, "name", None) or func.get("name")
                        func_args = getattr(func, "arguments", None) or func.get(
                            "arguments"
                        )

                        if func_name:
                            tool_calls_buffer[idx]["function"]["name"] = func_name
                        if func_args:
                            tool_calls_buffer[idx]["function"]["arguments"] += func_args

        except Exception as e:
            # Fallback for errors (including tool unsupported errors in streaming mode)
            if native_tools_enabled and _looks_like_tools_unsupported_error(e):
                # ... (Fallback logic would be complex to stream, let's keep it simple for now and yield error)
                yield {
                    "type": "error",
                    "error": f"Streaming tool error: {str(e)}",
                    "id": str(uuid.uuid4()),
                }
                return
            else:
                yield {"type": "error", "error": str(e), "id": str(uuid.uuid4())}
                return

        # End of stream for this round
        tool_calls = list(tool_calls_buffer.values())
        full_content = "".join(content_buffer)

        # If we had tool calls, process them
        if tool_calls:
            yield {
                "type": "tool_calls",
                "tool_calls": len(tool_calls),
                "round": round_num,
                "id": str(uuid.uuid4()),
            }

            if native_tools_enabled:
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": full_content or None,
                    "tool_calls": tool_calls,
                }
            else:
                assistant_msg = {
                    "role": "assistant",
                    "content": full_content or "",
                }
            current_messages.append(assistant_msg)

            for tc in tool_calls:
                func_name = tc["function"]["name"]
                func_args = tc["function"]["arguments"]

                if func_name in {"spawn_agent", "spawn_multiple"}:
                    try:
                        parsed_args = (
                            json.loads(func_args)
                            if isinstance(func_args, str) and func_args
                            else {}
                        )
                    except Exception:
                        parsed_args = {}
                    if not parsed_args.get("session_id"):
                        last_session_id = (
                            last_spawn_session_id
                            or _extract_last_session_id(current_messages)
                        )
                        if last_session_id:
                            if func_name == "spawn_agent":
                                parsed_args["session_id"] = last_session_id
                            elif isinstance(parsed_args.get("agents"), list):
                                for agent_spec in parsed_args["agents"]:
                                    if isinstance(
                                        agent_spec, dict
                                    ) and not agent_spec.get("session_id"):
                                        agent_spec["session_id"] = last_session_id
                            func_args = json.dumps(parsed_args)
                            tc["function"]["arguments"] = func_args

                yield {
                    "type": "tool_executing",
                    "name": func_name,
                    "arguments": func_args,
                    "id": str(uuid.uuid4()),
                }

                # Execute tool (this is still blocking, which is fine as we need the result)
                result = await executor.execute(func_name, func_args)
                if func_name in {"spawn_agent", "spawn_multiple"}:
                    try:
                        parsed_result = (
                            json.loads(result) if isinstance(result, str) else result
                        )
                    except Exception:
                        parsed_result = None
                    if isinstance(parsed_result, dict):
                        metadata = parsed_result.get("metadata")
                        session_id = (
                            metadata.get("session_id")
                            if isinstance(metadata, dict)
                            else None
                        )
                        if not session_id and func_name == "spawn_multiple":
                            results = parsed_result.get("results")
                            if isinstance(results, list) and results:
                                last_result = results[-1]
                                if isinstance(last_result, dict):
                                    last_meta = last_result.get("metadata")
                                    if isinstance(last_meta, dict):
                                        session_id = last_meta.get("session_id")
                        if session_id:
                            last_spawn_session_id = session_id

                yield {
                    "type": "tool_result",
                    "name": func_name,
                    "result": result,
                    "id": str(uuid.uuid4()),
                }

                if native_tools_enabled:
                    current_messages.append(
                        {
                            "tool_call_id": tc["id"],
                            "role": "tool",
                            "name": func_name,
                            "content": result,
                        }
                    )
                else:
                    current_messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "Tool result available. Use it to answer the user.\n"
                                f"tool_name: {func_name}\n"
                                f"tool_result: {result}"
                            ),
                        }
                    )
            # Loop continues to next round

        else:
            last_spawn_result = _extract_last_spawn_result(current_messages)
            last_session_id = None
            last_agent_type = "image"
            if isinstance(last_spawn_result, dict):
                metadata = last_spawn_result.get("metadata")
                if isinstance(metadata, dict):
                    last_session_id = metadata.get("session_id")
                if not last_session_id:
                    last_session_id = last_spawn_result.get("session_id")
                agent_type = last_spawn_result.get("agent_type")
                if isinstance(agent_type, str) and agent_type:
                    last_agent_type = agent_type

            last_user_message = _extract_last_user_message(current_messages)
            if (
                tools is not None
                and last_session_id
                and last_user_message
                and _is_retry_request(last_user_message)
            ):
                func_args = json.dumps(
                    {
                        "agent_type": last_agent_type,
                        "task": last_user_message,
                        "session_id": last_session_id,
                    }
                )
                yield {
                    "type": "tool_executing",
                    "name": "spawn_agent",
                    "arguments": func_args,
                    "id": str(uuid.uuid4()),
                }

                result = await executor.execute("spawn_agent", func_args)
                yield {
                    "type": "tool_result",
                    "name": "spawn_agent",
                    "result": result,
                    "id": str(uuid.uuid4()),
                }

                if native_tools_enabled:
                    current_messages.append(
                        {
                            "tool_call_id": "auto_spawn_agent",
                            "role": "tool",
                            "name": "spawn_agent",
                            "content": result,
                        }
                    )
                else:
                    current_messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "Tool result available. Use it to answer the user.\n"
                                "tool_name: spawn_agent\n"
                                f"tool_result: {result}"
                            ),
                        }
                    )

                yield {"type": "done", "done": True, "id": str(uuid.uuid4())}
                return

            # No tool calls, we are done
            yield {"type": "done", "done": True, "id": str(uuid.uuid4())}
            return
