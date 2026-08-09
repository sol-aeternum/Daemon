"""Tests for entity pipeline wiring - extraction to entity resolution."""

from __future__ import annotations

import json
import uuid
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


class TestProcessExtractionReturnsTuple:
    """Tests for process_extraction returning new memory IDs tuple."""

    @pytest.mark.asyncio
    async def test_process_extraction_returns_tuple(self) -> None:
        from orchestrator.memory.extraction import process_extraction

        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        mock_store = MagicMock()
        mock_store.get_conversation = AsyncMock(return_value=None)
        mock_store.get_last_extraction_time = AsyncMock(return_value=None)
        mock_store.get_messages = AsyncMock(return_value=[])

        with patch("orchestrator.memory.extraction.litellm.acompletion") as mock_llm:
            mock_response = MagicMock()
            mock_response.model_dump = MagicMock(
                return_value={"choices": [{"message": {"content": '{"facts": []}'}}]}
            )
            mock_llm.return_value = mock_response

            text = "Hi"
            result = await process_extraction(
                store=mock_store,
                user_id=user_id,
                conversation_id=conversation_id,
                text=text,
            )

            assert isinstance(result, tuple)
            assert len(result) == 3
            success, new_memories, continuation_needed = result
            assert success is True
            assert new_memories == []
            assert continuation_needed is False


class TestEntityResolutionJobBestEffort:
    """Tests for entity resolution job best-effort behavior."""

    @pytest.mark.asyncio
    async def test_job_returns_skipped_when_no_store(self) -> None:
        from orchestrator.worker.jobs import resolve_entities_job

        ctx = {}
        result = await resolve_entities_job(
            ctx,
            user_id=str(uuid.uuid4()),
            memory_ids_json='["not-a-uuid"]',
        )
        assert result["status"] == "skipped"
        assert result["error_count"] == 1

    @pytest.mark.asyncio
    async def test_job_returns_error_for_invalid_json(self) -> None:
        from orchestrator.memory.store import MemoryStore
        from orchestrator.worker.jobs import resolve_entities_job

        mock_store = MagicMock(spec=MemoryStore)
        ctx = cast(dict[str, object], {"store": mock_store})
        result = await resolve_entities_job(
            ctx,
            user_id=str(uuid.uuid4()),
            memory_ids_json="not-valid-json",
        )
        assert result["status"] == "error"
        assert result["error_count"] == 1

    @pytest.mark.asyncio
    async def test_job_handles_empty_memory_ids(self) -> None:
        from orchestrator.memory.store import MemoryStore
        from orchestrator.worker.jobs import resolve_entities_job

        mock_store = MagicMock(spec=MemoryStore)
        ctx = cast(dict[str, object], {"store": mock_store})
        result = await resolve_entities_job(
            ctx,
            user_id=str(uuid.uuid4()),
            memory_ids_json="[]",
        )
        assert result["status"] == "ok"
        assert result["memories_processed"] == 0


class TestPersistExtractionResult:
    """Tests for persist_extraction_result entity creation and alias linking."""

    @pytest.mark.asyncio
    async def test_creates_new_entity_for_new_merge_decision(self) -> None:
        from orchestrator.memory.entities import (
            CandidateMention,
            EntityResolution,
            ExtractionResult,
            persist_extraction_result,
        )

        user_id = uuid.uuid4()
        mock_store = MagicMock()
        memory_id = uuid.uuid4()
        new_entity_id = uuid.uuid4()
        mock_store.insert_entity = AsyncMock(return_value={"id": new_entity_id})
        mock_store.link_entity_to_memory = AsyncMock()

        mention = CandidateMention(
            text="Alice",
            normalized_key="alice",
            source_memory_id=memory_id,
            confidence=0.8,
        )
        resolution = EntityResolution(
            mention=mention,
            merge_decision="new",
            is_new=True,
        )

        result = ExtractionResult(
            candidates=[mention],
            resolutions=[resolution],
            ambiguous_merges_needed=[],
            spacy_enriched=False,
        )

        entity_ids = await persist_extraction_result(user_id, mock_store, result)

        mock_store.insert_entity.assert_called_once()
        call_kwargs = mock_store.insert_entity.call_args.kwargs
        assert call_kwargs["canonical_name"] == "Alice"
        assert len(entity_ids) == 1
        # Bug 3 fix: link_entity_to_memory must be called for new entities
        mock_store.link_entity_to_memory.assert_called_once_with(new_entity_id, memory_id)

    @pytest.mark.asyncio
    async def test_adds_alias_to_merged_entity(self) -> None:
        from orchestrator.memory.entities import (
            CandidateMention,
            EntityResolution,
            ExtractionResult,
            persist_extraction_result,
        )

        user_id = uuid.uuid4()
        mock_store = MagicMock()
        canonical_id = uuid.uuid4()
        memory_id = uuid.uuid4()

        mock_store.get_entity = AsyncMock(
            return_value={
                "id": canonical_id,
                "aliases": ["Bobby"],
                "alias_lookup_keys": ["bobby"],
            }
        )
        mock_store.update_entity_aliases = AsyncMock()
        mock_store.link_entity_to_memory = AsyncMock()

        mention = CandidateMention(
            text="Bob",
            normalized_key="bob",
            source_memory_id=memory_id,
            confidence=0.7,
        )
        resolution = EntityResolution(
            mention=mention,
            merge_decision="merge",
            resolved_entity_id=canonical_id,
            canonical_name="Robert",
            is_new=False,
        )

        result = ExtractionResult(
            candidates=[mention],
            resolutions=[resolution],
            ambiguous_merges_needed=[],
            spacy_enriched=False,
        )

        entity_ids = await persist_extraction_result(user_id, mock_store, result)

        mock_store.update_entity_aliases.assert_called_once()
        mock_store.link_entity_to_memory.assert_called_once()
        assert canonical_id in entity_ids

    @pytest.mark.asyncio
    async def test_skips_rejected_merge_decisions(self) -> None:
        from orchestrator.memory.entities import (
            CandidateMention,
            EntityResolution,
            ExtractionResult,
            persist_extraction_result,
        )

        user_id = uuid.uuid4()
        mock_store = MagicMock()
        memory_id = uuid.uuid4()
        mock_store.insert_entity = AsyncMock(return_value={"id": uuid.uuid4()})

        mention = CandidateMention(
            text="Charlie",
            normalized_key="charlie",
            source_memory_id=memory_id,
            confidence=0.6,
        )
        resolution = EntityResolution(
            mention=mention,
            merge_decision="reject",
            is_new=False,
        )

        result = ExtractionResult(
            candidates=[mention],
            resolutions=[resolution],
            ambiguous_merges_needed=[],
            spacy_enriched=False,
        )

        entity_ids = await persist_extraction_result(user_id, mock_store, result)

        # Rejected decisions should not create entities
        mock_store.insert_entity.assert_not_called()
        assert entity_ids == []


class TestResolveEntitiesJobWiring:
    """Tests for entity resolution job wiring in extraction flow."""

    @pytest.mark.asyncio
    async def test_job_processes_memories_and_creates_entities(self) -> None:
        from orchestrator.memory.store import MemoryStore
        from orchestrator.worker.jobs import resolve_entities_job

        user_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        entity_id = uuid.uuid4()

        mock_store = MagicMock(spec=MemoryStore)
        mock_store.get_memory = AsyncMock(
            return_value={
                "id": memory_id,
                "content": "I met Alice from the project team",
                "memory_slot": "person.name",
            }
        )
        mock_store.get_entities_for_user = AsyncMock(return_value=[])
        mock_store.insert_entity = AsyncMock(return_value={"id": entity_id})
        ctx = cast(dict[str, object], {"store": mock_store})

        result = await resolve_entities_job(
            ctx,
            user_id=str(user_id),
            memory_ids_json=json.dumps([str(memory_id)]),
        )

        assert result["status"] == "ok"
        assert result["memories_processed"] == 1
        assert result["entities_created"] >= 1


class TestEntityAliasPersistenceRegression:
    """Verify aliases stored as valid JSONB through encrypt-then-jsonEncode flow."""

    @pytest.mark.asyncio
    async def test_insert_entity_aliases_stored_as_jsonb_string(self) -> None:
        from orchestrator.memory.encryption import ContentEncryption
        from orchestrator.memory.store import MemoryStore

        user_id = uuid.uuid4()
        entity_id = uuid.uuid4()

        class MockEncryption(ContentEncryption):
            def encrypt(self, plaintext: str) -> str:
                return f"MOCK_TOKEN_{plaintext}"

            def decrypt(self, ciphertext: str) -> str:
                if ciphertext.startswith("MOCK_TOKEN_"):
                    return ciphertext[len("MOCK_TOKEN_") :]
                return ciphertext

        mock_pool = MagicMock()
        stored_aliases = json.dumps(f"MOCK_TOKEN_{json.dumps(['Bob', 'Bobby'])}")
        stored_row = {
            "id": entity_id,
            "user_id": user_id,
            "canonical_name": "MOCK_TOKEN_Alice",
            "lookup_key": "alice",
            "aliases": stored_aliases,
            "alias_lookup_keys": ["bob", "bobby"],
            "source_memory_id": None,
            "created_at": None,
            "updated_at": None,
        }
        mock_pool.fetchrow = AsyncMock(return_value=stored_row)

        store = MemoryStore(mock_pool, MockEncryption(Fernet.generate_key().decode()))

        result = await store.insert_entity(
            user_id=user_id,
            canonical_name="Alice",
            lookup_key="alice",
            aliases=["Bob", "Bobby"],
        )

        assert mock_pool.fetchrow.called
        assert result["aliases"] == ["Bob", "Bobby"]

    @pytest.mark.asyncio
    async def test_update_entity_aliases_stored_as_jsonb_string(self) -> None:
        from orchestrator.memory.encryption import ContentEncryption
        from orchestrator.memory.store import MemoryStore

        entity_id = uuid.uuid4()

        class MockEncryption(ContentEncryption):
            def encrypt(self, plaintext: str) -> str:
                return f"MOCK_TOKEN_{plaintext}"

            def decrypt(self, ciphertext: str) -> str:
                if ciphertext.startswith("MOCK_TOKEN_"):
                    return ciphertext[len("MOCK_TOKEN_") :]
                return ciphertext

        mock_pool = MagicMock()
        stored_aliases = json.dumps(f"MOCK_TOKEN_{json.dumps(['Bob', 'Bobby', 'Bobby2'])}")
        stored_row = {
            "id": entity_id,
            "user_id": uuid.uuid4(),
            "canonical_name": "MOCK_TOKEN_Alice",
            "lookup_key": "alice",
            "aliases": stored_aliases,
            "alias_lookup_keys": ["bob", "bobby", "bobby2"],
            "source_memory_id": None,
            "created_at": None,
            "updated_at": None,
        }
        mock_pool.fetchrow = AsyncMock(return_value=stored_row)

        store = MemoryStore(mock_pool, MockEncryption(Fernet.generate_key().decode()))

        result = await store.update_entity_aliases(
            entity_id=entity_id,
            aliases=["Bob", "Bobby", "Bobby2"],
            alias_lookup_keys=["bob", "bobby", "bobby2"],
        )

        assert mock_pool.fetchrow.called
        assert result is not None
        assert result["aliases"] == ["Bob", "Bobby", "Bobby2"]
