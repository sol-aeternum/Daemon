"""Tests for entity pipeline wiring - extraction to entity resolution."""

from __future__ import annotations

import json
import uuid
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from orchestrator.memory.entity_projection import ProjectionInputs, ProjectionSnapshot
from orchestrator.services.account_control.guard import CapturedGeneration


def _synthetic_inputs(captured: CapturedGeneration) -> ProjectionInputs:
    """Queued-plan input shape without touching the real guarded snapshots."""
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
        mock_store.log_extraction = AsyncMock()

        with patch("orchestrator.memory.extraction.guarded_completion") as mock_llm:
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
    async def test_job_refuses_missing_origin_capture(self) -> None:
        """Legacy callers without origin kwargs are refused before any store read."""
        from orchestrator.memory.store import MemoryStore
        from orchestrator.worker.jobs import resolve_entities_job

        mock_store = MagicMock(spec=MemoryStore)
        mock_store.read_entity_projection_inputs = AsyncMock()
        ctx = cast(dict[str, object], {"store": mock_store})
        result = await resolve_entities_job(
            ctx,
            user_id=str(uuid.uuid4()),
            memory_ids_json=json.dumps([str(uuid.uuid4())]),
        )
        assert result["status"] == "skipped"
        assert result["errors"] == ["invalid_capture"]
        assert result["error_count"] == 0
        # Refusal precedes reads: no projection input lookup is attempted.
        mock_store.read_entity_projection_inputs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_returns_skipped_when_store_unavailable(self) -> None:
        """A valid payload keeps the old store-unavailable result shape."""
        from orchestrator.worker.jobs import resolve_entities_job

        user_id = uuid.uuid4()
        ctx: dict[str, object] = {}
        result = await resolve_entities_job(
            ctx,
            user_id=str(user_id),
            memory_ids_json=json.dumps([str(uuid.uuid4())]),
            origin_account_id=str(user_id),
            origin_account_generation=7,
        )
        assert result["status"] == "skipped"
        assert result["memories_processed"] == 0
        assert result["entities_created"] == 0
        assert result["entities_updated"] == 0
        assert result["errors"] == ["store_unavailable"]
        assert result["error_count"] == 1

    @pytest.mark.asyncio
    async def test_job_skips_malformed_ids_json(self) -> None:
        """Valid origin capture still refuses malformed memory_ids_json."""
        from orchestrator.memory.store import MemoryStore
        from orchestrator.worker.jobs import resolve_entities_job

        user_id = uuid.uuid4()
        mock_store = MagicMock(spec=MemoryStore)
        mock_store.read_entity_projection_inputs = AsyncMock()
        ctx = cast(dict[str, object], {"store": mock_store})
        result = await resolve_entities_job(
            ctx,
            user_id=str(user_id),
            memory_ids_json="not-valid-json",
            origin_account_id=str(user_id),
            origin_account_generation=7,
        )
        assert result["status"] == "skipped"
        assert result["error_count"] == 0
        mock_store.read_entity_projection_inputs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_skips_non_uuid_list_entry(self) -> None:
        from orchestrator.memory.store import MemoryStore
        from orchestrator.worker.jobs import resolve_entities_job

        user_id = uuid.uuid4()
        mock_store = MagicMock(spec=MemoryStore)
        mock_store.read_entity_projection_inputs = AsyncMock()
        ctx = cast(dict[str, object], {"store": mock_store})
        result = await resolve_entities_job(
            ctx,
            user_id=str(user_id),
            memory_ids_json="['not-a-uuid']",
            origin_account_id=str(user_id),
            origin_account_generation=7,
        )
        assert result["status"] == "skipped"
        mock_store.read_entity_projection_inputs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_handles_empty_memory_ids(self) -> None:
        from orchestrator.memory.store import MemoryStore
        from orchestrator.worker.jobs import resolve_entities_job

        user_id = uuid.uuid4()
        mock_store = MagicMock(spec=MemoryStore)
        mock_store.read_entity_projection_inputs = AsyncMock(
            return_value=_synthetic_inputs(CapturedGeneration(user_id, 7))
        )
        ctx = cast(dict[str, object], {"store": mock_store})
        result = await resolve_entities_job(
            ctx,
            user_id=str(user_id),
            memory_ids_json="[]",
            origin_account_id=str(user_id),
            origin_account_generation=7,
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
        )
        from orchestrator.memory.entities import persist_extraction_result

        user_id = uuid.uuid4()
        captured = CapturedGeneration(user_id, 7)
        mock_store = MagicMock()
        new_entity_id = uuid.uuid4()
        mock_store.save_entity_projection = AsyncMock(return_value=[new_entity_id])
        memory_id = uuid.uuid4()

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

        entity_ids = await persist_extraction_result(
            user_id,
            mock_store,
            result,
            inputs=_synthetic_inputs(captured),
            captured_generation=captured,
        )

        # Single atomic writer seam: no individual insert/link store calls.
        mock_store.insert_entity.assert_not_called()
        mock_store.link_entity_to_memory.assert_not_called()
        mock_store.save_entity_projection.assert_awaited_once()
        save_args, save_kwargs = mock_store.save_entity_projection.await_args
        assert save_kwargs["captured_generation"] is captured
        mutations = save_args[1]
        assert len(mutations) == 1
        mutation = mutations[0]
        assert mutation.canonical_id is None
        assert mutation.canonical_name == "Alice"
        assert mutation.lookup_key == "alice"
        # Linking is carried by the mutation's source id inside the writer.
        assert mutation.source_id == memory_id
        assert mutation.alias is None
        assert len(entity_ids) == 1
        assert entity_ids[0] == new_entity_id

    @pytest.mark.asyncio
    async def test_adds_alias_to_merged_entity(self) -> None:
        from orchestrator.memory.entities import (
            CandidateMention,
            EntityResolution,
            ExtractionResult,
        )
        from orchestrator.memory.entities import persist_extraction_result

        user_id = uuid.uuid4()
        captured = CapturedGeneration(user_id, 7)
        mock_store = MagicMock()
        canonical_id = uuid.uuid4()
        memory_id = uuid.uuid4()

        mock_store.save_entity_projection = AsyncMock(return_value=[canonical_id])

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

        entity_ids = await persist_extraction_result(
            user_id,
            mock_store,
            result,
            inputs=_synthetic_inputs(captured),
            captured_generation=captured,
        )

        mock_store.save_entity_projection.assert_awaited_once()
        save_args, save_kwargs = mock_store.save_entity_projection.await_args
        assert save_kwargs["captured_generation"] is captured
        mutations = save_args[1]
        assert len(mutations) == 1
        mutation = mutations[0]
        assert mutation.canonical_id == canonical_id
        assert mutation.canonical_name == "Robert"
        assert mutation.lookup_key == "robert"
        # Alias and its normalized lookup key are passed through the mutation.
        assert mutation.alias == "Bob"
        assert mutation.alias_lookup_key == "bob"
        assert mutation.source_id == memory_id
        assert canonical_id in entity_ids

    @pytest.mark.asyncio
    async def test_grouped_merges_use_first_canonical_name_and_source(self) -> None:
        """Merged resolutions to one canonical entity consolidate into one
        mutation using the first resolution's canonical name/alias/source."""
        from orchestrator.memory.entities import (
            CandidateMention,
            EntityResolution,
            ExtractionResult,
        )
        from orchestrator.memory.entities import persist_extraction_result

        user_id = uuid.uuid4()
        captured = CapturedGeneration(user_id, 7)
        mock_store = MagicMock()
        canonical_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        other_memory_id = uuid.uuid4()
        mock_store.save_entity_projection = AsyncMock(return_value=[canonical_id])

        first = EntityResolution(
            mention=CandidateMention(
                text="Bobby",
                normalized_key="bobby",
                source_memory_id=memory_id,
            ),
            merge_decision="merge",
            resolved_entity_id=canonical_id,
            canonical_name="Robert",
            is_new=False,
        )
        second = EntityResolution(
            mention=CandidateMention(
                text="Bob",
                normalized_key="bob",
                source_memory_id=other_memory_id,
            ),
            merge_decision="merge",
            resolved_entity_id=canonical_id,
            canonical_name="Roberto",
            is_new=False,
        )

        result = ExtractionResult(
            candidates=[first.mention, second.mention],
            resolutions=[first, second],
            ambiguous_merges_needed=[],
            spacy_enriched=False,
        )

        entity_ids = await persist_extraction_result(
            user_id,
            mock_store,
            result,
            inputs=_synthetic_inputs(captured),
            captured_generation=captured,
        )

        mutations = mock_store.save_entity_projection.await_args.args[1]
        assert len(mutations) == 1
        mutation = mutations[0]
        assert mutation.canonical_name == "Robert"
        assert mutation.alias == "Bobby"
        assert mutation.source_id == memory_id
        assert len(entity_ids) == 1

    @pytest.mark.asyncio
    async def test_skips_rejected_merge_decisions(self) -> None:
        from orchestrator.memory.entities import (
            CandidateMention,
            EntityResolution,
            ExtractionResult,
        )
        from orchestrator.memory.entities import persist_extraction_result

        user_id = uuid.uuid4()
        captured = CapturedGeneration(user_id, 7)
        mock_store = MagicMock()
        mock_store.save_entity_projection = AsyncMock(return_value=[])

        mention = CandidateMention(
            text="Charlie",
            normalized_key="charlie",
            source_memory_id=uuid.uuid4(),
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

        entity_ids = await persist_extraction_result(
            user_id,
            mock_store,
            result,
            inputs=_synthetic_inputs(captured),
            captured_generation=captured,
        )

        # Rejects produce no mutations and the atomic writer stores nothing.
        mock_store.save_entity_projection.assert_awaited_once()
        assert mock_store.save_entity_projection.await_args.args[1] == []
        assert entity_ids == []


class TestResolveEntitiesJobWiring:
    """Tests for entity resolution job wiring in extraction flow."""

    @pytest.mark.asyncio
    async def test_job_processes_memories_and_creates_entities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from orchestrator.memory.entities import (
            CandidateMention,
            EntityResolution,
            ExtractionResult,
        )
        from orchestrator.memory.store import MemoryStore
        from orchestrator.worker.jobs import resolve_entities_job
        from tests.qualified_compute import install_qualified_compute

        install_qualified_compute(monkeypatch)

        user_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        captured = CapturedGeneration(user_id, 7)

        mock_store = MagicMock(spec=MemoryStore)
        inputs = _synthetic_inputs(captured)
        inputs = ProjectionInputs(
            snapshot=inputs.snapshot,
            memory_contents=(("I met Alice from the project team", memory_id, "person.name"),),
            canonical_names=(),
        )
        mock_store.read_entity_projection_inputs = AsyncMock(return_value=inputs)
        mock_store.check_entity_projection_inputs = AsyncMock()
        mock_store.save_entity_projection = AsyncMock(return_value=[entity_id])
        ctx = cast(dict[str, object], {"store": mock_store, "db_pool": object()})

        planned = ExtractionResult(
            candidates=[
                CandidateMention(
                    text="Alice",
                    normalized_key="alice",
                    source_memory_id=memory_id,
                )
            ],
            resolutions=[
                EntityResolution(
                    mention=CandidateMention(
                        text="Alice",
                        normalized_key="alice",
                        source_memory_id=memory_id,
                    ),
                    merge_decision="new",
                    is_new=True,
                )
            ],
            ambiguous_merges_needed=[],
            spacy_enriched=False,
        )

        with patch(
            "orchestrator.worker.jobs.plan_entity_projection", new_callable=AsyncMock
        ) as plan:
            plan.return_value = planned
            result = await resolve_entities_job(
                ctx,
                user_id=str(user_id),
                memory_ids_json=json.dumps([str(memory_id)]),
                origin_account_id=str(user_id),
                origin_account_generation=7,
            )

        assert result["status"] == "ok"
        assert result["memories_processed"] == 1
        assert result["entities_created"] == 1

        # Guarded input pipeline: read -> check -> plan -> persist.
        mock_store.read_entity_projection_inputs.assert_awaited_once()
        mock_store.check_entity_projection_inputs.assert_awaited_once()
        assert (
            mock_store.check_entity_projection_inputs.await_args.kwargs["captured_generation"]
            == captured
        )
        plan.assert_awaited_once()
        # Single atomic writer with mandatory inputs and captured generation.
        persist_call = mock_store.save_entity_projection.await_args
        assert persist_call.args[0] == inputs
        mutations = persist_call.args[1]
        assert len(mutations) == 1
        assert mutations[0].canonical_id is None
        assert mutations[0].canonical_name == "Alice"
        assert mutations[0].lookup_key == "alice"
        assert mutations[0].source_id == memory_id
        assert persist_call.kwargs["captured_generation"] == captured


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
