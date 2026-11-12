"""Account-owned reminder notes (#695).

Every read and write takes the owning ``user_id`` from trusted execution
context; there is no unscoped query and no file fallback. Text and due time are
encrypted at rest in one Fernet envelope. These are saved notes only: nothing
here schedules or sends a notification.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from orchestrator.config import Settings, get_settings
from orchestrator.memory.encryption import ContentEncryption

MAX_TEXT_CHARS = 2000
MAX_DUE_TIME_CHARS = 100
MAX_REMINDERS_PER_ACCOUNT = 500


class ReminderError(Exception):
    """A refusal with a stable, content-free reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReminderStore:
    def __init__(self, pool: asyncpg.Pool, encryption: ContentEncryption) -> None:
        self._pool = pool
        self._enc = encryption

    async def add(
        self, user_id: uuid.UUID, text: str, due_time: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ReminderError("text_required")
        if len(text) > MAX_TEXT_CHARS:
            raise ReminderError("text_too_long")
        if due_time is not None and (
            not isinstance(due_time, str) or len(due_time) > MAX_DUE_TIME_CHARS
        ):
            raise ReminderError("due_time_invalid")

        reminder_id = uuid.uuid4()
        # Encrypt before taking the account lock.
        envelope = self._enc.encrypt(json.dumps({"text": text, "due_time": due_time}))
        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    # Same per-account serialization point as web snapshots, so the
                    # count limit holds across backend and worker processes.
                    found = await conn.fetchval(
                        "SELECT id FROM users WHERE id = $1 FOR NO KEY UPDATE", user_id
                    )
                    if found is None:
                        raise ReminderError("account_unavailable")
                    count = await conn.fetchval(
                        "SELECT count(*) FROM reminders WHERE user_id = $1", user_id
                    )
                    if count >= MAX_REMINDERS_PER_ACCOUNT:
                        raise ReminderError("limit_reached")
                    created_at = await conn.fetchval(
                        "INSERT INTO reminders (id, user_id, content_encrypted) "
                        "VALUES ($1, $2, $3) RETURNING created_at",
                        reminder_id,
                        user_id,
                        envelope,
                    )
        except asyncpg.ForeignKeyViolationError as exc:
            raise ReminderError("account_unavailable") from exc
        return {
            "id": str(reminder_id),
            "text": text,
            "due_time": due_time,
            "created_at": created_at.isoformat(),
            "completed": False,
        }

    async def list(self, user_id: uuid.UUID, completed: bool | None = None) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, content_encrypted, completed, created_at FROM reminders "
                "WHERE user_id = $1 AND ($2::boolean IS NULL OR completed = $2) "
                "ORDER BY created_at, id",
                user_id,
                completed,
            )
        reminders = []
        for row in rows:
            # A wrong key or corrupt envelope raises: never an empty or partial list.
            document = json.loads(self._enc.decrypt(row["content_encrypted"]))
            reminders.append(
                {
                    "id": str(row["id"]),
                    "text": document["text"],
                    "due_time": document.get("due_time"),
                    "created_at": row["created_at"].isoformat(),
                    "completed": row["completed"],
                }
            )
        return reminders


def create_reminder_store(pool: asyncpg.Pool, settings: Settings | None = None) -> ReminderStore:
    """Build a store from the shared pool; a deployment with no key fails closed."""
    resolved = settings if settings is not None else get_settings()
    return ReminderStore(pool, ContentEncryption(resolved.daemon_encryption_key))
