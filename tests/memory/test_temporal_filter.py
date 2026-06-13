from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.retrieval import (
    _detect_temporal_query_window,
    retrieve_memories,
)


def _memory(
    *,
    memory_id: uuid.UUID,
    content: str,
    similarity: float,
    valid_from: dt.datetime,
    valid_to: dt.datetime | None,
) -> dict[str, object]:
    return {
        "id": memory_id,
        "content": content,
        "similarity": similarity,
        "confidence": 1.0,
        "trust_score": 1.0,
        "access_count": 0,
        "source_type": "import",
        "category": "fact",
        "created_at": valid_from,
        "updated_at": valid_from,
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


def test_detect_temporal_query_window_requires_explicit_resolvable_time() -> None:
    reference_time = "2023/07/01 (Sat) 02:36"

    june_window = _detect_temporal_query_window(
        "What was the date on which I attended the first BBQ event in June?",
        query_reference_time=reference_time,
    )
    assert june_window is not None
    assert june_window.detector == "month_only"
    assert june_window.start == dt.datetime(2023, 6, 1, tzinfo=dt.timezone.utc)
    assert june_window.end == dt.datetime(2023, 7, 1, tzinfo=dt.timezone.utc)

    relative_window = _detect_temporal_query_window(
        "Where was I living 2 years ago?",
        query_reference_time=dt.datetime(2025, 4, 19, 12, 0, tzinfo=dt.timezone.utc),
    )
    assert relative_window is not None
    assert relative_window.detector == "relative_ago"

    assert (
        _detect_temporal_query_window(
            "What snacks do I keep on my counter?",
            query_reference_time=reference_time,
        )
        is None
    )
    assert (
        _detect_temporal_query_window(
            "How many days ago did I watch the Super Bowl?",
            query_reference_time=reference_time,
        )
        is None
    )


def test_detect_temporal_query_window_respects_explicit_year() -> None:
    # "June 2022" with a 2024 reference must resolve to June 2022, not
    # the reference-relative June 2024.
    window = _detect_temporal_query_window(
        "What happened in June 2022?",
        query_reference_time=dt.datetime(2024, 8, 1, tzinfo=dt.timezone.utc),
    )
    assert window is not None
    assert window.detector == "month_and_year"
    assert window.start == dt.datetime(2022, 6, 1, tzinfo=dt.timezone.utc)
    assert window.end == dt.datetime(2022, 7, 1, tzinfo=dt.timezone.utc)


def test_detect_temporal_query_window_handles_leap_day_reference() -> None:
    # A Feb 29 reference with "1 year ago" must not raise ValueError.
    window = _detect_temporal_query_window(
        "Where was I living 1 year ago?",
        query_reference_time=dt.datetime(2024, 2, 29, 12, 0, tzinfo=dt.timezone.utc),
    )
    assert window is not None
    assert window.detector == "relative_ago"
    # Window centers on Feb 28 of the non-leap target year.
    assert window.start < dt.datetime(2023, 2, 28, 12, 0, tzinfo=dt.timezone.utc) < window.end


@pytest.mark.asyncio
async def test_retrieve_memories_keeps_normal_queries_on_active_only_path() -> None:
    user_id = uuid.uuid4()
    active_memory = _memory(
        memory_id=uuid.uuid4(),
        content="You keep apples on the counter.",
        similarity=0.92,
        valid_from=dt.datetime(2023, 5, 1, tzinfo=dt.timezone.utc),
        valid_to=None,
    )

    mock_store = AsyncMock()
    mock_store.search_memories.return_value = [active_memory]
    mock_store.search_memories_bm25.return_value = []
    mock_store.bulk_touch_memories.return_value = None

    with patch(
        "orchestrator.memory.retrieval._get_entity_expanded_candidates",
        new=AsyncMock(return_value=[]),
    ):
        result = await retrieve_memories(
            mock_store,
            [0.1] * 1024,
            query_text="What snacks do I keep on my counter?",
            user_id=user_id,
            limit=3,
        )

    assert [memory["id"] for memory in result] == [active_memory["id"]]
    assert mock_store.search_memories.await_args.kwargs["include_historical"] is False
    assert mock_store.search_memories_bm25.await_args.kwargs["include_historical"] is False


@pytest.mark.asyncio
async def test_retrieve_memories_filters_historical_candidates_for_explicit_time_query() -> None:
    user_id = uuid.uuid4()
    june_match = _memory(
        memory_id=uuid.uuid4(),
        content="You attended a BBQ on June 3.",
        similarity=0.88,
        valid_from=dt.datetime(2023, 6, 3, tzinfo=dt.timezone.utc),
        valid_to=dt.datetime(2023, 6, 30, 23, 59, tzinfo=dt.timezone.utc),
    )
    may_outside = _memory(
        memory_id=uuid.uuid4(),
        content="You attended a BBQ on May 20.",
        similarity=0.97,
        valid_from=dt.datetime(2023, 5, 20, tzinfo=dt.timezone.utc),
        valid_to=dt.datetime(2023, 5, 21, tzinfo=dt.timezone.utc),
    )
    july_future = _memory(
        memory_id=uuid.uuid4(),
        content="You attended a BBQ on July 2.",
        similarity=0.99,
        valid_from=dt.datetime(2023, 7, 2, tzinfo=dt.timezone.utc),
        valid_to=None,
    )

    mock_store = AsyncMock()
    mock_store.search_memories.return_value = [july_future, may_outside, june_match]
    mock_store.search_memories_bm25.return_value = []
    mock_store.bulk_touch_memories.return_value = None

    with patch(
        "orchestrator.memory.retrieval._get_entity_expanded_candidates",
        new=AsyncMock(return_value=[]),
    ):
        result = await retrieve_memories(
            mock_store,
            [0.1] * 1024,
            query_text="What was the date on which I attended the first BBQ event in June?",
            user_id=user_id,
            query_reference_time="2023/07/01 (Sat) 02:36",
            limit=5,
        )

    assert [memory["id"] for memory in result] == [june_match["id"]]
    assert mock_store.search_memories.await_args.kwargs["include_historical"] is True
    assert mock_store.search_memories_bm25.await_args.kwargs["include_historical"] is True


@pytest.mark.asyncio
async def test_retrieve_memories_temporal_filter_falls_back_when_window_is_too_narrow() -> None:
    user_id = uuid.uuid4()
    only_candidate = _memory(
        memory_id=uuid.uuid4(),
        content="You attended a BBQ on July 2.",
        similarity=0.95,
        valid_from=dt.datetime(2023, 7, 2, tzinfo=dt.timezone.utc),
        valid_to=None,
    )

    mock_store = AsyncMock()
    mock_store.search_memories.return_value = [only_candidate]
    mock_store.search_memories_bm25.return_value = []
    mock_store.bulk_touch_memories.return_value = None

    with patch(
        "orchestrator.memory.retrieval._get_entity_expanded_candidates",
        new=AsyncMock(return_value=[]),
    ):
        result = await retrieve_memories(
            mock_store,
            [0.1] * 1024,
            query_text="What was the date on which I attended the first BBQ event in June?",
            user_id=user_id,
            query_reference_time="2023/07/01 (Sat) 02:36",
            limit=5,
        )

    assert [memory["id"] for memory in result] == [only_candidate["id"]]


@pytest.mark.asyncio
async def test_retrieve_memories_for_text_threads_reference_time() -> None:
    from orchestrator.memory.retrieval import retrieve_memories_for_text

    user_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def fake_retrieve(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return []

    with (
        patch("orchestrator.memory.retrieval.retrieve_memories", new=fake_retrieve),
        patch(
            "orchestrator.memory.retrieval.embed_query_for_configured_storage_models",
            new=AsyncMock(
                return_value=[
                    EmbeddingVectorResult(
                        embedding=[0.1] * 8,
                        provider="voyage",
                        model="voyage-4-lite",
                        storage_model="voyage-4-large",
                    )
                ]
            ),
        ),
    ):
        await retrieve_memories_for_text(
            AsyncMock(),
            "what happened in June?",
            user_id=user_id,
            query_reference_time="2023/07/01 (Sat) 02:36",
        )

    assert captured["query_reference_time"] == "2023/07/01 (Sat) 02:36"
