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
from orchestrator.memory.embedding import EmbeddingBatchResult
from orchestrator.routes import memories as memories_router
from orchestrator.memory.store import MemoryContentConflictError


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Create an async test client with mock DB."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()

    async def override_app_state() -> AppState:
        return app.state.app_state

    app.dependency_overrides[memories_router.get_app_state] = override_app_state
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


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

    async def override_app_state() -> AppState:
        return app.state.app_state

    async def override_device_auth() -> AuthenticatedDevice:
        return fake_device

    app.dependency_overrides[memories_router.get_app_state] = override_app_state
    app.dependency_overrides[memories_router.require_device_auth] = override_device_auth

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
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


def create_embedding_result(vectors: list[list[float]]) -> EmbeddingBatchResult:
    return EmbeddingBatchResult(
        embeddings=vectors,
        provider="voyage",
        model="voyage-4-large",
        storage_model="voyage-4-large",
    )


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
    mock_store.count_listed_memories = AsyncMock(return_value=len(mock_memories))

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
    mock_store.count_listed_memories = AsyncMock(return_value=len(mock_memories))

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
    mock_store.count_listed_memories = AsyncMock(return_value=len(mock_memories))

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
    mock_store.count_listed_memories = AsyncMock(return_value=len(mock_memories))

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
    mock_store.count_listed_memories = AsyncMock(return_value=len(mock_memories))

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


def _edit_store(existing: dict[str, Any], updated: dict[str, Any] | None = None) -> AsyncMock:
    store = AsyncMock()
    store.get_memory = AsyncMock(return_value=existing)
    store.update_memory_content = AsyncMock(
        return_value=updated if updated is not None else {**existing, "content": "x"}
    )
    return store


@pytest.mark.asyncio
async def test_update_memory_returns_the_updated_memory(auth_client) -> None:
    """PATCH /memories/{id} corrects text and category and returns the memory."""
    memory_id = uuid.uuid4()
    existing = create_mock_memory(memory_id=memory_id, content="Old text", category="fact")
    updated = {**existing, "content": "New text", "category": "preference"}
    store = _edit_store(existing, updated)
    set_app_state(create_mock_app_state(store))
    embed = AsyncMock(return_value=create_embedding_result([[0.4, 0.5]]))

    with patch.object(memories_router, "embed_documents_with_metadata", embed):
        response = await auth_client.patch(
            f"/memories/{memory_id}",
            json={"content": "  New text  ", "category": "preference"},
        )

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["content"], body["category"]) == (
        str(memory_id),
        "New text",
        "preference",
    )
    embed.assert_awaited_once_with(["New text"])
    kwargs = store.update_memory_content.call_args.kwargs
    assert store.update_memory_content.call_args.args == (memory_id, "New text")
    assert kwargs["embedding"] == [0.4, 0.5]
    assert kwargs["embedding_model"] == "voyage-4-large"
    assert kwargs["clear_embedding"] is False
    assert kwargs["category"] == "preference"
    assert kwargs["user_id"] == uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["unqualified", "provider"],
)
async def test_update_memory_clears_the_vector_when_it_cannot_reembed(
    auth_client, failure: str
) -> None:
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    memory_id = uuid.uuid4()
    store = _edit_store(create_mock_memory(memory_id=memory_id, content="Old text"))
    set_app_state(create_mock_app_state(store))
    error = (
        EmbeddingConfigurationError("unqualified")
        if failure == "unqualified"
        else RuntimeError("provider down")
    )

    with patch.object(
        memories_router, "embed_documents_with_metadata", AsyncMock(side_effect=error)
    ):
        response = await auth_client.patch(f"/memories/{memory_id}", json={"content": "New"})

    assert response.status_code == 200
    kwargs = store.update_memory_content.call_args.kwargs
    assert kwargs["embedding"] is None
    assert kwargs["clear_embedding"] is True


@pytest.mark.asyncio
async def test_update_memory_category_only_keeps_text_and_vector(auth_client) -> None:
    memory_id = uuid.uuid4()
    store = _edit_store(create_mock_memory(memory_id=memory_id, content="Same text"))
    set_app_state(create_mock_app_state(store))
    embed = AsyncMock()

    with patch.object(memories_router, "embed_documents_with_metadata", embed):
        response = await auth_client.patch(
            f"/memories/{memory_id}",
            json={"content": "Same text", "category": "project"},
        )

    assert response.status_code == 200
    embed.assert_not_called()
    kwargs = store.update_memory_content.call_args.kwargs
    assert kwargs["clear_embedding"] is False
    assert kwargs["embedding"] is None
    assert kwargs["category"] == "project"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"content": "   "},
        {"content": ""},
        {"content": "x" * 2001},
        {"content": "ok", "category": "observation"},
        {"content": "ok", "category": "secret"},
        {},
    ],
    ids=["blank", "empty", "too-long", "system-category", "unknown-category", "missing"],
)
async def test_update_memory_rejects_invalid_edits(auth_client, body) -> None:
    memory_id = uuid.uuid4()
    store = _edit_store(create_mock_memory(memory_id=memory_id))
    set_app_state(create_mock_app_state(store))
    response = await auth_client.patch(f"/memories/{memory_id}", json=body)
    assert response.status_code == 422
    store.update_memory_content.assert_not_called()


@pytest.mark.asyncio
async def test_update_memory_denies_another_accounts_memory(auth_client) -> None:
    memory_id = uuid.uuid4()
    store = _edit_store(create_mock_memory(memory_id=memory_id, user_id=uuid.uuid4()))
    set_app_state(create_mock_app_state(store))
    response = await auth_client.patch(f"/memories/{memory_id}", json={"content": "Mine now"})
    assert response.status_code == 404
    store.update_memory_content.assert_not_called()


@pytest.mark.asyncio
async def test_update_memory_returns_404_if_the_memory_vanished(auth_client) -> None:
    memory_id = uuid.uuid4()
    store = _edit_store(create_mock_memory(memory_id=memory_id, content="Old"))
    store.get_memory = AsyncMock(
        side_effect=[create_mock_memory(memory_id=memory_id, content="Old"), None]
    )
    store.update_memory_content = AsyncMock(return_value=None)
    set_app_state(create_mock_app_state(store))
    with patch.object(
        memories_router,
        "embed_documents_with_metadata",
        AsyncMock(return_value=create_embedding_result([[0.1]])),
    ):
        response = await auth_client.patch(f"/memories/{memory_id}", json={"content": "New"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_memory_content_conflict_returns_409(auth_client) -> None:
    """Duplicate memory edits return a controlled conflict."""
    memory_id = uuid.uuid4()
    store = _edit_store(create_mock_memory(memory_id=memory_id, content="Old"))
    store.update_memory_content = AsyncMock(
        side_effect=MemoryContentConflictError(
            "Memory content duplicates an existing active memory"
        )
    )
    set_app_state(create_mock_app_state(store))
    with patch.object(
        memories_router,
        "embed_documents_with_metadata",
        AsyncMock(return_value=create_embedding_result([[0.1]])),
    ):
        response = await auth_client.patch(
            f"/memories/{memory_id}", json={"content": "Updated content"}
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "Memory content duplicates an existing active memory"


@pytest.mark.asyncio
async def test_update_memory_store_unavailable(auth_client) -> None:
    set_app_state(create_mock_app_state(None))
    response = await auth_client.patch(f"/memories/{uuid.uuid4()}", json={"content": "x"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_reembed_only_applies_vectors_to_unchanged_content(auth_client) -> None:
    from orchestrator.memory.store import compute_memory_content_hash

    memory_id = uuid.uuid4()
    store = AsyncMock()
    store.export_memories = AsyncMock(
        return_value=[create_mock_memory(memory_id=memory_id, content="Embedded text")]
    )
    store.update_memory_embedding = AsyncMock(return_value=False)  # edited meanwhile
    set_app_state(create_mock_app_state(store))
    with patch.object(
        memories_router,
        "embed_documents_with_metadata",
        AsyncMock(return_value=create_embedding_result([[0.2]])),
    ):
        response = await auth_client.post("/memories/reembed", json={"status": "active"})

    assert response.status_code == 200
    assert response.json()["updated"] == 0
    kwargs = store.update_memory_embedding.call_args.kwargs
    assert kwargs["expected_content_hash"] == compute_memory_content_hash("Embedded text")


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
async def test_confirm_memory_content_conflict_returns_409(auth_client, monkeypatch) -> None:
    """Test that confirming a duplicate legacy memory returns a controlled conflict."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_memory = create_mock_memory(memory_id=memory_id)
    mock_store.get_memory = AsyncMock(return_value=mock_memory)
    mock_store.confirm_memory = AsyncMock(
        side_effect=MemoryContentConflictError(
            "Memory content duplicates an existing active memory"
        )
    )

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await auth_client.post(
        f"/memories/{memory_id}/confirm",
        json={"status": "confirmed"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Memory content duplicates an existing active memory"


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
        "orchestrator.routes.memories.embed_documents_with_metadata",
        new_callable=AsyncMock,
        return_value=create_embedding_result([vector_a, vector_c]),
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
        "orchestrator.routes.memories.embed_documents_with_metadata",
        new_callable=AsyncMock,
        return_value=create_embedding_result([vector]),
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


# --- Hardened POST /memories/import -------------------------------------------------


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeConnection:
    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakeAcquire:
    async def __aenter__(self) -> _FakeConnection:
        return _FakeConnection()

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _import_store() -> AsyncMock:
    """Store double whose pool yields a connection with a transaction."""
    store = AsyncMock()
    store._pool = MagicMock()
    store._pool.acquire = MagicMock(side_effect=lambda: _FakeAcquire())
    return store


def _dedup_result(new: int = 0, merged: int = 0, superseded: int = 0) -> Any:
    from orchestrator.memory.dedup import DedupResult

    return DedupResult(
        new=[{"id": uuid.uuid4()} for _ in range(new)],
        merged=[{"id": uuid.uuid4()} for _ in range(merged)],
        superseded=[{"id": uuid.uuid4()} for _ in range(superseded)],
    )


@pytest.mark.asyncio
async def test_import_memories_uses_server_controlled_fields_and_dedup(auth_client) -> None:
    mock_store = _import_store()
    set_app_state(create_mock_app_state(mock_store))
    embed = AsyncMock(return_value=create_embedding_result([[0.1], [0.2]]))
    dedup = AsyncMock(side_effect=[_dedup_result(new=1), _dedup_result(merged=1)])

    with (
        patch.object(memories_router, "embed_documents_with_metadata", embed),
        patch("orchestrator.memory.dedup.deduplicate_facts", dedup),
    ):
        response = await auth_client.post(
            "/memories/import",
            json={
                "memories": [
                    {
                        "content": "  Prefers metric units  ",
                        "category": "preference",
                        "status": "deleted",
                        "source_type": "system",
                        "local_only": True,
                        "confidence": 99,
                        "memory_slot": "identity.name",
                        "embedding": [9.9, 9.9],
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {"content": "Lives in Adelaide"},
                ]
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "received": 2,
        "processed": 2,
        "inserted": 1,
        "created": 1,
        "merged": 1,
        "superseded": 0,
    }
    embed.assert_awaited_once_with(["Prefers metric units", "Lives in Adelaide"])
    calls = [call.kwargs for call in dedup.await_args_list]
    assert [c["source_type"] for c in calls] == ["import", "import"]
    assert [c["status"] for c in calls] == ["active", "active"]
    assert all(c["conversation_id"] is None for c in calls)
    assert all(c["lock_conn"] is not None for c in calls), "each item runs in a transaction"
    assert [[(f.content, f.category, f.slot) for f in c["facts"]] for c in calls] == [
        [("Prefers metric units", "preference", None)],
        [("Lives in Adelaide", "fact", None)],
    ]
    assert [[p.embeddings for p in c["prepared_embeddings"]] for c in calls] == [
        [[[0.1]]],
        [[[0.2]]],
    ]
    mock_store.import_memories.assert_not_called()


@pytest.mark.asyncio
async def test_import_memories_embeds_in_bounded_batches(auth_client) -> None:
    set_app_state(create_mock_app_state(_import_store()))
    sizes: list[int] = []

    async def embed(texts: list[str]) -> EmbeddingBatchResult:
        sizes.append(len(texts))
        return create_embedding_result([[float(i)] for i in range(len(texts))])

    async def dedup(**kwargs: Any) -> Any:
        return _dedup_result(new=len(kwargs["facts"]))

    with (
        patch.object(memories_router, "embed_documents_with_metadata", embed),
        patch("orchestrator.memory.dedup.deduplicate_facts", dedup),
    ):
        response = await auth_client.post(
            "/memories/import",
            json={"memories": [{"content": f"Note {i}"} for i in range(120)]},
        )

    assert response.status_code == 200
    assert sizes == [50, 50, 20]
    assert response.json()["created"] == 120


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "memories",
    [
        [{"content": "x" * 2001}],
        [{"content": "   "}],
        [{"content": "ok", "category": "secret-admin"}],
        [{"content": f"n{i}"} for i in range(501)],
        [{"category": "fact"}],
    ],
    ids=["too-long", "blank", "unknown-category", "too-many", "missing-content"],
)
async def test_import_memories_rejects_invalid_requests(auth_client, memories) -> None:
    set_app_state(create_mock_app_state(AsyncMock()))
    dedup = AsyncMock()
    with patch("orchestrator.memory.dedup.deduplicate_facts", dedup):
        response = await auth_client.post("/memories/import", json={"memories": memories})
    assert response.status_code == 422
    dedup.assert_not_called()


@pytest.mark.asyncio
async def test_import_memories_falls_back_when_embeddings_unqualified(auth_client) -> None:
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    set_app_state(create_mock_app_state(_import_store()))
    dedup = AsyncMock(return_value=_dedup_result(new=1))
    with (
        patch.object(
            memories_router,
            "embed_documents_with_metadata",
            AsyncMock(side_effect=EmbeddingConfigurationError("unqualified")),
        ),
        patch("orchestrator.memory.dedup.deduplicate_facts", dedup),
    ):
        response = await auth_client.post(
            "/memories/import", json={"memories": [{"content": "Plain fact"}]}
        )
    assert response.status_code == 200
    assert dedup.await_args is not None
    assert dedup.await_args.kwargs["prepared_embeddings"] is None


@pytest.mark.asyncio
async def test_import_memories_reports_partial_progress_when_a_service_fails(
    auth_client,
) -> None:
    set_app_state(create_mock_app_state(_import_store()))
    calls = 0

    async def embed(texts: list[str]) -> EmbeddingBatchResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("provider down")
        return create_embedding_result([[0.0]] * len(texts))

    seen = 0

    async def dedup(**kwargs: Any) -> Any:
        nonlocal seen
        seen += 1
        return _dedup_result(merged=1) if seen == 1 else _dedup_result(new=1)

    with (
        patch.object(memories_router, "embed_documents_with_metadata", embed),
        patch("orchestrator.memory.dedup.deduplicate_facts", dedup),
    ):
        response = await auth_client.post(
            "/memories/import",
            json={"memories": [{"content": f"Note {i}"} for i in range(60)]},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "message": "Import stopped because a memory service was unavailable.",
        "received": 60,
        "processed": 50,
        "created": 49,
        "merged": 1,
        "superseded": 0,
    }


@pytest.mark.asyncio
async def test_import_memories_unavailable_store(auth_client) -> None:
    set_app_state(create_mock_app_state(None))
    response = await auth_client.post("/memories/import", json={"memories": [{"content": "x"}]})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_import_memories_counts_only_items_committed_before_a_store_failure(
    auth_client,
) -> None:
    set_app_state(create_mock_app_state(_import_store()))
    outcomes = [_dedup_result(new=1), _dedup_result(merged=1), RuntimeError("db down")]

    async def dedup(**kwargs: Any) -> Any:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with (
        patch.object(
            memories_router,
            "embed_documents_with_metadata",
            AsyncMock(return_value=create_embedding_result([[0.0]] * 4)),
        ),
        patch("orchestrator.memory.dedup.deduplicate_facts", dedup),
    ):
        response = await auth_client.post(
            "/memories/import",
            json={"memories": [{"content": f"Note {i}"} for i in range(4)]},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "message": "Import stopped because a memory service was unavailable.",
        "received": 4,
        "processed": 2,
        "created": 1,
        "merged": 1,
        "superseded": 0,
    }


@pytest.mark.asyncio
async def test_import_memories_rolls_back_an_unclassified_item(auth_client) -> None:
    set_app_state(create_mock_app_state(_import_store()))
    outcomes = [_dedup_result(new=1), _dedup_result()]

    async def dedup(**kwargs: Any) -> Any:
        return outcomes.pop(0)

    with (
        patch.object(
            memories_router,
            "embed_documents_with_metadata",
            AsyncMock(return_value=create_embedding_result([[0.0]] * 2)),
        ),
        patch("orchestrator.memory.dedup.deduplicate_facts", dedup),
    ):
        response = await auth_client.post(
            "/memories/import",
            json={"memories": [{"content": "One"}, {"content": "Two"}]},
        )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["processed"] == 1
    assert detail["created"] + detail["merged"] + detail["superseded"] == detail["processed"]


# --- GET /memories paging and filter contract (#249) -------------------------------


def _list_store(page: list[dict[str, Any]], total: int) -> AsyncMock:
    store = AsyncMock()
    store.list_memories = AsyncMock(return_value=page)
    store.count_listed_memories = AsyncMock(return_value=total)
    return store


@pytest.mark.asyncio
async def test_list_memories_reports_true_total_and_continuation(auth_client) -> None:
    store = _list_store([create_mock_memory() for _ in range(20)], total=21)
    set_app_state(create_mock_app_state(store))

    first = await auth_client.get("/memories?limit=20&offset=0")
    assert first.status_code == 200
    body = first.json()
    assert (len(body["memories"]), body["total"], body["has_more"]) == (20, 21, True)
    assert (body["limit"], body["offset"]) == (20, 0)

    store.list_memories = AsyncMock(return_value=[create_mock_memory()])
    last = (await auth_client.get("/memories?limit=20&offset=20")).json()
    assert (len(last["memories"]), last["total"], last["has_more"]) == (1, 21, False)


@pytest.mark.asyncio
async def test_list_memories_empty_result(auth_client) -> None:
    set_app_state(create_mock_app_state(_list_store([], total=0)))
    body = (await auth_client.get("/memories?search=nothing")).json()
    assert (body["memories"], body["total"], body["has_more"]) == ([], 0, False)


@pytest.mark.asyncio
async def test_list_memories_count_uses_the_same_filters_as_the_page(auth_client) -> None:
    store = _list_store([], total=0)
    set_app_state(create_mock_app_state(store))

    response = await auth_client.get(
        "/memories?category=preference&status=superseded&source_type=import&search=tea"
    )

    assert response.status_code == 200
    page_kwargs = dict(store.list_memories.call_args.kwargs)
    count_kwargs = dict(store.count_listed_memories.call_args.kwargs)
    page_kwargs.pop("limit")
    page_kwargs.pop("offset")
    assert page_kwargs == count_kwargs
    assert count_kwargs["category"] == "preference"
    assert count_kwargs["status"] == "superseded"
    assert count_kwargs["source_type"] == "import"
    assert count_kwargs["search"] == "tea"


@pytest.mark.asyncio
async def test_list_memories_all_status_means_every_non_deleted_status(auth_client) -> None:
    store = _list_store([], total=0)
    set_app_state(create_mock_app_state(store))

    await auth_client.get("/memories?status=all")

    status = store.count_listed_memories.call_args.kwargs["status"]
    assert sorted(status) == ["active", "inactive", "pending", "rejected", "superseded"]
    assert "deleted" not in status


@pytest.mark.asyncio
async def test_list_memories_defaults_to_active(auth_client) -> None:
    store = _list_store([], total=0)
    set_app_state(create_mock_app_state(store))
    await auth_client.get("/memories")
    assert store.list_memories.call_args.kwargs["status"] == "active"
    assert store.list_memories.call_args.kwargs["source_type"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "status=deleted",
        "status=bogus",
        "source_type=system",
        "category=secret",
        "limit=101",
        "offset=-1",
    ],
)
async def test_list_memories_rejects_unsupported_filters(auth_client, query) -> None:
    store = _list_store([], total=0)
    set_app_state(create_mock_app_state(store))
    response = await auth_client.get(f"/memories?{query}")
    assert response.status_code == 422
    store.list_memories.assert_not_called()


@pytest.mark.asyncio
async def test_update_memory_writes_only_against_the_content_it_read(auth_client) -> None:
    memory_id = uuid.uuid4()
    existing = create_mock_memory(memory_id=memory_id, content="Old", content_hash="hash-old")
    store = _edit_store(existing)
    set_app_state(create_mock_app_state(store))
    with patch.object(
        memories_router,
        "embed_documents_with_metadata",
        AsyncMock(return_value=create_embedding_result([[0.1]])),
    ):
        await auth_client.patch(f"/memories/{memory_id}", json={"content": "New"})
    kwargs = store.update_memory_content.call_args.kwargs
    assert kwargs["require_content_hash"] is True
    assert kwargs["expected_content_hash"] == "hash-old"


@pytest.mark.asyncio
async def test_update_memory_returns_412_when_another_edit_landed_first(auth_client) -> None:
    memory_id = uuid.uuid4()
    read = create_mock_memory(memory_id=memory_id, content="X", content_hash="hash-x")
    newer = create_mock_memory(memory_id=memory_id, content="Y", content_hash="hash-y")
    store = _edit_store(read)
    store.get_memory = AsyncMock(side_effect=[read, newer])
    store.update_memory_content = AsyncMock(return_value=None)  # hash no longer matches
    set_app_state(create_mock_app_state(store))

    response = await auth_client.patch(
        f"/memories/{memory_id}", json={"content": "X", "category": "project"}
    )

    assert response.status_code == 412
    assert response.json()["detail"] == (
        "Memory changed since it was read; reload it and try again"
    )
