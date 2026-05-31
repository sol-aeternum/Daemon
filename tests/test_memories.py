"""Integration tests for memories API endpoints.

These tests verify that:
1. GET /memories returns memories array with various filters
2. GET /memories with category filter works
3. GET /memories with confirmed filter works
4. GET /memories with search query works
5. GET /memories/{id}/trail returns history
6. POST /memories/{id}/correct updates memory
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings
from orchestrator.main import app
from orchestrator.db import AppState
from orchestrator.auth import AuthenticatedDevice
from orchestrator.routes import memories as memories_router


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Create an async test client with mock DB."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def auth_client(monkeypatch):
    """Create an async test client with auth dependency overridden for device auth.

    This fixture provides a fake authenticated device for testing protected
    memory endpoints without requiring actual database auth verification.
    The dependency override is cleared after the test completes.
    """
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()

    fake_device = AuthenticatedDevice(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        device_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        session_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
    )

    app.dependency_overrides[memories_router.require_device_auth] = lambda: fake_device

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    app.dependency_overrides.clear()


def create_mock_app_state(mock_store: AsyncMock | None = None) -> AppState:
    """Create a mock AppState with optional memory store."""
    app_state = MagicMock(spec=AppState)
    app_state.memory_store = mock_store
    app_state.redis = None
    return app_state


def set_app_state(mock_app_state: AppState) -> None:
    """Set the app state on the FastAPI app."""
    app.state.app_state = mock_app_state


def create_mock_memory(memory_id: uuid.UUID | None = None, **overrides) -> dict[str, Any]:
    """Create a mock memory dict."""
    if memory_id is None:
        memory_id = uuid.uuid4()
    return {
        "id": memory_id,
        "user_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "content": "Test memory content",
        "category": "fact",
        "status": "active",
        "confirmed": True,
        "source_type": "extraction",
        "conversation_id": None,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "valid_to": None,
        **overrides,
    }


@pytest.mark.asyncio
async def test_get_memories_returns_memories_array(auth_client, monkeypatch) -> None:
    """Test that GET /memories returns a memories array."""
    mock_store = AsyncMock()
    mock_memories = [
        create_mock_memory(),
        create_mock_memory(),
        create_mock_memory(),
    ]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.get("/memories")

    assert response.status_code == 200
    data = response.json()
    assert "memories" in data
    assert "total" in data
    assert len(data["memories"]) == 3
    mock_store.list_memories.assert_called_once()


@pytest.mark.asyncio
async def test_get_memories_with_category_filter(auth_client, monkeypatch) -> None:
    """Test that GET /memories with category filter works."""
    mock_store = AsyncMock()
    mock_memories = [
        create_mock_memory(category="preference"),
        create_mock_memory(category="preference"),
    ]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.get("/memories?category=preference")

    assert response.status_code == 200
    data = response.json()
    assert len(data["memories"]) == 2

    call_args = mock_store.list_memories.call_args
    assert call_args.kwargs.get("category") == "preference"


@pytest.mark.asyncio
async def test_get_memories_with_confirmed_filter(auth_client, monkeypatch) -> None:
    """Test that GET /memories with confirmed filter works."""
    mock_store = AsyncMock()
    mock_memories = [create_mock_memory(confirmed=True)]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.get("/memories?confirmed=true")

    assert response.status_code == 200
    data = response.json()
    assert len(data["memories"]) == 1

    call_args = mock_store.list_memories.call_args
    assert call_args.kwargs.get("confirmed") is True


@pytest.mark.asyncio
async def test_get_memories_with_search_query(auth_client, monkeypatch) -> None:
    """Test that GET /memories with search query works."""
    mock_store = AsyncMock()
    mock_memories = [create_mock_memory(content="Python is awesome")]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.get("/memories?search=python")

    assert response.status_code == 200
    data = response.json()
    assert len(data["memories"]) == 1

    call_args = mock_store.list_memories.call_args
    assert call_args.kwargs.get("search") == "python"


@pytest.mark.asyncio
async def test_get_memories_with_limit_offset(auth_client, monkeypatch) -> None:
    """Test that GET /memories supports limit and offset pagination."""
    mock_store = AsyncMock()
    mock_memories = [create_mock_memory() for _ in range(5)]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.get("/memories?limit=10&offset=5")

    assert response.status_code == 200

    call_args = mock_store.list_memories.call_args
    assert call_args.kwargs.get("limit") == 10
    assert call_args.kwargs.get("offset") == 5


@pytest.mark.asyncio
async def test_get_memory_by_id(auth_client, monkeypatch) -> None:
    """Test that GET /memories/{id} returns single memory."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_memory = create_mock_memory(memory_id=memory_id)
    mock_store.get_memory = AsyncMock(return_value=mock_memory)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.get(f"/memories/{memory_id}")

    assert response.status_code == 200
    data = response.json()
    assert str(data["id"]) == str(memory_id)
    mock_store.get_memory.assert_called_once_with(memory_id)


@pytest.mark.asyncio
async def test_get_memory_by_id_not_found(auth_client, monkeypatch) -> None:
    """Test that GET /memories/{id} returns 404 for non-existent memory."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_store.get_memory = AsyncMock(return_value=None)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.get(f"/memories/{memory_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_memory(auth_client, monkeypatch) -> None:
    """Test that POST /memories creates a new memory."""
    mock_store = AsyncMock()
    new_memory_id = uuid.uuid4()

    async def mock_dedup_and_store(*args, **kwargs):
        return new_memory_id

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    with patch("orchestrator.memory.dedup.dedup_and_store", mock_dedup_and_store):
        response = await auth_client.post(
            "/memories",
            json={"content": "New test memory", "category": "fact"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"
    assert data["id"] == str(new_memory_id)


@pytest.mark.asyncio
async def test_update_memory(auth_client, monkeypatch) -> None:
    """Test that PATCH /memories/{id} updates memory content."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_memory = create_mock_memory(memory_id=memory_id)
    mock_store.get_memory = AsyncMock(return_value=mock_memory)
    mock_store.update_memory = AsyncMock(return_value=True)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.patch(
        f"/memories/{memory_id}",
        json={"content": "Updated content"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "updated"
    mock_store.update_memory.assert_called_once()


@pytest.mark.asyncio
async def test_delete_memory(auth_client, monkeypatch) -> None:
    """Test that DELETE /memories/{id} deletes memory."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_memory = create_mock_memory(memory_id=memory_id)
    mock_store.get_memory = AsyncMock(return_value=mock_memory)
    mock_store.delete_memory = AsyncMock(return_value=True)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.delete(f"/memories/{memory_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deleted"
    mock_store.delete_memory.assert_called_once()


@pytest.mark.asyncio
async def test_confirm_memory(auth_client, monkeypatch) -> None:
    """Test that POST /memories/{id}/confirm confirms or rejects memory."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_memory = create_mock_memory(memory_id=memory_id)
    mock_store.get_memory = AsyncMock(return_value=mock_memory)
    mock_store.confirm_memory = AsyncMock(return_value=True)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.post(
        f"/memories/{memory_id}/confirm",
        json={"status": "confirmed"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    mock_store.confirm_memory.assert_called_once_with(memory_id, confirmed=True)


@pytest.mark.asyncio
async def test_get_memory_trail_not_implemented(auth_client, monkeypatch) -> None:
    """Test that GET /memories/{id}/trail returns 404 (not implemented)."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.get(f"/memories/{memory_id}/trail")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_correct_memory_not_implemented(auth_client, monkeypatch) -> None:
    """Test that POST /memories/{id}/correct returns 404 (not implemented)."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.post(
        f"/memories/{memory_id}/correct",
        json={"content": "Corrected content"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_memories_unavailable_store(auth_client, monkeypatch) -> None:
    """Test that /memories returns 503 when store is unavailable."""
    mock_app_state = create_mock_app_state(None)
    set_app_state(mock_app_state)

    response = await auth_client.get("/memories")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_reembed_memories_returns_detailed_counts(auth_client, monkeypatch) -> None:
    mock_store = AsyncMock()
    memory_a = create_mock_memory(content="alpha")
    memory_b = create_mock_memory(content=" ")
    memory_c = create_mock_memory(content="gamma")
    mock_store.export_memories = AsyncMock(return_value=[memory_a, memory_b, memory_c])
    mock_store.update_memory_embedding = AsyncMock(return_value=True)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    vector_a = [0.1] * 1024
    vector_c = [0.2] * 1024
    with patch(
        "orchestrator.routes.memories.embed_documents",
        new_callable=AsyncMock,
        return_value=[vector_a, vector_c],
    ):
        response = await auth_client.post(
            "/memories/reembed",
            json={"status": "active", "batch_size": 2},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["requested"] == 3
    assert data["found"] == 3
    assert data["updated"] == 2
    assert data["skipped_empty"] == 1
    assert data["missing_ids"] == 0
    assert data["status"] == "active"
    assert mock_store.update_memory_embedding.await_count == 2


@pytest.mark.asyncio
async def test_reembed_memories_with_memory_ids_tracks_missing_ids(
    auth_client, monkeypatch
) -> None:
    mock_store = AsyncMock()
    existing_id = uuid.uuid4()
    missing_id = uuid.uuid4()
    existing_memory = create_mock_memory(memory_id=existing_id, content="one")
    mock_store.get_memory = AsyncMock(side_effect=[existing_memory, None])
    mock_store.update_memory_embedding = AsyncMock(return_value=True)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    vector = [0.3] * 1024
    with patch(
        "orchestrator.routes.memories.embed_documents",
        new_callable=AsyncMock,
        return_value=[vector],
    ):
        response = await auth_client.post(
            "/memories/reembed",
            json={"memory_ids": [str(existing_id), str(missing_id)]},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["requested"] == 2
    assert data["found"] == 1
    assert data["updated"] == 1
    assert data["missing_ids"] == 1


@pytest.mark.asyncio
async def test_reembed_memories_rejects_unknown_status(auth_client, monkeypatch) -> None:
    mock_store = AsyncMock()
    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.post(
        "/memories/reembed",
        json={"status": "unknown_status"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_memories_dream_enqueues_job(client, monkeypatch) -> None:
    monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "secret-admin")
    get_settings.cache_clear()

    mock_store = AsyncMock()
    mock_redis = AsyncMock()
    mock_redis.enqueue_job.return_value = SimpleNamespace(job_id="dream-job-123")

    mock_app_state = create_mock_app_state(mock_store)
    mock_app_state.redis = mock_redis
    set_app_state(mock_app_state)

    user_id = str(uuid.uuid4())
    response = await client.post(
        "/memories/dream",
        json={"user_id": user_id},
        headers={"Authorization": "Bearer secret-admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "enqueued"
    assert data["job_id"] == "dream-job-123"
    assert data["user_id"] == user_id
    assert mock_redis.enqueue_job.await_args.args[0] == "run_dreaming_job"


@pytest.mark.asyncio
async def test_post_memories_dream_requires_admin_token(client, monkeypatch) -> None:
    monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "secret-admin")
    get_settings.cache_clear()

    mock_app_state = create_mock_app_state(AsyncMock())
    mock_app_state.redis = AsyncMock()
    set_app_state(mock_app_state)

    response = await client.post("/memories/dream", json={})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_memories_dream_admin_no_user_id_enqueues_all(client, monkeypatch) -> None:
    monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "secret-admin")
    get_settings.cache_clear()

    mock_store = AsyncMock()
    mock_redis = AsyncMock()
    mock_redis.enqueue_job.return_value = SimpleNamespace(job_id="dream-job-all")

    mock_app_state = create_mock_app_state(mock_store)
    mock_app_state.redis = mock_redis
    set_app_state(mock_app_state)

    response = await client.post(
        "/memories/dream",
        json={},
        headers={"Authorization": "Bearer secret-admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "enqueued"
    assert data["job_id"] == "dream-job-all"
    assert data["user_id"] == "all"
    assert mock_redis.enqueue_job.await_args.args[0] == "run_dreaming_job"
    assert mock_redis.enqueue_job.await_args.args[1] is None


@pytest.mark.asyncio
async def test_post_memories_dream_device_no_user_id_enqueues_own(client, monkeypatch) -> None:
    monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "")
    get_settings.cache_clear()

    device_user_id = uuid.uuid4()

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "user_id": device_user_id,
            "device_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
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

    original_app_state = app.state.app_state

    mock_redis = AsyncMock()
    mock_redis.enqueue_job.return_value = SimpleNamespace(job_id="dream-job-own")

    try:
        app.state.app_state = MagicMock()
        app.state.app_state.db_pool = pool
        app.state.app_state.redis = mock_redis
        app.state.app_state.memory_store = None
        app.state.app_state.video_credits_dal = None

        response = await client.post(
            "/memories/dream",
            json={},
            headers={"Authorization": "Bearer valid-device-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "enqueued"
        assert data["job_id"] == "dream-job-own"
        assert data["user_id"] == str(device_user_id)
        assert mock_redis.enqueue_job.await_args.args[0] == "run_dreaming_job"
        assert mock_redis.enqueue_job.await_args.args[1] == str(device_user_id)
    finally:
        app.state.app_state = original_app_state


@pytest.mark.asyncio
async def test_post_memories_dream_device_different_user_forbidden(client, monkeypatch) -> None:
    monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "")
    get_settings.cache_clear()

    device_user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "user_id": device_user_id,
            "device_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
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

    original_app_state = app.state.app_state

    mock_redis = AsyncMock()

    try:
        app.state.app_state = MagicMock()
        app.state.app_state.db_pool = pool
        app.state.app_state.redis = mock_redis
        app.state.app_state.memory_store = None
        app.state.app_state.video_credits_dal = None

        response = await client.post(
            "/memories/dream",
            json={"user_id": str(other_user_id)},
            headers={"Authorization": "Bearer valid-device-token"},
        )

        assert response.status_code == 403
        data = response.json()
        assert "cannot target another user" in data["detail"]
    finally:
        app.state.app_state = original_app_state


@pytest.mark.asyncio
async def test_post_memories_dream_device_own_user_id_succeeds(client, monkeypatch) -> None:
    monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "")
    get_settings.cache_clear()

    device_user_id = uuid.uuid4()

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "user_id": device_user_id,
            "device_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
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

    original_app_state = app.state.app_state

    mock_redis = AsyncMock()
    mock_redis.enqueue_job.return_value = SimpleNamespace(job_id="dream-job-own-specific")

    try:
        app.state.app_state = MagicMock()
        app.state.app_state.db_pool = pool
        app.state.app_state.redis = mock_redis
        app.state.app_state.memory_store = None
        app.state.app_state.video_credits_dal = None

        response = await client.post(
            "/memories/dream",
            json={"user_id": str(device_user_id)},
            headers={"Authorization": "Bearer valid-device-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "enqueued"
        assert data["user_id"] == str(device_user_id)
    finally:
        app.state.app_state = original_app_state
