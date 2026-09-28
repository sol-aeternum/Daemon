"""Shared completeness checking for bounded, unattended helper completions.

Helpers that cap their own output (titles, contradiction verdicts, entity
confirmations) must never mistake a bound-truncated fragment for a valid
answer: a visible ``YES`` prefix cut off by ``finish_reason="length"`` is not a
confirmation. This module is the one small, dependency-free place that reads a
litellm-shaped response's content and finish reason, so every helper applies
the same truncation rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The litellm finish reason that means generation stopped at the output bound.
#: Any visible content accompanying it is a fragment, never a verdict.
TRUNCATED_FINISH_REASON = "length"

#: Stable, content-free reasons a helper can log or persist. They deliberately
#: never embed response text: helper failure reasons reach logs and metadata.
EMPTY_RESPONSE_REASON = "empty response"
TRUNCATED_RESPONSE_REASON = "response truncated by the output bound"


@dataclass(frozen=True)
class CompletionCompleteness:
    """The visible content of a completion, and why it may not be complete.

    ``reason`` is ``None`` exactly when the response is complete: non-empty
    content that was not cut off by the output bound. Otherwise it is one of
    the stable content-free reason strings above.
    """

    content: str
    reason: str | None

    @property
    def complete(self) -> bool:
        return self.reason is None


def _choice_fields(response: Any) -> tuple[Any, Any]:
    """Return ``(choice, message)`` from the first choice, either access style."""
    choices = (
        response.get("choices")
        if isinstance(response, dict)
        else getattr(response, "choices", None)
    )
    if not isinstance(choices, list) or not choices:
        dump = getattr(response, "model_dump", None)
        if not callable(dump):
            dump = getattr(response, "dict", None)
        if callable(dump):
            try:
                dumped = dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                choices = dumped.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, None
    choice = choices[0]
    if isinstance(choice, dict):
        return choice, choice.get("message")
    return choice, getattr(choice, "message", None)


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    return content if isinstance(content, str) else ""


def _choice_finish_reason(choice: Any) -> str:
    if isinstance(choice, dict):
        value = choice.get("finish_reason")
    else:
        value = getattr(choice, "finish_reason", None)
    return value if isinstance(value, str) else ""


def read_completeness(response: Any) -> CompletionCompleteness:
    """Read one completion's content and completeness from a litellm response.

    Handles both attribute-style SDK objects and their ``model_dump()``/``dict()``
    forms, matching how the helper modules already unpack responses.
    """
    choice, message = _choice_fields(response)
    content = _message_content(message)
    if choice is None:
        return CompletionCompleteness(content=content, reason=EMPTY_RESPONSE_REASON)
    if _choice_finish_reason(choice) == TRUNCATED_FINISH_REASON:
        # Truncation wins over emptiness: the bound cut the response, so the
        # visible prefix (if any) must not be accepted as a verdict.
        return CompletionCompleteness(content=content, reason=TRUNCATED_RESPONSE_REASON)
    if not content.strip():
        return CompletionCompleteness(content=content, reason=EMPTY_RESPONSE_REASON)
    return CompletionCompleteness(content=content, reason=None)
