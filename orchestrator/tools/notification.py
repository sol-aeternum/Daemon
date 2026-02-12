from __future__ import annotations

import json
from typing import Any

import httpx

from orchestrator.tools.registry import Tool


class NotificationSendTool(Tool):
    name = "notification_send"
    description = "Send push notifications via ntfy.sh to your devices"
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "ntfy topic/channel name (e.g., 'daemon-alerts')",
            },
            "message": {
                "type": "string",
                "description": "Notification message to send",
            },
            "title": {
                "type": "string",
                "description": "Optional notification title",
            },
            "priority": {
                "type": "string",
                "description": "Priority level",
                "enum": ["min", "low", "default", "high", "urgent"],
                "default": "default",
            },
            "tags": {
                "type": "array",
                "description": "Optional tags/emoji (e.g., ['warning', 'computer'])",
                "items": {"type": "string"},
            },
        },
        "required": ["topic", "message"],
    }

    async def execute(self, **kwargs: Any) -> str:
        topic = kwargs.get("topic", "")
        message = kwargs.get("message", "")
        title = kwargs.get("title")
        priority = kwargs.get("priority", "default")
        tags = kwargs.get("tags", [])

        if not topic or not message:
            return json.dumps({"error": "Topic and message are required"})

        try:
            headers = {
                "Title": title if title else "Daemon Notification",
                "Priority": priority,
            }

            if tags:
                headers["Tags"] = ",".join(tags)

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://ntfy.sh/{topic}",
                    headers=headers,
                    data=message,
                    timeout=10.0,
                )
                response.raise_for_status()

                return json.dumps(
                    {
                        "success": True,
                        "topic": topic,
                        "message": message,
                        "status": "sent",
                    }
                )

        except httpx.HTTPStatusError as e:
            return json.dumps(
                {
                    "error": f"ntfy.sh error: {e.response.status_code} - {e.response.text[:200]}"
                }
            )
        except Exception as e:
            return json.dumps({"error": f"Notification failed: {str(e)}"})
