from __future__ import annotations

"""Guardrails to keep persisted reasoning out of LLM/memory inputs."""

from collections.abc import Mapping, Sequence
from typing import cast


REASONING_FIELDS = frozenset({"reasoning_text", "reasoning_duration_secs"})


def strip_reasoning_fields_from_message(
    message: Mapping[str, object],
) -> dict[str, object]:
    return {k: v for k, v in message.items() if k not in REASONING_FIELDS}


def strip_reasoning_fields_from_messages(messages: Sequence[object]) -> list[object]:
    sanitized: list[object] = []
    for msg in messages:
        if isinstance(msg, Mapping):
            filtered: dict[str, object] = {}
            msg_map = cast(Mapping[object, object], msg)
            for k, v in msg_map.items():
                if isinstance(k, str) and k not in REASONING_FIELDS:
                    filtered[k] = v
            sanitized.append(filtered)
        else:
            sanitized.append(msg)
    return sanitized
