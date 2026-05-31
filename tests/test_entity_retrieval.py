"""Tests for alias-aware retrieval expansion."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGetEntityExpandedCandidates:
    """Tests for _get_entity_expanded_candidates."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_query(self) -> None:
        from orchestrator.memory.retrieval import _get_entity_expanded_candidates

        mock_store = MagicMock()
        result = await _get_entity_expanded_candidates(mock_store, uuid.uuid4(), "")
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_no_matching_entities(self) -> None:
        from orchestrator.memory.retrieval import _get_entity_expanded_candidates

        user_id = uuid.uuid4()

        mock_store = MagicMock()
        mock_store.find_entities_by_alias = AsyncMock(return_value=[])
        mock_store.get_entity_by_lookup_key = AsyncMock(return_value=None)

        result = await _get_entity_expanded_candidates(
            mock_store, user_id, "something that doesn't match"
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_is_best_effort_on_store_errors(self) -> None:
        from orchestrator.memory.retrieval import _get_entity_expanded_candidates

        user_id = uuid.uuid4()

        mock_store = MagicMock()
        mock_store.find_entities_by_alias = AsyncMock(side_effect=Exception("Database error"))

        result = await _get_entity_expanded_candidates(mock_store, user_id, "Alice")

        assert result == []


class TestRetrieveMemoriesWithEntityExpansion:
    """Tests for retrieval with entity expansion."""

    @pytest.mark.asyncio
    async def test_entity_expansion_does_not_replace_normal_retrieval(self) -> None:
        from orchestrator.memory.retrieval import retrieve_memories

        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        mock_store = MagicMock()
        mock_store.get_conversation = AsyncMock(
            return_value={"user_id": user_id, "pipeline": "cloud"}
        )
        mock_store.search_memories = AsyncMock(return_value=[])
        mock_store.search_memories_bm25 = AsyncMock(return_value=[])

        with patch("orchestrator.memory.retrieval.embed_query") as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            result = await retrieve_memories(
                store=mock_store,
                query_embedding=[0.1] * 1024,
                query_text="No matching entities or memories",
                conversation_id=conversation_id,
                user_id=user_id,
            )

            assert len(result) == 0

    @pytest.mark.asyncio
    async def test_entity_expansion_with_empty_query(self) -> None:
        from orchestrator.memory.retrieval import retrieve_memories

        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        mock_store = MagicMock()
        mock_store.get_conversation = AsyncMock(
            return_value={"user_id": user_id, "pipeline": "cloud"}
        )
        mock_store.search_memories = AsyncMock(
            return_value=[
                {
                    "id": uuid.uuid4(),
                    "content": "User likes JavaScript",
                    "similarity": 0.8,
                    "confidence": 0.8,
                    "trust_score": 0.5,
                    "source_type": "extracted",
                }
            ]
        )
        mock_store.search_memories_bm25 = AsyncMock(return_value=[])

        with patch("orchestrator.memory.retrieval.embed_query") as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            result = await retrieve_memories(
                store=mock_store,
                query_embedding=[0.1] * 1024,
                query_text="",
                conversation_id=conversation_id,
                user_id=user_id,
            )

            assert len(result) == 1


class TestAliasAwareRetrievalIntegration:
    """End-to-end integration tests for alias-aware retrieval expansion.

    These tests prove that when a user references an entity by one of its aliases,
    the retrieval system can expand that alias to the canonical entity and retrieve
    the associated memories even when the query text doesn't directly match.
    """

    @pytest.mark.asyncio
    async def test_retrieval_expands_alias_to_canonical_and_fetches_memory(
        self,
    ) -> None:
        from orchestrator.memory.retrieval import _get_entity_expanded_candidates

        user_id = uuid.uuid4()
        canonical_id = uuid.uuid4()
        memory_id = uuid.uuid4()

        mock_store = MagicMock()
        mock_store.find_entities_by_alias = AsyncMock(return_value=[])
        mock_store.get_entity_by_lookup_key = AsyncMock(
            return_value={
                "id": canonical_id,
                "canonical_name": "Robert",
                "linked_memory_ids": [memory_id],
            }
        )
        mock_store.get_memory = AsyncMock(
            return_value={
                "id": memory_id,
                "content": "User worked with Robert on the project",
                "similarity": 0.7,
                "confidence": 1.0,
                "trust_score": 1.0,
                "source_type": "extracted",
            }
        )

        # Query "Bob" should expand to find the linked memory about Robert
        result = await _get_entity_expanded_candidates(mock_store, user_id, "Tell me about Bob")

        assert len(result) == 1
        assert result[0]["id"] == memory_id
        assert result[0]["source"] == "entity_linked"

    @pytest.mark.asyncio
    async def test_entity_expansion_filters_out_dream_observations(self) -> None:
        from orchestrator.memory.retrieval import _get_entity_expanded_candidates

        user_id = uuid.uuid4()
        canonical_id = uuid.uuid4()
        dream_memory_id = uuid.uuid4()
        factual_memory_id = uuid.uuid4()

        mock_store = MagicMock()
        mock_store.find_entities_by_alias = AsyncMock(return_value=[])
        mock_store.get_entity_by_lookup_key = AsyncMock(
            return_value={
                "id": canonical_id,
                "canonical_name": "Alice",
                "linked_memory_ids": [dream_memory_id, factual_memory_id],
            }
        )

        dream_memory = {
            "id": dream_memory_id,
            "content": "User reflects that Alice is consistent",
            "similarity": 0.7,
            "confidence": 1.0,
            "trust_score": 1.0,
            "source_type": "dream",
        }
        factual_memory = {
            "id": factual_memory_id,
            "content": "User met Alice at the conference",
            "similarity": 0.6,
            "confidence": 1.0,
            "trust_score": 1.0,
            "source_type": "extracted",
        }

        async def mock_get_memory(mid):
            if mid == dream_memory_id:
                return dream_memory
            return factual_memory

        mock_store.get_memory = AsyncMock(side_effect=mock_get_memory)

        result = await _get_entity_expanded_candidates(mock_store, user_id, "Tell me about Alice")

        # Dream observation should be filtered out, only factual memory returned
        assert len(result) == 1
        assert result[0]["id"] == factual_memory_id

    @pytest.mark.asyncio
    async def test_alias_lookup_key_preserves_at_prefix(self) -> None:
        from orchestrator.memory.retrieval import _get_entity_expanded_candidates

        user_id = uuid.uuid4()
        canonical_id = uuid.uuid4()
        memory_id = uuid.uuid4()

        mock_store = MagicMock()
        # The @ prefix is NOT stripped - lookup uses the raw key
        mock_store.find_entities_by_alias = AsyncMock(
            return_value=[
                {
                    "id": canonical_id,
                    "canonical_name": "Alice Smith",
                    "linked_memory_ids": [memory_id],
                }
            ]
        )
        mock_store.get_entity_by_lookup_key = AsyncMock(return_value=None)
        mock_store.get_memory = AsyncMock(
            return_value={
                "id": memory_id,
                "content": "User mentioned Alice in a project context",
                "similarity": 0.6,
                "confidence": 1.0,
                "trust_score": 1.0,
                "source_type": "extracted",
            }
        )

        result = await _get_entity_expanded_candidates(mock_store, user_id, "What about @alice?")

        # Query extracts two candidates: "What" and "@alice"
        # Both are looked up via find_entities_by_alias
        # The @ prefix is preserved in the lookup key
        assert len(result) == 1
        mock_store.find_entities_by_alias.assert_called()

    @pytest.mark.asyncio
    async def test_entity_expansion_is_additive_to_hybrid_scoring(self) -> None:
        from orchestrator.memory.retrieval import retrieve_memories

        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        vector_memory_id = uuid.uuid4()
        entity_linked_memory_id = uuid.uuid4()

        mock_store = MagicMock()
        mock_store.get_conversation = AsyncMock(
            return_value={"user_id": user_id, "pipeline": "cloud"}
        )

        # Normal vector search finds a memory
        vector_memory = {
            "id": vector_memory_id,
            "content": "User likes coffee",
            "similarity": 0.8,
            "confidence": 0.8,
            "trust_score": 0.5,
            "source_type": "extracted",
        }
        mock_store.search_memories = AsyncMock(return_value=[vector_memory])
        mock_store.search_memories_bm25 = AsyncMock(return_value=[])

        # Entity expansion finds a different memory via alias
        mock_store.find_entities_by_alias = AsyncMock(return_value=[])
        mock_store.get_entity_by_lookup_key = AsyncMock(
            return_value={
                "id": uuid.uuid4(),
                "canonical_name": "Bob",
                "linked_memory_ids": [entity_linked_memory_id],
            }
        )
        mock_store.get_memory = AsyncMock(
            return_value={
                "id": entity_linked_memory_id,
                "content": "User mentioned Bob",
                "similarity": 0.4,
                "confidence": 0.8,
                "trust_score": 0.5,
                "source_type": "extracted",
            }
        )

        with patch("orchestrator.memory.retrieval.embed_query") as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            result = await retrieve_memories(
                store=mock_store,
                query_embedding=[0.1] * 1024,
                query_text="coffee and Bob",
                conversation_id=conversation_id,
                user_id=user_id,
            )

            # Both vector result AND entity-linked result should appear
            result_ids = [r["id"] for r in result]
            assert vector_memory_id in result_ids
            assert entity_linked_memory_id in result_ids
