"""Tests for the runtime deployment-mode setting and the public /v1/auth/config endpoint.

These tests are written before implementation. They should FAIL against `main`
(no `daemon_deployment_mode` setting, no `/v1/auth/config` endpoint) and PASS
once TODO 3 lands the implementation.

Surface covered:
- `Settings.daemon_deployment_mode` defaults to "self_hosted"
- `Settings.daemon_deployment_mode` accepts only the two allowed values
  (Literal enforcement at construction time)
- The public `GET /v1/auth/config` endpoint:
    - is unauthenticated (no cookie/Authorization required)
    - returns `Cache-Control: no-store`
    - returns the contract shape `{mode, email: {enabled}, google: {enabled, clientId}}`
    - reads only the four runtime-safe fields
    - never serializes secrets or secret-adjacent fields

These tests exercise the ASGI app directly via `httpx.ASGITransport` rather than
going through the network, matching the pattern established in
`tests/test_hosted_identity_smoke.py`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.config import Settings, get_settings
from orchestrator.main import app


# ---------------------------------------------------------------------------
# Settings-level: daemon_deployment_mode
# ---------------------------------------------------------------------------


def test_deployment_mode_default_is_self_hosted() -> None:
    """Settings.daemon_deployment_mode defaults to "self_hosted" — preserves existing installs."""
    settings = Settings()
    assert settings.daemon_deployment_mode == "self_hosted"


def test_deployment_mode_hosted_via_constructor() -> None:
    """Settings accepts "hosted" as an explicit value."""
    settings = Settings(daemon_deployment_mode="hosted")
    assert settings.daemon_deployment_mode == "hosted"


def test_deployment_mode_hosted_via_env(monkeypatch) -> None:
    """Settings reads DAEMON_DEPLOYMENT_MODE from env."""
    monkeypatch.setenv("DAEMON_DEPLOYMENT_MODE", "hosted")
    settings = Settings()
    assert settings.daemon_deployment_mode == "hosted"


def test_deployment_mode_invalid_value_rejected_by_literal() -> None:
    """Invalid daemon_deployment_mode is rejected at construction time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(daemon_deployment_mode="saas")  # type: ignore[arg-type]


def test_deployment_mode_is_orthogonal_to_hosted_identity_enabled() -> None:
    """Deployment mode and hosted-identity-enabled are independent axes.

    Hosted deployment with identity still disabled is a valid staging config;
    self-hosted deployment with identity enabled is a valid dev config. The
    setting must not couple them.
    """
    a = Settings(daemon_deployment_mode="hosted", daemon_hosted_identity_enabled=False)
    assert a.daemon_deployment_mode == "hosted"
    assert a.daemon_hosted_identity_enabled is False

    b = Settings(daemon_deployment_mode="self_hosted", daemon_hosted_identity_enabled=True)
    assert b.daemon_deployment_mode == "self_hosted"
    assert b.daemon_hosted_identity_enabled is True


# ---------------------------------------------------------------------------
# Endpoint-level: GET /v1/auth/config
# ---------------------------------------------------------------------------


async def _client_for_settings(
    overrides: dict[str, Any] | None = None,
) -> tuple[AsyncClient, Settings]:
    """Build an AsyncClient bound to a Settings instance with the given overrides.

    We bypass `get_settings()` (which is `@lru_cache`'d at module load) by
    constructing a fresh Settings object and patching the app's settings
    access for the lifetime of the call.
    """
    overrides = overrides or {}
    settings = Settings(**overrides)
    # Direct endpoint tests don't exercise lifespan, so the app may try to
    # read the lru_cached global. We clear the cache and inject our overrides.
    get_settings.cache_clear()
    Settings(**overrides)  # prime cache
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), settings


@pytest.mark.asyncio
async def test_auth_config_endpoint_is_unauthenticated_self_hosted() -> None:
    """GET /v1/auth/config works without cookies or Authorization headers."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # No cookies, no Authorization header.
        response = await client.get("/v1/auth/config")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "self_hosted"
    assert body["email"] == {"enabled": False}
    assert body["google"] == {"enabled": False, "clientId": ""}


@pytest.mark.asyncio
async def test_auth_config_endpoint_returns_no_store_cache_header() -> None:
    """Endpoint sets Cache-Control: no-store so a CDN cannot serve stale mode."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/auth/config")
    assert response.status_code == 200
    cache_control = response.headers.get("Cache-Control", "")
    assert "no-store" in cache_control.lower(), f"expected no-store, got: {cache_control!r}"


@pytest.mark.asyncio
async def test_auth_config_endpoint_response_shape_self_hosted_default() -> None:
    """Response has exactly the contract shape, no extra fields."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/auth/config")
    body = response.json()
    # Top-level keys
    assert set(body.keys()) == {"mode", "email", "google"}, body
    # email shape
    assert set(body["email"].keys()) == {"enabled"}, body["email"]
    # google shape
    assert set(body["google"].keys()) == {"enabled", "clientId"}, body["google"]


@pytest.mark.asyncio
async def test_auth_config_endpoint_reports_providers_enabled_when_identity_enabled(
    monkeypatch,
) -> None:
    """Provider flags become available only when the hosted identity master flag is on."""
    monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/auth/config")
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == {"enabled": True}
        assert body["google"]["enabled"] is True
    finally:
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_auth_config_endpoint_gates_providers_on_identity_master_flag(monkeypatch) -> None:
    """Master identity off keeps providers disabled even when individual flags are on."""
    monkeypatch.setenv("DAEMON_DEPLOYMENT_MODE", "hosted")
    monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "false")
    monkeypatch.setenv("DAEMON_EMAIL_ENABLED", "true")
    monkeypatch.setenv("DAEMON_GOOGLE_ENABLED", "true")
    monkeypatch.setenv("DAEMON_GOOGLE_CLIENT_ID", "demo.apps.googleusercontent.com")
    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/auth/config")
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "hosted"
        assert body["email"] == {"enabled": False}
        assert body["google"] == {
            "enabled": False,
            "clientId": "demo.apps.googleusercontent.com",
        }
    finally:
        monkeypatch.delenv("DAEMON_DEPLOYMENT_MODE", raising=False)
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_ENABLED", raising=False)
        monkeypatch.delenv("DAEMON_EMAIL_ENABLED", raising=False)
        monkeypatch.delenv("DAEMON_GOOGLE_ENABLED", raising=False)
        monkeypatch.delenv("DAEMON_GOOGLE_CLIENT_ID", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_auth_config_endpoint_reflects_email_disabled(monkeypatch) -> None:
    """`email.enabled` is False when DAEMON_EMAIL_ENABLED=false."""
    monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("DAEMON_EMAIL_ENABLED", "false")
    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/auth/config")
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == {"enabled": False}
    finally:
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_ENABLED", raising=False)
        monkeypatch.delenv("DAEMON_EMAIL_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_auth_config_endpoint_reflects_google_disabled(monkeypatch) -> None:
    """`google.enabled` is False when DAEMON_GOOGLE_ENABLED=false."""
    monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("DAEMON_GOOGLE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/auth/config")
        assert response.status_code == 200
        body = response.json()
        assert body["google"]["enabled"] is False
        # clientId is still the (possibly empty) configured value
        assert "clientId" in body["google"]
    finally:
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_ENABLED", raising=False)
        monkeypatch.delenv("DAEMON_GOOGLE_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_auth_config_endpoint_reflects_google_client_id(monkeypatch) -> None:
    """`google.clientId` echoes the public client ID when configured."""
    monkeypatch.setenv("DAEMON_GOOGLE_CLIENT_ID", "demo.apps.googleusercontent.com")
    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/auth/config")
        body = response.json()
        assert body["google"]["clientId"] == "demo.apps.googleusercontent.com"
    finally:
        monkeypatch.delenv("DAEMON_GOOGLE_CLIENT_ID", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_auth_config_endpoint_reflects_hosted_mode(monkeypatch) -> None:
    """`mode` is "hosted" when DAEMON_DEPLOYMENT_MODE=hosted."""
    monkeypatch.setenv("DAEMON_DEPLOYMENT_MODE", "hosted")
    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/auth/config")
        body = response.json()
        assert body["mode"] == "hosted"
    finally:
        monkeypatch.delenv("DAEMON_DEPLOYMENT_MODE", raising=False)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Endpoint-level: no-secret assertion
# ---------------------------------------------------------------------------

# Field names that MUST NEVER appear in the response body or its header set.
# If any of these leak, the endpoint is a null deliverable.
FORBIDDEN_FIELDS: tuple[str, ...] = (
    "audience_allowlist",
    "audience",
    "mail_sender_mode",
    "mail_smtp_host",
    "mail_smtp_port",
    "mail_smtp_use_tls",
    "mail_from_address",
    "private_refresh_ttl_days",
    "temporary_refresh_ttl_seconds",
    "email_challenge_ttl_seconds",
    "email_challenge_max_attempts",
    "google_nonce_ttl_seconds",
    "rate_limit",
    "pepper",
    "signup_mode",
    "environment",
    "trust_proxy",
    "require_redis",
    "session_cleanup",
    "password",
    "secret",
    "client_secret",
    "smtp_password",
)


@pytest.mark.asyncio
async def test_auth_config_endpoint_does_not_leak_secret_fields() -> None:
    """Response JSON contains only the contract fields; no secret-adjacent field leaks."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/auth/config")
    body = response.json()
    body_text = json.dumps(body).lower()
    for forbidden in FORBIDDEN_FIELDS:
        assert forbidden not in body_text, (
            f"forbidden field {forbidden!r} found in response body: {body!r}"
        )


@pytest.mark.asyncio
async def test_auth_config_endpoint_does_not_leak_via_cookie_or_authorization() -> None:
    """A request without any auth still gets the response — proves the endpoint is public.

    This is a behavioral test: if the endpoint required a session cookie, this
    request would 401/403. The test asserts 200.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Explicitly no cookies/Authorization.
        response = await client.get(
            "/v1/auth/config",
            headers={"Cookie": "", "Authorization": ""},
        )
    assert response.status_code == 200
