from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
import json
import logging
import uuid
from typing import Any, cast

import litellm

from orchestrator.config import ProviderConfig, Settings, TierConfig, ModelSlotConfig
from orchestrator.guardrails import strip_reasoning_fields_from_message

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
        tier_config = settings.tier_configs.get(settings.tier)
        if tier_config:
            model = tier_config.models.get("chat")
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
                # Actual LLM streaming
                model_to_call = actual_model or f"{provider}/{model}"
                completion_kwargs: dict[str, Any] = {
                    "model": model_to_call,
                    "messages": messages,
                    "stream": True,
                    "timeout": provider_config.timeout_s,
                }
                if provider == "openrouter" and "gemini" in model_to_call.lower():
                    completion_kwargs["reasoning"] = {"enabled": True}

                response = await litellm.acompletion(**completion_kwargs)

                async for chunk in response:
                    if await is_disconnected():
                        forced_terminal_status = "cancelled"
                        terminal_reason = "Client disconnected during streaming"
                        break

                    now = asyncio.get_event_loop().time()
                    if first_token_time is None:
                        first_token_time = now
                    last_token_time = now

                    delta_text = _extract_delta_text(chunk)
                    if delta_text:
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

                    delta_reasoning = _extract_delta_reasoning(chunk)
                    if delta_reasoning:
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

                    # Extract finish reason and usage if available
                    try:
                        choices = getattr(chunk, "choices", None)
                        if choices and isinstance(choices, list) and len(choices) > 0:
                            choice = choices[0]
                            if (
                                hasattr(choice, "finish_reason")
                                and choice.finish_reason
                            ):
                                finish_reason = choice.finish_reason

                        usage_data = getattr(chunk, "usage", None)
                        if usage_data:
                            usage = {
                                "prompt_tokens": getattr(
                                    usage_data, "prompt_tokens", 0
                                ),
                                "completion_tokens": getattr(
                                    usage_data, "completion_tokens", 0
                                ),
                                "total_tokens": getattr(usage_data, "total_tokens", 0),
                            }
                    except Exception:
                        pass

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

        # Final event with complete message and metadata
        final_text = "".join(final_text_parts)
        final_data = {
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
                            "generate_conversation_title_job",
                            str(conversation_uuid),
                            _job_id=f"title:{conversation_uuid}",
                            _defer_by=timedelta(seconds=30),
                        )
                    except Exception as enqueue_error:
                        logger.warning(
                            "Failed to enqueue title generation: %s", enqueue_error
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
