from __future__ import annotations

from typing import Any
from typing_extensions import override
import json

from orchestrator.tools.registry import Tool
from orchestrator.services.fetch.service import FetchService


class WebFetchTool(Tool):
    name: str = "web_fetch"
    description: str = "Fetch content from a URL using multiple strategies (direct, Jina, Crawl4AI, Archive.org)"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch content from",
            },
            "extract": {
                "type": "string",
                "description": "Content extraction type: article, transcript, or metadata",
                "default": "article",
                "enum": [
                    "article",
                    "transcript",
                    "metadata",
                    "text",
                    "markdown",
                ],
            },
            "force_refresh": {
                "type": "boolean",
                "description": "Bypass cache and force fresh fetch",
                "default": False,
            },
        },
        "required": ["url"],
    }

    def __init__(self) -> None:
        self._fetch_service: FetchService | None = None

    @staticmethod
    def _normalize_extract_mode(extract: Any) -> str:
        value = str(extract or "article").strip().lower()
        aliases = {
            "text": "article",
            "markdown": "article",
        }
        normalized = aliases.get(value, value)
        allowed = {"article", "transcript", "metadata"}
        return normalized if normalized in allowed else "article"

    @override
    async def execute(self, **kwargs: Any) -> str:
        url: str = kwargs.get("url", "")
        extract: str = self._normalize_extract_mode(kwargs.get("extract", "article"))
        force_refresh: bool = kwargs.get("force_refresh", False)

        if not url:
            return json.dumps({"error": "URL parameter is required"})

        if self._fetch_service is None:
            self._fetch_service = FetchService()

        try:
            result = await self._fetch_service.fetch(
                url=url, extract=extract, force_refresh=force_refresh
            )

            if result is None:
                return json.dumps({"error": "Failed to fetch content from URL"})

            return json.dumps(
                {
                    "url": result.url,
                    "content": result.content,
                    "strategy_used": result.strategy_used,
                    "content_length": result.content_length,
                    "fetch_time_ms": result.fetch_time_ms,
                }
            )
        except Exception as e:
            return json.dumps({"error": f"Fetch failed: {str(e)}"})
