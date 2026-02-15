from __future__ import annotations

# pyright: reportUnknownMemberType=false

import re
from collections.abc import Sequence
from typing import Protocol, TypedDict, cast

import litellm

TITLE_GENERATION_PROMPT = """
Generate a concise 3-5 word title for this conversation.

Guidelines:
- Use Title Case
- Capture the main topic
- No quotes, no punctuation at end
- If code-related, include language or framework name

Conversation:
{messages}
"""


class ConversationMessage(TypedDict):
    role: str
    content: str


class _CompletionMessage(Protocol):
    content: str | None


class _CompletionChoice(Protocol):
    message: _CompletionMessage


class _CompletionResponse(Protocol):
    choices: list[_CompletionChoice]


def _prepare_excerpt(messages: Sequence[ConversationMessage]) -> str:
    lines: list[str] = []
    for msg in messages[:6]:
        role = str(msg.get("role", "user")).strip().lower()
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        speaker = "User" if role == "user" else "Assistant"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)[:4000]


def _sanitize_title(text: str) -> str:
    cleaned = text.strip().strip("\"'`")
    cleaned = re.sub(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF]", "", cleaned)
    cleaned = re.sub(r"[.!?:;,\s]+$", "", cleaned)
    words = cleaned.split()
    if len(words) > 5:
        words = words[:5]
    title = " ".join(words).strip()
    return title.title()


async def generate_conversation_title(
    messages: Sequence[ConversationMessage],
    model: str = "openrouter/openai/gpt-4o-mini",
) -> str:
    excerpt = _prepare_excerpt(messages)
    if not excerpt:
        return "New Conversation"

    response = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": "You generate concise conversation titles."},
            {
                "role": "user",
                "content": TITLE_GENERATION_PROMPT.format(messages=excerpt),
            },
        ],
        temperature=0.1,
        max_tokens=24,
    )
    typed_response = cast(_CompletionResponse, cast(object, response))
    content = typed_response.choices[0].message.content or ""
    title = _sanitize_title(content)
    if len(title.split()) < 3:
        fallback_words = re.sub(r"[^A-Za-z0-9\s_-]", "", excerpt).split()
        title = " ".join(fallback_words[:3]).title() or "New Conversation"
    return title
