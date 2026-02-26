from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import uuid
from typing import cast

from orchestrator.memory.retrieval import (
    MIN_FINAL_SCORE,
    _access_boost,
    _recency_score,
    _score_memory,
    retrieve_memories,
)


@pytest.mark.parametrize(
    ("days", "expected"),
    [(7, 1.0), (8, 0.9), (30, 0.9), (31, 0.7), (90, 0.7), (91, 0.5)],
)
def test_recency_score_boundaries(days: float, expected: float) -> None:
    assert _recency_score(days) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, 1.0), (1, 1.05), (5, 1.05), (6, 1.1), (20, 1.1), (21, 1.15), (999, 1.15)],
)
def test_access_boost_tiers(count: int, expected: float) -> None:
    assert _access_boost({"access_count": count}) == pytest.approx(expected)


def test_score_memory_multiplies_all_factors() -> None:
    now = datetime.now(timezone.utc)
    memory = cast(
        dict[str, object],
        {
            "similarity": 0.8,
            "category": "project",
            "source_type": "project",
            "confidence": 0.9,
            "access_count": 10,
            "last_accessed_at": now - timedelta(days=10),
        },
    )
    expected = 0.8 * 0.9 * 1.1 * 0.9 * 1.1
    assert _score_memory(memory) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_retrieve_memories_filters_below_min_score() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }
    old = datetime.now(timezone.utc) - timedelta(days=365)
    store.search_memories.return_value = [
        {
            "id": "m-low",
            "content": "very weak match",
            "similarity": 0.2,
            "confidence": 0.4,
            "access_count": 0,
            "last_accessed_at": old,
            "category": "fact",
            "source_type": "extracted",
        },
        {
            "id": "m-good",
            "content": "useful memory",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 2,
            "last_accessed_at": datetime.now(timezone.utc) - timedelta(days=2),
            "category": "fact",
            "source_type": "extracted",
        },
    ]

    result = await retrieve_memories(
        store, [0.1] * 5, conversation_id=conversation_id, limit=5
    )

    assert len(result) == 1
    assert result[0]["id"] == "m-good"
    final_score = result[0].get("final_score")
    assert isinstance(final_score, (int, float))
    assert final_score >= MIN_FINAL_SCORE


@pytest.mark.asyncio
async def test_retrieve_memories_can_return_fewer_than_limit() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }
    now = datetime.now(timezone.utc)
    store.search_memories.return_value = [
        {
            "id": "m1",
            "similarity": 0.9,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": now,
            "category": "fact",
            "source_type": "extracted",
        },
        {
            "id": "m2",
            "similarity": 0.1,
            "confidence": 0.1,
            "access_count": 0,
            "last_accessed_at": now - timedelta(days=365),
            "category": "fact",
            "source_type": "extracted",
        },
    ]

    result = await retrieve_memories(
        store, [0.1] * 5, conversation_id=conversation_id, limit=5
    )

    assert len(result) == 1
    assert result[0]["id"] == "m1"
