from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from typing import cast

from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)

MAX_RETURNED_MEMORIES = 5
INITIAL_VECTOR_CANDIDATES = 10
MIN_FINAL_SCORE = 0.15

# Empirical hybrid-search calibration:
# - keep vector similarity as the primary signal so the existing vector-only path
#   remains stable when BM25 returns nothing;
# - give BM25 enough influence to lift strong lexical matches and cold-start
#   memories into the candidate set;
# - reserve a smaller recency/confidence term for tie-breaking so very old or
#   low-confidence memories do not outrank fresher, comparable matches.
#
# A small scenario sweep around the baseline weights (vector-only fallback,
# BM25-only cold start, and stale-vs-fresh ties) showed these defaults stayed
# robust without over-rewarding max-normalized BM25 scores.
HYBRID_VECTOR_WEIGHT = 0.5
HYBRID_BM25_WEIGHT = 0.3
HYBRID_RECENCY_CONFIDENCE_WEIGHT = 0.2


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


def _recency_score(days: float) -> float:
    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.9
    if days <= 90:
        return 0.7
    return 0.5


def _access_boost(memory: dict[str, object]) -> float:
    access_count_raw = memory.get("access_count")
    access_count = int(access_count_raw) if isinstance(access_count_raw, int) else 0
    if access_count <= 0:
        return 1.0
    if access_count <= 5:
        return 1.05
    if access_count <= 20:
        return 1.1
    return 1.15


def score_memory(memory: dict[str, object]) -> float:
    similarity = _as_float(memory.get("similarity"), 0.0)
    recency_days = _days_since_accessed(memory)
    recency = _recency_score(recency_days)
    source = _source_boost(memory)
    confidence = _as_float(memory.get("confidence"), 1.0)
    trust = _as_float(memory.get("trust_score"), 0.5)  # Default trust is 0.5
    access = _access_boost(memory)
    return similarity * recency * source * confidence * trust * access


_score_memory = score_memory


def _hybrid_score(
    vector_sim: float,
    bm25_normalized: float,
    recency: float,
    confidence: float,
    trust: float = 0.5,
) -> float:
    recency_confidence_trust = recency * confidence * trust
    return (
        HYBRID_VECTOR_WEIGHT * vector_sim
        + HYBRID_BM25_WEIGHT * bm25_normalized
        + HYBRID_RECENCY_CONFIDENCE_WEIGHT * recency_confidence_trust
    )


def _normalize_bm25_scores(candidates: list[dict[str, object]]) -> None:
    if not candidates:
        return
    max_bm25 = max(_as_float(c.get("bm25_score"), 0.0) for c in candidates)
    if max_bm25 > 0:
        for c in candidates:
            c["bm25_normalized"] = _as_float(c.get("bm25_score"), 0.0) / max_bm25
    else:
        for c in candidates:
            c["bm25_normalized"] = 0.0


async def retrieve_memories(
    store: MemoryStore,
    query_embedding: list[float],
    query_text: str | None = None,
    conversation_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    if not query_embedding:
        return []

    include_local = False

    if conversation_id is not None:
        conversation = await store.get_conversation(conversation_id)
        if not conversation:
            logger.debug(
                "Conversation %s not found for memory retrieval", conversation_id
            )
            return []

        user_id_from_conv = conversation.get("user_id")
        if not isinstance(user_id_from_conv, uuid.UUID):
            logger.warning("Conversation %s has invalid user_id", conversation_id)
            return []

        user_id = user_id_from_conv
        pipeline = str(conversation.get("pipeline") or "").strip().lower()
        include_local = pipeline == "local"

    if user_id is None:
        logger.debug("No user_id provided for memory retrieval")
        return []

    target_limit = max(1, min(limit, MAX_RETURNED_MEMORIES))
    vector_limit = max(INITIAL_VECTOR_CANDIDATES, target_limit)

    vector_candidates = cast(
        list[dict[str, object]],
        await store.search_memories(
            user_id=user_id,
            query_embedding=query_embedding,
            limit=vector_limit,
            include_local=include_local,
        ),
    )

    bm25_candidates: list[dict[str, object]] = []
    if query_text:
        bm25_candidates = cast(
            list[dict[str, object]],
            await store.search_memories_bm25(
                user_id=user_id,
                query=query_text,
                limit=vector_limit,
                include_local=include_local,
            ),
        )

    candidate_map: dict[str, dict[str, object]] = {}

    for c in vector_candidates:
        memory_id = str(c.get("id", ""))
        if memory_id:
            entry = dict(c)
            entry["vector_sim"] = _as_float(entry.get("similarity"), 0.0)
            entry["bm25_score"] = 0.0
            entry["bm25_normalized"] = 0.0
            entry["source"] = "vector"
            candidate_map[memory_id] = entry

    for c in bm25_candidates:
        memory_id = str(c.get("id", ""))
        if memory_id:
            if memory_id in candidate_map:
                candidate_map[memory_id]["bm25_score"] = _as_float(
                    c.get("bm25_score"), 0.0
                )
                candidate_map[memory_id]["source"] = "hybrid"
            else:
                entry = dict(c)
                entry["vector_sim"] = 0.0
                entry["similarity"] = 0.0
                entry["source"] = "bm25"
                candidate_map[memory_id] = entry

    all_candidates = list(candidate_map.values())

    if not all_candidates:
        return []

    _normalize_bm25_scores(all_candidates)

    scored: list[dict[str, object]] = []
    for entry in all_candidates:
        vector_sim = _as_float(entry.get("vector_sim"), 0.0)
        bm25_normalized = _as_float(entry.get("bm25_normalized"), 0.0)
        recency_days = _days_since_accessed(entry)
        recency_boost = _recency_score(recency_days)
        source_boost = _source_boost(entry)
        confidence = _as_float(entry.get("confidence"), 1.0)
        access_boost = _access_boost(entry)

        trust = _as_float(entry.get("trust_score"), 0.5)
        final_score = _hybrid_score(
            vector_sim,
            bm25_normalized,
            recency_boost * source_boost * access_boost,
            confidence,
            trust,
        )

        entry["recency_boost"] = recency_boost
        entry["source_boost"] = source_boost
        entry["access_boost"] = access_boost
        entry["final_score"] = final_score
        scored.append(entry)

    filtered = [
        item
        for item in scored
        if _as_float(item.get("final_score"), 0.0) >= MIN_FINAL_SCORE
    ]

    ranked = sorted(
        filtered,
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
