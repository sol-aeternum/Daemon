"""Offline Phase 3A selection regressions; synthetic facts/vectors and mocked IO only."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
import uuid

import pytest

from orchestrator.memory import injection, retrieval
from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.store import MemoryStore

SPACE = "synthetic:document-space:2"


def candidate(
    index: int, score: float, vector: object = (1.0, 0.0), model: object = SPACE
) -> dict[str, object]:
    return {
        "id": uuid.UUID(int=index),
        "content": f"Synthetic fact {index}",
        "category": "fact",
        "final_score": score,
        "embedding": vector,
        "embedding_model": model,
    }


def select(rows: list[dict[str, object]], limit: int = 2, penalty: float = 0.10):
    return retrieval._select_diverse_memories(rows, limit=limit, penalty=penalty)


def test_diversity_can_refill_a_redundant_slot_without_mutation() -> None:
    rows = [candidate(1, 0.9), candidate(2, 0.89), candidate(3, 0.85, (0.0, 1.0))]
    before = copy.deepcopy(rows)
    assert select(rows, penalty=0) == rows[:2]
    assert select(rows) == [rows[0], rows[2]]
    assert rows == before
    assert all(result is rows[index] for result, index in zip(select(rows), (0, 2), strict=True))


@pytest.mark.parametrize("limit", [0, 1, 2, 5, 15])
def test_zero_penalty_is_exact_historical_sort_and_slice(limit: int) -> None:
    rows = [candidate(index, 0.9 - (index % 3) * 0.01, (index, 1)) for index in range(1, 13)]
    expected = sorted(rows, key=lambda row: (-cast(float, row["final_score"]), str(row["id"])))[
        :limit
    ]
    actual = select(list(reversed(rows)), limit=limit, penalty=0)
    assert actual == expected
    assert injection.render_memory_context(
        injection.MemoryContextInputs(retrieved=actual)
    ) == injection.render_memory_context(injection.MemoryContextInputs(retrieved=expected))


def test_membership_is_diverse_but_output_keeps_original_relevance_order() -> None:
    rows = [
        candidate(1, 0.9),
        candidate(2, 0.89),
        candidate(3, 0.85, (0, 1)),
        candidate(4, 0.83, (0, 1)),
    ]
    # Greedy selection chooses 1, 3, 2; output retains the old relevance ordering.
    assert select(rows, limit=3) == rows[:3]


def test_ties_are_deterministic_and_penalty_cannot_reverse_a_larger_score_gap() -> None:
    rows = [candidate(1, 0.9), candidate(2, 0.85), candidate(3, 0.749, (0, 1))]
    assert select(rows) == rows[:2]
    tied = [
        candidate(1, 0.9),
        candidate(2, 0.89),
        candidate(3, 0.85, (0, 1)),
        candidate(4, 0.85, (0, 1)),
    ]
    assert select(tied) == select(list(reversed(tied))) == [tied[0], tied[2]]


def test_negative_cosine_is_not_a_novelty_bonus() -> None:
    rows = [candidate(1, 0.9), candidate(2, 0.85, (-1, 0)), candidate(3, 0.86, (0, 1))]
    assert select(rows) == [rows[0], rows[2]]


@pytest.mark.parametrize("raw", ["[1, 0]", [1, 0], (1, 0), [1e-300, 0]])
def test_stored_vector_formats_are_normalized_without_inference(raw: object) -> None:
    assert retrieval._normalized_memory_vector(candidate(1, 1, raw)) == (SPACE, (1.0, 0.0))


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        (),
        [0, 0],
        "",
        "1,0",
        "[]",
        "[oops, 1]",
        "[nan, 0]",
        "[inf, 0]",
        [float("nan"), 0],
        [float("inf"), 0],
        [True, 0],
        [{}, 0],
        [1.7e308, 1.7e308],
    ],
)
def test_invalid_vectors_have_zero_redundancy_and_do_not_drop_rows(raw: object) -> None:
    assert retrieval._normalized_memory_vector(candidate(2, 0.89, raw)) is None
    rows = [candidate(1, 0.9), candidate(2, 0.89, raw), candidate(3, 0.85, (0, 1))]
    assert select(rows) == rows[:2]


@pytest.mark.parametrize("model", [None, "", " ", "different-space"])
def test_unknown_or_different_embedding_spaces_are_not_compared(model: object) -> None:
    rows = [candidate(1, 0.9), candidate(2, 0.89, model=model), candidate(3, 0.85, (0, 1))]
    assert select(rows) == rows[:2]


def test_mismatched_dimensions_are_not_compared() -> None:
    rows = [candidate(1, 0.9), candidate(2, 0.89, (1, 0, 0)), candidate(3, 0.85, (0, 1))]
    assert select(rows) == rows[:2]


def test_quantities_and_conflicting_updates_remain_separate_unchanged_facts() -> None:
    rows = [candidate(index, 0.9 - index * 0.01) for index in range(1, 7)]
    for row, text in zip(
        rows,
        (
            "Normal run took 25 hours",
            "Hard run took 30 hours",
            "Earlier pre-approval was $350,000",
            "Later pre-approval was $400,000",
            "Previously weekly tennis",
            "Later every-other-week tennis",
        ),
        strict=True,
    ):
        row["content"] = text
    original = copy.deepcopy(rows)
    # Identical embeddings never authorize merging rows or rewriting quantities.
    assert select(rows, limit=5) == rows[:5]
    assert select(rows, limit=6) == rows
    assert rows == original


def _mock_runtime(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]):
    store = AsyncMock(spec=MemoryStore)
    store.search_memories.return_value = rows
    store.search_memories_bm25.return_value = []
    store.get_l0_memories.return_value = []
    store.get_recent_summaries.return_value = []
    store.get_recent_messages.return_value = [{"role": "user", "content": "Synthetic question"}]
    store.get_conversation.return_value = {"user_id": uuid.UUID(int=100), "pipeline": "cloud"}
    result = EmbeddingVectorResult(
        embedding=[1.0, 0.0], provider="synthetic", model="query", storage_model=SPACE
    )
    embed = AsyncMock(return_value=result)
    embedding_passes = AsyncMock(return_value=[result])
    monkeypatch.setattr(injection, "embed_query_with_metadata", embed)
    monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: "synthetic-route")
    monkeypatch.setattr(injection, "_lazy_import_trust_signals", lambda: None)
    monkeypatch.setattr(retrieval, "embed_query_for_configured_storage_models", embedding_passes)
    monkeypatch.setattr(
        retrieval, "_available_fallback_storage_models", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(retrieval, "_get_entity_expanded_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_is_retrieval_logging_enabled", lambda _: False)
    monkeypatch.setattr(retrieval, "get_primary_embedding_storage_model", lambda: SPACE)
    monkeypatch.setattr(retrieval, "get_configured_embedding_fallback_storage_models", lambda: ())
    monkeypatch.setattr(retrieval, "get_lexical_embedding_storage_models", lambda: [SPACE])
    monkeypatch.setattr(
        retrieval, "get_settings", lambda: SimpleNamespace(embedding_query_model="query")
    )
    touches = Mock()
    monkeypatch.setattr(retrieval, "_schedule_memory_touches", touches)
    return store, result, embed, embedding_passes, touches


def _search_rows() -> list[dict[str, object]]:
    now = datetime.now(timezone.utc)
    rows = [candidate(index, 0, (0, 1) if index == 6 else (1, 0)) for index in range(1, 7)]
    for index, row in enumerate(rows):
        row.update(
            similarity=0.95 - index * 0.01, confidence=1.0, trust_score=0.5, last_accessed_at=now
        )
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["production", "evaluation"])
async def test_shared_prompt_path_changes_membership_without_extra_io_or_pool_expansion(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    store, _, embed, passes, touches = _mock_runtime(monkeypatch, _search_rows())
    scope = [uuid.UUID(int=200)]

    async def context() -> str:
        if entrypoint == "production":
            return await injection.build_memory_context(store, uuid.UUID(int=101))
        inputs = await injection.gather_memory_context_inputs(
            store,
            user_id=uuid.UUID(int=100),
            pipeline="cloud",
            query_text="Synthetic question",
            allowed_source_conversation_ids=scope,
            record_side_effects=False,
        )
        return injection.render_memory_context(inputs)

    monkeypatch.setattr(retrieval, "DIVERSITY_PENALTY", 0.0)
    baseline = await context()
    monkeypatch.setattr(retrieval, "DIVERSITY_PENALTY", 0.10)
    candidate_context = await context()
    assert "Synthetic fact 5" in baseline and "Synthetic fact 6" not in baseline
    assert "Synthetic fact 6" in candidate_context and "Synthetic fact 5" not in candidate_context
    assert baseline.count("- Fact:") == candidate_context.count("- Fact:") == 5
    assert embed.await_count == passes.await_count == 2
    for call in store.search_memories.await_args_list + store.search_memories_bm25.await_args_list:
        assert call.kwargs["limit"] == 10
        assert call.kwargs["include_local"] is False
        assert call.kwargs["include_historical"] is False
        assert call.kwargs["source_conversation_ids"] == (
            scope if entrypoint == "evaluation" else None
        )
    assert touches.call_count == (2 if entrypoint == "production" else 0)
    store.log_retrieval.assert_not_called()


@pytest.mark.asyncio
async def test_lexical_pass_is_unchanged_even_when_rows_have_vectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _search_rows()
    store, _, embed, passes, _ = _mock_runtime(monkeypatch, [])
    store.search_memories_bm25.return_value = [
        dict(row, bm25_score=10 - index) for index, row in enumerate(rows)
    ]
    monkeypatch.setattr(retrieval, "DIVERSITY_PENALTY", 0.0)
    baseline = await retrieval.retrieve_memories(
        store, [], query_text="Synthetic question", user_id=uuid.UUID(int=100), touch_results=False
    )
    monkeypatch.setattr(retrieval, "DIVERSITY_PENALTY", 0.10)
    result = await retrieval.retrieve_memories(
        store, [], query_text="Synthetic question", user_id=uuid.UUID(int=100), touch_results=False
    )
    assert result == baseline
    assert len(result) == 5 and result[-1]["id"] == uuid.UUID(int=5)
    store.search_memories.assert_not_called()
    embed.assert_not_called()
    passes.assert_not_called()


@pytest.mark.asyncio
async def test_score_floor_and_candidate_limits_are_not_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _search_rows()
    rows[-1]["similarity"] = 0.01  # orthogonal but below the unchanged relevance floor
    store, _, _, _, _ = _mock_runtime(monkeypatch, rows)
    result = await retrieval.retrieve_memories(
        store, [1.0, 0.0], user_id=uuid.UUID(int=100), touch_results=False
    )
    assert [row["id"] for row in result] == [uuid.UUID(int=index) for index in range(1, 6)]
    assert retrieval.MIN_FINAL_SCORE == 0.15 and retrieval.INITIAL_VECTOR_CANDIDATES == 10
    assert retrieval.DIVERSITY_PENALTY == 0.10


@pytest.mark.asyncio
async def test_outer_merge_does_not_expand_the_selected_per_space_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _search_rows()[:3]
    rows[2]["embedding"] = (0.0, 1.0)
    other_space = "synthetic:other-document-space:2"
    other_rows = [
        dict(
            row,
            id=uuid.UUID(int=index + 10),
            embedding_model=other_space,
            similarity=0.8 - index * 0.01,
        )
        for index, row in enumerate(rows[:2])
    ]
    store, primary, _, passes, _ = _mock_runtime(monkeypatch, rows)
    passes.return_value = [
        primary,
        EmbeddingVectorResult(
            embedding=[1.0, 0.0],
            provider="synthetic",
            model="other-query",
            storage_model=other_space,
        ),
    ]

    async def search(**kwargs: object):
        return rows if kwargs["embedding_model"] == SPACE else other_rows

    store.search_memories.side_effect = search
    result = await retrieval.retrieve_memories_for_text(
        store,
        "Synthetic question",
        user_id=uuid.UUID(int=100),
        query_embedding=primary.embedding,
        storage_embedding_model=SPACE,
        query_embedding_model="query",
        limit=2,
        touch_results=False,
    )
    # Row 2 has a higher raw score than row 3 but was not selected in its pass.
    # The existing outer relevance sort must not reintroduce it from scored diagnostics.
    assert [row["id"] for row in result] == [rows[0]["id"], rows[2]["id"]]
    assert [call.kwargs["embedding_model"] for call in store.search_memories.await_args_list] == [
        SPACE,
        other_space,
    ]
