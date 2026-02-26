from __future__ import annotations

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportArgumentType=false, reportMissingImports=false

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, cast

from arq.connections import ArqRedis
from arq.jobs import Job

from orchestrator.config import Settings
from orchestrator.memory.extraction import process_extraction
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.titles import ConversationMessage, generate_conversation_title

logger = logging.getLogger(__name__)


WorkerContext = dict[str, object]


def _parse_raw_messages(messages_json: object) -> list[dict[str, Any]]:
    parsed: object
    if isinstance(messages_json, str):
        try:
            parsed = cast(object, json.loads(messages_json))
        except json.JSONDecodeError:
            return []
    else:
        parsed = messages_json

    if not isinstance(parsed, list):
        return []

    raw_messages: list[dict[str, Any]] = []
    for item in cast(list[object], parsed):
        if isinstance(item, dict):
            raw_messages.append(item)
    return raw_messages


def _contains_memory_write_marker(value: object) -> bool:
    if isinstance(value, str):
        return "memory_write" in value.lower()
    if isinstance(value, dict):
        return any(_contains_memory_write_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_memory_write_marker(v) for v in value)
    return False


def _is_memory_write_artifact(message: dict[str, Any]) -> bool:
    tool_calls = message.get("tool_calls")
    if _contains_memory_write_marker(tool_calls):
        return True

    tool_results = message.get("tool_results")
    if _contains_memory_write_marker(tool_results):
        return True

    role = str(message.get("role") or "").lower()
    if role == "tool" and _contains_memory_write_marker(message.get("content")):
        return True

    return False


def _parse_messages(messages_json: object) -> list[ConversationMessage]:
    messages: list[ConversationMessage] = []
    for item in _parse_raw_messages(messages_json):
        role = item.get("role")
        content = item.get("content")
        if role is None or content is None:
            continue
        messages.append({"role": str(role), "content": str(content)})
    return messages


def _messages_to_text(messages: list[ConversationMessage]) -> str:
    return "\n".join(
        f"{message['role']}: {message['content']}" for message in messages if message
    )


async def enqueue_with_debounce(
    queue: ArqRedis,
    job_name: str,
    job_id: str,
    defer_by: timedelta | None = None,
    args: Sequence[object] = (),
    kwargs: Mapping[str, object] | None = None,
) -> Job | None:
    delay = defer_by or timedelta(seconds=30)
    return await queue.enqueue_job(
        job_name,
        *args,
        _job_id=job_id,
        _defer_by=delay,
        **dict(kwargs or {}),
    )


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


async def extract_memories(
    ctx: WorkerContext,
    user_id: str | uuid.UUID,
    conversation_id: str | uuid.UUID,
    messages_json: object,
) -> dict[str, object]:
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"status": "skipped", "reason": "store_unavailable"}

    raw_messages = _parse_raw_messages(messages_json)
    filtered_raw_messages = [
        message for message in raw_messages if not _is_memory_write_artifact(message)
    ]
    messages = _parse_messages(filtered_raw_messages)
    text = _messages_to_text(messages)
    if not text:
        return {"status": "skipped", "reason": "no_messages"}

    await process_extraction(
        store=store_obj,
        user_id=_as_uuid(user_id),
        conversation_id=_as_uuid(conversation_id),
        text=text,
    )
    return {"status": "ok", "processed_messages": len(messages)}


async def generate_title(
    ctx: WorkerContext,
    conversation_id: str | uuid.UUID,
    messages_json: object,
) -> str | None:
    messages = _parse_messages(messages_json)
    if not messages:
        return None

    store_obj = ctx.get("store")
    if isinstance(store_obj, MemoryStore):
        try:
            existing = await store_obj.get_conversation(_as_uuid(conversation_id))
            if existing and bool(existing.get("title_locked")):
                return None
        except Exception:
            logger.warning("Failed to check title lock", exc_info=True)

    settings_obj = ctx.get("settings")
    settings = settings_obj if isinstance(settings_obj, Settings) else None
    title_model = (
        settings.title_model if settings else None
    ) or "openrouter/openai/gpt-4o-mini"

    title = await generate_conversation_title(messages, model=title_model)
    if isinstance(store_obj, MemoryStore):
        try:
            _ = await store_obj.update_conversation(
                _as_uuid(conversation_id), title=title
            )
        except Exception:
            logger.warning("Failed to persist conversation title", exc_info=True)

    return title


async def generate_conversation_title_job(
    ctx: WorkerContext,
    conversation_id: str | uuid.UUID,
) -> dict[str, object]:
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"status": "skipped", "reason": "store_unavailable"}

    conv_id = _as_uuid(conversation_id)
    conversation = await store_obj.get_conversation(conv_id)
    if not conversation:
        return {"status": "not_found"}
    if bool(conversation.get("title_locked")):
        return {"status": "skipped", "reason": "title_locked"}

    messages_raw = await store_obj.get_messages(conv_id, limit=50)
    messages: list[ConversationMessage] = []
    for msg in messages_raw:
        role = msg.get("role")
        content = msg.get("content")
        if role not in {"user", "assistant"}:
            continue
        if content is None:
            continue
        content_str = str(content).strip()
        if not content_str:
            continue
        messages.append({"role": str(role), "content": content_str})

    if not messages:
        return {"status": "skipped", "reason": "no_messages"}

    settings_obj = ctx.get("settings")
    settings = settings_obj if isinstance(settings_obj, Settings) else None
    title_model = (
        settings.title_model if settings else None
    ) or "openrouter/openai/gpt-4o-mini"
    title = await generate_conversation_title(messages, model=title_model)

    try:
        _ = await store_obj.update_conversation(conv_id, title=title)
    except Exception:
        logger.warning("Failed to persist conversation title", exc_info=True)
        return {"status": "error", "reason": "persist_failed"}

    return {"status": "ok", "title": title}


async def generate_summary_job(
    ctx: WorkerContext,
    conversation_id: str,
) -> dict[str, Any]:
    """Generate and store conversation summary."""
    from orchestrator.memory.summarization import should_summarize, generate_summary

    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"status": "skipped", "reason": "store_unavailable"}

    store = store_obj
    conv_id = uuid.UUID(conversation_id)

    conversation = await store.get_conversation(conv_id)
    if not conversation:
        return {"status": "not_found"}

    last_summary_time = conversation.get("summary_updated_at")
    settings = {}

    if not await should_summarize(conv_id, last_summary_time, store, settings):
        return {"status": "skipped", "reason": "thresholds_not_met"}

    messages = await store.get_messages(conv_id, limit=100)
    previous_summary = conversation.get("summary")

    summary = await generate_summary(messages, previous_summary, settings)
    await store.update_conversation(conv_id, summary=summary)

    return {"status": "success", "summary_length": len(summary)}


async def garbage_collect(ctx: WorkerContext) -> dict[str, int]:
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"scanned": 0, "deleted": 0}

    async with store_obj._pool.acquire() as conn:
        scanned = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM memories
            WHERE (status = 'inactive' AND updated_at < NOW() - INTERVAL '90 days')
               OR (status = 'rejected' AND updated_at < NOW() - INTERVAL '30 days')
               OR (status = 'pending' AND updated_at < NOW() - INTERVAL '30 days')
               OR (status = 'deleted' AND updated_at < NOW() - INTERVAL '30 days')
            """
        )

        result = await conn.execute(
            """
            DELETE FROM memories
            WHERE (status = 'inactive' AND updated_at < NOW() - INTERVAL '90 days')
               OR (status = 'rejected' AND updated_at < NOW() - INTERVAL '30 days')
               OR (status = 'pending' AND updated_at < NOW() - INTERVAL '30 days')
               OR (status = 'deleted' AND updated_at < NOW() - INTERVAL '30 days')
            """
        )

    deleted = int(result.split()[-1]) if result else 0
    return {"scanned": int(scanned or 0), "deleted": deleted}
