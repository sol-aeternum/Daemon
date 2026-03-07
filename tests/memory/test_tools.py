"""Tests for memory tools."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch



@pytest.mark.asyncio
async def test_memory_tools_import():
    with patch("orchestrator.memory.dedup.dedup_and_store", new_callable=AsyncMock):
        mock_store = AsyncMock()
        from orchestrator.memory.tools import MemoryReadTool, MemoryWriteTool
        user_id = uuid.uuid4()
        tool = MemoryReadTool(mock_store, user_id)
        assert tool.name == "memory_read"


@pytest.mark.asyncio
async def test_memory_read_semantic_mode_passes_memory_slot():
    """Test that semantic mode passes memory_slot to search_memories."""
    with patch(
        "orchestrator.memory.tools.embed_text", new_callable=AsyncMock
    ) as mock_embed:
        mock_embed.return_value = [0.1, 0.2, 0.3]

        mock_store = AsyncMock()
        mock_store.search_memories = AsyncMock()
        mock_store.search_memories.return_value = []

        user_id = uuid.uuid4()
        tool = MemoryReadTool(mock_store, user_id)

        # Execute with slot parameter
        await tool.execute(query="test query", mode="semantic", slot="test_slot")

        # Verify search_memories was called with memory_slot parameter
        mock_store.search_memories.assert_called_once()
        call_args = mock_store.search_memories.call_args
        assert call_args[1]["memory_slot"] == "test_slot"


@pytest.mark.asyncio
async def test_memory_read_temporal_mode_calls_list_memories_with_confirmed_true():
    """Test that temporal mode calls list_memories with confirmed=True (not status="active")."""
    mock_store = AsyncMock()
    mock_store.list_memories = AsyncMock()
    mock_store.list_memories.return_value = []

    user_id = uuid.uuid4()
    tool = MemoryReadTool(mock_store, user_id)

    await tool.execute(mode="temporal", after="2023-01-01T00:00:00Z", before="2023-12-31T23:59:59Z")
    # Verify list_memories was called with confirmed=True
    mock_store.list_memories.assert_called_once()
    call_args = mock_store.list_memories.call_args
    assert call_args[1]["confirmed"] is True
    assert "status" not in call_args[1] or call_args[1]["status"] is None


@pytest.mark.asyncio
async def test_memory_read_temporal_mode_with_history_calls_list_memories_with_confirmed_none():
    """Test that temporal mode with history=True calls list_memories with confirmed=None."""
    mock_store = AsyncMock()
    mock_store.list_memories = AsyncMock()
    mock_store.list_memories.return_value = []

    user_id = uuid.uuid4()
    tool = MemoryReadTool(mock_store, user_id)

    await tool.execute(mode="temporal", history=True, after="2023-01-01T00:00:00Z", before="2023-12-31T23:59:59Z")
    # Verify list_memories was called with confirmed=None
    mock_store.list_memories.assert_called_once()
    call_args = mock_store.list_memories.call_args
    assert call_args[1]["confirmed"] is None


@pytest.mark.asyncio
async def test_memory_read_history_mode_excludes_deleted_memories():
    """Test that history mode excludes deleted memories from output."""
    mock_store = AsyncMock()
    mock_store.list_memories = AsyncMock()
    # Return memories with different statuses including deleted
    mock_store.list_memories.return_value = [
        {"content": "active memory", "status": "active", "category": "fact", "valid_from": None, "valid_to": None},
        {"content": "deleted memory", "status": "deleted", "category": "fact", "valid_from": None, "valid_to": None},
        {"content": "closed memory", "status": "closed", "category": "fact", "valid_from": None, "valid_to": None},
    ]

    user_id = uuid.uuid4()
    tool = MemoryReadTool(mock_store, user_id)

    result = await tool.execute(mode="temporal", history=True, after="2023-01-01T00:00:00Z", before="2023-12-31T23:59:59Z")
    # Verify deleted memory is not in the result
    assert "[FACT] [None -> None] active memory" in result
    assert "[FACT] [None -> None] closed memory" in result
    assert "[FACT] [None -> None] deleted memory" not in result


@pytest.mark.asyncio
async def test_memory_read_temporal_mode_slot_post_filter_with_increased_limit():
    """Test slot post-filter in temporal mode with effective_limit = limit * 4."""
    mock_store = AsyncMock()
    mock_store.list_memories = AsyncMock()
    # Return memories with different slots
    mock_store.list_memories.return_value = [
        {"content": "memory 1", "memory_slot": "slot_a", "category": "fact", "valid_from": None, "valid_to": None},
        {"content": "memory 2", "memory_slot": "slot_b", "category": "fact", "valid_from": None, "valid_to": None},
        {"content": "memory 3", "memory_slot": "slot_a", "category": "fact", "valid_from": None, "valid_to": None},
        {"content": "memory 4", "memory_slot": "slot_c", "category": "fact", "valid_from": None, "valid_to": None},
    ]

    user_id = uuid.uuid4()
    tool = MemoryReadTool(mock_store, user_id)
    limit = 2
    result = await tool.execute(mode="temporal", slot="slot_a", limit=limit, after="2023-01-01T00:00:00Z", before="2023-12-31T23:59:59Z")
    # Verify list_memories was called with increased limit (limit * 4)
    mock_store.list_memories.assert_called_once()
    call_args = mock_store.list_memories.call_args
    assert call_args[1]["limit"] == limit * 4

    # Verify only memories with matching slot are in the result
    assert "[FACT] [None -> None] memory 1" in result
    assert "[FACT] [None -> None] memory 3" in result
    assert "[FACT] [None -> None] memory 2" not in result
    assert "[FACT] [None -> None] memory 4" not in result



@pytest.mark.asyncio
async def test_memory_write_create_passes_slot_to_dedup_and_store():
    """Test that create action passes slot parameter to dedup_and_store."""
    with patch(
        "orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock
    ) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()

        mock_store = AsyncMock()
        user_id = uuid.uuid4()
        tool = MemoryWriteTool(mock_store, user_id)

        await tool.execute(action="create", content="test content", slot="test_slot")

        mock_dedup.assert_called_once()
        call_kwargs = mock_dedup.call_args[1]
        assert call_kwargs["slot"] == "test_slot"


@pytest.mark.asyncio
async def test_memory_write_update_calls_close_then_dedup():
    """Test that update action calls close_memory before dedup_and_store."""
    with patch(
        "orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock
    ) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()

        mock_store = AsyncMock()
        existing_memory_id = uuid.uuid4()
        mock_store.get_memory = AsyncMock()
        mock_store.get_memory.return_value = {
            "id": existing_memory_id,
            "content": "old content",
            "category": "fact",
            "source_type": "user_created",
            "conversation_id": None,
            "memory_slot": None,
        }

        user_id = uuid.uuid4()
        tool = MemoryWriteTool(mock_store, user_id)

        await tool.execute(action="update", memory_id=str(existing_memory_id), content="new content")

        # Verify close_memory was called
        mock_store.close_memory.assert_called_once()
        # Verify dedup_and_store was called after close
        mock_dedup.assert_called_once()


@pytest.mark.asyncio
async def test_memory_write_update_inherits_category_and_slot():
    """Test that update action inherits category and slot from old memory when not provided."""
    with patch(
        "orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock
    ) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()

        mock_store = AsyncMock()
        existing_memory_id = uuid.uuid4()
        mock_store.get_memory = AsyncMock()
        mock_store.get_memory.return_value = {
            "id": existing_memory_id,
            "content": "old content",
            "category": "preference",
            "source_type": "user_created",
            "conversation_id": None,
            "memory_slot": "inherited_slot",
        }

        user_id = uuid.uuid4()
        tool = MemoryWriteTool(mock_store, user_id)

        await tool.execute(action="update", memory_id=str(existing_memory_id), content="new content")

        mock_dedup.assert_called_once()
        call_kwargs = mock_dedup.call_args[1]
        assert call_kwargs["category"] == "preference"
        assert call_kwargs["slot"] == "inherited_slot"


@pytest.mark.asyncio
async def test_memory_write_update_missing_memory_id_returns_error():
    """Test that update action returns error when memory_id is missing."""
    mock_store = AsyncMock()
    user_id = uuid.uuid4()
    tool = MemoryWriteTool(mock_store, user_id)

    result = await tool.execute(action="update", content="new content")

    assert result == "memory_id is required for update"


@pytest.mark.asyncio
async def test_memory_write_update_invalid_memory_id_returns_error():
    """Test that update action returns error for invalid memory_id format."""
    mock_store = AsyncMock()
    user_id = uuid.uuid4()
    tool = MemoryWriteTool(mock_store, user_id)

    result = await tool.execute(action="update", memory_id="not-a-valid-uuid", content="new content")

    assert result == "Invalid memory_id format"


@pytest.mark.asyncio
async def test_memory_write_delete_calls_delete_memory_with_soft_true():
    """Test that delete action calls delete_memory with soft=True."""
    mock_store = AsyncMock()
    existing_memory_id = uuid.uuid4()
    mock_store.get_memory = AsyncMock()
    mock_store.get_memory.return_value = {
        "id": existing_memory_id,
        "content": "to delete",
        "category": "fact",
    }

    user_id = uuid.uuid4()
    tool = MemoryWriteTool(mock_store, user_id)

    result = await tool.execute(action="delete", memory_id=str(existing_memory_id))

    mock_store.delete_memory.assert_called_once_with(existing_memory_id, soft=True)
    assert str(existing_memory_id) in result


@pytest.mark.asyncio
async def test_memory_write_invalid_category_returns_error():
    """Test that create action returns error for invalid category."""
    mock_store = AsyncMock()
    user_id = uuid.uuid4()
    tool = MemoryWriteTool(mock_store, user_id)

    result = await tool.execute(action="create", content="test content", category="invalid_category")

    assert "Invalid category 'invalid_category'" in result
    assert "Use one of:" in result
