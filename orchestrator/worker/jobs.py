from __future__ import annotations

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportArgumentType=false, reportMissingImports=false

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast, NotRequired, TypedDict
from zoneinfo import ZoneInfo

from arq import Retry
from arq.connections import ArqRedis
from arq.jobs import Job

from orchestrator.config import Settings
from orchestrator.memory.dreaming import run_dreaming
from orchestrator.memory.entities import (
    extract_and_resolve_entities,
    persist_extraction_result,
)
from orchestrator.memory.extraction import (
    MAX_EXTRACTION_INPUT_CHARS,
    process_extraction,
    messages_to_extraction_text,
)
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.titles import ConversationMessage, generate_conversation_title
from orchestrator.skill_evaluator import (
    SkillEvaluator,
    SkillEvaluationRequest,
)

logger = logging.getLogger(__name__)


WorkerContext = dict[str, object]
# One chunk can perform two 90-second provider calls (initial + retry).
# Keeping the cap at one leaves headroom inside arq's 300-second job timeout.
MAX_EXTRACTION_CHUNKS_PER_JOB = 1


class ConsolidationResults(TypedDict):
    """Typed results dict for consolidate_memories job."""

    status: str
    clusters_found: int
    clusters_processed: int
    memories_created: int
    memories_demoted: int
    errors: list[str]
    users_processed: int
    error_count: int


class DreamingResults(TypedDict):
    status: str
    users_processed: int
    dream_runs_completed: int
    dream_runs_skipped: int
    dream_runs_failed: int
    observations_created: int
    errors: list[str]
    error_count: int


class EntityResolutionResults(TypedDict):
    """Typed results dict for consolidate_memories job."""

    status: str
    memories_processed: int
    entities_created: int
    entities_updated: int
    errors: list[str]
    error_count: int


class ExtractionChunk(TypedDict):
    """A bounded extractor call plus whether it completes its last message."""

    messages: list[ConversationMessage]
    raw_messages: list[Mapping[str, Any]]
    advances_cursor: bool


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


def _parse_message(item: Mapping[str, Any]) -> ConversationMessage | None:
    role = item.get("role")
    content = item.get("content")
    if role is None or content is None:
        return None
    return {"role": str(role), "content": str(content)}


def _parse_messages(messages_json: object) -> list[ConversationMessage]:
    messages: list[ConversationMessage] = []
    for item in _parse_raw_messages(messages_json):
        message = _parse_message(item)
        if message is not None:
            messages.append(message)
    return messages


def _coerce_message_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


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


def _extract_timezone_name(user_settings: dict[str, Any]) -> str | None:
    candidates = [
        user_settings.get("timezone"),
        user_settings.get("time_zone"),
    ]

    preferences = user_settings.get("preferences")
    if isinstance(preferences, dict):
        candidates.extend(
            [
                preferences.get("timezone"),
                preferences.get("time_zone"),
            ]
        )

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


async def _user_matches_dream_schedule_hour(
    store: MemoryStore,
    user_id: uuid.UUID,
    target_hour: int,
    now_utc: datetime | None = None,
) -> bool:
    current_utc = now_utc or datetime.now(timezone.utc)
    settings = await store.get_user_settings(user_id)
    timezone_name = _extract_timezone_name(settings)
    if not timezone_name:
        return True

    try:
        user_now = current_utc.astimezone(ZoneInfo(timezone_name))
    except Exception:
        logger.warning(
            "Invalid user timezone for dreaming schedule; falling back to server schedule",
            extra={"user_id": str(user_id), "timezone": timezone_name},
        )
        return True

    return user_now.hour == target_hour


def _chunk_messages_for_extraction(
    parsed_messages: Sequence[ConversationMessage],
    filtered_raw_messages: Sequence[Mapping[str, Any]],
    *,
    max_chars: int = MAX_EXTRACTION_INPUT_CHARS,
) -> list[ExtractionChunk]:
    """Split oldest-first messages into lossless, bounded extractor calls.

    Normal messages are packed up to ``max_chars``. A single oversized
    message is split into role-labeled fragments; only its final fragment
    is allowed to advance the durable cursor. This guarantees the extractor
    sees every selected character before the message falls behind the cursor.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    parsed_list = list(parsed_messages)
    raw_list = list(filtered_raw_messages)
    if len(parsed_list) != len(raw_list):
        raise AssertionError("parsed and raw message lists must be index-aligned")

    chunks: list[ExtractionChunk] = []
    current_parsed: list[ConversationMessage] = []
    current_raw: list[Mapping[str, Any]] = []

    def raw_advances_cursor(raw_message: Mapping[str, Any]) -> bool:
        return raw_message.get("_extraction_cursor_checkpoint") is not False

    def flush_current() -> None:
        nonlocal current_parsed, current_raw
        if current_parsed:
            chunks.append(
                {
                    "messages": current_parsed,
                    "raw_messages": current_raw,
                    "advances_cursor": raw_advances_cursor(current_raw[-1]),
                }
            )
            current_parsed = []
            current_raw = []

    for parsed_msg, raw_msg in zip(parsed_list, raw_list):
        single_text = messages_to_extraction_text([parsed_msg])
        if len(single_text) > max_chars:
            flush_current()
            content = str(parsed_msg.get("content") or "")
            empty_message: ConversationMessage = {
                "role": str(parsed_msg.get("role") or ""),
                "content": "",
            }
            payload_budget = max_chars - len(messages_to_extraction_text([empty_message]))
            if payload_budget <= 0:
                raise ValueError("max_chars is too small for a role label")
            fragments: list[str] = []
            start = 0
            overlap = min(200, max(1, payload_budget // 10))
            while start < len(content):
                end = min(len(content), start + payload_budget)
                fragments.append(content[start:end])
                if end == len(content):
                    break
                start = end - overlap
            for index, fragment in enumerate(fragments):
                fragment_message: ConversationMessage = {
                    "role": str(parsed_msg.get("role") or ""),
                    "content": fragment,
                }
                fragment_raw = dict(raw_msg)
                fragment_raw["content"] = fragment
                fragment_raw["_extraction_cursor_checkpoint"] = index == len(fragments) - 1
                fragment_raw["_extraction_continuation_key"] = (
                    f"{raw_msg.get('id', 'unknown')}:{index}"
                )
                chunks.append(
                    {
                        "messages": [fragment_message],
                        "raw_messages": [fragment_raw],
                        "advances_cursor": index == len(fragments) - 1,
                    }
                )
            continue

        candidate = [*current_parsed, parsed_msg]
        if current_parsed and len(messages_to_extraction_text(candidate)) > max_chars:
            flush_current()
        current_parsed.append(parsed_msg)
        current_raw.append(raw_msg)

    flush_current()
    return chunks


async def extract_memories(
    ctx: WorkerContext,
    user_id: str | uuid.UUID,
    conversation_id: str | uuid.UUID,
    messages_json: object | None = None,
) -> dict[str, object]:
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"status": "skipped", "reason": "store_unavailable"}

    # Retry-recovery: if a previous attempt committed a summary with
    # ``summary_continuation_pending=true`` but failed to enqueue the
    # forced-summary continuation (e.g. Redis transient error -> Retry),
    # the watermark will have advanced past the messages, so this retry
    # may find ``no_messages`` and exit before reaching the continuation
    # enqueue block. Consume the flag first: if it was set, enqueue the
    # continuation immediately (Codex P2 on PR #165, ``worker/jobs.py:295``).
    continuation_pending = False
    if hasattr(store_obj, "consume_summary_continuation_pending"):
        try:
            continuation_pending = await store_obj.consume_summary_continuation_pending(
                _as_uuid(conversation_id)
            )
        except Exception:
            logger.warning(
                "Failed to consume summary_continuation_pending for conversation %s",
                conversation_id,
            )

    if continuation_pending:
        queue = ctx.get("redis")
        if queue is not None and hasattr(queue, "enqueue_job"):
            try:
                await enqueue_with_debounce(
                    queue,
                    "generate_summary_job",
                    job_id=(
                        f"summary_continuation_recovery:{_as_uuid(conversation_id)}:"
                        f"{int(datetime.now(timezone.utc).timestamp())}"
                    ),
                    defer_by=timedelta(seconds=1),
                    args=(str(_as_uuid(conversation_id)), True),
                )
            except Exception:
                # ``generate_summary_job`` is the only periodic caller of
                # the forced-continuation path; if Redis transiently
                # rejects the enqueue, surface it as an arq ``Retry`` so
                # the extraction job is retried rather than silently
                # leaving the remaining summary backlog unscheduled.
                logger.warning(
                    "Failed to enqueue recovered summary continuation for "
                    "conversation %s; raising arq Retry",
                    conversation_id,
                )
                raise Retry(defer=5) from None
        # Whether the enqueue succeeded or not, the flag is already
        # consumed. Continue with the normal extraction flow.

    batch_limit = 250
    raw_messages: list[dict[str, Any]]
    needs_extraction_continuation = False
    if messages_json is None:
        cursor_at, cursor_message_id = await store_obj.get_last_extraction_cursor(
            _as_uuid(conversation_id)
        )
        messages = await store_obj.get_messages_after_cursor(
            _as_uuid(conversation_id),
            created_at=cursor_at,
            message_id=cursor_message_id,
            limit=batch_limit,
        )
        raw_messages = [dict(message) for message in messages]
        needs_extraction_continuation = len(raw_messages) == batch_limit
    else:
        decoded_messages_json = messages_json
        try:
            envelope = (
                json.loads(messages_json) if isinstance(messages_json, str) else messages_json
            )
        except (TypeError, json.JSONDecodeError):
            envelope = None
        if isinstance(envelope, Mapping) and isinstance(
            envelope.get("_encrypted_extraction_continuation"), str
        ):
            decoded_messages_json = store_obj.decrypt_extraction_continuation(
                str(envelope["_encrypted_extraction_continuation"])
            )
        raw_messages = _parse_raw_messages(decoded_messages_json)

    aligned_pairs: list[tuple[ConversationMessage, dict[str, Any], int]] = []
    for index, raw_message in enumerate(raw_messages):
        if raw_message.get("_extraction_skip") or _is_memory_write_artifact(raw_message):
            continue
        parsed_message = _parse_message(raw_message)
        if parsed_message is not None:
            aligned_pairs.append((parsed_message, raw_message, index))
    if not aligned_pairs and messages_json is not None:
        return {"status": "skipped", "reason": "no_messages"}

    def message_order_key(
        pair: tuple[ConversationMessage, dict[str, Any], int],
    ) -> tuple[datetime, str]:
        _, raw_message, original_index = pair
        timestamp = _coerce_message_timestamp(raw_message.get("created_at"))
        if timestamp is None:
            return datetime.min.replace(tzinfo=timezone.utc), f"{original_index:020d}"
        return timestamp, str(raw_message.get("id") or f"{original_index:020d}")

    aligned_pairs.sort(key=message_order_key)
    sorted_parsed = [pair[0] for pair in aligned_pairs]
    sorted_raw = [pair[1] for pair in aligned_pairs]
    chunks = _chunk_messages_for_extraction(sorted_parsed, sorted_raw) if aligned_pairs else []
    queue = ctx.get("redis")
    synchronous_direct_call = messages_json is not None and (
        queue is None or not hasattr(queue, "enqueue_job")
    )
    resume_database_after_payload = any(
        bool(raw_message.get("_resume_database_extraction")) for raw_message in raw_messages
    )
    if messages_json is not None:
        needs_extraction_continuation = resume_database_after_payload

    chunk_count = (
        len(chunks) if synchronous_direct_call else min(len(chunks), MAX_EXTRACTION_CHUNKS_PER_JOB)
    )
    chunks_to_process = chunks[:chunk_count]
    needs_extraction_continuation = needs_extraction_continuation or chunk_count < len(chunks)
    continuation_messages_json: str | None = None
    if chunk_count < len(chunks):
        remaining_raw: list[Mapping[str, Any]] = []
        seen_raw_ids: set[int] = set()
        for remaining_chunk in chunks[chunk_count:]:
            for raw_message in remaining_chunk["raw_messages"]:
                raw_identity = id(raw_message)
                if raw_identity not in seen_raw_ids:
                    seen_raw_ids.add(raw_identity)
                    remaining_raw.append(raw_message)
        if (messages_json is None or resume_database_after_payload) and remaining_raw:
            first_remaining = dict(remaining_raw[0])
            first_remaining["_resume_database_extraction"] = True
            remaining_raw[0] = first_remaining
        continuation_messages_json = json.dumps(remaining_raw, default=str)
        if messages_json is None or resume_database_after_payload:
            encrypted_payload = store_obj.encrypt_extraction_continuation(
                continuation_messages_json
            )
            continuation_messages_json = json.dumps(
                {"_encrypted_extraction_continuation": encrypted_payload}
            )

    new_memories: list[dict[str, Any]] = []
    summary_continuation_needed = False
    extraction_success = True
    processed_message_count = 0
    last_processed_message_id: str | None = None

    async def enqueue_entity_resolution(memories: Sequence[Mapping[str, Any]]) -> None:
        memory_ids = [str(memory.get("id")) for memory in memories if memory.get("id")]
        if not memory_ids:
            return
        entity_queue = ctx.get("redis")
        if not isinstance(entity_queue, ArqRedis):
            return
        await enqueue_with_debounce(
            entity_queue,
            "resolve_entities_job",
            job_id=(
                f"resolve_entities_{_as_uuid(user_id)}_{_as_uuid(conversation_id)}_{memory_ids[-1]}"
            ),
            args=(),
            kwargs={
                "user_id": str(_as_uuid(user_id)),
                "memory_ids_json": json.dumps(memory_ids),
            },
        )

    for chunk_index, chunk in enumerate(chunks_to_process):
        chunk_parsed = chunk["messages"]
        chunk_raw = chunk["raw_messages"]
        chunk_text = messages_to_extraction_text(chunk_parsed)
        if not chunk_text:
            continue

        advances_cursor = chunk["advances_cursor"]
        cursor_message = chunk_raw[-1]
        chunk_last_observed = (
            _coerce_message_timestamp(cursor_message.get("created_at")) if advances_cursor else None
        )
        raw_message_id = cursor_message.get("id") if advances_cursor else None
        chunk_last_message_id = str(raw_message_id) if raw_message_id is not None else None

        chunk_success, chunk_new_memories, chunk_continuation = await process_extraction(
            store=store_obj,
            user_id=_as_uuid(user_id),
            conversation_id=_as_uuid(conversation_id),
            text=chunk_text,
            last_message_observed_at=chunk_last_observed,
            last_message_id=chunk_last_message_id,
            chunk_index=chunk_index,
            cursor_checkpoint=advances_cursor,
        )
        if not chunk_success:
            # Earlier chunks may already have committed memories and cursor
            # checkpoints. Queue their entity projection before retrying the
            # failed suffix, because the retry will start after those chunks.
            await enqueue_entity_resolution(new_memories)
            raise Retry(defer=5)
        if chunk_new_memories:
            new_memories.extend(chunk_new_memories)
        summary_continuation_needed = summary_continuation_needed or chunk_continuation
        if advances_cursor:
            processed_message_count += len(chunk_raw)
            last_processed_message_id = chunk_last_message_id

    # Intentionally filtered artifacts and malformed rows are not extractor
    # inputs, but they must not pin pagination forever. Advance across a
    # trailing skipped suffix only after every selected chunk in this page
    # has completed successfully.
    if messages_json is None and raw_messages and chunk_count == len(chunks):
        page_last = raw_messages[-1]
        page_last_id_raw = page_last.get("id")
        page_last_id = str(page_last_id_raw) if page_last_id_raw is not None else None
        if page_last_id != last_processed_message_id:
            page_last_observed = _coerce_message_timestamp(page_last.get("created_at"))
            if page_last_observed is None:
                raise Retry(defer=5)
            await store_obj.log_extraction(
                user_id=_as_uuid(user_id),
                conversation_id=_as_uuid(conversation_id),
                input_snippet="",
                extracted_facts=[],
                dedup_results={
                    "merged": 0,
                    "superseded": 0,
                    "new": 0,
                    "last_message_id": page_last_id,
                    "cursor_checkpoint": True,
                    "filtered_page_checkpoint": True,
                },
                model_used="filter-only",
                last_message_observed_at=page_last_observed,
            )
            last_processed_message_id = page_last_id

    if extraction_success and new_memories:
        try:
            await enqueue_entity_resolution(new_memories)
        except Exception:
            logger.warning(
                "Failed to enqueue entity resolution job for user %s conversation %s",
                user_id,
                conversation_id,
            )

    if extraction_success and summary_continuation_needed:
        try:
            queue = ctx.get("redis")
            if queue is not None and hasattr(queue, "enqueue_job"):
                await enqueue_with_debounce(
                    queue,
                    "generate_summary_job",
                    job_id=(
                        f"summary_continuation:{_as_uuid(conversation_id)}:"
                        f"{int(datetime.now(timezone.utc).timestamp())}"
                    ),
                    defer_by=timedelta(seconds=1),
                    args=(str(_as_uuid(conversation_id)), True),
                )
        except Retry:
            # Let arq's Retry signal propagate so the extraction job is
            # retried; the inline summary was already committed by
            # ``process_extraction`` and the required continuation must
            # not be lost (Codex P2 on PR #165, ``worker/jobs.py:295``).
            raise
        except Exception:
            # ``generate_summary_job`` is the only periodic caller of
            # the forced-continuation path; if Redis transiently rejects
            # the enqueue, surface it as an arq ``Retry`` so the
            # extraction job is retried rather than silently leaving the
            # remaining summary backlog unscheduled (Codex P2 on PR
            # #165, ``worker/jobs.py:295``).
            logger.warning(
                "Failed to enqueue summary continuation for conversation %s; "
                "raising arq Retry to reschedule extraction",
                conversation_id,
            )
            raise Retry(defer=5) from None

    if needs_extraction_continuation:
        queue = ctx.get("redis")
        if queue is None or not hasattr(queue, "enqueue_job"):
            raise Retry(defer=5)
        try:
            continuation_key = last_processed_message_id
            if continuation_key is None and continuation_messages_json is not None:
                remaining_for_key = _parse_raw_messages(continuation_messages_json)
                continuation_key = str(
                    remaining_for_key[0].get("_extraction_continuation_key", "unknown")
                    if remaining_for_key
                    else "unknown"
                )
            continuation_args: tuple[str, str] | tuple[str, str, str] = (
                (str(_as_uuid(user_id)), str(_as_uuid(conversation_id)), continuation_messages_json)
                if continuation_messages_json is not None
                else (str(_as_uuid(user_id)), str(_as_uuid(conversation_id)))
            )
            enqueued = await enqueue_with_debounce(
                queue,
                "extract_memories",
                job_id=(f"extract_continuation:{_as_uuid(conversation_id)}:{continuation_key}"),
                defer_by=timedelta(seconds=1),
                args=continuation_args,
            )
        except Retry:
            raise
        except Exception:
            logger.warning(
                "Failed to enqueue extraction continuation for conversation %s",
                conversation_id,
                exc_info=True,
            )
            raise Retry(defer=5) from None
        if enqueued is None:
            raise Retry(defer=5)

    return {
        "status": "ok",
        "processed_messages": processed_message_count,
        "processed_chunks": len(chunks_to_process),
        "last_processed_message_id": last_processed_message_id,
    }


async def generate_title(
    ctx: WorkerContext,
    conversation_id: str | uuid.UUID,
    user_message_text: str,
) -> str | None:
    if not user_message_text:
        return None

    messages = [{"role": "user", "content": user_message_text}]

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
    title_model = (settings.title_model if settings else None) or "openrouter/openai/gpt-4o-mini"

    title = await generate_conversation_title(messages, model=title_model)
    if isinstance(store_obj, MemoryStore):
        try:
            _ = await store_obj.update_conversation(_as_uuid(conversation_id), title=title)
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
    title_model = (settings.title_model if settings else None) or "openrouter/openai/gpt-4o-mini"
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
    force: bool = False,
) -> dict[str, Any]:
    """Generate and store one bounded conversation-summary batch."""
    from orchestrator.memory.summarization import (
        generate_summary,
        should_summarize,
        validated_summary_baseline,
    )

    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"status": "skipped", "reason": "store_unavailable"}

    store = store_obj
    conv_id = uuid.UUID(conversation_id)

    conversation = await store.get_conversation(conv_id)
    if not conversation:
        return {"status": "not_found"}

    last_summary_time = conversation.get("summary_updated_at")
    previous_summary = conversation.get("summary")
    current_message_count = await store.count_summary_messages(conv_id)
    persisted_baseline = validated_summary_baseline(
        conversation,
        current_message_count,
    )
    # Pin the iteration to the moment we started, so concurrent status
    # flips from ``streaming`` to ``complete`` (which do not change
    # ``created_at``) cannot reorder the row set under an in-flight
    # cursor. The next iteration captures a fresh ``now()`` itself.
    iteration_snapshot = datetime.now(timezone.utc)

    # Advance only through the contiguous-finalized prefix at the snapshot.
    # A row that was streaming at fetch time but is now finalized with an
    # earlier ``created_at`` would otherwise insert before the prior
    # finalized rows and replay messages already counted toward the
    # baseline (Codex P2 on PR #165). ``persisted_baseline`` is the prior
    # cursor (lower bound); ``contiguous_baseline`` is the current
    # contiguous-prefix count, which is only larger when the streaming
    # tail shrank.
    contiguous_baseline = await store.count_contiguous_finalized_messages_at(
        conv_id,
        snapshot_at=iteration_snapshot,
    )
    # ``last_summarized_msg_count`` is the count used by ``should_summarize``
    # to compute delta against the current finalized-message count. It is
    # not the offset — those are two distinct concepts (matches the inline
    # path on PR #165).
    last_summarized_msg_count = max(persisted_baseline, contiguous_baseline)
    settings: dict[str, Any] = {}

    if not force and not await should_summarize(
        conv_id,
        last_summary_time,
        last_summarized_msg_count,
        store,
        settings,
    ):
        return {"status": "skipped", "reason": "thresholds_not_met"}

    # The cursor advances only through rows actually included in past
    # summaries (``persisted_baseline``). ``contiguous_baseline`` is
    # used solely to bound how far this iteration may claim — never as
    # an offset. The prior ``max(...)`` offset skipped finalized rows
    # that were contiguous-but-not-yet-summarized (e.g. baseline 0 with
    # rows ``complete m1, streaming m2, complete m3``: offset became 1,
    # so m1 was skipped). Using ``persisted_baseline`` directly produces
    # the rows that need summarization (Codex P2 on PR #165,
    # ``worker/jobs.py:441``).
    messages = await store.get_summary_message_batch(
        conv_id,
        offset=persisted_baseline,
        limit=100,
        snapshot_at=iteration_snapshot,
    )
    if not messages:
        return {"status": "skipped", "reason": "up_to_date"}

    summary = await generate_summary(messages, previous_summary, settings)
    if not summary.strip():
        raise Retry(defer=5)

    # Advance the persisted baseline by ONLY the rows actually
    # incorporated in this iteration (``persisted_baseline + len(messages)``),
    # capped at the contiguous-prefix boundary so a later
    # ``streaming -> complete`` transition cannot pull already-claimed
    # rows back into the next batch. Matches the inline path on PR
    # #165 (``summary.py:225``). Prior implementation derived the
    # advance from ``last_summarized_msg_count`` (the inflated
    # ``max(persisted_baseline, contiguous_baseline)``), which caused
    # the persisted baseline to skip finalized rows that were
    # contiguous-but-not-yet-summarized (Codex P2 on PR #165,
    # ``worker/jobs.py:445``).
    summarized_message_count = min(
        persisted_baseline + len(messages),
        contiguous_baseline,
    )
    updated = await store.update_conversation_summary(
        conv_id,
        summary=summary,
        expected_summary_updated_at=last_summary_time,
        summarized_message_count=summarized_message_count,
        summary_snapshot_at=iteration_snapshot,
    )
    if not updated:
        raise Retry(defer=5)

    continuation_enqueued = False
    if len(messages) == 100:
        queue = ctx.get("redis")
        if queue is not None:
            follow_up = await enqueue_with_debounce(
                cast(ArqRedis, queue),
                "generate_summary_job",
                job_id=f"summary:{conv_id}:{summarized_message_count}",
                defer_by=timedelta(seconds=1),
                args=(str(conv_id), True),
            )
            continuation_enqueued = follow_up is not None

    return {
        "status": "success",
        "summary_length": len(summary),
        "summarized_message_count": summarized_message_count,
        "continuation_enqueued": continuation_enqueued,
    }


async def garbage_collect(ctx: WorkerContext) -> dict[str, int]:
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"scanned": 0, "deleted": 0}

    return await store_obj.run_garbage_collect()


async def cleanup_generated_files(ctx: WorkerContext) -> dict[str, int]:
    """Delete generated files older than 24 hours."""
    from orchestrator.config import get_settings

    settings = get_settings()  # noqa: F841
    generated_files_dir = Path(__file__).resolve().parent.parent.parent / "data" / "generated_files"

    if not generated_files_dir.exists():
        return {"scanned": 0, "deleted": 0}

    cutoff = datetime.now() - timedelta(hours=24)
    deleted = 0
    scanned = 0

    for item in generated_files_dir.iterdir():
        scanned += 1
        # Only delete files, not directories
        if item.is_file():
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if mtime < cutoff:
                    item.unlink()
                    deleted += 1
                    logger.info(f"Deleted old generated file: {item.name}")
            except Exception as e:
                logger.warning(f"Failed to process {item.name}: {e}")

    return {"scanned": scanned, "deleted": deleted}


async def cleanup_generated_images(ctx: WorkerContext) -> dict[str, int]:
    _ = ctx
    generated_images_dir = (
        Path(__file__).resolve().parent.parent.parent / "data" / "generated_images"
    )

    if not generated_images_dir.exists():
        return {"scanned": 0, "deleted": 0}

    cutoff = datetime.now() - timedelta(hours=24)
    deleted = 0
    scanned = 0

    for item in generated_images_dir.iterdir():
        scanned += 1
        if item.is_file():
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if mtime < cutoff:
                    item.unlink()
                    deleted += 1
                    logger.info(f"Deleted old generated image artifact: {item.name}")
            except Exception as e:
                logger.warning(f"Failed to process generated image artifact {item.name}: {e}")

    return {"scanned": scanned, "deleted": deleted}


async def run_dreaming_job(
    ctx: WorkerContext,
    user_id: str | uuid.UUID | None = None,
    *,
    scheduled: bool = False,
    now_utc: datetime | None = None,
) -> DreamingResults:
    store_obj = ctx.get("store")
    settings_obj = ctx.get("settings")

    if not isinstance(store_obj, MemoryStore):
        return DreamingResults(
            status="skipped",
            users_processed=0,
            dream_runs_completed=0,
            dream_runs_skipped=0,
            dream_runs_failed=0,
            observations_created=0,
            errors=["store_unavailable"],
            error_count=1,
        )

    if not isinstance(settings_obj, Settings):
        return DreamingResults(
            status="skipped",
            users_processed=0,
            dream_runs_completed=0,
            dream_runs_skipped=0,
            dream_runs_failed=0,
            observations_created=0,
            errors=["settings_unavailable"],
            error_count=1,
        )

    if not settings_obj.dreaming_enabled:
        return DreamingResults(
            status="skipped",
            users_processed=0,
            dream_runs_completed=0,
            dream_runs_skipped=0,
            dream_runs_failed=0,
            observations_created=0,
            errors=["dreaming_disabled"],
            error_count=1,
        )

    if user_id is not None:
        try:
            user_ids = [_as_uuid(user_id)]
        except ValueError as error:
            return DreamingResults(
                status="error",
                users_processed=0,
                dream_runs_completed=0,
                dream_runs_skipped=0,
                dream_runs_failed=0,
                observations_created=0,
                errors=[f"invalid_user_id: {error}"],
                error_count=1,
            )
    else:
        user_ids = await store_obj.get_users_with_dream_candidates()

    results: DreamingResults = {
        "status": "ok",
        "users_processed": 0,
        "dream_runs_completed": 0,
        "dream_runs_skipped": 0,
        "dream_runs_failed": 0,
        "observations_created": 0,
        "errors": [],
        "error_count": 0,
    }

    for uid in user_ids:
        try:
            if scheduled and not await _user_matches_dream_schedule_hour(
                store_obj,
                uid,
                settings_obj.dream_schedule_hour,
                now_utc,
            ):
                continue

            dream_result = await run_dreaming(uid, store=store_obj)
            results["users_processed"] += 1
            results["observations_created"] += int(dream_result.get("observations_created", 0) or 0)

            status = str(dream_result.get("status") or "").lower()
            if status == "completed":
                results["dream_runs_completed"] += 1
            elif status == "failed":
                results["dream_runs_failed"] += 1
                error_text = str(dream_result.get("error") or "dream_run_failed")
                results["errors"].append(f"{uid}: {error_text}")
            else:
                results["dream_runs_skipped"] += 1
        except Exception as error:
            logger.warning("Dreaming job failed for user %s: %s", uid, error, exc_info=True)
            results["users_processed"] += 1
            results["dream_runs_failed"] += 1
            results["errors"].append(f"{uid}: {error}")

    if results["dream_runs_failed"] and not results["dream_runs_completed"]:
        results["status"] = "error"
    elif not user_ids:
        results["status"] = "skipped"

    results["error_count"] = len(results["errors"])
    return results


async def run_scheduled_dreaming_job(ctx: WorkerContext) -> DreamingResults:
    return await run_dreaming_job(ctx, scheduled=True)


async def consolidate_memories(
    ctx: WorkerContext,
    user_id: str | uuid.UUID | None = None,
) -> ConsolidationResults:
    """Run memory consolidation for a user or all users.

    Finds clusters of related L1 memories and consolidates them into
    summary memories. Source memories are demoted to L2 (not deleted).

    This job is interruptible - each cluster is processed independently,
    so partial failures don't lose progress on other clusters.

    Args:
        ctx: Worker context with store and settings
        user_id: Optional specific user to consolidate. If None, processes
                 all users with eligible memories.

    Returns:
        ConsolidationResults with counts and status
    """
    from orchestrator.memory.consolidation import (
        find_memory_clusters,
        consolidate_cluster,
        MemoryCluster,
    )

    store_obj = ctx.get("store")
    settings_obj = ctx.get("settings")

    if not isinstance(store_obj, MemoryStore):
        return ConsolidationResults(
            status="skipped",
            clusters_found=0,
            clusters_processed=0,
            memories_created=0,
            memories_demoted=0,
            errors=["store_unavailable"],
            users_processed=0,
            error_count=1,
        )

    if not isinstance(settings_obj, Settings):
        return ConsolidationResults(
            status="skipped",
            clusters_found=0,
            clusters_processed=0,
            memories_created=0,
            memories_demoted=0,
            errors=["settings_unavailable"],
            users_processed=0,
            error_count=1,
        )

    # Check if consolidation is enabled
    if not settings_obj.consolidation_enabled:
        logger.info("Memory consolidation is disabled via config")
        return ConsolidationResults(
            status="skipped",
            clusters_found=0,
            clusters_processed=0,
            memories_created=0,
            memories_demoted=0,
            errors=["consolidation_disabled"],
            users_processed=0,
            error_count=1,
        )

    store = store_obj
    results: ConsolidationResults = {
        "status": "ok",
        "clusters_found": 0,
        "clusters_processed": 0,
        "memories_created": 0,
        "memories_demoted": 0,
        "errors": [],
        "users_processed": 0,
        "error_count": 0,
    }

    # Get list of users to process
    user_ids: list[uuid.UUID] = []

    if user_id is not None:
        # Single user mode (manual trigger)
        try:
            user_ids = [_as_uuid(user_id)]
        except ValueError as e:
            results["status"] = "error"
            results["errors"].append(f"invalid_user_id: {e}")
            results["error_count"] = 1
    else:
        # Periodic job - find all users with eligible L1 memories
        user_ids = await store.list_users_with_eligible_l1_memories()
        logger.info(f"Found {len(user_ids)} users with eligible memories for consolidation")

    # Process each user
    for uid in user_ids:
        try:
            clusters = await find_memory_clusters(uid, store)
            results["clusters_found"] += len(clusters)

            for cluster in clusters:
                try:
                    if not isinstance(cluster, MemoryCluster):
                        logger.warning(f"Invalid cluster type for user {uid}: {type(cluster)}")
                        continue

                    if len(cluster) < 3:
                        logger.debug(f"Cluster too small, skipping: {len(cluster)} members")
                        continue

                    created = await consolidate_cluster(cluster, store, uid)

                    if created:
                        results["clusters_processed"] += 1
                        results["memories_created"] += len(created)
                        results["memories_demoted"] += len(cluster.members)
                        logger.info(
                            f"Consolidated cluster for user {uid}: "
                            f"created {len(created)} summaries from {len(cluster.members)} sources"
                        )
                    else:
                        logger.warning(
                            f"Consolidation produced no memories for cluster in user {uid}"
                        )

                except Exception as e:
                    error_msg = f"Cluster consolidation failed for user {uid}: {e}"
                    logger.warning(error_msg, exc_info=True)
                    results["errors"].append(error_msg)

            results["users_processed"] += 1

        except Exception as e:
            error_msg = f"User consolidation failed for {uid}: {e}"
            logger.warning(error_msg, exc_info=True)
            results["errors"].append(error_msg)

    # Summary logging
    logger.info(
        f"Consolidation complete: {results['clusters_processed']}/{results['clusters_found']} "
        f"clusters processed, {results['memories_created']} memories created, "
        f"{results['memories_demoted']} sources demoted, {len(results['errors'])} errors"
    )

    results["error_count"] = len(results["errors"])

    return results


class SkillEvaluationJobResult(TypedDict):
    """Typed results dict for run_skill_evaluation_job."""

    status: str
    classification: str
    tool_call_count: int
    created_skill_id: str | None
    patched_skill_id: str | None
    matched_skill_id: str | None
    matched_similarity: float | None
    matched_source_type: str | None
    protected: bool
    trigger_conditions: str | None
    complexity_origin: int | None
    reason: str | None
    errors: list[str]
    error_count: int


async def run_skill_evaluation_job(
    ctx: WorkerContext,
    user_id: str | uuid.UUID,
    conversation_id: str | uuid.UUID,
    assistant_message_id: str | uuid.UUID,
    tool_call_count: int,
) -> SkillEvaluationJobResult:
    store_obj = ctx.get("store")
    db_pool = ctx.get("db_pool")

    if not isinstance(store_obj, MemoryStore):
        return SkillEvaluationJobResult(
            status="skipped",
            classification="skipped_store_unavailable",
            tool_call_count=tool_call_count,
            created_skill_id=None,
            patched_skill_id=None,
            matched_skill_id=None,
            matched_similarity=None,
            matched_source_type=None,
            protected=False,
            trigger_conditions=None,
            complexity_origin=None,
            reason="memory store unavailable",
            errors=["store_unavailable"],
            error_count=1,
        )

    evaluator = SkillEvaluator(store=store_obj, db_pool=db_pool)

    request = SkillEvaluationRequest(
        user_id=_as_uuid(user_id),
        conversation_id=_as_uuid(conversation_id),
        assistant_message_id=_as_uuid(assistant_message_id),
        tool_call_count=tool_call_count,
    )

    try:
        result = await evaluator.evaluate_completed_turn(request)

        return SkillEvaluationJobResult(
            status="ok",
            classification=result.classification,
            tool_call_count=result.tool_call_count,
            created_skill_id=result.created_skill_id,
            patched_skill_id=result.patched_skill_id,
            matched_skill_id=result.matched_skill_id,
            matched_similarity=result.matched_similarity,
            matched_source_type=result.matched_source_type,
            protected=result.protected,
            trigger_conditions=result.trigger_conditions,
            complexity_origin=result.complexity_origin,
            reason=result.reason,
            errors=[],
            error_count=0,
        )
    except Exception as e:
        logger.warning(
            "Skill evaluation job failed for user %s conversation %s: %s",
            user_id,
            conversation_id,
            e,
            exc_info=True,
        )
        return SkillEvaluationJobResult(
            status="error",
            classification="error",
            tool_call_count=tool_call_count,
            created_skill_id=None,
            patched_skill_id=None,
            matched_skill_id=None,
            matched_similarity=None,
            matched_source_type=None,
            protected=False,
            trigger_conditions=None,
            complexity_origin=None,
            reason=str(e),
            errors=[str(e)],
            error_count=1,
        )


async def resolve_entities_job(
    ctx: WorkerContext,
    user_id: str | uuid.UUID,
    memory_ids_json: str,
) -> EntityResolutionResults:
    """Run entity resolution for newly created memories.

    This job is best-effort - failures are logged but don't block
    memory creation or extraction completion.

    Args:
        ctx: Worker context with store
        user_id: User ID
        memory_ids_json: JSON-serialized list of memory ID strings

    Returns:
        EntityResolutionResults with counts and status
    """
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return EntityResolutionResults(
            status="skipped",
            memories_processed=0,
            entities_created=0,
            entities_updated=0,
            errors=["store_unavailable"],
            error_count=1,
        )

    try:
        memory_ids = json.loads(memory_ids_json)
        if not isinstance(memory_ids, list):
            memory_ids = []
    except (json.JSONDecodeError, TypeError):
        return EntityResolutionResults(
            status="error",
            memories_processed=0,
            entities_created=0,
            entities_updated=0,
            errors=["invalid_memory_ids_json"],
            error_count=1,
        )

    uid = _as_uuid(user_id)
    results: EntityResolutionResults = {
        "status": "ok",
        "memories_processed": 0,
        "entities_created": 0,
        "entities_updated": 0,
        "errors": [],
        "error_count": 0,
    }

    memory_contents: list[tuple[str, uuid.UUID | None, str | None]] = []
    for mid in memory_ids:
        try:
            memory_uuid = uuid.UUID(mid) if isinstance(mid, str) else mid
        except (ValueError, TypeError):
            continue

        try:
            memory = await store_obj.get_memory(memory_uuid)
            if memory and memory.get("content"):
                content = memory.get("content", "")
                slot = memory.get("memory_slot")
                memory_contents.append((content, memory_uuid, slot))
        except Exception as e:
            logger.warning("Failed to fetch memory %s for entity resolution: %s", memory_uuid, e)
            continue

    if not memory_contents:
        return EntityResolutionResults(
            status="ok",
            memories_processed=0,
            entities_created=0,
            entities_updated=0,
            errors=[],
            error_count=0,
        )

    try:
        extraction_result = await extract_and_resolve_entities(
            uid, store_obj, memory_contents, use_spacy=False
        )

        if extraction_result.resolutions:
            entity_ids = await persist_extraction_result(uid, store_obj, extraction_result)
            results["entities_created"] = len(entity_ids)
            results["entities_updated"] = len([e for e in entity_ids if e is not None])

        results["memories_processed"] = len(memory_contents)

    except Exception as e:
        logger.warning("Entity resolution failed for user %s: %s", uid, e, exc_info=True)
        results["status"] = "error"
        results["errors"].append(f"entity_resolution_failed: {e}")
        results["error_count"] = len(results["errors"])

    return results


class ConsolidationNudgeAction(TypedDict):
    action_type: str
    skill_id: str | None
    target_skill_id: str | None
    reason: str
    similarity: float | None
    status: str
    audit_id: NotRequired[uuid.UUID]


class ConsolidationNudgeResults(TypedDict):
    status: str
    user_id: str | None
    actions: list[ConsolidationNudgeAction]
    skills_reviewed: int
    duplicates_found: int
    duplicates_merged: int
    stale_flagged: int
    errors: list[str]
    error_count: int


def _build_consolidation_nudge_debounce_key(user_id: str | uuid.UUID) -> str:
    return f"consolidation_nudge:{user_id}"


async def run_consolidation_nudge_job(
    ctx: WorkerContext,
    user_id: str | uuid.UUID | None = None,
) -> ConsolidationNudgeResults:

    store_obj = ctx.get("store")
    settings_obj = ctx.get("settings")
    db_pool = ctx.get("db_pool")  # noqa: F841

    if not isinstance(store_obj, MemoryStore):
        return ConsolidationNudgeResults(
            status="skipped",
            user_id=str(user_id) if user_id else None,
            actions=[],
            skills_reviewed=0,
            duplicates_found=0,
            duplicates_merged=0,
            stale_flagged=0,
            errors=["store_unavailable"],
            error_count=1,
        )

    if not isinstance(settings_obj, Settings):
        return ConsolidationNudgeResults(
            status="skipped",
            user_id=str(user_id) if user_id else None,
            actions=[],
            skills_reviewed=0,
            duplicates_found=0,
            duplicates_merged=0,
            stale_flagged=0,
            errors=["settings_unavailable"],
            error_count=1,
        )

    interval = getattr(settings_obj, "consolidation_nudge_conversation_interval", 15)
    stale_days = getattr(settings_obj, "consolidation_nudge_stale_days", 30)
    min_skills = getattr(settings_obj, "consolidation_nudge_min_skills", 3)

    if user_id is not None:
        user_ids = [_as_uuid(user_id)]
    else:
        user_ids = await store_obj.get_users_with_skill_candidates(interval)

    results: ConsolidationNudgeResults = {
        "status": "ok",
        "user_id": None,
        "actions": [],
        "skills_reviewed": 0,
        "duplicates_found": 0,
        "duplicates_merged": 0,
        "stale_flagged": 0,
        "errors": [],
        "error_count": 0,
    }

    for uid in user_ids:
        try:
            user_result = await _process_user_consolidation_nudge(
                uid, store_obj, interval, stale_days, min_skills
            )
            results["user_id"] = str(uid)
            results["skills_reviewed"] += user_result["skills_reviewed"]
            results["duplicates_found"] += user_result["duplicates_found"]
            results["duplicates_merged"] += user_result["duplicates_merged"]
            results["stale_flagged"] += user_result["stale_flagged"]
            results["actions"].extend(user_result["actions"])
            results["errors"].extend(user_result["errors"])
        except Exception as e:
            error_msg = f"User {uid} consolidation nudge failed: {e}"
            logger.warning(error_msg, exc_info=True)
            results["errors"].append(error_msg)

    results["error_count"] = len(results["errors"])
    if results["errors"]:
        results["status"] = "error"

    return results


async def _process_user_consolidation_nudge(
    user_id: uuid.UUID,
    store: MemoryStore,
    interval: int,
    stale_days: int,
    min_skills: int,
) -> dict[str, Any]:
    from orchestrator.consolidation_nudge_prompts import (
        build_consolidation_nudge_prompt,
    )

    actions: list[ConsolidationNudgeAction] = []
    errors: list[str] = []
    skills_reviewed = 0
    duplicates_found = 0
    duplicates_merged = 0
    stale_flagged = 0

    conversation_delta = await store.get_user_conversation_count_since_last_nudge(user_id)
    if conversation_delta < interval:
        return {
            "skills_reviewed": 0,
            "duplicates_found": 0,
            "duplicates_merged": 0,
            "stale_flagged": 0,
            "actions": [],
            "errors": [],
        }

    total_conversations = await store.get_total_conversation_count(user_id)

    autonomous_skills = await store.get_autonomous_skill_candidates(min_skills)
    if not autonomous_skills:
        await store.record_consolidation_nudge_run(user_id, total_conversations)
        return {
            "skills_reviewed": 0,
            "duplicates_found": 0,
            "duplicates_merged": 0,
            "stale_flagged": 0,
            "actions": [],
            "errors": [],
        }

    skills_reviewed = len(autonomous_skills)

    recent_memories = await store.get_recent_memories_for_user(user_id, limit=20)

    prompt = build_consolidation_nudge_prompt(
        autonomous_skills=autonomous_skills,
        recent_memories=recent_memories,
        user_context=None,
    )

    model_actions = await _call_consolidation_model(prompt)

    autonomous_skill_ids = {s["skill_id"] for s in autonomous_skills}

    for action in model_actions:
        action_type = action.get("type", "")
        skill_id = action.get("skill_id")

        if not skill_id:
            continue

        if action_type == "merge":
            duplicates_found += 1

        if action_type in {"merge", "delete"}:
            if skill_id not in autonomous_skill_ids:
                actions.append(
                    ConsolidationNudgeAction(
                        action_type=action_type,
                        skill_id=skill_id,
                        target_skill_id=action.get("target_skill_id"),
                        reason=f"skip: {action.get('reason', 'skill is not autonomous')}",
                        similarity=action.get("similarity"),
                        status="skipped",
                    )
                )
                continue

        if action_type == "merge":
            target_id = action.get("target_skill_id")
            if target_id and target_id in autonomous_skill_ids:
                merge_result = await _apply_merge_action(skill_id, target_id, store, user_id)
                if merge_result["merged"]:
                    duplicates_merged += 1
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="merge",
                            skill_id=skill_id,
                            target_skill_id=target_id,
                            reason=action.get("reason", "model-driven merge"),
                            similarity=action.get("similarity"),
                            status="applied",
                        )
                    )
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="delete",
                            skill_id=target_id,
                            target_skill_id=None,
                            reason=f"merged_into {skill_id}",
                            similarity=None,
                            status="applied",
                        )
                    )
                else:
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="merge",
                            skill_id=skill_id,
                            target_skill_id=target_id,
                            reason=merge_result.get("reason", "merge failed"),
                            similarity=action.get("similarity"),
                            status="skipped",
                        )
                    )
            else:
                actions.append(
                    ConsolidationNudgeAction(
                        action_type="merge",
                        skill_id=skill_id,
                        target_skill_id=target_id,
                        reason="skip: target not autonomous or not found",
                        similarity=action.get("similarity"),
                        status="skipped",
                    )
                )

        elif action_type == "delete":
            if skill_id in autonomous_skill_ids:
                delete_result = await _apply_delete_action(
                    skill_id,
                    store,
                    user_id,
                    reason=action.get("reason", "model-driven delete"),
                )
                audit_id: uuid.UUID | None = delete_result.get("audit_id")
                if delete_result["deleted"]:
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="delete",
                            skill_id=skill_id,
                            target_skill_id=None,
                            reason=delete_result.get("reason")
                            or action.get("reason", "model-driven delete"),
                            similarity=None,
                            status="applied",
                            audit_id=audit_id,
                        )
                    )
                else:
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="delete",
                            skill_id=skill_id,
                            target_skill_id=None,
                            reason=delete_result.get("reason", "delete failed"),
                            similarity=None,
                            status="skipped",
                            audit_id=audit_id,
                        )
                    )

        elif action_type == "flag_stale":
            if skill_id in autonomous_skill_ids:
                stale_flagged += 1
                actions.append(
                    ConsolidationNudgeAction(
                        action_type="flag_stale",
                        skill_id=skill_id,
                        target_skill_id=None,
                        reason=action.get("reason", "stale by model assessment"),
                        similarity=None,
                        status="recorded",
                    )
                )

    run_id = uuid.uuid4()
    await store.record_consolidation_nudge_run(user_id, total_conversations)

    for action in actions:
        skill_name = None
        skill_desc = None
        skill_use_count = None
        skill_last_used = None
        for s in autonomous_skills:
            if s["skill_id"] == action["skill_id"]:
                skill_name = s.get("name")
                skill_desc = s.get("description")
                skill_use_count = s.get("use_count")
                skill_last_used = s.get("last_used_at")
                break
        existing_audit_id = action.get("audit_id")
        if existing_audit_id is not None:
            # _apply_delete_action pre-wrote the pending audit row and has
            # already finalized its status (applied on success, failed with
            # reason on failure). Re-touching the row here would either
            # duplicate the work or overwrite the `failed` status with the
            # outer loop's `skipped`. Skip persistence; the action is kept
            # in the returned list for caller visibility.
            continue
        await store.log_consolidation_nudge_action(
            user_id=user_id,
            run_id=run_id,
            action_type=action["action_type"],
            skill_id=action["skill_id"],
            target_skill_id=action.get("target_skill_id"),
            reason=action.get("reason", ""),
            similarity=action.get("similarity"),
            status=action["status"],
            skill_name=skill_name,
            skill_description=skill_desc,
            skill_use_count=skill_use_count,
            skill_last_used_at=skill_last_used,
        )

    return {
        "skills_reviewed": skills_reviewed,
        "duplicates_found": duplicates_found,
        "duplicates_merged": duplicates_merged,
        "stale_flagged": stale_flagged,
        "actions": actions,
        "errors": errors,
    }


async def _call_consolidation_model(prompt: str) -> list[dict[str, Any]]:
    from orchestrator.config import get_settings
    import litellm

    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")
    model = settings.auto_fast_model
    if not model.startswith("openrouter/"):
        model = f"openrouter/{model}"

    call_params: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a skill consolidation analyst. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
        "timeout": provider_config.timeout_s,
        "response_format": {"type": "json_object"},
    }
    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    try:
        response = await litellm.acompletion(**call_params)
        content = _extract_response_content(response)
        if content:
            from orchestrator.consolidation_nudge_prompts import (
                parse_consolidation_actions,
            )

            return parse_consolidation_actions(content)
    except Exception:
        pass
    return []


def _extract_response_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        choice0 = choices[0]
        if isinstance(choice0, dict):
            message = choice0.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
        else:
            message = getattr(choice0, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
    return ""


async def _apply_merge_action(
    skill_id: str,
    target_skill_id: str,
    store: MemoryStore,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    try:
        await store.merge_autonomous_skills(
            kept_skill_id=skill_id,
            absorbed_skill_ids=[target_skill_id],
            user_id=user_id,
        )
        return {"merged": True, "reason": "ok"}
    except Exception as e:
        return {"merged": False, "reason": str(e)}


async def _apply_delete_action(
    skill_id: str,
    store: MemoryStore,
    user_id: uuid.UUID,
    *,
    reason: str = "model-driven delete",
) -> dict[str, Any]:
    run_id = uuid.uuid4()
    try:
        audit_id = await store.log_consolidation_nudge_action(
            user_id=user_id,
            run_id=run_id,
            action_type="delete",
            skill_id=skill_id,
            target_skill_id=None,
            reason=reason,
            similarity=None,
            status="pending",
        )
    except Exception as e:
        return {"deleted": False, "reason": f"audit log failed before delete: {e}"}

    try:
        from orchestrator.skills_store import delete_skill

        delete_skill(skill_id)
        await store.delete_skill_projection(skill_id)
        await store.update_consolidation_nudge_action_status(
            audit_id,
            status="applied",
        )
        return {"deleted": True, "reason": "ok", "audit_id": audit_id}
    except Exception as e:
        try:
            await store.update_consolidation_nudge_action_status(
                audit_id,
                status="failed",
                reason=f"delete failed: {e}",
            )
        except Exception:
            logger.warning("Failed to mark consolidation delete audit row failed", exc_info=True)
        return {"deleted": False, "reason": str(e), "audit_id": audit_id}


async def _find_duplicate_autonomous_skills(
    skills: list[dict[str, Any]],
    store: MemoryStore,
) -> list[list[dict[str, Any]]]:
    if len(skills) < 2:
        return []

    from orchestrator.memory.embedding import embed_query

    groups: list[list[dict[str, Any]]] = []
    processed: set[str] = set()

    for i, skill in enumerate(skills):
        if skill["skill_id"] in processed:
            continue

        group = [skill]
        query_text = f"{skill['name']}\n{skill['description']}"
        try:
            query_emb = await embed_query(query_text)
        except Exception:
            continue

        for j, other_skill in enumerate(skills[i + 1 :], start=i + 1):
            if other_skill["skill_id"] in processed:
                continue

            if other_skill.get("embedding") is None:
                continue

            norm_a = sum(a * a for a in query_emb) ** 0.5
            norm_b = sum(b * b for b in other_skill["embedding"]) ** 0.5
            dot = sum(a * b for a, b in zip(query_emb, other_skill["embedding"]))
            similarity = dot / (norm_a * norm_b + 1e-8)
            cosine_distance = 1 - similarity  # noqa: F841

            if similarity >= 0.90:
                group.append(other_skill)
                processed.add(other_skill["skill_id"])

        if len(group) > 1:
            groups.append(group)
            processed.add(skill["skill_id"])

    return groups


async def _try_merge_duplicate_skills(
    group: list[dict[str, Any]],
    store: MemoryStore,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    if len(group) < 2:
        return {
            "merged": False,
            "reason": "group has fewer than 2 skills",
            "similarity": None,
            "kept_skill_id": None,
            "absorbed_skill_ids": [],
        }

    primary = max(group, key=lambda s: s.get("use_count") or 0)
    absorbed = [s for s in group if s["skill_id"] != primary["skill_id"]]

    try:
        await store.merge_autonomous_skills(
            kept_skill_id=primary["skill_id"],
            absorbed_skill_ids=[s["skill_id"] for s in absorbed],
            user_id=user_id,
        )
        return {
            "merged": True,
            "reason": f"merged {len(absorbed)} duplicate(s)",
            "similarity": 0.90,
            "kept_skill_id": primary["skill_id"],
            "absorbed_skill_ids": [s["skill_id"] for s in absorbed],
        }
    except Exception as e:
        return {
            "merged": False,
            "reason": f"merge failed: {e}",
            "similarity": 0.90,
            "kept_skill_id": primary["skill_id"],
            "absorbed_skill_ids": [],
        }


async def _find_stale_autonomous_skills(
    skills: list[dict[str, Any]],
    store: MemoryStore,
    stale_days: int,
) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for skill in skills:
        last_used = skill.get("last_used_at")
        if last_used is None:
            continue
        try:
            from datetime import timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
            if last_used < cutoff:
                stale.append(skill)
        except Exception:
            continue
    return stale
