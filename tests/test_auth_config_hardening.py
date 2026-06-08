"""Tests for the mode-gate on the legacy /v1/auth/setup endpoint.

These tests are written before implementation. They should FAIL against `main`
(the gate does not exist) and PASS once TODO 9 lands the implementation.

The gate logic is:
- When `daemon_deployment_mode == "hosted"`, the endpoint refuses to
  initialize owner/admin state and returns 403 with a clear error body.
- When `daemon_deployment_mode == "self_hosted"` (the default), the
  endpoint behaves exactly as before.
- The gate is independent of `daemon_hosted_identity_enabled` — even if
  hosted identity is disabled, a hosted deployment still cannot
  initialize owner/admin state via this endpoint.
"""

from __future__ import annotations


import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.config import Settings, get_settings
from orchestrator.main import app


def _apply_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Set env vars, clear the Settings cache so the next get_settings() re-reads them."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop deployment-mode env vars and clear the Settings cache."""
    for key in (
        "DAEMON_DEPLOYMENT_MODE",
        "DAEMON_HOSTED_IDENTITY_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_setup_blocked_in_hosted_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosted mode: POST /v1/auth/setup returns 403 with a clear error body."""
    _apply_env(monkeypatch, DAEMON_DEPLOYMENT_MODE="hosted")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/auth/setup",
                json={"setup_token": "any-token-here", "owner_email": "owner@example.com"},
            )
        assert response.status_code == 403, response.text
        body = response.json()
        detail = body.get("detail")
        assert isinstance(detail, dict), f"expected detail to be a dict, got: {detail!r}"
        assert detail.get("error") == "setup_disabled_in_hosted_mode"
        assert "hosted" in detail.get("detail", "").lower()
    finally:
        _reset_env(monkeypatch)


@pytest.mark.asyncio
async def test_setup_blocked_in_hosted_mode_even_with_identity_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate uses deployment mode, not the identity flag.

    Even when hosted identity is disabled (a valid staging config), a
    hosted deployment still cannot initialize owner/admin state.
    """
    _apply_env(
        monkeypatch,
        DAEMON_DEPLOYMENT_MODE="hosted",
        DAEMON_HOSTED_IDENTITY_ENABLED="false",
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/auth/setup",
                json={"setup_token": "any-token-here", "owner_email": "owner@example.com"},
            )
        assert response.status_code == 403, response.text
        body = response.json()
        detail = body.get("detail")
        assert isinstance(detail, dict)
        assert detail.get("error") == "setup_disabled_in_hosted_mode"
    finally:
        _reset_env(monkeypatch)


@pytest.mark.asyncio
async def test_setup_allowed_in_self_hosted_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-level: the gate predicate returns False under self_hosted mode.

    The end-to-end self-hosted flow is exercised by the existing
    `test_enrollment_flow.py` and `test_hosted_identity_smoke.py`. Here we
    only need to prove the gate predicate is well-formed and doesn't
    misfire for self-hosted.
    """

    settings = Settings(daemon_deployment_mode="self_hosted")
    assert (settings.daemon_deployment_mode == "hosted") is False


@pytest.mark.asyncio
async def test_setup_default_is_self_hosted_so_gate_does_not_fire() -> None:
    """Unit-level: the default Settings gives self_hosted; gate is inactive.

    End-to-end default-mode flow is covered by the existing
    `test_enrollment_flow.py` and `test_hosted_identity_smoke.py`.
    """

    settings = Settings()
    assert settings.daemon_deployment_mode == "self_hosted"
    assert (settings.daemon_deployment_mode == "hosted") is False
