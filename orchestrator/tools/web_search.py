from __future__ import annotations

import json
import os
from typing import Any

import httpx

from orchestrator.tools.registry import Tool


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for current information using Brave Search API"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query to find information on the web",
            },
            "num_results": {
                "type": "integer",
                "description": "Number of search results to return (1-10)",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    }

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY")
        self.base_url = "https://api.search.brave.com/res/v1/web/search"

    async def execute(self, **kwargs: Any) -> str:
        query = kwargs.get("query", "")
        num_results = min(10, max(1, kwargs.get("num_results", 5)))

        if not self.api_key:
            return json.dumps(
                {"error": "BRAVE_API_KEY not configured. Add it to your .env file."}
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.base_url,
                    headers={
                        "X-Subscription-Token": self.api_key,
                        "Accept": "application/json",
                    },
                    params={
                        "q": query,
                        "count": num_results,
                        "offset": 0,
                        "safesearch": "moderate",
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                results = []
                web_results = data.get("web", {}).get("results", [])
                for result in web_results[:num_results]:
                    results.append(
                        {
                            "title": result.get("title", "No title"),
                            "url": result.get("url", ""),
                            "description": result.get("description", "")[:300],
                        }
                    )

                return json.dumps(
                    {
                        "query": query,
                        "results": results,
                        "total_found": len(results),
                    }
                )

        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"Search API error: {e.response.status_code}"})
        except Exception as e:
            return json.dumps({"error": f"Search failed: {str(e)}"})
