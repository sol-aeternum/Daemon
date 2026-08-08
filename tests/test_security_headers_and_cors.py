from __future__ import annotations

import logging
import re
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

    # Real-runtime CSP must allow the Google Identity Services script
    # (loaded by AuthLanding.tsx for hosted Google sign-in) and the
    # ElevenLabs WebSocket origin (used by useStreamingTts.ts / useStt.ts
    # / TextToSpeechButton.tsx). Without these, hosted Google sign-in
    # and streaming TTS / realtime STT are blocked by the strict CSP.
    assert "https://accounts.google.com" in source
    assert "wss://api.elevenlabs.io" in source

    # CSP must include a media-src directive so provider-hosted video
    # (e.g. fal / xAI generation URLs passed to <video>) is not blocked
    # by default-src 'self'.
    assert "media-src" in source

    # media-src must permit `blob:` so authenticated audio that is
    # converted to a blob: object URL (AudioPlaybackProvider /
    # useAuthenticatedImageUrl) can play.
    assert "blob:" in source
    assert re.search(r"media-src[^;]*blob:", source) is not None

    # connect-src must include the configured backend origin
    # (NEXT_PUBLIC_API_URL) so direct browser hooks
    # (useConversationHistory etc.) can reach the backend without going
    # through the same-origin Next API routes.
    assert "NEXT_PUBLIC_API_URL" in source
    # The helper that builds the connect-src host list must reference the
    # env var so the resulting directive includes the configured backend
    # origin at runtime.
    assert re.search(r"process\.env\.NEXT_PUBLIC_API_URL", source) is not None
    # 'self' must remain in connect-src so same-origin requests still work.
    assert re.search(r"connect-src[^;]*'self'", source) is not None

    # frame-src must permit `blob:` and `data:` so PDF previews
    # (FilePreview.tsx / PdfPreview.tsx) using blob:/data: URLs can
    # load inside the preview iframe.
    assert "frame-src" in source
    assert re.search(r"frame-src[^;]*blob:", source) is not None
    assert re.search(r"frame-src[^;]*data:", source) is not None

    # CSP must forward on the request headers (alongside x-nonce) so Next
    # propagates it to the rendered response — otherwise framework bootstrap
    # scripts are blocked by the strict nonce-only script-src. Prettier
    # may wrap the multi-arg set() call across lines, so we check for the
    # method name plus the header key (rather than the full one-line call).
    assert (
        re.search(r"requestHeaders\.set\(\s*['\"]Content-Security-Policy['\"]", source) is not None
    )


def test_inline_artifact_src_doc_includes_csp_nonce_on_inline_tags() -> None:
    """The InlineArtifact iframe's srcDoc contains inline <style> and
    <script> tags. Without a nonce, the embedding page's CSP blocks
    them. The component must read the per-request nonce from the
    <meta name="csp-nonce"> tag rendered by the root layout and inject
    it onto both inline tags.
    """
    source = (ROOT / "frontend" / "components" / "chat" / "InlineArtifact.tsx").read_text()
    assert 'meta[name="csp-nonce"]' in source
    assert "styleNonceAttr" in source
    assert "scriptNonceAttr" in source
    assert "<style${styleNonceAttr}>" in source
    assert "<script${scriptNonceAttr}>" in source


def test_root_layout_propagates_csp_nonce_to_client() -> None:
    """The root layout must read the per-request `x-nonce` header set by
    the proxy and surface it as a <meta name="csp-nonce"> tag so the
    InlineArtifact iframe (and any other client component that needs it)
    can pull the nonce into their inline scripts/styles.
    """
    source = (ROOT / "frontend" / "app" / "layout.tsx").read_text()
    assert "headers()" in source
    assert '"x-nonce"' in source
    assert 'meta name="csp-nonce"' in source


def test_production_disables_fastapi_docs_endpoints() -> None:
    """The strict CSP forbids the docs CDN assets and inline bootstrap
    script that FastAPI / Swagger UI / ReDoc require. We disable the
    docs endpoints unconditionally so the policy and the docs surface
    don't conflict in any environment.
    """
    source = (ROOT / "orchestrator" / "main.py").read_text()
    # docs/redoc/openapi URL kwargs are passed as plain `None` (not
    # env-conditional) so FastAPI 404s on /docs and /redoc regardless of
    # daemon_environment. The OpenAPI schema URL is also disabled.
    assert "docs_url=None,\n    redoc_url=None,\n    openapi_url=None," in source or (
        "docs_url=None" in source and "redoc_url=None" in source and "openapi_url=None" in source
    )
