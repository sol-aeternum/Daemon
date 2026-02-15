from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import uuid

from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)

MAX_RETURNED_MEMORIES = 5
INITIAL_VECTOR_CANDIDATES = 10


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default


def _days_since_accessed(memory: dict[str, object]) -> float:
    now = dt.datetime.now(dt.timezone.utc)
    accessed_at = (
        memory.get("last_accessed_at")
        or memory.get("updated_at")
        or memory.get("created_at")
    )
    if not isinstance(accessed_at, dt.datetime):
        return 1.0

    if accessed_at.tzinfo is None:
        accessed_at = accessed_at.replace(tzinfo=dt.timezone.utc)

    delta = now - accessed_at
    return max(delta.total_seconds() / 86400.0, 1.0)


def _source_boost(memory: dict[str, object]) -> float:
    source_type = str(memory.get("source_type") or "").lower()
    category = str(memory.get("category") or "").lower()

    if source_type in {"project", "important"}:
        return 1.1
    if category in {"project", "important"}:
        return 1.1
    return 1.0


def _score_memory(memory: dict[str, object]) -> float:
    similarity = _as_float(memory.get("similarity"), 0.0)
    recency_days = _days_since_accessed(memory)
    recency = 1.0 / math.sqrt(recency_days)
    source = _source_boost(memory)
    confidence = _as_float(memory.get("confidence"), 1.0)
    return similarity * recency * source * confidence


async def retrieve_memories(
    store: MemoryStore,
    query_embedding: list[float],
    conversation_id: uuid.UUID | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    if not query_embedding:
        return []

    if conversation_id is None:
        logger.debug("No conversation_id provided for memory retrieval")
        return []

    conversation = await store.get_conversation(conversation_id)
    if not conversation:
        logger.debug("Conversation %s not found for memory retrieval", conversation_id)
        return []

    user_id = conversation.get("user_id")
    if not isinstance(user_id, uuid.UUID):
        logger.warning("Conversation %s has invalid user_id", conversation_id)
        return []

    pipeline = str(conversation.get("pipeline") or "").strip().lower()
    include_local = pipeline == "local"

    target_limit = max(1, min(limit, MAX_RETURNED_MEMORIES))
    vector_limit = max(INITIAL_VECTOR_CANDIDATES, target_limit)

    candidates = await store.search_memories(
        user_id=user_id,
        query_embedding=query_embedding,
        limit=vector_limit,
        include_local=include_local,
    )

    if not candidates:
        return []

    scored: list[dict[str, object]] = []
    for memory in candidates:
        entry = dict(memory)
        similarity = _as_float(entry.get("similarity"), 0.0)
        recency_days = _days_since_accessed(entry)
        recency_boost = 1.0 / math.sqrt(recency_days)
        source_boost = _source_boost(entry)
        confidence = _as_float(entry.get("confidence"), 1.0)
        final_score = _score_memory(entry)

        entry["similarity"] = similarity
        entry["recency_boost"] = recency_boost
        entry["source_boost"] = source_boost
        entry["confidence"] = confidence
        entry["final_score"] = final_score
        scored.append(entry)

    ranked = sorted(
        scored,
        key=lambda item: _as_float(item.get("final_score"), 0.0),
        reverse=True,
    )[:target_limit]

    memory_ids: list[uuid.UUID] = []
    for memory in ranked:
        memory_id = memory.get("id")
        if isinstance(memory_id, uuid.UUID):
            memory_ids.append(memory_id)
    if memory_ids:

        async def _touch() -> None:
            try:
                await store.bulk_touch_memories(memory_ids)
            except Exception:
                logger.exception("Failed to update memory access timestamps")

        _ = asyncio.create_task(_touch())

    return ranked
