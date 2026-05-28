"""Tests for device enrollment flow."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.auth_tokens import hash_enrollment_code, hash_token
from orchestrator.main import app
from orchestrator.config import get_settings


SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class MockConn:
    def __init__(self, pool):
        self._pool = pool
        self._in_transaction = False
        self._session_insert_args = None
        self._pending_insert_args = None
        self._pending_update_args = None
        self._update_wrong_attempts = None

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql and "devices" in sql:
            return self._pool._active_count
        if "SELECT id FROM users" in sql and str(SINGLETON_ID) in str(args):
            return SINGLETON_ID if self._pool._singleton_exists else None
        if "INSERT INTO users" in sql:
            self._pool._singleton_exists = True
            return SINGLETON_ID
        if "SELECT NOW()" in sql:
            return datetime.now(timezone.utc)
        if "INSERT INTO devices" in sql:
            self._pool._device_created = True
            self._pool._active_count += 1
            return uuid.uuid4()
        if "INSERT INTO sessions" in sql:
            self._session_insert_args = args
            return uuid.uuid4()
        if "INSERT INTO pending_enrollments" in sql:
            self._pending_insert_args = args
            return None
        if "SELECT id, user_id, code_verifier_hash" in sql:
            pending_id = args[0]
            if pending_id in self._pool._pending_enrollments:
                return self._pool._pending_enrollments[pending_id]
            return None
        if "UPDATE pending_enrollments" in sql:
            if self._update_wrong_attempts is not None:
                self._update_wrong_attempts.append(args)
            else:
                self._pending_update_args = args
            return None
        return None

    async def execute(self, sql, *args):
        if "INSERT INTO sessions" in sql:
            self._session_insert_args = args
        if "INSERT INTO pending_enrollments" in sql:
            self._pending_insert_args = args
        if "UPDATE pending_enrollments" in sql:
            if "wrong_attempts_remaining" in sql:
                if "wrong_attempts_remaining = 0" in sql:
                    new_wrong_attempts = 0
                    pending_id = args[0]
                else:
                    new_wrong_attempts = args[0]
                    pending_id = args[1]
                if pending_id in self._pool._pending_enrollments:
                    self._pool._pending_enrollments[pending_id] = {
                        **self._pool._pending_enrollments[pending_id],
                        "wrong_attempts_remaining": new_wrong_attempts,
                    }
                if self._update_wrong_attempts is None:
                    self._update_wrong_attempts = []
                self._update_wrong_attempts.append(args)
            else:
                self._pending_update_args = args
        return None

    async def fetchrow(self, sql, *args):
        if "SELECT id, user_id, code_verifier_hash" in sql:
            pending_id = args[0]
            if pending_id in self._pool._pending_enrollments:
                return self._pool._pending_enrollments[pending_id]
            return None
        if "SELECT id FROM users" in sql:
            return {"id": SINGLETON_ID}
        return None

    @asynccontextmanager
    async def transaction(self):
        self._in_transaction = True
        try:
            yield self
        finally:
            self._in_transaction = False


class MockPool:
    def __init__(self, active_device_count=1, singleton_user_exists=True):
        self._active_count = active_device_count
        self._singleton_exists = singleton_user_exists
        self._closed = False
        self._device_created = False
        self._connections = []
        self._pending_enrollments = {}
        self._user_id = SINGLETON_ID
        self._session_insert_args = None
        self._update_wrong_attempts = None

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql and "devices" in sql:
            return self._active_count
        if "SELECT NOW()" in sql:
            return datetime.now(timezone.utc)
        return None

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
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
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "test-pepper-for-all-tests-12345678901234567890")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestEnrollStartRequiresAuth:
    @pytest.mark.asyncio
    async def test_enroll_start_without_token_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool()
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post("/v1/auth/enroll/start")

                    assert response.status_code == 401
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_enroll_start_with_invalid_token_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool()
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers("invalid-token"),
                    )

                    assert response.status_code == 401
        finally:
            restore_init(original)


class TestEnrollHappyPath:
    @pytest.mark.asyncio
    async def test_enroll_start_complete(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                settings = get_settings()
                pepper = validate_and_get_pepper(settings)

                access_token = "test-access-token-123"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    start_response = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers(access_token),
                    )

                    assert start_response.status_code == 200, start_response.text
                    start_data = start_response.json()
                    assert "pending_id" in start_data
                    assert "code" in start_data
                    assert "qr_payload" in start_data
                    assert "expires_at" in start_data
                    assert isinstance(start_data["expires_at"], int)
                    assert start_data["qr_payload"].startswith("daemon-enroll://")
                    assert "#" in start_data["qr_payload"]
                    pending_id = start_data["pending_id"]

                    code = start_data["code"]
                    code_verifier_hash = hash_enrollment_code(code, pepper)

                    pending_row = {
                        "id": uuid.UUID(pending_id),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                        "consumed_at": None,
                    }
                    state.db_pool._pending_enrollments[uuid.UUID(pending_id)] = pending_row

                    complete_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": code,
                            "client_kind": "web",
                        },
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert complete_response.status_code == 200, complete_response.text
                    complete_data = complete_response.json()
                    assert "access_token" in complete_data
                    assert complete_data["token_type"] == "Bearer"
                    assert "refresh_token" not in complete_data

                    cookie_header = complete_response.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" in cookie_header
                    assert "HttpOnly" in cookie_header

                    assert mock_pool._device_created is True

                    conn = mock_pool._connections[-1]
                    assert conn._session_insert_args is not None
                    assert conn._session_insert_args[2] == "web"

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_native_enroll_complete_returns_body_refresh_and_rejects_mixed_mode(
        self, setup_env, monkeypatch
    ):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                settings = get_settings()
                pepper = validate_and_get_pepper(settings)

                access_token = "test-access-token-native"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    start_response = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers(access_token),
                    )

                    assert start_response.status_code == 200
                    start_data = start_response.json()
                    pending_id = start_data["pending_id"]
                    code = start_data["code"]
                    code_verifier_hash = hash_enrollment_code(code, pepper)

                    pending_row = {
                        "id": uuid.UUID(pending_id),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                        "consumed_at": None,
                    }
                    state.db_pool._pending_enrollments[uuid.UUID(pending_id)] = pending_row

                    complete_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": code,
                            "client_kind": "native",
                        },
                    )

                    assert complete_response.status_code == 200, complete_response.text
                    complete_data = complete_response.json()
                    assert "access_token" in complete_data
                    assert "refresh_token" in complete_data
                    assert complete_data["refresh_token"] is not None
                    assert complete_data["token_type"] == "Bearer"

                    cookie_header = complete_response.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" not in cookie_header

                    conn = mock_pool._connections[-1]
                    assert conn._session_insert_args is not None
                    assert conn._session_insert_args[2] == "native"

                    pending_id_2 = str(uuid.uuid4())
                    pending_row_2 = {
                        "id": uuid.UUID(pending_id_2),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                        "consumed_at": None,
                    }
                    state.db_pool._pending_enrollments[uuid.UUID(pending_id_2)] = pending_row_2

                    mixed_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id_2,
                            "code": code,
                            "client_kind": "native",
                        },
                        cookies={"__Host-daemon_refresh": "some-refresh-token"},
                    )

                    assert mixed_response.status_code == 400
                    assert "native" in mixed_response.json()["detail"]

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestWrongAttemptsAndReplay:
    @pytest.mark.asyncio
    async def test_wrong_attempts_and_replay(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                settings = get_settings()
                pepper = validate_and_get_pepper(settings)

                access_token = "test-access-token-attempts"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    start_response = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers(access_token),
                    )

                    assert start_response.status_code == 200
                    start_data = start_response.json()
                    pending_id = start_data["pending_id"]
                    code = start_data["code"]
                    code_verifier_hash = hash_enrollment_code(code, pepper)

                    pending_row = {
                        "id": uuid.UUID(pending_id),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                        "consumed_at": None,
                    }
                    state.db_pool._pending_enrollments[uuid.UUID(pending_id)] = pending_row

                    wrong_response_1 = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": "0000-0000",
                            "client_kind": "native",
                        },
                    )

                    assert wrong_response_1.status_code == 401

                    wrong_response_2 = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": "1111-1111",
                            "client_kind": "native",
                        },
                    )

                    assert wrong_response_2.status_code == 401

                    wrong_response_3 = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": "2222-2222",
                            "client_kind": "native",
                        },
                    )

                    assert wrong_response_3.status_code == 410

                    replay_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": code,
                            "client_kind": "native",
                        },
                    )

                    assert replay_response.status_code == 410

                    unknown_pending_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": str(uuid.uuid4()),
                            "code": "0000-0000",
                            "client_kind": "native",
                        },
                    )

                    assert unknown_pending_response.status_code == 401

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_unknown_pending_id_does_not_decrement_other(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                settings = get_settings()
                pepper = validate_and_get_pepper(settings)

                access_token = "test-access-token-isolate"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    start_response = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers(access_token),
                    )

                    assert start_response.status_code == 200
                    start_data = start_response.json()
                    pending_id = start_data["pending_id"]
                    code = start_data["code"]
                    code_verifier_hash = hash_enrollment_code(code, pepper)

                    pending_row = {
                        "id": uuid.UUID(pending_id),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                        "consumed_at": None,
                    }
                    state.db_pool._pending_enrollments[uuid.UUID(pending_id)] = pending_row

                    unknown_pending_id = str(uuid.uuid4())
                    unknown_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": unknown_pending_id,
                            "code": "0000-0000",
                            "client_kind": "native",
                        },
                    )

                    assert unknown_response.status_code == 401

                    correct_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": code,
                            "client_kind": "native",
                        },
                    )

                    assert correct_response.status_code == 200

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestExpiredAndConsumed:
    @pytest.mark.asyncio
    async def test_expired_pending_returns_410(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                settings = get_settings()
                pepper = validate_and_get_pepper(settings)

                access_token = "test-access-token-expired"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    start_response = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers(access_token),
                    )

                    assert start_response.status_code == 200
                    start_data = start_response.json()
                    pending_id = start_data["pending_id"]
                    code = start_data["code"]
                    code_verifier_hash = hash_enrollment_code(code, pepper)

                    pending_row = {
                        "id": uuid.UUID(pending_id),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
                        "consumed_at": None,
                    }
                    state.db_pool._pending_enrollments[uuid.UUID(pending_id)] = pending_row

                    expired_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": code,
                            "client_kind": "native",
                        },
                    )

                    assert expired_response.status_code == 410

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_consumed_pending_returns_410(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                settings = get_settings()
                pepper = validate_and_get_pepper(settings)

                access_token = "test-access-token-consumed"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    start_response = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers(access_token),
                    )

                    assert start_response.status_code == 200
                    start_data = start_response.json()
                    pending_id = start_data["pending_id"]
                    code = start_data["code"]
                    code_verifier_hash = hash_enrollment_code(code, pepper)

                    pending_row = {
                        "id": uuid.UUID(pending_id),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                        "consumed_at": datetime.now(timezone.utc),
                    }
                    state.db_pool._pending_enrollments[uuid.UUID(pending_id)] = pending_row

                    consumed_response = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": code,
                            "client_kind": "native",
                        },
                    )

                    assert consumed_response.status_code == 410

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestMalformedPendingId:
    @pytest.mark.asyncio
    async def test_malformed_pending_id_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state

                access_token = "test-access-token-malformed"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    malformed_ids = [
                        "not-a-uuid",
                        "12345",
                        "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz",
                        "",
                        "00000000-0000-0000-0000-00000000000x",
                    ]
                    for malformed_id in malformed_ids:
                        response = await client.post(
                            "/v1/auth/enroll/complete",
                            json={
                                "pending_id": malformed_id,
                                "code": "0000-0000",
                                "client_kind": "native",
                            },
                        )
                        assert response.status_code == 401, f"Expected 401 for {malformed_id}, got {response.status_code}"

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestMalformedCode:
    @pytest.mark.asyncio
    async def test_malformed_code_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state

                access_token = "test-access-token-malformed-code"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    valid_pending_id = str(uuid.uuid4())
                    malformed_codes = [
                        "1234567",
                        "123456789",
                        "abcdefgh",
                        "1234-567",
                        "12345-678",
                        "12-345678",
                        "abcd-efgh",
                        "",
                        "1234 5678",
                        "1234567a",
                    ]
                    for malformed_code in malformed_codes:
                        response = await client.post(
                            "/v1/auth/enroll/complete",
                            json={
                                "pending_id": valid_pending_id,
                                "code": malformed_code,
                                "client_kind": "native",
                            },
                        )
                        assert response.status_code == 401, f"Expected 401 for code={malformed_code!r}, got {response.status_code}"

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestNoPlaintextCode:
    @pytest.mark.asyncio
    async def test_no_plaintext_code_in_db(self, setup_env, monkeypatch):
        mock_pool = MockPool(active_device_count=1)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                settings = get_settings()
                pepper = validate_and_get_pepper(settings)

                access_token = "test-access-token-noplaintext"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = SINGLETON_ID
                state.db_pool._device_id = uuid.uuid4()
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=SINGLETON_ID,
                            device_id=pool._device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    start_response = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers(access_token),
                    )

                    assert start_response.status_code == 200
                    start_data = start_response.json()

                    conn = mock_pool._connections[-1]
                    pending_insert = conn._pending_insert_args

                    assert pending_insert is not None
                    stored_hash = pending_insert[3]

                    code = start_data["code"]
                    assert code not in str(pending_insert)
                    assert code != stored_hash

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)
