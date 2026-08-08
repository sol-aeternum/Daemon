"""Conversation summary generation module for Daemon memory layer.

Implements incremental summary generation using auto_fast_model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import litellm

from orchestrator.config import get_settings
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.summarization import validated_summary_baseline


# Default inline batch size for the post-extraction summary path. Kept
# small so the prompt stays bounded; the worker path uses a larger
# 100-message batch with an explicit continuation enqueue.
INLINE_SUMMARY_BATCH_LIMIT = 20


SUMMARY_PROMPT = """Given the existing summary and new messages, produce an updated summary of this conversation in 2-3 sentences.

Focus on:
- Topics discussed
- Decisions made
- Facts shared by the user
- Current status or open items

Guidelines:
- Maximum 3 sentences
- Be concise but informative
- Preserve important context from previous summary
- Highlight new information from recent messages

Existing Summary (if any):
{existing_summary}

New Messages:
{new_messages}

Updated Summary (2-3 sentences):"""


def _normalize_model_for_provider(model_id: str) -> str:
    """Normalize model ID for OpenRouter provider.

    Ensures model has proper openrouter/ prefix.
    """
    if model_id.startswith("openrouter/"):
        return model_id
    if model_id.startswith("opencode/"):
        return f"openrouter/{model_id[len('opencode/') :]}"
    return f"openrouter/{model_id}"


async def generate_or_update_summary(
    conversation_id: uuid.UUID,
    store: MemoryStore,
) -> str | None:
    """Generate or incrementally update conversation summary.

    Fetches existing summary, summarizes only new messages since last update,
    and updates the conversation record with the new summary.

    The inline post-extraction path is bounded to
    ``INLINE_SUMMARY_BATCH_LIMIT`` messages per call. When the bound is
    reached, the caller (a worker job with a redis handle) is expected to
    enqueue ``generate_summary_job(force=True)`` to drain the remaining
    tail — this avoids silent summarization gaps for backlogged
    conversations.

    Args:
        conversation_id: UUID of conversation to summarize
        store: MemoryStore instance

    Returns:
        New summary string or None if generation failed
    """
    result = await _generate_or_update_summary_result(conversation_id, store)
    return result.summary


@dataclass
class SummaryUpdateResult:
    """Outcome of an inline ``generate_or_update_summary`` call.

    Attributes:
        summary: Newly persisted summary text, or ``None`` if the call
            was a no-op (nothing to summarize) or failed.
        continuation_needed: ``True`` when the inline batch filled its
            bound (``INLINE_SUMMARY_BATCH_LIMIT``) and additional
            finalized messages remain, OR when an optimistic-concurrency
            conflict left finalized messages unsummarized by either
            party. The caller should enqueue
            ``generate_summary_job(force=True)`` to drain the tail.
    """

    summary: str | None
    continuation_needed: bool


async def _generate_or_update_summary_result(
    conversation_id: uuid.UUID,
    store: MemoryStore,
) -> SummaryUpdateResult:
    """Worker-internal entry point that exposes the continuation flag."""
    # Get conversation to check for existing summary and persisted baseline.
    conversation = await store.get_conversation(conversation_id)
    if not conversation:
        return SummaryUpdateResult(summary=None, continuation_needed=False)

    existing_summary = conversation.get("summary") or ""
    summary_updated_at = conversation.get("summary_updated_at")
    current_message_count = await store.count_summary_messages(conversation_id)
    persisted_baseline = validated_summary_baseline(
        conversation,
        current_message_count,
    )
    # Pin the iteration to the moment we started, so concurrent status
    # flips from ``streaming`` to ``complete`` (which do not change
    # ``created_at``) cannot reorder the row set under an in-flight
    # cursor. The next iteration captures a fresh ``now()`` itself.
    iteration_snapshot = datetime.now(timezone.utc)

    # The cursor advances only through rows actually included in past
    # summaries (``persisted_baseline``). ``contiguous_baseline`` is
    # used solely to bound how far this iteration may claim — never as
    # an offset. The prior ``max(persisted_baseline, contiguous_baseline)``
    # offset skipped finalized rows that were contiguous-but-not-yet-
    # summarized (e.g. baseline 0 with rows ``complete m1, streaming m2,
    # complete m3``: offset became 1, so m1 was skipped and m3 was
    # summarized with persisted_baseline advancing to 1; after m2
    # completed, m2 was skipped and m3 was replayed). Using
    # ``persisted_baseline`` directly produces the rows that need
    # summarization without skipping the streaming hole (Codex P2 on
    # PR #165, ``summary.py:147``).
    contiguous_baseline = await store.count_contiguous_finalized_messages_at(
        conversation_id,
        snapshot_at=iteration_snapshot,
    )

    # Keep the prompt bounded and only advance through messages actually included.
    messages = await store.get_summary_message_batch(
        conversation_id,
        offset=persisted_baseline,
        limit=INLINE_SUMMARY_BATCH_LIMIT,
        snapshot_at=iteration_snapshot,
    )
    # Cap the persisted claim at the contiguous-prefix boundary so a
    # later ``streaming -> complete`` transition cannot pull
    # already-claimed rows back into the next batch. The
    # ``get_summary_message_batch`` SQL already filters out non-
    # finalized rows in ``created_at`` order; this ``min`` guards
    # against any future regression that loosens the SQL filter or a
    # race that returns rows beyond the contiguous prefix.
    claimed_message_count = min(
        persisted_baseline + len(messages),
        max(persisted_baseline, contiguous_baseline),
    )
    if not messages:
        return SummaryUpdateResult(
            summary=existing_summary or None,
            continuation_needed=False,
        )

    formatted_messages = "\n\n".join(
        [
            f"[{msg.get('role', 'unknown').upper()}]: {msg.get('content', '')[:500]}"
            for msg in messages
        ]
    )

    # Get settings and provider config
    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")

    # Normalize model for provider
    model = _normalize_model_for_provider(settings.auto_fast_model)

    # Build prompt
    prompt = SUMMARY_PROMPT.format(
        existing_summary=existing_summary if existing_summary else "No previous summary.",
        new_messages=formatted_messages,
    )

    # Build call parameters matching the pattern in main.py
    call_params: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 200,
        "timeout": provider_config.timeout_s,
    }

    # Add provider-specific configuration
    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    # Decide continuation BEFORE persisting so a transient follow-up
    # error (``count_summary_messages_at`` raising) cannot leave the
    # remaining backlog unsummarized (Codex P2 on PR #165). If the batch
    # is full and there are more finalized rows in the contiguous prefix
    # than we just covered, continuation is needed regardless of whether
    # the persist succeeds. ``count_summary_messages_at`` is wrapped in
    # the same ``try`` block as the persist so a transient failure
    # conservatively requests continuation rather than escaping to the
    # caller (Codex P2 on PR #165, ``summary.py:211``).
    batch_was_full = len(messages) >= INLINE_SUMMARY_BATCH_LIMIT
    pre_persist_continuation = False
    finalizing_after: int | None = None

    # Cap the claim at the contiguous-prefix boundary so a later
    # ``streaming -> complete`` transition cannot pull already-claimed
    # rows back into the next batch. With offset=``persisted_baseline``
    # and the batch stopping at the first non-finalized row,
    # ``len(messages)`` is already bounded by ``contiguous_baseline -
    # persisted_baseline``; this min() guards the persist against any
    # future regression that loosens the SQL filter.
    claimed_message_count = min(
        persisted_baseline + len(messages),
        contiguous_baseline,
    )

    try:
        if batch_was_full:
            finalizing_after = await store.count_summary_messages_at(
                conversation_id,
                snapshot_at=iteration_snapshot,
            )
            pre_persist_continuation = finalizing_after > (persisted_baseline + len(messages))

        response = await litellm.acompletion(**call_params)

        # Extract content
        content = _extract_content(response)
        if not content:
            return SummaryUpdateResult(
                summary=None,
                continuation_needed=pre_persist_continuation,
            )

        summary = content.strip()

        # Validate length (should be <= 3 sentences)
        sentences = [s.strip() for s in summary.split(".") if s.strip()]
        if len(sentences) > 3:
            # Truncate to 3 sentences
            summary = ". ".join(sentences[:3]) + "."

        # Persist the summary and finalized-message baseline atomically.
        updated = await store.update_conversation_summary(
            conversation_id,
            summary=summary,
            expected_summary_updated_at=summary_updated_at,
            summarized_message_count=claimed_message_count,
            summary_snapshot_at=iteration_snapshot,
        )
        if not updated:
            # Optimistic-concurrency conflict: another summary invocation
            # committed against the same baseline. The winner's snapshot
            # may predate finalized messages visible to this invocation,
            # so surface continuation unconditionally — the conflict
            # means our batch was not applied and any rows beyond the
            # winner's claim need a retry to drain (Codex P2 on PR
            # #165, ``summary.py:250``).
            return SummaryUpdateResult(
                summary=None,
                continuation_needed=True,
            )

        return SummaryUpdateResult(
            summary=summary,
            continuation_needed=pre_persist_continuation,
        )

    except Exception as e:
        # Log error but don't fail. Preserve the pre-persist continuation
        # signal so a transient litellm or storage error doesn't strand
        # the remaining tail (Codex P2 on PR #165). If the count query
        # itself raised before we could set ``pre_persist_continuation``
        # we cannot know whether the bound was filled; conservatively
        # assume a full batch with unfinished tail so the caller
        # schedules a forced continuation rather than silently dropping
        # the backlog (Codex P2 on PR #165, ``summary.py:211``).
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Summary generation failed for {conversation_id}: {e}")
        return SummaryUpdateResult(
            summary=None,
            continuation_needed=pre_persist_continuation or batch_was_full,
        )


def _extract_content(response: Any) -> str | None:
    """Extract content from litellm response."""
    content: Any = None

    # Try choices first
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        choice0 = choices[0]
        if isinstance(choice0, dict):
            message = choice0.get("message")
            if isinstance(message, dict):
                content = message.get("content")
        else:
            message = getattr(choice0, "message", None)
            if message is not None:
                content = getattr(message, "content", None)

    # Try model_dump
    if content is None:
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            maybe = model_dump()
            if isinstance(maybe, dict):
                choices = maybe.get("choices")
                if isinstance(choices, list) and choices:
                    message = choices[0].get("message") if isinstance(choices[0], dict) else None
                    if isinstance(message, dict):
                        content = message.get("content")

    # Try dict method
    if content is None:
        dict_method = getattr(response, "dict", None)
        if callable(dict_method):
            maybe = dict_method()
            if isinstance(maybe, dict):
                choices = maybe.get("choices")
                if isinstance(choices, list) and choices:
                    message = choices[0].get("message") if isinstance(choices[0], dict) else None
                    if isinstance(message, dict):
                        content = message.get("content")

    return content if isinstance(content, str) else None
