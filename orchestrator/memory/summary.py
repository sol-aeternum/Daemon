"""Conversation summary generation module for Daemon memory layer.

Implements incremental summary generation using auto_fast_model.
"""

from __future__ import annotations

import uuid
from typing import Any

import litellm

from orchestrator.config import get_settings
from orchestrator.memory.store import MemoryStore


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
        return f"openrouter/{model_id[len('opencode/'):]}"
    return f"openrouter/{model_id}"


async def generate_or_update_summary(
    conversation_id: uuid.UUID,
    store: MemoryStore,
) -> str | None:
    """Generate or incrementally update conversation summary.

    Fetches existing summary, summarizes only new messages since last update,
    and updates the conversation record with the new summary.

    Args:
        conversation_id: UUID of conversation to summarize
        store: MemoryStore instance

    Returns:
        New summary string or None if generation failed
    """
    from datetime import datetime

    # Get conversation to check for existing summary
    conversation = await store.get_conversation(conversation_id)
    if not conversation:
        return None

    existing_summary = conversation.get("summary") or ""
    summary_updated_at = conversation.get("summary_updated_at")

    # Fetch messages - incremental if we have a previous summary with timestamp
    if existing_summary and summary_updated_at and isinstance(summary_updated_at, datetime):
        # Incremental: fetch only messages since last summary update
        messages = await store.get_messages(
            conversation_id,
            limit=100,
            created_after=summary_updated_at,
        )
        if not messages:
            # No new messages since last summary
            return existing_summary
        is_incremental = True
    else:
        # No existing summary: fetch all messages for initial summary
        messages = await store.get_messages(conversation_id, limit=100)
        if not messages:
            return None
        is_incremental = False

    # Format messages for prompt (last 20 for context window)
    formatted_messages = "\n\n".join(
        [
            f"[{msg.get('role', 'unknown').upper()}]: {msg.get('content', '')[:500]}"
            for msg in messages[-20:]
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

    try:
        response = await litellm.acompletion(**call_params)

        # Extract content
        content = _extract_content(response)
        if not content:
            return None

        summary = content.strip()

        # Validate length (should be <= 3 sentences)
        sentences = [s.strip() for s in summary.split(".") if s.strip()]
        if len(sentences) > 3:
            # Truncate to 3 sentences
            summary = ". ".join(sentences[:3]) + "."

        # Update conversation with new summary
        await store.update_conversation(
            conversation_id=conversation_id,
            summary=summary,
        )

        return summary

    except Exception as e:
        # Log error but don't fail
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Summary generation failed for {conversation_id}: {e}")
        return None


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
