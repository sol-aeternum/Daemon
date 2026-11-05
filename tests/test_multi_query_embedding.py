"""Component embeddings use one query-mode dispatch and existing outcome accounting."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from orchestrator.compute_runtime import ComputeUnavailable, FAILURE_SETTLEMENT_FAILED
from orchestrator.memory import embedding


@pytest.fixture
def batch(monkeypatch):
    monkeypatch.setattr(embedding, "get_selected_embedding_route_id", lambda: "pinned")
    monkeypatch.setattr(
        embedding.embedding_adapter,
        "approved_route",
        lambda _: SimpleNamespace(
            max_batch_items=100, max_batch_tokens=100000, max_input_tokens=8000
        ),
    )
    monkeypatch.setattr(
        embedding,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_query_model="query-model", embedding_document_model="document-model"
        ),
    )
    dispatch = AsyncMock(
        return_value=embedding.EmbeddingBatchResult(
            embeddings=[[1.0], [2.0], [3.0]],
            provider="azure-openrouter",
            model="served-model",
            storage_model="served-storage",
        )
    )
    success, failure = Mock(), Mock()
    monkeypatch.setattr(embedding, "_embed_texts", dispatch)
    monkeypatch.setattr(embedding, "_record_dispatch_success", success)
    monkeypatch.setattr(embedding, "_record_provider_failure_outcome", failure)
    return dispatch, success, failure


@pytest.mark.asyncio
async def test_one_query_batch_metadata_and_accounting(batch):
    dispatch, success, failure = batch
    results = await embedding.embed_queries_with_metadata(["Cat name", "Dog name", "Bird name"])
    dispatch.assert_awaited_once_with(
        ["Cat name", "Dog name", "Bird name"],
        model="query-model",
        input_type="query",
        max_tokens=embedding.QUERY_MAX_TOKENS,
    )
    assert [r.embedding for r in results] == [[1.0], [2.0], [3.0]]
    assert all(r.storage_model == "served-storage" and r.model == "served-model" for r in results)
    assert all(getattr(r.embedding, "storage_model") == "served-storage" for r in results)
    success.assert_called_once()
    failure.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [True, None, "cloud"])
async def test_component_batch_denies_non_cloud_permission(batch, local):
    with pytest.raises(embedding.EmbeddingConfigurationError):
        await embedding.embed_queries_with_metadata(["query"], local_only=local)
    batch[0].assert_not_awaited()


@pytest.mark.asyncio
async def test_component_batch_requires_single_pinned_send(batch, monkeypatch):
    monkeypatch.setattr(embedding, "get_selected_embedding_route_id", lambda: "")
    with pytest.raises(embedding.EmbeddingConfigurationError):
        await embedding.embed_queries_with_metadata(["query"])
    monkeypatch.setattr(embedding, "get_selected_embedding_route_id", lambda: "pinned")
    monkeypatch.setattr(
        embedding.embedding_adapter,
        "approved_route",
        lambda _: SimpleNamespace(
            max_batch_items=1, max_batch_tokens=100000, max_input_tokens=8000
        ),
    )
    with pytest.raises(embedding.EmbeddingConfigurationError, match="one send"):
        await embedding.embed_queries_with_metadata(["first", "second"])
    batch[0].assert_not_awaited()
    assert await embedding.embed_queries_with_metadata([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("vectors", [[], [[1.0]], [[], [2.0]]])
async def test_component_batch_cardinality_failure_counted_once(batch, vectors):
    dispatch, success, failure = batch
    dispatch.return_value = embedding.EmbeddingBatchResult(
        embeddings=vectors, provider="azure-openrouter", model="m", storage_model="s"
    )
    with pytest.raises(embedding.EmbeddingRequestError):
        await embedding.embed_queries_with_metadata(["first", "second"])
    failure.assert_called_once()
    success.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["failure", "cancellation", "accounting"])
async def test_component_batch_no_retry_and_accounting_errors_escape(batch, kind):
    dispatch, success, failure = batch
    if kind == "accounting":
        error = ComputeUnavailable(
            "settlement_failed", "Accounting incomplete", category=FAILURE_SETTLEMENT_FAILED
        )
    elif kind == "cancellation":
        error = asyncio.CancelledError()
    else:
        error = RuntimeError("PRIVATE_COMPONENT_SENTINEL")
    dispatch.side_effect = error
    expected = embedding.EmbeddingRequestError if kind == "failure" else type(error)
    with pytest.raises(expected) as caught:
        await embedding.embed_queries_with_metadata(["first", "second"])
    assert "PRIVATE_COMPONENT_SENTINEL" not in str(caught.value)
    dispatch.assert_awaited_once()
    success.assert_not_called()
    if kind == "cancellation":
        failure.assert_not_called()
