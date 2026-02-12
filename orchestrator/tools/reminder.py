from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

from orchestrator.tools.registry import Tool


class ReminderStorage:
    """Simple JSON file storage for reminders."""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reminders_file = self.data_dir / "reminders.json"
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not self.reminders_file.exists():
            self.reminders_file.write_text(json.dumps({"reminders": []}))

    def _load(self) -> dict:
        try:
            return json.loads(self.reminders_file.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            return {"reminders": []}

    def _save(self, data: dict):
        self.reminders_file.write_text(json.dumps(data, indent=2))

    def add(self, text: str, due_time: str | None = None) -> dict:
        data = self._load()
        reminder = {
            "id": len(data["reminders"]) + 1,
            "text": text,
            "due_time": due_time,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed": False,
        }
        data["reminders"].append(reminder)
        self._save(data)
        return reminder

    def list(self, completed: bool | None = None) -> list[dict]:
        data = self._load()
        reminders = data["reminders"]
        if completed is not None:
            reminders = [r for r in reminders if r["completed"] == completed]
        return sorted(reminders, key=lambda x: x["created_at"], reverse=True)

    def complete(self, reminder_id: int) -> dict | None:
        data = self._load()
        for r in data["reminders"]:
            if r["id"] == reminder_id:
                r["completed"] = True
                r["completed_at"] = datetime.now(timezone.utc).isoformat()
                self._save(data)
                return r
        return None


class ReminderSetTool(Tool):
    name = "reminder_set"
    description = "Set a reminder for future reference"
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

    def __init__(self, storage: ReminderStorage | None = None) -> None:
        self.storage = storage or ReminderStorage()

    async def execute(self, **kwargs: Any) -> str:
        text = kwargs.get("text", "")
        due_time = kwargs.get("due_time")

        if not text:
            return json.dumps({"error": "Reminder text is required"})

        try:
            reminder = self.storage.add(text, due_time)
            return json.dumps(
                {
                    "success": True,
                    "reminder": reminder,
                    "message": f"Reminder set: {text}",
                }
            )
        except Exception as e:
            return json.dumps({"error": f"Failed to set reminder: {str(e)}"})


class ReminderListTool(Tool):
    name = "reminder_list"
    description = "List all reminders"
    parameters = {
        "type": "object",
        "properties": {
            "completed": {
                "type": "boolean",
                "description": "Filter by completion status (null for all)",
            },
        },
    }

    def __init__(self, storage: ReminderStorage | None = None) -> None:
        self.storage = storage or ReminderStorage()

    async def execute(self, **kwargs: Any) -> str:
        completed = kwargs.get("completed")

        try:
            reminders = self.storage.list(completed)
            return json.dumps(
                {
                    "count": len(reminders),
                    "reminders": reminders,
                }
            )
        except Exception as e:
            return json.dumps({"error": f"Failed to list reminders: {str(e)}"})
