from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from orchestrator.main import (
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    app,
    warn_on_unsafe_cors_wildcards,
)
from orchestrator.security_headers import SECURITY_HEADER_VALUES, SecurityHeadersMiddleware

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_backend_responses_include_security_headers() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/auth/config")

    assert response.status_code == 200
    for name, value in SECURITY_HEADER_VALUES.items():
        assert response.headers[name] == value

    nonce = response.headers["X-CSP-Nonce"]
    csp = response.headers["Content-Security-Policy"]
    assert f"'nonce-{nonce}'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "connect-src 'self'" in csp


@pytest.mark.asyncio
async def test_backend_csp_nonce_is_unique_per_response() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/v1/auth/config")
        second = await client.get("/v1/auth/config")

    assert first.headers["X-CSP-Nonce"] != second.headers["X-CSP-Nonce"]
    assert first.headers["Content-Security-Policy"] != second.headers["Content-Security-Policy"]


@pytest.mark.asyncio
async def test_security_headers_do_not_break_streaming_responses() -> None:
    stream_app = FastAPI()
    stream_app.add_middleware(SecurityHeadersMiddleware)

    @stream_app.get("/events")
    async def events() -> StreamingResponse:
        async def generate() -> AsyncIterator[str]:
            yield "data: ok\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    async with AsyncClient(
        transport=ASGITransport(app=stream_app),
        base_url="http://test",
    ) as client:
        response = await client.get("/events")

    assert response.status_code == 200
    assert response.text == "data: ok\n\n"
    assert response.headers["Content-Type"].startswith("text/event-stream")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "connect-src 'self'" in response.headers["Content-Security-Policy"]


def test_cors_credentials_config_uses_explicit_methods_and_headers() -> None:
    assert "*" not in CORS_ALLOW_METHODS
    assert "*" not in CORS_ALLOW_HEADERS
    assert set(CORS_ALLOW_METHODS) == {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
    assert set(CORS_ALLOW_HEADERS) == {
        "Authorization",
        "Content-Type",
        "X-CSRF-Token",
        "X-Daemon-Client-IP",
    }


def test_cors_wildcard_warning_is_available(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        warn_on_unsafe_cors_wildcards(
            allow_credentials=True,
            allow_methods=["GET", "*"],
            allow_headers=["Content-Type"],
        )

    assert "Unsafe CORS configuration" in caplog.text


@pytest.mark.asyncio
async def test_cors_preflight_rejects_disallowed_trace_method() -> None:
    cors_app = FastAPI()
    cors_app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://app.daemon.ai"],
        allow_credentials=True,
        allow_methods=list(CORS_ALLOW_METHODS),
        allow_headers=list(CORS_ALLOW_HEADERS),
    )

    @cors_app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(
        transport=ASGITransport(app=cors_app),
        base_url="http://test",
    ) as client:
        response = await client.options(
            "/ok",
            headers={
                "Origin": "https://app.daemon.ai",
                "Access-Control-Request-Method": "TRACE",
                "Access-Control-Request-Headers": "X-CSRF-Token",
            },
        )

    assert response.status_code == 400
    assert "TRACE" not in response.headers.get("Access-Control-Allow-Methods", "")


def test_frontend_proxy_emits_matching_security_headers() -> None:
    source = (ROOT / "frontend" / "proxy.ts").read_text()

    for name, value in SECURITY_HEADER_VALUES.items():
        assert name in source
        assert value in source

    assert "Content-Security-Policy" in source
    assert "crypto.randomUUID" in source
    assert "X-CSP-Nonce" in source
    assert "x-nonce" in source
