from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
import json
import uuid
from typing import Any, cast

import litellm

from orchestrator.config import Settings


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex}"


def sse(event_type: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"event: {event_type}\ndata: {data}\n\n"


def build_openai_messages(system_prompt: str, user_message: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def _extract_delta_text(chunk: Any) -> str:
    try:
        choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
        if not choices:
            return ""
        choice0 = choices[0]
        delta = choice0.get("delta") if isinstance(choice0, dict) else getattr(choice0, "delta", None)
        if not delta:
            return ""
        if isinstance(delta, dict):
            return str(delta.get("content") or "")
        return str(getattr(delta, "content", "") or "")
    except Exception:
        return ""


def _extract_finish_reason(chunk: Any) -> str | None:
    try:
        choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
        if not choices:
            return None
        choice0 = choices[0]
        if isinstance(choice0, dict):
            return choice0.get("finish_reason")
        return getattr(choice0, "finish_reason", None)
    except Exception:
        return None


def _extract_usage(chunk: Any) -> dict[str, int] | None:
    usage = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
    if not usage:
        return None
    if isinstance(usage, dict):
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        return {"input_tokens": prompt, "output_tokens": completion, "total_tokens": total}
    return None


def _provider_from_model(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[0]
    return "unknown"


def effective_provider_and_model(settings: Settings) -> tuple[str, str]:
    if settings.mock_llm:
        return ("mock", "mock")

    if settings.llm_provider == "opencode_zen":
        return ("opencode_zen", settings.opencode_model)

    # Default
    return ("openrouter", settings.litellm_model)


async def litellm_stream(settings: Settings, messages: list[dict[str, str]]) -> AsyncIterator[Any]:
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

    if settings.llm_provider == "opencode_zen":
        if not settings.opencode_api_key:
            raise RuntimeError("OPENCODE_API_KEY is not set")

        # OpenCode Zen exposes an OpenAI-compatible `/chat/completions` endpoint.
        # We call it via LiteLLM's OpenAI adapter by prefixing the model.
        stream = await litellm.acompletion(
            model=f"openai/{settings.opencode_model}",
            messages=messages,
            stream=True,
            api_key=settings.opencode_api_key,
            api_base=settings.opencode_base_url,
            timeout=settings.request_timeout_s,
        )
    else:
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

        stream = await litellm.acompletion(
            model=settings.litellm_model,
            messages=messages,
            stream=True,
            api_key=settings.openrouter_api_key,
            api_base=settings.openrouter_base_url,
            timeout=settings.request_timeout_s,
        )

    stream_iter = cast(AsyncIterator[Any], stream)
    async for chunk in stream_iter:
        yield chunk


async def stream_sse_chat(
    *,
    settings: Settings,
    system_prompt: str,
    user_message: str,
    conversation_id: str,
    request_id: str,
    ping_interval_s: float,
    is_disconnected: Any,
) -> AsyncIterator[str]:
    provider, model = effective_provider_and_model(settings)

    evt_counter = 0
    final_text_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, int] | None = None

    def make_envelope(event_type: str, data: dict[str, Any], *, evt_id: str | None = None) -> dict[str, Any]:
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

    messages = build_openai_messages(system_prompt, user_message)
    iterator = litellm_stream(settings, messages).__aiter__()

    while True:
        if await is_disconnected():
            return

        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=ping_interval_s)
        except asyncio.TimeoutError:
            evt_counter += 1
            yield sse("ping", make_envelope("ping", {}, evt_id=f"evt_ping_{evt_counter:06d}"))
            continue
        except StopAsyncIteration:
            break

        delta = _extract_delta_text(chunk)
        if delta:
            final_text_parts.append(delta)
            yield sse(
                "token",
                make_envelope("token", {"index": 0, "delta": delta, "role": "assistant"}),
            )

        finish_reason = _extract_finish_reason(chunk) or finish_reason
        usage = _extract_usage(chunk) or usage

    final_text = "".join(final_text_parts)
    final_payload = {
        "message": {
            "id": "msg_assistant_001",
            "role": "assistant",
            "content": final_text,
            "content_type": "text/plain",
        },
        "usage": usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "model": model,
        "provider": provider,
        "finish_reason": finish_reason or "stop",
    }

    yield sse("final", make_envelope("final", final_payload, evt_id="evt_final"))
    yield sse("done", make_envelope("done", {"ok": True}, evt_id="evt_done"))
