"""Blank memory content is rejected at every ingress (#323).

Covers POST /memories, the memory_write tool's create/update, the dedup
pipeline's alignment with prepared embeddings, and the empty-vector contract
of the embedding helper.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.auth import AuthenticatedDevice
from orchestrator.config import get_settings
from orchestrator.db import AppState
from orchestrator.main import app
from orchestrator.memory.dedup import deduplicate_facts, prepare_memory_embedding
from orchestrator.memory.embedding import (
    EmbeddingBatchResult,
    EmbeddingConfigurationError,
    EmbeddingRequestError,
)
from orchestrator.memory.extraction import ExtractedFact
from orchestrator.memory.tools import MemoryWriteTool
from orchestrator.routes import memories as memories_router

USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
BLANKS = ["", " ", "   ", "\t", "\n", " \t\n\r ", " ", "  ", "　"]


def _batch(vectors: list[list[float]]) -> EmbeddingBatchResult:
    return EmbeddingBatchResult(
        embeddings=vectors,
        provider="voyage",
        model="voyage-4-large",
        storage_model="voyage-4-large",
    )


@pytest_asyncio.fixture
async def auth_client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()

    async def device() -> AuthenticatedDevice:
        return AuthenticatedDevice(user_id=USER_ID, device_id=uuid.uuid4(), session_id=uuid.uuid4())

    store = AsyncMock()
    state = MagicMock(spec=AppState)
    state.memory_store = store
    state.redis = None
    app.state.app_state = state

    async def app_state() -> AppState:
        return state

    app.dependency_overrides[memories_router.get_app_state] = app_state
    app.dependency_overrides[memories_router.require_device_auth] = device
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# --- POST /memories ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("content", BLANKS)
async def test_create_rejects_blank_content_before_any_side_effect(auth_client, content) -> None:
    dedup = AsyncMock()
    with patch("orchestrator.memory.dedup.dedup_and_store", dedup):
        response = await auth_client.post("/memories", json={"content": content})
    assert response.status_code == 422
    dedup.assert_not_called()


@pytest.mark.asyncio
async def test_create_stores_valid_content_without_surrounding_whitespace(auth_client) -> None:
    dedup = AsyncMock(return_value=uuid.uuid4())
    with patch("orchestrator.memory.dedup.dedup_and_store", dedup):
        response = await auth_client.post(
            "/memories", json={"content": "\n  Likes green tea \t", "category": "preference"}
        )
    assert response.status_code == 200
    assert dedup.call_args.kwargs["content"] == "Likes green tea"


@pytest.mark.asyncio
async def test_create_maps_a_missing_vector_to_503_not_500(auth_client) -> None:
    dedup = AsyncMock(side_effect=EmbeddingRequestError("provider returned 0 vectors"))
    with patch("orchestrator.memory.dedup.dedup_and_store", dedup):
        response = await auth_client.post("/memories", json={"content": "Valid text"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Memory embedding service unavailable"


# --- memory_write tool -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [*BLANKS, None, 7])
async def test_tool_create_rejects_blank_before_quota_or_embedding(content: Any) -> None:
    store = AsyncMock()
    tool = MemoryWriteTool(store, USER_ID)
    quota = AsyncMock()
    with (
        patch.object(tool, "_check_write_quota", quota),
        patch("orchestrator.memory.tools.prepare_memory_embedding", AsyncMock()) as prepare,
    ):
        result = await tool.execute(action="create", content=content)
    assert result == "Memory content can't be blank."
    quota.assert_not_called()
    prepare.assert_not_called()
    store.acquire_user_cap_lock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["   ", "\n\t", " "])
async def test_tool_update_rejects_blank_before_quota_close_or_embedding(content: str) -> None:
    memory_id = uuid.uuid4()
    store = AsyncMock()
    store.get_memory = AsyncMock(
        return_value={
            "id": memory_id,
            "user_id": USER_ID,
            "content": "Existing",
            "category": "fact",
            "status": "active",
            "valid_to": None,
        }
    )
    tool = MemoryWriteTool(store, USER_ID)
    quota = AsyncMock()
    with (
        patch.object(tool, "_check_write_quota", quota),
        patch("orchestrator.memory.tools.prepare_memory_embedding", AsyncMock()) as prepare,
    ):
        result = await tool.execute(action="update", memory_id=str(memory_id), content=content)
    assert result == "Memory content can't be blank."
    quota.assert_not_called()
    prepare.assert_not_called()
    store.close_memory.assert_not_called()


@pytest.mark.asyncio
async def test_tool_update_without_content_still_inherits_existing_text() -> None:
    memory_id = uuid.uuid4()
    store = AsyncMock()
    store.get_memory = AsyncMock(
        return_value={
            "id": memory_id,
            "user_id": USER_ID,
            "local_only": False,
            "content": "Existing",
            "category": "fact",
            "status": "active",
            "valid_to": None,
        }
    )
    tool = MemoryWriteTool(store, USER_ID)
    refusal = MagicMock(refusal="stop here", reservation=None)
    quota = AsyncMock(return_value=refusal)
    with patch.object(tool, "_check_write_quota", quota):
        result = await tool.execute(action="update", memory_id=str(memory_id), category="project")
    assert result == "stop here"  # validation passed; reached the quota check
    quota.assert_awaited_once()


# --- dedup pipeline and embedding contract ---------------------------------------------


@pytest.mark.asyncio
async def test_prepare_memory_embedding_rejects_blank_and_missing_vectors() -> None:
    with pytest.raises(ValueError):
        await prepare_memory_embedding("   ")
    with (
        patch(
            "orchestrator.memory.dedup.embed_documents_with_metadata",
            AsyncMock(return_value=_batch([])),
        ),
        pytest.raises(EmbeddingRequestError),
    ):
        await prepare_memory_embedding("Valid text")


@pytest.mark.asyncio
async def test_mixed_batch_skips_blank_facts_and_keeps_vectors_aligned() -> None:
    store = AsyncMock()
    store.search_memories.return_value = []
    store.search_memories_bm25.return_value = []
    inserted: list[tuple[str, list[float]]] = []

    async def insert_memory(**kwargs: Any) -> tuple[dict[str, Any], bool]:
        inserted.append((kwargs["content"], kwargs["embedding"]))
        return {"id": uuid.uuid4(), "content": kwargs["content"], "valid_to": None}, True

    store._insert_memory_with_outcome.side_effect = insert_memory
    facts = [
        ExtractedFact(content="First fact", category="fact", confidence=0.9),
        ExtractedFact(content="   ", category="fact", confidence=0.9),
        ExtractedFact(content="Third fact", category="fact", confidence=0.9),
    ]
    prepared = [_batch([[0.1]]), _batch([[9.9]]), _batch([[0.3]])]

    result = await deduplicate_facts(
        store,
        USER_ID,
        facts,
        conversation_id=None,
        prepared_embeddings=prepared,
        lock_conn=AsyncMock(),
    )

    assert inserted == [("First fact", [0.1]), ("Third fact", [0.3])]
    assert len(result.new) == 2


@pytest.mark.asyncio
async def test_empty_provider_result_for_real_text_is_a_clear_error_not_index_error() -> None:
    store = AsyncMock()
    with pytest.raises(EmbeddingRequestError):
        await deduplicate_facts(
            store,
            USER_ID,
            [ExtractedFact(content="Real text", category="fact", confidence=0.9)],
            conversation_id=None,
            prepared_embeddings=[_batch([])],
        )
    store.insert_memory.assert_not_called()


@pytest.mark.asyncio
async def test_unqualified_embeddings_keep_the_lexical_fallback() -> None:
    store = AsyncMock()
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    @asynccontextmanager
    async def context():
        conn = AsyncMock()
        conn.transaction = MagicMock(side_effect=context)
        yield conn

    store._pool.acquire = MagicMock(side_effect=context)
    store._discover_equivalence_candidates.return_value = []
    store._insert_memory_with_outcome.return_value = (
        {"id": uuid.uuid4(), "content": "Real text"},
        True,
    )
    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata",
        AsyncMock(side_effect=EmbeddingConfigurationError("unqualified")),
    ):
        result = await deduplicate_facts(
            store,
            USER_ID,
            [ExtractedFact(content="Real text", category="fact", confidence=0.9)],
            conversation_id=None,
        )
    assert len(result.new) == 1
    assert store._insert_memory_with_outcome.call_args.kwargs["embedding"] is None
