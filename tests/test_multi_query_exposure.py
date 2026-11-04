"""Synthetic offline exposure gates, not spent-case or fresh acceptance evidence."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator.eval import exposure
from orchestrator.memory import injection, retrieval
from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.multi_query import ProductionSubqueryProvider
from tests.longmemeval.evaluate import build_parity_memory_context

USER = uuid.UUID(int=100)
SOURCE = uuid.UUID(int=101)
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
QUESTION = "Which two fictional facts?"
COMPONENT = "Second fictional fact"


@pytest.fixture
def offline(monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("Live embedding forbidden"))
    monkeypatch.setattr(injection, "embed_query_with_metadata", forbidden)
    monkeypatch.setattr(injection, "embed_queries_with_metadata", forbidden)
    monkeypatch.setattr(retrieval, "embed_query_for_configured_storage_models", forbidden)
    monkeypatch.setattr(
        retrieval, "_available_fallback_storage_models", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(retrieval, "_get_entity_expanded_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_is_retrieval_logging_enabled", lambda _: False)

    async def search(**kwargs):
        start = 1 if kwargs["query_embedding"] == [1.0] else 6
        return [
            {
                "id": uuid.UUID(int=i),
                "content": f"Fictional fact {i}",
                "similarity": 1.0,
                "local_only": False,
                "source_conversation_id": SOURCE,
            }
            for i in range(start, start + 5)
        ]

    store = SimpleNamespace(
        get_l0_memories=AsyncMock(return_value=[]),
        get_recent_summaries=AsyncMock(return_value=[]),
        search_memories=AsyncMock(side_effect=search),
        search_memories_bm25=AsyncMock(return_value=[]),
        bulk_touch_memories=AsyncMock(),
        log_retrieval=AsyncMock(),
    )
    vectors = {
        QUESTION: EmbeddingVectorResult([1.0], "cached", "query-model", "storage-model"),
        COMPONENT: EmbeddingVectorResult([2.0], "cached", "query-model", "storage-model"),
    }
    return store, vectors, forbidden


async def _measure(store, vectors, *, merge="additive", provider=None):
    return await exposure.measure_multi_query_exposure(
        cast(Any, store),
        user_id=USER,
        question=QUESTION,
        provider=provider or exposure.PinnedSubqueryProvider((COMPONENT,)),
        query_embeddings=vectors,
        reference_time=NOW,
        allowed_source_conversation_ids=[SOURCE],
        target_ids=[str(uuid.UUID(int=9))],
        gold_conversation_ids=[str(SOURCE)],
        source_conversations={str(uuid.UUID(int=i)): str(SOURCE) for i in range(1, 11)},
        merge=merge,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("merge", ["additive", "round_robin"])
async def test_exposure_uses_production_retrieval_and_selected_merge_without_providers(
    offline, merge
):
    store, vectors, forbidden = offline
    result = await _measure(store, vectors, merge=merge)
    before = result["baseline"]
    after = result["candidate"]
    assert before["rendered_ids"] == [str(uuid.UUID(int=i)) for i in range(1, 6)]
    expected = [1, 2, 3, 4, 5, 6, 7, 8] if merge == "additive" else [1, 6, 2, 7, 3, 8, 4, 9]
    assert after["rendered_ids"] == [str(uuid.UUID(int=i)) for i in expected]
    assert after["target_exposed"] is (merge == "round_robin")
    assert result["subquery_count"] == 1
    assert not result["exposure_regression"]
    assert store.search_memories.await_count == 3
    assert [c.kwargs["limit"] for c in store.search_memories.await_args_list] == [10, 10, 10]
    assert COMPONENT not in json.dumps(result)
    forbidden.assert_not_awaited()
    store.bulk_touch_memories.assert_not_awaited()
    store.log_retrieval.assert_not_awaited()


@pytest.mark.asyncio
async def test_harness_pinned_provider_and_cache_match_production_exposure(offline):
    store, vectors, forbidden = offline
    block, inputs = await build_parity_memory_context(
        cast(Any, store),
        user_id=USER,
        question_text=QUESTION,
        allowed_source_conversation_ids=[SOURCE],
        query_reference_time=NOW,
        subquery_provider=exposure.PinnedSubqueryProvider((COMPONENT,)),
        pinned_query_embeddings=vectors,
    )
    assert block == injection.render_memory_context(inputs)
    result = await _measure(store, vectors)
    assert [str(row["id"]) for row in injection.rendered_memory_rows(inputs, block)] == result[
        "candidate"
    ]["rendered_ids"]
    forbidden.assert_not_awaited()
    store.bulk_touch_memories.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["live", "subclass", "missing_query", "missing_component"])
async def test_exposure_refuses_live_or_uncached_work_before_reads(offline, kind):
    store, vectors, forbidden = offline
    provider = exposure.PinnedSubqueryProvider((COMPONENT,))
    if kind == "live":
        provider = cast(Any, ProductionSubqueryProvider())
    elif kind == "subclass":

        class NotFrozen(exposure.PinnedSubqueryProvider):
            async def get_subqueries(self, *args, **kwargs):
                raise AssertionError("Must not execute arbitrary provider code")

        provider = NotFrozen((COMPONENT,))
    else:
        vectors = dict(vectors)
        del vectors[QUESTION if kind == "missing_query" else COMPONENT]
    with pytest.raises(ValueError):
        await _measure(store, vectors, provider=provider)
    store.get_l0_memories.assert_not_awaited()
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_vector_pins_do_not_leak_across_concurrent_evaluations(offline):
    store, vectors, forbidden = offline
    first, second = await asyncio.gather(
        _measure(store, vectors), _measure(store, vectors, merge="round_robin")
    )
    assert first["candidate"]["target_exposed"] is False
    assert second["candidate"]["target_exposed"] is True
    forbidden.assert_not_awaited()
