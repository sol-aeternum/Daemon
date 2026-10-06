"""Canonical accepted task input and its request hash (DURABLE_REQUEST_DESIGN §6)."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

#: Version of the accepted-input schema stored in ``tasks.input_ciphertext``.
CHAT_INPUT_VERSION = 1


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, UTF-8 kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chat_request_hash(
    *,
    conversation_id: uuid.UUID | None,
    message: str,
    attachments: list[dict[str, Any]] | None,
    model: str | None,
    provider: str | None,
    metadata: dict[str, Any] | None,
    disable_memory_write: bool,
) -> str:
    """SHA-256 over what a submission *asks for*.

    Client-held history (``messages``) is deliberately excluded: a client that
    reloads and resubmits with the same idempotency key may send different
    history for the same request, and must get the same task back.
    """
    payload = {
        "conversation_id": str(conversation_id) if conversation_id is not None else None,
        "message": message,
        "attachments": attachments or [],
        "model": model or "auto",
        "provider": provider,
        "metadata": metadata or {},
        "disable_memory_write": bool(disable_memory_write),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
