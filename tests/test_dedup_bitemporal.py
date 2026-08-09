from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.dedup import dedup_and_store, deduplicate_facts
from orchestrator.memory.embedding import EmbeddingBatchResult
from orchestrator.memory.extraction import ExtractedFact


def _new_fact(content: str, slot: str | None = None) -> ExtractedFact:
    return ExtractedFact(content=content, category="fact", confidence=0.9, slot=slot)


def _embedding_result(
    vector: list[float],
    storage_model: str = "voyage-4-large",
) -> EmbeddingBatchResult:
    return EmbeddingBatchResult(
        embeddings=[vector],
        provider="openai" if storage_model.startswith("openai:") else "voyage",
        model=storage_model,
        storage_model=storage_model,
    )


@pytest.fixture(autouse=True)
def _mock_contradiction_check():
    with patch(
        "orchestrator.memory.dedup.check_contradiction",
        new_callable=AsyncMock,
        return_value=(True, "bitemporal test contradiction"),
    ):
        yield


@pytest.mark.asyncio
async def test_dedup_slot_supersedes_with_similarity_threshold() -> None:
    store = AsyncMock()
    store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "similarity": 0.80,
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    store.supersede_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User drives a Tesla",
        "memory_slot": "vehicle",
        "valid_to": None,
    }

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.01, 0.02])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User drives a Tesla", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    store.supersede_memory.assert_awaited_once()
    assert store.supersede_memory.await_args.kwargs["memory_slot"] == "vehicle"


@pytest.mark.asyncio
async def test_dedup_slot_merges_when_similarity_is_high() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.95,
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.01, 0.02])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User drives a Tesla", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.merged) == 1
    store.touch_memory.assert_awaited_once_with(existing_id)
    store.insert_memory.assert_not_awaited()
    store.supersede_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_without_slot_falls_back_to_similarity() -> None:
    store = AsyncMock()
    store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "similarity": 0.96,
            "memory_slot": "other",
            "valid_to": None,
        }
    ]

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.2, 0.3])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User likes coffee")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.merged) == 1
    store.touch_memory.assert_awaited()


@pytest.mark.asyncio
async def test_dedup_same_slot_mid_similarity_supersedes() -> None:
    store = AsyncMock()
    store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "similarity": 0.70,
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    store.insert_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User rents a bike",
        "memory_slot": "vehicle",
        "valid_to": None,
    }
    store.supersede_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User rents a bike",
        "memory_slot": "vehicle",
        "valid_to": None,
    }

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.4, 0.5])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User rents a bike", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    store.supersede_memory.assert_awaited_once()
    store.insert_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_same_slot_very_low_similarity_inserts_new_active() -> None:
    store = AsyncMock()
    store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "similarity": 0.55,
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    store.insert_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User rents a bike",
        "memory_slot": "vehicle",
        "valid_to": None,
    }

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.4, 0.5])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User rents a bike", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.new) == 1
    store.insert_memory.assert_awaited_once()
    store.supersede_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_queries_include_historical() -> None:
    store = AsyncMock()
    store.search_memories.return_value = []
    store.insert_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User has shellfish allergy",
    }

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.9, 0.8])
        await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User has shellfish allergy", "allergy.shellfish")],
            conversation_id=uuid.uuid4(),
        )

    assert store.search_memories.await_args.kwargs["include_historical"] is True


@pytest.mark.asyncio
async def test_dedup_fallback_embedding_uses_lexical_existing_memory() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = []
    store.search_memories_bm25.return_value = [
        {
            "id": existing_id,
            "content": "User has shellfish allergy",
            "memory_slot": "allergy.shellfish",
            "valid_to": None,
        }
    ]

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result(
            [0.9, 0.8],
            storage_model="openai:text-embedding-3-small",
        )
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User has shellfish allergy", "allergy.shellfish")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.merged) == 1
    lexical_kwargs = store.search_memories_bm25.await_args.kwargs
    assert lexical_kwargs["include_l0"] is False
    assert lexical_kwargs["embedding_models"] == [
        "openai:text-embedding-3-small",
        "voyage-4-large",
    ]
    store.touch_memory.assert_awaited_once_with(existing_id)
    store.insert_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_primary_embedding_checks_lexical_fallback_memories() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = []
    store.search_memories_bm25.return_value = [
        {
            "id": existing_id,
            "content": "User has shellfish allergy",
            "memory_slot": "allergy.shellfish",
            "valid_to": None,
            "embedding_model": "openai:text-embedding-3-small",
        }
    ]

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result(
            [0.9, 0.8],
            storage_model="voyage-4-large",
        )
        with patch(
            "orchestrator.memory.dedup.get_configured_embedding_fallback_storage_models",
            return_value=("openai:text-embedding-3-small",),
        ):
            result = await deduplicate_facts(
                store,
                uuid.uuid4(),
                [_new_fact("User has shellfish allergy", "allergy.shellfish")],
                conversation_id=uuid.uuid4(),
            )

    assert len(result.merged) == 1
    store.touch_memory.assert_awaited_once_with(existing_id)
    store.insert_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_fallback_embedding_checks_same_slot_outside_bm25() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = []
    store.search_memories_bm25.return_value = []
    store.list_memories_by_slot_family.return_value = [
        {
            "id": existing_id,
            "content": "User drives a Tesla Model 3",
            "memory_slot": "vehicle.current",
            "valid_to": None,
        }
    ]
    store.supersede_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User drives a Subaru Outback",
        "memory_slot": "vehicle.current",
        "valid_to": None,
    }

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result(
            [0.9, 0.8],
            storage_model="openai:text-embedding-3-small",
        )
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User drives a Subaru Outback", "vehicle.current")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    store.list_memories_by_slot_family.assert_awaited_once()
    store.supersede_memory.assert_awaited_once()
    assert store.supersede_memory.await_args.kwargs["old_memory_id"] == existing_id
    store.insert_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_write_slot_passthrough_to_dedup() -> None:
    store = AsyncMock()
    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.1, 0.2])
        store.search_memories.return_value = []
        created_id = uuid.uuid4()
        store.insert_memory.return_value = {"id": created_id}
        memory_id = await dedup_and_store(
            store=store,
            user_id=uuid.uuid4(),
            content="User drives Tesla",
            source_type="user_created",
            category="fact",
            conversation_id=None,
            slot="vehicle",
        )

    assert memory_id == created_id
    store.insert_memory.assert_awaited_once()
    assert store.insert_memory.await_args.kwargs["memory_slot"] == "vehicle"


@pytest.mark.asyncio
async def test_current_vehicle_closes_other_active_vehicle_family() -> None:
    store = AsyncMock()
    old_vehicle_id = uuid.uuid4()
    keep_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": old_vehicle_id,
            "similarity": 0.58,
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    store.insert_memory.return_value = {
        "id": keep_id,
        "content": "User drives a 2023 Tesla Model 3",
        "memory_slot": "vehicle.current",
        "valid_to": None,
    }
    store.list_memories.return_value = [
        {"id": old_vehicle_id, "memory_slot": "vehicle"},
        {"id": keep_id, "memory_slot": "vehicle.current"},
    ]

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.3, 0.7])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User drives a 2023 Tesla Model 3", "vehicle.current")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.new) == 1
    store.close_memory.assert_any_await(old_vehicle_id)


@pytest.mark.asyncio
async def test_current_vehicle_closes_high_similarity_no_slot_memory() -> None:
    store = AsyncMock()
    no_slot_id = uuid.uuid4()
    keep_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": no_slot_id,
            "similarity": 0.72,
            "memory_slot": None,
            "valid_to": None,
        }
    ]
    store.insert_memory.return_value = {
        "id": keep_id,
        "content": "User drives a 2023 Tesla Model 3",
        "memory_slot": "vehicle.current",
        "valid_to": None,
    }
    store.list_memories.return_value = [
        {"id": no_slot_id, "memory_slot": None},
        {"id": keep_id, "memory_slot": "vehicle.current"},
    ]

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.3, 0.7])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User drives a 2023 Tesla Model 3", "vehicle.current")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.new) == 1
    store.close_memory.assert_any_await(no_slot_id)


@pytest.mark.asyncio
async def test_extracted_does_not_supersede_user_created_same_conversation() -> None:
    store = AsyncMock()
    conversation_id = uuid.uuid4()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.80,
            "memory_slot": "personal.name",
            "valid_to": None,
            "source_type": "user_created",
            "source_conversation_id": str(conversation_id),
            "created_at": datetime.now(timezone.utc),
        }
    ]

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.5, 0.6])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User's name is Julian", "personal.name")],
            conversation_id=conversation_id,
            source_type="extracted",
        )

    assert len(result.merged) == 1
    store.touch_memory.assert_awaited_once_with(existing_id)
    store.supersede_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_extracted_does_not_supersede_recent_user_created_within_window() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.79,
            "memory_slot": "location.city",
            "valid_to": None,
            "source_type": "user_created",
            "source_conversation_id": None,
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=4, seconds=59),
        }
    ]

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.2, 0.3])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User lives in Adelaide", "location.city")],
            conversation_id=uuid.uuid4(),
            source_type="extracted",
        )

    assert len(result.merged) == 1
    store.touch_memory.assert_awaited_once_with(existing_id)
    store.supersede_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_extracted_can_supersede_user_created_outside_window() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.79,
            "memory_slot": "location.city",
            "valid_to": None,
            "source_type": "user_created",
            "source_conversation_id": None,
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=6),
        }
    ]
    store.supersede_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User lives in Adelaide",
        "memory_slot": "location.city",
        "valid_to": None,
    }

    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata", new_callable=AsyncMock
    ) as embed:
        embed.return_value = _embedding_result([0.2, 0.3])
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User lives in Adelaide", "location.city")],
            conversation_id=uuid.uuid4(),
            source_type="extracted",
        )

    assert len(result.superseded) == 1
    store.supersede_memory.assert_awaited_once()
