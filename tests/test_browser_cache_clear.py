"""Cache-clear responses have no authentication, cookie or persistence effects."""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from orchestrator.config import Settings
from orchestrator.main import CORS_EXPOSE_HEADERS
from orchestrator.routes import auth_setup


@pytest.fixture
def cache_app(monkeypatch):
    settings = Settings(
        daemon_allowed_origins="https://app.example.invalid",
        daemon_public_origin="https://app.example.invalid",
    )
    monkeypatch.setattr(auth_setup, "get_settings", lambda: settings)

    def no_state(*args):
        raise AssertionError("Cache clearing must not access account/database state")

    monkeypatch.setattr(auth_setup, "get_app_state", no_state)
    app = FastAPI()
    app.include_router(auth_setup.router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://app.example.invalid"],
        allow_credentials=True,
        allow_methods=["POST"],
        expose_headers=list(CORS_EXPOSE_HEADERS),
    )
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "FICTIONAL_EXPIRED_TOKEN"])
async def test_clear_cache_without_session_preserves_cookie_and_has_no_body(cache_app, token):
    headers = {"Origin": "https://app.example.invalid", "Sec-Fetch-Site": "same-site"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with AsyncClient(
        transport=ASGITransport(app=cache_app), base_url="https://api.example.invalid"
    ) as client:
        client.cookies.set("fictional-cookie", "retained")
        response = await client.post("/v1/auth/cache/clear", headers=headers)
        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["clear-site-data"] == '"cache"'
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert client.cookies.get("fictional-cookie") == "retained"
        assert response.headers["access-control-allow-origin"] == headers["Origin"]
        assert response.headers["access-control-allow-credentials"] == "true"
        # Cache-Control is CORS-safelisted. CSD need not be exposed to page JS;
        # the browser processes it outside the JavaScript response-header view.
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://foreign.invalid"},
        {"Origin": "https://app.example.invalid", "Sec-Fetch-Site": "cross-site"},
        {"Referer": "https://foreign.invalid/path"},
    ],
)
async def test_rejected_origins_never_clear_cache(cache_app, headers):
    async with AsyncClient(
        transport=ASGITransport(app=cache_app), base_url="https://api.example.invalid"
    ) as client:
        response = await client.post("/v1/auth/cache/clear", headers=headers)
    assert response.status_code == 403
    assert "clear-site-data" not in response.headers
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin"},
        {"Referer": "https://app.example.invalid/auth"},
    ],
)
async def test_existing_origin_signals_work_without_cookie(cache_app, headers):
    async with AsyncClient(
        transport=ASGITransport(app=cache_app), base_url="https://api.example.invalid"
    ) as client:
        response = await client.post("/v1/auth/cache/clear", headers=headers)
    assert response.status_code == 204
    assert response.headers["clear-site-data"] == '"cache"'


@pytest.mark.asyncio
async def test_nonempty_request_does_not_clear_cache(cache_app):
    async with AsyncClient(
        transport=ASGITransport(app=cache_app), base_url="https://api.example.invalid"
    ) as client:
        response = await client.post(
            "/v1/auth/cache/clear", headers={"Sec-Fetch-Site": "same-origin"}, content=b"{}"
        )
    assert response.status_code == 400
    assert "clear-site-data" not in response.headers


@pytest.mark.asyncio
async def test_get_is_not_a_cache_clear_operation(cache_app):
    async with AsyncClient(
        transport=ASGITransport(app=cache_app), base_url="https://api.example.invalid"
    ) as client:
        response = await client.get(
            "/v1/auth/cache/clear", headers={"Sec-Fetch-Site": "same-origin"}
        )
    assert response.status_code == 405
    assert "clear-site-data" not in response.headers
