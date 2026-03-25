from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
import json
import logging
import uuid
from typing import Any
from zoneinfo import ZoneInfo

from orchestrator.config import ProviderConfig, Settings, TierConfig, ModelSlotConfig
from orchestrator.guardrails import strip_reasoning_fields_from_message
from orchestrator.services.fetch.url_extract import extract_urls
from orchestrator.tools.builtin import create_default_registry
from orchestrator.tools.completion import completion_with_tools

logger = logging.getLogger(__name__)

_RUNTIME_DATETIME_MARKER = "<runtime-datetime-context>"
_RUNTIME_DATETIME_ZONE = ZoneInfo("Australia/Adelaide")


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex}"


_spawn_session_by_conversation: dict[str, str] = {}


def sse(event_type: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"event: {event_type}\ndata: {data}\n\n"


def build_openai_messages(
    system_prompt: str, user_message: str | list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def build_openai_messages_from_history(
    system_prompt: str, history_messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history_messages:
        role = msg.get("role")
        content = msg.get("content")
        if not role or content is None:
            continue
        messages.append({"role": str(role), "content": content})
    return messages


def with_runtime_datetime_context(
    system_prompt: str, now_utc: datetime | None = None
) -> str:
    current_utc = now_utc or datetime.now(timezone.utc)
    current_local = current_utc.astimezone(_RUNTIME_DATETIME_ZONE)

    base_prompt = system_prompt
    if _RUNTIME_DATETIME_MARKER in base_prompt:
        base_prompt = base_prompt.split(_RUNTIME_DATETIME_MARKER, 1)[0].rstrip()

    runtime_block = (
        f"{_RUNTIME_DATETIME_MARKER}\n"
        f"- Current date: {current_local.strftime('%Y-%m-%d')}\n"
        f"- Current time: {current_local.strftime('%H:%M:%S %Z')}\n"
        f"- Current UTC datetime: {current_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        "- Use this as authoritative temporal context for this response."
    )
    return f"{base_prompt.rstrip()}\n\n{runtime_block}"


def _extract_session_id_from_result(result: Any) -> str | None:
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except Exception:
            return None
    if not isinstance(parsed, dict):
        return None
    metadata = parsed.get("metadata")
    if isinstance(metadata, dict):
        session_id = metadata.get("session_id")
        if session_id:
            return session_id
    session_id = parsed.get("session_id")
    if session_id:
        return session_id
    results = parsed.get("results")
    if isinstance(results, list) and results:
        last_result = results[-1]
        if isinstance(last_result, dict):
            last_meta = last_result.get("metadata")
            if isinstance(last_meta, dict):
                return last_meta.get("session_id")
    return None


def _extract_delta_text(chunk: Any) -> str:
    try:
        choices = (
            chunk.get("choices")
            if isinstance(chunk, dict)
            else getattr(chunk, "choices", None)
        )
        if not choices:
            return ""
        delta = choices[0].get("delta") if isinstance(choices, list) else None
        if not delta:
            return ""
        return delta.get("content", "") or ""
    except Exception:
        return ""


def _reasoning_text_from_details(reasoning_details: Any) -> str:
    if not reasoning_details:
        return ""
    if isinstance(reasoning_details, list):
        parts: list[str] = []
        for item in reasoning_details:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                    continue
                summary = item.get("summary")
                if isinstance(summary, str) and summary:
                    parts.append(summary)
                    continue
                if isinstance(summary, list):
                    for s in summary:
                        if isinstance(s, str) and s:
                            parts.append(s)
                        elif isinstance(s, dict):
                            s_text = s.get("text")
                            if isinstance(s_text, str) and s_text:
                                parts.append(s_text)
            else:
                item_text = getattr(item, "text", None)
                if isinstance(item_text, str) and item_text:
                    parts.append(item_text)
                    continue
                item_summary = getattr(item, "summary", None)
                if isinstance(item_summary, str) and item_summary:
                    parts.append(item_summary)
        return "\n".join(parts).strip()
    if isinstance(reasoning_details, dict):
        text = reasoning_details.get("text")
        if isinstance(text, str) and text:
            return text
        summary = reasoning_details.get("summary")
        if isinstance(summary, str) and summary:
            return summary
    text_attr = getattr(reasoning_details, "text", None)
    if isinstance(text_attr, str) and text_attr:
        return text_attr
    summary_attr = getattr(reasoning_details, "summary", None)
    if isinstance(summary_attr, str) and summary_attr:
        return summary_attr
    return ""


def _extract_delta_reasoning(chunk: Any) -> str:
    try:
        choices = (
            chunk.get("choices")
            if isinstance(chunk, dict)
            else getattr(chunk, "choices", None)
        )
        if not choices:
            return ""

        choice0 = choices[0]
        if isinstance(choice0, dict):
            delta = choice0.get("delta")
        else:
            delta = getattr(choice0, "delta", None)
        if not delta:
            return ""

        if isinstance(delta, dict):
            direct = (
                delta.get("reasoning_content")
                or delta.get("reasoning")
                or delta.get("thinking")
            )
            details = delta.get("reasoning_details")
        else:
            direct = (
                getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
                or getattr(delta, "thinking", None)
            )
            details = getattr(delta, "reasoning_details", None)

        if isinstance(direct, str) and direct:
            return direct

        return _reasoning_text_from_details(details)
    except Exception:
        return ""


def effective_provider_and_model(
    settings: Settings, provider_config: ProviderConfig
) -> tuple[str, str]:
    provider = provider_config.name or settings.default_provider
    model = provider_config.model
    if not model:
        tier_config = settings.get_tier_config(settings.default_tier)
        model = tier_config.orchestrator.model
    if not model:
        model = "gpt-4o-mini"
    return provider, model


async def stream_sse_chat(
    settings: Settings,
    provider_config: ProviderConfig,
    system_prompt: str,
    user_message: str,
    request_id: str,
    conversation_id: str,
    is_disconnected: Any,
    ping_interval_s: float = 15.0,
    actual_model: str | None = None,
    reported_model: str | None = None,
    routing_info: dict[str, Any] | None = None,
    history_messages: list[dict[str, Any]] | None = None,
    memory_store: Any = None,
    user_id: Any = None,
    conversation_uuid: uuid.UUID | None = None,
    queue: Any = None,
    db_pool: Any = None,
    trusted_spawn_context: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    provider, model = effective_provider_and_model(settings, provider_config)
    model_for_events = reported_model or actual_model or model

    evt_counter = 0
    final_text_parts: list[str] = []
    persisted_tool_calls: list[dict[str, Any]] = []
    persisted_tool_results: list[dict[str, Any]] = []
    finish_reason: str | None = None
    usage: dict[str, int] | None = None

    assistant_message_id: uuid.UUID | None = None
    _last_persist_s: float | None = None
    _persist_interval_s = 1.0

    forced_terminal_status: str | None = None
    terminal_reason: str | None = None

    def make_envelope(
        event_type: str, data: dict[str, Any], *, evt_id: str | None = None
    ) -> dict[str, Any]:
        nonlocal evt_counter
        if evt_id is None:
            evt_counter += 1
            evt_id = f"evt_{evt_counter:06d}"
        return {
            "type": event_type,
            "id": evt_id,
            "ts": now_rfc3339(),
            "conversation_id": conversation_id,
            "request_id": request_id,
            "data": data,
        }

    effective_system_prompt = with_runtime_datetime_context(system_prompt)

    if history_messages:
        messages = build_openai_messages_from_history(
            effective_system_prompt, history_messages
        )
    else:
        messages = build_openai_messages(effective_system_prompt, user_message)

    extracted_urls = extract_urls(user_message)
    if extracted_urls:
        yield sse(
            "metadata",
            make_envelope(
                "metadata",
                {"urls": extracted_urls},
                evt_id="evt_metadata",
            ),
        )

    if conversation_uuid:
        yield sse(
            "conversation",
            make_envelope(
                "conversation",
                {"conversation_id": str(conversation_uuid)},
                evt_id="evt_conversation",
            ),
        )

    if routing_info:
        yield sse(
            "routing",
            make_envelope("routing", routing_info, evt_id="evt_routing"),
        )

    # Mock mode uses the simple token stream for deterministic tests.
    try:
        try:
            first_token_time: float | None = None
            last_token_time: float | None = None
            first_reasoning_time: float | None = None
            last_reasoning_time: float | None = None
            reasoning_parts: list[str] = []

            if settings.mock_llm:
                if memory_store and conversation_uuid and user_id:
                    try:
                        inserted = await memory_store.insert_message(
                            conversation_id=conversation_uuid,
                            user_id=user_id,
                            role="assistant",
                            content="",
                            model=actual_model or model,
                        )
                        assistant_message_id = inserted["id"]
                    except Exception as e:
                        logger.warning("Failed to insert mock message: %s", e)

                for token in "Mock response tokens from Daemon":
                    if await is_disconnected():
                        break
                    yield sse(
                        "token",
                        make_envelope(
                            "token",
                            {"text": token},
                            evt_id=f"evt_token_{uuid.uuid4().hex}",
                        ),
                    )
                    await asyncio.sleep(0.05)

                finish_reason = "stop"
                usage = {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                }
            else:
                model_to_call = actual_model or f"{provider}/{model}"
                registry = create_default_registry(
                    brave_api_key=settings.brave_api_key,
                    memory_store=memory_store,
                    user_id=user_id,
                    db_pool=db_pool,
                    trusted_spawn_context=trusted_spawn_context,
                )
                pending_tool_calls: list[str] = []

                async for event in completion_with_tools(
                    settings=settings,
                    provider_config=provider_config,
                    messages=messages,
                    registry=registry,
                    actual_model=model_to_call,
                    max_tool_rounds=4,
                ):
                    if await is_disconnected():
                        forced_terminal_status = "cancelled"
                        terminal_reason = "Client disconnected during streaming"
                        break

                    now = asyncio.get_event_loop().time()
                    event_type = str(event.get("type") or "")

                    if event_type == "content_delta":
                        delta_text = event.get("content")
                        if not isinstance(delta_text, str) or not delta_text:
                            continue

                        if first_token_time is None:
                            first_token_time = now
                        last_token_time = now
                        final_text_parts.append(delta_text)
                        yield sse(
                            "token",
                            make_envelope(
                                "token",
                                {"text": delta_text},
                                evt_id=f"evt_token_{uuid.uuid4().hex}",
                            ),
                        )

                        # Periodic persistence of incremental content
                        if (
                            memory_store
                            and conversation_uuid
                            and user_id
                            and assistant_message_id
                        ):
                            current_time = now
                            if (
                                _last_persist_s is None
                                or (current_time - _last_persist_s)
                                >= _persist_interval_s
                            ):
                                try:
                                    await memory_store.update_message_content(
                                        message_id=assistant_message_id,
                                        content_delta=delta_text,
                                    )
                                    _last_persist_s = current_time
                                except Exception as e:
                                    logger.warning(
                                        "Failed to persist incremental content: %s", e
                                    )

                    elif event_type == "thinking":
                        delta_reasoning = event.get("content")
                        if not isinstance(delta_reasoning, str) or not delta_reasoning:
                            continue

                        if (
                            not reasoning_parts
                            or reasoning_parts[-1] != delta_reasoning
                        ):
                            reasoning_parts.append(delta_reasoning)

                        if first_reasoning_time is None:
                            first_reasoning_time = now
                        last_reasoning_time = now

                        yield sse(
                            "thinking",
                            make_envelope(
                                "thinking",
                                {"content": delta_reasoning},
                                evt_id=f"evt_thinking_{uuid.uuid4().hex}",
                            ),
                        )
                    elif event_type == "tool_executing":
                        tool_name = str(event.get("name") or "tool")
                        pending_tool_calls.append(tool_name)
                        raw_arguments = event.get("arguments")
                        tool_arguments: Any = raw_arguments
                        if isinstance(raw_arguments, str):
                            try:
                                tool_arguments = json.loads(raw_arguments)
                            except Exception:
                                tool_arguments = {"raw": raw_arguments}

                        normalized_arguments = (
                            tool_arguments
                            if isinstance(tool_arguments, dict)
                            else {"value": tool_arguments}
                        )
                        persisted_tool_calls.append(
                            {
                                "name": tool_name,
                                "arguments": normalized_arguments,
                            }
                        )

                        yield sse(
                            "tool_call",
                            make_envelope(
                                "tool_call",
                                {
                                    "name": tool_name,
                                    "arguments": tool_arguments,
                                },
                                evt_id=f"evt_tool_call_{uuid.uuid4().hex}",
                            ),
                        )
                    elif event_type == "tool_result":
                        tool_name = str(event.get("name") or "tool")
                        if tool_name in pending_tool_calls:
                            pending_tool_calls.remove(tool_name)
                        raw_result = event.get("result")
                        tool_result: Any = raw_result
                        if isinstance(raw_result, str):
                            try:
                                tool_result = json.loads(raw_result)
                            except Exception:
                                tool_result = raw_result

                        persisted_tool_results.append(
                            {
                                "name": tool_name,
                                "result": tool_result,
                            }
                        )

                        yield sse(
                            "tool_result",
                            make_envelope(
                                "tool_result",
                                {
                                    "name": tool_name,
                                    "result": tool_result,
                                },
                                evt_id=f"evt_tool_result_{uuid.uuid4().hex}",
                            ),
                        )
                    elif event_type == "error":
                        error_message = str(
                            event.get("error") or "Tool execution failed"
                        )
                        logger.warning(
                            "Tool pipeline reported recoverable error: %s",
                            error_message,
                        )

                        unresolved_tools = list(pending_tool_calls)
                        pending_tool_calls.clear()
                        if unresolved_tools:
                            for unresolved_name in unresolved_tools:
                                yield sse(
                                    "tool_result",
                                    make_envelope(
                                        "tool_result",
                                        {
                                            "name": unresolved_name,
                                            "result": {
                                                "success": False,
                                                "error": error_message,
                                                "metadata": {
                                                    "recoverable": True,
                                                    "synthetic": True,
                                                    "reason": "pipeline_error",
                                                },
                                            },
                                        },
                                        evt_id=f"evt_tool_result_{uuid.uuid4().hex}",
                                    ),
                                )
                                persisted_tool_results.append(
                                    {
                                        "name": unresolved_name,
                                        "result": {
                                            "success": False,
                                            "error": error_message,
                                            "metadata": {
                                                "recoverable": True,
                                                "synthetic": True,
                                                "reason": "pipeline_error",
                                            },
                                        },
                                    }
                                )
                        else:
                            yield sse(
                                "tool_result",
                                make_envelope(
                                    "tool_result",
                                    {
                                        "name": "tool_pipeline",
                                        "result": {
                                            "success": False,
                                            "error": error_message,
                                            "metadata": {
                                                "recoverable": True,
                                                "synthetic": True,
                                                "reason": "pipeline_error",
                                            },
                                        },
                                    },
                                    evt_id=f"evt_tool_result_{uuid.uuid4().hex}",
                                ),
                            )
                            persisted_tool_results.append(
                                {
                                    "name": "tool_pipeline",
                                    "result": {
                                        "success": False,
                                        "error": error_message,
                                        "metadata": {
                                            "recoverable": True,
                                            "synthetic": True,
                                            "reason": "pipeline_error",
                                        },
                                    },
                                }
                            )

                        graceful_notice = (
                            "I hit a tool error and will continue with the best available "
                            f"information. ({error_message})"
                        )

                        if first_token_time is None:
                            first_token_time = now
                        last_token_time = now
                        final_text_parts.append(graceful_notice)

                        yield sse(
                            "token",
                            make_envelope(
                                "token",
                                {"text": graceful_notice},
                                evt_id=f"evt_token_{uuid.uuid4().hex}",
                            ),
                        )
                        break
                    elif event_type == "done":
                        if pending_tool_calls:
                            unresolved_tools = list(pending_tool_calls)
                            pending_tool_calls.clear()

                            done_notice = (
                                "Some tool calls did not finish before the turn ended. "
                                "I will continue with the best available information."
                            )
                            if first_token_time is None:
                                first_token_time = now
                            last_token_time = now
                            final_text_parts.append(done_notice)

                            yield sse(
                                "token",
                                make_envelope(
                                    "token",
                                    {"text": done_notice},
                                    evt_id=f"evt_token_{uuid.uuid4().hex}",
                                ),
                            )

                            for unresolved_name in unresolved_tools:
                                yield sse(
                                    "tool_result",
                                    make_envelope(
                                        "tool_result",
                                        {
                                            "name": unresolved_name,
                                            "result": {
                                                "success": False,
                                                "error": "Tool call did not complete before stream finished.",
                                                "metadata": {
                                                    "recoverable": True,
                                                    "synthetic": True,
                                                    "reason": "stream_done_with_pending",
                                                },
                                            },
                                        },
                                        evt_id=f"evt_tool_result_{uuid.uuid4().hex}",
                                    ),
                                )
                                persisted_tool_results.append(
                                    {
                                        "name": unresolved_name,
                                        "result": {
                                            "success": False,
                                            "error": "Tool call did not complete before stream finished.",
                                            "metadata": {
                                                "recoverable": True,
                                                "synthetic": True,
                                                "reason": "stream_done_with_pending",
                                            },
                                        },
                                    }
                                )
                        break

        except asyncio.CancelledError:
            forced_terminal_status = "cancelled"
            terminal_reason = "Request was cancelled"
            raise
        except Exception as e:
            forced_terminal_status = "error"
            terminal_reason = str(e)
            logger.error("Streaming error: %s", e, exc_info=True)
            yield sse(
                "error",
                make_envelope("error", {"message": str(e)}, evt_id="evt_error"),
            )
            return

        # Ensure assistant always provides visible response even when tool failures occur
        # without explicit error events (e.g., tool_result with failure status then done)
        if not final_text_parts:
            fallback_message = (
                "I encountered issues while executing tools and couldn't complete the request as intended. "
                "Please try rephrasing your request, or ask me to explain what went wrong."
            )
            final_text_parts.append(fallback_message)
            yield sse(
                "token",
                make_envelope(
                    "token",
                    {"text": fallback_message},
                    evt_id=f"evt_token_{uuid.uuid4().hex}",
                ),
            )

        # Final event with complete message and metadata
        final_text = "".join(final_text_parts)
        final_data: dict[str, Any] = {
            "text": final_text,
            "model": model_for_events,
            "finish_reason": finish_reason or "unknown",
        }
        if usage:
            final_data["usage"] = usage
        if first_token_time is not None and last_token_time is not None:
            final_data["timing"] = {
                "first_token_s": first_token_time,
                "last_token_s": last_token_time,
            }

        yield sse(
            "final",
            make_envelope("final", final_data, evt_id="evt_final"),
        )

        # Persist final message to memory store
        if memory_store and conversation_uuid and user_id:
            try:
                content = final_text
                model_name = actual_model or model
                reasoning_text = "\n".join(reasoning_parts).strip() or None
                reasoning_duration_secs: int | None = None
                if (
                    first_reasoning_time is not None
                    and last_reasoning_time is not None
                    and last_reasoning_time >= first_reasoning_time
                ):
                    reasoning_duration_secs = max(
                        1, int(last_reasoning_time - first_reasoning_time)
                    )
                final_metadata: dict[str, Any] = {}
                if finish_reason is not None:
                    final_metadata["finish_reason"] = finish_reason
                if usage is not None:
                    final_metadata["usage"] = usage

                if assistant_message_id:
                    # Update existing message
                    await memory_store.update_message(
                        message_id=assistant_message_id,
                        content=content,
                        tool_calls=persisted_tool_calls,
                        tool_results=persisted_tool_results,
                        reasoning_text=reasoning_text,
                        reasoning_duration_secs=reasoning_duration_secs,
                        reasoning_model=model_name,
                        status="complete",
                        metadata=final_metadata or None,
                    )
                else:
                    # Insert new message
                    inserted = await memory_store.insert_message(
                        conversation_id=conversation_uuid,
                        user_id=user_id,
                        role="assistant",
                        content=content,
                        model=model_name,
                        tool_calls=persisted_tool_calls,
                        tool_results=persisted_tool_results,
                        reasoning_text=reasoning_text,
                        reasoning_duration_secs=reasoning_duration_secs,
                        reasoning_model=model_name,
                        status="complete",
                        metadata=final_metadata or None,
                    )
                    assistant_message_id = inserted["id"]

                if queue is not None:
                    try:
                        await queue.enqueue_job(
                            "extract_memories",
                            str(user_id),
                            str(conversation_uuid),
                            _job_id=f"extract:{conversation_uuid}",
                            _defer_by=timedelta(seconds=30),
                        )
                    except Exception as extract_error:
                        logger.warning(
                            "Failed to enqueue memory extraction: %s", extract_error
                        )
            except Exception as e:
                logger.warning("Failed to persist final message: %s", e)

        # Terminal status event
        terminal_status = forced_terminal_status or "completed"
        terminal_data = {"status": terminal_status}
        if terminal_reason:
            terminal_data["reason"] = terminal_reason

        yield sse(
            "done",
            make_envelope("done", terminal_data, evt_id="evt_done"),
        )

    except Exception as e:
        logger.error("Unexpected error in stream_sse_chat: %s", e, exc_info=True)
        yield sse(
            "error",
            make_envelope(
                "error", {"message": "Internal server error"}, evt_id="evt_error"
            ),
        )
