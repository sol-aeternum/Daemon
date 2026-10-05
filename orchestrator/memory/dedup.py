from __future__ import annotations

from orchestrator.compute_runtime import guarded_completion
from orchestrator.memory.completion import read_completeness
from orchestrator.memory.embedding import EmbeddingConfigurationError, EmbeddingRequestError

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import asyncpg

from orchestrator.config import get_settings
from orchestrator.memory.embedding import (
    embed_documents_with_metadata,
    get_configured_embedding_fallback_storage_models,
)
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.equivalence import (
    EquivalencePlan,
    IncomingMemory,
    eligible,
    may_merge_sources,
    plan_equivalence,
)
from orchestrator.model_routing import routing_context

logger = logging.getLogger(__name__)

# Contradiction detection is a judgement call over two stored facts, so it runs
# on the reasoning profile. Benchmark mode is exempt: it stays pinned to the
# dated snapshot model and never enters workload-profile routing.
CONTRADICTION_PROFILE = "reasoning"

# The automatic verdict call must never mistake a fragment cut off by its own
# output bound for a valid "NO", so it uses the approved helper output budget
# instead of the historic 50-token cap.
AUTOMATIC_CONTRADICTION_MAX_TOKENS = 4096

# Historic benchmark cap: the pinned deterministic benchmark call keeps it.
BENCHMARK_CONTRADICTION_MAX_TOKENS = 50

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


def _is_fallback_storage_model(model: str) -> bool:
    return model != _document_model()


def _has_configured_fallback_storage_spaces() -> bool:
    return bool(get_configured_embedding_fallback_storage_models())


def _normalize_lexical_content(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _embedding_text(content: str, slot: str | None) -> str:
    normalized_content = content.strip()
    if isinstance(slot, str) and slot.strip():
        return f"{slot.strip()}: {normalized_content}"
    return normalized_content


def _single_vector(result: Any) -> list[float]:
    """The one vector for one non-empty text, or a clear error.

    The embedding helper silently drops blank texts and can return an empty
    batch. For non-empty text that is a provider fault, not the same as
    embeddings being unqualified (which keeps its explicit lexical fallback),
    so it raises instead of indexing past the end.
    """
    embeddings = getattr(result, "embeddings", None) or []
    if len(embeddings) != 1:
        raise EmbeddingRequestError(
            f"embedding provider returned {len(embeddings)} vectors for one memory text"
        )
    return embeddings[0]


@dataclass
class DedupResult:
    merged: list[dict[str, Any]] = field(default_factory=list)
    superseded: list[dict[str, Any]] = field(default_factory=list)
    new: list[dict[str, Any]] = field(default_factory=list)
    deferred_supersede_effects: list[DeferredSupersedeEffects] = field(default_factory=list)


@dataclass(frozen=True)
class DeferredSupersedeEffects:
    """External and pool-backed work deferred until a cap transaction commits."""

    superseded_memory_id: uuid.UUID
    new_memory_id: uuid.UUID
    existing_content: str
    new_content: str


# Thresholds per Spec E - now configurable via config.py
SIMILARITY_MERGE = 0.85  # Deprecated: use get_settings().dedup_merge_threshold
SIMILARITY_SUPERSEDE = 0.75  # Deprecated: use get_settings().dedup_supersede_threshold
SIMILARITY_SUPERSEDE_SAME_SLOT = (
    0.60  # Deprecated: use get_settings().dedup_supersede_same_slot_threshold
)
EXPLICIT_SUPPRESSION_WINDOW = timedelta(minutes=5)
CONTRADICTION_TEMPERATURE = 0.1
DEDUP_BENCHMARK_SEED = 42
BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-chat-v3-5"
# Historical benchmark scripts assign this name; transport ignores it and uses
# only the reviewed inference policy. Remove when those archived scripts retire.
BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = BENCHMARK_CONTRADICTION_MODEL
DEDUP_BENCHMARK_MODE = False


class DedupBenchmarkProviderError(RuntimeError):
    """Provider or transport failure in dedup benchmark mode."""


class DedupBenchmarkSamplingError(RuntimeError):
    """Non-deterministic dedup benchmark sampling metadata was detected."""


_DEDUP_BM_METADATA: dict[str, dict[str, str | None]] = {}


def reset_dedup_benchmark_tracking() -> None:
    _DEDUP_BM_METADATA.clear()


def get_dedup_benchmark_tracking() -> dict[str, dict[str, str | None]]:
    return {key: dict(value) for key, value in _DEDUP_BM_METADATA.items()}


def _capture_dedup_benchmark_metadata(response_data: Any, *, key: str) -> None:
    if not isinstance(response_data, dict):
        return

    fingerprint = response_data.get("system_fingerprint")
    model = response_data.get("model")
    normalized_fingerprint = fingerprint if isinstance(fingerprint, str) else None
    normalized_model = model if isinstance(model, str) else None

    previous = _DEDUP_BM_METADATA.get(key)
    if previous is not None:
        previous_fingerprint = previous.get("fingerprint")
        if (
            previous_fingerprint
            and normalized_fingerprint
            and previous_fingerprint != normalized_fingerprint
        ):
            raise DedupBenchmarkSamplingError(
                f"Benchmark fingerprint drift in {key}: "
                f"expected {previous_fingerprint!r}, got {normalized_fingerprint!r}"
            )

    _DEDUP_BM_METADATA[key] = {
        "fingerprint": normalized_fingerprint,
        "model": normalized_model,
    }


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
    benchmark_mode: bool | None = None,
) -> tuple[bool, str]:
    """Check if two facts contradict each other.

    Returns (contradiction_detected, explanation).
    Contradiction detection is ADVISORY - callers should proceed regardless.
    LLM failures result in (False, "").
    """
    is_benchmark = DEDUP_BENCHMARK_MODE if benchmark_mode is None else bool(benchmark_mode)
    try:
        call_params: dict[str, Any] = {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Do these two facts contradict each other? "
                        f"Fact A: {existing_content}. Fact B: {new_content}. "
                        f"Reply YES or NO with a one-sentence explanation."
                    ),
                }
            ],
            "temperature": 0.0 if is_benchmark else CONTRADICTION_TEMPERATURE,
            # A YES/NO verdict with one sentence of evidence. The automatic
            # call uses the approved helper output budget so a reasoning-preset
            # response is not truncated into a false verdict; the historic
            # benchmark cap stays on the pinned benchmark call. This bound is
            # the real output contract, not a routing hint; the reasoning
            # profile's min-output floor is a separate model-capability gate.
            "max_tokens": (
                BENCHMARK_CONTRADICTION_MAX_TOKENS
                if is_benchmark
                else AUTOMATIC_CONTRADICTION_MAX_TOKENS
            ),
        }
        if is_benchmark:
            # Benchmark isolation: pin the dated snapshot model and seed, and
            # tell the guard the pin is exact even inside an automatic scope.
            call_params["model"] = BENCHMARK_CONTRADICTION_MODEL
            call_params["seed"] = DEDUP_BENCHMARK_SEED
            call_params["_exact_model"] = True

        try:
            if is_benchmark:
                response = await guarded_completion(**call_params)
            else:
                # No explicit model: the guard picks an approved reasoning route.
                with routing_context(CONTRADICTION_PROFILE):
                    response = await guarded_completion(**call_params)
        except Exception as exc:
            if is_benchmark:
                raise DedupBenchmarkProviderError(
                    f"Benchmark-mode contradiction provider failure: {exc}"
                ) from exc
            raise

        response_data: Any = response
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            response_data = model_dump()
        else:
            dict_method = getattr(response, "dict", None)
            if callable(dict_method):
                response_data = dict_method()

        if is_benchmark:
            _capture_dedup_benchmark_metadata(response_data, key="contradiction")

        completeness = read_completeness(response)
        if not completeness.complete:
            if is_benchmark:
                # Historic benchmark semantics: an unusable reply is advisory
                # silence, not an error signal.
                return False, ""
            # An empty or bound-truncated reply must not read as a legitimate
            # "NO". Surface one content-free warning and a distinguishable
            # reason; the advisory contract still proceeds on False.
            reason = completeness.reason or "unreadable response"
            logger.warning("Contradiction check did not complete: %s", reason)
            return False, f"error: {reason}"

        content = completeness.content
        contradiction_detected = content.lower().startswith("yes")
        explanation = content.strip() if contradiction_detected else ""
        return contradiction_detected, explanation
    except (DedupBenchmarkProviderError, DedupBenchmarkSamplingError):
        raise
    except Exception:
        return False, ""


async def _touch_memory(store: MemoryStore, memory_id: uuid.UUID, conn: Any | None) -> None:
    if conn is None:
        await store.touch_memory(memory_id)
    else:
        await store.touch_memory(memory_id, conn=conn)


async def _close_memory(
    store: MemoryStore,
    memory_id: uuid.UUID,
    conn: Any | None,
    *,
    user_id: uuid.UUID | None = None,
) -> bool:
    kwargs: dict[str, Any] = {}
    if conn is not None:
        kwargs["conn"] = conn
    if user_id is not None:
        kwargs["user_id"] = user_id
    return await store.close_memory(memory_id, **kwargs)


async def _close_active_family_memories(
    store: MemoryStore,
    user_id: uuid.UUID,
    slot_family: str,
    keep_id: uuid.UUID | None,
    excluded_ids: set[uuid.UUID] | None = None,
    conn: Any | None = None,
) -> None:
    # Issue #221: when the caller holds the per-user cap lock, route
    # the SELECT onto ``conn`` so the close participates in the same
    # transaction. When called outside a cap-locked path (extraction
    # without the lock), fall back to the pool.
    executor = conn if conn is not None else store._pool
    rows = await executor.fetch(
        """
        SELECT id
        FROM memories
        WHERE user_id = $1
          AND status != 'deleted'
          AND tier != 'l0'
          AND source_type != 'dream'
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
        await _close_memory(store, memory_id, conn)


async def _find_slot_family_candidates(
    store: MemoryStore,
    user_id: uuid.UUID,
    slot_family: str,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    finder = getattr(store, "list_memories_by_slot_family", None)
    if not callable(finder):
        return []
    typed_finder = cast(Callable[..., Awaitable[list[dict[str, Any]]]], finder)

    try:
        candidates = await typed_finder(
            user_id,
            slot_family,
            include_historical=True,
            limit=50,
            conn=conn,
        )
    except Exception:
        logger.exception("Failed to fetch same-slot dedup candidates")
        return []

    return candidates if isinstance(candidates, list) else []


async def _close_current_related_candidates(
    store: MemoryStore,
    similar: list[dict[str, Any]],
    slot_family: str,
    keep_id: uuid.UUID | None,
    conn: Any | None = None,
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
            await _close_memory(store, candidate_id, conn)
            closed_ids.add(candidate_id)
            continue

        similarity = float(candidate.get("similarity") or 0.0)
        if candidate_family is None and similarity >= _get_supersede_same_slot_threshold():
            await _close_memory(store, candidate_id, conn)
            closed_ids.add(candidate_id)
    return closed_ids


async def _deduplicate_facts_benchmark(
    store: MemoryStore,
    user_id: uuid.UUID,
    facts: list[Any],
    conversation_id: uuid.UUID | None,
    *,
    source_type: str = "extracted",
    status: str = "active",
    lock_conn: Any | None = None,
    prepared_embeddings: list[Any] | None = None,
    excluded_memory_ids: set[uuid.UUID] | None = None,
) -> DedupResult:
    """Frozen threshold algorithm for explicitly historical benchmarks only.

    Production never enters this implementation unless the offline benchmark
    harness explicitly sets DEDUP_BENCHMARK_MODE. Locked writes are forbidden.
    """
    if lock_conn is not None:
        raise ValueError("historical threshold benchmark cannot run inside a write lock")
    result = DedupResult()
    current_slot_families: set[str] = set()
    current_family_keep_ids: dict[str, uuid.UUID] = {}
    if prepared_embeddings is not None and len(prepared_embeddings) != len(facts):
        raise ValueError("prepared_embeddings must match facts length")

    for fact_index, fact in enumerate(facts):
        if not str(getattr(fact, "content", "") or "").strip():
            # Blank text must never reach embedding or storage. Skipping by
            # index keeps any prepared embeddings aligned with their facts.
            logger.warning("deduplicate_facts skipped a blank fact at index %d", fact_index)
            continue
        fact_slot = getattr(fact, "slot", None)
        fact_slot_family = _slot_family(fact_slot)
        current_like_slot = _is_current_like_slot(fact_slot)
        embedding_input = _embedding_text(fact.content, fact_slot)
        try:
            embedding_result = (
                prepared_embeddings[fact_index]
                if prepared_embeddings is not None
                else await embed_documents_with_metadata([embedding_input])
            )
        except EmbeddingConfigurationError:
            # No semantic similarity score is available. Only identical active
            # facts in the same slot/source/category can be merged safely;
            # never close a slot family based on lexical similarity alone.
            matches = await store.search_memories_bm25(
                user_id=user_id,
                query=fact.content,
                limit=50,
                include_local=True,
                memory_slot=fact_slot,
                conn=lock_conn,
            )
            exact = next(
                (
                    match
                    for match in matches
                    if match.get("content") == fact.content
                    and match.get("memory_slot") == fact_slot
                    and match.get("source_type") == source_type
                    and match.get("category") == fact.category
                    and match.get("status") == status
                    and match.get("valid_to") is None
                ),
                None,
            )
            if exact is not None:
                result.merged.append(exact)
            else:
                memory = await store.insert_memory(
                    user_id=user_id,
                    content=fact.content,
                    category=fact.category,
                    source_type=source_type,
                    embedding=None,
                    source_conversation_id=conversation_id,
                    confidence=fact.confidence,
                    status=status,
                    memory_slot=fact_slot,
                    conn=lock_conn,
                )
                result.new.append(memory)
            continue
        if current_like_slot and fact_slot_family:
            current_slot_families.add(fact_slot_family)
        embedding = _single_vector(embedding_result)
        document_model = embedding_result.storage_model

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
            embedding_model=document_model,
            conn=lock_conn,
        )
        if _is_fallback_storage_model(document_model) or _has_configured_fallback_storage_spaces():
            enabled_storage_models = sorted(
                {
                    _document_model(),
                    document_model,
                    *get_configured_embedding_fallback_storage_models(),
                }
            )
            raw_lexical_candidates = await store.search_memories_bm25(
                user_id=user_id,
                query=fact.content,
                limit=50,
                include_historical=True,
                memory_slot=None,
                embedding_models=enabled_storage_models,
                include_l0=False,
                conn=lock_conn,
            )
            lexical_candidates = (
                raw_lexical_candidates if isinstance(raw_lexical_candidates, list) else []
            )
            seen_ids = {candidate.get("id") for candidate in similar}
            normalized_fact_content = _normalize_lexical_content(fact.content)
            for candidate in lexical_candidates:
                candidate_id = candidate.get("id")
                if candidate_id in seen_ids:
                    continue
                candidate_slot = candidate.get("memory_slot")
                lexical_similarity = 0.0
                if _normalize_lexical_content(candidate.get("content")) == normalized_fact_content:
                    lexical_similarity = _get_merge_threshold()
                elif fact_slot is not None and candidate_slot == fact_slot:
                    lexical_similarity = _get_supersede_same_slot_threshold()
                elif fact_slot_family and _slot_family(candidate_slot) == fact_slot_family:
                    lexical_similarity = _get_supersede_same_slot_threshold()

                if lexical_similarity <= 0:
                    continue
                candidate_with_similarity = dict(candidate)
                candidate_with_similarity["similarity"] = max(
                    float(candidate_with_similarity.get("similarity") or 0.0),
                    lexical_similarity,
                )
                similar.append(candidate_with_similarity)
                seen_ids.add(candidate_id)
            if fact_slot_family:
                slot_family_candidates = await _find_slot_family_candidates(
                    store,
                    user_id,
                    fact_slot_family,
                    conn=lock_conn,
                )
                for candidate in slot_family_candidates:
                    candidate_id = candidate.get("id")
                    if candidate_id in seen_ids:
                        continue
                    candidate_slot = candidate.get("memory_slot")
                    lexical_similarity = 0.0
                    if fact_slot is not None and candidate_slot == fact_slot:
                        lexical_similarity = _get_supersede_same_slot_threshold()
                    elif _slot_family(candidate_slot) == fact_slot_family:
                        lexical_similarity = _get_supersede_same_slot_threshold()

                    if lexical_similarity <= 0:
                        continue
                    candidate_with_similarity = dict(candidate)
                    candidate_with_similarity["similarity"] = max(
                        float(candidate_with_similarity.get("similarity") or 0.0),
                        lexical_similarity,
                    )
                    similar.append(candidate_with_similarity)
                    seen_ids.add(candidate_id)
        # The caller may have closed one or more rows earlier in the same
        # transaction. Apply the exclusion once, AFTER every candidate
        # source has been appended; filtering before the BM25/slot-family
        # fallback loops would let excluded IDs re-enter the candidate set.
        if excluded_memory_ids:
            similar = [
                m for m in similar if _as_uuid_or_none(m.get("id")) not in excluded_memory_ids
            ]
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
                embedding_model=document_model,
                source_conversation_id=conversation_id,
                confidence=fact.confidence,
                status=status,
                memory_slot=fact_slot,
                conn=lock_conn,
            )
            result.new.append(memory)
            if current_like_slot and fact_slot_family:
                new_id = memory.get("id")
                closed_ids = await _close_current_related_candidates(
                    store,
                    similar,
                    fact_slot_family,
                    _as_uuid_or_none(new_id),
                    conn=lock_conn,
                )
                await _close_active_family_memories(
                    store,
                    user_id,
                    fact_slot_family,
                    _as_uuid_or_none(new_id),
                    excluded_ids=closed_ids,
                    conn=lock_conn,
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
                await _touch_memory(store, best_match_id, lock_conn)
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
                        embedding_model=document_model,
                        source_conversation_id=conversation_id,
                        confidence=fact.confidence,
                        status=status,
                        memory_slot=fact_slot,
                        conn=lock_conn,
                    )
                    result.new.append(memory)
                else:
                    await _touch_memory(store, best_match_id, lock_conn)
                    result.merged.append(best_match)
                    if current_like_slot and fact_slot_family:
                        closed_ids = await _close_current_related_candidates(
                            store,
                            similar,
                            fact_slot_family,
                            best_match_id,
                            conn=lock_conn,
                        )
                        await _close_active_family_memories(
                            store,
                            user_id,
                            fact_slot_family,
                            best_match_id,
                            excluded_ids=closed_ids,
                            conn=lock_conn,
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
                        embedding_model=document_model,
                        source_conversation_id=conversation_id,
                        confidence=fact.confidence,
                        status=status,
                        memory_slot=fact_slot,
                        conn=lock_conn,
                    )
                    result.new.append(memory)
                else:
                    existing_content = best_match.get("content", "")
                    metadata = None
                    if lock_conn is None:
                        contradiction_detected, explanation = await check_contradiction(
                            existing_content, fact.content
                        )
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
                        "embedding_model": document_model,
                        "source_conversation_id": conversation_id,
                        "confidence": fact.confidence,
                        "new_status": status,
                        "memory_slot": fact_slot or best_match.get("memory_slot"),
                    }

                    if lock_conn is not None:
                        new_memory = await store.insert_memory(
                            user_id=user_id,
                            content=fact.content,
                            category=fact.category,
                            source_type=source_type,
                            embedding=embedding,
                            embedding_model=document_model,
                            source_conversation_id=conversation_id,
                            confidence=fact.confidence,
                            status=status,
                            memory_slot=fact_slot or best_match.get("memory_slot"),
                            metadata=metadata,
                            conn=lock_conn,
                        )
                        if new_memory.get("id") != best_match_id:
                            closed = await _close_memory(
                                store,
                                best_match_id,
                                lock_conn,
                                user_id=user_id,
                            )
                            if not closed:
                                raise RuntimeError(
                                    "Supersede failed to close source memory in active state"
                                )
                    else:
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

                    superseded_id = _as_uuid_or_none(best_match.get("id"))
                    new_memory_id = _as_uuid_or_none(new_memory.get("id"))
                    if lock_conn is not None:
                        if superseded_id is not None and new_memory_id is not None:
                            result.deferred_supersede_effects.append(
                                DeferredSupersedeEffects(
                                    superseded_memory_id=superseded_id,
                                    new_memory_id=new_memory_id,
                                    existing_content=existing_content,
                                    new_content=fact.content,
                                )
                            )
                    else:
                        # Apply explicit negative trust signal for superseded memory.
                        try:
                            ts_module = _lazy_import_trust_signals()
                            if ts_module and superseded_id is not None:
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
                            conn=lock_conn,
                        )
                        await _close_active_family_memories(
                            store,
                            user_id,
                            fact_slot_family,
                            _as_uuid_or_none(new_id),
                            excluded_ids=closed_ids,
                            conn=lock_conn,
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
                    embedding_model=document_model,
                    source_conversation_id=conversation_id,
                    confidence=fact.confidence,
                    status=status,
                    memory_slot=fact_slot,
                    conn=lock_conn,
                )
                result.new.append(memory)

                if current_like_slot and fact_slot_family:
                    new_id = memory.get("id")
                    closed_ids = await _close_current_related_candidates(
                        store,
                        similar,
                        fact_slot_family,
                        _as_uuid_or_none(new_id),
                        conn=lock_conn,
                    )
                    await _close_active_family_memories(
                        store,
                        user_id,
                        fact_slot_family,
                        _as_uuid_or_none(new_id),
                        excluded_ids=closed_ids,
                        conn=lock_conn,
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
        executor = lock_conn if lock_conn is not None else store._pool
        await executor.execute(
            """
            UPDATE memories
            SET valid_to = NOW(),
                updated_at = NOW()
            WHERE user_id = $1
              AND status != 'deleted'
              AND tier != 'l0'
              AND source_type != 'dream'
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


async def prepare_memory_plan(
    store: MemoryStore,
    incoming: IncomingMemory,
    *,
    embedding_result: Any | None = None,
    excluded_memory_ids: set[uuid.UUID] | None = None,
) -> EquivalencePlan:
    """Discover and judge before acquiring any database transaction or lock."""
    if (
        incoming.local_only is not False
        or incoming.tier != "l1"
        or incoming.status != "active"
        or incoming.source_type == "dream"
    ):
        return EquivalencePlan(incoming)
    rows = await store._discover_equivalence_candidates(
        incoming.user_id,
        incoming.content,
        incoming.category,
        incoming.slot,
        embedding=_single_vector(embedding_result) if embedding_result is not None else None,
        embedding_model=embedding_result.storage_model if embedding_result is not None else None,
        excluded_memory_ids=excluded_memory_ids,
    )
    return await plan_equivalence(incoming, rows, excluded_memory_ids=excluded_memory_ids)


async def _commit_memory_plan(
    store: MemoryStore,
    plan: EquivalencePlan,
    *,
    conn: Any | None,
    embedding_result: Any | None,
    excluded_memory_ids: set[uuid.UUID] | None = None,
    locked_rows: dict[uuid.UUID, dict[str, Any]] | None = None,
    revalidate_only: bool = False,
) -> DedupResult:
    """Only same-connection DB work; no rejudge and no threshold/family fallback."""
    incoming = plan.incoming
    result = DedupResult()
    selected = plan.selected
    if selected is not None and selected.memory_id not in (excluded_memory_ids or ()):
        if conn is None:
            raise ValueError("semantic revalidation requires a transaction connection")
        rows = locked_rows
        if rows is None:
            rows = await store._lock_memory_rows(incoming.user_id, [selected.memory_id], conn=conn)
        row = rows.get(selected.memory_id)
        if (
            row is not None
            and eligible(row, incoming)
            and selected.matches(row)
            and may_merge_sources(incoming.source_type, row["source_type"])
        ):
            await store.touch_memory(selected.memory_id, conn=conn)
            result.merged.append(row)
            return result
    if revalidate_only:
        return result
    memory, inserted = await store._insert_memory_with_outcome(
        user_id=incoming.user_id,
        content=incoming.content,
        category=incoming.category,
        source_type=incoming.source_type,
        source_conversation_id=incoming.conversation_id,
        confidence=incoming.confidence,
        status=incoming.status,
        local_only=incoming.local_only,
        memory_slot=incoming.slot,
        embedding=_single_vector(embedding_result) if embedding_result is not None else None,
        embedding_model=embedding_result.storage_model if embedding_result is not None else None,
        conn=conn,
    )
    (result.new if inserted else result.merged).append(memory)
    return result


async def deduplicate_facts(
    store: MemoryStore,
    user_id: uuid.UUID,
    facts: list[Any],
    conversation_id: uuid.UUID | None,
    *,
    source_type: str = "extracted",
    status: str = "active",
    lock_conn: Any | None = None,
    prepared_embeddings: list[Any] | None = None,
    excluded_memory_ids: set[uuid.UUID] | None = None,
    prepared_plans: list[EquivalencePlan] | None = None,
    locked_rows: dict[uuid.UUID, dict[str, Any]] | None = None,
) -> DedupResult:
    """Sequential PLAN -> revalidate/COMMIT. Only equivalent verdicts merge.

    Two concurrent empty plans may both insert paraphrases (safe false negative).
    Exact hash uniqueness remains the existing database baseline exception.
    Locked legacy callers without a plan insert conservatively, with no network.
    """
    if DEDUP_BENCHMARK_MODE:
        if lock_conn is not None:
            raise ValueError("historical threshold benchmark cannot run inside a write lock")
        return await _deduplicate_facts_benchmark(
            store,
            user_id,
            facts,
            conversation_id,
            source_type=source_type,
            status=status,
            prepared_embeddings=prepared_embeddings,
            excluded_memory_ids=excluded_memory_ids,
        )
    if prepared_embeddings is not None and len(prepared_embeddings) != len(facts):
        raise ValueError("prepared_embeddings must match facts length")
    if prepared_plans is not None and len(prepared_plans) != len(facts):
        raise ValueError("prepared_plans must match facts length")
    result = DedupResult()
    for index, fact in enumerate(facts):
        if not str(getattr(fact, "content", "") or "").strip():
            logger.warning("deduplicate_facts skipped a blank fact at index %d", index)
            continue
        if conversation_id is not None:
            from orchestrator.memory.embedding import get_selected_embedding_route_id

            if get_selected_embedding_route_id():
                source = await store.get_conversation(conversation_id)
                if (
                    not source
                    or source.get("user_id") != user_id
                    or source.get("pipeline") != "cloud"
                ):
                    raise EmbeddingConfigurationError(
                        "Local or unknown source cannot be cloud processed"
                    )
        incoming = IncomingMemory(
            user_id=user_id,
            content=fact.content,
            category=fact.category,
            source_type=source_type,
            conversation_id=conversation_id,
            slot=getattr(fact, "slot", None),
            confidence=getattr(fact, "confidence", 0.8),
            status=status,
            local_only=getattr(fact, "local_only", False),
        )
        embedding_result = prepared_embeddings[index] if prepared_embeddings is not None else None
        if type(incoming.local_only) is not bool:
            raise ValueError("Memory locality must be known before processing")
        if incoming.local_only is not False:
            embedding_result = None
        if prepared_embeddings is None and lock_conn is None and incoming.local_only is False:
            try:
                embedding_result = await prepare_memory_embedding(incoming.content, incoming.slot)
            except EmbeddingConfigurationError:
                embedding_result = None
        if prepared_plans is not None:
            plan = prepared_plans[index]
            if plan.incoming != incoming:
                raise ValueError("prepared plan does not match immutable incoming memory")
        elif lock_conn is not None:
            plan = EquivalencePlan(incoming)
        else:
            plan = await prepare_memory_plan(
                store,
                incoming,
                embedding_result=embedding_result,
                excluded_memory_ids=excluded_memory_ids,
            )
        if lock_conn is not None:
            committed = await _commit_memory_plan(
                store,
                plan,
                conn=lock_conn,
                embedding_result=embedding_result,
                excluded_memory_ids=excluded_memory_ids,
                locked_rows=locked_rows,
            )
        else:
            # Unlocked callers own a short transaction only for row-locked
            # revalidation/touch. Conservative inserts use the store's existing
            # atomic INSERT/exact-conflict path after that transaction ends.
            committed = DedupResult()
            if plan.selected is not None:
                async with store._pool.acquire() as conn:
                    async with conn.transaction():
                        committed = await _commit_memory_plan(
                            store,
                            plan,
                            conn=conn,
                            embedding_result=embedding_result,
                            excluded_memory_ids=excluded_memory_ids,
                            revalidate_only=True,
                        )
            if not committed.merged:
                committed = await _commit_memory_plan(
                    store,
                    EquivalencePlan(incoming),
                    conn=None,
                    embedding_result=embedding_result,
                )
            # Each fact finishes persistence before the next fact's plan.
        result.new.extend(committed.new)
        result.merged.extend(committed.merged)
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
    lock_conn: Any | None = None,
    embedding_result: Any | None = None,
    excluded_memory_ids: set[uuid.UUID] | None = None,
    deferred_supersede_effects: list[DeferredSupersedeEffects] | None = None,
    prepared_plan: EquivalencePlan | None = None,
    locked_rows: dict[uuid.UUID, dict[str, Any]] | None = None,
) -> uuid.UUID:
    """Store a single memory with deduplication.

    Returns the memory ID (existing if merged/superseded, new if created).

    ``lock_conn`` (issue #221): when supplied, the caller has already
    acquired the per-user active-row cap advisory lock on this connection.
    Provider work must already have completed in ``prepared_plan`` and the
    prepared embedding. The commit reuses this connection without acquiring
    the pool. A locked caller without a plan conservatively inserts/reuses
    the exact database duplicate; it never judges or falls through to legacy
    thresholds. ``deferred_supersede_effects`` retains benchmark compatibility.
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
        lock_conn=lock_conn,
        prepared_embeddings=[embedding_result] if embedding_result is not None else None,
        excluded_memory_ids=excluded_memory_ids,
        prepared_plans=[prepared_plan] if prepared_plan is not None else None,
        locked_rows=locked_rows,
    )
    if deferred_supersede_effects is not None:
        deferred_supersede_effects.extend(result.deferred_supersede_effects)
    elif result.deferred_supersede_effects:
        raise RuntimeError("cap-locked dedup requires a deferred effects collector")

    if result.merged:
        return result.merged[0]["id"]
    elif result.superseded:
        return result.superseded[0]["id"]
    elif result.new:
        return result.new[0]["id"]
    else:
        raise ValueError("memory content must not be blank")


async def prepare_memory_embedding(content: str, slot: str | None = None) -> Any:
    """Compute a write embedding before opening a cap transaction."""
    if not content or not content.strip():
        raise ValueError("memory content must not be blank")
    result = await embed_documents_with_metadata([_embedding_text(content, slot)])
    _single_vector(result)  # fail before any lock if the provider returned none
    return result
