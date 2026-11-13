from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportPrivateUsage=false

import uuid
from typing import Any, final
from unittest.mock import AsyncMock

import pytest

from orchestrator.skill_evaluator import (
    SKILL_EVALUATION_MATCH_THRESHOLD,
    SkillDraft,
    SkillEvaluator,
    _build_dedup_query_text,
    _normalize_skill_name,
    _skill_name_match_rank,
)


@final
class StubStore:
    async def get_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        raise AssertionError("get_messages should not be called in dedup unit tests")

    async def get_conversation(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        del args, kwargs
        raise AssertionError("get_conversation should not be called in dedup unit tests")


USER_ID = uuid.uuid4()


@final
class StubSkillStore:
    def __init__(self, matches: list[dict[str, Any]]) -> None:
        self.search_by_embedding = AsyncMock(return_value=matches)


def _make_evaluator(
    matches: list[dict[str, Any]],
    *,
    query_embedding: list[float] | None = None,
) -> tuple[SkillEvaluator, StubSkillStore, AsyncMock]:
    skill_store = StubSkillStore(matches)
    query_embedder = AsyncMock(return_value=query_embedding or [0.1, 0.2])
    evaluator = SkillEvaluator(
        store=StubStore(),
        db_pool=None,
        skill_store=skill_store,  # type: ignore[arg-type]
        query_embedder=query_embedder,
    )
    return evaluator, skill_store, query_embedder


def _build_draft(name: str = "Debug Workflow") -> SkillDraft:
    return SkillDraft(
        name=name,
        description="Investigate repo bugs using a repeatable workflow.",
        trigger_conditions="Use when a bug requires repo tracing and verification.",
        skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable steps.",
    )


def test_normalize_skill_name_collapses_punctuation_and_whitespace() -> None:
    assert _normalize_skill_name("  Debug_Workflow!!!  ") == "debug workflow"
    assert _normalize_skill_name("Debug---Workflow") == "debug workflow"


def test_skill_name_match_rank_requires_name_alignment() -> None:
    assert _skill_name_match_rank("Debug Workflow!!!", "debug workflow") == 2
    assert _skill_name_match_rank("Workflow Debug", "debug workflow") == 1
    assert _skill_name_match_rank("Worker Repair", "debug workflow") == 0


@pytest.mark.asyncio
async def test_find_best_match_ignores_semantic_overlap_without_name_match() -> None:
    draft = _build_draft()
    evaluator, skill_store, query_embedder = _make_evaluator(
        [
            {
                "slug": "worker-repair",
                "name": "Worker Repair",
                "source_type": "autonomous",
                "similarity": 0.97,
            }
        ],
        query_embedding=[0.25, 0.75],
    )

    match = await evaluator._find_best_match(USER_ID, draft)

    assert match is None
    query_embedder.assert_awaited_once_with(_build_dedup_query_text(draft))
    # Only the account of the evaluated turn is searched.
    skill_store.search_by_embedding.assert_awaited_once_with(
        USER_ID,
        [0.25, 0.75],
        limit=5,
        min_similarity=SKILL_EVALUATION_MATCH_THRESHOLD,
    )


@pytest.mark.asyncio
async def test_find_best_match_prefers_exact_normalized_name_over_word_set_match() -> None:
    draft = _build_draft()
    evaluator, _, _ = _make_evaluator(
        [
            {
                "slug": "same-words-higher-similarity",
                "name": "Workflow Debug",
                "source_type": "autonomous",
                "similarity": 0.99,
            },
            {
                "slug": "exact-normalized-name",
                "name": "Debug Workflow!!!",
                "source_type": "autonomous",
                "similarity": 0.86,
            },
        ]
    )

    match = await evaluator._find_best_match(USER_ID, draft)

    assert match is not None
    assert match["slug"] == "exact-normalized-name"
    assert match["name"] == "Debug Workflow!!!"


@pytest.mark.asyncio
async def test_find_best_match_uses_similarity_to_break_name_rank_ties() -> None:
    draft = _build_draft()
    evaluator, _, _ = _make_evaluator(
        [
            {
                "slug": "lower-similarity",
                "name": "debug workflow",
                "source_type": "autonomous",
                "similarity": 0.86,
            },
            {
                "slug": "higher-similarity",
                "name": "Debug Workflow!!!",
                "source_type": "autonomous",
                "similarity": 0.93,
            },
        ]
    )

    match = await evaluator._find_best_match(USER_ID, draft)

    assert match is not None
    assert match["slug"] == "higher-similarity"
    assert match["similarity"] == 0.93
