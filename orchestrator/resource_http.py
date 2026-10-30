"""Explicit isolated resource read surface. Default startup never installs it."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, Response

from orchestrator.auth import require_device_auth
from orchestrator.resource_catalog import ResourceCatalog, safe_display
from orchestrator.resource_input_codec import validate_png
from orchestrator.resource_presentations import ResourcePresentations
from orchestrator.resource_store import ResourceError, _canonical
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    capture_generation,
    guard_content_write,
)

PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
ERROR_STATUS = {
    "not_found": 404,
    "original_not_received": 409,
    "invalid_cursor": 400,
    "invalid_query": 400,
    "restart_required": 409,
    "capacity": 503,
}


class _ReadAuthChanged(Exception):
    pass


def _error(exc: Exception, head: bool = False) -> Response:
    code = exc.code if isinstance(exc, ResourceError) and exc.code in ERROR_STATUS else "uncertain"
    status = ERROR_STATUS.get(code, 503)
    if isinstance(exc, HTTPException):
        status, code = exc.status_code, "unauthorized" if exc.status_code == 401 else "unavailable"
    if isinstance(exc, _ReadAuthChanged):
        status, code = 401, "unauthorized"
    res = JSONResponse({"code": code}, status_code=status, headers=PRIVATE)
    if head:
        res.body = b""
    return res


def resource_path(path: str) -> bool:
    return (
        path == "/resources"
        or path.startswith("/resources/")
        or (path.startswith("/tasks/") and any(s in path for s in ("/inputs", "/presentations")))
    )


class ResourceReadBoundary:
    def __init__(self, app: Any, *, catalog: ResourceCatalog) -> None:
        self.app, self.catalog = app, catalog

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not resource_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        started = False
        request = Request(scope, receive)
        try:
            if (
                len(scope.get("query_string", b"")) > 16384
                or sum(len(k) + len(v) for k, v in scope["headers"]) > 32768
            ):
                raise ResourceError("invalid_query")
            inputs = self.catalog.inputs
            if (
                request.app.state.app_state.db_pool is not inputs._tasks._pool
                or inputs._tasks._resource_inputs is not inputs
                or inputs._tasks._memory._resource_inputs is not inputs
            ):
                raise ResourceError()
            auth = await require_device_auth(request)
            async with inputs._slot(auth.user_id):
                async with inputs._account(auth.user_id) as conn:
                    expected = await capture_generation(conn, auth.user_id)
                    async with guard_content_write(conn, expected):
                        pass
                scope.setdefault("state", {})["resource_generation"] = expected

                async def secure_send(message: Any) -> None:
                    nonlocal started
                    if message["type"] == "http.response.start":
                        try:
                            current = await require_device_auth(request)
                        except HTTPException as exc:
                            if exc.status_code == 401:
                                raise _ReadAuthChanged() from None
                            raise ResourceError() from None
                        if current != auth:
                            raise _ReadAuthChanged()
                        async with (
                            inputs._account(auth.user_id) as conn,
                            guard_content_write(conn, expected),
                        ):
                            pass
                        headers = [
                            (k, v)
                            for k, v in message.get("headers", [])
                            if k.lower() not in (b"cache-control", b"x-content-type-options")
                        ]
                        message = {
                            **message,
                            "headers": headers
                            + [(k.lower().encode(), v.encode()) for k, v in PRIVATE.items()],
                        }
                        started = True
                    await send(message)

                await self.app(scope, receive, secure_send)
        except Exception as exc:
            if started:
                raise
            await _error(exc, scope["method"] == "HEAD")(scope, receive, send)


def _params(request: Request) -> dict[str, str]:
    pairs = list(request.query_params.multi_items())
    if len(pairs) > 12 or len({k for k, _ in pairs}) != len(pairs):
        raise ResourceError("invalid_query")
    return dict(pairs)


def _download(
    content: bytes,
    mime: str,
    name: str | None,
    request: Request,
    expected: CapturedGeneration,
    resource: UUID,
    version: UUID,
    *,
    preview: bool,
) -> Response:
    digest = hashlib.sha256(content).hexdigest()
    etag = (
        '"'
        + hashlib.sha256(
            b"daemon.resource.etag.v1\x00"
            + _canonical(
                [str(expected.account_id), expected.generation, str(resource), str(version), digest]
            )
        ).hexdigest()
        + '"'
    )
    extension = {"image/png": ".png", "text/plain": ".txt", "text/csv": ".csv"}.get(mime, ".bin")
    stem = (safe_display(name) or "resource").rsplit(".", 1)[0][:100]
    filename = stem + extension
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)
    inline = preview and mime in ("image/png", "text/plain", "text/csv")
    headers = {
        **PRIVATE,
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if preview and mime in ("text/plain", "text/csv"):
        mime = "text/plain; charset=utf-8"
    if_none = request.headers.get("if-none-match")
    if if_none and any(
        part.strip().removeprefix("W/") in (etag, "*") for part in if_none.split(",")
    ):
        return Response(status_code=304, headers=headers)
    size, status, start, end = len(content), 200, 0, len(content) - 1
    raw = request.headers.get("range")
    if raw and request.headers.get("if-range", etag) == etag:
        if len(raw) > 100 or "," in raw:
            return Response(status_code=400, headers=headers)
        match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", raw)
        if match is None or not any(match.groups()):
            return Response(status_code=400, headers=headers)
        a, b = match.groups()
        if a:
            start, end = int(a), min(int(b), size - 1) if b else size - 1
        else:
            count = int(b)
            start, end = max(0, size - count), size - 1
            if count == 0:
                start = size
        if start >= size or end < start:
            return Response(
                status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
            )
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    body = content[start : end + 1]
    headers["Content-Length"] = str(len(body))
    return Response(
        b"" if request.method == "HEAD" else body,
        status_code=status,
        media_type=mime,
        headers=headers,
    )


def install_resource_routes(
    app: FastAPI, catalog: ResourceCatalog, presentations: ResourcePresentations
) -> None:
    if (
        presentations.catalog is not catalog
        or getattr(app.state, "resource_catalog", None) is not None
    ):
        raise ResourceError("invalid_composition")
    app.state.resource_catalog = catalog
    app.add_middleware(ResourceReadBoundary, catalog=catalog)
    router = APIRouter(dependencies=[Depends(require_device_auth)])

    @router.get("/resources")
    async def resources(request: Request):
        return await catalog.page(request.state.resource_generation, _params(request))

    @router.get("/tasks/{task_id}/inputs")
    async def task_inputs(task_id: UUID, request: Request):
        return await catalog.page(request.state.resource_generation, _params(request), task=task_id)

    @router.get("/tasks/{task_id}/presentations")
    async def task_presentations(task_id: UUID, request: Request):
        if _params(request):
            raise ResourceError("invalid_query")
        return await presentations.page(request.state.resource_generation, task_id)

    @router.get("/resources/{resource}/versions/{version}")
    async def metadata(resource: UUID, version: UUID, request: Request):
        if _params(request):
            raise ResourceError("invalid_query")
        return await catalog.metadata(request.state.resource_generation, resource, version)

    @router.api_route("/resources/{resource}/versions/{version}/content", methods=["GET", "HEAD"])
    @router.get("/resources/{resource}/versions/{version}/preview")
    async def content(resource: UUID, version: UUID, request: Request):
        if _params(request):
            raise ResourceError("invalid_query")
        expected = request.state.resource_generation
        result = await catalog.store.read(expected, resource, version)
        preview = request.url.path.endswith("/preview")
        if preview and result.mime == "image/png":
            validate_png(result.content, catalog.inputs._limits)
        return _download(
            result.content,
            result.mime,
            result.name,
            request,
            expected,
            resource,
            version,
            preview=preview,
        )

    app.include_router(router)
