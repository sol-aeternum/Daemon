"""Unit test for deduplicate_facts slot fallback behavior.

Tests that when an unslotted memory exists and a slotted fact with similar
content is provided, the slotted fact gets merged with the existing memory.
"""

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.dedup import deduplicate_facts


@dataclass
class SimpleFact:
    """Simple fact dataclass for testing."""

    content: str
    category: str
    confidence: float = 0.8
    slot: str | None = None


class TestDedupSlotFallback:
    """Test slot fallback in deduplicate_facts."""

    @pytest.mark.asyncio
    async def test_slotted_fact_merges_with_existing_unslotted_memory(self):
        """When unslotted memory exists, slotted fact with similar content merges."""
        # Setup user ID
        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Create mock memory store
        mock_store = AsyncMock()

        # Mock search_memories to return existing unslotted memory
        mock_store.search_memories = AsyncMock(
            return_value=[
                {
                    "id": str(uuid.uuid4()),
                    "content": "User's name is Julian",
                    "category": "fact",
                    "memory_slot": None,  # Unslotted memory
                    "valid_to": None,  # Active
                    "similarity": 0.92,  # High similarity
                }
            ]
        )

        # Mock touch_memory (called when merging)
        mock_store.touch_memory = AsyncMock()

        # Create the slotted fact
        fact = SimpleFact(
            content="User's name is Julian", category="fact", slot="personal.name"
        )

        # Mock embed_text at the module level where it's imported
        mock_embedding = [0.1] * 1536  # Mock 1536-dim embedding

        with patch(
            "orchestrator.memory.dedup.embed_text",
            new_callable=AsyncMock,
            return_value=mock_embedding,
        ):
            # Call deduplicate_facts
            result = await deduplicate_facts(
                store=mock_store,
                user_id=user_id,
                facts=[fact],
                conversation_id=conversation_id,
            )

            # Assert: should merge with existing unslotted memory
            assert len(result.merged) == 1, (
                f"Expected 1 merged, got {len(result.merged)}"
            )
            assert len(result.new) == 0, f"Expected 0 new, got {len(result.new)}"
            assert len(result.superseded) == 0

    @pytest.mark.asyncio
    async def test_slotted_fact_creates_new_when_no_match(self):
        """When no similar memory exists, slotted fact creates new memory."""
        # Setup user ID
        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Create mock memory store
        mock_store = AsyncMock()

        # Mock search_memories to return empty (no similar memories)
        mock_store.search_memories = AsyncMock(return_value=[])

        # Mock insert_memory (for new memory creation)
        new_memory_id = uuid.uuid4()
        mock_store.insert_memory = AsyncMock(
            return_value={
                "id": str(new_memory_id),
                "content": "User's name is Julian",
                "category": "fact",
                "memory_slot": "personal.name",
            }
        )

        # Create the slotted fact
        fact = SimpleFact(
            content="User's name is Julian", category="fact", slot="personal.name"
        )

        # Mock embed_text at the module level where it's imported
        mock_embedding = [0.1] * 1536  # Mock 1536-dim embedding

        with patch(
            "orchestrator.memory.dedup.embed_text",
            new_callable=AsyncMock,
            return_value=mock_embedding,
        ):
            # Call deduplicate_facts
            result = await deduplicate_facts(
                store=mock_store,
                user_id=user_id,
                facts=[fact],
                conversation_id=conversation_id,
            )

            # Assert: should create new memory (no match)
            assert len(result.new) == 1, f"Expected 1 new, got {len(result.new)}"
            assert len(result.merged) == 0
            assert len(result.superseded) == 0
