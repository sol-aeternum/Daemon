from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.embedding import (
    EmbeddingRequestError,
    embed_documents,
    embed_documents_with_metadata,
    embed_query,
    embed_query_for_configured_storage_models,
    embed_query_with_metadata,
    get_configured_embedding_fallback_storage_models,
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


def test_configured_embedding_providers_default_to_primary_only(monkeypatch):
    mock_settings = SimpleNamespace(embedding_fallback_providers="")

    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)

    assert get_configured_embedding_providers() == ("voyage",)
    assert get_configured_embedding_fallback_storage_models() == ()


def test_configured_embedding_storage_models_include_openrouter_identity(monkeypatch):
    mock_settings = SimpleNamespace(
        embedding_fallback_providers="openrouter,openai",
        embedding_openrouter_document_model="voyageai/voyage-4-large",
        embedding_openai_fallback_model="text-embedding-3-small",
    )

    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)

    assert get_configured_embedding_fallback_storage_models() == (
        "openrouter:voyageai/voyage-4-large",
        "openai:text-embedding-3-small",
    )


@pytest.mark.asyncio
async def test_voyage_failure_falls_back_to_openrouter_with_distinct_identity(monkeypatch):
    fallback_embedding = [0.65] * 1024
    openrouter_response = {
        "data": [{"index": 0, "embedding": fallback_embedding}],
        "usage": {"total_tokens": 11},
    }
    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="openrouter",
        embedding_openrouter_document_model="voyageai/voyage-4-large",
        embedding_openrouter_query_model="voyageai/voyage-4-lite",
        openrouter_api_key="openrouter-key",
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
            "orchestrator.memory.embedding._post_openrouter_embeddings",
            new_callable=AsyncMock,
            return_value=openrouter_response,
        ) as openrouter_post:
            document_result = await embed_documents_with_metadata(["fallback document"])
            query_result = await embed_query_with_metadata("fallback query")

    assert document_result.provider == "openrouter"
    assert document_result.storage_model == "openrouter:voyageai/voyage-4-large"
    assert query_result.provider == "openrouter"
    assert query_result.model == "openrouter:voyageai/voyage-4-lite"
    assert query_result.storage_model == "openrouter:voyageai/voyage-4-large"
    assert openrouter_post.await_count == 2
    assert openrouter_post.await_args_list[0].kwargs == {
        "api_key": "openrouter-key",
        "texts": ["fallback document"],
        "model": "voyageai/voyage-4-large",
        "input_type": "document",
        "output_dimension": 1024,
    }
    assert openrouter_post.await_args_list[1].kwargs["model"] == "voyageai/voyage-4-lite"
    assert openrouter_post.await_args_list[1].kwargs["input_type"] == "query"
    assert get_embedding_provider_used_counts()["openrouter"] == 2


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
            result = await embed_query_with_metadata("fallback query")

    assert result.embedding == fallback_embedding
    assert voyage_post.await_count == 1
    assert openai_post.await_count == 1
    assert openai_post.await_args is not None
    assert openai_post.await_args.kwargs["model"] == "text-embedding-3-small"
    assert openai_post.await_args.kwargs["output_dimension"] == 1024
    assert get_embedding_provider_used_counts()["openai"] == 1
    assert get_embedding_failures_total() == 1
    assert result.storage_model == "openai:text-embedding-3-small"


@pytest.mark.asyncio
async def test_openai_fallback_preserves_model_identity(monkeypatch):
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
    ):
        with patch(
            "orchestrator.memory.embedding._post_openai_embeddings",
            new_callable=AsyncMock,
            return_value=openai_response,
        ):
            document_result = await embed_documents_with_metadata(["fallback document"])
            query_result = await embed_query_with_metadata("fallback query")

    assert document_result.embeddings == [fallback_embedding]
    assert document_result.provider == "openai"
    assert document_result.storage_model == "openai:text-embedding-3-small"
    assert query_result.embedding == fallback_embedding
    assert query_result.provider == "openai"
    assert query_result.storage_model == "openai:text-embedding-3-small"


@pytest.mark.asyncio
async def test_legacy_embed_documents_rejects_fallback_vectors(monkeypatch):
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
    ):
        with patch(
            "orchestrator.memory.embedding._post_openai_embeddings",
            new_callable=AsyncMock,
            return_value=openai_response,
        ):
            with pytest.raises(EmbeddingRequestError, match="metadata"):
                await embed_documents(["legacy untagged document"])


@pytest.mark.asyncio
async def test_legacy_embed_query_rejects_fallback_vectors(monkeypatch):
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
    ):
        with patch(
            "orchestrator.memory.embedding._post_openai_embeddings",
            new_callable=AsyncMock,
            return_value=openai_response,
        ):
            with pytest.raises(EmbeddingRequestError, match="metadata"):
                await embed_query("legacy untagged query")


@pytest.mark.asyncio
async def test_embed_query_for_configured_storage_models_includes_openai_space(monkeypatch):
    voyage_embedding = [0.4] * 1024
    openai_embedding = [0.6] * 1024
    voyage_response = {
        "data": [{"index": 0, "embedding": voyage_embedding}],
        "usage": {"total_tokens": 8},
    }
    openai_response = {
        "data": [{"index": 0, "embedding": openai_embedding}],
        "usage": {"prompt_tokens": 8},
    }
    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="openai",
        embedding_openai_fallback_model="text-embedding-3-small",
        openai_api_key="openai-key",
    )

    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)
    monkeypatch.setattr("orchestrator.memory.embedding._get_voyage_api_key", lambda: "voyage-key")

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=voyage_response,
    ):
        with patch(
            "orchestrator.memory.embedding._post_openai_embeddings",
            new_callable=AsyncMock,
            return_value=openai_response,
        ):
            results = await embed_query_for_configured_storage_models("recall this")

    assert [result.storage_model for result in results] == [
        "voyage-4-large",
        "openai:text-embedding-3-small",
    ]
    assert results[0].embedding == voyage_embedding
    assert results[1].embedding == openai_embedding


@pytest.mark.asyncio
async def test_embed_query_for_configured_storage_models_includes_openrouter_space(monkeypatch):
    voyage_embedding = [0.4] * 1024
    openrouter_embedding = [0.55] * 1024
    voyage_response = {
        "data": [{"index": 0, "embedding": voyage_embedding}],
        "usage": {"total_tokens": 8},
    }
    openrouter_response = {
        "data": [{"index": 0, "embedding": openrouter_embedding}],
        "usage": {"total_tokens": 8},
    }
    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="openrouter",
        embedding_openrouter_document_model="voyageai/voyage-4-large",
        embedding_openrouter_query_model="voyageai/voyage-4-lite",
        openrouter_api_key="openrouter-key",
    )

    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)
    monkeypatch.setattr("orchestrator.memory.embedding._get_voyage_api_key", lambda: "voyage-key")

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=voyage_response,
    ):
        with patch(
            "orchestrator.memory.embedding._post_openrouter_embeddings",
            new_callable=AsyncMock,
            return_value=openrouter_response,
        ):
            results = await embed_query_for_configured_storage_models(
                "recall this",
                fallback_storage_models={"openrouter:voyageai/voyage-4-large"},
            )

    assert [result.storage_model for result in results] == [
        "voyage-4-large",
        "openrouter:voyageai/voyage-4-large",
    ]
    assert results[1].model == "openrouter:voyageai/voyage-4-lite"
    assert results[1].embedding == openrouter_embedding


@pytest.mark.asyncio
async def test_embed_query_for_configured_storage_models_respects_available_fallback_spaces(
    monkeypatch,
):
    voyage_embedding = [0.4] * 1024
    voyage_response = {
        "data": [{"index": 0, "embedding": voyage_embedding}],
        "usage": {"total_tokens": 8},
    }
    mock_settings = SimpleNamespace(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="openai",
        embedding_openai_fallback_model="text-embedding-3-small",
        openai_api_key="openai-key",
    )

    monkeypatch.setattr("orchestrator.memory.embedding.get_settings", lambda: mock_settings)
    monkeypatch.setattr("orchestrator.memory.embedding._get_voyage_api_key", lambda: "voyage-key")

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=voyage_response,
    ):
        with patch(
            "orchestrator.memory.embedding._post_openai_embeddings",
            new_callable=AsyncMock,
        ) as openai_post:
            results = await embed_query_for_configured_storage_models(
                "recall this",
                fallback_storage_models=set(),
            )

    assert [result.storage_model for result in results] == ["voyage-4-large"]
    openai_post.assert_not_awaited()


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
                assert (
                    await embed_query_with_metadata("fallback query")
                ).embedding == fallback_embedding

            voyage_post.reset_mock()
            assert (
                await embed_query_with_metadata("fallback query")
            ).embedding == fallback_embedding

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
async def test_openai_fallback_truncates_inputs_to_provider_limit(monkeypatch):
    fallback_embedding = [0.5] * 1024
    openai_response = {
        "data": [{"index": 0, "embedding": fallback_embedding}],
        "usage": {"prompt_tokens": 8000},
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
        ) as openai_post:
            result = await embed_documents_with_metadata(["x" * 80_000])

    assert result.embeddings == [fallback_embedding]
    assert openai_post.await_args is not None
    sent_text = openai_post.await_args.kwargs["texts"][0]
    assert len(sent_text) == 32_000


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
                        await embed_query_with_metadata("fallback query")

    result = await get_status(
        app_state=AppState(settings=Settings(daemon_environment="development")),
        auth=cast(AuthenticatedDevice, object()),
    )

    assert result["embedding_failures_total"] == 1
    assert result["embedding_provider_used"]["openai"] == 1
