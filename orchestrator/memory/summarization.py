"""Conversation summarization module for Daemon memory layer."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import litellm

from orchestrator.memory.store import MemoryStore


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
    """Generate conversation summary using GPT-4o-mini.

    Args:
        messages: List of message dicts with role/content
        previous_summary: Optional previous summary to incorporate
        settings: Optional settings dict

    Returns:
        2-5 sentence summary ending with "Open: ..."
    """
    # Format messages for prompt
    formatted_messages = "\n\n".join(
        [
            f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}"
            for msg in messages
        ]
    )

    # Build prompt
    prompt = SUMMARIZATION_PROMPT.format(
        previous_summary=previous_summary or "None", messages=formatted_messages
    )

    settings = settings or {}
    model = settings.get("summary_model", "openrouter/openai/gpt-4o-mini")
    temperature = settings.get("summary_temperature", 0.3)
    max_tokens = settings.get("summary_max_tokens", 300)

    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )

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
            message = (
                choices[0].get("message") if isinstance(choices[0], dict) else None
            )
            if isinstance(message, dict):
                content = message.get("content")

    if not isinstance(content, str):
        return ""
    return content.strip()


async def should_summarize(
    conversation_id: uuid.UUID,
    last_summary_time: datetime | None,
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
        store: MemoryStore instance
        settings: Settings dict with summary_idle_minutes, summary_token_threshold

    Returns:
        True if should summarize
    """
    settings = settings or {}
    idle_minutes = settings.get("summary_idle_minutes", 30)
    token_threshold = settings.get("summary_token_threshold", 15000)

    # Check idle time
    if last_summary_time:
        idle_time = datetime.now(timezone.utc) - last_summary_time
        if idle_time < timedelta(minutes=idle_minutes):
            return False

    # Check token count since last summary
    messages = await store.get_messages(conversation_id, limit=1000)
    total_tokens = sum(len(msg.get("content", "")) // 4 for msg in messages)

    return total_tokens >= token_threshold
