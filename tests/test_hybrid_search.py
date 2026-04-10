"""Tests for BM25 hybrid search in memory retrieval."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import uuid

import pytest

from orchestrator.memory.retrieval import (
    _hybrid_score,
    _normalize_bm25_scores,
    retrieve_memories,
)


def test_hybrid_score_formula() -> None:
    result = _hybrid_score(
        vector_sim=0.8,
        bm25_normalized=0.6,
        recency=1.0,
        confidence=0.9,
    )
    # With trust=0.5 default: recency * confidence * trust = 1.0 * 0.9 * 0.5 = 0.45
    expected = 0.5 * 0.8 + 0.3 * 0.6 + 0.2 * 0.45
    assert result == pytest.approx(0.67, abs=0.01)


def test_hybrid_score_with_zero_bm25() -> None:
    result = _hybrid_score(
        vector_sim=0.5,
        bm25_normalized=0.0,
        recency=1.0,
        confidence=1.0,
    )
    # With trust=0.5 default: recency * confidence * trust = 1.0 * 1.0 * 0.5 = 0.5
    expected = 0.5 * 0.5 + 0.3 * 0.0 + 0.2 * 0.5
    assert result == pytest.approx(0.35, abs=0.01)


def test_hybrid_score_with_zero_vector() -> None:
    result = _hybrid_score(
        vector_sim=0.0,
        bm25_normalized=0.7,
        recency=0.9,
        confidence=0.8,
    )
    # With trust=0.5 default: recency * confidence * trust = 0.9 * 0.8 * 0.5 = 0.36
    expected = 0.5 * 0.0 + 0.3 * 0.7 + 0.2 * 0.36
    assert result == pytest.approx(0.282, abs=0.01)


def test_normalize_bm25_scores_empty() -> None:
    candidates = []
    _normalize_bm25_scores(candidates)
    assert candidates == []


def test_normalize_bm25_scores_normalizes_correctly() -> None:
    candidates: list[dict[str, object]] = [
        {"id": "1", "bm25_score": 0.5},
        {"id": "2", "bm25_score": 1.0},
        {"id": "3", "bm25_score": 0.25},
    ]
    _normalize_bm25_scores(candidates)
    assert candidates[0]["bm25_normalized"] == pytest.approx(0.5)
    assert candidates[1]["bm25_normalized"] == pytest.approx(1.0)
    assert candidates[2]["bm25_normalized"] == pytest.approx(0.25)


def test_normalize_bm25_scores_all_zero() -> None:
    candidates: list[dict[str, object]] = [
        {"id": "1", "bm25_score": 0.0},
        {"id": "2", "bm25_score": 0.0},
    ]
    _normalize_bm25_scores(candidates)
    assert candidates[0]["bm25_normalized"] == 0.0
    assert candidates[1]["bm25_normalized"] == 0.0


@pytest.mark.asyncio
async def test_retrieve_memories_hybrid_union() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }

    store.search_memories.return_value = [
        {
            "id": "vec-only",
            "content": "vector match",
            "similarity": 0.9,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": now,
            "category": "fact",
            "source_type": "extracted",
        },
    ]

    store.search_memories_bm25.return_value = [
        {
            "id": "bm25-only",
            "content": "bm25 match",
            "bm25_score": 0.8,
            "confidence": 0.8,
            "access_count": 2,
            "last_accessed_at": now,
            "category": "fact",
            "source_type": "extracted",
        },
    ]

    result = await retrieve_memories(
        store,
        [0.1] * 1024,
        query_text="test query",
        conversation_id=conversation_id,
        limit=5,
    )

    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert "vec-only" in ids
    assert "bm25-only" in ids


@pytest.mark.asyncio
async def test_retrieve_memories_hybrid_combines_scores() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }

    memory_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": memory_id,
            "content": "both match",
            "similarity": 0.8,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": now,
            "category": "fact",
            "source_type": "extracted",
        },
    ]

    store.search_memories_bm25.return_value = [
        {
            "id": memory_id,
            "content": "both match",
            "bm25_score": 0.6,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": now,
            "category": "fact",
            "source_type": "extracted",
        },
    ]

    result = await retrieve_memories(
        store,
        [0.1] * 1024,
        query_text="test query",
        conversation_id=conversation_id,
        limit=5,
    )

    assert len(result) == 1
    assert result[0]["source"] == "hybrid"
    assert result[0]["vector_sim"] == pytest.approx(0.8)
    assert result[0]["bm25_normalized"] == pytest.approx(1.0)
    assert "final_score" in result[0]


@pytest.mark.asyncio
async def test_retrieve_memories_hybrid_without_query_text() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }

    store.search_memories.return_value = [
        {
            "id": "vec-only",
            "content": "vector match",
            "similarity": 0.9,
            "confidence": 0.9,
            "access_count": 1,
            "last_accessed_at": now,
            "category": "fact",
            "source_type": "extracted",
        },
    ]

    result = await retrieve_memories(
        store,
        [0.1] * 1024,
        conversation_id=conversation_id,
        limit=5,
    )

    assert len(result) == 1
    store.search_memories_bm25.assert_not_called()


@pytest.mark.asyncio
async def test_retrieve_memories_hybrid_bm25_fallback() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }

    store.search_memories.return_value = []
    store.search_memories_bm25.return_value = [
        {
            "id": "bm25-fallback",
            "content": "bm25 match",
            "bm25_score": 0.5,
            "confidence": 0.8,
            "access_count": 0,
            "last_accessed_at": now,
            "category": "fact",
            "source_type": "extracted",
        },
    ]

    result = await retrieve_memories(
        store,
        [0.1] * 1024,
        query_text="test query",
        conversation_id=conversation_id,
        limit=5,
    )

    assert len(result) == 1
    assert result[0]["source"] == "bm25"
    assert result[0]["bm25_normalized"] == pytest.approx(1.0)
