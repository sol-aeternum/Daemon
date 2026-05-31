from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

import asyncpg
import litellm

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_documents
from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Dynamic import for trust signals to avoid circular imports
_trust_signals = None


def _lazy_import_trust_signals():
    global _trust_signals
    if _trust_signals is None:
        import importlib

        try:
            _trust_signals = importlib.import_module("orchestrator.memory.trust_signals")
        except ImportError:
            pass
    return _trust_signals


def _document_model() -> str:
    return get_settings().embedding_document_model


def _embedding_text(content: str, slot: str | None) -> str:
    normalized_content = content.strip()
    if isinstance(slot, str) and slot.strip():
        return f"{slot.strip()}: {normalized_content}"
    return normalized_content


@dataclass
class DedupResult:
    merged: list[dict[str, Any]] = field(default_factory=list)
    superseded: list[dict[str, Any]] = field(default_factory=list)
    new: list[dict[str, Any]] = field(default_factory=list)


# Thresholds per Spec E - now configurable via config.py
SIMILARITY_MERGE = 0.85  # Deprecated: use get_settings().dedup_merge_threshold
SIMILARITY_SUPERSEDE = 0.75  # Deprecated: use get_settings().dedup_supersede_threshold
SIMILARITY_SUPERSEDE_SAME_SLOT = (
    0.60  # Deprecated: use get_settings().dedup_supersede_same_slot_threshold
)
EXPLICIT_SUPPRESSION_WINDOW = timedelta(minutes=5)


def _get_merge_threshold() -> float:
    return get_settings().dedup_merge_threshold


def _get_supersede_threshold() -> float:
    return get_settings().dedup_supersede_threshold


def _get_supersede_same_slot_threshold() -> float:
    return get_settings().dedup_supersede_same_slot_threshold


def _slot_family(slot: str | None) -> str | None:
    if not isinstance(slot, str):
        return None
    cleaned = slot.strip().lower()
    if not cleaned:
        return None
    return cleaned.split(".")[0]


def _is_current_slot(slot: str | None) -> bool:
    if not isinstance(slot, str):
        return False
    return slot.strip().lower().endswith(".current")


def _is_current_like_slot(slot: str | None) -> bool:
    if _is_current_slot(slot):
        return True
    if not isinstance(slot, str):
        return False
    return slot.strip().lower() == "vehicle"


def _as_uuid_or_none(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _as_datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _is_explicit_source(value: Any) -> bool:
    return str(value or "").strip().lower() == "user_created"


def _is_protected_explicit_match(
    best_match: dict[str, Any],
    incoming_source_type: str,
    conversation_id: uuid.UUID | None,
) -> bool:
    if incoming_source_type != "extracted":
        return False
    if not _is_explicit_source(best_match.get("source_type")):
        return False

    if conversation_id is not None:
        candidate_conv = _as_uuid_or_none(best_match.get("source_conversation_id"))
        if candidate_conv is not None and candidate_conv == conversation_id:
            return True

    created_at = _as_datetime_or_none(best_match.get("created_at"))
    if created_at is None:
        return False

    now = datetime.now(tz=created_at.tzinfo)
    return now - created_at <= EXPLICIT_SUPPRESSION_WINDOW


async def check_contradiction(
    existing_content: str,
    new_content: str,
) -> tuple[bool, str]:
    """Check if two facts contradict each other.

    Returns (contradiction_detected, explanation).
    Contradiction detection is ADVISORY - callers should proceed regardless.
    LLM failures result in (False, "").
    """
    try:
        response = await litellm.acompletion(
            model=get_settings().background_reasoning_model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Do these two facts contradict each other? "
                        f"Fact A: {existing_content}. Fact B: {new_content}. "
                        f"Reply YES or NO with a one-sentence explanation."
                    ),
                }
            ],
            temperature=0.1,
            max_tokens=50,
        )
        response_data: Any = response
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            response_data = model_dump()
        else:
            dict_method = getattr(response, "dict", None)
            if callable(dict_method):
                response_data = dict_method()

        content = None
        if isinstance(response_data, dict):
            choices = response_data.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(message, dict):
                    content = message.get("content")

        if not isinstance(content, str) or not content:
            return False, ""

        contradiction_detected = content.lower().startswith("yes")
        explanation = content.strip() if contradiction_detected else ""
        return contradiction_detected, explanation
    except Exception:
        return False, ""


async def _close_active_family_memories(
    store: MemoryStore,
    user_id: uuid.UUID,
    slot_family: str,
    keep_id: uuid.UUID | None,
    excluded_ids: set[uuid.UUID] | None = None,
) -> None:
    rows = await store._pool.fetch(
        """
        SELECT id
        FROM memories
        WHERE user_id = $1
          AND valid_to IS NULL
          AND memory_slot IS NOT NULL
          AND split_part(lower(memory_slot), '.', 1) = $2
        """,
        user_id,
        slot_family.lower(),
    )
    for row in rows:
        memory_id = _as_uuid_or_none(row.get("id"))
        if memory_id is None:
            continue
        if keep_id is not None and memory_id == keep_id:
            continue
        if excluded_ids is not None and memory_id in excluded_ids:
            continue
        await store.close_memory(memory_id)


async def _close_current_related_candidates(
    store: MemoryStore,
    similar: list[dict[str, Any]],
    slot_family: str,
    keep_id: uuid.UUID | None,
) -> set[uuid.UUID]:
    closed_ids: set[uuid.UUID] = set()
    for candidate in similar:
        if candidate.get("valid_to") is not None:
            continue
        candidate_id = _as_uuid_or_none(candidate.get("id"))
        if candidate_id is None:
            continue
        if keep_id is not None and candidate_id == keep_id:
            continue

        candidate_family = _slot_family(candidate.get("memory_slot"))
        if candidate_family == slot_family:
            await store.close_memory(candidate_id)
            closed_ids.add(candidate_id)
            continue

        similarity = float(candidate.get("similarity") or 0.0)
        if candidate_family is None and similarity >= _get_supersede_same_slot_threshold():
            await store.close_memory(candidate_id)
            closed_ids.add(candidate_id)
    return closed_ids


async def deduplicate_facts(
    store: MemoryStore,
    user_id: uuid.UUID,
    facts: list[Any],
    conversation_id: uuid.UUID | None,
    *,
    source_type: str = "extracted",
    status: str = "active",
) -> DedupResult:
    """Deduplicate extracted facts against existing memories."""
    result = DedupResult()
    current_slot_families: set[str] = set()
    current_family_keep_ids: dict[str, uuid.UUID] = {}

    for fact in facts:
        fact_slot = getattr(fact, "slot", None)
        fact_slot_family = _slot_family(fact_slot)
        current_like_slot = _is_current_like_slot(fact_slot)
        if current_like_slot and fact_slot_family:
            current_slot_families.add(fact_slot_family)
        embedding_input = _embedding_text(fact.content, fact_slot)
        embedding = (await embed_documents([embedding_input]))[0]

        min_similarity = (
            _get_supersede_same_slot_threshold() if fact_slot_family else _get_supersede_threshold()
        )
        if _is_current_slot(fact_slot):
            min_similarity = 0.0
        similar = await store.search_memories(
            user_id=user_id,
            query_embedding=embedding,
            limit=50,
            min_similarity=min_similarity,
            include_historical=True,
            memory_slot=None,
        )
        best_match: dict[str, Any] | None = None
        supersede_threshold = _get_supersede_threshold()

        if fact_slot_family:
            slot_matches = [
                m
                for m in similar
                if _slot_family(m.get("memory_slot")) == fact_slot_family
                and m.get("valid_to") is None
            ]
            if slot_matches:
                exact_slot_matches = [m for m in slot_matches if m.get("memory_slot") == fact_slot]
                if exact_slot_matches:
                    best_match = exact_slot_matches[0]
                    supersede_threshold = _get_supersede_same_slot_threshold()
                else:
                    best_match = slot_matches[0]
                    supersede_threshold = _get_supersede_same_slot_threshold()
            elif similar:
                active_matches = [m for m in similar if m.get("valid_to") is None]
                best_match = active_matches[0] if active_matches else similar[0]
        elif similar:
            active_matches = [m for m in similar if m.get("valid_to") is None]
            best_match = active_matches[0] if active_matches else similar[0]

        if not best_match:
            logger.debug(
                "Dedup branch=new fact=%r slot=%r family=%r similar=%d",
                fact.content,
                fact_slot,
                fact_slot_family,
                len(similar),
            )
            memory = await store.insert_memory(
                user_id=user_id,
                content=fact.content,
                category=fact.category,
                source_type=source_type,
                embedding=embedding,
                embedding_model=_document_model(),
                source_conversation_id=conversation_id,
                confidence=fact.confidence,
                status=status,
                memory_slot=fact_slot,
            )
            result.new.append(memory)
            if current_like_slot and fact_slot_family:
                new_id = memory.get("id")
                closed_ids = await _close_current_related_candidates(
                    store,
                    similar,
                    fact_slot_family,
                    _as_uuid_or_none(new_id),
                )
                await _close_active_family_memories(
                    store,
                    user_id,
                    fact_slot_family,
                    _as_uuid_or_none(new_id),
                    excluded_ids=closed_ids,
                )
                normalized_new_id = _as_uuid_or_none(new_id)
                if normalized_new_id is not None:
                    current_family_keep_ids[fact_slot_family] = normalized_new_id
        else:
            similarity = best_match.get("similarity", 0)
            best_match_id = best_match["id"]
            logger.debug(
                "Dedup candidate fact=%r slot=%r family=%r best_id=%s similarity=%.4f merge=%.2f supersede=%.2f",
                fact.content,
                fact_slot,
                fact_slot_family,
                best_match_id,
                float(similarity),
                _get_merge_threshold(),
                supersede_threshold,
            )

            if _is_protected_explicit_match(
                best_match=best_match,
                incoming_source_type=source_type,
                conversation_id=conversation_id,
            ):
                await store.touch_memory(best_match_id)
                result.merged.append(best_match)
                continue

            if similarity >= _get_merge_threshold():
                # Block merge when both have explicit, different slots — sibling facts.
                best_match_slot = best_match.get("memory_slot")
                if (
                    fact_slot is not None
                    and best_match_slot is not None
                    and fact_slot != best_match_slot
                ):
                    logger.debug(
                        "Dedup branch=new_sibling (merge blocked) fact=%r slot=%r vs existing slot=%r",
                        fact.content,
                        fact_slot,
                        best_match_slot,
                    )
                    memory = await store.insert_memory(
                        user_id=user_id,
                        content=fact.content,
                        category=fact.category,
                        source_type=source_type,
                        embedding=embedding,
                        embedding_model=_document_model(),
                        source_conversation_id=conversation_id,
                        confidence=fact.confidence,
                        status=status,
                        memory_slot=fact_slot,
                    )
                    result.new.append(memory)
                else:
                    await store.touch_memory(best_match_id)
                    result.merged.append(best_match)
                    if current_like_slot and fact_slot_family:
                        closed_ids = await _close_current_related_candidates(
                            store,
                            similar,
                            fact_slot_family,
                            best_match_id,
                        )
                        await _close_active_family_memories(
                            store,
                            user_id,
                            fact_slot_family,
                            best_match_id,
                            excluded_ids=closed_ids,
                        )
                        current_family_keep_ids[fact_slot_family] = best_match_id
            elif similarity >= supersede_threshold:
                # Block supersession when both facts have explicit, different slots.
                # Same-family siblings (e.g. language.python vs language.typescript)
                # are parallel facts, not updates to the same fact.
                best_match_slot = best_match.get("memory_slot")
                if (
                    fact_slot is not None
                    and best_match_slot is not None
                    and fact_slot != best_match_slot
                ):
                    logger.debug(
                        "Dedup branch=new_sibling fact=%r slot=%r vs existing slot=%r — different slots, inserting as new",
                        fact.content,
                        fact_slot,
                        best_match_slot,
                    )
                    memory = await store.insert_memory(
                        user_id=user_id,
                        content=fact.content,
                        category=fact.category,
                        source_type=source_type,
                        embedding=embedding,
                        embedding_model=_document_model(),
                        source_conversation_id=conversation_id,
                        confidence=fact.confidence,
                        status=status,
                        memory_slot=fact_slot,
                    )
                    result.new.append(memory)
                else:
                    existing_content = best_match.get("content", "")
                    contradiction_detected, explanation = await check_contradiction(
                        existing_content, fact.content
                    )
                    metadata = None
                    if contradiction_detected:
                        metadata = {
                            "contradiction_detected": True,
                            "contradiction_explanation": explanation,
                        }
                    supersede_kwargs: dict[str, Any] = {
                        "old_memory_id": best_match_id,
                        "new_content": fact.content,
                        "new_category": fact.category,
                        "new_source_type": source_type,
                        "user_id": user_id,
                        "embedding": embedding,
                        "embedding_model": _document_model(),
                        "source_conversation_id": conversation_id,
                        "confidence": fact.confidence,
                        "new_status": status,
                        "memory_slot": fact_slot or best_match.get("memory_slot"),
                    }

                    try:
                        new_memory = await store.supersede_memory(
                            **supersede_kwargs,
                            metadata=metadata,
                        )
                    except asyncpg.UndefinedColumnError as error:
                        if metadata is None:
                            raise
                        logger.warning(
                            "Dedup contradiction metadata unavailable; retrying supersede without metadata (%s)",
                            error,
                        )
                        contradiction_detected, explanation = False, ""
                        new_memory = await store.supersede_memory(
                            **supersede_kwargs,
                            metadata=None,
                        )
                    result.superseded.append(new_memory)

                    # Apply explicit negative trust signal for superseded memory
                    try:
                        ts_module = _lazy_import_trust_signals()
                        if ts_module and best_match.get("id"):
                            superseded_id = uuid.UUID(str(best_match.get("id")))
                            await ts_module.apply_explicit_negative_signal(
                                superseded_memory_id=superseded_id,
                                store=store,
                            )
                    except Exception:
                        pass  # Trust signals are best-effort

                    if current_like_slot and fact_slot_family:
                        new_id = new_memory.get("id")
                        closed_ids = await _close_current_related_candidates(
                            store,
                            similar,
                            fact_slot_family,
                            _as_uuid_or_none(new_id),
                        )
                        await _close_active_family_memories(
                            store,
                            user_id,
                            fact_slot_family,
                            _as_uuid_or_none(new_id),
                            excluded_ids=closed_ids,
                        )
                        normalized_new_id = _as_uuid_or_none(new_id)
                        if normalized_new_id is not None:
                            current_family_keep_ids[fact_slot_family] = normalized_new_id
            else:
                memory = await store.insert_memory(
                    user_id=user_id,
                    content=fact.content,
                    category=fact.category,
                    source_type=source_type,
                    embedding=embedding,
                    embedding_model=_document_model(),
                    source_conversation_id=conversation_id,
                    confidence=fact.confidence,
                    status=status,
                    memory_slot=fact_slot,
                )
                result.new.append(memory)

                if current_like_slot and fact_slot_family:
                    new_id = memory.get("id")
                    closed_ids = await _close_current_related_candidates(
                        store,
                        similar,
                        fact_slot_family,
                        _as_uuid_or_none(new_id),
                    )
                    await _close_active_family_memories(
                        store,
                        user_id,
                        fact_slot_family,
                        _as_uuid_or_none(new_id),
                        excluded_ids=closed_ids,
                    )
                    normalized_new_id = _as_uuid_or_none(new_id)
                    if normalized_new_id is not None:
                        current_family_keep_ids[fact_slot_family] = normalized_new_id

    for slot_family in current_slot_families:
        keep_id = current_family_keep_ids.get(slot_family)
        if keep_id is None:
            logger.warning("Dedup post-close skipped family=%s keep_id=None", slot_family)
            continue
        logger.warning(
            "Dedup post-close executing family=%s keep_id=%s",
            slot_family,
            keep_id,
        )
        await store._pool.execute(
            """
            UPDATE memories
            SET valid_to = NOW(),
                updated_at = NOW()
            WHERE user_id = $1
              AND valid_to IS NULL
              AND memory_slot IS NOT NULL
              AND split_part(lower(memory_slot), '.', 1) = $2
              AND id != $3
            """,
            user_id,
            slot_family,
            keep_id,
        )

    return result


async def dedup_and_store(
    store: MemoryStore,
    user_id: uuid.UUID,
    content: str,
    source_type: str,
    category: str,
    conversation_id: uuid.UUID | None = None,
    *,
    status: str = "active",
    slot: str | None = None,
) -> uuid.UUID:
    """Store a single memory with deduplication.

    Returns the memory ID (existing if merged/superseded, new if created).
    """
    from dataclasses import dataclass

    @dataclass
    class SimpleFact:
        content: str
        category: str
        confidence: float = 0.8
        slot: str | None = None

    fact = SimpleFact(content=content, category=category, slot=slot)
    result = await deduplicate_facts(
        store=store,
        user_id=user_id,
        facts=[fact],
        conversation_id=conversation_id,
        source_type=source_type,
        status=status,
    )

    if result.merged:
        return result.merged[0]["id"]
    elif result.superseded:
        return result.superseded[0]["id"]
    elif result.new:
        return result.new[0]["id"]
    else:
        # Fallback - create directly
        embedding_input = _embedding_text(content, slot)
        embedding = (await embed_documents([embedding_input]))[0]
        memory = await store.insert_memory(
            user_id=user_id,
            content=content,
            category=category,
            source_type=source_type,
            embedding=embedding,
            embedding_model=_document_model(),
            source_conversation_id=conversation_id,
            status=status,
            memory_slot=slot,
        )
        return memory["id"]
