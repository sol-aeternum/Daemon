"""Closed audited ingress for the explicitly selected observation-only host."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.routing import iter_route_contexts
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Match, Route, WebSocketRoute, Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .configuration import history_selected
from .runtime import HistoryRuntime

# Exact existing endpoints, not GET/prefix/module admission. Adding a new route
# never makes it available implicitly. These reads retain native authentication,
# owner checks and captured-generation fences; no result is execution authority.
AUDITED = frozenset(
    {
        ("orchestrator.main", "health", "/health", "GET"),
        ("orchestrator.routes.auth_config", "get_auth_config", "/v1/auth/config", "GET"),
        *(
            ("orchestrator.routes.auth_setup", name, "/v1/auth/" + path, method)
            for name, path, method in (
                ("clear_browser_cache_endpoint", "cache/clear", "POST"),
                ("email_start_endpoint", "email/start", "POST"),
                ("email_complete_endpoint", "email/complete", "POST"),
                ("google_start_endpoint", "google/start", "POST"),
                ("google_complete_endpoint", "google/complete", "POST"),
                ("setup_endpoint", "setup", "POST"),
                ("enroll_start_endpoint", "enroll/start", "POST"),
                ("enroll_complete_endpoint", "enroll/complete", "POST"),
                ("refresh_endpoint", "refresh", "POST"),
                ("logout_endpoint", "logout", "POST"),
                ("list_devices_endpoint", "devices", "GET"),
                ("revoke_device_endpoint", "devices/{device_id}", "DELETE"),
            )
        ),
        ("orchestrator.routes.tasks", "get_task_by_key", "/tasks/by-key/{idempotency_key}", "GET"),
        ("orchestrator.routes.tasks", "get_task", "/tasks/{task_id}", "GET"),
        ("orchestrator.routes.tasks", "observe", "/tasks/{task_id}/events", "GET"),
        ("orchestrator.routes.conversations", "list_conversations", "/conversations", "GET"),
        (
            "orchestrator.routes.conversations",
            "get_conversation",
            "/conversations/{conversation_id}",
            "GET",
        ),
    }
)


def refusal() -> JSONResponse:
    return JSONResponse(
        {"detail": {"code": "history_unavailable", "task_id": None, "retry_safe": False}},
        status_code=503,
        headers={"Cache-Control": "no-store"},
    )


class HistoryObservationOnlyMiddleware:
    """Before route dependencies/handlers; outer security/CORS headers remain."""

    def __init__(self, app: ASGIApp, *, application: FastAPI) -> None:
        self.app, self.application = app, application
        self._cors = any(item.cls is CORSMiddleware for item in application.user_middleware)
        self._audited = {
            (id(route.original_route), route.path, method): route.endpoint
            for route in iter_route_contexts(application.routes)
            if isinstance(route.original_route, Route) and route.endpoint is not None
            for method in route.methods or ()
            if (route.endpoint.__module__, route.endpoint.__name__, route.path, method) in AUDITED
        }

    def allowed(self, scope: Scope, method: str) -> bool:
        candidate = dict(scope, method=method)
        for route in iter_route_contexts(self.application.routes):
            if not isinstance(route.original_route, (Route, Mount, WebSocketRoute)):
                return False
            match, _ = route.matches(candidate)
            if match is Match.FULL:
                return (
                    isinstance(route.original_route, Route)
                    and self._audited.get((id(route.original_route), route.path, method))
                    is route.endpoint
                )
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        state = getattr(self.application.state, "app_state", None)
        settings = getattr(state, "settings", None)
        if settings is None:
            settings = getattr(self.application.state, "settings", None)
        selected = settings is not None and history_selected(settings)
        host = getattr(state, "history_runtime", None)
        if not selected and host is None:
            await self.app(scope, receive, send)
            return
        try:
            ready = type(host) is HistoryRuntime and host.ready(state)
        except Exception:
            ready = False
        if scope["type"] == "http" and ready:
            method = scope["method"]
            headers = dict(scope.get("headers", []))
            preflight = (
                method == "OPTIONS"
                and b"origin" in headers
                and b"access-control-request-method" in headers
            )
            if preflight and self._cors:
                method = headers[b"access-control-request-method"].decode("latin-1")
            if self.allowed(scope, method) and (scope["method"] != "OPTIONS" or preflight):

                async def no_store(message: Message) -> None:
                    if message["type"] == "http.response.start":
                        message = dict(message)
                        message["headers"] = [
                            (name, value)
                            for name, value in message.get("headers", [])
                            if name.lower() != b"cache-control"
                        ] + [(b"cache-control", b"no-store")]
                    await send(message)

                await self.app(scope, receive, no_store)
                return
        response = refusal()
        if scope["type"] == "http":
            await response(scope, receive, send)
        elif "websocket.http.response" in scope.get("extensions", {}):
            await send(
                {
                    "type": "websocket.http.response.start",
                    "status": 503,
                    "headers": response.raw_headers,
                }
            )
            await send({"type": "websocket.http.response.body", "body": response.body})
        else:
            await send({"type": "websocket.close", "code": 1008})
