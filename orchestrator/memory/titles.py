from __future__ import annotations

from orchestrator.compute_runtime import guarded_completion
from orchestrator.model_routing import routing_context

# pyright: reportUnknownMemberType=false

import re
from collections.abc import Sequence
from typing import Any, TypedDict

from orchestrator.memory.completion import (
    EMPTY_RESPONSE_REASON,
    read_completeness,
)

# Title generation is a short unattended background workload, so it is routed
# by the background profile and never pinned to a deployment model ID.
TITLE_PROFILE = "background"

# The automatic background call asks for a whole title but must never silently
# accept a fragment cut off by its own output bound, so it uses the approved
# helper output budget instead of the historic 24-token cap.
AUTOMATIC_TITLE_MAX_TOKENS = 4096

# Legacy sampling control, kept only for an explicit model pin. The automatic
# background request sends no temperature/top_p, because the approved automatic
# background candidate declares seed-only sampling support.
TITLE_TEMPERATURE = 0.1

# Historic explicit-pin cap, preserved for tests and benchmark harnesses.
EXPLICIT_TITLE_MAX_TOKENS = 24


class IncompleteTitleResponse(ValueError):
    """An automatic title call returned an empty or bound-truncated response.

    Raised instead of silently accepting a fragment (or fabricating a fallback)
    so the worker observably retries or reports the failure. The message never
    includes response content.
    """


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
    model: str | None = None,
) -> str:
    """Generate a short conversation title under the background profile.

    ``model`` is an explicit injection point for tests and benchmark harnesses
    only. Deployment passes ``None`` so the compute guard picks a qualified
    route from the background profile; there is no configured model default.

    An automatic call that returns nothing, or returns a fragment cut off by
    its output bound, raises :class:`IncompleteTitleResponse` instead of
    silently persisting an incomplete title.
    """
    excerpt = _prepare_excerpt(messages)
    if not excerpt:
        return "New Conversation"

    call_params: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": "You generate concise conversation titles."},
            {
                "role": "user",
                "content": TITLE_GENERATION_PROMPT.format(messages=excerpt),
            },
        ],
        "max_tokens": AUTOMATIC_TITLE_MAX_TOKENS,
    }
    if model is not None:
        # An explicit pin is a caller-owned choice, so the historical sampling
        # control and output cap travel with it, and the guard honours the
        # exact model inside an automatic account scope. The automatic call
        # sends none of these.
        call_params["model"] = model
        call_params["temperature"] = TITLE_TEMPERATURE
        call_params["max_tokens"] = EXPLICIT_TITLE_MAX_TOKENS
        call_params["_exact_model"] = True

    with routing_context(TITLE_PROFILE, preferred_model=model):
        response = await guarded_completion(**call_params)

    completeness = read_completeness(response)
    if model is None and not completeness.complete:
        # A deployment title job must observably fail rather than silently
        # persisting a fragment or a fabricated fallback. Content-free reason
        # only: the message never carries response text.
        reason = completeness.reason or EMPTY_RESPONSE_REASON
        raise IncompleteTitleResponse(f"Automatic conversation title did not complete: {reason}")
    content = completeness.content
    title = _sanitize_title(content)
    if len(title.split()) < 3:
        fallback_words = re.sub(r"[^A-Za-z0-9\s_-]", "", excerpt).split()
        title = " ".join(fallback_words[:3]).title() or "New Conversation"
    return title
