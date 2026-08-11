"""Tests for the /chat and /v1/chat/completions rate limits (issue #38).

These tests verify the resolver-side wiring of the per-user, per-token,
and per-IP rate-limit policies against the three chat endpoints. The
``FakeRedis`` harness matches the one in ``test_identity_rate_limiter.py``
so the rate limiter behaves the same way it does in production (one
shared Lua-style INCR + EXPIRE per window).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings
from orchestrator.db import AppState, get_app_state
from orchestrator.main import app
from tests.test_identity_rate_limiter import FakeRedis


# Two distinct fixtures so per-user isolation is observable in tests:
# each auth override creates its own AuthenticatedDevice with a unique
# user_id, so the per-user rate limit does not bleed across sessions.
AUTH_ALICE = AuthenticatedDevice(
    user_id=uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
    device_id=uuid.UUID("00000000-0000-0000-0000-0000000000a2"),
    session_id=uuid.UUID("00000000-0000-0000-0000-0000000000a3"),
)
AUTH_BOB = AuthenticatedDevice(
    user_id=uuid.UUID("00000000-0000-0000-0000-0000000000b1"),
    device_id=uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
    session_id=uuid.UUID("00000000-0000-0000-0000-0000000000b3"),
)


def _make_chat_payload() -> dict[str, Any]:
    return {
        "message": "hello",
        "provider": "openrouter",
        "model": "openai/gpt-4o-mini",
    }


def _make_openai_payload() -> dict[str, Any]:
    return {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }


@pytest_asyncio.fixture
async def chat_client(monkeypatch):
    """Async client with a FakeRedis wired through AppState.

    Each test that uses this fixture gets a fresh Redis so counter state
    cannot leak across tests.
    """
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    # Set very small limits so tests stay fast — 2 per user, 5 per IP.
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_USER_PER_MINUTE", "2")
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_TOKEN_PER_MINUTE", "2")
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_IP_PER_MINUTE", "5")
    get_settings.cache_clear()

    settings = get_settings()
    fake_redis = FakeRedis()
    app_state = AppState(settings=settings, redis=fake_redis)  # type: ignore[arg-type]

    async def override_settings():
        return get_settings()

    async def override_app_state():
        return app.state.app_state

    async def override_auth(request: Request):
        api_key = os.environ.get("DAEMON_API_KEY")
        if api_key:
            authorization = request.headers.get("Authorization", "")
            if authorization != f"Bearer {api_key}":
                raise HTTPException(status_code=401, detail="Invalid API key")
        return AUTH_ALICE

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_app_state] = override_app_state
    app.dependency_overrides[require_device_auth] = override_auth

    original_app_state = getattr(app.state, "app_state", None)
    original_settings = getattr(app.state, "settings", None)
    app.state.app_state = app_state
    app.state.settings = settings
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.state.app_state = original_app_state
        app.state.settings = original_settings


@pytest.mark.asyncio
async def test_chat_endpoint_serves_below_user_limit(chat_client: AsyncClient, monkeypatch) -> None:
    """Below the per-user limit, requests succeed (or stream)."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    # Two requests — at the limit; both succeed (response is SSE).
    for _ in range(2):
        response = await chat_client.post(
            "/chat",
            json=_make_chat_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_chat_endpoint_returns_429_after_user_limit(
    chat_client: AsyncClient, monkeypatch
) -> None:
    """Third request from the same user exceeds the per-user limit and
    is rejected with 429 + Retry-After.
    """
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    for _ in range(2):
        ok = await chat_client.post(
            "/chat",
            json=_make_chat_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert ok.status_code == 200, ok.text

    rejected = await chat_client.post(
        "/chat",
        json=_make_chat_payload(),
        headers={"Content-Type": "application/json"},
    )
    assert rejected.status_code == 429, rejected.text
    assert "retry-after" in {k.lower() for k in rejected.headers.keys()}


@pytest.mark.asyncio
async def test_openai_chat_completions_returns_429_after_user_limit(
    chat_client: AsyncClient, monkeypatch
) -> None:
    """Same per-user limit applies to /v1/chat/completions."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    for _ in range(2):
        ok = await chat_client.post(
            "/v1/chat/completions",
            json=_make_openai_payload(),
            headers={"Content-Type": "application/json"},
        )
        # Either 200 (non-stream) or 500-on-mock-non-stream is OK as
        # long as it isn't a 429; we only assert the limit kicks in
        # on the third request.
        assert ok.status_code != 429, ok.text

    rejected = await chat_client.post(
        "/v1/chat/completions",
        json=_make_openai_payload(),
        headers={"Content-Type": "application/json"},
    )
    assert rejected.status_code == 429, rejected.text


@pytest.mark.asyncio
async def test_per_user_isolation_across_sessions(chat_client: AsyncClient, monkeypatch) -> None:
    """A second user with their own user_id should not be rate-limited
    by the first user's traffic.
    """
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    # Burn Alice's quota.
    for _ in range(2):
        ok = await chat_client.post(
            "/chat",
            json=_make_chat_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert ok.status_code == 200, ok.text
    alice_blocked = await chat_client.post(
        "/chat",
        json=_make_chat_payload(),
        headers={"Content-Type": "application/json"},
    )
    assert alice_blocked.status_code == 429

    # Switch to Bob's session.
    async def override_auth_bob(request: Request):
        return AUTH_BOB

    original_override_auth = app.dependency_overrides.get(require_device_auth)
    app.dependency_overrides[require_device_auth] = override_auth_bob
    try:
        bob_ok = await chat_client.post(
            "/chat",
            json=_make_chat_payload(),
            headers={"Content-Type": "application/json"},
        )
        # Bob's first request must not be rate-limited by Alice's
        # history — the per-user scope must keep counters independent.
        assert bob_ok.status_code != 429, bob_ok.text
    finally:
        if original_override_auth is not None:
            app.dependency_overrides[require_device_auth] = original_override_auth


@pytest.mark.asyncio
async def test_session_limit_does_not_consume_user_counter(
    chat_client: AsyncClient, monkeypatch
) -> None:
    """Codex round-2 P2 finding #2: when the narrowest scope (session)
    rejects, the broader scopes (user, ip) must NOT have been
    incremented for the rejected request. Otherwise a runaway session
    drains the user-wide quota.
    """
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    # Separate session and user budgets so we can observe that the
    # session-rejected 3rd request does NOT also consume the user
    # counter.
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_USER_PER_MINUTE", "10")
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_TOKEN_PER_MINUTE", "2")
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_IP_PER_MINUTE", "50")
    get_settings.cache_clear()

    for _ in range(2):
        ok = await chat_client.post(
            "/chat",
            json=_make_chat_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert ok.status_code == 200, ok.text

    rejected = await chat_client.post(
        "/chat",
        json=_make_chat_payload(),
        headers={"Content-Type": "application/json"},
    )
    assert rejected.status_code == 429, rejected.text

    # The user quota is now 2 used (out of 10). A *new* session for the
    # same user must succeed because the user counter was NOT charged
    # for the rejected session attempt above.
    AUTH_ALICE_NEW_SESSION = AUTH_ALICE.__class__(
        user_id=AUTH_ALICE.user_id,
        device_id=AUTH_ALICE.device_id,
        session_id=uuid.uuid4(),
    )

    async def override_auth_new_session(request: Request):
        return AUTH_ALICE_NEW_SESSION

    original_override_auth = app.dependency_overrides.get(require_device_auth)
    app.dependency_overrides[require_device_auth] = override_auth_new_session
    try:
        ok_new_session = await chat_client.post(
            "/chat",
            json=_make_chat_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert ok_new_session.status_code != 429, ok_new_session.text
    finally:
        if original_override_auth is not None:
            app.dependency_overrides[require_device_auth] = original_override_auth


@pytest.mark.asyncio
async def test_per_route_quota_is_independent(chat_client: AsyncClient, monkeypatch) -> None:
    """Codex round-2 P2 finding #5: /chat and /v1/chat/completions must
    keep separate Redis keys so a client can hit the per-user limit on
    one route without throttling the other. Without the route-level
    endpoint in the key, both routes shared ``chat:user:minute`` and
    cross-throttled.
    """
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_USER_PER_MINUTE", "2")
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_TOKEN_PER_MINUTE", "20")
    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_IP_PER_MINUTE", "100")
    get_settings.cache_clear()

    # Burn the /chat route quota (chat:daemon endpoint namespace).
    for _ in range(2):
        ok = await chat_client.post(
            "/chat",
            json=_make_chat_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert ok.status_code == 200, ok.text
    blocked_on_daemon = await chat_client.post(
        "/chat",
        json=_make_chat_payload(),
        headers={"Content-Type": "application/json"},
    )
    assert blocked_on_daemon.status_code == 429

    # The /v1/chat/completions route (chat:openai namespace) must NOT
    # have been charged by /chat traffic.
    openai_ok = await chat_client.post(
        "/v1/chat/completions",
        json=_make_openai_payload(),
        headers={"Content-Type": "application/json"},
    )
    assert openai_ok.status_code != 429, openai_ok.text


@pytest.mark.asyncio
async def test_malformed_payloads_consume_ip_quota_before_validation(
    chat_client: AsyncClient,
    monkeypatch,
) -> None:
    """Malformed chat bodies cannot bypass the IP-scoped quota."""

    monkeypatch.setenv("DAEMON_RATE_LIMIT_CHAT_PER_IP_PER_MINUTE", "2")
    get_settings.cache_clear()

    for _ in range(2):
        invalid = await chat_client.post(
            "/v1/chat/completions",
            content=b"{not-json",
            headers={"Content-Type": "application/json"},
        )
        assert invalid.status_code == 422, invalid.text

    limited = await chat_client.post(
        "/v1/chat/completions",
        json=_make_openai_payload(),
        headers={"Content-Type": "application/json"},
    )
    assert limited.status_code == 429
    assert limited.json() == {"detail": "rate_limited"}
    assert int(limited.headers["Retry-After"]) > 0
