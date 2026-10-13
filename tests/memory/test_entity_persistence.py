"""Regression tests for entity persistence and entity-aware retrieval expansion."""

import uuid
import pytest
from unittest.mock import AsyncMock

from orchestrator.memory.entity_projection import ProjectionInputs, ProjectionSnapshot
from orchestrator.memory.entities import (
    CandidateMention,
    EntityResolution,
    ExtractionResult,
    persist_extraction_result,
)
from orchestrator.memory.retrieval import _get_entity_expanded_candidates
from orchestrator.services.account_control.guard import CapturedGeneration


def _projection_inputs(captured: CapturedGeneration) -> ProjectionInputs:
    """Minimal guarded-plan input carrying only the origin capture."""
    return ProjectionInputs(
        snapshot=ProjectionSnapshot(
            origin=captured,
            source_ids=(),
            memories=(),
            conversations=(),
            canonicals=(),
        ),
        memory_contents=(),
        canonical_names=(),
    )


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

        capture = CapturedGeneration(user_id, 7)
        mock_store.save_entity_projection = AsyncMock(return_value=[uuid.uuid4(), uuid.uuid4()])

        entity_ids = await persist_extraction_result(
            user_id,
            mock_store,
            extraction_result,
            inputs=_projection_inputs(capture),
            captured_generation=capture,
        )

        # BUG: before fix, both Alice and Bob went into one bucket (None) and only
        # ONE entity was created, with Bob as an alias of Alice.
        # After fix: each new resolution gets its own independent mutation in a
        # single atomic writer call, never merged via shared alias updates.
        mock_store.save_entity_projection.assert_awaited_once()
        save_args, save_kwargs = mock_store.save_entity_projection.await_args
        assert save_kwargs["captured_generation"] is capture
        mutations = save_args[1]
        assert len(mutations) == 2, (
            f"Expected 2 mutations (one per unrelated 'new' mention), got {len(mutations)}. "
            "Unrelated entities were likely collapsed into one bucket."
        )
        by_name = {m.canonical_name: m for m in mutations}
        assert set(by_name) == {"Alice", "Bob"}
        alice_mutation = by_name["Alice"]
        bob_mutation = by_name["Bob"]
        assert alice_mutation.canonical_id is None
        assert alice_mutation.lookup_key == "alice"
        assert alice_mutation.source_id == memory_id_alice
        assert bob_mutation.canonical_id is None
        assert bob_mutation.lookup_key == "bob"
        assert bob_mutation.source_id == memory_id_bob
        assert alice_mutation.source_id != bob_mutation.source_id
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
        assert other_memory_id in returned_ids, f"Active memory {other_memory_id} was not returned"

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
    async def test_new_entity_links_memory_inside_atomic_writer(self):
        # Bug 3 regression: the new mention's source memory rides inside the
        # single atomic writer mutation so linking cannot be lost on retry.
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

        capture = CapturedGeneration(user_id, 7)
        new_entity_id = uuid.uuid4()
        mock_store.save_entity_projection = AsyncMock(return_value=[new_entity_id])

        entity_ids = await persist_extraction_result(
            user_id,
            mock_store,
            extraction_result,
            inputs=_projection_inputs(capture),
            captured_generation=capture,
        )

        mock_store.save_entity_projection.assert_awaited_once()
        save_args, save_kwargs = mock_store.save_entity_projection.await_args
        assert save_kwargs["captured_generation"] is capture
        mutations = save_args[1]
        assert len(mutations) == 1
        mutation = mutations[0]
        assert mutation.canonical_id is None
        assert mutation.source_id == memory_id
        # No per-entity link helper remains in the persist path.
        mock_store.link_entity_to_memory.assert_not_awaited()
        assert entity_ids == [new_entity_id]
