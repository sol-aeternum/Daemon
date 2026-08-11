"""Streaming request-body size limits for the HTTP API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_BODY_TOO_LARGE_RESPONSES: dict[int | str, dict[str, Any]] = {
    413: {"description": "Request body exceeds the configured size limit."}
}


class _RequestBodyTooLarge(Exception):
    """Internal signal raised before a downstream handler buffers the body."""


def _normalise_path(path: str) -> str:
    """Treat a route and its trailing-slash redirect as the same surface."""

    return path.rstrip("/") or "/"


def _route_path(scope: Scope) -> str:
    """Return the application-relative path, including for mounted apps."""

    path = scope.get("path", "/")
    root_path = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        suffix = path[len(root_path) :]
        if not suffix or suffix.startswith("/"):
            path = suffix or "/"
    return _normalise_path(path)


def _declared_content_length(scope: Scope) -> int | None:
    """Return the largest valid Content-Length value declared by the client.

    Invalid values are ignored and the streaming byte counter remains the
    authoritative limit. ASGI servers normally reject malformed or conflicting
    Content-Length headers before constructing the scope, but considering every
    valid value here avoids trusting a smaller duplicate if one reaches us.
    """

    lengths: list[int] = []
    for name, raw_value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        for item in raw_value.split(b","):
            value = item.strip()
            if value.isdigit():
                lengths.append(int(value))
    return max(lengths, default=None)


class RequestBodyLimitMiddleware:
    """Enforce global and route-specific limits while an ASGI body streams.

    A valid oversized ``Content-Length`` is rejected before the downstream app
    runs. Requests without a trustworthy length are counted chunk-by-chunk, so
    multipart parsing and Pydantic request parsing cannot buffer an unbounded
    body first. Route limits are always capped by ``global_limit``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        global_limit: int,
        route_limits: Mapping[str, int],
    ) -> None:
        if global_limit < 1:
            raise ValueError("global_limit must be at least 1 byte")
        invalid_routes = [path for path, limit in route_limits.items() if limit < 1]
        if invalid_routes:
            raise ValueError("route limits must be at least 1 byte")

        self.app = app
        self.global_limit = global_limit
        self.route_limits = {
            _normalise_path(path): min(limit, global_limit) for path, limit in route_limits.items()
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = _route_path(scope)
        limit = self.route_limits.get(path, self.global_limit)
        declared_length = _declared_content_length(scope)
        if declared_length is not None and declared_length > limit:
            await self._reject(scope, receive, send, limit)
            return

        received = 0
        limit_exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal limit_exceeded, received
            if limit_exceeded:
                raise _RequestBodyTooLarge

            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    limit_exceeded = True
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            # The affected FastAPI routes consume their complete request before
            # starting a response. If a future streaming route starts a response
            # first, the status can no longer be replaced safely; terminate that
            # response instead of emitting a second ASGI response start.
            if response_started:
                raise
            await self._reject(scope, receive, send, limit)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "detail": "Request body exceeds the configured size limit.",
                "max_bytes": limit,
            },
        )
        await response(scope, receive, send)
