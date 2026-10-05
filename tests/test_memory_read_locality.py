"""Trusted conversation locality, not model arguments, governs memory reads."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.memory import tools
from orchestrator.tools.builtin import create_default_registry


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["cloud", "local", None, "unknown", "wrong-owner"])
@pytest.mark.parametrize("mode", ["semantic", "temporal"])
async def test_read_permissions_from_trusted_registry_conversation(monkeypatch, pipeline, mode):
    owner, conversation_id = uuid.uuid4(), uuid.uuid4()
    store = AsyncMock()
    store.get_conversation.return_value = {
        "user_id": uuid.uuid4() if pipeline == "wrong-owner" else owner,
        "pipeline": "local" if pipeline == "wrong-owner" else pipeline,
    }
    rows = [
        {"content": "CLOUD", "local_only": False},
        {"content": "PRIVATE", "local_only": True},
        {"content": "UNCLASSIFIED_SOURCE"},
    ]
    store.list_memories.return_value = rows
    retrieve = AsyncMock(return_value=rows)
    embed = AsyncMock(
        return_value=SimpleNamespace(embedding=[1.0], model="test", storage_model="test")
    )
    monkeypatch.setattr(tools, "retrieve_memories_for_text", retrieve)
    monkeypatch.setattr(tools, "embed_query_with_metadata", embed)
    registry = create_default_registry(
        memory_store=store, user_id=owner, conversation_id=str(conversation_id)
    )
    tool = registry.get("memory_read")
    assert isinstance(tool, tools.MemoryReadTool)
    result = await tool.execute(query="fictional", mode=mode, include_local=True, pipeline="local")
    assert "CLOUD" in result
    assert ("PRIVATE" in result) == (pipeline == "local")
    assert ("UNCLASSIFIED_SOURCE" in result) == (pipeline == "local")
    store.get_conversation.assert_awaited_once_with(conversation_id)
    if mode == "semantic":
        assert retrieve.await_args is not None
        assert retrieve.await_args.kwargs["include_local"] == (pipeline == "local")
        if pipeline == "cloud":
            embed.assert_awaited_once()
        else:
            embed.assert_not_awaited()
            assert retrieve.await_args.kwargs["query_embedding"] == []
    else:
        embed.assert_not_awaited()
        assert store.list_memories.await_args is not None
        assert store.list_memories.await_args.kwargs["include_local"] == (pipeline == "local")


@pytest.mark.asyncio
async def test_unknown_context_cannot_trigger_second_embedding_in_real_retrieval(monkeypatch):
    from orchestrator.memory import retrieval

    store = AsyncMock()
    store.search_memories.return_value = []
    store.search_memories_bm25.return_value = []
    store.find_entities_by_alias.return_value = []
    store.get_entity_by_lookup_key.return_value = None
    embed = AsyncMock(side_effect=AssertionError("unknown query must not dispatch"))
    monkeypatch.setattr(tools, "embed_query_with_metadata", embed)
    monkeypatch.setattr(retrieval, "embed_query_for_configured_storage_models", embed)
    monkeypatch.setattr(retrieval, "get_selected_embedding_route_id", lambda: "selected")
    tool = tools.MemoryReadTool(store, uuid.uuid4())
    await tool.execute(query="fictional")
    embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_settlement_failure_does_not_become_lexical_success(monkeypatch):
    from orchestrator.compute_runtime import ComputeUnavailable, FAILURE_SETTLEMENT_FAILED

    owner, conversation_id = uuid.uuid4(), uuid.uuid4()
    store = AsyncMock()
    store.get_conversation.return_value = {"user_id": owner, "pipeline": "cloud"}
    error = ComputeUnavailable("settlement_failed", "synthetic", category=FAILURE_SETTLEMENT_FAILED)
    monkeypatch.setattr(tools, "embed_query_with_metadata", AsyncMock(side_effect=error))
    retrieve = AsyncMock()
    monkeypatch.setattr(tools, "retrieve_memories_for_text", retrieve)
    tool = tools.MemoryReadTool(store, owner, conversation_id=conversation_id)
    with pytest.raises(ComputeUnavailable) as caught:
        await tool.execute(query="fictional")
    assert caught.value is error
    retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_selector_fails_closed_without_dispatch(monkeypatch):
    from orchestrator.memory import retrieval
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    def invalid():
        raise EmbeddingConfigurationError("invalid selector")

    store = AsyncMock()
    store.search_memories_bm25.return_value = []
    store.find_entities_by_alias.return_value = []
    store.get_entity_by_lookup_key.return_value = None
    monkeypatch.setattr(retrieval, "get_selected_embedding_route_id", invalid)
    # Storage-space discovery is independent of this supplied-vector branch.
    monkeypatch.setattr(retrieval, "_available_fallback_storage_models", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "get_primary_embedding_storage_model", lambda: "fictional")
    embed = AsyncMock(side_effect=AssertionError("malformed config must not dispatch"))
    monkeypatch.setattr(retrieval, "embed_query_for_configured_storage_models", embed)
    with pytest.raises(EmbeddingConfigurationError):
        await retrieval.retrieve_memories_for_text(
            store, "fictional", user_id=uuid.uuid4(), query_embedding=[]
        )
    embed.assert_not_awaited()
