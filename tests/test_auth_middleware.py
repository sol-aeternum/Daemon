from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from orchestrator.auth import (
    _extract_bearer_token,
    _verify_access_token,
    require_device_auth,
)


class TestExtractBearerToken:
    def test_none_returns_none(self):
        assert _extract_bearer_token(None) is None

    def test_empty_string_returns_none(self):
        assert _extract_bearer_token("") is None

    def test_no_bearer_prefix_returns_none(self):
        assert _extract_bearer_token("Basic abc123") is None

    def test_bearer_only_returns_none(self):
        assert _extract_bearer_token("Bearer ") is None

    def test_bearer_with_token_returns_token(self):
        assert _extract_bearer_token("Bearer abc123xyz") == "abc123xyz"

    def test_bearer_with_whitespace_token_returns_stripped(self):
        assert _extract_bearer_token("Bearer   abc123xyz   ") == "abc123xyz"


class TestVerifyAccessToken:

    def _make_pool_mock(self, fetchrow_result):
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=fetchrow_result)
        conn.fetchval = AsyncMock(return_value=datetime.now(timezone.utc))
        conn.execute = AsyncMock()
        @asynccontextmanager
        async def mock_acquire():
            yield conn
        pool.acquire = MagicMock(side_effect=mock_acquire)
        return pool

    @pytest.mark.asyncio
    async def test_valid_token_returns_authenticated_device(self):
        user_id = uuid.uuid4()
        device_id = uuid.uuid4()
        session_id = uuid.uuid4()
        pool = self._make_pool_mock({
            "user_id": user_id,
            "device_id": device_id,
            "session_id": session_id,
            "session_revoked_at": None,
            "access_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "device_revoked_at": None,
        })
        token = "valid-access-token-abc123"
        result = await _verify_access_token(pool, token)
        assert result is not None
        assert result.user_id == user_id
        assert result.device_id == device_id
        assert result.session_id == session_id

    @pytest.mark.asyncio
    async def test_unknown_token_hash_returns_none(self):
        pool = self._make_pool_mock(None)
        result = await _verify_access_token(pool, "unknown-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoked_session_returns_none(self):
        pool = self._make_pool_mock({
            "user_id": uuid.uuid4(),
            "device_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
            "session_revoked_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "access_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "device_revoked_at": None,
        })
        result = await _verify_access_token(pool, "some-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_access_token_returns_none(self):
        pool = self._make_pool_mock({
            "user_id": uuid.uuid4(),
            "device_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
            "session_revoked_at": None,
            "access_expires_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "device_revoked_at": None,
        })
        result = await _verify_access_token(pool, "some-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoked_device_returns_none(self):
        pool = self._make_pool_mock({
            "user_id": uuid.uuid4(),
            "device_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
            "session_revoked_at": None,
            "access_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "device_revoked_at": datetime.now(timezone.utc) - timedelta(hours=1),
        })
        result = await _verify_access_token(pool, "some-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_updates_last_seen_at(self):
        executed_sql = []
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={
            "user_id": uuid.uuid4(),
            "device_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
            "session_revoked_at": None,
            "access_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "device_revoked_at": None,
        })
        conn.fetchval = AsyncMock(return_value=datetime.now(timezone.utc))
        async def mock_execute(sql, *args):
            executed_sql.append(sql)
        conn.execute = mock_execute
        @asynccontextmanager
        async def mock_acquire():
            yield conn
        pool.acquire = MagicMock(side_effect=mock_acquire)
        result = await _verify_access_token(pool, "some-token")
        assert result is not None
        assert len(executed_sql) == 1
        sql = executed_sql[0]
        assert "UPDATE devices" in sql
        assert "last_seen_at" in sql
        assert "NOW()" in sql
        assert "5 minutes" in sql

    @pytest.mark.asyncio
    async def test_hash_token_is_deterministic(self):
        token = "test-token-xyz"
        pool = self._make_pool_mock({
            "user_id": uuid.uuid4(),
            "device_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
            "session_revoked_at": None,
            "access_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "device_revoked_at": None,
        })
        result1 = await _verify_access_token(pool, token)
        result2 = await _verify_access_token(pool, token)
        assert result1 is not None
        assert result2 is not None
        assert result1.user_id == result2.user_id


class TestRequireDeviceAuthDependency:

    @pytest.mark.asyncio
    async def test_missing_auth_header_raises_401(self):
        request = MagicMock()
        request.headers = {}
        with pytest.raises(HTTPException) as exc_info:
            await require_device_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_bearer_raises_401(self):
        request = MagicMock()
        request.headers = {"Authorization": "Basic abc123"}
        with pytest.raises(HTTPException) as exc_info:
            await require_device_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_bearer_raises_401(self):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer "}
        with pytest.raises(HTTPException) as exc_info:
            await require_device_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_db_unavailable_raises_503(self):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer valid-token"}
        app_state = MagicMock()
        app_state.db_pool = None
        request.app.state.app_state = app_state
        with pytest.raises(HTTPException) as exc_info:
            await require_device_auth(request)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_unknown_token_raises_401(self):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer unknown-token"}
        app_state = MagicMock()
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        conn.fetchval = AsyncMock(return_value=datetime.now(timezone.utc))
        conn.execute = AsyncMock()
        @asynccontextmanager
        async def mock_acquire():
            yield conn
        pool.acquire = MagicMock(side_effect=mock_acquire)
        app_state.db_pool = pool
        request.app.state.app_state = app_state
        with pytest.raises(HTTPException) as exc_info:
            await require_device_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_sets_request_state(self):
        user_id = uuid.uuid4()
        device_id = uuid.uuid4()
        session_id = uuid.uuid4()
        request = MagicMock()
        request.headers = {"Authorization": "Bearer valid-token"}
        app_state = MagicMock()
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={
            "user_id": user_id,
            "device_id": device_id,
            "session_id": session_id,
            "session_revoked_at": None,
            "access_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "device_revoked_at": None,
        })
        conn.fetchval = AsyncMock(return_value=datetime.now(timezone.utc))
        conn.execute = AsyncMock()
        @asynccontextmanager
        async def mock_acquire():
            yield conn
        pool.acquire = MagicMock(side_effect=mock_acquire)
        app_state.db_pool = pool
        request.app.state.app_state = app_state
        result = await require_device_auth(request)
        assert result.user_id == user_id
        assert result.device_id == device_id
        assert result.session_id == session_id
        assert request.state.user_id == user_id
        assert request.state.device_id == device_id
        assert request.state.session_id == session_id
