"""Unit tests for memory_promote and memory_demote tools."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from orchestrator.tools.memory_promote import MemoryPromoteTool
from orchestrator.tools.memory_demote import MemoryDemoteTool


@pytest.mark.asyncio
async def test_promote_l1_to_l0():
    store = AsyncMock()
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = {
        "id": memory_id,
        "user_id": user_id,
        "tier": "l1",
        "content": "Test memory",
    }
    store.update_memory_tier.return_value = True

    tool = MemoryPromoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["previous_tier"] == "l1"
    assert parsed["new_tier"] == "l0"
    store.update_memory_tier.assert_called_once_with(memory_id, "l0")


@pytest.mark.asyncio
async def test_promote_l2_to_l0():
    store = AsyncMock()
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = {
        "id": memory_id,
        "user_id": user_id,
        "tier": "l2",
        "content": "Test memory",
    }
    store.update_memory_tier.return_value = True

    tool = MemoryPromoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["previous_tier"] == "l2"
    assert parsed["new_tier"] == "l0"


@pytest.mark.asyncio
async def test_promote_already_l0():
    store = AsyncMock()
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = {
        "id": memory_id,
        "user_id": user_id,
        "tier": "l0",
        "content": "Test memory",
    }

    tool = MemoryPromoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["tier"] == "l0"
    assert "already at L0" in parsed["message"]
    store.update_memory_tier.assert_not_called()


@pytest.mark.asyncio
async def test_promote_memory_not_found():
    store = AsyncMock()
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = None

    tool = MemoryPromoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert "error" in parsed
    assert "not found" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_promote_invalid_memory_id():
    store = AsyncMock()
    user_id = uuid.uuid4()

    tool = MemoryPromoteTool(store, user_id)
    result = await tool.execute(memory_id="not-a-uuid")

    import json

    parsed = json.loads(result)
    assert "error" in parsed
    assert "invalid" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_demote_l0_to_l1():
    store = AsyncMock()
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = {
        "id": memory_id,
        "user_id": user_id,
        "tier": "l0",
        "content": "Test memory",
    }
    store.update_memory_tier.return_value = True

    tool = MemoryDemoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["previous_tier"] == "l0"
    assert parsed["new_tier"] == "l1"
    store.update_memory_tier.assert_called_once_with(memory_id, "l1")


@pytest.mark.asyncio
async def test_demote_not_at_l0():
    store = AsyncMock()
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = {
        "id": memory_id,
        "user_id": user_id,
        "tier": "l1",
        "content": "Test memory",
    }

    tool = MemoryDemoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert "not at L0" in parsed["message"]
    store.update_memory_tier.assert_not_called()


@pytest.mark.asyncio
async def test_demote_memory_not_found():
    store = AsyncMock()
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = None

    tool = MemoryDemoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert "error" in parsed
    assert "not found" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_demote_invalid_memory_id():
    store = AsyncMock()
    user_id = uuid.uuid4()

    tool = MemoryDemoteTool(store, user_id)
    result = await tool.execute(memory_id="not-a-uuid")

    import json

    parsed = json.loads(result)
    assert "error" in parsed
    assert "invalid" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_demote_wrong_user():
    store = AsyncMock()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = {
        "id": memory_id,
        "user_id": other_user_id,
        "tier": "l0",
        "content": "Test memory",
    }

    tool = MemoryDemoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert "error" in parsed
    assert "not found" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_promote_wrong_user():
    store = AsyncMock()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    memory_id = uuid.uuid4()

    store.get_memory.return_value = {
        "id": memory_id,
        "user_id": other_user_id,
        "tier": "l1",
        "content": "Test memory",
    }

    tool = MemoryPromoteTool(store, user_id)
    result = await tool.execute(memory_id=str(memory_id))

    import json

    parsed = json.loads(result)
    assert "error" in parsed
    assert "not found" in parsed["error"].lower()
