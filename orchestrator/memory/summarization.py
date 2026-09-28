"""Conversation summarization module for Daemon memory layer."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from orchestrator.compute_runtime import guarded_completion

from orchestrator.memory.store import MemoryStore
from orchestrator.model_routing import routing_context

SUMMARY_PROFILE = "background"

# Legacy sampling default, kept only for an explicit ``summary_model`` pin. The
# automatic background request sends no temperature/top_p, because the approved
# automatic background candidate declares seed-only sampling support.
SUMMARY_TEMPERATURE = 0.3


SUMMARIZATION_PROMPT = """
Create a concise summary of this conversation. 

Guidelines:
- 2-5 sentences capturing the main topics discussed
- Focus on decisions made and key information exchanged
- End with "Open: [comma-separated items]" or "Open: none"
- If previous summary exists, incorporate it as context

Previous Context (if any):
{previous_summary}

Conversation:
{messages}
"""


async def generate_summary(
    messages: list[dict[str, Any]],
    previous_summary: str | None = None,
    settings: dict[str, Any] | None = None,
) -> str:
    """Generate a conversation summary on the background inference profile.

    Args:
        messages: List of message dicts with role/content
        previous_summary: Optional previous summary to incorporate
        settings: Optional settings dict. ``summary_model`` is an explicit
            test/benchmark injection point; deployment leaves it unset so the
            compute guard picks an approved background route.

    Returns:
        2-5 sentence summary ending with "Open: ..."
    """
    # Format messages for prompt
    formatted_messages = "\n\n".join(
        [f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}" for msg in messages]
    )

    # Build prompt
    prompt = SUMMARIZATION_PROMPT.format(
        previous_summary=previous_summary or "None", messages=formatted_messages
    )

    settings = settings or {}
    model = settings.get("summary_model")
    max_tokens = settings.get("summary_max_tokens", 300)

    call_params: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if model is not None:
        # An explicit pin is a caller-owned choice, so the historical sampling
        # controls travel with it. The automatic background call sends none,
        # because the approved automatic background candidate declares seed-only
        # sampling support.
        call_params["model"] = model
        call_params["temperature"] = settings.get("summary_temperature", SUMMARY_TEMPERATURE)

    with routing_context(SUMMARY_PROFILE, preferred_model=model):
        response = await guarded_completion(**call_params)

    content: Any = None

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

    response_data: Any = None
    model_dump = getattr(response, "model_dump", None)
    if content is None and callable(model_dump):
        maybe = model_dump()
        if isinstance(maybe, dict):
            response_data = maybe

    dict_method = getattr(response, "dict", None)
    if content is None and response_data is None and callable(dict_method):
        maybe = dict_method()
        if isinstance(maybe, dict):
            response_data = maybe

    if content is None and isinstance(response_data, dict):
        choices = response_data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = message.get("content")

    if not isinstance(content, str):
        return ""
    return content.strip()


def validated_summary_baseline(
    conversation: dict[str, Any],
    current_message_count: int,
) -> int:
    """Return a safe persisted summary baseline, replaying invalid legacy state.

    The earlier snapshot timestamp accessor has been removed: the cursor
    no longer needs a persisted upper bound because each iteration pins
    its own ``now()`` snapshot when fetching the batch. The persisted
    ``last_summarized_at_time`` is still written for audit / debugging
    but is not consulted here.
    """
    metadata = conversation.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    previous_summary = conversation.get("summary")
    has_existing_summary = isinstance(previous_summary, str) and bool(previous_summary.strip())
    raw_baseline = metadata.get("last_summarized_msg_count") if isinstance(metadata, dict) else None

    if (
        isinstance(raw_baseline, int)
        and not isinstance(raw_baseline, bool)
        and 0 <= raw_baseline <= current_message_count
        and (raw_baseline == 0 or has_existing_summary)
    ):
        return raw_baseline

    # Replaying is safer than guessing a cursor and skipping unseen messages.
    return 0


async def should_summarize(
    conversation_id: uuid.UUID,
    last_summary_time: datetime | None,
    last_summarized_msg_count: int,
    store: MemoryStore,
    settings: dict[str, Any] | None = None,
) -> bool:
    """Check if conversation should be summarized.

    Returns True if:
    - Conversation idle > summary_idle_minutes (default 30)
    - Token count since last summary > summary_token_threshold (default 15K)

    Args:
        conversation_id: UUID of conversation
        last_summary_time: When last summary was generated
        last_summarized_msg_count: Number of finalized messages in the previous summary
        store: MemoryStore instance
        settings: Settings dict with summary_idle_minutes, summary_token_threshold

    Returns:
        True if should summarize
    """
    settings = settings or {}
    idle_minutes = settings.get("summary_idle_minutes", 15)
    summary_message_delta = settings.get("summary_message_delta", 20)
    idle_delta = 6

    current_msg_count = await store.count_summary_messages(conversation_id)
    delta = max(0, int(current_msg_count) - int(last_summarized_msg_count))
    if delta >= int(summary_message_delta):
        return True

    if delta >= idle_delta and last_summary_time:
        idle_time = datetime.now(timezone.utc) - last_summary_time
        if idle_time >= timedelta(minutes=idle_minutes):
            return True

    return False
