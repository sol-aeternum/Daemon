"""Value-based cursors for UI history reads, not model context or extraction.

Tokens are opaque transport values, not capabilities. Every store query must
still apply ownership predicates. No anchor lookup is needed after deletion.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid


MAX_CURSOR_LENGTH = 1024


class InvalidHistoryCursor(ValueError):
    """Malformed, unsupported or incorrectly scoped UI cursor."""


@dataclass(frozen=True)
class HistoryBoundary:
    at: datetime
    id: uuid.UUID
    pinned: bool | None = None


def encode_cursor(
    boundary: HistoryBoundary,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
) -> str:
    if boundary.at.tzinfo is None or boundary.at.utcoffset() is None:
        raise ValueError("History boundaries require timezone-aware timestamps")
    payload = {
        "v": 1,
        "owner": str(user_id),
        "scope": str(conversation_id) if conversation_id is not None else "conversations",
        "at": boundary.at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        "id": str(boundary.id),
        "pinned": boundary.pinned,
    }
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(
    token: str,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
) -> HistoryBoundary:
    try:
        if not token or len(token) > MAX_CURSOR_LENGTH or not token.isascii():
            raise ValueError
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "v",
            "owner",
            "scope",
            "at",
            "id",
            "pinned",
        }:
            raise ValueError
        if type(payload["v"]) is not int or payload["v"] != 1:
            raise ValueError
        scope = str(conversation_id) if conversation_id is not None else "conversations"
        if payload["owner"] != str(user_id) or payload["scope"] != scope:
            raise ValueError
        if conversation_id is None:
            if type(payload["pinned"]) is not bool:
                raise ValueError
        elif payload["pinned"] is not None:
            raise ValueError
        if not isinstance(payload["at"], str) or not isinstance(payload["id"], str):
            raise ValueError
        at = datetime.fromisoformat(payload["at"])
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError
        return HistoryBoundary(
            at.astimezone(timezone.utc), uuid.UUID(payload["id"]), payload["pinned"]
        )
    except (ValueError, TypeError, KeyError, binascii.Error, OverflowError) as exc:
        raise InvalidHistoryCursor("Invalid history cursor") from exc
