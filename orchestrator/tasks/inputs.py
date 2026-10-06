"""Canonical accepted task input and its request hash (DURABLE_REQUEST_DESIGN §6)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

#: Version of the accepted-input schema stored in ``tasks.input_ciphertext``.
CHAT_INPUT_VERSION = 1
#: Domain separation for the request fingerprint HMAC.
_REQUEST_HASH_DOMAIN = b"daemon.task.request.v1\n"


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, UTF-8 kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chat_request_hash(
    *,
    key: str,
    conversation_id: uuid.UUID | None,
    message: str,
    attachments: list[dict[str, Any]] | None,
    model: str | None,
    provider: str | None,
    metadata: dict[str, Any] | None,
    disable_memory_write: bool,
) -> str:
    """Keyed fingerprint (HMAC-SHA-256) of what a submission *asks for*.

    Keyed with a server secret so a database snapshot cannot be used to test
    guesses of the otherwise encrypted input. Client-held history
    (``messages``) is deliberately excluded: a client that reloads and
    resubmits with the same idempotency key may send different history for
    the same request, and must get the same task back.
    """
    if not key:
        raise ValueError("request fingerprint key is required")
    payload = {
        "conversation_id": str(conversation_id) if conversation_id is not None else None,
        "message": message,
        "attachments": attachments or [],
        "model": model or "auto",
        "provider": provider,
        "metadata": metadata or {},
        "disable_memory_write": bool(disable_memory_write),
    }
    return hmac.new(
        key.encode("utf-8"),
        _REQUEST_HASH_DOMAIN + canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
