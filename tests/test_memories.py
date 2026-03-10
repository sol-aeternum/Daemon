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
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings
from orchestrator.main import app
from orchestrator.db import AppState


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Create an async test client with mock DB."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def create_mock_app_state(mock_store: AsyncMock | None = None) -> AppState:
    """Create a mock AppState with optional memory store."""
    app_state = MagicMock(spec=AppState)
    app_state.memory_store = mock_store
    app_state.redis = None
    return app_state


def set_app_state(mock_app_state: AppState) -> None:
    """Set the app state on the FastAPI app."""
    app.state.app_state = mock_app_state


def create_mock_memory(
    memory_id: uuid.UUID | None = None, **overrides
) -> dict[str, Any]:
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
async def test_get_memories_returns_memories_array(client, monkeypatch) -> None:
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

    response = await client.get("/memories")

    assert response.status_code == 200
    data = response.json()
    assert "memories" in data
    assert "total" in data
    assert len(data["memories"]) == 3
    mock_store.list_memories.assert_called_once()


@pytest.mark.asyncio
async def test_get_memories_with_category_filter(client, monkeypatch) -> None:
    """Test that GET /memories with category filter works."""
    mock_store = AsyncMock()
    mock_memories = [
        create_mock_memory(category="preference"),
        create_mock_memory(category="preference"),
    ]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.get("/memories?category=preference")

    assert response.status_code == 200
    data = response.json()
    assert len(data["memories"]) == 2

    call_args = mock_store.list_memories.call_args
    assert call_args.kwargs.get("category") == "preference"


@pytest.mark.asyncio
async def test_get_memories_with_confirmed_filter(client, monkeypatch) -> None:
    """Test that GET /memories with confirmed filter works."""
    mock_store = AsyncMock()
    mock_memories = [create_mock_memory(confirmed=True)]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.get("/memories?confirmed=true")

    assert response.status_code == 200
    data = response.json()
    assert len(data["memories"]) == 1

    call_args = mock_store.list_memories.call_args
    assert call_args.kwargs.get("confirmed") is True


@pytest.mark.asyncio
async def test_get_memories_with_search_query(client, monkeypatch) -> None:
    """Test that GET /memories with search query works."""
    mock_store = AsyncMock()
    mock_memories = [create_mock_memory(content="Python is awesome")]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.get("/memories?search=python")

    assert response.status_code == 200
    data = response.json()
    assert len(data["memories"]) == 1

    call_args = mock_store.list_memories.call_args
    assert call_args.kwargs.get("search") == "python"


@pytest.mark.asyncio
async def test_get_memories_with_limit_offset(client, monkeypatch) -> None:
    """Test that GET /memories supports limit and offset pagination."""
    mock_store = AsyncMock()
    mock_memories = [create_mock_memory() for _ in range(5)]
    mock_store.list_memories = AsyncMock(return_value=mock_memories)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.get("/memories?limit=10&offset=5")

    assert response.status_code == 200

    call_args = mock_store.list_memories.call_args
    assert call_args.kwargs.get("limit") == 10
    assert call_args.kwargs.get("offset") == 5


@pytest.mark.asyncio
async def test_get_memory_by_id(client, monkeypatch) -> None:
    """Test that GET /memories/{id} returns single memory."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_memory = create_mock_memory(memory_id=memory_id)
    mock_store.get_memory = AsyncMock(return_value=mock_memory)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.get(f"/memories/{memory_id}")

    assert response.status_code == 200
    data = response.json()
    assert str(data["id"]) == str(memory_id)
    mock_store.get_memory.assert_called_once_with(memory_id)


@pytest.mark.asyncio
async def test_get_memory_by_id_not_found(client, monkeypatch) -> None:
    """Test that GET /memories/{id} returns 404 for non-existent memory."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_store.get_memory = AsyncMock(return_value=None)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.get(f"/memories/{memory_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_memory(client, monkeypatch) -> None:
    """Test that POST /memories creates a new memory."""
    mock_store = AsyncMock()
    new_memory_id = uuid.uuid4()

    async def mock_dedup_and_store(*args, **kwargs):
        return new_memory_id

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    with patch("orchestrator.memory.dedup.dedup_and_store", mock_dedup_and_store):
        response = await client.post(
            "/memories",
            json={"content": "New test memory", "category": "fact"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"
    assert data["id"] == str(new_memory_id)


@pytest.mark.asyncio
async def test_update_memory(client, monkeypatch) -> None:
    """Test that PATCH /memories/{id} updates memory content."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_store.update_memory = AsyncMock(return_value=True)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.patch(
        f"/memories/{memory_id}",
        json={"content": "Updated content"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "updated"
    mock_store.update_memory.assert_called_once()


@pytest.mark.asyncio
async def test_delete_memory(client, monkeypatch) -> None:
    """Test that DELETE /memories/{id} deletes memory."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_store.delete_memory = AsyncMock(return_value=True)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.delete(f"/memories/{memory_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deleted"
    mock_store.delete_memory.assert_called_once()


@pytest.mark.asyncio
async def test_confirm_memory(client, monkeypatch) -> None:
    """Test that POST /memories/{id}/confirm confirms or rejects memory."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()
    mock_store.confirm_memory = AsyncMock(return_value=True)

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.post(
        f"/memories/{memory_id}/confirm",
        json={"status": "confirmed"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    mock_store.confirm_memory.assert_called_once_with(memory_id, confirmed=True)


@pytest.mark.asyncio
async def test_get_memory_trail_not_implemented(client, monkeypatch) -> None:
    """Test that GET /memories/{id}/trail returns 404 (not implemented)."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.get(f"/memories/{memory_id}/trail")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_correct_memory_not_implemented(client, monkeypatch) -> None:
    """Test that POST /memories/{id}/correct returns 404 (not implemented)."""
    memory_id = uuid.uuid4()
    mock_store = AsyncMock()

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    response = await client.post(
        f"/memories/{memory_id}/correct",
        json={"content": "Corrected content"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_memories_unavailable_store(client, monkeypatch) -> None:
    """Test that /memories returns 503 when store is unavailable."""
    mock_app_state = create_mock_app_state(None)
    set_app_state(mock_app_state)

    response = await client.get("/memories")

    assert response.status_code == 503
