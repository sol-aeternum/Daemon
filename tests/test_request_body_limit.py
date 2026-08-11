from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from orchestrator.config import Settings
from orchestrator.request_body_limit import RequestBodyLimitMiddleware, _RequestBodyTooLarge

MIB = 1024 * 1024
APPROVED_GLOBAL_LIMIT = 50 * MIB
APPROVED_ROUTE_LIMITS = {
    "/chat": 8 * MIB,
    "/chat/completions": 8 * MIB,
    "/v1/chat/completions": 8 * MIB,
    "/stt": 25 * MIB,
    "/skills/upload": 50 * MIB,
}


def _scope(path: str, *, content_length: int | None = None) -> Scope:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    return cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "state": {},
        },
    )


async def _invoke(
    middleware: RequestBodyLimitMiddleware,
    scope: Scope,
    body_messages: list[Message],
) -> tuple[list[Message], int]:
    pending = iter(body_messages)
    sent: list[Message] = []
    receive_calls = 0

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        return next(pending)

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return sent, receive_calls


def _consuming_app(called: list[bool]) -> ASGIApp:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        called.append(True)
        while True:
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


def _status(sent: list[Message]) -> int:
    start = next(message for message in sent if message["type"] == "http.response.start")
    return start["status"]


def _json_body(sent: list[Message]) -> dict[str, object]:
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return cast(dict[str, object], json.loads(body))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "content_length", "expected_limit"),
    [
        ("/chat", 8 * MIB + 1, 8 * MIB),
        ("/chat/", 8 * MIB + 1, 8 * MIB),
        ("/chat/completions", 8 * MIB + 1, 8 * MIB),
        ("/v1/chat/completions", 8 * MIB + 1, 8 * MIB),
        ("/stt", 26 * MIB, 25 * MIB),
        ("/skills/upload", 51 * MIB, 50 * MIB),
        ("/unlisted", 50 * MIB + 1, 50 * MIB),
    ],
)
async def test_declared_oversized_bodies_are_rejected_before_downstream(
    path: str,
    content_length: int,
    expected_limit: int,
) -> None:
    called: list[bool] = []
    middleware = RequestBodyLimitMiddleware(
        _consuming_app(called),
        global_limit=APPROVED_GLOBAL_LIMIT,
        route_limits=APPROVED_ROUTE_LIMITS,
    )

    sent, receive_calls = await _invoke(
        middleware,
        _scope(path, content_length=content_length),
        [],
    )

    assert _status(sent) == 413
    assert _json_body(sent) == {
        "detail": "Request body exceeds the configured size limit.",
        "max_bytes": expected_limit,
    }
    assert called == []
    assert receive_calls == 0


@pytest.mark.asyncio
async def test_streamed_body_without_content_length_is_counted() -> None:
    called: list[bool] = []
    middleware = RequestBodyLimitMiddleware(
        _consuming_app(called),
        global_limit=10,
        route_limits={"/chat": 5},
    )

    sent, receive_calls = await _invoke(
        middleware,
        _scope("/chat"),
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ],
    )

    assert _status(sent) == 413
    assert _json_body(sent)["max_bytes"] == 5
    assert called == [True]
    assert receive_calls == 2


@pytest.mark.asyncio
async def test_body_at_exact_limit_is_accepted() -> None:
    called: list[bool] = []
    middleware = RequestBodyLimitMiddleware(
        _consuming_app(called),
        global_limit=10,
        route_limits={"/chat": 5},
    )

    sent, _ = await _invoke(
        middleware,
        _scope("/chat", content_length=5),
        [{"type": "http.request", "body": b"abcde", "more_body": False}],
    )

    assert _status(sent) == 200
    assert called == [True]


@pytest.mark.asyncio
async def test_approved_chat_limit_accepts_two_mibibytes() -> None:
    called: list[bool] = []
    middleware = RequestBodyLimitMiddleware(
        _consuming_app(called),
        global_limit=APPROVED_GLOBAL_LIMIT,
        route_limits=APPROVED_ROUTE_LIMITS,
    )

    sent, _ = await _invoke(
        middleware,
        _scope("/chat", content_length=2 * MIB),
        [{"type": "http.request", "body": b"x" * (2 * MIB), "more_body": False}],
    )

    assert _status(sent) == 200
    assert called == [True]


@pytest.mark.asyncio
async def test_global_ceiling_caps_a_larger_route_setting() -> None:
    called: list[bool] = []
    middleware = RequestBodyLimitMiddleware(
        _consuming_app(called),
        global_limit=5,
        route_limits={"/chat": 8},
    )

    sent, _ = await _invoke(middleware, _scope("/chat", content_length=6), [])

    assert _status(sent) == 413
    assert _json_body(sent)["max_bytes"] == 5
    assert called == []


@pytest.mark.asyncio
async def test_root_path_is_removed_before_route_limit_matching() -> None:
    called: list[bool] = []
    middleware = RequestBodyLimitMiddleware(
        _consuming_app(called),
        global_limit=10,
        route_limits={"/chat": 5},
    )
    scope = _scope("/api/chat", content_length=6)
    scope["root_path"] = "/api"

    sent, _ = await _invoke(middleware, scope, [])

    assert _status(sent) == 413
    assert _json_body(sent)["max_bytes"] == 5
    assert called == []


@pytest.mark.asyncio
async def test_late_overflow_aborts_instead_of_starting_a_second_response() -> None:
    sent: list[Message] = []
    pending = iter(
        [
            cast(Message, {"type": "http.request", "body": b"abc", "more_body": True}),
            cast(Message, {"type": "http.request", "body": b"def", "more_body": False}),
        ]
    )

    async def early_response_app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()

    async def receive() -> Message:
        return next(pending)

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(
        early_response_app,
        global_limit=10,
        route_limits={"/chat": 5},
    )

    with pytest.raises(_RequestBodyTooLarge):
        await middleware(_scope("/chat"), receive, send)

    assert [message["type"] for message in sent] == ["http.response.start"]


def test_request_body_limit_settings_defaults_and_validation() -> None:
    fields = Settings.model_fields
    assert fields["daemon_max_request_body_bytes"].default == 50 * MIB
    assert fields["daemon_max_chat_body_bytes"].default == 8 * MIB
    assert fields["daemon_max_stt_body_bytes"].default == 25 * MIB
    assert fields["daemon_max_skill_upload_body_bytes"].default == 50 * MIB

    with pytest.raises(ValidationError):
        Settings(daemon_max_chat_body_bytes=0)


@pytest.mark.asyncio
async def test_chunked_http_request_integration_returns_413() -> None:
    test_app = FastAPI()
    test_app.add_middleware(
        RequestBodyLimitMiddleware,
        global_limit=10,
        route_limits={"/chat": 5},
    )

    @test_app.post("/chat")
    async def chat(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    async def chunks() -> AsyncIterator[bytes]:
        yield b"abc"
        yield b"def"

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as client:
        response = await client.post("/chat", content=chunks())

    assert response.status_code == 413
    assert response.json()["max_bytes"] == 5


def test_production_openapi_documents_request_body_limits() -> None:
    from orchestrator.main import app

    schema = app.openapi()
    for path in (
        "/chat",
        "/chat/completions",
        "/v1/chat/completions",
        "/stt",
        "/skills/upload",
    ):
        assert schema["paths"][path]["post"]["responses"]["413"]["description"] == (
            "Request body exceeds the configured size limit."
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "content_length", "expected_limit"),
    [
        ("/chat", 8 * MIB + 1, 8 * MIB),
        ("/chat/completions", 8 * MIB + 1, 8 * MIB),
        ("/v1/chat/completions", 8 * MIB + 1, 8 * MIB),
        ("/stt", 25 * MIB + 1, 25 * MIB),
        ("/skills/upload", 50 * MIB + 1, 50 * MIB),
        ("/not-a-route", 50 * MIB + 1, 50 * MIB),
    ],
)
async def test_production_stack_rejects_declared_oversized_bodies(
    path: str,
    content_length: int,
    expected_limit: int,
) -> None:
    from orchestrator.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            path,
            content=b"",
            headers={"Content-Length": str(content_length)},
        )

    assert response.status_code == 413
    assert response.json()["max_bytes"] == expected_limit
