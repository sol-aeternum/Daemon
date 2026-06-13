from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import cast

from orchestrator.config import get_settings
from orchestrator.memory.embedding import (
    EmbeddingVectorResult,
    embed_query_for_configured_storage_models,
)
from orchestrator.memory.entities import (
    _normalize_lookup_key,
    extract_candidates_baseline,
)
from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)

MAX_RETURNED_MEMORIES = 5
INITIAL_VECTOR_CANDIDATES = 10
MIN_FINAL_SCORE = 0.15
MAX_LOGGED_CONTENT_CHARS = 120


@dataclass(frozen=True)
class TemporalQueryWindow:
    start: dt.datetime
    end: dt.datetime
    detector: str


_MONTH_BY_NAME = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _coerce_reference_datetime(value: str | dt.datetime | None) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        reference = value
    elif isinstance(value, str) and value.strip():
        match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", value)
        if not match:
            return None
        reference = dt.datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    else:
        return None

    if reference.tzinfo is None:
        return reference.replace(tzinfo=dt.timezone.utc)
    return reference.astimezone(dt.timezone.utc)


def _month_end(year: int, month: int) -> dt.datetime:
    if month == 12:
        return dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    return dt.datetime(year, month + 1, 1, tzinfo=dt.timezone.utc)


def _detect_temporal_query_window(
    query_text: str | None,
    *,
    query_reference_time: str | dt.datetime | None = None,
) -> TemporalQueryWindow | None:
    normalized = _normalize_query_text(query_text).lower()
    if not normalized:
        return None

    reference = _coerce_reference_datetime(query_reference_time)
    if reference is None:
        return None

    for month_name, month in _MONTH_BY_NAME.items():
        if re.search(rf"\b{month_name}\b", normalized):
            explicit_year = re.search(r"\b(19\d{2}|20\d{2})\b", normalized)
            if explicit_year:
                year = int(explicit_year.group(1))
                detector = "month_and_year"
            else:
                year = reference.year
                if month > reference.month:
                    year -= 1
                detector = "month_only"
            start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
            return TemporalQueryWindow(
                start=start,
                end=_month_end(year, month),
                detector=detector,
            )

    relative_match = re.search(r"\b(\d+)\s+years?\s+ago\b", normalized)
    if relative_match:
        years = int(relative_match.group(1))
        try:
            center = reference.replace(year=reference.year - years)
        except ValueError:
            # Feb 29 reference with a non-leap target year.
            center = reference.replace(month=2, day=28, year=reference.year - years)
        return TemporalQueryWindow(
            start=center - dt.timedelta(days=183),
            end=center + dt.timedelta(days=183),
            detector="relative_ago",
        )

    return None


def _overlaps_temporal_window(
    memory: dict[str, object],
    window: TemporalQueryWindow,
) -> bool:
    valid_from = memory.get("valid_from") or memory.get("created_at")
    valid_to = memory.get("valid_to")
    if not isinstance(valid_from, dt.datetime):
        return False
    if valid_from.tzinfo is None:
        valid_from = valid_from.replace(tzinfo=dt.timezone.utc)
    else:
        valid_from = valid_from.astimezone(dt.timezone.utc)

    if isinstance(valid_to, dt.datetime):
        if valid_to.tzinfo is None:
            valid_to = valid_to.replace(tzinfo=dt.timezone.utc)
        else:
            valid_to = valid_to.astimezone(dt.timezone.utc)
    else:
        valid_to = dt.datetime.max.replace(tzinfo=dt.timezone.utc)

    return valid_from < window.end and valid_to >= window.start


def _is_retrieval_logging_enabled(explicit_flag: bool) -> bool:
    if explicit_flag:
        return True
    try:
        settings = get_settings()
        return settings.retrieval_logging_enabled or settings.retrieval_logging_debug
    except Exception:
        return False


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


def _normalize_query_text(query_text: str | None) -> str:
    return " ".join(str(query_text or "").split())


def _embedding_metadata_value(query_embedding: list[float], name: str) -> str | None:
    value = getattr(query_embedding, name, None)
    return value if isinstance(value, str) and value.strip() else None


def _normalize_memory_slot(memory_slot: str | None) -> str | None:
    if not isinstance(memory_slot, str):
        return None
    normalized = memory_slot.strip()
    return normalized or None


def _truncate_for_log(value: object, limit: int = MAX_LOGGED_CONTENT_CHARS) -> str:
    text = _normalize_query_text(str(value or ""))
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _days_since_accessed(memory: dict[str, object]) -> float:
    now = dt.datetime.now(dt.timezone.utc)
    accessed_at = (
        memory.get("last_accessed_at") or memory.get("updated_at") or memory.get("created_at")
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


def _prepend_l0_memories(
    l0_memories: list[dict[str, object]],
    retrieved_memories: list[dict[str, object]],
) -> list[dict[str, object]]:
    seen_ids: set[str] = set()
    combined: list[dict[str, object]] = []

    def add_memory(memory: dict[str, object], *, source: str) -> None:
        memory_id = memory.get("id")
        if memory_id is None:
            return

        memory_key = str(memory_id)
        if not memory_key or memory_key in seen_ids:
            return

        entry = dict(memory)
        entry.setdefault("source", source)
        if source == "l0":
            entry["final_score"] = float("inf")
        combined.append(entry)
        seen_ids.add(memory_key)

    for memory in l0_memories:
        add_memory(memory, source="l0")

    for memory in retrieved_memories:
        add_memory(memory, source=str(memory.get("source") or "hybrid"))

    return combined


def _log_retrieval_results(
    *,
    query_text: str | None,
    ranked: list[dict[str, object]],
    include_local: bool,
    include_historical: bool,
    memory_slot: str | None,
) -> None:
    logger.info(
        "Memory retrieval query=%r returned=%d include_local=%s include_historical=%s slot=%s",
        _truncate_for_log(query_text),
        len(ranked),
        include_local,
        include_historical,
        memory_slot,
    )

    for index, memory in enumerate(ranked, start=1):
        logger.info(
            "Memory retrieval #%d id=%s source=%s category=%s final=%.4f vector=%.4f bm25=%.4f content=%s",
            index,
            memory.get("id"),
            memory.get("source"),
            memory.get("category"),
            _as_float(memory.get("final_score"), 0.0),
            _as_float(memory.get("vector_sim"), _as_float(memory.get("similarity"), 0.0)),
            _as_float(memory.get("bm25_normalized"), 0.0),
            _truncate_for_log(memory.get("content")),
        )


async def retrieve_memories_for_text(
    store: MemoryStore,
    query_text: str,
    *,
    user_id: uuid.UUID,
    query_embedding: list[float] | None = None,
    limit: int = 5,
    include_local: bool = False,
    include_historical: bool = False,
    query_reference_time: str | dt.datetime | None = None,
    memory_slot: str | None = None,
    include_l0: bool = False,
    log_retrieval: bool = False,
    retrieval_triggered_by: str | None = None,
    allowed_source_conversation_ids: list[uuid.UUID] | None = None,
    include_dream_observations: bool = False,
    storage_embedding_model: str | None = None,
    query_embedding_model: str | None = None,
) -> list[dict[str, object]]:
    """Canonical text-query retrieval contract.

    This is the shared path for prompt injection, memory_read/memory_reflect
    style queries, and LongMemEval benchmark runs. Callers that already have an
    embedding may pass it to preserve their existing timeout/error handling.
    """

    start_time = time.monotonic()
    normalized_query = _normalize_query_text(query_text)
    normalized_slot = _normalize_memory_slot(memory_slot)
    effective_embedding = query_embedding
    embedding_model_used: str | None = None
    effective_storage_embedding_model = storage_embedding_model

    ranked: list[dict[str, object]] = []
    if normalized_query:
        if effective_embedding is None:
            embedding_results = await embed_query_for_configured_storage_models(normalized_query)
        else:
            settings = get_settings()
            inferred_query_model = _embedding_metadata_value(effective_embedding, "model")
            inferred_storage_model = _embedding_metadata_value(effective_embedding, "storage_model")
            embedding_model_used = (
                query_embedding_model or inferred_query_model or settings.embedding_query_model
            )
            effective_storage_embedding_model = (
                effective_storage_embedding_model
                or inferred_storage_model
                or settings.embedding_document_model
            )
            embedding_results = [
                EmbeddingVectorResult(
                    embedding=effective_embedding,
                    provider=_embedding_metadata_value(effective_embedding, "provider")
                    or "unknown",
                    model=embedding_model_used,
                    storage_model=effective_storage_embedding_model,
                )
            ]

        combined_ranked: dict[uuid.UUID, dict[str, object]] = {}
        for index, embedding_result in enumerate(embedding_results):
            effective_embedding = embedding_result.embedding
            embedding_model_used = embedding_result.model
            effective_storage_embedding_model = embedding_result.storage_model
            partial_ranked = await retrieve_memories(
                store=store,
                query_embedding=effective_embedding,
                query_text=query_text,  # Use original query for entity extraction
                user_id=user_id,
                limit=limit,
                include_local=include_local,
                include_historical=include_historical,
                query_reference_time=query_reference_time,
                memory_slot=normalized_slot,
                log_retrieval=(
                    _is_retrieval_logging_enabled(log_retrieval) and not include_l0 and index == 0
                ),
                retrieval_triggered_by=retrieval_triggered_by,
                retrieval_context="prompt_injection" if retrieval_triggered_by is None else None,
                allowed_source_conversation_ids=allowed_source_conversation_ids,
                include_dream_observations=include_dream_observations,
                embedding_model=effective_storage_embedding_model,
                query_embedding_model=embedding_model_used,
            )
            for memory in partial_ranked:
                memory_id = memory.get("id")
                if not isinstance(memory_id, uuid.UUID):
                    continue
                existing = combined_ranked.get(memory_id)
                if existing is None or _as_float(memory.get("final_score"), 0.0) > _as_float(
                    existing.get("final_score"), 0.0
                ):
                    combined_ranked[memory_id] = memory
        ranked = sorted(
            combined_ranked.values(),
            key=lambda item: (
                -_as_float(item.get("final_score"), 0.0),
                str(item.get("id")),
            ),
        )[:limit]

    l0_included = False
    if include_l0:
        l0_memories = cast(list[dict[str, object]], await store.get_l0_memories(user_id))
        combined = _prepend_l0_memories(l0_memories, ranked)
        l0_included = len(l0_memories) > 0

        if (
            _is_retrieval_logging_enabled(log_retrieval)
            and normalized_query
            and effective_embedding is not None
        ):
            end_time = time.monotonic()
            latency_ms = int((end_time - start_time) * 1000)
            candidate_scores: dict[str, object] = {}
            for c in ranked:
                mid = c.get("id")
                if isinstance(mid, uuid.UUID):
                    candidate_scores[str(mid)] = {
                        "vector_sim": _as_float(c.get("vector_sim"), 0.0),
                        "bm25_normalized": _as_float(c.get("bm25_normalized"), 0.0),
                        "recency_boost": _as_float(c.get("recency_boost"), 0.0),
                        "source_boost": _as_float(c.get("source_boost"), 0.0),
                        "access_boost": _as_float(c.get("access_boost"), 0.0),
                        "confidence": _as_float(c.get("confidence"), 1.0),
                        "trust": _as_float(c.get("trust_score"), 0.5),
                        "final_score": _as_float(c.get("final_score"), 0.0),
                    }
            selected_ids: list[uuid.UUID] = [
                cast(uuid.UUID, m.get("id")) for m in ranked if isinstance(m.get("id"), uuid.UUID)
            ]

            async def _persist_l0_log() -> None:
                try:
                    await store.log_retrieval(
                        user_id=user_id,
                        query_text=normalized_query or "",
                        query_embedding_model=embedding_model_used
                        or get_settings().embedding_query_model,
                        query_embedding=effective_embedding,
                        candidate_memory_ids=[uuid.UUID(k) for k in candidate_scores],
                        candidate_scores=candidate_scores,
                        selected_memory_ids=selected_ids,
                        l0_included=l0_included,
                        latency_ms=latency_ms,
                        retrieval_context="prompt_injection"
                        if retrieval_triggered_by is None
                        else None,
                        retrieval_triggered_by=retrieval_triggered_by,
                    )
                except Exception:
                    logger.exception("Failed to persist L0 retrieval log")

            _ = asyncio.create_task(_persist_l0_log())

        ranked = combined

    return ranked


async def _get_entity_expanded_candidates(
    store: MemoryStore,
    user_id: uuid.UUID,
    query_text: str | None,
    *,
    include_local: bool = False,
    allowed_source_conversation_ids: list[uuid.UUID] | None = None,
) -> list[dict[str, object]]:
    if not query_text or not user_id:
        return []

    try:
        candidates = extract_candidates_baseline(query_text)
    except Exception:
        return []

    if not candidates:
        return []

    entity_linked_memories: list[dict[str, object]] = []
    seen_memory_ids: set[str] = set()
    allowed_conversation_ids = (
        {str(value) for value in allowed_source_conversation_ids}
        if allowed_source_conversation_ids
        else None
    )

    for candidate in candidates:
        lookup_key = candidate.normalized_key
        if lookup_key.startswith("@") or lookup_key.startswith("#"):
            pass
        else:
            lookup_key = _normalize_lookup_key(candidate.text)

        try:
            entities_by_alias = await store.find_entities_by_alias(user_id, lookup_key)
            entity_by_key = await store.get_entity_by_lookup_key(user_id, lookup_key)
            entities: list[dict[str, object]] = list(entities_by_alias)
            if entity_by_key and entity_by_key not in entities:
                entities.append(entity_by_key)
        except Exception:
            continue

        for entity in entities:
            entity_id = entity.get("id")
            if not entity_id:
                continue

            raw_linked = entity.get("linked_memory_ids")
            if not isinstance(raw_linked, list):
                continue
            linked_memory_ids = raw_linked
            for memory_id in linked_memory_ids:
                memory_id_str = str(memory_id)
                if memory_id_str in seen_memory_ids:
                    continue
                seen_memory_ids.add(memory_id_str)

                try:
                    memory = await store.get_memory(memory_id)
                    if not memory or not memory.get("content"):
                        continue
                    # Mirror search_memories() eligibility contract:
                    if memory.get("status") == "deleted":
                        continue
                    if memory.get("valid_to") is not None:
                        continue
                    if memory.get("source_type") == "dream":
                        continue
                    if memory.get("local_only") and not include_local:
                        continue
                    if allowed_conversation_ids is not None:
                        source_conversation_id = memory.get("source_conversation_id")
                        if (
                            source_conversation_id is None
                            or str(source_conversation_id) not in allowed_conversation_ids
                        ):
                            continue
                    memory["source"] = "entity_linked"
                    entity_linked_memories.append(memory)
                except Exception:
                    continue

    return entity_linked_memories


async def retrieve_memories(
    store: MemoryStore,
    query_embedding: list[float],
    query_text: str | None = None,
    conversation_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: int = 5,
    include_local: bool | None = None,
    include_historical: bool = False,
    query_reference_time: str | dt.datetime | None = None,
    memory_slot: str | None = None,
    log_retrieval: bool = False,
    retrieval_triggered_by: str | None = None,
    retrieval_context: str | None = None,
    allowed_source_conversation_ids: list[uuid.UUID] | None = None,
    include_dream_observations: bool = False,
    embedding_model: str | None = None,
    query_embedding_model: str | None = None,
) -> list[dict[str, object]]:
    if not query_embedding:
        return []
    if allowed_source_conversation_ids is not None and len(allowed_source_conversation_ids) == 0:
        return []

    start_time = time.monotonic()
    effective_include_local = bool(include_local)
    normalized_query = _normalize_query_text(query_text)
    normalized_slot = _normalize_memory_slot(memory_slot)
    effective_user_id: uuid.UUID | None = user_id
    temporal_window = _detect_temporal_query_window(
        normalized_query,
        query_reference_time=query_reference_time,
    )
    effective_include_historical = include_historical or temporal_window is not None

    if conversation_id is not None:
        conversation = await store.get_conversation(conversation_id)
        if not conversation:
            logger.debug("Conversation %s not found for memory retrieval", conversation_id)
            return []

        user_id_from_conv = conversation.get("user_id")
        if not isinstance(user_id_from_conv, uuid.UUID):
            logger.warning("Conversation %s has invalid user_id", conversation_id)
            return []

        effective_user_id = user_id_from_conv
        pipeline = str(conversation.get("pipeline") or "").strip().lower()
        if include_local is None:
            effective_include_local = pipeline == "local"

    if effective_user_id is None:
        logger.debug("No user_id provided for memory retrieval")
        return []

    target_limit = max(1, limit)
    vector_limit = max(INITIAL_VECTOR_CANDIDATES, target_limit)

    vector_candidates = cast(
        list[dict[str, object]],
        await store.search_memories(
            user_id=effective_user_id,
            query_embedding=query_embedding,
            limit=vector_limit,
            include_local=effective_include_local,
            include_historical=effective_include_historical,
            memory_slot=normalized_slot,
            include_dream_observations=include_dream_observations,
            source_conversation_ids=allowed_source_conversation_ids,
            embedding_model=embedding_model,
        ),
    )

    bm25_candidates: list[dict[str, object]] = []
    if normalized_query:
        bm25_candidates = cast(
            list[dict[str, object]],
            await store.search_memories_bm25(
                user_id=effective_user_id,
                query=normalized_query,
                limit=vector_limit,
                include_local=effective_include_local,
                include_historical=effective_include_historical,
                memory_slot=normalized_slot,
                include_dream_observations=include_dream_observations,
                source_conversation_ids=allowed_source_conversation_ids,
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
                candidate_map[memory_id]["bm25_score"] = _as_float(c.get("bm25_score"), 0.0)
                candidate_map[memory_id]["source"] = "hybrid"
            else:
                entry = dict(c)
                entry["vector_sim"] = 0.0
                entry["similarity"] = 0.0
                entry["source"] = "bm25"
                candidate_map[memory_id] = entry

    all_candidates = list(candidate_map.values())

    # Entity expansion: runs even when vector/BM25 candidates are empty
    # to support alias-only/entity-only queries that rely on entity-linked retrieval
    if normalized_query and effective_user_id:
        try:
            entity_candidates = await _get_entity_expanded_candidates(
                store,
                effective_user_id,
                query_text,  # Use original query (not normalized) for entity extraction
                include_local=effective_include_local,
                allowed_source_conversation_ids=allowed_source_conversation_ids,
            )
            for memory in entity_candidates:
                memory_id = str(memory.get("id", ""))
                if memory_id and memory_id not in candidate_map:
                    entry = dict(memory)
                    entry["vector_sim"] = _as_float(entry.get("similarity"), 0.0)
                    entry["bm25_score"] = 0.0
                    entry["bm25_normalized"] = 0.0
                    candidate_map[memory_id] = entry
            if entity_candidates:
                all_candidates = list(candidate_map.values())
        except Exception:
            pass

    if not all_candidates:
        if _is_retrieval_logging_enabled(log_retrieval):
            _log_retrieval_results(
                query_text=normalized_query,
                ranked=[],
                include_local=effective_include_local,
                include_historical=effective_include_historical,
                memory_slot=normalized_slot,
            )
        return []

    if temporal_window is not None:
        temporal_candidates = [
            item for item in all_candidates if _overlaps_temporal_window(item, temporal_window)
        ]
        if temporal_candidates:
            all_candidates = temporal_candidates

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
        item for item in scored if _as_float(item.get("final_score"), 0.0) >= MIN_FINAL_SCORE
    ]

    ranked = sorted(
        filtered,
        key=lambda item: (
            -_as_float(item.get("final_score"), 0.0),
            str(item.get("id", "")),
        ),
    )[:target_limit]

    if _is_retrieval_logging_enabled(log_retrieval):
        _log_retrieval_results(
            query_text=normalized_query,
            ranked=ranked,
            include_local=effective_include_local,
            include_historical=effective_include_historical,
            memory_slot=normalized_slot,
        )

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

    if _is_retrieval_logging_enabled(log_retrieval):
        latency_ms = int((time.monotonic() - start_time) * 1000)
        settings = get_settings()
        logged_embedding_model = query_embedding_model or settings.embedding_query_model
        candidate_scores: dict[str, object] = {}
        for c in scored:
            mid = c.get("id")
            if isinstance(mid, uuid.UUID):
                candidate_scores[str(mid)] = {
                    "vector_sim": _as_float(c.get("vector_sim"), 0.0),
                    "bm25_normalized": _as_float(c.get("bm25_normalized"), 0.0),
                    "recency_boost": _as_float(c.get("recency_boost"), 0.0),
                    "source_boost": _as_float(c.get("source_boost"), 0.0),
                    "access_boost": _as_float(c.get("access_boost"), 0.0),
                    "confidence": _as_float(c.get("confidence"), 1.0),
                    "trust": _as_float(c.get("trust_score"), 0.5),
                    "final_score": _as_float(c.get("final_score"), 0.0),
                }
        selected_ids: list[uuid.UUID] = [
            cast(uuid.UUID, m.get("id")) for m in ranked if isinstance(m.get("id"), uuid.UUID)
        ]
        l0_included = any(m.get("source") == "l0" for m in ranked)

        async def _persist_log() -> None:
            try:
                await store.log_retrieval(
                    user_id=effective_user_id,
                    query_text=normalized_query or "",
                    query_embedding_model=logged_embedding_model,
                    query_embedding=query_embedding,
                    candidate_memory_ids=[uuid.UUID(k) for k in candidate_scores],
                    candidate_scores=candidate_scores,
                    selected_memory_ids=selected_ids,
                    l0_included=l0_included,
                    latency_ms=latency_ms,
                    conversation_id=conversation_id,
                    retrieval_context=retrieval_context,
                    retrieval_triggered_by=retrieval_triggered_by,
                )
            except Exception:
                logger.exception("Failed to persist retrieval log")

        _ = asyncio.create_task(_persist_log())

    return ranked
