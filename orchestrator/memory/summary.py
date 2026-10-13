"""Conversation summary generation module for Daemon memory layer.

Incremental summary generation runs on the background inference profile: it is
a short unattended workload, so no model is pinned here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from orchestrator.compute_runtime import guarded_completion

from orchestrator.config import get_settings
from orchestrator.memory.store import MemoryStore
from orchestrator.model_routing import routing_context
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal

SUMMARY_PROFILE = "background"

# Legacy sampling control, kept only for an explicit model pin. The automatic
# background request sends no temperature/top_p, because the approved automatic
# background candidate declares seed-only sampling support.
SUMMARY_TEMPERATURE = 0.3


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
    """Normalize an explicitly injected model ID for the OpenRouter provider.

    Only used by test/benchmark callers that pin a concrete model; deployment
    leaves the model unset and lets the compute guard choose the route.
    """
    if model_id.startswith("openrouter/"):
        return model_id
    if model_id.startswith("opencode/"):
        return f"openrouter/{model_id[len('opencode/') :]}"
    return f"openrouter/{model_id}"


async def generate_or_update_summary(
    conversation_id: uuid.UUID,
    store: MemoryStore,
    model: str | None = None,
    *,
    captured_generation: CapturedGeneration | None = None,
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
        model: Optional explicit model injection for tests/benchmarks only.
            Deployment leaves this ``None`` and lets the background profile
            choose an approved route.

    Returns:
        New summary string or None if generation failed
    """
    result = await _generate_or_update_summary_result(
        conversation_id, store, model=model, captured_generation=captured_generation
    )
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
    *,
    model: str | None = None,
    captured_generation: CapturedGeneration | None = None,
) -> SummaryUpdateResult:
    """Worker-internal entry point that exposes the continuation flag.

    ``model`` is an explicit injection point for tests and benchmark harnesses
    only; deployment leaves it ``None`` and lets the background profile choose
    an approved route.
    """
    if not isinstance(captured_generation, CapturedGeneration):
        return SummaryUpdateResult(summary=None, continuation_needed=False)
    try:
        conversation = await store.read_conversation_summary_inputs(
            conversation_id,
            captured_generation=captured_generation,
            limit=INLINE_SUMMARY_BATCH_LIMIT,
        )
    except Exception:
        # Optional summary failure never discards unrelated extraction work.
        return SummaryUpdateResult(summary=None, continuation_needed=False)
    if not conversation:
        return SummaryUpdateResult(summary=None, continuation_needed=False)

    existing_summary = conversation.get("summary") or ""
    summary_updated_at = conversation.get("summary_updated_at")
    persisted_baseline = conversation["persisted_baseline"]
    iteration_snapshot = conversation["snapshot_at"]
    contiguous_baseline = conversation["contiguous_count"]
    messages = conversation["messages"]
    if not messages:
        if not conversation["has_hole"] and conversation["finalized_count"] <= persisted_baseline:
            try:
                await store.clear_finished_summary_continuation(
                    conversation_id,
                    captured_generation=captured_generation,
                    expected_summary_updated_at=summary_updated_at,
                    expected_baseline=persisted_baseline,
                )
            except Exception:
                return SummaryUpdateResult(summary=None, continuation_needed=False)
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

    # Build prompt
    prompt = SUMMARY_PROMPT.format(
        existing_summary=existing_summary if existing_summary else "No previous summary.",
        new_messages=formatted_messages,
    )

    # Build call parameters matching the pattern in main.py
    call_params: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "timeout": provider_config.timeout_s,
    }
    if model is not None:
        # An explicit pin is a caller-owned choice, so the historical sampling
        # control travels with it. The automatic background call sends none.
        call_params["model"] = _normalize_model_for_provider(model)
        call_params["temperature"] = SUMMARY_TEMPERATURE

    # Add provider-specific configuration
    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    # Decide continuation from the same statement snapshot as the bounded batch,
    # before persisting. No post-write count can strand an unrecorded backlog.
    batch_was_full = len(messages) >= INLINE_SUMMARY_BATCH_LIMIT
    pre_persist_continuation = False

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
        pre_persist_continuation = conversation["finalized_count"] > claimed_message_count

        # Extraction's account_compute admission already happened before this
        # optional call. Recheck after all intervening awaits, before inference.
        valid = await store.check_conversation_summary_generation(
            conversation_id,
            captured_generation=captured_generation,
            expected_summary_updated_at=summary_updated_at,
        )
        if not valid:
            return SummaryUpdateResult(summary=None, continuation_needed=True)

        with routing_context(SUMMARY_PROFILE, preferred_model=model):
            response = await guarded_completion(**call_params)

        # Extract content
        content = _extract_content(response)
        if not content or not content.strip():
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
        # When ``continuation_needed`` will be True, persist the
        # ``summary_continuation_pending`` flag atomically with the
        # summary update so a retry of the extraction job (which may
        # find ``no_messages`` after the watermark advances) can still
        # recover and enqueue the forced-summary continuation
        # (Codex P2 on PR #165, ``worker/jobs.py:295``).
        updated = await store.update_conversation_summary(
            conversation_id,
            summary=summary,
            expected_summary_updated_at=summary_updated_at,
            summarized_message_count=claimed_message_count,
            summary_snapshot_at=iteration_snapshot,
            summary_continuation_pending=pre_persist_continuation or conversation["has_hole"],
            captured_generation=captured_generation,
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

    except WriteGuardRefusal:
        return SummaryUpdateResult(summary=None, continuation_needed=False)
    except Exception:
        # Preserve bounded-backlog recovery on optional model/storage errors,
        # but never convert an account refusal above into continuation authority.
        import logging

        logger = logging.getLogger(__name__)
        logger.warning("Optional summary generation failed")
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
