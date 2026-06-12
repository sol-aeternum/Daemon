from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.auth_tokens import generate_setup_token, hash_enrollment_code, hash_token
from orchestrator.config import get_settings
from orchestrator.main import app
from orchestrator.setup_token_delivery import write_setup_token_file


SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class MockConn:
    def __init__(self, pool):
        self._pool = pool
        self._in_transaction = False

    async def fetchval(self, sql, *args):
        if "FROM system_state" in sql:
            return self._pool._system_state.get(args[0])
        if "COUNT(*)" in sql and "devices" in sql:
            return self._pool._active_count
        if "SELECT NOW()" in sql:
            return datetime.now(timezone.utc)
        if "SELECT id FROM users" in sql:
            if str(SINGLETON_ID) in str(args):
                return SINGLETON_ID if self._pool._singleton_exists else None
            return None
        if "INSERT INTO users" in sql:
            self._pool._singleton_exists = True
            return SINGLETON_ID
        if "INSERT INTO tenants" in sql:
            user_id = args[0]
            name = args[1]
            existing = self._pool._tenants.get(user_id)
            if existing is not None:
                return None
            tenant_id = uuid.uuid4()
            self._pool._tenants[user_id] = {
                "id": tenant_id,
                "owner_user_id": user_id,
                "kind": "personal",
                "name": name,
            }
            return tenant_id
        if "INSERT INTO tenant_memberships" in sql:
            tenant_id = args[0]
            user_id = args[1]
            key = (tenant_id, user_id)
            if key in self._pool._tenant_memberships:
                return None
            self._pool._tenant_memberships[key] = "owner"
            return "owner"
        if "SELECT role" in sql and "tenant_memberships" in sql:
            tenant_id = args[0]
            user_id = args[1]
            return self._pool._tenant_memberships.get((tenant_id, user_id))
        if "INSERT INTO devices" in sql:
            device_id = uuid.uuid4()
            self._pool._devices[str(device_id)] = {
                "id": device_id,
                "user_id": SINGLETON_ID,
                "tenant_id": args[1],
                "display_name": args[2],
                "platform": args[3],
                "created_at": datetime.now(timezone.utc),
                "last_seen_at": datetime.now(timezone.utc),
                "revoked_at": None,
            }
            self._pool._active_count = len(
                [d for d in self._pool._devices.values() if d["revoked_at"] is None]
            )
            return device_id
        if "INSERT INTO sessions" in sql:
            session_id = uuid.uuid4()
            if "device_persistence" in sql:
                device_persistence = args[3]
                tenant_id = args[4]
                access_hash = args[5]
                access_expires_at = args[6]
                refresh_hash = args[7]
            else:
                device_persistence = "private"
                tenant_id = args[3]
                access_hash = args[4]
                access_expires_at = args[5]
                refresh_hash = args[6]
            session = {
                "id": session_id,
                "user_id": args[0],
                "device_id": args[1],
                "client_kind": args[2],
                "tenant_id": tenant_id,
                "device_persistence": device_persistence,
                "refresh_token_hash": refresh_hash,
                "access_hash": access_hash,
                "access_expires_at": access_expires_at,
                "refresh_expires_at": datetime.now(timezone.utc) + timedelta(days=90),
                "refresh_consumed_at": None,
                "revoked_at": None,
            }
            self._pool._sessions[access_hash] = session
            self._pool._sessions[refresh_hash] = session
            return session_id
        if "INSERT INTO pending_enrollments" in sql:
            return None

        def _get_pending(pending_id):
            key_str = str(pending_id)
            key_uuid = (
                uuid.UUID(key_str) if key_str not in self._pool._pending_enrollments else pending_id
            )
            return self._pool._pending_enrollments.get(
                key_str
            ) or self._pool._pending_enrollments.get(key_uuid)

        if "FROM pending_enrollments" in sql and "WHERE id =" in sql:
            pending_id = args[0]
            result = _get_pending(pending_id)
            if result is not None and "created_by_device_id" not in result:
                active_devices = [
                    d for d in self._pool._devices.values() if d.get("revoked_at") is None
                ]
                result["created_by_device_id"] = active_devices[0]["id"] if active_devices else None
            return result
        if "UPDATE pending_enrollments" in sql and "wrong_attempts_remaining" in sql:
            if "wrong_attempts_remaining = 0" in sql:
                pending_id = args[0]
                new_val = 0
            else:
                new_val = args[0]
                pending_id = args[1]
            entry = _get_pending(pending_id)
            if entry is not None:
                entry["wrong_attempts_remaining"] = new_val
        if "UPDATE devices" in sql and "revoked_at" in sql:
            device_id = args[0]
            key = device_id if device_id in self._pool._devices else str(device_id)
            if key in self._pool._devices:
                self._pool._devices[key]["revoked_at"] = datetime.now(timezone.utc)
                self._pool._active_count = len(
                    [d for d in self._pool._devices.values() if d["revoked_at"] is None]
                )
        if "UPDATE sessions" in sql and "revoked_at" in sql:
            device_id = args[0]
            for sess in self._pool._sessions.values():
                if sess.get("device_id") == device_id:
                    sess["revoked_at"] = datetime.now(timezone.utc)
        if "UPDATE sessions" in sql and "RETURNING" in sql:
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if row["refresh_consumed_at"] is None and row["revoked_at"] is None:
                    device_key = row["device_id"]
                    device = self._pool._devices.get(str(device_key)) or self._pool._devices.get(
                        device_key
                    )
                    if device and device.get("revoked_at") is not None:
                        return None
                    if row["refresh_expires_at"] > datetime.now(timezone.utc):
                        row["refresh_consumed_at"] = datetime.now(timezone.utc)
                        return row
            return None
        if (
            "SELECT" in sql
            and "refresh_consumed_at" in sql
            and "UPDATE" not in sql
            and "refresh_token_hash" in sql
        ):
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if row.get("refresh_consumed_at") is not None:
                    return row
            return None
        if (
            "SELECT" in sql
            and "refresh_token_hash" in sql
            and "id, user_id, device_id, client_kind" in sql
        ):
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if row.get("refresh_consumed_at") is None:
                    return row
            return None
        return None

    async def fetchrow(self, sql, *args):
        if "INSERT INTO system_state" in sql and "ON CONFLICT" in sql:
            key, value = args[:2]
            if key in self._pool._system_state:
                return None
            self._pool._system_state[key] = value
            return {"value": value}
        if "access_token_hash" in sql and "FROM sessions" in sql:
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                device = self._pool._devices.get(str(row["device_id"])) or self._pool._devices.get(
                    row["device_id"]
                )
                if device is None:
                    return None
                if device.get("revoked_at") is not None:
                    return None
                if row.get("revoked_at") is not None:
                    return None
                now = datetime.now(timezone.utc)
                if row.get("access_expires_at") and row["access_expires_at"] <= now:
                    return None
                return {
                    "user_id": row["user_id"],
                    "device_id": row["device_id"],
                    "session_id": row["id"],
                    "session_revoked_at": row.get("revoked_at"),
                    "access_expires_at": row.get("access_expires_at"),
                    "device_revoked_at": device.get("revoked_at"),
                }
            return None
        if "FROM pending_enrollments" in sql and "WHERE id =" in sql:
            pending_id = args[0]
            key_str = str(pending_id)
            entry = self._pool._pending_enrollments.get(
                key_str
            ) or self._pool._pending_enrollments.get(pending_id)
            if entry is not None and "created_by_device_id" not in entry:
                active_devices = [
                    d for d in self._pool._devices.values() if d.get("revoked_at") is None
                ]
                entry["created_by_device_id"] = active_devices[0]["id"] if active_devices else None
            return entry
        if "SELECT revoked_at FROM devices WHERE id = $1" in sql:
            device_id = args[0]
            device = self._pool._devices.get(str(device_id)) or self._pool._devices.get(device_id)
            return {"revoked_at": device.get("revoked_at")} if device else None
        if "SELECT id FROM users" in sql:
            return {"id": SINGLETON_ID}
        if "FROM tenants" in sql and "owner_user_id" in sql:
            user_id = args[0]
            return self._pool._tenants.get(user_id)
        if (
            "SELECT id, display_name, platform, created_at, last_seen_at, revoked_at" in sql
            and "user_id" in sql
        ):
            user_id = args[0]
            result = []
            for d in self._pool._devices.values():
                if d.get("user_id") == user_id:
                    if "revoked_at IS NULL" in sql and d.get("revoked_at") is not None:
                        continue
                    result.append(d)
            if not result:
                return None
            return result[0]
        if "SELECT id, user_id, revoked_at" in sql and "FOR UPDATE" in sql:
            target_id = args[0]
            user_id = args[1]
            for d in self._pool._devices.values():
                if d["id"] == target_id and d.get("user_id") == user_id:
                    return d
            return None
        if "UPDATE sessions" in sql and "RETURNING" in sql:
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if row["refresh_consumed_at"] is None and row["revoked_at"] is None:
                    device_key = row["device_id"]
                    device = self._pool._devices.get(str(device_key)) or self._pool._devices.get(
                        device_key
                    )
                    if device and device.get("revoked_at") is not None:
                        return None
                    if row["refresh_expires_at"] > datetime.now(timezone.utc):
                        row["refresh_consumed_at"] = datetime.now(timezone.utc)
                        return row
            return None
        if (
            "SELECT" in sql
            and "refresh_consumed_at" in sql
            and "UPDATE" not in sql
            and "refresh_token_hash" in sql
        ):
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if row.get("refresh_consumed_at") is not None:
                    return row
            return None
        if (
            "SELECT" in sql
            and "refresh_token_hash" in sql
            and "id, user_id, device_id, client_kind" in sql
        ):
            token_hash = args[0]
            if token_hash in self._pool._sessions:
                row = self._pool._sessions[token_hash]
                if row.get("refresh_consumed_at") is None:
                    return row
            return None
        return None

    async def fetch(self, sql, *args):
        if (
            "SELECT id, display_name, platform, created_at, last_seen_at, revoked_at" in sql
            and "user_id" in sql
        ):
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
        if "INSERT INTO system_state" in sql:
            key, value = args[:2]
            self._pool._system_state[key] = value
            return None
        if "DELETE FROM system_state" in sql:
            self._pool._system_state.pop(args[0], None)
            return None
        if "UPDATE pending_enrollments" in sql and "wrong_attempts_remaining" in sql:
            if "wrong_attempts_remaining = 0" in sql:
                pending_id = args[0]
                new_val = 0
            else:
                new_val = args[0]
                pending_id = args[1]
            key = pending_id if pending_id in self._pool._pending_enrollments else str(pending_id)
            if key in self._pool._pending_enrollments:
                self._pool._pending_enrollments[key]["wrong_attempts_remaining"] = new_val
        if "UPDATE devices" in sql and "revoked_at" in sql:
            device_id = args[0]
            key = device_id if device_id in self._pool._devices else str(device_id)
            if key in self._pool._devices:
                self._pool._devices[key]["revoked_at"] = datetime.now(timezone.utc)
                self._pool._active_count = len(
                    [d for d in self._pool._devices.values() if d["revoked_at"] is None]
                )
        if "UPDATE sessions" in sql and "revoked_at" in sql:
            device_id = args[0]
            for sess in self._pool._sessions.values():
                if sess.get("device_id") == device_id:
                    sess["revoked_at"] = datetime.now(timezone.utc)
        if "INSERT INTO sessions" in sql:
            session_id = uuid.uuid4()
            if "device_persistence" in sql:
                device_persistence = args[3]
                tenant_id = args[4]
                access_hash = args[5]
                access_expires_at = args[6]
                refresh_hash = args[7]
            else:
                device_persistence = "private"
                tenant_id = args[3]
                access_hash = args[4]
                access_expires_at = args[5]
                refresh_hash = args[6]
            session = {
                "id": session_id,
                "user_id": args[0],
                "device_id": args[1],
                "client_kind": args[2],
                "tenant_id": tenant_id,
                "device_persistence": device_persistence,
                "refresh_token_hash": refresh_hash,
                "access_hash": access_hash,
                "access_expires_at": access_expires_at,
                "refresh_expires_at": datetime.now(timezone.utc) + timedelta(days=90),
                "refresh_consumed_at": None,
                "revoked_at": None,
            }
            self._pool._sessions[access_hash] = session
            self._pool._sessions[refresh_hash] = session
        return None

    @asynccontextmanager
    async def transaction(self):
        self._in_transaction = True
        try:
            yield self
        finally:
            self._in_transaction = False


class MockPool:
    def __init__(self, active_device_count=0):
        self._devices: dict[str, Any] = {}
        self._sessions: dict[str, Any] = {}
        self._pending_enrollments: dict[str, Any] = {}
        self._tenants: dict[uuid.UUID, dict[str, Any]] = {}
        self._tenant_memberships: dict[tuple[uuid.UUID, uuid.UUID], str] = {}
        self._active_count = active_device_count
        self._singleton_exists = False
        self._closed = False
        self._connections = []
        self._system_state: dict[str, str] = {}

    async def fetchval(self, sql, *args):
        if "FROM system_state" in sql:
            return self._system_state.get(args[0])
        if "COUNT(*)" in sql and "devices" in sql:
            return self._active_count
        return None

    async def fetchrow(self, sql, *args):
        if "INSERT INTO system_state" in sql and "ON CONFLICT" in sql:
            key, value = args[:2]
            if key in self._system_state:
                return None
            self._system_state[key] = value
            return {"value": value}
        return None

    async def execute(self, sql, *args):
        if "INSERT INTO system_state" in sql:
            key, value = args[:2]
            self._system_state[key] = value
            return None
        if "DELETE FROM system_state" in sql:
            self._system_state.pop(args[0], None)
            return None
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
    from orchestrator.db import AppState

    original_init = main_module.init_app_state
    original_check = main_module._check_first_boot_setup

    async def mock_init(settings):
        state = AppState(settings=settings)
        state.db_pool = mock_pool
        state.redis = None
        state.memory_store = None
        state.video_credits_dal = None
        state.setup_token_hash = None
        object.__setattr__(state, "_startup_setup_token", None)
        return state

    async def mock_check(state):
        if state.db_pool is None:
            return
        try:
            active_count = await state.db_pool.fetchval(
                "SELECT COUNT(*) FROM devices WHERE revoked_at IS NULL"
            )
            if active_count == 0:
                plaintext = generate_setup_token()
                state.db_pool._system_state["auth.setup_token_hash"] = hash_token(plaintext)
                object.__setattr__(state, "_startup_setup_token", plaintext)
                token_path = write_setup_token_file(
                    get_settings().daemon_setup_token_file,
                    plaintext,
                )
                logger.info(
                    ">>> Daemon setup required. Open http://<host>:<port>/setup "
                    "and enter the setup token from %s",
                    token_path,
                )
        except Exception:
            logger.warning("First-boot setup check failed", exc_info=True)

    main_module.init_app_state = mock_init
    main_module._check_first_boot_setup = mock_check
    return original_init, original_check


def restore_init(original_init, original_check):
    import orchestrator.main as main_module

    main_module.init_app_state = original_init
    main_module._check_first_boot_setup = original_check


logger = logging.getLogger(__name__)


@pytest_asyncio.fixture
async def setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ALLOWED_ORIGINS", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_PUBLIC_ORIGIN", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DAEMON_SETUP_TOKEN_FILE", str(tmp_path / "setup-token"))
    monkeypatch.setenv(
        "DAEMON_AUTH_PEPPER",
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestAuthDeviceLifecycleSmoke:
    @pytest.mark.asyncio
    async def test_full_lifecycle_smoke(self, setup_env, monkeypatch, caplog):
        pool = MockPool(active_device_count=0)
        original_init, original_check = make_mock_init(pool)
        try:
            caplog.set_level(logging.INFO)

            async with app.router.lifespan_context(app):
                state = app.state.app_state
                assert ">>> Daemon setup required" in caplog.text
                setup_plaintext = getattr(state, "_startup_setup_token")
                assert setup_plaintext is not None
                assert setup_plaintext not in caplog.text

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": "wrong-token"},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )
                    assert resp.status_code == 401

                    resp = await client.post(
                        "/v1/auth/setup",
                        json={"setup_token": setup_plaintext},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )
                    assert resp.status_code == 200, resp.text
                    data = resp.json()
                    assert "access_token" in data
                    assert data["token_type"] == "Bearer"
                    cookie_header = resp.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" in cookie_header
                    assert "HttpOnly" in cookie_header
                    assert len(pool._devices) == 1
                    device_a_id = list(pool._devices.keys())[0]
                    seen_ids = set()
                    device_a_sessions = []
                    for s in pool._sessions.values():
                        sid = s["id"]
                        if (
                            sid not in seen_ids
                            and s["device_id"] == pool._devices[device_a_id]["id"]
                        ):
                            seen_ids.add(sid)
                            device_a_sessions.append(s)
                    assert len(device_a_sessions) == 1
                    session_a = device_a_sessions[0]
                    access_token_a = data["access_token"]

                    match = re.search(r"__Host-daemon_refresh=([^;]+)", cookie_header)
                    assert match
                    raw_refresh_a = match.group(1)

                    resp = await client.get(
                        "/v1/auth/devices",
                        headers=make_auth_headers(access_token_a),
                    )
                    assert resp.status_code == 200

                    resp = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": raw_refresh_a},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )
                    assert resp.status_code == 200, resp.text
                    data = resp.json()
                    assert "access_token" in data
                    new_cookie_header = resp.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" in new_cookie_header
                    assert session_a["refresh_consumed_at"] is not None

                    match2 = re.search(r"__Host-daemon_refresh=([^;]+)", new_cookie_header)
                    assert match2

                    resp = await client.post(
                        "/v1/auth/enroll/start",
                        headers=make_auth_headers(data["access_token"]),
                    )
                    assert resp.status_code == 200, resp.text
                    enroll_data = resp.json()
                    assert "pending_id" in enroll_data
                    assert "code" in enroll_data
                    assert "expires_at" in enroll_data
                    pending_id = enroll_data["pending_id"]
                    enroll_code = enroll_data["code"]

                    settings = get_settings()
                    pepper = validate_and_get_pepper(settings)
                    code_verifier_hash = hash_enrollment_code(enroll_code, pepper)
                    pool._pending_enrollments[pending_id] = {
                        "id": uuid.UUID(pending_id),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                        "consumed_at": None,
                    }

                    resp = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": "0000-0000",
                            "client_kind": "native",
                        },
                    )
                    assert resp.status_code == 401

                    resp = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": "1111-1111",
                            "client_kind": "native",
                        },
                    )
                    assert resp.status_code == 401

                    resp = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id,
                            "code": "2222-2222",
                            "client_kind": "native",
                        },
                    )
                    assert resp.status_code == 410

                    pending_id_new = str(uuid.uuid4())
                    new_enroll_code = "1234-5678"
                    code_verifier_hash_new = hash_enrollment_code(new_enroll_code, pepper)
                    pool._pending_enrollments[pending_id_new] = {
                        "id": uuid.UUID(pending_id_new),
                        "user_id": SINGLETON_ID,
                        "code_verifier_hash": code_verifier_hash_new,
                        "wrong_attempts_remaining": 3,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                        "consumed_at": None,
                    }

                    resp = await client.post(
                        "/v1/auth/enroll/complete",
                        json={
                            "pending_id": pending_id_new,
                            "code": new_enroll_code,
                            "client_kind": "web",
                        },
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )
                    assert resp.status_code == 200, resp.text
                    enroll_complete = resp.json()
                    assert "access_token" in enroll_complete
                    assert enroll_complete["token_type"] == "Bearer"
                    enroll_cookie_header = resp.headers.get("set-cookie", "")
                    assert "__Host-daemon_refresh" in enroll_cookie_header

                    assert len(pool._devices) == 2
                    device_b_id = [k for k in pool._devices if k != device_a_id][0]

                    seen_ids_b = set()
                    device_b_sessions = []
                    for s in pool._sessions.values():
                        sid = s["id"]
                        if (
                            sid not in seen_ids_b
                            and s["device_id"] == pool._devices[device_b_id]["id"]
                        ):
                            seen_ids_b.add(sid)
                            device_b_sessions.append(s)
                    assert len(device_b_sessions) == 1
                    session_b = device_b_sessions[0]

                    match_b = re.search(r"__Host-daemon_refresh=([^;]+)", enroll_cookie_header)
                    assert match_b
                    raw_refresh_b = match_b.group(1)

                    resp = await client.get(
                        "/v1/auth/devices",
                        headers=make_auth_headers(enroll_complete["access_token"]),
                    )
                    assert resp.status_code == 200
                    device_list = resp.json()
                    assert len(device_list["devices"]) == 2

                    resp = await client.delete(
                        f"/v1/auth/devices/{pool._devices[device_a_id]['id']}",
                        headers=make_auth_headers(enroll_complete["access_token"]),
                        cookies={"__Host-daemon_refresh": raw_refresh_b},
                    )
                    assert resp.status_code == 204
                    assert pool._devices[device_a_id]["revoked_at"] is not None

                    resp = await client.get(
                        "/v1/auth/devices",
                        headers=make_auth_headers(access_token_a),
                    )
                    assert resp.status_code == 401

                    resp = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": raw_refresh_b},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )
                    assert resp.status_code == 200, resp.text
                    assert session_b["refresh_consumed_at"] is not None

                    assert pool._devices[device_b_id]["revoked_at"] is None
                    resp = await client.post(
                        "/v1/auth/refresh",
                        cookies={"__Host-daemon_refresh": raw_refresh_b},
                        headers={
                            "Origin": "https://app.daemon.ai",
                            "Sec-Fetch-Site": "same-origin",
                        },
                    )
                    assert resp.status_code == 401
                    assert pool._devices[device_b_id]["revoked_at"] is not None

            caplog.clear()

            pool2 = MockPool(active_device_count=0)
            original_init2, original_check2 = make_mock_init(pool2)
            try:
                async with app.router.lifespan_context(app):
                    state2 = app.state.app_state
                    assert ">>> Daemon setup required" in caplog.text
                    setup_plaintext2 = getattr(state2, "_startup_setup_token")
                    assert setup_plaintext2 is not None
                    assert setup_plaintext2 not in caplog.text
            finally:
                restore_init(original_init2, original_check2)

        finally:
            restore_init(original_init, original_check)
