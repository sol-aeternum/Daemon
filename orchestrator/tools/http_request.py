from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from orchestrator.tools.registry import Tool
from orchestrator.tools.ssrf_guard import (
    SsrfViolation,
    check_egress_allowlist,
    socket_guard,
    validate_url,
)

# Host header overrides are stripped (case-insensitively — HTTP header names
# are case-insensitive): the resolved host is what httpx will connect to, and
# a model-supplied "Host" header would let it lie about its destination
# (e.g. trick upstream proxies or virtual-host routers).
_STRIPPED_HEADERS = frozenset({"host"})


class HttpRequestTool(Tool):
    name = "http_request"
    description = (
        "Make HTTPS requests to external APIs and services. "
        "Outbound requests are restricted to public, non-loopback hosts on "
        "port 443 and do not follow redirects."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTPS URL to request (port 443 only).",
            },
            "method": {
                "type": "string",
                "description": "HTTP method",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "HTTP headers to include (Host header is stripped).",
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
        headers = kwargs.get("headers") or {}
        body = kwargs.get("body")

        if not url:
            return json.dumps({"error": "URL is required"})

        try:
            # Allowlist first: a non-allowlisted host must be rejected before
            # validate_url() resolves it, so attacker-chosen hostnames cannot
            # trigger DNS lookups (information leak / DNS-based exfiltration).
            try:
                hostname = urlparse(url).hostname or ""
            except ValueError as exc:
                return json.dumps({"error": f"SSRF blocked: malformed URL: {exc}"})
            check_egress_allowlist(hostname)
            # Preflight validation resolves DNS synchronously; run it in a
            # worker thread with a bounded timeout so a slow resolver cannot
            # stall the event loop (and every concurrent chat/SSE stream).
            await asyncio.wait_for(asyncio.to_thread(validate_url, url), timeout=10.0)
        except TimeoutError:
            return json.dumps({"error": "SSRF blocked: DNS resolution timed out"})
        except SsrfViolation as exc:
            return json.dumps({"error": f"SSRF blocked: {exc}"})

        safe_headers = {k: v for k, v in headers.items() if k.lower() not in _STRIPPED_HEADERS}

        try:
            with socket_guard():
                # trust_env=False: environment proxies (HTTPS_PROXY/ALL_PROXY)
                # would move destination resolution to the proxy, bypassing
                # the connect-time DNS-rebinding guard below.
                async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=safe_headers,
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

        except SsrfViolation as exc:
            return json.dumps({"error": f"SSRF blocked: {exc}"})
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"})
        except Exception as e:
            return json.dumps({"error": f"Request failed: {str(e)}"})
