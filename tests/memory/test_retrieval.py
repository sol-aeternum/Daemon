"""Tests for memory retrieval."""

import pytest
from unittest.mock import AsyncMock
from orchestrator.memory.retrieval import retrieve_memories


@pytest.mark.asyncio
async def test_retrieve_memories_empty():
    mock_store = AsyncMock()
    mock_store.search_memories.return_value = []
    result = await retrieve_memories(mock_store, [0.1] * 1536)
    assert result == []
