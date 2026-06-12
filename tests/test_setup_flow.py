"""Tests for first-boot setup flow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.auth_tokens import generate_setup_token, hash_token
from orchestrator.main import app
from orchestrator.config import get_settings


SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class MockConn:
    def __init__(self, pool):
        self._pool = pool
        self._in_transaction = False
        self._session_insert_args = None

    async def fetchval(self, sql, *args):
        if "FROM system_state" in sql:
            return self._pool._system_state.get(args[0])
        if "COUNT(*)" in sql and "devices" in sql:
            return self._pool._active_count
        if "EXISTS" in sql and "SELECT 1" in sql and "sessions" in sql:
            return self._pool._has_valid_session
        if "SELECT id FROM users" in sql and str(SINGLETON_ID) in str(args):
            return SINGLETON_ID if self._pool._singleton_exists else None
        if "INSERT INTO users" in sql:
            self._pool._singleton_exists = True
            return SINGLETON_ID
        if "INSERT INTO tenants" in sql:
            if self._pool._tenant_id is None:
                self._pool._tenant_id = uuid.uuid4()
                return self._pool._tenant_id
            return None
        if "SELECT role" in sql and "tenant_memberships" in sql:
            return "owner" if self._pool._membership_exists else None
        if "INSERT INTO devices" in sql:
            self._pool._device_created = True
            self._pool._active_count = 1
            self._pool._device_insert_args = args
            return uuid.uuid4()
        if "INSERT INTO sessions" in sql:
            self._session_insert_args = args
            return uuid.uuid4()
        if "INSERT INTO tenant_memberships" in sql:
            if self._pool._membership_exists:
                return None
            self._pool._membership_exists = True
            return "owner"
        return None

    async def execute(self, sql, *args):
        if "INSERT INTO system_state" in sql:
            key, value = args[:2]
            self._pool._system_state[key] = value
            return None
        if "DELETE FROM system_state" in sql:
            self._pool._system_state.pop(args[0], None)
            return None
        if "INSERT INTO sessions" in sql:
            self._session_insert_args = args
        if "pg_advisory_xact_lock" in sql:
            self._pool._lock_acquired = True
        if "UPDATE devices SET revoked_at = NOW() WHERE revoked_at IS NULL" in sql:
            self._pool._active_count = 0
        return None

    async def fetchrow(self, sql, *args):
        if "INSERT INTO system_state" in sql and "ON CONFLICT" in sql:
            key, value = args[:2]
            if key in self._pool._system_state:
                return None
            self._pool._system_state[key] = value
            return {"value": value}
        if (
            "SELECT id, owner_user_id, kind, name FROM tenants" in sql
            and self._pool._tenant_id is not None
        ):
            return {
                "id": self._pool._tenant_id,
                "owner_user_id": SINGLETON_ID,
                "kind": "personal",
                "name": "Personal",
            }
        return None

    @asynccontextmanager
    async def transaction(self):
        self._in_transaction = True
        try:
            yield self
        finally:
            self._in_transaction = False


class MockPool:
    def __init__(self, active_device_count=0, singleton_user_exists=False, has_valid_session=True):
        self._active_count = active_device_count
        self._singleton_exists = singleton_user_exists
        self._has_valid_session = has_valid_session
        self._closed = False
        self._lock_acquired = False
        self._device_created = False
        self._device_insert_args = None
        self._tenant_id = None
        self._membership_exists = False
        self._connections = []
        self._system_state = {}

    def set_setup_token(self, plaintext: str) -> None:
        self._system_state["auth.setup_token_hash"] = hash_token(plaintext)

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql and "devices" in sql:
            return self._active_count
        if "EXISTS" in sql and "SELECT 1" in sql and "sessions" in sql:
            return self._has_valid_session
        return None

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        if "UPDATE devices SET revoked_at = NOW() WHERE revoked_at IS NULL" in sql:
            self._active_count = 0
        return None

    @asynccontextmanager
    async def acquire(self):
        conn = MockConn(self)
        self._connections.append(conn)
        yield conn

    async def close(self):
        self._closed = True


def make_mock_init(mock_pool):
    import orchestrator.main as main_module

    original_init = main_module.init_app_state

    async def mock_init(settings):
        from orchestrator.db import AppState

        state = AppState(settings=settings)
        state.db_pool = mock_pool
        state.redis = None
        state.memory_store = None
        state.video_credits_dal = None
        state.setup_token_hash = None
        return state

    main_module.init_app_state = mock_init
    return original_init


def restore_init(original):
    import orchestrator.main as main_module

    main_module.init_app_state = original


@pytest_asyncio.fixture
async def setup_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ALLOWED_ORIGINS", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_PUBLIC_ORIGIN", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestStartupNoDeviceLogsToken:
    @pytest.mark.asyncio
    async def test_startup_logs_setup_token_when_no_active_devices(
        self, setup_env, monkeypatch, caplog
    ):
        mock_pool = MockPool(active_device_count=0)
        original = make_mock_init(mock_pool)
        try:
            caplog.set_level(logging.INFO)
            caplog.clear()
            async with app.router.lifespan_context(app):
                pass
            assert ">>> Daemon setup required" in caplog.text
        finally:
            restore_init(original)


class TestStartupActiveDeviceSuppressesToken:
    @pytest.mark.asyncio
    async def test_startup_does_not_log_token_when_active_devices_exist(
        self, setup_env, monkeypatch, caplog
    ):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            caplog.clear()
            async with app.router.lifespan_context(app):
                pass
            assert ">>> Daemon setup required" not in caplog.text
        finally:
            restore_init(original)


class TestStartupRecoveryWithStaleDevices:
    @pytest.mark.asyncio
    async def test_startup_generates_recovery_token_when_devices_have_no_valid_sessions(
        self, setup_env, monkeypatch, caplog
    ):
        mock_pool = MockPool(active_device_count=1, has_valid_session=False)
        original = make_mock_init(mock_pool)
        try:
            caplog.set_level(logging.INFO)
            caplog.clear()
            async with app.router.lifespan_context(app):
                pass
            assert ">>> Daemon recovery:" in caplog.text
            assert mock_pool._active_count == 0
        finally:
            restore_init(original)


class TestSetupHappyPath:
    @pytest.mark.asyncio
    async def test_setup_creates_first_device(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=0, singleton_user_exists=False)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                plaintext = generate_setup_token()
                mock_pool.set_setup_token(plaintext)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": plaintext},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 200, response.text
                    data = response.json()
                    assert "access_token" in data
                    assert data["token_type"] == "Bearer"
                    assert "refresh_token" not in data
                    cookie_header = response.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" in cookie_header
                    assert "HttpOnly" in cookie_header
                    assert mock_pool._lock_acquired is True
                    assert mock_pool._device_created is True
                    session_insert_args = None
                    for conn in mock_pool._connections:
                        if conn._session_insert_args is not None:
                            session_insert_args = conn._session_insert_args
                            break
                    assert session_insert_args is not None, (
                        "No session INSERT was recorded during setup; "
                        "session_insert_args should not be None"
                    )
                    assert session_insert_args[2] == "web", (
                        f"Expected client_kind='web', got {session_insert_args[2]}"
                    )
                    assert mock_pool._tenant_id is not None
                    assert mock_pool._device_insert_args is not None
                    assert mock_pool._device_insert_args[1] == mock_pool._tenant_id
                    assert session_insert_args[3] == mock_pool._tenant_id
                    assert "auth.setup_token_hash" not in mock_pool._system_state
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_setup_uses_configured_private_refresh_ttl(self, setup_env, monkeypatch):
        monkeypatch.setenv("DAEMON_PRIVATE_REFRESH_TTL_DAYS", "7")
        get_settings.cache_clear()
        mock_pool = MockPool(active_device_count=0, singleton_user_exists=False)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                plaintext = generate_setup_token()
                mock_pool.set_setup_token(plaintext)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": plaintext},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 200, response.text
                    assert "Max-Age=604800" in response.headers.get("set-cookie", "")
                    session_insert_args = None
                    for conn in mock_pool._connections:
                        if conn._session_insert_args is not None:
                            session_insert_args = conn._session_insert_args
                            break
                    assert session_insert_args is not None
                    refresh_expires_at = session_insert_args[7]
                    delta_days = (
                        refresh_expires_at - datetime.now(timezone.utc)
                    ).total_seconds() / 86400
                    assert 6.5 <= delta_days <= 7.5
        finally:
            restore_init(original)


class TestSetupWrongToken:
    @pytest.mark.asyncio
    async def test_wrong_token_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=0)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                correct_token = generate_setup_token()
                mock_pool.set_setup_token(correct_token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": "wrong-token"},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 401
                    assert "auth.setup_token_hash" in mock_pool._system_state
                    assert mock_pool._lock_acquired is True
        finally:
            restore_init(original)


class TestSetupAlreadyComplete:
    @pytest.mark.asyncio
    async def test_second_setup_returns_setup_already_complete(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                plaintext = generate_setup_token()
                mock_pool.set_setup_token(plaintext)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": plaintext},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 409
                    assert response.json()["detail"] == "setup_already_complete"
        finally:
            restore_init(original)


class TestSetupNoTokenWhenDeactivated:
    @pytest.mark.asyncio
    async def test_no_setup_token_returns_409(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=0)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                mock_pool._system_state.pop("auth.setup_token_hash", None)
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": "any-token"},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 409
                    assert response.json()["detail"] == "setup_already_complete"
        finally:
            restore_init(original)


class TestSetupCSRFRejection:
    @pytest.mark.asyncio
    async def test_cross_site_setup_rejected(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=0)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                plaintext = generate_setup_token()
                mock_pool.set_setup_token(plaintext)
                mock_pool._lock_acquired = False

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": plaintext},
                        headers={
                            "Origin": "https://evil.example",
                            "Sec-Fetch-Site": "cross-site",
                        },
                    )

                    assert response.status_code == 403
                    assert mock_pool._lock_acquired is False
        finally:
            restore_init(original)


class TestActiveDeviceConditionIgnoresUsersTable:
    @pytest.mark.asyncio
    async def test_setup_runs_when_users_exist_but_no_active_devices(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=0, singleton_user_exists=True)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                plaintext = generate_setup_token()
                mock_pool.set_setup_token(plaintext)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": plaintext},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 200
        finally:
            restore_init(original)


class TestConcurrentSetup:
    @pytest.mark.asyncio
    async def test_concurrent_correct_submissions_produce_one_success(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=0)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                plaintext = generate_setup_token()
                mock_pool.set_setup_token(plaintext)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    results = await asyncio.gather(
                        client.post(
                            "/v1/auth/setup",
                            json={"setup_token": plaintext},
                            headers={
                                "Origin": "https://app.daemon.ai",
                                "Sec-Fetch-Site": "same-origin",
                            },
                        ),
                        client.post(
                            "/v1/auth/setup",
                            json={"setup_token": plaintext},
                            headers={
                                "Origin": "https://app.daemon.ai",
                                "Sec-Fetch-Site": "same-origin",
                            },
                        ),
                        client.post(
                            "/v1/auth/setup",
                            json={"setup_token": plaintext},
                            headers={
                                "Origin": "https://app.daemon.ai",
                                "Sec-Fetch-Site": "same-origin",
                            },
                        ),
                    )

                    statuses = [r.status_code for r in results]
                    success_count = statuses.count(200)
                    already_complete_count = statuses.count(409)

                    assert success_count == 1, (
                        f"Expected exactly 1 success, got {success_count}: {statuses}"
                    )
                    assert already_complete_count == 2, (
                        f"Expected 2 already_complete, got {already_complete_count}: {statuses}"
                    )
        finally:
            restore_init(original)


class TestSetupCookiePolicyValidation:
    """Verify cookie policy is validated BEFORE state mutation.

    When production environment uses insecure cookies, the setup request must be
    rejected BEFORE any device/session creation and BEFORE clearing setup_token_hash.
    """

    @pytest.mark.asyncio
    async def test_production_insecure_cookie_rejected_before_state_mutation(self, monkeypatch):
        """CookiePolicyError raised before device/session creation and before setup_token_hash cleared."""
        # Use production + insecure cookie to trigger CookiePolicyError
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")
        monkeypatch.setenv("REDIS_URL", "")
        monkeypatch.setenv("DAEMON_ALLOWED_ORIGINS", "https://app.daemon.ai")
        monkeypatch.setenv("DAEMON_PUBLIC_ORIGIN", "https://app.daemon.ai")
        monkeypatch.setenv("DAEMON_ENVIRONMENT", "production")
        monkeypatch.setenv("DAEMON_COOKIE_SECURE", "false")
        monkeypatch.setenv("DAEMON_AUTH_PEPPER", "test-pepper-for-all-tests-12345678901234567890")
        get_settings.cache_clear()

        mock_pool = MockPool(active_device_count=0, singleton_user_exists=False)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                plaintext = generate_setup_token()
                mock_pool.set_setup_token(plaintext)
                original_setup_token_hash = mock_pool._system_state["auth.setup_token_hash"]

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": plaintext},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    # Request must fail
                    assert response.status_code == 500, (
                        f"Expected 500 for CookiePolicyError, got {response.status_code}: {response.text}"
                    )
                    # setup token hash must NOT be cleared (validation happened before state mutation)
                    assert (
                        mock_pool._system_state["auth.setup_token_hash"]
                        == original_setup_token_hash
                    ), (
                        "setup token hash was cleared! "
                        f"Expected {original_setup_token_hash}, "
                        f"got {mock_pool._system_state.get('auth.setup_token_hash')}"
                    )
                    # No device must have been created
                    assert mock_pool._device_created is False, (
                        "Device was created despite CookiePolicyError"
                    )
        finally:
            restore_init(original)
            get_settings.cache_clear()
