"""Tests for refresh token rotation flow."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.auth_tokens import generate_token, hash_token
from orchestrator.main import app
from orchestrator.config import get_settings


SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class MockConn:
    def __init__(self, pool):
        self._pool = pool
        self._in_transaction = False

    async def fetchval(self, sql, *args):
        if "SELECT NOW()" in sql:
            return datetime.now(timezone.utc)
        if "COUNT(*)" in sql and "devices" in sql:
            return self._pool._active_count
        return None

    def _is_session_valid(self, row):
        now = datetime.now(timezone.utc)
        if row.get("refresh_consumed_at") is not None:
            return False
        if row.get("refresh_expires_at") <= now:
            return False
        if row.get("revoked_at") is not None:
            return False
        device_id = row.get("device_id")
        key = device_id if device_id in self._pool._devices else str(device_id)
        if key in self._pool._devices and self._pool._devices[key].get("revoked_at") is not None:
            return False
        return True

    async def fetchrow(self, sql, *args):
        if "UPDATE sessions" in sql and "RETURNING" in sql:
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if self._is_session_valid(row):
                    self._pool._sessions[token_hash] = {
                        **row,
                        "refresh_consumed_at": datetime.now(timezone.utc),
                    }
                    return self._pool._sessions[token_hash]
            return None
        if "SELECT client_kind" in sql and "refresh_token_hash" in sql and "refresh_consumed_at IS NULL" in sql:
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if self._is_session_valid(row):
                    return row
            return None
        if "SELECT" in sql and "refresh_consumed_at" in sql and "UPDATE" not in sql and "refresh_token_hash" in sql:
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if row.get("refresh_consumed_at") is not None:
                    return row
            return None
        if "SELECT" in sql and "refresh_token_hash" in sql and "id, user_id, device_id, client_kind" in sql:
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if row.get("refresh_consumed_at") is None:
                    return row
            return None
        if "SELECT id FROM users" in sql:
            return {"id": SINGLETON_ID}
        return None

    async def execute(self, sql, *args):
        if "INSERT INTO sessions" in sql:
            self._pool._captured_inserts.append({"sql": sql, "args": args})
        if "UPDATE devices" in sql and "revoked_at" in sql and args:
            device_id = args[0]
            key = device_id if device_id in self._pool._devices else str(device_id)
            if key in self._pool._devices:
                self._pool._devices[key]["revoked_at"] = datetime.now(timezone.utc)
        if "UPDATE sessions" in sql and "revoked_at" in sql and "device_id" in sql and args:
            device_id = args[0]
            for key, sess in self._pool._sessions.items():
                if sess["device_id"] == device_id:
                    self._pool._sessions[key] = {
                        **sess,
                        "revoked_at": datetime.now(timezone.utc),
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
    def __init__(self, sessions=None, devices=None, active_device_count=1):
        self._sessions = sessions or {}
        self._devices = devices or {}
        self._active_count = active_device_count
        self._closed = False
        self._connections = []
        self._captured_inserts = []

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql and "devices" in sql:
            return self._active_count
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


def _make_session(token_hash, user_id, device_id, client_kind, consumed=False,
                  expired=False, session_revoked=False, device_revoked=False):
    now = datetime.now(timezone.utc)
    if expired:
        refresh_expires_at = now - timedelta(hours=1)
    else:
        refresh_expires_at = now + timedelta(days=90)
    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "device_id": device_id,
        "client_kind": client_kind,
        "refresh_token_hash": token_hash,
        "refresh_expires_at": refresh_expires_at,
        "refresh_consumed_at": now if consumed else None,
        "revoked_at": now if session_revoked else None,
    }


class TestWebRefreshRotatesCookie:
    @pytest.mark.asyncio
    async def test_web_refresh_rotates_cookie(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "web")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 200, response.text
                    data = response.json()
                    assert "access_token" in data
                    assert "refresh_token" not in data
                    assert data["token_type"] == "Bearer"

                    cookie_header = response.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" in cookie_header
                    assert "HttpOnly" in cookie_header

                    updated = mock_pool._sessions.get(refresh_hash)
                    assert updated is not None
                    assert updated["refresh_consumed_at"] is not None
        finally:
            restore_init(original)


class TestConsumedRefreshReuse:
    @pytest.mark.asyncio
    async def test_consumed_refresh_reuse_revokes_device(self, setup_env, monkeypatch, caplog):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        device_id2 = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        consumed_session = _make_session(refresh_hash, user_id, device_id, "web", consumed=True)
        active_hash = hash_token(generate_token())
        active_session = _make_session(
            active_hash, user_id, device_id2, "web", consumed=False
        )
        devices = {
            str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None},
            str(device_id2): {"id": device_id2, "user_id": user_id, "revoked_at": None},
        }
        mock_pool = MockPool(
            sessions={refresh_hash: consumed_session, active_hash: active_session},
            devices=devices,
        )
        original = make_mock_init(mock_pool)
        try:
            caplog.set_level(logging.WARNING)
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 401, response.text
                    assert mock_pool._devices[str(device_id)]["revoked_at"] is not None
                    assert mock_pool._sessions[refresh_hash]["revoked_at"] is not None
                    assert any(
                        "device_id=" + str(device_id) in record.message
                        for record in caplog.records
                    )
                    assert not any(
                        refresh_token in record.message or refresh_hash in record.message
                        for record in caplog.records
                    )
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_consumed_refresh_reuse_via_cookie_includes_clear_cookie_header(
        self, setup_env, monkeypatch
    ):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        consumed_session = _make_session(refresh_hash, user_id, device_id, "web", consumed=True)
        devices = {
            str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None},
        }
        mock_pool = MockPool(
            sessions={refresh_hash: consumed_session},
            devices=devices,
        )
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 401, response.text
                    cookie_header = response.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" in cookie_header
                    assert "Max-Age=0" in cookie_header or "max-age=0" in cookie_header
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_consumed_reuse_revokes_same_device_sessions(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        consumed_session = _make_session(refresh_hash, user_id, device_id, "web", consumed=True)
        other_hash = hash_token(generate_token())
        other_session = _make_session(other_hash, user_id, device_id, "web", consumed=False)
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(
            sessions={refresh_hash: consumed_session, other_hash: other_session},
            devices=devices,
        )
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 401, response.text
                    assert mock_pool._sessions[other_hash]["revoked_at"] is not None
        finally:
            restore_init(original)


class TestNativeRefresh:
    @pytest.mark.asyncio
    async def test_native_refresh_returns_body_token_and_rejects_mixed_mode(
        self, setup_env, monkeypatch
    ):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "native")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        json={"refresh_token": refresh_token},
                    )

                    assert response.status_code == 200, response.text
                    data = response.json()
                    assert "access_token" in data
                    assert "refresh_token" in data
                    assert data["token_type"] == "Bearer"

                    cookie_header = response.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" not in cookie_header
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_native_rejects_cookie_body_mixed_mode(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "native")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        json={"refresh_token": refresh_token},
                        cookies={"__Host-daemon_refresh": refresh_token},
                    )

                    assert response.status_code == 400, response.text
                    assert "refresh token present in both" in response.json()["detail"]
        finally:
            restore_init(original)


class TestConcurrentRefresh:
    @pytest.mark.asyncio
    async def test_concurrent_refresh_one_success(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "web")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    results = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )
                    results2 = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    statuses = sorted([results.status_code, results2.status_code])
                    assert statuses == [200, 401]
        finally:
            restore_init(original)


class TestExpiredBadRevokedRefresh:
    @pytest.mark.asyncio
    async def test_unknown_refresh_token_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool(sessions={})
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        json={"refresh_token": "unknown-token"},
                    )

                    assert response.status_code == 401, response.text
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_missing_refresh_token_returns_400(self, setup_env, monkeypatch):
        mock_pool = MockPool(sessions={})
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        json={},
                    )

                    assert response.status_code == 400, response.text
                    assert "refresh token required" in response.json()["detail"]
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_expired_refresh_token_returns_401(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        expired_session = _make_session(
            refresh_hash, user_id, device_id, "web", expired=True
        )
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: expired_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 401, response.text
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_session_revoked_refresh_returns_401(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        revoked_session = _make_session(
            refresh_hash, user_id, device_id, "web", session_revoked=True
        )
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: revoked_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 401, response.text
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_device_revoked_refresh_returns_401(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        valid_session = _make_session(refresh_hash, user_id, device_id, "web")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": datetime.now(timezone.utc)}}
        mock_pool = MockPool(sessions={refresh_hash: valid_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 401, response.text
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_expired_refresh_no_device_revocation(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        expired_session = _make_session(
            refresh_hash, user_id, device_id, "web", expired=True
        )
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: expired_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 401, response.text
                    assert mock_pool._devices[str(device_id)]["revoked_at"] is None
        finally:
            restore_init(original)


class TestClientKindPreservation:
    @pytest.mark.asyncio
    async def test_web_refresh_preserves_client_kind_web(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "web")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 200, response.text
                    assert len(mock_pool._captured_inserts) >= 1
                    insert_args = mock_pool._captured_inserts[0]["args"]
                    assert insert_args[2] == "web"
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_native_refresh_preserves_client_kind_native(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "native")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        json={"refresh_token": refresh_token},
                    )

                    assert response.status_code == 200, response.text
                    assert len(mock_pool._captured_inserts) >= 1
                    insert_args = mock_pool._captured_inserts[0]["args"]
                    assert insert_args[2] == "native"
        finally:
            restore_init(original)


class TestRefreshTransportMismatch:
    @pytest.mark.asyncio
    async def test_web_token_via_body_returns_400_no_replacement(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "web")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        json={"refresh_token": refresh_token},
                    )

                    assert response.status_code == 400, response.text
                    assert "transport mismatch" in response.json()["detail"]
                    assert len(mock_pool._captured_inserts) == 0
                    assert mock_pool._sessions[refresh_hash]["refresh_consumed_at"] is None
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_native_token_via_cookie_returns_400_no_replacement(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "native")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )

                    assert response.status_code == 400, response.text
                    assert "transport mismatch" in response.json()["detail"]
                    assert len(mock_pool._captured_inserts) == 0
                    assert mock_pool._sessions[refresh_hash]["refresh_consumed_at"] is None
        finally:
            restore_init(original)


class TestWebRefreshCSRF:
    @pytest.mark.asyncio
    async def test_cross_site_refresh_rejected(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        device_id = uuid.uuid4()
        refresh_token = generate_token()
        refresh_hash = hash_token(refresh_token)
        old_session = _make_session(refresh_hash, user_id, device_id, "web")
        devices = {str(device_id): {"id": device_id, "user_id": user_id, "revoked_at": None}}
        mock_pool = MockPool(sessions={refresh_hash: old_session}, devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": refresh_token},
                        headers={
                            "Origin": "https://evil.example.com",
                            "Sec-Fetch-Site": "cross-site",
                        },
                    )

                    assert response.status_code == 403, response.text
                    assert "CSRF" in response.json()["detail"] or "origin" in response.json()["detail"]
        finally:
            restore_init(original)
