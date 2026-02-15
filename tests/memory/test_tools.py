"""Tests for memory tools."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_memory_tools_import():
    with patch("orchestrator.memory.dedup.dedup_and_store", new_callable=AsyncMock):
        from orchestrator.memory.tools import MemoryReadTool, MemoryWriteTool

        mock_store = AsyncMock()
        user_id = uuid.uuid4()
        tool = MemoryReadTool(mock_store, user_id)
        assert tool.name == "memory_read"
