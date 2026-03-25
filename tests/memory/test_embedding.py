import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orchestrator.memory.embedding import embed_documents, embed_query


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
        with patch(
            "orchestrator.memory.embedding.get_settings", return_value=mock_settings
        ):
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
        with patch(
            "orchestrator.memory.embedding.get_settings", return_value=mock_settings
        ):
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
