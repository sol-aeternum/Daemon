from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.embedding import (
    embed_documents,
    embed_query,
    reset_embedding_metrics_for_tests,
)


@pytest.fixture(autouse=True)
def reset_embedding_metrics() -> None:
    reset_embedding_metrics_for_tests()


@pytest.mark.asyncio
async def test_embed_documents_returns_1024_vectors():
    vector = [0.3] * 1024
    response = {
        "data": [{"index": 0, "embedding": vector}],
        "usage": {"total_tokens": 8},
    }
    settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
    )

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=response,
    ):
        with patch("orchestrator.memory.embedding.get_settings", return_value=settings):
            with patch(
                "orchestrator.memory.embedding._get_voyage_api_key",
                return_value="test-key",
            ):
                result = await embed_documents(["doc"])

    assert result == [vector]
    assert len(result[0]) == 1024


@pytest.mark.asyncio
async def test_embed_query_returns_1024_vectors():
    vector = [0.4] * 1024
    response = {
        "data": [{"index": 0, "embedding": vector}],
        "usage": {"total_tokens": 6},
    }
    settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
    )

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=response,
    ):
        with patch("orchestrator.memory.embedding.get_settings", return_value=settings):
            with patch(
                "orchestrator.memory.embedding._get_voyage_api_key",
                return_value="test-key",
            ):
                result = await embed_query("query")

    assert result == vector
    assert len(result) == 1024


@pytest.mark.asyncio
async def test_embed_documents_dispatches_correct_model_and_input_type():
    vector = [0.5] * 1024
    response = {
        "data": [{"index": 0, "embedding": vector}],
        "usage": {"total_tokens": 9},
    }
    settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
    )

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=response,
    ) as post:
        with patch("orchestrator.memory.embedding.get_settings", return_value=settings):
            with patch(
                "orchestrator.memory.embedding._get_voyage_api_key",
                return_value="test-key",
            ):
                _ = await embed_documents(["memory"])

    kwargs = post.call_args.kwargs
    assert kwargs["model"] == "voyage-4-large"
    assert kwargs["input_type"] == "document"


@pytest.mark.asyncio
async def test_embed_query_dispatches_correct_model_and_input_type():
    vector = [0.6] * 1024
    response = {
        "data": [{"index": 0, "embedding": vector}],
        "usage": {"total_tokens": 7},
    }
    settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
    )

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=response,
    ) as post:
        with patch("orchestrator.memory.embedding.get_settings", return_value=settings):
            with patch(
                "orchestrator.memory.embedding._get_voyage_api_key",
                return_value="test-key",
            ):
                _ = await embed_query("search text")

    kwargs = post.call_args.kwargs
    assert kwargs["model"] == "voyage-4-lite"
    assert kwargs["input_type"] == "query"


def test_embedding_providers_follow_inference_policy_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.entitlements.errors import PolicyError
    from orchestrator.memory import embedding

    approved: set[str] = set()
    monkeypatch.setattr(
        embedding,
        "load_inference_policy",
        lambda: SimpleNamespace(is_tool_service_approved=lambda service_id: service_id in approved),
    )
    with pytest.raises(embedding.EmbeddingConfigurationError):
        embedding._require_approved_embedding_service("voyage")

    approved.add("voyage-embeddings")
    embedding._require_approved_embedding_service("voyage")
    # Providers without a reviewed tool service stay denied by default.
    for provider in ("openai", "openrouter"):
        with pytest.raises(embedding.EmbeddingConfigurationError):
            embedding._require_approved_embedding_service(provider)

    def broken_policy():
        raise PolicyError("invalid")

    monkeypatch.setattr(embedding, "load_inference_policy", broken_policy)
    with pytest.raises(embedding.EmbeddingConfigurationError):
        embedding._require_approved_embedding_service("voyage")
