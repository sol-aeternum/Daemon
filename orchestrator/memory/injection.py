from __future__ import annotations

import asyncio
import uuid
from typing import cast

from orchestrator.guardrails import strip_reasoning_fields_from_message
from orchestrator.memory.embedding import embed_text
from orchestrator.memory.retrieval import retrieve_memories
from orchestrator.memory.store import MemoryStore
from orchestrator.prompts import DAEMON_SYSTEM_PROMPT

MAX_MEMORY_ITEMS = 5
MAX_SUMMARY_ITEMS = 3
DEFAULT_MAX_TOKENS = 2500
MAX_SINGLE_MEMORY_CHARS = 400

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

    query_text = ""
    recent_messages = await store.get_recent_messages(conversation_id, limit=20)

    recent_messages = [strip_reasoning_fields_from_message(m) for m in recent_messages]

    for message in reversed(recent_messages):
        if str(message.get("role") or "").lower() == "user":
            query_text = _normalize_content(message.get("content"))
            if query_text:
                break

    if not query_text and recent_messages:
        query_text = _normalize_content(recent_messages[-1].get("content"))

    retrieved: list[dict[str, object]] = []
    summaries_task = asyncio.create_task(
        store.get_recent_summaries(user_id, limit=MAX_SUMMARY_ITEMS)
    )

    if query_text:
        try:
            query_embedding = await asyncio.wait_for(
                embed_text(query_text), timeout=8.0
            )
            retrieved = await retrieve_memories(
                store=store,
                query_embedding=query_embedding,
                conversation_id=conversation_id,
                limit=MAX_MEMORY_ITEMS,
            )
        except Exception:
            retrieved = []

    summaries = await summaries_task

    memory_lines: list[str] = []
    for memory in retrieved[:MAX_MEMORY_ITEMS]:
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

    summary_lines: list[str] = []
    for summary in summaries[:MAX_SUMMARY_ITEMS]:
        text = _normalize_content(summary.get("content"))
        if text:
            summary_lines.append(f"- Session: {text}")

    if not memory_lines and not summary_lines:
        return ""

    effective_token_budget = max(1, max_tokens)

    def render(memories: list[str], summary_items: list[str]) -> str:
        parts = ["About this user:"]
        if memories:
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
    summary_lines = truncate_items(summary_lines, MAX_SINGLE_MEMORY_CHARS)

    context = render(memory_lines, summary_lines)

    while estimate_tokens(context) > effective_token_budget and memory_lines:
        _ = memory_lines.pop()
        context = render(memory_lines, summary_lines)

    while estimate_tokens(context) > effective_token_budget and summary_lines:
        _ = summary_lines.pop()
        context = render(memory_lines, summary_lines)

    return context


async def assemble_system_prompt(
    memory_context: str,
    preferences_block: str | None = None,
    conversation_id: uuid.UUID | None = None,
) -> str:
    del conversation_id

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
