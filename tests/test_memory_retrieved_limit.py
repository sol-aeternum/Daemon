"""Offline coverage for the default-preserving M1 renderer width seam."""

from __future__ import annotations

import itertools
import uuid
from dataclasses import replace
from typing import Any

import pytest

from orchestrator.eval import exposure
from orchestrator.memory import injection
from tests.test_memory_context_parity import (
    CONVERSATION_ID,
    L0_SETS,
    RETRIEVED_SETS,
    SUMMARY_SETS,
    USER_ID,
    RecordingStore,
    _frozen_reference,
    _install_collaborators,
)


def _rows(count: int) -> list[dict[str, object]]:
    return [
        {"id": uuid.UUID(int=index + 1), "content": f"Fictional fact {index}"}
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_none_matches_goldens_across_all_renderer_fixtures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare explicit None against the independent, pre-split frozen formatter."""
    reference = _frozen_reference()
    for l0, retrieved, summaries, budget in itertools.product(
        L0_SETS.values(),
        RETRIEVED_SETS.values(),
        SUMMARY_SETS.values(),
        [injection.DEFAULT_MAX_TOKENS, 40],
    ):
        _install_collaborators(monkeypatch, route="", embedding="ok", retrieved=retrieved)
        store = RecordingStore(
            conversation={"user_id": USER_ID, "pipeline": "cloud"},
            l0=l0,
            messages=[{"role": "user", "content": "Synthetic question"}],
            summaries=summaries,
        )
        expected = await reference(store, CONVERSATION_ID, budget)
        inputs = await injection.gather_memory_context_inputs(
            store,  # type: ignore[arg-type]
            user_id=USER_ID,
            pipeline="cloud",
            conversation_id=CONVERSATION_ID,
            record_side_effects=False,
        )
        assert inputs.retrieved_limit is None
        assert injection.render_memory_context(replace(inputs, retrieved_limit=None), budget) == (
            expected
        )


@pytest.mark.parametrize("limit", [-1, 0, 1, 4, True, False, 5.0, "8"])
def test_rejects_shrinking_or_noninteger_limit(limit: Any) -> None:
    with pytest.raises(ValueError, match="retrieved_limit"):
        injection.MemoryContextInputs(retrieved_limit=limit)


@pytest.mark.parametrize("limit", [None, 5, 8, 14])
def test_accepts_nonshrinking_limit(limit: int | None) -> None:
    assert injection.MemoryContextInputs(retrieved_limit=limit).retrieved_limit == limit


def test_explicit_eight_renders_all_and_exposure_tracks_them() -> None:
    rows = _rows(8)
    inputs = injection.MemoryContextInputs(retrieved=rows, retrieved_limit=8)
    block = injection.render_memory_context(inputs)
    assert block.count("- Fact:") == 8
    assert exposure.rendered_memory_rows(inputs, block, require_complete=True) == rows
    metrics = exposure.measure(
        inputs,
        target_ids=[str(rows[-1]["id"])],
        gold_conversation_ids=[],
        source_conversations={},
        ranking=rows,
    )
    assert metrics["target_exposed"] is True
    assert metrics["target_pre_budget"] is True
    assert metrics["effective_k"] == 8
    assert metrics["retrieved_ids"] == [str(row["id"]) for row in rows]
    assert metrics["budget_dropped_ids"] == []


def test_explicit_eight_still_obeys_budget_and_exposure_prefix() -> None:
    rows = _rows(8)
    inputs = injection.MemoryContextInputs(retrieved=rows, retrieved_limit=8)
    # Exactly the renderer's pre-fence content cost for the first three rows.
    first_three = injection.render_memory_context(injection.MemoryContextInputs(retrieved=rows[:3]))
    inner = first_three.split("\n", 1)[1].rsplit("\n", 1)[0]
    budget = injection.estimate_tokens(inner)
    block = injection.render_memory_context(inputs, budget)
    assert block == first_three
    assert exposure.rendered_memory_rows(inputs, block) == rows[:3]
    metrics = exposure.measure(
        inputs,
        target_ids=[str(rows[-1]["id"])],
        gold_conversation_ids=[],
        source_conversations={},
        ranking=rows,
        max_tokens=budget,
    )
    assert metrics["target_pre_budget"] is True
    assert metrics["target_exposed"] is False
    assert metrics["effective_k"] == 3
    assert metrics["budget_dropped_ids"] == [str(row["id"]) for row in rows[3:]]


def test_none_reads_in_process_width_at_render_time(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows(8)
    inputs = injection.MemoryContextInputs(retrieved=rows)
    assert injection.render_memory_context(inputs).count("- Fact:") == 5
    monkeypatch.setattr(injection, "MAX_MEMORY_ITEMS", 8)
    block = injection.render_memory_context(inputs)
    assert inputs.retrieved_limit is None
    assert block.count("- Fact:") == 8
    assert exposure.rendered_memory_rows(inputs, block, require_complete=True) == rows


def test_validation_uses_current_width(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(injection, "MAX_MEMORY_ITEMS", 8)
    with pytest.raises(ValueError, match=">= 8"):
        injection.MemoryContextInputs(retrieved_limit=5)
    assert injection.MemoryContextInputs(retrieved_limit=8).retrieved_limit == 8


def test_width_override_does_not_change_l0_or_labels() -> None:
    rows = _rows(8)
    rows[0]["category"] = "preference"
    inputs = injection.MemoryContextInputs(
        retrieved=rows,
        retrieved_limit=8,
        l0_memories=[{"id": uuid.UUID(int=100), "content": "Frozen fictional fact"}],
        summaries=[{"content": "Synthetic session"}],
    )
    block = injection.render_memory_context(inputs)
    assert "[FROZEN MEMORIES]\n- Frozen fictional fact" in block
    assert "- Preference: Fictional fact 0" in block
    assert "Recent context:\n- Session: Synthetic session" in block
    assert exposure.rendered_memory_rows(inputs, block, require_complete=True) == [
        *inputs.l0_memories,
        *rows,
    ]
