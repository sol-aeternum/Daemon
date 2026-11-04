from __future__ import annotations

import asyncio
import re
import uuid
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from time import monotonic
from typing import Any, cast

from orchestrator.guardrails import strip_reasoning_fields_from_message
from orchestrator.config import get_settings
from orchestrator.memory import provenance
from orchestrator.memory.embedding import (
    EmbeddingConfigurationError,
    embed_query_with_metadata,
    embed_queries_with_metadata,
    get_selected_embedding_route_id,
    raise_if_embedding_accounting_error,
)
from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.retrieval import RECENCY_REFERENCE_TIME
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
            lines.append(f"- {text}")
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
) -> MemoryContextInputs:
    """Enabled gathering is read-only; the production wrapper owns rendered effects.

    Flag off delegates to the unchanged single-query path and effect contract.
    Enabled callers can render the returned inputs with any budget without
    affecting ranking state. ``record_side_effects`` applies only when flag off.
    """
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
    if not settings.memory_multi_query_enabled:
        return await _gather_single_query_inputs(store, **kwargs)
    # This guard is before task creation or any new outbound work.
    if settings.memory_multi_query_merge == "round_robin" and not evaluation:
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
    reference = query_reference_time or RECENCY_REFERENCE_TIME.get() or datetime.now(timezone.utc)
    reference = (
        reference.replace(tzinfo=timezone.utc)
        if reference.tzinfo is None
        else reference.astimezone(timezone.utc)
    )
    kwargs.update(
        query_text=baseline_query, query_reference_time=reference, record_side_effects=False
    )
    token = RECENCY_REFERENCE_TIME.set(reference)
    tasks: list[asyncio.Task[Any]] = []
    try:
        baseline_task = asyncio.create_task(
            _gather_single_query_inputs(store, **kwargs, query_role="original", owned_tasks=tasks)
        )
        tasks.append(baseline_task)
        components_task = None
        if pipeline == "cloud" and latest_user and settings.memory_max_subqueries > 0:
            deadline = monotonic() + settings.memory_decomposition_timeout_ms / 1000
            components_task = asyncio.create_task(
                _decompose_components(
                    subquery_provider or ProductionSubqueryProvider(),
                    latest_user,
                    reference,
                    deadline,
                    settings.memory_max_subqueries,
                )
            )
            tasks.append(components_task)
        # Await both arms together so an accounting failure cancels/drains the other.
        inputs: MemoryContextInputs
        components: list[str]
        if components_task is not None:
            inputs, components = await asyncio.gather(baseline_task, components_task)
        else:
            inputs, components = await baseline_task, []
        if components:
            try:
                started = monotonic()
                try:
                    vectors = await asyncio.wait_for(
                        embed_queries_with_metadata(components), timeout=8.0
                    )
                finally:
                    _phase_latency("embedding", started)
                if len(vectors) != len(components):
                    raise ValueError("Component embedding count mismatch")
                arms = []
                started = monotonic()
                try:
                    for index, (component, vector) in enumerate(
                        zip(components, vectors, strict=True)
                    ):
                        arms.append(
                            await retrieve_memories_for_text(
                                store=store,
                                query_text=component,
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
                        )
                finally:
                    _phase_latency("sub_retrieval", started)
                merged = merge_memories(
                    inputs.retrieved,
                    arms,
                    quota=settings.memory_subquery_quota,
                    mode=settings.memory_multi_query_merge,
                    evaluation=evaluation,
                )
                existing = {str(row.get("id")) for row in inputs.retrieved}
                if any(str(row.get("id")) not in existing for row in merged):
                    inputs = replace(
                        inputs,
                        retrieved=merged,
                        retrieved_limit=len(merged) if len(merged) > MAX_MEMORY_ITEMS else None,
                    )
            except Exception as error:
                raise_if_embedding_accounting_error(error)
                logger.info("Memory multi-query branch failed; retaining baseline")
        return inputs
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            RECENCY_REFERENCE_TIME.reset(token)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise_if_embedding_accounting_error(outcome)


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
) -> None:
    try:
        rows = rendered_memory_rows(inputs, block)
        ids = list(dict.fromkeys(row["id"] for row in rows if isinstance(row.get("id"), uuid.UUID)))
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
                await trust.record_retrieved_memories(
                    conversation_id=conversation_id, memory_ids=ids, store=store
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
    inputs = await gather_memory_context_inputs(
        store,
        user_id=user_id,
        pipeline=pipeline,
        conversation_id=conversation_id,
    )
    block = render_memory_context(inputs, max_tokens)
    if enabled:
        await _record_rendered_effects(store, inputs, conversation_id, block)
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
