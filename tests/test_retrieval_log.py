from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.retrieval import retrieve_memories, retrieve_memories_for_text
from orchestrator.memory.store import MemoryStore


@pytest.fixture
def mock_store() -> MemoryStore:
    store = AsyncMock(spec=MemoryStore)
    store.search_memories = AsyncMock(return_value=[])
    store.search_memories_bm25 = AsyncMock(return_value=[])
    store.get_conversation = AsyncMock(return_value=None)
    store.log_retrieval = AsyncMock(return_value={})
    store.get_l0_memories = AsyncMock(return_value=[])
    store.bulk_touch_memories = AsyncMock()
    return store  # type: ignore[return-value]


async def _allow_background_tasks() -> None:
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_retrieve_memories_does_not_log_when_flag_false(mock_store):
    user_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5

    result = await retrieve_memories(
        mock_store,
        embedding,
        user_id=user_id,
        log_retrieval=False,
    )

    assert len(result) >= 0
    mock_store.log_retrieval.assert_not_called()


@pytest.mark.asyncio
async def test_retrieve_memories_logs_when_flag_true(mock_store):
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": memory_id,
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5

    result = await retrieve_memories(
        mock_store,
        embedding,
        user_id=user_id,
        log_retrieval=True,
    )
    await _allow_background_tasks()

    assert len(result) >= 0
    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["user_id"] == user_id
    assert call_kwargs["query_embedding_model"] == "voyage-4-lite"
    assert isinstance(call_kwargs["latency_ms"], int)
    assert call_kwargs["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_retrieve_memories_captures_detailed_candidate_scores(mock_store):
    user_id = uuid.uuid4()
    memory_id_1 = uuid.uuid4()
    memory_id_2 = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": memory_id_1,
            "content": "memory 1",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        },
        {
            "id": memory_id_2,
            "content": "memory 2",
            "similarity": 0.6,
            "confidence": 0.8,
            "access_count": 0,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        },
    ]
    embedding = [0.1] * 5

    await retrieve_memories(
        mock_store,
        embedding,
        user_id=user_id,
        log_retrieval=True,
    )
    await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    candidate_scores = call_kwargs["candidate_scores"]
    assert isinstance(candidate_scores, dict)
    assert str(memory_id_1) in candidate_scores
    assert str(memory_id_2) in candidate_scores

    for mid, breakdown in candidate_scores.items():
        assert isinstance(breakdown, dict)
        assert "vector_sim" in breakdown
        assert "bm25_normalized" in breakdown
        assert "recency_boost" in breakdown
        assert "source_boost" in breakdown
        assert "access_boost" in breakdown
        assert "confidence" in breakdown
        assert "trust" in breakdown
        assert "final_score" in breakdown
        assert all(isinstance(v, float) for v in breakdown.values())


@pytest.mark.asyncio
async def test_retrieve_memories_includes_l0_flag(mock_store):
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": memory_id,
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5

    await retrieve_memories(
        mock_store,
        embedding,
        user_id=user_id,
        log_retrieval=True,
    )
    await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["l0_included"] is False


@pytest.mark.asyncio
async def test_retrieve_memories_logs_conversation_id_when_provided(mock_store):
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5
    mock_store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }

    await retrieve_memories(
        mock_store,
        embedding,
        conversation_id=conversation_id,
        log_retrieval=True,
    )
    await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["conversation_id"] == conversation_id


@pytest.mark.asyncio
async def test_retrieve_memories_logs_retrieval_trigger_and_context(mock_store):
    user_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5

    await retrieve_memories(
        mock_store,
        embedding,
        user_id=user_id,
        log_retrieval=True,
        retrieval_triggered_by="memory_read",
        retrieval_context="explicit_recall",
    )
    await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["retrieval_triggered_by"] == "memory_read"
    assert call_kwargs["retrieval_context"] == "explicit_recall"


@pytest.mark.asyncio
async def test_retrieve_memories_handles_logging_failure_gracefully(mock_store):
    user_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5
    mock_store.log_retrieval.side_effect = Exception("DB connection failed")

    result = await retrieve_memories(
        mock_store,
        embedding,
        user_id=user_id,
        log_retrieval=True,
    )

    assert len(result) >= 0


@pytest.mark.asyncio
async def test_retrieve_memories_for_text_logs_with_l0_inclusion(mock_store):
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    l0_memory_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": memory_id,
            "content": "regular memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    mock_store.get_l0_memories.return_value = [
        {
            "id": l0_memory_id,
            "content": "L0 frozen memory",
            "similarity": 1.0,
            "confidence": 1.0,
            "access_count": 0,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
            "tier": "l0",
        }
    ]

    result = await retrieve_memories_for_text(
        mock_store,
        "test query",
        user_id=user_id,
        include_l0=True,
        log_retrieval=True,
    )
    await _allow_background_tasks()

    assert len(result) >= 2
    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["l0_included"] is True
    assert call_kwargs["query_text"] == "test query"


@pytest.mark.asyncio
async def test_retrieve_memories_for_text_passes_triggered_by_to_inner_call(mock_store):
    user_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]

    await retrieve_memories_for_text(
        mock_store,
        "test query",
        user_id=user_id,
        log_retrieval=True,
        retrieval_triggered_by="memory_reflect",
    )
    await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["retrieval_triggered_by"] == "memory_reflect"


@pytest.mark.asyncio
async def test_retrieve_memories_logs_when_config_flag_enabled(mock_store):
    user_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5

    mock_settings = AsyncMock()
    mock_settings.retrieval_logging_enabled = True
    mock_settings.retrieval_logging_debug = False

    with patch(
        "orchestrator.memory.retrieval.get_settings", return_value=mock_settings
    ):
        await retrieve_memories(
            mock_store,
            embedding,
            user_id=user_id,
            log_retrieval=False,
        )
        await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["user_id"] == user_id


@pytest.mark.asyncio
async def test_retrieve_memories_logs_when_config_debug_flag_enabled(mock_store):
    user_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5

    mock_settings = AsyncMock()
    mock_settings.retrieval_logging_enabled = False
    mock_settings.retrieval_logging_debug = True

    with patch(
        "orchestrator.memory.retrieval.get_settings", return_value=mock_settings
    ):
        await retrieve_memories(
            mock_store,
            embedding,
            user_id=user_id,
            log_retrieval=False,
        )
        await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["user_id"] == user_id


@pytest.mark.asyncio
async def test_retrieve_memories_does_not_log_when_both_flags_false(mock_store):
    user_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5

    mock_settings = AsyncMock()
    mock_settings.retrieval_logging_enabled = False
    mock_settings.retrieval_logging_debug = False

    with patch(
        "orchestrator.memory.retrieval.get_settings", return_value=mock_settings
    ):
        await retrieve_memories(
            mock_store,
            embedding,
            user_id=user_id,
            log_retrieval=False,
        )
        await _allow_background_tasks()

    mock_store.log_retrieval.assert_not_called()


@pytest.mark.asyncio
async def test_retrieve_memories_logs_when_both_flags_true(mock_store):
    """Test retrieval logs when both retrieval_logging_enabled AND debug are True."""
    user_id = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "test memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        }
    ]
    embedding = [0.1] * 5

    mock_settings = AsyncMock()
    mock_settings.retrieval_logging_enabled = True
    mock_settings.retrieval_logging_debug = True

    with patch(
        "orchestrator.memory.retrieval.get_settings", return_value=mock_settings
    ):
        await retrieve_memories(
            mock_store,
            embedding,
            user_id=user_id,
            log_retrieval=False,
        )
        await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert call_kwargs["user_id"] == user_id


@pytest.mark.asyncio
async def test_retrieve_memories_candidate_scores_has_selected_ids(mock_store):
    """Test that candidate_scores dict includes selected_ids tracking."""
    user_id = uuid.uuid4()
    memory_id_1 = uuid.uuid4()
    memory_id_2 = uuid.uuid4()
    mock_store.search_memories.return_value = [
        {
            "id": memory_id_1,
            "content": "memory 1",
            "similarity": 0.9,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        },
        {
            "id": memory_id_2,
            "content": "memory 2",
            "similarity": 0.6,
            "confidence": 0.8,
            "access_count": 0,
            "last_accessed_at": datetime.now(timezone.utc),
            "category": "fact",
            "source_type": "extracted",
            "trust_score": 0.5,
        },
    ]
    embedding = [0.1] * 5

    await retrieve_memories(
        mock_store,
        embedding,
        user_id=user_id,
        log_retrieval=True,
    )
    await _allow_background_tasks()

    mock_store.log_retrieval.assert_called_once()
    call_kwargs = mock_store.log_retrieval.call_args.kwargs
    assert "candidate_memory_ids" in call_kwargs
    assert "selected_memory_ids" in call_kwargs
    candidate_ids = call_kwargs["candidate_memory_ids"]
    selected_ids = call_kwargs["selected_memory_ids"]
    assert isinstance(candidate_ids, list)
    assert isinstance(selected_ids, list)
    assert memory_id_1 in candidate_ids
    assert memory_id_2 in candidate_ids
