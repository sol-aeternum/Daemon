"""Reminder note tools. Notes are owned by the authenticated account (#695)."""

import json
import logging
import uuid
from typing import Any

from orchestrator.services.reminders import ReminderError, ReminderStore
from orchestrator.tools.registry import Tool

logger = logging.getLogger(__name__)

_UNAVAILABLE = "Reminders are unavailable: no authenticated account or database."


class _ReminderTool(Tool):
    def __init__(
        self, store: ReminderStore | None = None, user_id: uuid.UUID | None = None
    ) -> None:
        # Owner comes from trusted execution context, never from tool arguments.
        self._store = store
        self._user_id = user_id


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
        if self._store is None or self._user_id is None:
            return json.dumps({"error": _UNAVAILABLE})

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
        if self._store is None or self._user_id is None:
            return json.dumps({"error": _UNAVAILABLE})

        try:
            reminders = await self._store.list(self._user_id, completed)
        except Exception as exc:
            logger.error("reminder_list failed: %s", type(exc).__name__)
            return json.dumps({"error": "Failed to list reminders: storage error"})
        return json.dumps({"count": len(reminders), "reminders": reminders})
