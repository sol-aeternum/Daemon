from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
import json
import logging
import uuid
from typing import Any, cast

import litellm

from orchestrator.config import ProviderConfig, Settings, TierConfig, ModelSlotConfig

logger = logging.getLogger(__name__)


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
    system_prompt: str, user_message: str
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def build_openai_messages_from_history(
    system_prompt: str, history_messages: list[dict[str, Any]]
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in history_messages:
        role = msg.get("role")
        content = msg.get("content")
        if not role or content is None:
            continue
        messages.append({"role": str(role), "content": str(content)})
    return messages


def _is_retry_request(text: str) -> bool:
    lowered = text.lower()
    if "try again" in lowered or "retry" in lowered or "redo" in lowered:
        return True
    if "again" in lowered:
        return True
    if "different" in lowered or "another" in lowered:
        return True
    if "not " in lowered and ("that" in lowered or "this" in lowered):
        return True
    return False


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
        choice0 = choices[0]
        delta = (
            choice0.get("delta")
            if isinstance(choice0, dict)
            else getattr(choice0, "delta", None)
        )
        if not delta:
            return ""
        if isinstance(delta, dict):
            return str(delta.get("content") or "")
        return str(getattr(delta, "content", "") or "")
    except Exception:
        return ""


def _extract_finish_reason(chunk: Any) -> str | None:
    try:
        choices = (
            chunk.get("choices")
            if isinstance(chunk, dict)
            else getattr(chunk, "choices", None)
        )
        if not choices:
            return None
        choice0 = choices[0]
        if isinstance(choice0, dict):
            return choice0.get("finish_reason")
        return getattr(choice0, "finish_reason", None)
    except Exception:
        return None


def _extract_usage(chunk: Any) -> dict[str, int] | None:
    usage = (
        chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
    )
    if not usage:
        return None
    if isinstance(usage, dict):
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        return {
            "input_tokens": prompt,
            "output_tokens": completion,
            "total_tokens": total,
        }
    return None


def _provider_from_model(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[0]
    return "unknown"


def effective_provider_and_model(
    settings: Settings, provider_config: ProviderConfig | None = None
) -> tuple[str, str]:
    if settings.mock_llm:
        return ("mock", "mock")

    if provider_config:
        return (provider_config.name, provider_config.model)

    # Fallback to settings
    return (settings.default_provider, "unknown")


async def litellm_stream(
    settings: Settings,
    provider_config: ProviderConfig,
    messages: list[dict[str, str]],
    actual_model: str | None = None,
) -> AsyncIterator[Any]:
    if settings.mock_llm:
        # Emit chunks shaped like OpenAI/LiteLLM streaming responses.
        yield {"choices": [{"delta": {"content": "(mock) "}, "finish_reason": None}]}
        yield {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]}
        yield {"choices": [{"delta": {"content": " world"}, "finish_reason": None}]}
        yield {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 3, "total_tokens": 3},
        }
        return

    # Use actual model from request if provided, otherwise fall back to config default
    model_to_use = actual_model if actual_model else provider_config.model

    # Prepare LiteLLM call parameters
    call_params: dict[str, Any] = {
        "model": model_to_use,
        "messages": messages,
        "stream": True,
        "timeout": provider_config.timeout_s,
    }

    # Add provider-specific configuration
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

        # OpenRouter format already includes provider prefix
    stream = await litellm.acompletion(**call_params)

    stream_iter = cast(AsyncIterator[Any], stream)
    async for chunk in stream_iter:
        yield chunk


async def stream_sse_chat(
    *,
    settings: Settings,
    provider_config: ProviderConfig,
    system_prompt: str,
    user_message: str,
    conversation_id: str,
    request_id: str,
    ping_interval_s: float,
    is_disconnected: Any,
    actual_model: str | None = None,
    reported_model: str | None = None,
    routing_info: dict[str, Any] | None = None,
    history_messages: list[dict[str, Any]] | None = None,
    memory_store: Any = None,
    user_id: Any = None,
    conversation_uuid: uuid.UUID | None = None,
    queue: Any = None,
) -> AsyncIterator[str]:
    provider, model = effective_provider_and_model(settings, provider_config)
    model_for_events = reported_model or actual_model or model

    evt_counter = 0
    final_text_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, int] | None = None

    assistant_message_id: uuid.UUID | None = None
    _last_persist_s: float | None = None
    _persist_interval_s = 1.0

    forced_terminal_status: str | None = None
    terminal_reason: str | None = None

    async def _maybe_persist_streaming_message(
        *, force: bool = False, status: str | None = None
    ) -> None:
        nonlocal _last_persist_s
        if not memory_store or not assistant_message_id:
            return

        now_s = asyncio.get_running_loop().time()
        if not force:
            if _last_persist_s is None:
                return
            if (now_s - _last_persist_s) < _persist_interval_s:
                return

        _last_persist_s = now_s
        try:
            await memory_store.update_message(
                assistant_message_id,
                content="".join(final_text_parts),
                status=status or "streaming",
            )
        except Exception:
            logger.warning(
                "Failed to persist streaming assistant message", exc_info=True
            )

    async def _finalize_assistant_message() -> None:
        final_text = "".join(final_text_parts)
        tokens_in = int((usage or {}).get("input_tokens", 0))
        tokens_out = int((usage or {}).get("output_tokens", 0))

        if not (memory_store and conversation_uuid and user_id):
            return

        final_status: str
        if forced_terminal_status:
            final_status = forced_terminal_status
        else:
            final_status = "error" if (finish_reason == "error") else "complete"

        metadata: dict[str, Any] = {
            "request_id": request_id,
            "provider": provider,
            "model": model_for_events,
            "finish_reason": finish_reason or "stop",
            "usage": usage
            or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }
        if terminal_reason:
            metadata["reason"] = terminal_reason

        if assistant_message_id:
            try:
                await memory_store.update_message(
                    assistant_message_id,
                    content=final_text,
                    status=final_status,
                    metadata=metadata,
                )

                # Backfill token counters and model if available (update_message does not).
                try:
                    await memory_store._pool.execute(
                        """
                        UPDATE messages
                        SET tokens_in = $2,
                            tokens_out = $3,
                            model = COALESCE($4, model),
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        assistant_message_id,
                        tokens_in,
                        tokens_out,
                        actual_model or model,
                    )
                except Exception:
                    logger.warning(
                        "Failed to backfill assistant token counters", exc_info=True
                    )
            except Exception:
                logger.warning("Failed to finalize assistant message", exc_info=True)
        elif final_text:
            try:
                await memory_store.insert_message(
                    conversation_id=conversation_uuid,
                    user_id=user_id,
                    role="assistant",
                    content=final_text,
                    model=actual_model or model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    status=final_status,
                    metadata={
                        "request_id": request_id,
                        "provider": provider,
                        "model": model_for_events,
                        "finish_reason": finish_reason or "stop",
                        **({"reason": terminal_reason} if terminal_reason else {}),
                    },
                )
            except Exception:
                logger.warning("Failed to persist assistant message", exc_info=True)

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

    if history_messages:
        messages = build_openai_messages_from_history(system_prompt, history_messages)
    else:
        messages = build_openai_messages(system_prompt, user_message)

    if routing_info:
        yield sse(
            "routing",
            make_envelope("routing", routing_info, evt_id="evt_routing"),
        )

    # Mock mode uses the simple token stream for deterministic tests.
    try:
        try:
            if settings.mock_llm:
                if memory_store and conversation_uuid and user_id:
                    try:
                        inserted = await memory_store.insert_message(
                            conversation_id=conversation_uuid,
                            user_id=user_id,
                            role="assistant",
                            content="",
                            model=actual_model or model,
                            status="streaming",
                            metadata={
                                "request_id": request_id,
                                "provider": provider,
                                "model": model_for_events,
                            },
                        )
                        raw_id = inserted.get("id")
                        assistant_message_id = (
                            raw_id
                            if isinstance(raw_id, uuid.UUID)
                            else uuid.UUID(str(raw_id))
                        )
                        _last_persist_s = asyncio.get_running_loop().time()
                    except Exception:
                        logger.warning(
                            "Failed to create streaming assistant message",
                            exc_info=True,
                        )

                iterator = litellm_stream(
                    settings, provider_config, messages, actual_model
                ).__aiter__()

                while True:
                    if await is_disconnected():
                        forced_terminal_status = "error"
                        terminal_reason = "client_disconnected"
                        return

                    try:
                        chunk = await asyncio.wait_for(
                            iterator.__anext__(), timeout=ping_interval_s
                        )
                    except asyncio.TimeoutError:
                        evt_counter += 1
                        yield sse(
                            "ping",
                            make_envelope(
                                "ping", {}, evt_id=f"evt_ping_{evt_counter:06d}"
                            ),
                        )
                        continue
                    except StopAsyncIteration:
                        break

                    delta = _extract_delta_text(chunk)
                    if delta:
                        final_text_parts.append(delta)
                        await _maybe_persist_streaming_message()
                        yield sse(
                            "token",
                            make_envelope(
                                "token",
                                {"index": 0, "delta": delta, "role": "assistant"},
                            ),
                        )

                    finish_reason = _extract_finish_reason(chunk) or finish_reason
                    usage = _extract_usage(chunk) or usage
            else:
                from orchestrator.tools.builtin import create_default_registry
                from orchestrator.tools.completion import completion_with_tools
                from orchestrator.tools.executor import ToolExecutor

                registry = create_default_registry(
                    brave_api_key=settings.brave_api_key,
                    memory_store=memory_store,
                    user_id=user_id,
                )

                if _is_retry_request(user_message):
                    session_id = _spawn_session_by_conversation.get(conversation_id)
                    if session_id:
                        executor = ToolExecutor(registry)
                        func_args = json.dumps(
                            {
                                "agent_type": "image",
                                "task": user_message,
                                "session_id": session_id,
                            }
                        )

                        yield sse(
                            "tool_call",
                            make_envelope(
                                "tool_call",
                                {
                                    "name": "spawn_agent",
                                    "arguments": json.loads(func_args),
                                },
                            ),
                        )

                        result = await executor.execute("spawn_agent", func_args)
                        updated_session_id = _extract_session_id_from_result(result)
                        if updated_session_id:
                            _spawn_session_by_conversation[conversation_id] = (
                                updated_session_id
                            )

                        yield sse(
                            "tool_result",
                            make_envelope(
                                "tool_result",
                                {"name": "spawn_agent", "result": result},
                            ),
                        )

                        final_payload = {
                            "message": {
                                "id": "msg_assistant_001",
                                "role": "assistant",
                                "content": "",
                                "content_type": "text/plain",
                            },
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                            },
                            "model": model_for_events,
                            "provider": provider,
                            "finish_reason": "stop",
                        }

                        yield sse(
                            "final",
                            make_envelope("final", final_payload, evt_id="evt_final"),
                        )
                        yield sse(
                            "done",
                            make_envelope("done", {"ok": True}, evt_id="evt_done"),
                        )
                        return

                if memory_store and conversation_uuid and user_id:
                    try:
                        inserted = await memory_store.insert_message(
                            conversation_id=conversation_uuid,
                            user_id=user_id,
                            role="assistant",
                            content="",
                            model=actual_model or model,
                            status="streaming",
                            metadata={
                                "request_id": request_id,
                                "provider": provider,
                                "model": model_for_events,
                            },
                        )
                        raw_id = inserted.get("id")
                        assistant_message_id = (
                            raw_id
                            if isinstance(raw_id, uuid.UUID)
                            else uuid.UUID(str(raw_id))
                        )
                        _last_persist_s = asyncio.get_running_loop().time()
                    except Exception:
                        logger.warning(
                            "Failed to create streaming assistant message",
                            exc_info=True,
                        )

                async for evt in completion_with_tools(
                    settings=settings,
                    provider_config=provider_config,
                    messages=messages,
                    registry=registry,
                    actual_model=actual_model,
                ):
                    if await is_disconnected():
                        forced_terminal_status = "error"
                        terminal_reason = "client_disconnected"
                        return

                    evt_type = evt.get("type")
                    if evt_type == "tool_executing":
                        name = str(evt.get("name") or "")
                        raw_args = evt.get("arguments")
                        args_obj: Any
                        if isinstance(raw_args, str):
                            try:
                                args_obj = json.loads(raw_args)
                            except Exception:
                                args_obj = {"raw": raw_args}
                        else:
                            args_obj = raw_args or {}

                        yield sse(
                            "tool_call",
                            make_envelope(
                                "tool_call", {"name": name, "arguments": args_obj}
                            ),
                        )

                    elif evt_type == "tool_result":
                        name = str(evt.get("name") or "")
                        raw_result = evt.get("result")
                        result = str(raw_result or "")
                        if name in {"spawn_agent", "spawn_multiple"}:
                            session_id = _extract_session_id_from_result(raw_result)
                            if session_id:
                                _spawn_session_by_conversation[conversation_id] = (
                                    session_id
                                )
                        yield sse(
                            "tool_result",
                            make_envelope(
                                "tool_result", {"name": name, "result": result}
                            ),
                        )

                    elif evt_type == "thinking":
                        content = str(evt.get("content") or "")
                        if content:
                            yield sse(
                                "thinking",
                                make_envelope(
                                    "thinking",
                                    {"content": content},
                                ),
                            )

                    elif evt_type == "content":
                        content = str(evt.get("content") or "")
                        if content:
                            final_text_parts.append(content)
                            await _maybe_persist_streaming_message()
                            yield sse(
                                "token",
                                make_envelope(
                                    "token",
                                    {
                                        "index": 0,
                                        "delta": content,
                                        "role": "assistant",
                                    },
                                ),
                            )

                        finish_reason = "stop"
                        usage = usage or {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        }
                        if evt.get("done") is True:
                            break

                    elif evt_type == "error":
                        # Surface a stable SSE error event (final/done emitted below).
                        yield sse(
                            "error",
                            make_envelope(
                                "error",
                                {
                                    "code": "tool_or_model_error",
                                    "message": str(evt.get("error") or "Unknown error"),
                                    "retryable": False,
                                },
                            ),
                        )
                        finish_reason = "error"
                        usage = usage or {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        }
                        break

        except Exception:
            forced_terminal_status = forced_terminal_status or "error"
            terminal_reason = terminal_reason or "exception"
            raise

        final_text = "".join(final_text_parts)
        final_payload = {
            "message": {
                "id": "msg_assistant_001",
                "role": "assistant",
                "content": final_text,
                "content_type": "text/plain",
            },
            "usage": usage
            or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "model": model_for_events,
            "provider": provider,
            "finish_reason": finish_reason or "stop",
        }

        yield sse("final", make_envelope("final", final_payload, evt_id="evt_final"))
        yield sse("done", make_envelope("done", {"ok": True}, evt_id="evt_done"))
    finally:
        if memory_store and conversation_uuid and user_id:
            finalize_task = asyncio.create_task(_finalize_assistant_message())
            try:
                await asyncio.shield(finalize_task)
            except asyncio.CancelledError:
                raise

    if queue and conversation_uuid and user_id:
        try:
            from orchestrator.worker.jobs import enqueue_with_debounce

            messages_for_jobs: list[dict[str, str]] = []
            if history_messages:
                for msg in history_messages:
                    role = msg.get("role")
                    content = msg.get("content")
                    if role in {"user", "assistant"} and content is not None:
                        messages_for_jobs.append(
                            {"role": str(role), "content": str(content)}
                        )

            messages_for_jobs.append({"role": "user", "content": user_message})
            if final_text:
                messages_for_jobs.append({"role": "assistant", "content": final_text})

            messages_json = json.dumps(messages_for_jobs)

            await enqueue_with_debounce(
                queue,
                "extract_memories",
                f"extract:{conversation_uuid}",
                args=(str(user_id), str(conversation_uuid), messages_json),
            )

            # Only generate a title after the first full exchange.
            if final_text and finish_reason != "error" and memory_store:
                try:
                    msg_count = await memory_store._pool.fetchval(
                        "SELECT COUNT(*) FROM messages WHERE conversation_id = $1",
                        conversation_uuid,
                    )
                    if int(msg_count or 0) == 2:
                        await enqueue_with_debounce(
                            queue,
                            "generate_conversation_title_job",
                            f"title:{conversation_uuid}",
                            args=(str(conversation_uuid),),
                        )
                except Exception:
                    logger.warning("Failed to enqueue title job", exc_info=True)

            await enqueue_with_debounce(
                queue,
                "generate_summary_job",
                f"summary:{conversation_uuid}",
                args=(str(conversation_uuid),),
            )
        except Exception:
            logger.warning("Failed to enqueue memory jobs", exc_info=True)
