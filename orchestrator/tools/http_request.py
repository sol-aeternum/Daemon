from __future__ import annotations

import json
from typing import Any

import httpx

from orchestrator.tools.registry import Tool


class HttpRequestTool(Tool):
    name = "http_request"
    description = "Make HTTP requests to external APIs and services"
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to request",
            },
            "method": {
                "type": "string",
                "description": "HTTP method",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "HTTP headers to include",
                "default": {},
            },
            "body": {
                "type": "object",
                "description": "Request body (for POST/PUT/PATCH)",
            },
        },
        "required": ["url"],
    }

    async def execute(self, **kwargs: Any) -> str:
        url = kwargs.get("url", "")
        method = kwargs.get("method", "GET").upper()
        headers = kwargs.get("headers", {})
        body = kwargs.get("body")

        if not url:
            return json.dumps({"error": "URL is required"})

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=body if body else None,
                    timeout=30.0,
                )

                try:
                    response_data = response.json()
                except json.JSONDecodeError:
                    response_data = {"text": response.text}

                return json.dumps(
                    {
                        "status": response.status_code,
                        "headers": dict(response.headers),
                        "body": response_data,
                    }
                )

        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
            )
        except Exception as e:
            return json.dumps({"error": f"Request failed: {str(e)}"})
