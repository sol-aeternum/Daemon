"""Tests for device management endpoints (list and revoke)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.auth_tokens import hash_token
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
        return None

    async def fetchrow(self, sql, *args):
        # Device list query
        if "SELECT id, display_name, platform, created_at, last_seen_at, revoked_at" in sql and "user_id" in sql:
            user_id = args[0]
            if user_id != SINGLETON_ID:
                return None
            # Return only devices belonging to this user
            result = []
            for d in self._pool._devices.values():
                if d.get("user_id") == user_id:
                    if "revoked_at IS NULL" in sql and d.get("revoked_at") is not None:
                        continue
                    result.append(d)
            if not result:
                return None
            return result[0]
        # Device revoke lookup
        if "SELECT id, user_id, revoked_at" in sql and "FOR UPDATE" in sql:
            target_id = args[0]
            user_id = args[1]
            for d in self._pool._devices.values():
                if d["id"] == target_id and d.get("user_id") == user_id:
                    return d
            return None
        return None

    async def fetch(self, sql, *args):
        # Device list query - returns list
        if "SELECT id, display_name, platform, created_at, last_seen_at, revoked_at" in sql and "user_id" in sql:
            user_id = args[0]
            result = []
            for d in self._pool._devices.values():
                if d.get("user_id") == user_id:
                    if "revoked_at IS NULL" in sql and d.get("revoked_at") is not None:
                        continue
                    result.append(d)
            return result
        return []

    async def execute(self, sql, *args):
        # Revoke device
        if "UPDATE devices SET revoked_at" in sql:
            device_id = args[0]
            key = device_id if device_id in self._pool._devices else str(device_id)
            if key in self._pool._devices:
                self._pool._devices[key]["revoked_at"] = datetime.now(timezone.utc)
        # Revoke sessions for device
        if "UPDATE sessions SET revoked_at" in sql and "device_id" in sql:
            device_id = args[0]
            for sess_key, sess in list(self._pool._sessions.items()):
                if sess.get("device_id") == device_id:
                    self._pool._sessions[sess_key] = {
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
    def __init__(self, devices=None, sessions=None):
        self._devices = devices or {}
        self._sessions = sessions or {}
        self._closed = False
        self._connections = []

    async def fetchval(self, sql, *args):
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


def _make_device(device_id, user_id, display_name, platform="web", revoked_at=None, last_seen_at=None):
    now = datetime.now(timezone.utc)
    return {
        "id": device_id,
        "user_id": user_id,
        "display_name": display_name,
        "platform": platform,
        "created_at": now - timedelta(days=30),
        "last_seen_at": last_seen_at or (now - timedelta(hours=1)),
        "revoked_at": revoked_at,
    }


# ---------------------------------------------------------------------------
# GET /v1/auth/devices
# ---------------------------------------------------------------------------

class TestListDevicesRequiresAuth:
    @pytest.mark.asyncio
    async def test_list_devices_without_auth_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool()
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get("/v1/auth/devices")
                    assert response.status_code == 401
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_list_devices_with_invalid_token_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool()
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/v1/auth/devices",
                        headers=make_auth_headers("invalid-token"),
                    )
                    assert response.status_code == 401
        finally:
            restore_init(original)


class TestListDevicesOwnOnly:
    @pytest.mark.asyncio
    async def test_lists_only_own_devices(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        my_device_id = uuid.uuid4()
        other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        other_device_id = uuid.uuid4()

        devices = {
            str(my_device_id): _make_device(my_device_id, user_id, "My Phone", "native"),
            str(other_device_id): _make_device(other_device_id, other_user_id, "Other User Device", "web"),
        }

        mock_pool = MockPool(devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-list-own"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = my_device_id
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=my_device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/v1/auth/devices",
                        headers=make_auth_headers(access_token),
                    )

                    assert response.status_code == 200, response.text
                    data = response.json()
                    assert "devices" in data
                    device_ids = [d["id"] for d in data["devices"]]
                    assert str(my_device_id) in device_ids
                    assert str(other_device_id) not in device_ids

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestListDevicesCurrentFlag:
    @pytest.mark.asyncio
    async def test_current_flag_true_for_requesting_device(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        my_device_id = uuid.uuid4()
        other_device_id = uuid.uuid4()

        devices = {
            str(my_device_id): _make_device(my_device_id, user_id, "My Phone", "native"),
            str(other_device_id): _make_device(other_device_id, user_id, "My Laptop", "web"),
        }

        mock_pool = MockPool(devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-current-flag"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = my_device_id
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=my_device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/v1/auth/devices",
                        headers=make_auth_headers(access_token),
                    )

                    assert response.status_code == 200, response.text
                    data = response.json()
                    device_map = {d["id"]: d for d in data["devices"]}
                    assert device_map[str(my_device_id)]["current"] is True
                    assert device_map[str(my_device_id)]["revoked"] is False
                    assert device_map[str(other_device_id)]["current"] is False
                    assert device_map[str(other_device_id)]["revoked"] is False

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestListDevicesRevokedExclusion:
    @pytest.mark.asyncio
    async def test_excludes_revoked_by_default(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        active_id = uuid.uuid4()
        revoked_id = uuid.uuid4()

        devices = {
            str(active_id): _make_device(active_id, user_id, "Active Device", "web"),
            str(revoked_id): _make_device(
                revoked_id, user_id, "Revoked Device", "native",
                revoked_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        }

        mock_pool = MockPool(devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-exclude-revoked"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = active_id
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=active_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/v1/auth/devices",
                        headers=make_auth_headers(access_token),
                    )

                    assert response.status_code == 200, response.text
                    data = response.json()
                    device_ids = [d["id"] for d in data["devices"]]
                    assert str(active_id) in device_ids
                    assert str(revoked_id) not in device_ids

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_include_revoked_true_shows_revoked(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        active_id = uuid.uuid4()
        revoked_id = uuid.uuid4()

        devices = {
            str(active_id): _make_device(active_id, user_id, "Active Device", "web"),
            str(revoked_id): _make_device(
                revoked_id, user_id, "Revoked Device", "native",
                revoked_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        }

        mock_pool = MockPool(devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-include-revoked"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = active_id
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=active_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/v1/auth/devices?include_revoked=true",
                        headers=make_auth_headers(access_token),
                    )

                    assert response.status_code == 200, response.text
                    data = response.json()
                    device_ids = [d["id"] for d in data["devices"]]
                    assert str(active_id) in device_ids
                    assert str(revoked_id) in device_ids
                    active_device = next(d for d in data["devices"] if d["id"] == str(active_id))
                    revoked_device = next(d for d in data["devices"] if d["id"] == str(revoked_id))
                    assert active_device["revoked"] is False
                    assert revoked_device["revoked"] is True

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


# ---------------------------------------------------------------------------
# DELETE /v1/auth/devices/{id}
# ---------------------------------------------------------------------------

class TestRevokeDeviceRequiresAuth:
    @pytest.mark.asyncio
    async def test_revoke_without_auth_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool()
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    device_id = str(uuid.uuid4())
                    response = await client.delete(f"/v1/auth/devices/{device_id}")
                    assert response.status_code == 401
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_revoke_with_invalid_token_returns_401(self, setup_env, monkeypatch):
        mock_pool = MockPool()
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    device_id = str(uuid.uuid4())
                    response = await client.delete(
                        f"/v1/auth/devices/{device_id}",
                        headers=make_auth_headers("invalid-token"),
                    )
                    assert response.status_code == 401
        finally:
            restore_init(original)


class TestRevokeDeviceCrossUser:
    @pytest.mark.asyncio
    async def test_revoke_other_users_device_returns_404(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        my_device_id = uuid.uuid4()
        other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        other_device_id = uuid.uuid4()

        devices = {
            str(my_device_id): _make_device(my_device_id, user_id, "My Phone", "native"),
            str(other_device_id): _make_device(other_device_id, other_user_id, "Other User Device", "web"),
        }

        mock_pool = MockPool(devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-cross-user"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = my_device_id
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=my_device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.delete(
                        f"/v1/auth/devices/{other_device_id}",
                        headers=make_auth_headers(access_token),
                    )
                    # Must return 404, not 403, to avoid leaking device existence
                    assert response.status_code == 404, f"Expected 404, got {response.status_code}"

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestRevokeDeviceMalformedId:
    @pytest.mark.asyncio
    async def test_revoke_malformed_uuid_returns_404(self, setup_env, monkeypatch):
        mock_pool = MockPool()
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
                    for malformed in ["not-a-uuid", "12345", "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"]:
                        response = await client.delete(
                            f"/v1/auth/devices/{malformed}",
                            headers=make_auth_headers(access_token),
                        )
                        assert response.status_code == 404, f"Expected 404 for {malformed}, got {response.status_code}"

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestRevokeDeviceOwnDevice:
    @pytest.mark.asyncio
    async def test_revoke_own_device_revokes_and_invalidates_sessions(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        my_device_id = uuid.uuid4()
        other_device_id = uuid.uuid4()
        session_id = uuid.uuid4()

        devices = {
            str(my_device_id): _make_device(my_device_id, user_id, "My Phone", "native"),
            str(other_device_id): _make_device(other_device_id, user_id, "My Laptop", "web"),
        }
        sessions = {
            f"hash-{my_device_id}": {
                "id": session_id,
                "user_id": user_id,
                "device_id": my_device_id,
                "client_kind": "native",
                "revoked_at": None,
            },
            f"hash-{other_device_id}": {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "device_id": other_device_id,
                "client_kind": "web",
                "revoked_at": None,
            },
        }

        mock_pool = MockPool(devices=devices, sessions=sessions)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-revoke-own"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = my_device_id
                state.db_pool._session_id = session_id

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=my_device_id,
                            session_id=session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.delete(
                        f"/v1/auth/devices/{my_device_id}",
                        headers=make_auth_headers(access_token),
                    )

                    assert response.status_code == 204, f"Expected 204, got {response.status_code}"

                    # Device should be revoked
                    assert mock_pool._devices[str(my_device_id)]["revoked_at"] is not None

                    # Other device should NOT be revoked
                    assert mock_pool._devices[str(other_device_id)]["revoked_at"] is None

                    # Sessions for my device should be revoked
                    my_session = mock_pool._sessions.get(f"hash-{my_device_id}")
                    assert my_session is not None
                    assert my_session["revoked_at"] is not None

                    # Sessions for other device should NOT be revoked
                    other_session = mock_pool._sessions.get(f"hash-{other_device_id}")
                    assert other_session is not None
                    assert other_session["revoked_at"] is None

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestRevokeDeviceIdempotent:
    @pytest.mark.asyncio
    async def test_revoke_already_revoked_device_returns_204(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        my_device_id = uuid.uuid4()

        devices = {
            str(my_device_id): _make_device(
                my_device_id, user_id, "My Phone", "native",
                revoked_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        }

        mock_pool = MockPool(devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-idempotent"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = my_device_id
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=my_device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.delete(
                        f"/v1/auth/devices/{my_device_id}",
                        headers=make_auth_headers(access_token),
                    )

                    # Idempotent: already revoked returns 204
                    assert response.status_code == 204, f"Expected 204, got {response.status_code}"

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)


class TestRevokeDeviceCurrentDeviceClearsCookie:
    @pytest.mark.asyncio
    async def test_revoke_current_web_device_with_cookie_clears_cookie(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        my_device_id = uuid.uuid4()

        devices = {
            str(my_device_id): _make_device(my_device_id, user_id, "My Phone", "web"),
        }

        mock_pool = MockPool(devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-clear-cookie"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = my_device_id
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=my_device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.delete(
                        f"/v1/auth/devices/{my_device_id}",
                        headers=make_auth_headers(access_token),
                        cookies={"__Host-daemon_refresh": "some-refresh-token"},
                    )

                    assert response.status_code == 204, f"Expected 204, got {response.status_code}"

                    cookie_header = response.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" in cookie_header
                    assert "Max-Age=0" in cookie_header

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)

    @pytest.mark.asyncio
    async def test_revoke_current_native_device_without_cookie_does_not_clear_cookie(self, setup_env, monkeypatch):
        user_id = SINGLETON_ID
        my_device_id = uuid.uuid4()

        devices = {
            str(my_device_id): _make_device(my_device_id, user_id, "My Phone", "native"),
        }

        mock_pool = MockPool(devices=devices)
        original = make_mock_init(mock_pool)
        try:
            async with app.router.lifespan_context(app):
                state = app.state.app_state
                access_token = "test-access-token-native-no-cookie"
                state.db_pool._access_token = access_token
                state.db_pool._access_token_hash = hash_token(access_token)
                state.db_pool._user_id = user_id
                state.db_pool._device_id = my_device_id
                state.db_pool._session_id = uuid.uuid4()

                async def mock_verify(pool, token):
                    if token == access_token:
                        from orchestrator.auth import AuthenticatedDevice
                        return AuthenticatedDevice(
                            user_id=user_id,
                            device_id=my_device_id,
                            session_id=pool._session_id,
                        )
                    return None

                import orchestrator.auth as auth_module
                original_verify = auth_module._verify_access_token
                auth_module._verify_access_token = lambda pool, token: mock_verify(pool, token)

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.delete(
                        f"/v1/auth/devices/{my_device_id}",
                        headers=make_auth_headers(access_token),
                    )

                    assert response.status_code == 204, f"Expected 204, got {response.status_code}"

                    set_cookie = response.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" not in set_cookie

                auth_module._verify_access_token = original_verify
        finally:
            restore_init(original)
