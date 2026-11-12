"""Reminder note tools. Notes are owned by the authenticated account (#695)."""

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from orchestrator.services.reminders import (
    LEGACY_REMINDERS_FILE,
    ReminderError,
    ReminderStore,
    legacy_file_state,
)
from orchestrator.tools.registry import Tool

logger = logging.getLogger(__name__)

_UNAVAILABLE = "Reminders are unavailable: no authenticated account or database."
_LEGACY_UNRESOLVED = (
    "Reminders are paused: reminder notes saved before account ownership existed are "
    "still on this installation and have no owner. An operator must review and remove "
    "the legacy file before reminders can be used. Tell the user; do not retry."
)


class _ReminderTool(Tool):
    def __init__(
        self,
        store: ReminderStore | None = None,
        user_id: uuid.UUID | None = None,
        legacy_file: Path = LEGACY_REMINDERS_FILE,
    ) -> None:
        # Owner comes from trusted execution context, never from tool arguments.
        self._store = store
        self._user_id = user_id
        self._legacy_file = legacy_file

    def _refusal(self) -> str | None:
        """An error payload when the tool must not run, else None."""
        state = legacy_file_state(self._legacy_file)
        if state in ("pending", "unreadable"):
            # Never an empty success while unowned legacy notes are unresolved.
            logger.warning("legacy_reminders_unresolved state=%s path=%s", state, self._legacy_file)
            return json.dumps({"error": _LEGACY_UNRESOLVED, "legacy_reminders": state})
        if self._store is None or self._user_id is None:
            return json.dumps({"error": _UNAVAILABLE})
        return None


class ReminderSetTool(_ReminderTool):
    name = "reminder_set"
    description = (
        "Save a reminder note for the user. This only stores the note: "
        "no notification is scheduled or sent."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Reminder text/description",
            },
            "due_time": {
                "type": "string",
                "description": "Optional due time (ISO 8601 format, e.g., '2026-02-03T10:00:00Z')",
            },
        },
        "required": ["text"],
    }

    async def execute(self, **kwargs: Any) -> str:
        text = kwargs.get("text", "")
        due_time = kwargs.get("due_time")

        if not text:
            return json.dumps({"error": "Reminder text is required"})
        refusal = self._refusal()
        if refusal is not None or self._store is None or self._user_id is None:
            return refusal or json.dumps({"error": _UNAVAILABLE})

        try:
            reminder = await self._store.add(self._user_id, text, due_time)
        except ReminderError as exc:
            return json.dumps({"error": f"Failed to set reminder: {exc.reason}"})
        except Exception as exc:
            logger.error("reminder_set failed: %s", type(exc).__name__)
            return json.dumps({"error": "Failed to set reminder: storage error"})
        return json.dumps(
            {
                "success": True,
                "reminder": reminder,
                "message": "Saved reminder note. Automatic notifications are not enabled.",
            }
        )


class ReminderListTool(_ReminderTool):
    name = "reminder_list"
    description = "List the user's saved reminder notes"
    parameters = {
        "type": "object",
        "properties": {
            "completed": {
                "type": "boolean",
                "description": "Filter by completion status (null for all)",
            },
        },
    }

    async def execute(self, **kwargs: Any) -> str:
        completed = kwargs.get("completed")
        if completed is not None and not isinstance(completed, bool):
            return json.dumps({"error": "Failed to list reminders: completed must be a boolean"})
        refusal = self._refusal()
        if refusal is not None or self._store is None or self._user_id is None:
            return refusal or json.dumps({"error": _UNAVAILABLE})

        try:
            reminders = await self._store.list(self._user_id, completed)
        except Exception as exc:
            logger.error("reminder_list failed: %s", type(exc).__name__)
            return json.dumps({"error": "Failed to list reminders: storage error"})
        return json.dumps({"count": len(reminders), "reminders": reminders})
