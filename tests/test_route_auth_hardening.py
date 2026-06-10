"""Tests for route auth hardening: all protected routes require device access-token auth."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from orchestrator.auth import require_admin_or_device_auth, require_device_auth
from orchestrator.config import get_settings
from orchestrator.db import AppState, get_app_state
import orchestrator.main as main_module
from orchestrator.main import app


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()
    monkeypatch.setattr(main_module, "fetch_openrouter_models", AsyncMock(return_value=[]))

    settings = get_settings()
    original_app_state = getattr(app.state, "app_state", None)
    original_settings = getattr(app.state, "settings", None)
    app.state.app_state = AppState(settings=settings)
    app.state.settings = settings

    async def reject_device_auth():
        raise HTTPException(status_code=401, detail="test unauthenticated")

    async def reject_admin_or_device_auth():
        raise HTTPException(status_code=401, detail="test unauthenticated")

    async def override_app_state():
        return app.state.app_state

    async def override_settings():
        return get_settings()

    app.dependency_overrides[get_app_state] = override_app_state
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[require_device_auth] = reject_device_auth
    app.dependency_overrides[require_admin_or_device_auth] = reject_admin_or_device_auth
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.state.app_state = original_app_state
        app.state.settings = original_settings


@pytest_asyncio.fixture
async def authenticated_client(monkeypatch):
    """Client with a fake DB pool that returns valid session for bearer auth."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()

    user_id = uuid.uuid4()
    device_id = uuid.uuid4()
    session_id = uuid.uuid4()

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "user_id": user_id,
            "device_id": device_id,
            "session_id": session_id,
            "session_revoked_at": None,
            "access_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "device_revoked_at": None,
        }
    )
    conn.fetchval = AsyncMock(return_value=datetime.now(timezone.utc))
    conn.execute = AsyncMock()

    @asynccontextmanager
    async def mock_acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=lambda: mock_acquire())

    app_state = MagicMock()
    app_state.db_pool = pool
    app_state.redis = None
    app_state.memory_store = None
    app_state.video_credits_dal = None

    original_app_state = getattr(app.state, "app_state", None)
    original_settings = getattr(app.state, "settings", None)
    app.state.settings = get_settings()

    async def override_app_state():
        return app.state.app_state

    async def override_settings():
        return get_settings()

    app.dependency_overrides[get_app_state] = override_app_state
    app.dependency_overrides[get_settings] = override_settings
    try:
        app.state.app_state = app_state
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, "valid-token"
    finally:
        app.dependency_overrides.clear()
        app.state.app_state = original_app_state
        app.state.settings = original_settings


class TestConversationsRoutesAreProtected:
    @pytest.mark.asyncio
    async def test_list_returns_401(self, client):
        response = await client.get("/conversations")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_returns_401(self, client):
        response = await client.post("/conversations", json={})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_returns_401(self, client):
        response = await client.get(f"/conversations/{uuid.uuid4()}")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_patch_returns_401(self, client):
        response = await client.patch(f"/conversations/{uuid.uuid4()}", json={"title": "test"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_returns_401(self, client):
        response = await client.delete(f"/conversations/{uuid.uuid4()}")
        assert response.status_code == 401


class TestMemoriesRoutesAreProtected:
    @pytest.mark.asyncio
    async def test_list_returns_401(self, client):
        response = await client.get("/memories")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_export_returns_401(self, client):
        response = await client.post("/memories/export", json={"status": "active"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_import_returns_401(self, client):
        response = await client.post("/memories/import", json={"memories": []})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_reembed_returns_401(self, client):
        response = await client.post("/memories/reembed", json={"status": "active"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_all_returns_401(self, client):
        response = await client.delete("/memories")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_single_returns_401(self, client):
        response = await client.get(f"/memories/{uuid.uuid4()}")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_returns_401(self, client):
        response = await client.post("/memories", json={"content": "test"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_returns_401(self, client):
        response = await client.patch(f"/memories/{uuid.uuid4()}", json={"content": "updated"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_single_returns_401(self, client):
        response = await client.delete(f"/memories/{uuid.uuid4()}")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_confirm_returns_401(self, client):
        response = await client.post(
            f"/memories/{uuid.uuid4()}/confirm", json={"status": "confirmed"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_consolidate_returns_401(self, client):
        response = await client.post("/memories/consolidate")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_dream_returns_401(self, client):
        response = await client.post("/memories/dream")
        assert response.status_code == 401


class TestSkillsRoutesAreProtected:
    @pytest.mark.asyncio
    async def test_list_returns_401(self, client):
        response = await client.get("/skills")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_returns_401(self, client):
        response = await client.get("/skills/skill-123")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_returns_401(self, client):
        response = await client.post("/skills", json={"name": "test", "description": ""})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_upload_returns_401(self, client):
        response = await client.post(
            "/skills/upload",
            data={"overwrite": "false"},
            files={"file": ("test.md", b"# Test", "text/markdown")},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_returns_401(self, client):
        response = await client.put("/skills/skill-123", json={"name": "updated"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_toggle_enabled_returns_401(self, client):
        response = await client.patch("/skills/skill-123/enabled", json={"enabled": True})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_returns_401(self, client):
        response = await client.delete("/skills/skill-123")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_autonomous_edit_returns_401(self, client):
        response = await client.patch(
            "/skills/skill-123/autonomous-edit", json={"allow_autonomous_edit": False}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_pending_update_returns_401(self, client):
        response = await client.post("/skills/skill-123/pending-update", json={"action": "dismiss"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_download_returns_401(self, client):
        response = await client.get("/skills/skill-123/download")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_admin_sync_returns_401(self, client):
        response = await client.post("/skills/admin/sync")
        assert response.status_code == 401


class TestUsersRoutesAreProtected:
    @pytest.mark.asyncio
    async def test_get_settings_returns_401(self, client):
        response = await client.get("/users/me/settings")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_settings_returns_401(self, client):
        response = await client.patch("/users/me/settings", json={"preferences": {}})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_presets_returns_401(self, client):
        response = await client.get("/users/me/settings/presets")
        assert response.status_code == 401


class TestVideoCreditsRoutesAreProtected:
    @pytest.mark.asyncio
    async def test_balance_returns_401(self, client):
        response = await client.get(
            "/video-credits/balance",
            params={"user_id": str(uuid.uuid4())},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_transactions_returns_401(self, client):
        response = await client.get(
            "/video-credits/transactions",
            params={"user_id": str(uuid.uuid4())},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_grant_returns_403_when_admin_disabled(self, client):
        response = await client.post(
            "/video-credits/grant",
            json={
                "user_id": str(uuid.uuid4()),
                "amount": 100,
                "description": "test",
            },
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_estimate_returns_401(self, client):
        response = await client.get(
            "/video-credits/estimate",
            params={
                "duration": 5,
                "tier": "pro",
                "user_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 401


class TestImageGenRoutesAreProtected:
    @pytest.mark.asyncio
    async def test_models_returns_401(self, client):
        response = await client.get("/api/images/models")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_image_returns_401(self, client):
        response = await client.get("/api/images/image-123")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_metadata_returns_401_when_unauthenticated(self, client):
        response = await client.get("/api/images/image-123/metadata")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_upload_reference_returns_401(self, client):
        response = await client.post(
            "/api/images/upload-reference",
            files={"file": ("test.png", b"fake", "image/png")},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_generate_returns_401(self, client):
        response = await client.post(
            "/api/images/generate",
            json={"models": ["flux"], "prompt": "test"},
        )
        assert response.status_code == 401


class TestRetiredImageGenRoutesReturnGoneWhenAuthenticated:
    @pytest.mark.asyncio
    async def test_models_returns_410(self, authenticated_client):
        client, token = authenticated_client
        response = await client.get(
            "/api/images/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 410
        assert "retired" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_generate_returns_410(self, authenticated_client):
        client, token = authenticated_client
        response = await client.post(
            "/api/images/generate",
            headers={"Authorization": f"Bearer {token}"},
            json={"models": ["flux"], "prompt": "test"},
        )
        assert response.status_code == 410
        assert "retired" in response.json()["detail"]


class TestGeneratedArtifactRoutesAreProtected:
    @pytest.mark.asyncio
    async def test_generated_images_returns_401(self, client):
        response = await client.get("/generated-images/test.png")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_generated_files_returns_401(self, client):
        response = await client.get("/generated-files/test.txt")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_generated_audio_returns_401(self, client):
        response = await client.get("/generated-audio/test.mp3")
        assert response.status_code == 401


class TestPublicEndpointsRemainPublic:
    @pytest.mark.asyncio
    async def test_health_is_public(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_v1_models_is_public(self, client):
        response = await client.get("/v1/models")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_models_is_public(self, client):
        response = await client.get("/models")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_api_models_is_public(self, client):
        response = await client.get("/api/models")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_v1_catalog_is_public(self, client):
        response = await client.get("/v1/catalog")
        assert response.status_code == 200


class TestSystemRoutesAreProtected:
    @pytest.mark.asyncio
    async def test_status_returns_401(self, client):
        response = await client.get("/status")
        assert response.status_code == 401


class TestProtectedRoutesReturn200WithValidBearer:
    """Verify protected routes return 200 when given a valid bearer access token.

    Uses a real require_device_auth dependency with a fake async DB pool
    that returns a valid unexpired non-revoked session row.
    """

    @pytest.mark.asyncio
    async def test_providers_returns_200_with_valid_token(self, authenticated_client):
        client, token = authenticated_client
        response = await client.get("/providers", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert "default" in data

    @pytest.mark.asyncio
    async def test_status_returns_200_with_valid_token(self, authenticated_client):
        client, token = authenticated_client
        response = await client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200


class TestCookieOnlyAuthRejected:
    """Verify that refresh cookies are NOT accepted as access auth on protected routes.

    Decision 19: Protected routes accept access tokens only — refresh cookies
    are valid only on refresh/setup/enrollment cookie-setting endpoints.
    """

    @pytest.mark.asyncio
    async def test_refresh_cookie_not_accepted_on_protected_route(self, client):
        response = await client.get(
            "/conversations",
            cookies={"__Host-daemon_refresh": "some-refresh-token-value"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_any_cookie_not_accepted_as_bearer(self, client):
        response = await client.get(
            "/providers",
            cookies={"some_cookie": "some_value"},
        )
        assert response.status_code == 401
