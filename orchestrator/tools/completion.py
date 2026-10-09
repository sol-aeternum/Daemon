from __future__ import annotations

from orchestrator.compute_runtime import guarded_completion
from orchestrator.compute_runtime import ComputeUnavailable, current_scope
from orchestrator import model_routing, routing_log
from orchestrator.entitlements.errors import EntitlementsError

import contextlib
import json
import logging
from contextlib import asynccontextmanager

import re

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator, cast


from orchestrator.config import ProviderConfig, Settings
from orchestrator.guardrails import strip_reasoning_fields_from_message
from orchestrator.tools.registry import ToolRegistry
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.context_budget import (
    OMITTED_RESULT,
    SKIPPED_RESULT,
    synthesis_messages,
    tool_context_budget,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _closing_stream(stream: AsyncIterator[Any]) -> AsyncIterator[AsyncIterator[Any]]:
    try:
        yield stream
    finally:
        close = getattr(stream, "aclose", None)
        if callable(close):
            await cast(Callable[[], Awaitable[None]], close)()


# Tool results reach the LLM as plain text. Adversarial tool outputs (web pages,
# fetched files, memory records) can contain instructions like "Ignore previous
# instructions and ...". To bound the prompt-injection surface we wrap every tool
# result in a strict XML fence with a `trust="untrusted"` attribute, and the
# system prompt explicitly tells the model to treat the contents as DATA, not
# INSTRUCTIONS. The model is also instructed to ignore any instruction-like text
# inside the fence. This mirrors the <memory_records> fence from issue #19.
_TOOL_RESULT_FENCE_TAG = "tool_result"


def _sanitize_xml_attr(value: str) -> str:
    """Escape characters that could break out of an XML attribute value.

    Tool names originate from the trusted registry, but a defense-in-depth
    escape prevents any future tool name from being able to terminate the
    `<tool_result tool="...">` attribute and inject a fake closing tag.
    """
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _wrap_tool_result_untrusted(tool_name: str, body: str) -> str:
    """Wrap a tool result body in a strict <tool_result> fence.

    The fence has a `trust="untrusted"` attribute so the model can pattern-match
    on it after the system prompt teaches it to. Any literal closing tag inside
    the body is neutralized so adversarial tool output cannot break out of the
    fence; parsers that need the original body must go through
    `_unwrap_tool_result`. The opening and closing tags are on their own lines
    so log scrapers and regex audits can detect them unambiguously.
    """
    safe_name = _sanitize_xml_attr(tool_name)
    # Neutralize anything shaped like a closing tag, including XML-valid
    # whitespace/case variants such as "</tool_result >".
    safe_body = re.sub(
        rf"<\s*/\s*{_TOOL_RESULT_FENCE_TAG}\s*>",
        "&lt;/tool_result&gt;",
        body,
        flags=re.IGNORECASE,
    )
    return (
        f'<{_TOOL_RESULT_FENCE_TAG} tool="{safe_name}" trust="untrusted">\n'
        f"{safe_body}\n"
        f"</{_TOOL_RESULT_FENCE_TAG}>"
    )


def _unwrap_tool_result(content: str) -> str:
    """Strip the fence added by `_wrap_tool_result_untrusted`, if present.

    Returns the inner body so existing parsers (e.g. session_id extraction from
    spawn_agent results) keep operating on the raw tool output. Body escaping in
    the wrapper guarantees the final closing tag is ours. Content without a
    fence is returned unchanged.
    """
    stripped = content.strip()
    closing = f"</{_TOOL_RESULT_FENCE_TAG}>"
    if not stripped.startswith(f"<{_TOOL_RESULT_FENCE_TAG} ") or not stripped.endswith(closing):
        return content
    open_end = stripped.find(">")
    if open_end == -1:
        return content
    body = stripped[open_end + 1 : -len(closing)].strip("\n")
    # Reverse the wrapper's neutralization so parsers (e.g. spawn_agent
    # metadata fields that legitimately contain the literal closing tag)
    # see the original data, not the entity-escaped form. Whitespace/case
    # variants are normalized to the canonical tag on round-trip.
    return body.replace("&lt;/tool_result&gt;", closing)


def _tool_result_message(tc: dict[str, Any], result: str, native: bool) -> dict[str, Any]:
    name = tc["function"]["name"]
    if native:
        return {
            "tool_call_id": tc["id"],
            "role": "tool",
            "name": name,
            "content": _wrap_tool_result_untrusted(name, result),
        }
    return {
        "role": "assistant",
        "content": _wrap_tool_result_untrusted(
            name,
            f"Tool result available. Use it to answer the user.\ntool_name: {name}\ntool_result: {result}",
        ),
    }


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
                parsed = (
                    json.loads(_unwrap_tool_result(content))
                    if isinstance(content, str)
                    else content
                )
            except Exception:
                parsed = None
        elif role == "assistant" and isinstance(content, str):
            unwrapped = _unwrap_tool_result(content)
            if "Tool spawn_agent result:" in unwrapped:
                payload = unwrapped.split("Tool spawn_agent result:", 1)[-1].strip()
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
                parsed = (
                    json.loads(_unwrap_tool_result(content))
                    if isinstance(content, str)
                    else content
                )
            except Exception:
                parsed = None
        elif role == "assistant" and isinstance(content, str):
            unwrapped = _unwrap_tool_result(content)
            if "tool_name: spawn_agent" in unwrapped and "tool_result:" in unwrapped:
                payload = unwrapped.split("tool_result:", 1)[-1].strip()
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
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") != "text":
                        continue
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())
                if text_parts:
                    return "\n".join(text_parts)
    return None


from orchestrator.tools.retry import is_retry_request  # noqa: E402


def _reasoning_text_from_details(reasoning_details: Any) -> str | None:
    """Extract human-readable reasoning text from streaming reasoning_details."""

    if not reasoning_details:
        return None

    parts: list[str] = []

    if isinstance(reasoning_details, list):
        for item in reasoning_details:
            text: Any = None
            if isinstance(item, dict):
                text = item.get("text") or item.get("summary")
            else:
                text = getattr(item, "text", None) or getattr(item, "summary", None)
            if text:
                parts.append(str(text))
    elif isinstance(reasoning_details, dict):
        text_val = reasoning_details.get("text") or reasoning_details.get("summary")
        if text_val:
            parts.append(str(text_val))
    else:
        text_val = getattr(reasoning_details, "text", None) or getattr(
            reasoning_details, "summary", None
        )
        if text_val:
            parts.append(str(text_val))

    return "".join(parts) if parts else None


def _prepare_call_params(
    settings: Settings,
    provider_config: ProviderConfig,
    messages: list[dict[str, Any]],
    actual_model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = True,
) -> dict[str, Any]:
    model_to_use = actual_model if actual_model else provider_config.model

    # Guardrail: reasoning is persisted for UX/telemetry, but must never be sent
    # back to an LLM as part of the prompt/history payload.
    sanitized_messages = [strip_reasoning_fields_from_message(m) for m in messages]

    call_params: dict[str, Any] = {
        "model": model_to_use,
        "messages": sanitized_messages,
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
        raise RuntimeError(f"{provider_config.name} requires an API key but none was provided")

    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    # Candidate-specific reasoning controls belong to guarded dispatch: auto
    # selection or fallback can choose a different model from this caller hint.

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
                idx = getattr(tc, "index", 0) if hasattr(tc, "index") else tc.get("index", 0)

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


#: Refusals of an automatic reasoning dispatch that may be served, disclosed, under the
#: routine profile instead (optional work O1). Provider outages and every other
#: refusal keep their existing behaviour.
_FALLBACK_CAUSES = frozenset({"capability_unavailable", "budget_exceeded"})


def _reasoning_fallback_cause(refusal: ComputeUnavailable) -> str | None:
    """The disclosed-fallback cause for ``refusal``, or ``None`` when it must stand.

    Only an *inferred* reasoning request qualifies: an automatic chat scope whose
    active profile is ``reasoning``. Explicit selections and background work keep
    their refusal.
    """
    if refusal.code not in _FALLBACK_CAUSES:
        return None
    routing = model_routing.active_routing()
    if routing is None or routing.profile != "reasoning":
        return None
    try:
        scope = current_scope()
    except ComputeUnavailable:
        return None
    if not scope.auto_route or scope.operation != "chat":
        return None
    return refusal.code


async def _under_profile(profile: str, call: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``call`` under ``profile`` and keep the turn's attribution current."""
    outer = model_routing.active_routing()
    with model_routing.routing_context(profile) as inner:
        result = await call()
    if (
        outer is not None
        and inner.selected_model is not None
        and inner.selected_route_id is not None
    ):
        outer.record_selection(
            model=inner.selected_model,
            route_id=inner.selected_route_id,
            group=inner.selected_group,
            explicit=inner.explicit,
            effort=inner.selected_effort,
        )
    return result


def _apply_caller_controls(
    params: dict[str, Any],
    max_output_tokens: int | None,
    call_overrides: dict[str, Any] | None,
) -> None:
    """Apply a caller's own output cap and explicit-model request controls.

    The cap only ever lowers an output budget the loop already chose. Overrides are
    the compatibility endpoint's explicitly set request parameters for an explicit
    model; the guarded runtime still validates every one of them.
    """
    if max_output_tokens is not None:
        current = params.get("max_tokens")
        params["max_tokens"] = (
            max_output_tokens if not isinstance(current, int) else min(current, max_output_tokens)
        )
    if call_overrides:
        params.update(call_overrides)


def _fallback_event(cause: str) -> dict[str, Any]:
    with contextlib.suppress(ComputeUnavailable):
        routing_log.emit(
            "profile_fallback",
            scope_id=str(current_scope().scope_id),
            from_profile="reasoning",
            to_profile="routine",
            cause=cause,
        )
    return {
        "type": "routing_fallback",
        "from_profile": "reasoning",
        "to_profile": "routine",
        "cause": cause,
        "id": str(uuid.uuid4()),
    }


async def completion_with_tools(
    settings: Settings,
    provider_config: ProviderConfig,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
    actual_model: str | None = None,
    max_tool_rounds: int = 5,
    *,
    completion_dispatch: Callable[..., Awaitable[Any]] | None = None,
    reasoning_fallback: bool = False,
    max_output_tokens: int | None = None,
    call_overrides: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    # The evaluation adapter observes this exact loop and keeps its dispatches
    # inside the ordinary qualification/accounting runtime. Normal callers use
    # the same guarded completion as before; no provider request is reimplemented.
    dispatch = completion_dispatch or guarded_completion
    # Once a turn falls back to the routine profile it stays there, so later rounds
    # and the context budget are computed for the model that is actually answering.
    fallback_profile: str | None = None

    async def routed(call: Callable[[], Awaitable[Any]]) -> Any:
        if fallback_profile is None:
            return await call()
        return await _under_profile(fallback_profile, call)

    executor = ToolExecutor(registry)
    tools = registry.list_schemas() if len(registry) > 0 else None
    native_tools_enabled = tools is not None
    current_messages = list(messages)
    fetch_tool = registry.get("web_fetch")
    if fetch_tool is not None:
        manifest_params = _prepare_call_params(
            settings,
            provider_config,
            current_messages,
            actual_model=actual_model,
            tools=tools if native_tools_enabled else None,
        )
        manifest_budget = await tool_context_budget(manifest_params, current_messages)
        if manifest_budget is not None:
            manifest = await fetch_tool.execute(action="list", limit=20)
            try:
                manifest_data = json.loads(manifest)
            except (ValueError, TypeError):
                manifest_data = None
            manifest_message = {
                "role": "user",
                "content": _wrap_tool_result_untrusted(
                    "web_fetch", "Saved source references (not page contents):\n" + manifest
                ),
            }
            # Keep the real user request last: retry/task classifiers inspect it.
            user_index = next(
                (
                    index
                    for index in range(len(current_messages) - 1, -1, -1)
                    if current_messages[index].get("role") == "user"
                ),
                len(current_messages),
            )
            with_manifest = list(current_messages)
            with_manifest.insert(user_index, manifest_message)
            if (
                isinstance(manifest_data, dict)
                and manifest_data.get("sources")
                and manifest_budget.fits(
                    with_manifest, tools=tools if native_tools_enabled else None
                )
            ):
                current_messages = with_manifest

    last_spawn_session_id: str | None = None
    context_exhausted = False
    last_context_budget = None

    for round_num in range(max_tool_rounds):
        call_params = _prepare_call_params(
            settings,
            provider_config,
            current_messages,
            actual_model,
            tools if native_tools_enabled else None,
            stream=True,
        )
        if last_context_budget is not None and last_context_budget.output_tokens > 0:
            call_params["max_tokens"] = last_context_budget.output_tokens
        _apply_caller_controls(call_params, max_output_tokens, call_overrides)

        # Buffer for accumulating tool calls across stream chunks
        tool_calls_buffer: dict[int, dict[str, Any]] = {}
        content_buffer: list[str] = []

        try:
            try:
                response_stream = await routed(lambda: dispatch(**call_params))
            except ComputeUnavailable as refusal:
                cause = (
                    _reasoning_fallback_cause(refusal)
                    if reasoning_fallback and fallback_profile is None
                    else None
                )
                if cause is None:
                    raise
                fallback_profile = "routine"
                yield _fallback_event(cause)
                response_stream = await routed(lambda: dispatch(**call_params))
            stream_iter = cast(AsyncIterator[Any], response_stream)

            async with _closing_stream(stream_iter):
                async for chunk in stream_iter:
                    choices = getattr(chunk, "choices", None) or chunk.get("choices", [])
                    if not choices:
                        continue

                    delta = getattr(choices[0], "delta", None) or choices[0].get("delta", {})
                    if not delta:
                        continue

                    # 1. Handle Thinking/Reasoning (if present)
                    # Some providers emit `reasoning_content` / `thinking`, others stream `reasoning_details`.
                    reasoning = (
                        getattr(delta, "reasoning_content", None)
                        or (delta.get("reasoning_content") if isinstance(delta, dict) else None)
                        or getattr(delta, "thinking", None)
                        or (delta.get("thinking") if isinstance(delta, dict) else None)
                    )
                    if not reasoning:
                        reasoning_details = getattr(delta, "reasoning_details", None) or (
                            delta.get("reasoning_details") if isinstance(delta, dict) else None
                        )
                        reasoning = _reasoning_text_from_details(reasoning_details)

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
                        # Yield incremental delta for real-time streaming
                        yield {
                            "type": "content_delta",
                            "content": content_chunk,
                            "id": str(uuid.uuid4()),
                        }

                    # 3. Handle Tool Calls
                    tool_calls_chunk = getattr(delta, "tool_calls", None) or delta.get("tool_calls")
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
                            func_args = getattr(func, "arguments", None) or func.get("arguments")

                            if func_name:
                                tool_calls_buffer[idx]["function"]["name"] = func_name
                            if func_args:
                                tool_calls_buffer[idx]["function"]["arguments"] += func_args

        except (ComputeUnavailable, EntitlementsError):
            raise
        except Exception as e:
            logger.warning("Streaming completion failed", exc_info=True)
            # Fallback for errors (including tool unsupported errors in streaming mode)
            if native_tools_enabled and _looks_like_tools_unsupported_error(e):
                # ... (Fallback logic would be complex to stream, let's keep it simple for now and yield error)
                yield {
                    "type": "error",
                    "error": "Streaming tool unavailable",
                    "id": str(uuid.uuid4()),
                }
                return
            else:
                yield {"type": "error", "error": "Request failed", "id": str(uuid.uuid4())}
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

            # Reserve response pairing/error overhead for every requested tool,
            # plus a tools-disabled answer, before accepting external evidence.
            placeholders = [
                _tool_result_message(tc, SKIPPED_RESULT + " " * 256, native_tools_enabled)
                for tc in tool_calls
            ]
            last_context_budget = await routed(
                lambda: tool_context_budget(call_params, [*current_messages, *placeholders])
            )

            for tool_index, tc in enumerate(tool_calls):
                invocation_metadata: dict[str, Any] = {}
                func_name = tc["function"]["name"]
                func_args = tc["function"]["arguments"]
                remaining_placeholders = placeholders[tool_index + 1 :]

                def fits_result(candidate: str) -> bool:
                    return last_context_budget is None or last_context_budget.fits(
                        [
                            *current_messages,
                            _tool_result_message(tc, candidate, native_tools_enabled),
                            *remaining_placeholders,
                        ]
                    )

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
                        last_session_id = last_spawn_session_id or _extract_last_session_id(
                            current_messages
                        )
                        if last_session_id:
                            if func_name == "spawn_agent":
                                parsed_args["session_id"] = last_session_id
                            elif isinstance(parsed_args.get("agents"), list):
                                for agent_spec in parsed_args["agents"]:
                                    if isinstance(agent_spec, dict) and not agent_spec.get(
                                        "session_id"
                                    ):
                                        agent_spec["session_id"] = last_session_id
                            func_args = json.dumps(parsed_args)
                            tc["function"]["arguments"] = func_args

                yield {
                    "type": "tool_executing",
                    "name": func_name,
                    "arguments": func_args,
                    "id": str(uuid.uuid4()),
                }

                if not fits_result(SKIPPED_RESULT):
                    context_exhausted = True
                if context_exhausted:
                    result = SKIPPED_RESULT
                else:
                    tool = registry.get(func_name)
                    set_allowance = getattr(tool, "set_result_allowance", None)
                    if callable(set_allowance):
                        set_allowance(fits_result)
                    try:
                        invocation = await executor.execute_invocation(func_name, func_args)
                        result = invocation.result
                        invocation_metadata = invocation.event_metadata()
                    finally:
                        if callable(set_allowance):
                            set_allowance(None)
                    if not fits_result(result):
                        result = OMITTED_RESULT
                        invocation_metadata = invocation.event_metadata(suppressed=True)
                        context_exhausted = True
                    elif func_name == "web_fetch":
                        try:
                            parsed_budget_result = json.loads(result)
                        except (ValueError, TypeError):
                            parsed_budget_result = None
                        if (
                            isinstance(parsed_budget_result, dict)
                            and parsed_budget_result.get("error") == "context_budget_exhausted"
                        ):
                            context_exhausted = True
                if func_name in {"spawn_agent", "spawn_multiple"}:
                    try:
                        parsed_result = json.loads(result) if isinstance(result, str) else result
                    except Exception:
                        parsed_result = None
                    if isinstance(parsed_result, dict):
                        metadata = parsed_result.get("metadata")
                        session_id = (
                            metadata.get("session_id") if isinstance(metadata, dict) else None
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
                    **invocation_metadata,
                    "id": str(uuid.uuid4()),
                }

                current_messages.append(_tool_result_message(tc, result, native_tools_enabled))
            if context_exhausted or (
                last_context_budget is not None
                and not last_context_budget.fits(
                    current_messages, tools=tools if native_tools_enabled else None
                )
            ):
                break
            # Loop continues to next round

        else:
            if full_content:
                yield {
                    "type": "content_done",
                    "content": full_content,
                    "id": str(uuid.uuid4()),
                }
            last_spawn_result = _extract_last_spawn_result(current_messages)
            last_session_id = None
            last_generation_code = None
            last_agent_type = "image"
            if isinstance(last_spawn_result, dict):
                metadata = last_spawn_result.get("metadata")
                if isinstance(metadata, dict):
                    last_session_id = metadata.get("session_id")
                    last_generation_code = metadata.get("generation_code")
                if not last_session_id:
                    last_session_id = last_spawn_result.get("session_id")
                agent_type = last_spawn_result.get("agent_type")
                if isinstance(agent_type, str) and agent_type:
                    last_agent_type = agent_type

            last_user_message = _extract_last_user_message(current_messages)
            if (
                tools is not None
                and registry.get("spawn_agent") is not None
                and last_session_id
                and last_user_message
                and is_retry_request(last_user_message)
            ):
                func_args_dict = {
                    "agent_type": last_agent_type,
                    "task": last_user_message,
                    "session_id": last_session_id,
                }
                # Pass generation_code for document revisions
                if last_generation_code:
                    func_args_dict["context"] = {"original_code": last_generation_code}
                func_args = json.dumps(func_args_dict)
                yield {
                    "type": "tool_executing",
                    "name": "spawn_agent",
                    "arguments": func_args,
                    "id": str(uuid.uuid4()),
                }

                invocation = await executor.execute_invocation("spawn_agent", func_args)
                result = invocation.result
                yield {
                    "type": "tool_result",
                    "name": "spawn_agent",
                    "result": result,
                    **invocation.event_metadata(),
                    "id": str(uuid.uuid4()),
                }

                if native_tools_enabled:
                    current_messages.append(
                        {
                            "tool_call_id": "auto_spawn_agent",
                            "role": "tool",
                            "name": "spawn_agent",
                            "content": _wrap_tool_result_untrusted("spawn_agent", result),
                        }
                    )
                else:
                    current_messages.append(
                        {
                            "role": "assistant",
                            "content": _wrap_tool_result_untrusted(
                                "spawn_agent",
                                (
                                    "Tool result available. Use it to answer the user.\n"
                                    "tool_name: spawn_agent\n"
                                    f"tool_result: {result}"
                                ),
                            ),
                        }
                    )

                yield {"type": "done", "done": True, "id": str(uuid.uuid4())}
                return

            # No tool calls, we are done
            yield {"type": "done", "done": True, "id": str(uuid.uuid4())}
            return

    final_messages = synthesis_messages(current_messages)

    synthesis_content_buffer: list[str] = []

    try:
        synthesis_params = _prepare_call_params(
            settings,
            provider_config,
            final_messages,
            actual_model,
            tools=None,
            stream=True,
        )
        if last_context_budget is not None and last_context_budget.output_tokens > 0:
            synthesis_params["max_tokens"] = last_context_budget.output_tokens
        _apply_caller_controls(synthesis_params, max_output_tokens, call_overrides)

        try:
            synthesis_stream = await routed(lambda: guarded_completion(**synthesis_params))
        except ComputeUnavailable as refusal:
            cause = (
                _reasoning_fallback_cause(refusal)
                if reasoning_fallback and fallback_profile is None
                else None
            )
            if cause is None:
                raise
            fallback_profile = "routine"
            yield _fallback_event(cause)
            synthesis_stream = await routed(lambda: guarded_completion(**synthesis_params))
        synthesis_iter = cast(AsyncIterator[Any], synthesis_stream)

        async with _closing_stream(synthesis_iter):
            async for chunk in synthesis_iter:
                choices = getattr(chunk, "choices", None) or chunk.get("choices", [])
                if not choices:
                    continue

                delta = getattr(choices[0], "delta", None) or choices[0].get("delta", {})
                if not delta:
                    continue

                reasoning = (
                    getattr(delta, "reasoning_content", None)
                    or (delta.get("reasoning_content") if isinstance(delta, dict) else None)
                    or getattr(delta, "thinking", None)
                    or (delta.get("thinking") if isinstance(delta, dict) else None)
                )
                if not reasoning:
                    reasoning_details = getattr(delta, "reasoning_details", None) or (
                        delta.get("reasoning_details") if isinstance(delta, dict) else None
                    )
                    reasoning = _reasoning_text_from_details(reasoning_details)

                if reasoning:
                    yield {
                        "type": "thinking",
                        "content": reasoning,
                        "id": str(uuid.uuid4()),
                    }

                content_chunk = getattr(delta, "content", None) or (
                    delta.get("content") if isinstance(delta, dict) else None
                )
                if content_chunk:
                    synthesis_content_buffer.append(content_chunk)
                    yield {
                        "type": "content_delta",
                        "content": content_chunk,
                        "id": str(uuid.uuid4()),
                    }

    except (ComputeUnavailable, EntitlementsError):
        raise
    except Exception as e:
        fallback_message = (
            "I completed the tool runs, but I hit a synthesis error while preparing "
            f"the final summary: {str(e)}"
        )
        yield {
            "type": "content_delta",
            "content": fallback_message,
            "id": str(uuid.uuid4()),
        }
        yield {
            "type": "content_done",
            "content": fallback_message,
            "id": str(uuid.uuid4()),
        }
        yield {"type": "done", "done": True, "id": str(uuid.uuid4())}
        return

    synthesis_content = "".join(synthesis_content_buffer).strip()
    if not synthesis_content:
        synthesis_content = (
            "I completed the tool runs and reached the tool-round limit before a "
            "final synthesis message was generated. Ask me to summarize the most "
            "recent tool outputs and I will provide a structured report immediately."
        )
        yield {
            "type": "content_delta",
            "content": synthesis_content,
            "id": str(uuid.uuid4()),
        }

    yield {
        "type": "content_done",
        "content": synthesis_content,
        "id": str(uuid.uuid4()),
    }
    yield {"type": "done", "done": True, "id": str(uuid.uuid4())}
    return
