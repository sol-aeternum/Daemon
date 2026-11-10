from __future__ import annotations

import asyncio
import re
import uuid
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from time import monotonic
from typing import Any, cast
from collections.abc import Mapping

from orchestrator.guardrails import strip_reasoning_fields_from_message
from orchestrator.config import get_settings
from orchestrator.memory import provenance
from orchestrator.memory.embedding import (
    EmbeddingConfigurationError,
    EmbeddingVectorResult,
    embed_query_with_metadata,
    embed_queries_with_metadata,
    get_selected_embedding_route_id,
    raise_if_embedding_accounting_error,
)
from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.retrieval import RECENCY_REFERENCE_TIME
from orchestrator.memory.retrieval import _RETRIEVAL_LOG_BUFFER, _flush_retrieval_logs
from orchestrator.memory.multi_query import (
    SubqueryProvider,
    ProductionSubqueryProvider,
    validate_subqueries,
    merge_memories,
    MAX_INPUT_CHARS,
    SUBQUERY_DEPTH,
)
from orchestrator.memory.store import MemoryStore

# Dynamic import for trust signals to avoid circular imports
_trust_signals = None
logger = logging.getLogger(__name__)


def _lazy_import_trust_signals():
    global _trust_signals
    if _trust_signals is None:
        import importlib

        try:
            _trust_signals = importlib.import_module("orchestrator.memory.trust_signals")
        except ImportError:
            pass
    return _trust_signals


from orchestrator.prompts import DAEMON_SYSTEM_PROMPT  # noqa: E402

MAX_MEMORY_ITEMS = 5
MAX_SUMMARY_ITEMS = 3
DEFAULT_MAX_TOKENS = 2500
MAX_SINGLE_MEMORY_CHARS = 400
L0_TOKEN_BUDGET = 200
MAX_L0_CHARS = 600
# One post-decomposition budget for the entire added branch, not per arm.
COMPONENT_RETRIEVAL_TIMEOUT_SECONDS = 8.0

PERSONALITY_PRESETS: dict[str, str] = {
    "default": "",
    "friendly": "Be warm and friendly while still being clear and useful.",
    "efficient": "Be extremely concise. Focus on getting things done quickly and clearly.",
    "mentor": "Act as a patient mentor. Teach clearly and guide reasoning step by step.",
    "professional": "Respond in a formal, structured manner. Prioritize clarity and precision.",
    "candid": "Be direct and honest. Prioritize truth over comfort.",
    "technical": "Assume high technical literacy and provide implementation-level detail.",
    "minimal": "Respond with the minimum words necessary.",
}

CHARACTERISTIC_MODIFIERS: dict[str, dict[str, str]] = {
    "warmth": {
        "less": "Keep tone neutral and direct.",
        "default": "Use a balanced level of warmth.",
        "more": "Be warm and personable.",
        "low": "Keep tone neutral and direct.",
        "normal": "Use a balanced level of warmth.",
        "medium": "Use a balanced level of warmth.",
        "high": "Be warm and personable.",
    },
    "emoji": {
        "less": "Avoid emoji.",
        "default": "Use emoji sparingly when they improve clarity.",
        "more": "Use emoji where they add clarity or tone.",
        "low": "Avoid emoji.",
        "normal": "Use emoji sparingly when they improve clarity.",
        "medium": "Use emoji sparingly when they improve clarity.",
        "high": "Use emoji where they add clarity or tone.",
    },
    "enthusiasm": {
        "less": "Keep tone calm and measured.",
        "default": "Use a balanced, neutral level of enthusiasm.",
        "more": "Use energetic and encouraging tone.",
        "low": "Keep tone calm and measured.",
        "normal": "Use a balanced, neutral level of enthusiasm.",
        "medium": "Use a balanced, neutral level of enthusiasm.",
        "high": "Use energetic and encouraging tone.",
    },
    "formatting": {
        "less": "Prefer plain text and minimal formatting.",
        "default": "Use standard formatting for clarity.",
        "more": "Use clear structure with headings and bullets when helpful.",
        "low": "Prefer plain text and minimal formatting.",
        "normal": "Use standard formatting for clarity.",
        "medium": "Use standard formatting for clarity.",
        "high": "Use clear structure with headings and bullets when helpful.",
    },
}


def _normalize_content(value: object | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _truncate_to_chars(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _format_l0_block(l0_memories: list[dict[str, object]]) -> str:
    if not l0_memories:
        return ""
    lines = ["[FROZEN MEMORIES]"]
    for memory in l0_memories:
        text = _truncate_to_chars(_normalize_content(memory.get("content")), MAX_L0_CHARS)
        if text:
            lines.append(f"- {text}{provenance.noted_suffix(memory)}")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    words = text.split()
    if not words:
        return 0
    total_chars = len(text)
    average_word_length = total_chars / len(words)
    if average_word_length > 4:
        return max(1, total_chars // 3)
    return max(1, int(len(words) * 1.3))


def format_preferences_block(preferences: dict[str, object]) -> str:
    raw = preferences
    nested_preferences = preferences.get("preferences")
    if isinstance(nested_preferences, dict):
        normalized_nested: dict[str, object] = {}
        for key, value in cast(dict[object, object], nested_preferences).items():
            if isinstance(key, str):
                normalized_nested[key] = value
        raw = normalized_nested

    personality = str(raw.get("personality") or "default").strip().lower()
    characteristics_obj = raw.get("characteristics")
    custom_instructions = _normalize_content(raw.get("custom_instructions"))

    lines: list[str] = []
    preset = PERSONALITY_PRESETS.get(personality, "")
    if preset:
        lines.append(f"- Personality: {preset}")

    if isinstance(characteristics_obj, dict):
        characteristics: dict[str, object] = {}
        for key, value in cast(dict[object, object], characteristics_obj).items():
            if isinstance(key, str):
                characteristics[key] = value

        for axis in ("warmth", "enthusiasm", "emoji", "formatting"):
            raw_value = str(characteristics.get(axis) or "").strip().lower()
            modifier = CHARACTERISTIC_MODIFIERS.get(axis, {}).get(raw_value, "")
            if modifier:
                lines.append(f"- {axis.title()}: {modifier}")

    if custom_instructions:
        lines.append(f"- Custom Instructions: {custom_instructions}")

    if not lines:
        return ""

    return "Communication preferences:\n" + "\n".join(lines)


@dataclass(frozen=True, slots=True)
class MemoryContextInputs:
    """Everything ``render_memory_context`` needs, already gathered and filtered.

    ``retrieval_mode`` is gather metadata for callers that report it (the
    LongMemEval harness); rendering ignores it.
    """

    l0_memories: list[dict[str, object]] = field(default_factory=list)
    retrieved: list[dict[str, object]] = field(default_factory=list)
    summaries: list[dict[str, object]] = field(default_factory=list)
    retrieval_mode: str = "none"
    retrieved_limit: int | None = None
    added_memory_ids: tuple[uuid.UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.retrieved_limit is not None and (
            type(self.retrieved_limit) is not int or self.retrieved_limit < MAX_MEMORY_ITEMS
        ):
            raise ValueError(f"retrieved_limit must be None or an integer >= {MAX_MEMORY_ITEMS}")


def retrieved_width(inputs: MemoryContextInputs) -> int:
    return inputs.retrieved_limit or MAX_MEMORY_ITEMS


def _section_counts(block: str) -> tuple[int, int]:
    """Count rendered (retrieved, frozen) rows per production section."""
    section = ""
    retrieved_count = 0
    frozen_count = 0
    for line in block.splitlines():
        if line == "[FROZEN MEMORIES]":
            section = "frozen"
        elif line == "About this user:":
            section = "retrieved"
        elif line == "Recent context:":
            section = "summary"
        elif line.startswith("- "):
            if section == "frozen":
                frozen_count += 1
            elif section == "retrieved" and re.match(
                r"^- (?:Fact|Project|Preference|Important): ", line
            ):
                retrieved_count += 1
    return retrieved_count, frozen_count


def _eligible_l0(inputs: MemoryContextInputs) -> list[dict[str, object]]:
    """Frozen rows the renderer would print, excluding normalized-empty rows."""
    return [
        memory
        for memory in inputs.l0_memories
        if _truncate_to_chars(_normalize_content(memory.get("content")), MAX_L0_CHARS)
    ]


def _expected_retained_l0(inputs: MemoryContextInputs, eligible: int) -> int:
    """Replay the renderer's independent L0 line-count budget, not its output text."""
    block = _format_l0_block(inputs.l0_memories)
    if not block:
        return 0
    lines = block.split("\n")
    if len(lines) - 1 != eligible:
        raise ValueError("Renderer section contract changed")
    while estimate_tokens("\n".join(lines)) > L0_TOKEN_BUDGET and len(lines) > 1:
        lines.pop()
    return len(lines) - 1


def rendered_memory_rows(
    inputs: MemoryContextInputs, block: str, *, require_complete: bool = False
) -> list[dict[str, object]]:
    """Map an actual rendered block to its retained row prefixes, including L0.

    Normalized content occupies a single line. Counting section lines avoids
    substring matching, so duplicate content cannot misattribute IDs. Shared by
    production's post-render effects and the read-only exposure diagnostics.
    """
    retrieved_count, frozen_count = _section_counts(block)
    eligible = [
        memory
        for memory in inputs.retrieved[: retrieved_width(inputs)]
        if _truncate_to_chars(_normalize_content(memory.get("content")), MAX_SINGLE_MEMORY_CHARS)
    ]
    eligible_l0 = _eligible_l0(inputs)
    expected_frozen = len(eligible_l0)
    if require_complete:
        expected_frozen = _expected_retained_l0(inputs, len(eligible_l0))
    if (
        retrieved_count > len(eligible)
        or frozen_count > len(eligible_l0)
        or (
            require_complete
            and (retrieved_count != len(eligible) or frozen_count != expected_frozen)
        )
    ):
        raise ValueError("Renderer section contract changed")
    return [*eligible_l0[:frozen_count], *eligible[:retrieved_count]]


def _derive_query_text(recent_messages: list[dict[str, Any]]) -> str:
    query_text = ""
    for message in reversed(recent_messages):
        if str(message.get("role") or "").lower() == "user":
            query_text = _normalize_content(message.get("content"))
            if query_text:
                break

    if not query_text and recent_messages:
        query_text = _normalize_content(recent_messages[-1].get("content"))
    return query_text


def _in_scope(row: dict[str, object], allowed: frozenset[uuid.UUID] | None) -> bool:
    return allowed is None or row.get("source_conversation_id") in allowed


async def gather_memory_context_inputs(
    store: MemoryStore,
    *,
    user_id: uuid.UUID,
    pipeline: object,
    conversation_id: uuid.UUID | None = None,
    query_text: str | None = None,
    allowed_source_conversation_ids: list[uuid.UUID] | None = None,
    record_side_effects: bool = True,
    log_retrieval: bool = False,
    retrieval_triggered_by: str | None = None,
    query_reference_time: datetime | None = None,
    subquery_provider: SubqueryProvider | None = None,
    evaluation: bool = False,
    evaluation_merge: str | None = None,
    evaluation_embeddings: Mapping[str, EmbeddingVectorResult] | None = None,
) -> MemoryContextInputs:
    """Preserve legacy baseline effects; defer only added-row effects to rendering."""
    kwargs: dict[str, Any] = dict(
        user_id=user_id,
        pipeline=pipeline,
        conversation_id=conversation_id,
        query_text=query_text,
        allowed_source_conversation_ids=allowed_source_conversation_ids,
        record_side_effects=record_side_effects,
        log_retrieval=log_retrieval,
        retrieval_triggered_by=retrieval_triggered_by,
        query_reference_time=query_reference_time,
    )
    settings = get_settings()
    if evaluation and subquery_provider is None:
        raise ValueError("Evaluation requires pinned subqueries")
    if not evaluation and (evaluation_merge is not None or evaluation_embeddings is not None):
        raise ValueError("Pinned evaluation controls are evaluation-only")
    if not settings.memory_multi_query_enabled and not evaluation:
        return await _gather_single_query_inputs(store, **kwargs)
    merge_mode = evaluation_merge or settings.memory_multi_query_merge
    if merge_mode not in {"additive", "round_robin"}:
        raise ValueError("Invalid memory merge configuration")
    # This guard is before task creation or any new outbound work.
    if merge_mode == "round_robin" and not evaluation:
        raise ValueError("Round-robin memory merge is evaluation-only")
    if query_text is None:
        messages = (
            await store.get_recent_messages(conversation_id, limit=20)
            if conversation_id is not None
            else []
        )
        messages = [strip_reasoning_fields_from_message(m) for m in messages]
        baseline_query = _derive_query_text(messages)
        latest_user = next(
            (
                _normalize_content(m.get("content"))
                for m in reversed(messages)
                if str(m.get("role") or "").lower() == "user"
                and _normalize_content(m.get("content"))
            ),
            "",
        )
    else:
        baseline_query = latest_user = _normalize_content(query_text)
    pinned_query = None
    if evaluation_embeddings is not None and baseline_query:
        pinned_query = evaluation_embeddings.get(baseline_query)
        if pinned_query is None:
            raise ValueError("Uncached evaluation query refused")
    reference = query_reference_time or RECENCY_REFERENCE_TIME.get() or datetime.now(timezone.utc)
    reference = (
        reference.replace(tzinfo=timezone.utc)
        if reference.tzinfo is None
        else reference.astimezone(timezone.utc)
    )
    tasks: list[asyncio.Task[Any]] = []
    try:
        baseline_task = asyncio.create_task(
            _gather_single_query_inputs(
                store,
                **kwargs,
                owned_tasks=tasks,
                evaluation_embedding=pinned_query,
            )
        )
        tasks.append(baseline_task)
        components_task = None
        if pipeline == "cloud" and latest_user and settings.memory_max_subqueries > 0:
            deadline = monotonic() + settings.memory_decomposition_timeout_ms / 1000
            components_task = asyncio.create_task(
                _component_branch(
                    store,
                    subquery_provider or ProductionSubqueryProvider(),
                    latest_user,
                    reference,
                    deadline,
                    settings.memory_max_subqueries,
                    user_id=user_id,
                    allowed_source_conversation_ids=allowed_source_conversation_ids,
                    log_retrieval=log_retrieval,
                    retrieval_triggered_by=retrieval_triggered_by,
                    evaluation_embeddings=evaluation_embeddings,
                )
            )
            tasks.append(components_task)
        # Await both arms together so an accounting failure cancels/drains the other.
        inputs: MemoryContextInputs
        component_result = None
        if components_task is not None:
            inputs, component_result = await asyncio.gather(baseline_task, components_task)
        else:
            inputs = await baseline_task
        if component_result is not None:
            arms, buffers = component_result
            try:
                merged = merge_memories(
                    inputs.retrieved,
                    arms,
                    quota=settings.memory_subquery_quota,
                    mode=merge_mode,
                    evaluation=evaluation,
                )
                existing = {str(row.get("id")) for row in inputs.retrieved}
                added_ids = tuple(
                    row["id"]
                    for row in merged
                    if isinstance(row.get("id"), uuid.UUID) and str(row["id"]) not in existing
                )
                if added_ids:
                    inputs = replace(
                        inputs,
                        retrieved=merged,
                        retrieved_limit=len(merged) if len(merged) > MAX_MEMORY_ITEMS else None,
                        added_memory_ids=added_ids,
                    )
            except Exception as error:
                raise_if_embedding_accounting_error(error)
                logger.info("Memory multi-query merge failed; retaining baseline")
                return inputs
            # No detached component writes. Flush in input-arm order only
            # after a successful additive merge with additions (never fallback).
            if added_ids and merge_mode == "additive":
                await _flush_retrieval_logs(store, [row for buffer in buffers for row in buffer])
        return inputs
    finally:
        await _cancel_and_drain(tasks)


async def _cancel_and_drain(tasks: list[asyncio.Task[Any]]) -> None:
    # Cancel owners before their children; do not re-cancel settlement cleanup.
    for task in reversed(tasks):
        if not task.done() and not task.cancelling():
            task.cancel()
    drained = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not drained.done():
        try:
            await asyncio.shield(drained)
        except asyncio.CancelledError:
            cancelled = True
    outcomes = drained.result()
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise_if_embedding_accounting_error(outcome)
    if cancelled:
        raise asyncio.CancelledError


async def _before_component_deadline(task: asyncio.Task[Any], deadline: float) -> Any:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    # Explicit ownership lets cleanup inspect accounting failures even when a
    # timeout/cancellation races with a task's settlement cleanup.
    return await asyncio.wait_for(asyncio.shield(task), remaining)


async def _component_branch(
    store: MemoryStore,
    provider: SubqueryProvider,
    message: str,
    reference: datetime,
    decomposition_deadline: float,
    maximum: int,
    *,
    user_id: uuid.UUID,
    allowed_source_conversation_ids: list[uuid.UUID] | None,
    log_retrieval: bool,
    retrieval_triggered_by: str | None,
    evaluation_embeddings: Mapping[str, EmbeddingVectorResult] | None,
) -> tuple[list[list[dict[str, object]]], list[list[dict[str, Any]]]] | None:
    tasks: list[asyncio.Task[Any]] = []
    clock_token = RECENCY_REFERENCE_TIME.set(reference)
    try:
        components = await _decompose_components(
            provider, message, reference, decomposition_deadline, maximum
        )
        if not components:
            return None
        deadline = monotonic() + COMPONENT_RETRIEVAL_TIMEOUT_SECONDS
        started = monotonic()
        try:
            if evaluation_embeddings is not None:
                vectors = [evaluation_embeddings[text] for text in components]
            else:
                embedding_task = asyncio.create_task(embed_queries_with_metadata(components))
                tasks.append(embedding_task)
                vectors = await _before_component_deadline(embedding_task, deadline)
        finally:
            _phase_latency("embedding", started)
        if len(vectors) != len(components):
            raise ValueError("Component embedding count mismatch")
        buffers: list[list[dict[str, Any]]] = [[] for _ in components]

        async def retrieve(index: int) -> list[dict[str, object]]:
            token = _RETRIEVAL_LOG_BUFFER.set(buffers[index])
            try:
                vector = vectors[index]
                return await retrieve_memories_for_text(
                    store=store,
                    query_text=components[index],
                    user_id=user_id,
                    query_embedding=vector.embedding,
                    limit=SUBQUERY_DEPTH,
                    include_local=False,
                    storage_embedding_model=vector.storage_model,
                    query_embedding_model=vector.model,
                    allow_query_embedding=False,
                    touch_results=False,
                    record_query_text=False,
                    query_role=f"sub:{index}",
                    allowed_source_conversation_ids=allowed_source_conversation_ids,
                    log_retrieval=log_retrieval,
                    retrieval_triggered_by=retrieval_triggered_by,
                    query_reference_time=reference,
                )
            finally:
                _RETRIEVAL_LOG_BUFFER.reset(token)

        started = monotonic()
        try:
            arms = [asyncio.create_task(retrieve(i)) for i in range(len(components))]
            tasks.extend(arms)

            async def collect() -> list[list[dict[str, object]]]:
                return await asyncio.gather(*arms)

            collector = asyncio.create_task(collect())
            tasks.append(collector)
            results = await _before_component_deadline(collector, deadline)
            return results, buffers
        finally:
            _phase_latency("sub_retrieval", started)
    except Exception as error:
        raise_if_embedding_accounting_error(error)
        logger.info("Memory multi-query branch failed; retaining baseline")
        return None
    finally:
        try:
            await _cancel_and_drain(tasks)
        finally:
            RECENCY_REFERENCE_TIME.reset(clock_token)


def _phase_latency(phase: str, started: float) -> None:
    logger.info(
        "Memory multi-query phase",
        extra={"phase": phase, "latency_ms": max(0, (monotonic() - started) * 1000)},
    )


async def _wait_for_components(
    provider: SubqueryProvider, message: str, reference: datetime, deadline: float
) -> list[str]:
    remaining = deadline - monotonic()
    if remaining <= 0:
        return []
    return await asyncio.wait_for(
        provider.get_subqueries(
            message[:MAX_INPUT_CHARS], reference_time=reference, deadline=deadline
        ),
        timeout=remaining,
    )


async def _decompose_components(
    provider: SubqueryProvider, message: str, reference: datetime, deadline: float, maximum: int
) -> list[str]:
    started = monotonic()
    try:
        values = await _wait_for_components(provider, message, reference, deadline)
        return validate_subqueries(values, message, max_subqueries=maximum)
    except Exception as error:
        raise_if_embedding_accounting_error(error)
        logger.info("Memory decomposition unavailable; retaining baseline")
        return []
    finally:
        _phase_latency("decomposition", started)


async def _record_rendered_effects(
    store: MemoryStore,
    inputs: MemoryContextInputs,
    conversation_id: uuid.UUID | None,
    block: str,
    *,
    empty_baseline_snapshot: object = None,
) -> None:
    if not inputs.added_memory_ids:
        return
    try:
        rows = rendered_memory_rows(inputs, block)
        ids = list(
            dict.fromkeys(row["id"] for row in rows if row.get("id") in inputs.added_memory_ids)
        )
    except Exception as error:
        raise_if_embedding_accounting_error(error)
        logger.warning("Memory multi-query rendered-row mapping failed")
        return
    if not ids:
        return
    try:
        await store.bulk_touch_memories(cast(list[uuid.UUID], ids))
    except Exception as error:
        raise_if_embedding_accounting_error(error)
        logger.warning("Memory multi-query access update failed")
    if conversation_id is not None:
        try:
            trust = _lazy_import_trust_signals()
            if trust:
                await trust._record_added_retrieved_memories(
                    conversation_id=conversation_id,
                    baseline_ids=[
                        row["id"]
                        for row in inputs.retrieved
                        if row.get("id") and row.get("id") not in inputs.added_memory_ids
                    ],
                    added_ids=ids,
                    store=store,
                    empty_baseline_snapshot=empty_baseline_snapshot,
                )
        except Exception as error:
            raise_if_embedding_accounting_error(error)
            logger.warning("Memory multi-query trust update failed")


async def _gather_single_query_inputs(
    store: MemoryStore,
    *,
    user_id: uuid.UUID,
    pipeline: object,
    conversation_id: uuid.UUID | None = None,
    query_text: str | None = None,
    allowed_source_conversation_ids: list[uuid.UUID] | None = None,
    record_side_effects: bool = True,
    log_retrieval: bool = False,
    retrieval_triggered_by: str | None = None,
    query_reference_time: datetime | None = None,
    query_role: str | None = None,
    owned_tasks: list[asyncio.Task[Any]] | None = None,
    evaluation_embedding: EmbeddingVectorResult | None = None,
) -> MemoryContextInputs:
    """Read L0 memories, retrieved memories and summaries for one prompt.

    Production passes ``conversation_id`` and lets the query come from the
    conversation's recent messages. The LongMemEval harness passes an explicit
    ``query_text``, scopes every source to the question's haystack with
    ``allowed_source_conversation_ids`` and disables ``record_side_effects``
    (trust recording and memory access touches) so evaluation never changes
    ranking state. Only its historical-clock mode passes
    ``query_reference_time``, the "now" for temporal query windows; production
    leaves it unset. The production defaults are unchanged.
    """
    include_local = str(pipeline or "").strip().lower() == "local"
    allowed = (
        frozenset(allowed_source_conversation_ids)
        if allowed_source_conversation_ids is not None
        else None
    )

    l0_memories = await store.get_l0_memories(user_id, include_local=include_local)
    if not include_local:
        l0_memories = [row for row in l0_memories if row.get("local_only") is False]
    l0_memories = [row for row in l0_memories if _in_scope(row, allowed)]

    if query_text is None:
        recent_messages: list[dict[str, Any]] = []
        if conversation_id is not None:
            recent_messages = await store.get_recent_messages(conversation_id, limit=20)
        recent_messages = [strip_reasoning_fields_from_message(m) for m in recent_messages]
        query_text = _derive_query_text(recent_messages)
    else:
        query_text = _normalize_content(query_text)

    retrieved: list[dict[str, object]] = []
    retrieval_mode = "none"
    summaries_task = asyncio.create_task(
        store.get_recent_summaries(user_id, limit=MAX_SUMMARY_ITEMS, include_local=include_local)
    )
    if owned_tasks is not None:
        owned_tasks.append(summaries_task)

    if query_text:
        try:
            try:
                if get_selected_embedding_route_id() and pipeline != "cloud":
                    raise EmbeddingConfigurationError(
                        "Local or unknown-locality conversation cannot be cloud embedded"
                    )
                if evaluation_embedding is not None:
                    query_result = evaluation_embedding
                else:
                    query_result = await asyncio.wait_for(
                        embed_query_with_metadata(query_text), timeout=8.0
                    )
            except EmbeddingConfigurationError:
                query_result = None
            retrieval_mode = (
                f"embedding:{query_result.storage_model}" if query_result is not None else "lexical"
            )
            retrieval_kwargs: dict[str, Any] = {}
            if query_role is not None:
                retrieval_kwargs["query_role"] = query_role
            if evaluation_embedding is not None:
                retrieval_kwargs["allow_query_embedding"] = False
            if not record_side_effects:
                retrieval_kwargs["touch_results"] = False
            if allowed_source_conversation_ids is not None:
                retrieval_kwargs["allowed_source_conversation_ids"] = (
                    allowed_source_conversation_ids
                )
            if log_retrieval:
                retrieval_kwargs["log_retrieval"] = True
            if retrieval_triggered_by is not None:
                retrieval_kwargs["retrieval_triggered_by"] = retrieval_triggered_by
            if query_reference_time is not None:
                retrieval_kwargs["query_reference_time"] = query_reference_time
            retrieved = await retrieve_memories_for_text(
                store=store,
                query_text=query_text,
                user_id=user_id,
                query_embedding=query_result.embedding if query_result is not None else None,
                limit=MAX_MEMORY_ITEMS,
                include_local=include_local,
                storage_embedding_model=query_result.storage_model
                if query_result is not None
                else None,
                query_embedding_model=query_result.model if query_result is not None else None,
                **retrieval_kwargs,
            )

            # Record retrieved memory IDs for trust signal tracking
            if retrieved and record_side_effects and conversation_id is not None:
                try:
                    ts_module = _lazy_import_trust_signals()
                    if ts_module:
                        memory_ids = [m.get("id") for m in retrieved if m.get("id")]
                        await ts_module.record_retrieved_memories(
                            conversation_id=conversation_id,
                            memory_ids=memory_ids,
                            store=store,
                        )
                except Exception:
                    pass  # Trust signals are best-effort

        except Exception as error:
            raise_if_embedding_accounting_error(error)
            retrieved = []
            retrieval_mode = "failed"

    summaries = await summaries_task
    if not include_local:
        summaries = [row for row in summaries if row.get("local_only") is False]
    summaries = [row for row in summaries if _in_scope(row, allowed)]

    return MemoryContextInputs(
        l0_memories=l0_memories,
        retrieved=retrieved,
        summaries=summaries,
        retrieval_mode=retrieval_mode,
    )


def render_memory_context(
    inputs: MemoryContextInputs,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Render gathered inputs into the fenced memory block. Pure and deterministic.

    This is the single memory prompt formatter: production chat, durable task
    turns and the LongMemEval harness all call it.
    """
    l0_block = _format_l0_block(inputs.l0_memories)

    if estimate_tokens(l0_block) > L0_TOKEN_BUDGET:
        l0_lines = l0_block.split("\n")
        while estimate_tokens("\n".join(l0_lines)) > L0_TOKEN_BUDGET and len(l0_lines) > 1:
            l0_lines.pop()
        l0_block = "\n".join(l0_lines)

    memory_lines: list[str] = []
    memory_suffixes: list[str] = []
    for memory in inputs.retrieved[: retrieved_width(inputs)]:
        category = str(memory.get("category") or "fact").strip().lower()
        label = (
            category.title()
            if category in {"fact", "project", "preference", "important"}
            else "Fact"
        )
        text = _truncate_to_chars(
            _normalize_content(memory.get("content")), MAX_SINGLE_MEMORY_CHARS
        )
        if text:
            memory_lines.append(f"- {label}: {text}")
            memory_suffixes.append(provenance.noted_suffix(memory))

    summary_lines: list[str] = []
    for summary in inputs.summaries[:MAX_SUMMARY_ITEMS]:
        text = _normalize_content(summary.get("content"))
        if text:
            summary_lines.append(f"- Session: {text}")

    effective_token_budget = max(1, max_tokens)

    def render(memories: list[str], summary_items: list[str]) -> str:
        parts: list[str] = []
        if memories:
            parts.append("About this user:")
            parts.extend(provenance.header_lines())
            parts.extend(memories)
        if summary_items:
            parts.append("Recent context:")
            parts.extend(summary_items)
        return "\n".join(parts).strip()

    def truncate_items(items: list[str], item_limit: int) -> list[str]:
        output: list[str] = []
        for line in items:
            if len(line) <= item_limit:
                output.append(line)
                continue
            output.append(_truncate_to_chars(line, item_limit))
        return output

    memory_lines = truncate_items(memory_lines, MAX_SINGLE_MEMORY_CHARS)
    # Dates go on after truncation so a long memory can't cut them off; with the
    # dormant flag off every suffix is "" and the output is unchanged.
    memory_lines = [
        line + suffix for line, suffix in zip(memory_lines, memory_suffixes, strict=True)
    ]
    summary_lines = truncate_items(summary_lines, MAX_SINGLE_MEMORY_CHARS)

    context = render(memory_lines, summary_lines)

    while estimate_tokens(context) > effective_token_budget and memory_lines:
        _ = memory_lines.pop()
        context = render(memory_lines, summary_lines)

    while estimate_tokens(context) > effective_token_budget and summary_lines:
        _ = summary_lines.pop()
        context = render(memory_lines, summary_lines)

    if not l0_block and not memory_lines and not summary_lines:
        return ""

    if l0_block and not context:
        raw_block = l0_block
    elif l0_block:
        raw_block = l0_block + "\n\n" + context
    else:
        raw_block = context

    # Wrap the entire memory block in an explicit XML fence (defense in depth against
    # prompt injection via planted memory content; see issue #19). The system prompt
    # at orchestrator/prompts.py explicitly disclaims instructions found inside.
    # Neutralize anything shaped like a closing tag — including XML-valid
    # whitespace variants such as "</memory_records >" — so planted content
    # cannot break out of the fence.
    raw_block = re.sub(
        r"<\s*/\s*memory_records\s*>",
        "&lt;/memory_records&gt;",
        raw_block,
        flags=re.IGNORECASE,
    )
    return f'<memory_records trust="user_data">\n{raw_block}\n</memory_records>'


async def build_memory_context(
    store: MemoryStore,
    conversation_id: uuid.UUID,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    *,
    temporal_scope=None,
    query_text: str | None = None,
) -> str:
    from orchestrator.memory.revisions import read_scope, resolve
    from orchestrator.memory.temporal import HISTORICAL_CONTEXT_NOTE, TemporalRefusal

    if temporal_scope is None:
        return await _build_current_memory_context(store, conversation_id, max_tokens)
    conversation = await store.get_conversation(conversation_id)
    if not conversation or not isinstance(conversation.get("user_id"), uuid.UUID):
        raise TemporalRefusal("historical_context_owner_unavailable")
    scope = await resolve(store, conversation["user_id"], temporal_scope)
    async with read_scope(scope):
        inputs = await _gather_single_query_inputs(
            store,
            user_id=conversation["user_id"],
            pipeline=conversation.get("pipeline"),
            query_text=query_text or "",
            record_side_effects=False,
        )
        return HISTORICAL_CONTEXT_NOTE + "\n" + render_memory_context(inputs, max_tokens)


async def _build_current_memory_context(
    store: MemoryStore,
    conversation_id: uuid.UUID,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    conversation = await store.get_conversation(conversation_id)
    if not conversation:
        return ""

    user_id = conversation.get("user_id")
    if not isinstance(user_id, uuid.UUID):
        return ""

    pipeline = conversation.get("pipeline")
    if get_selected_embedding_route_id() and pipeline not in ("cloud", "local"):
        return ""  # Unknown source permissions cannot authorize context processing.

    enabled = get_settings().memory_multi_query_enabled
    # Capture from the existing owner-qualified read, before gather can yield.
    # Copy decoded lists so later changes to that read result cannot alter CAS.
    snapshot = conversation.get("last_retrieved_memory_ids")
    if isinstance(snapshot, list):
        snapshot = list(snapshot)
    inputs = await gather_memory_context_inputs(
        store,
        user_id=user_id,
        pipeline=pipeline,
        conversation_id=conversation_id,
    )
    block = render_memory_context(inputs, max_tokens)
    if enabled:
        await _record_rendered_effects(
            store, inputs, conversation_id, block, empty_baseline_snapshot=snapshot
        )
    return block


def compose_system_prompt(
    memory_context: str,
    preferences_block: str | None = None,
) -> str:
    parts = [DAEMON_SYSTEM_PROMPT.strip()]

    prefs = (preferences_block or "").strip()
    if prefs:
        parts.append(prefs)

    memory_block = memory_context.strip()
    if memory_block:
        parts.append(memory_block)

    assembled = "\n\n".join(part for part in parts if part)
    if "memory tools" not in assembled.lower():
        assembled = (
            assembled
            + "\n\n"
            + "You have access to memory tools for reading and writing durable user and project context."
        )

    return assembled


async def assemble_system_prompt(
    memory_context: str,
    preferences_block: str | None = None,
    conversation_id: uuid.UUID | None = None,
) -> str:
    del conversation_id
    return compose_system_prompt(memory_context, preferences_block)


# Re-export get_l0_memories for backward compatibility / QA testing
# This is a method on MemoryStore, exposed here for convenience
async def get_l0_memories(store: MemoryStore, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Get L0 memories for a user.

    This is a wrapper around MemoryStore.get_l0_memories for backward compatibility.
    Use directly via MemoryStore.get_l0_memories when you have a store instance.
    This compatibility wrapper returns only explicitly cloud-eligible rows;
    known-local callers must use the store's explicit include_local option.
    """
    return await store.get_l0_memories(user_id)
