"""Tests for embedding generation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from orchestrator.memory.embedding import embed_text


@pytest.mark.asyncio
async def test_embed_text():
    mock_embedding = [0.1] * 1536

    # Create mock client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=mock_embedding)]
    mock_client.embeddings.create = AsyncMock(return_value=mock_response)

    result = await embed_text("test query", client=mock_client)
    assert result == mock_embedding
    assert mock_client.embeddings.create.called
