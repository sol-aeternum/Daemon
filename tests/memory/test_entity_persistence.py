"""Regression tests for entity persistence and entity-aware retrieval expansion."""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from orchestrator.memory.entities import (
    CandidateMention,
    EntityResolution,
    ExtractionResult,
    persist_extraction_result,
)
from orchestrator.memory.retrieval import _get_entity_expanded_candidates


class TestPersistExtractionNewEntities:
    """Test Bug 1 fix: unrelated new entities must not be collapsed together."""

    @pytest.mark.asyncio
    async def test_two_unrelated_new_entities_remain_separate(self):
        """Two EntityResolution with merge_decision='new' and different names
        must each get their own entity, not be merged as aliases."""
        user_id = uuid.uuid4()
        mock_store = AsyncMock()

        memory_id_alice = uuid.uuid4()
        memory_id_bob = uuid.uuid4()

        alice_mention = CandidateMention(
            text="Alice",
            normalized_key="alice",
            source_memory_id=memory_id_alice,
        )
        bob_mention = CandidateMention(
            text="Bob",
            normalized_key="bob",
            source_memory_id=memory_id_bob,
        )

        alice_resolution = EntityResolution(
            mention=alice_mention,
            resolved_entity_id=None,
            canonical_name=None,
            is_new=True,
            merge_decision="new",
            similarity=0.0,
            alias_added=False,
        )
        bob_resolution = EntityResolution(
            mention=bob_mention,
            resolved_entity_id=None,
            canonical_name=None,
            is_new=True,
            merge_decision="new",
            similarity=0.0,
            alias_added=False,
        )

        extraction_result = ExtractionResult(
            candidates=[alice_mention, bob_mention],
            resolutions=[alice_resolution, bob_resolution],
            ambiguous_merges_needed=[],
            spacy_enriched=False,
        )

        entity_counter = 0

        async def mock_insert_entity(
            user_id=None,
            canonical_name=None,
            lookup_key=None,
            aliases=None,
            alias_lookup_keys=None,
            source_memory_id=None,
        ):
            nonlocal entity_counter
            entity_counter += 1
            result = MagicMock()
            result.get = lambda key: uuid.uuid4() if key == "id" else None
            return {"id": uuid.uuid4(), "canonical_name": canonical_name}

        mock_store.insert_entity = mock_insert_entity

        entity_ids = await persist_extraction_result(
            user_id, mock_store, extraction_result
        )

        # BUG: before fix, both Alice and Bob went into one bucket (None) and only
        # ONE entity was created, with Bob as an alias of Alice.
        # After fix: each new resolution gets its own entity.
        assert entity_counter == 2, (
            f"Expected 2 entities (one per unrelated 'new' mention), got {entity_counter}. "
            "Unrelated entities were likely collapsed into one bucket."
        )
        assert len(entity_ids) == 2, f"Expected 2 entity IDs, got {len(entity_ids)}"


class TestEntityExpandedCandidatesEligibility:
    """Test Bug 2 fix: entity-aware retrieval expansion must honor retrieval contract."""

    @pytest.mark.asyncio
    async def test_deleted_memory_not_returned(self):
        """A memory with status='deleted' must be excluded from entity expansion."""
        user_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        other_memory_id = uuid.uuid4()

        mock_store = AsyncMock()

        mock_entity = {
            "id": entity_id,
            "canonical_name": "Alice",
            "lookup_key": "alice",
            "linked_memory_ids": [str(memory_id), str(other_memory_id)],
        }

        mock_store.find_entities_by_alias.return_value = [mock_entity]
        mock_store.get_entity_by_lookup_key.return_value = None

        async def mock_get_memory(mid):
            if mid == memory_id:
                return {
                    "id": memory_id,
                    "content": "Alice works at Acme",
                    "status": "deleted",
                    "valid_to": None,
                    "source_type": "fact",
                    "local_only": False,
                    "source_conversation_id": None,
                }
            else:
                return {
                    "id": other_memory_id,
                    "content": "Bob is Alice's colleague",
                    "status": "active",
                    "valid_to": None,
                    "source_type": "fact",
                    "local_only": False,
                    "source_conversation_id": None,
                }

        mock_store.get_memory = mock_get_memory

        result = await _get_entity_expanded_candidates(
            mock_store,
            user_id,
            "Alice",
            include_local=False,
            allowed_source_conversation_ids=None,
        )

        returned_ids = {m["id"] for m in result}
        assert memory_id not in returned_ids, (
            f"Deleted memory {memory_id} was returned but should have been excluded"
        )
        assert other_memory_id in returned_ids, (
            f"Active memory {other_memory_id} was not returned"
        )

    @pytest.mark.asyncio
    async def test_local_only_memory_excluded_when_include_local_false(self):
        """A local_only memory must be excluded when include_local=False."""
        user_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        other_memory_id = uuid.uuid4()

        mock_store = AsyncMock()

        mock_entity = {
            "id": entity_id,
            "canonical_name": "Alice",
            "lookup_key": "alice",
            "linked_memory_ids": [str(memory_id), str(other_memory_id)],
        }

        mock_store.find_entities_by_alias.return_value = [mock_entity]
        mock_store.get_entity_by_lookup_key.return_value = None

        async def mock_get_memory(mid):
            if mid == memory_id:
                return {
                    "id": memory_id,
                    "content": "Alice works at Acme",
                    "status": "active",
                    "valid_to": None,
                    "source_type": "fact",
                    "local_only": True,
                    "source_conversation_id": None,
                }
            else:
                return {
                    "id": other_memory_id,
                    "content": "Bob is Alice's colleague",
                    "status": "active",
                    "valid_to": None,
                    "source_type": "fact",
                    "local_only": False,
                    "source_conversation_id": None,
                }

        mock_store.get_memory = mock_get_memory

        result = await _get_entity_expanded_candidates(
            mock_store,
            user_id,
            "Alice",
            include_local=False,
            allowed_source_conversation_ids=None,
        )

        returned_ids = {m["id"] for m in result}
        assert memory_id not in returned_ids, (
            f"local_only memory {memory_id} was returned but include_local=False"
        )
        assert other_memory_id in returned_ids, (
            f"Non-local memory {other_memory_id} was not returned"
        )

    @pytest.mark.asyncio
    async def test_superseded_memory_excluded(self):
        """A memory with valid_to set (superseded) must be excluded."""
        user_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        memory_id = uuid.uuid4()

        mock_store = AsyncMock()

        mock_entity = {
            "id": entity_id,
            "canonical_name": "Alice",
            "lookup_key": "alice",
            "linked_memory_ids": [str(memory_id)],
        }

        mock_store.find_entities_by_alias.return_value = [mock_entity]
        mock_store.get_entity_by_lookup_key.return_value = None

        mock_store.get_memory.return_value = {
            "id": memory_id,
            "content": "Alice works at Acme",
            "status": "active",
            "valid_to": "2025-01-01T00:00:00Z",
            "source_type": "fact",
            "local_only": False,
            "source_conversation_id": None,
        }

        result = await _get_entity_expanded_candidates(
            mock_store,
            user_id,
            "Alice",
            include_local=False,
            allowed_source_conversation_ids=None,
        )

        assert memory_id not in {m["id"] for m in result}, (
            f"Superseded memory {memory_id} was returned but should have been excluded"
        )

    @pytest.mark.asyncio
    async def test_dream_memory_excluded(self):
        """A dream memory must be excluded from entity expansion."""
        user_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        memory_id = uuid.uuid4()

        mock_store = AsyncMock()

        mock_entity = {
            "id": entity_id,
            "canonical_name": "Alice",
            "lookup_key": "alice",
            "linked_memory_ids": [str(memory_id)],
        }

        mock_store.find_entities_by_alias.return_value = [mock_entity]
        mock_store.get_entity_by_lookup_key.return_value = None

        mock_store.get_memory.return_value = {
            "id": memory_id,
            "content": "Alice in a dream",
            "status": "active",
            "valid_to": None,
            "source_type": "dream",
            "local_only": False,
            "source_conversation_id": None,
        }

        result = await _get_entity_expanded_candidates(
            mock_store,
            user_id,
            "Alice",
            include_local=False,
            allowed_source_conversation_ids=None,
        )

        assert memory_id not in {m["id"] for m in result}, (
            f"Dream memory {memory_id} was returned but should have been excluded"
        )


class TestPersistExtractionLinksNewEntitiesForRetrieval:
    # Bug 3 regression: newly created entities must be linked to memories for retrieval

    @pytest.mark.asyncio
    async def test_new_entity_linked_to_memory_via_link_entity_to_memory(self):
        # Bug 3: persist_extraction_result must call link_entity_to_memory for new entities
        user_id = uuid.uuid4()
        mock_store = AsyncMock()

        memory_id = uuid.uuid4()
        mention = CandidateMention(
            text="Alice",
            normalized_key="alice",
            source_memory_id=memory_id,
        )
        resolution = EntityResolution(
            mention=mention,
            resolved_entity_id=None,
            canonical_name=None,
            is_new=True,
            merge_decision="new",
            similarity=0.0,
            alias_added=False,
        )
        extraction_result = ExtractionResult(
            candidates=[mention],
            resolutions=[resolution],
            ambiguous_merges_needed=[],
            spacy_enriched=False,
        )

        new_entity_id = uuid.uuid4()

        async def mock_insert_entity(
            user_id=None,
            canonical_name=None,
            lookup_key=None,
            aliases=None,
            alias_lookup_keys=None,
            source_memory_id=None,
        ):
            return {"id": new_entity_id, "canonical_name": canonical_name}

        mock_store.insert_entity = mock_insert_entity
        mock_store.link_entity_to_memory = AsyncMock(return_value=True)

        await persist_extraction_result(user_id, mock_store, extraction_result)

        mock_store.link_entity_to_memory.assert_called_once_with(
            new_entity_id, memory_id
        )
