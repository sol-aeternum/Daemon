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
from orchestrator.security_headers import (
    SECURITY_HEADER_VALUES,
    STRICT_TRANSPORT_SECURITY,
    SecurityHeadersMiddleware,
    X_CONTENT_TYPE_OPTIONS,
    X_FRAME_OPTIONS,
    _OuterSecurityHeadersMiddleware,
)

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
        # X-Request-ID is now permitted on cross-origin requests so a
        # trusted upstream proxy / load-test harness can propagate its
        # own correlation handle. The response always carries a
        # server-generated X-Request-ID regardless (PR #218 round-1).
        "X-Request-ID",
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

    # ToolCallBlock and VideoPlayer explicitly accept inline data: videos.
    assert re.search(r"const mediaSrc = \[[^\]]*'data:'", source) is not None

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
    assert re.search(r"\.get\((['\"])x-nonce\1\)", source) is not None
    assert 'meta name="csp-nonce"' in source


def test_production_disables_fastapi_docs_endpoints() -> None:
    """The strict CSP forbids the docs CDN assets and inline bootstrap
    script that FastAPI / Swagger UI / ReDoc require. We disable the
    rendered docs endpoints unconditionally so the policy and the docs
    surface don't conflict in any environment; the OpenAPI schema
    endpoint (``/openapi.json``) is left at the FastAPI default so API
    clients, SDK generators, and development tooling can still introspect
    the surface.
    """
    source = (ROOT / "orchestrator" / "main.py").read_text()
    # Rendered docs URLs are explicitly disabled.
    assert "docs_url=None" in source
    assert "redoc_url=None" in source
    # The OpenAPI schema URL is intentionally not overridden to ``None``
    # — the JSON schema has no CDN assets or inline scripts that
    # conflict with the strict CSP, and SDK generators / tooling depend
    # on it. The FastAPI default leaves the kwarg unset entirely, so we
    # check that no assignment to that name exists on the FastAPI(...)
    # constructor invocation.
    fastapi_ctor_block_match = re.search(r"FastAPI\([^)]*\)", source, flags=re.DOTALL)
    assert fastapi_ctor_block_match is not None
    assert "openapi_url" not in fastapi_ctor_block_match.group(0)


def test_proxy_csp_permits_gis_frame_and_connect() -> None:
    """Hosted Google sign-in (frontend/components/AuthLanding.tsx →
    google.accounts.id.prompt()) renders an iframe pointed at
    https://accounts.google.com and exchanges XHRs with that origin. The
    strict CSP must allow Google in both frame-src and connect-src so
    the GIS iframe prompt and its callbacks are not blocked.
    """
    source = (ROOT / "frontend" / "proxy.ts").read_text()
    # frame-src must include the GIS origin.
    assert re.search(r"frame-src[^;]*https://accounts\.google\.com", source) is not None
    # connect-src must include the GIS origin (not just ElevenLabs /
    # NEXT_PUBLIC_API_URL). Both directives reference it.
    assert source.count("https://accounts.google.com") >= 3


def test_proxy_csp_permits_inline_style_attributes() -> None:
    """React's ``style={{...}}`` attributes cannot carry nonces — nonces
    authorize whole ``<style>`` tags or external stylesheets, not inline
    attribute values. The repository relies on inline style attributes
    for behavior-critical positioning (conversation menu offsets, video /
    council progress widths, preview colors, input sizing). Without
    ``style-src-attr 'unsafe-inline'`` browsers enforcing strict CSP
    silently strip those values.
    """
    source = (ROOT / "frontend" / "proxy.ts").read_text()
    assert "style-src-attr 'unsafe-inline'" in source


def test_proxy_csp_permits_development_unsafe_eval() -> None:
    """Next's webpack dev server emits modules that load via ``eval(...)``,
    so the documented ``npm run dev`` workflow requires ``'unsafe-eval'``
    in ``script-src`` to render the client bundle. The keyword is added
    only when ``NODE_ENV === 'development'`` so production CSP remains
    strict (Codex P1 on PR #163).
    """
    source = (ROOT / "frontend" / "proxy.ts").read_text()
    # The conditional must check NODE_ENV and add 'unsafe-eval' only in dev.
    assert (
        re.search(
            r"isDevelopment\s*=\s*process\.env\.NODE_ENV\s*===\s*['\"]development['\"]",
            source,
        )
        is not None
    )
    # 'unsafe-eval' must be added to the script-src parts list when isDevelopment.
    assert (
        re.search(
            r"isDevelopment[\s\S]{0,200}scriptSrcParts\.push\(['\"]'unsafe-eval'['\"]\)",
            source,
        )
        is not None
    )


def test_proxy_csp_permits_local_api_fallback_in_connect_src() -> None:
    """When ``NEXT_PUBLIC_API_URL`` is unset (the documented local
    development fallback), the connect-src host list must include
    ``http://localhost:8000`` so hooks like ``useConversationHistory`` and
    the Studio video generation path can reach the daemon backend
    (Codex P2 on PR #163). Production deployments must set
    ``NEXT_PUBLIC_API_URL`` so this fallback is never added.
    """
    source = (ROOT / "frontend" / "proxy.ts").read_text()
    # The fallback must be added in the else branch when NEXT_PUBLIC_API_URL
    # is empty.
    assert (
        re.search(
            r"NEXT_PUBLIC_API_URL[\s\S]{0,400}http://localhost:8000",
            source,
        )
        is not None
    )


def test_docx_preview_nonces_styles_before_attaching_them() -> None:
    """A nonce makes ``'unsafe-inline'`` ineffective in the same
    ``style-src`` directive. DocxPreview must therefore render generated
    styles into a detached container, nonce them, and only then attach them
    to the live document.
    """
    proxy_source = (ROOT / "frontend" / "proxy.ts").read_text()
    preview_source = (
        ROOT / "frontend" / "src" / "components" / "previews" / "DocxPreview.tsx"
    ).read_text()

    assert "`style-src 'self' 'nonce-${nonce}'`" in proxy_source
    assert "`style-src 'self' 'nonce-${nonce}' 'unsafe-inline'`" not in proxy_source
    create_style_container = re.search(r"document\.createElement\((['\"])div\1\)", preview_source)
    set_style_nonce = re.search(r"style\.setAttribute\((['\"])nonce\1, nonce\)", preview_source)
    assert create_style_container is not None
    assert set_style_nonce is not None
    assert set_style_nonce.start() < preview_source.index("containerRef.current.prepend")


def test_html_preview_uses_an_isolated_frame_policy() -> None:
    proxy_source = (ROOT / "frontend" / "proxy.ts").read_text()
    preview_source = (
        ROOT / "frontend" / "src" / "components" / "previews" / "HtmlPreview.tsx"
    ).read_text()

    assert "HTML_PREVIEW_FRAME_PATH" in preview_source
    assert "srcDoc={content}" not in preview_source
    assert "HTML_PREVIEW_CONTENT_SECURITY_POLICY" in proxy_source
    assert "response.headers.set('X-Frame-Options', 'SAMEORIGIN')" in proxy_source


@pytest.mark.asyncio
async def test_openapi_schema_endpoint_remains_available() -> None:
    """The /openapi.json schema endpoint must remain reachable for API
    clients, SDK generators, and development tooling. Only the rendered
    docs (Swagger UI / ReDoc) are disabled.
    """
    from orchestrator.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    body = response.json()
    assert body.get("openapi")
    assert body.get("paths") is not None


@pytest.mark.asyncio
async def test_rendered_docs_endpoints_return_404() -> None:
    """Swagger UI and ReDoc are disabled — both render CDN assets that
    conflict with the strict CSP, so they return 404 in every environment.
    """
    from orchestrator.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        docs_response = await client.get("/docs")
        redoc_response = await client.get("/redoc")

    assert docs_response.status_code == 404
    assert redoc_response.status_code == 404


@pytest.mark.asyncio
async def test_unhandled_exception_response_carries_security_headers() -> None:
    """When a route raises an unhandled exception, Starlette's outermost
    ``ServerErrorMiddleware`` generates the 500 response *outside* the
    user-added middleware stack. ``add_middleware`` only places the
    middleware inside that error handler, so the outer wrap must be done
    by building the middleware stack explicitly and wrapping it. Verify
    the resulting 500 still carries HSTS, CSP, and X-Frame-Options.
    """

    crash_app = FastAPI()

    @crash_app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("intentional crash for security-headers regression test")

    # Mimic the orchestrator.main wiring: build the stack eagerly (which
    # puts ServerErrorMiddleware on the outside) and wrap with the outer
    # security-headers pass so 500s emitted by ServerErrorMiddleware
    # carry the headers.
    _built = crash_app.build_middleware_stack()
    crash_app.middleware_stack = _OuterSecurityHeadersMiddleware(_built)

    async with AsyncClient(
        transport=ASGITransport(app=crash_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.headers["Strict-Transport-Security"] == STRICT_TRANSPORT_SECURITY
    assert response.headers["X-Frame-Options"] == X_FRAME_OPTIONS
    assert response.headers["X-Content-Type-Options"] == X_CONTENT_TYPE_OPTIONS
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
