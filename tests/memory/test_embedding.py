from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.embedding import (
    EmbeddingRequestError,
    embed_documents,
    embed_query,
    get_configured_embedding_providers,
    get_embedding_failures_total,
    get_embedding_provider_used_counts,
    reset_embedding_metrics_for_tests,
)


@pytest.fixture(autouse=True)
def reset_embedding_metrics() -> None:
    reset_embedding_metrics_for_tests()


@pytest.mark.asyncio
async def test_embed_query_uses_query_model_and_input_type():
    mock_embedding = [0.1] * 1024
    mock_response = {
        "data": [{"index": 0, "embedding": mock_embedding}],
        "usage": {"total_tokens": 12},
    }

    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
    )

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_post:
        with patch("orchestrator.memory.embedding.get_settings", return_value=mock_settings):
            with patch(
                "orchestrator.memory.embedding._get_voyage_api_key",
                return_value="test-key",
            ):
                result = await embed_query("test query")

    assert result == mock_embedding
    assert mock_post.called
    kwargs = mock_post.call_args.kwargs
    assert kwargs["model"] == "voyage-4-lite"
    assert kwargs["input_type"] == "query"
    assert kwargs["output_dimension"] == 1024


@pytest.mark.asyncio
async def test_embed_documents_uses_document_model_and_input_type():
    mock_embedding = [0.2] * 1024
    mock_response = {
        "data": [{"index": 0, "embedding": mock_embedding}],
        "usage": {"total_tokens": 22},
    }

    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
    )

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_post:
        with patch("orchestrator.memory.embedding.get_settings", return_value=mock_settings):
            with patch(
                "orchestrator.memory.embedding._get_voyage_api_key",
                return_value="test-key",
            ):
                result = await embed_documents(["memory document"])

    assert result == [mock_embedding]
    kwargs = mock_post.call_args.kwargs
    assert kwargs["model"] == "voyage-4-large"
    assert kwargs["input_type"] == "document"
    assert kwargs["output_dimension"] == 1024


def test_configured_embedding_providers_include_fallback(monkeypatch):
    mock_settings = SimpleNamespace(embedding_fallback_providers="openai")

    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)

    assert get_configured_embedding_providers() == ("voyage", "openai")


@pytest.mark.asyncio
async def test_voyage_failure_falls_back_to_openai(monkeypatch):
    fallback_embedding = [0.7] * 1024
    openai_response = {
        "data": [{"index": 0, "embedding": fallback_embedding}],
        "usage": {"prompt_tokens": 14},
    }
    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="openai",
        embedding_openai_fallback_model="text-embedding-3-small",
        openai_api_key="openai-key",
    )

    monkeypatch.setattr("orchestrator.memory.embedding.MAX_RETRIES", 1)
    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)
    monkeypatch.setattr("orchestrator.memory.embedding._get_voyage_api_key", lambda: "voyage-key")

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        side_effect=EmbeddingRequestError("voyage down"),
    ) as voyage_post:
        with patch(
            "orchestrator.memory.embedding._post_openai_embeddings",
            new_callable=AsyncMock,
            return_value=openai_response,
        ) as openai_post:
            result = await embed_query("fallback query")

    assert result == fallback_embedding
    assert voyage_post.await_count == 1
    assert openai_post.await_count == 1
    assert openai_post.await_args is not None
    assert openai_post.await_args.kwargs["model"] == "text-embedding-3-small"
    assert openai_post.await_args.kwargs["output_dimension"] == 1024
    assert get_embedding_provider_used_counts()["openai"] == 1
    assert get_embedding_failures_total() == 1


@pytest.mark.asyncio
async def test_voyage_circuit_breaker_skips_primary_after_recent_failures(monkeypatch):
    fallback_embedding = [0.8] * 1024
    openai_response = {
        "data": [{"index": 0, "embedding": fallback_embedding}],
        "usage": {"prompt_tokens": 10},
    }
    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="openai",
        embedding_openai_fallback_model="text-embedding-3-small",
        openai_api_key="openai-key",
    )

    monkeypatch.setattr("orchestrator.memory.embedding.MAX_RETRIES", 1)
    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)
    monkeypatch.setattr("orchestrator.memory.embedding._get_voyage_api_key", lambda: "voyage-key")

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        side_effect=EmbeddingRequestError("voyage down"),
    ) as voyage_post:
        with patch(
            "orchestrator.memory.embedding._post_openai_embeddings",
            new_callable=AsyncMock,
            return_value=openai_response,
        ) as openai_post:
            for _ in range(5):
                assert await embed_query("fallback query") == fallback_embedding

            voyage_post.reset_mock()
            assert await embed_query("fallback query") == fallback_embedding

    voyage_post.assert_not_awaited()
    assert openai_post.await_count == 6


@pytest.mark.asyncio
async def test_openai_fallback_enforces_configured_dimension(monkeypatch):
    openai_response = {
        "data": [{"index": 0, "embedding": [0.9] * 1536}],
        "usage": {"prompt_tokens": 12},
    }
    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="openai",
        embedding_openai_fallback_model="text-embedding-3-small",
        openai_api_key="openai-key",
    )

    monkeypatch.setattr("orchestrator.memory.embedding.MAX_RETRIES", 1)
    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)
    monkeypatch.setattr("orchestrator.memory.embedding._get_voyage_api_key", lambda: "voyage-key")

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        side_effect=EmbeddingRequestError("voyage down"),
    ):
        with patch(
            "orchestrator.memory.embedding._post_openai_embeddings",
            new_callable=AsyncMock,
            return_value=openai_response,
        ):
            with pytest.raises(EmbeddingRequestError, match="dimension mismatch"):
                await embed_documents(["memory document"])


@pytest.mark.asyncio
async def test_status_exposes_embedding_provider_metrics():
    from typing import cast

    from orchestrator.auth import AuthenticatedDevice
    from orchestrator.config import Settings
    from orchestrator.db import AppState
    from orchestrator.routes.system import get_status

    fallback_embedding = [1.0] * 1024
    openai_response = {
        "data": [{"index": 0, "embedding": fallback_embedding}],
        "usage": {"prompt_tokens": 12},
    }
    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="openai",
        embedding_openai_fallback_model="text-embedding-3-small",
        openai_api_key="openai-key",
    )

    with patch("orchestrator.memory.embedding.MAX_RETRIES", 1):
        with patch("orchestrator.memory.embedding.get_settings", lambda: mock_settings):
            with patch("orchestrator.memory.embedding._get_voyage_api_key", lambda: "voyage-key"):
                with patch(
                    "orchestrator.memory.embedding._post_embeddings",
                    new_callable=AsyncMock,
                    side_effect=EmbeddingRequestError("voyage down"),
                ):
                    with patch(
                        "orchestrator.memory.embedding._post_openai_embeddings",
                        new_callable=AsyncMock,
                        return_value=openai_response,
                    ):
                        await embed_query("fallback query")

    result = await get_status(
        app_state=AppState(settings=Settings(daemon_environment="development")),
        auth=cast(AuthenticatedDevice, object()),
    )

    assert result["embedding_failures_total"] == 1
    assert result["embedding_provider_used"]["openai"] == 1
