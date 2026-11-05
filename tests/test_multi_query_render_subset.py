"""Baseline render-subset property under the additive multi-query merge (#601).

Replaces the dev-50 coverage gate in the owner's M2 pre-registration, so it must be
able to fail: for the same render budget, every retrieved row the baseline renders
must also be rendered by the candidate produced by ``merge_memories`` with the
production ``retrieved_limit``. A negative control proves the property is testable.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any, cast

import pytest

from orchestrator.memory.injection import (
    MAX_MEMORY_ITEMS,
    MemoryContextInputs,
    render_memory_context,
    rendered_memory_rows,
)
from orchestrator.memory.multi_query import merge_memories

# Very small (nothing renders) through large enough that no trimming happens.
BUDGETS = (1, 5, 12, 25, 40, 60, 90, 140, 220, 400, 900, 2500, 20000)
BASELINE_SIZES = (0, 1, 3, 5)
ARM_COUNTS = (0, 1, 2, 3)
CONTENT_MODES = ("short", "long", "empty", "mixed")
SUMMARY_FLAGS = (False, True)
L0_FLAGS = (False, True)

L0_IDS = (9001, 9002)
SUMMARY_IDS = (9501, 9502)


def _memory(mid: int, content: str, category: str = "fact") -> dict[str, Any]:
    return {"id": uuid.UUID(int=mid), "content": content, "category": category}


def _content(index: int, mode: str) -> str:
    if mode == "empty":
        return ""
    if mode == "long":
        return f"Long content {index} " + "detail " * 120
    if mode == "mixed":
        return "" if index % 3 == 0 else f"Mixed content {index} " + "detail " * 20
    return f"Short content {index}"


def _cases():
    return list(
        itertools.product(
            BASELINE_SIZES,
            ARM_COUNTS,
            CONTENT_MODES,
            SUMMARY_FLAGS,
            L0_FLAGS,
            BUDGETS,
        )
    )


def _build_inputs(
    baseline_n: int,
    arms_n: int,
    content_mode: str,
    with_summary: bool,
    with_l0: bool,
) -> tuple[MemoryContextInputs, MemoryContextInputs]:
    baseline = [_memory(1000 + i, _content(i, content_mode)) for i in range(baseline_n)]
    arms: list[list[dict[str, Any]]] = []
    for arm_index in range(arms_n):
        rows: list[dict[str, Any]] = []
        if baseline and arm_index == 0:
            # A duplicate baseline ID must be dropped, never shifted.
            rows.append(baseline[0])
        rows.extend(
            _memory(
                2000 + arm_index * 10 + offset,
                _content(500 + arm_index * 10 + offset, content_mode),
                "preference",
            )
            for offset in range(2)
        )
        arms.append(rows)
    merged = merge_memories(baseline, arms, quota=3, mode="additive", evaluation=False)
    l0 = [
        _memory(mid, f"Frozen fact {mid - L0_IDS[0]}", "summary") for mid in L0_IDS[:2] if with_l0
    ]
    summaries: list[dict[str, Any]] = (
        [
            {"id": uuid.UUID(int=mid), "content": f"Session summary {mid - SUMMARY_IDS[0]}"}
            for mid in SUMMARY_IDS[:2]
        ]
        if with_summary
        else []
    )
    baseline_inputs = MemoryContextInputs(l0_memories=l0, retrieved=baseline, summaries=summaries)
    candidate_inputs = MemoryContextInputs(
        l0_memories=l0,
        retrieved=merged,
        summaries=summaries,
        retrieved_limit=len(merged) if len(merged) > MAX_MEMORY_ITEMS else None,
    )
    return baseline_inputs, candidate_inputs


def _rendered_ids(inputs: MemoryContextInputs, budget: int) -> list[uuid.UUID]:
    block = render_memory_context(inputs, budget)
    return [cast(uuid.UUID, row["id"]) for row in rendered_memory_rows(inputs, block)]


def _retrieved_rendered_ids(inputs: MemoryContextInputs, budget: int) -> list[uuid.UUID]:
    retrieved = {row["id"] for row in inputs.retrieved}
    return [mid for mid in _rendered_ids(inputs, budget) if mid in retrieved]


@pytest.mark.parametrize(
    ("baseline_n", "arms_n", "content_mode", "with_summary", "with_l0", "budget"), _cases()
)
def test_baseline_rows_survive_the_additive_merge_at_the_same_budget(
    baseline_n: int,
    arms_n: int,
    content_mode: str,
    with_summary: bool,
    with_l0: bool,
    budget: int,
) -> None:
    baseline_inputs, candidate_inputs = _build_inputs(
        baseline_n, arms_n, content_mode, with_summary, with_l0
    )

    baseline_rendered = _retrieved_rendered_ids(baseline_inputs, budget)
    candidate_rendered = _retrieved_rendered_ids(candidate_inputs, budget)

    missing = [mid for mid in baseline_rendered if mid not in set(candidate_rendered)]
    assert not missing, (
        f"candidate dropped baseline rows {missing} at budget {budget} "
        f"(baseline={baseline_rendered}, candidate={candidate_rendered})"
    )


def test_cases_really_exercise_trimming() -> None:
    """A green suite must mean the property was actually exercised, not vacuous."""
    nothing_rendered = False
    baseline_trimmed = False
    candidate_trimmed = False
    candidate_trimmed_inside_added = False

    for baseline_n, arms_n, content_mode, with_summary, with_l0, budget in _cases():
        baseline_inputs, candidate_inputs = _build_inputs(
            baseline_n, arms_n, content_mode, with_summary, with_l0
        )
        rendered_rows = rendered_memory_rows(
            candidate_inputs, render_memory_context(candidate_inputs, budget)
        )
        if not rendered_rows:
            nothing_rendered = True

        if content_mode in {"short", "long"}:
            baseline_rendered = _retrieved_rendered_ids(baseline_inputs, budget)
            candidate_rendered = _retrieved_rendered_ids(candidate_inputs, budget)
            baseline_given = min(len(baseline_inputs.retrieved), MAX_MEMORY_ITEMS)
            candidate_given = min(
                len(candidate_inputs.retrieved),
                candidate_inputs.retrieved_limit or MAX_MEMORY_ITEMS,
            )
            if 0 < len(baseline_rendered) < baseline_given:
                baseline_trimmed = True
            if 0 < len(candidate_rendered) < candidate_given:
                candidate_trimmed = True
            if (
                candidate_trimmed
                and len(candidate_rendered) > len(baseline_rendered)
                and len(candidate_inputs.retrieved) > len(baseline_inputs.retrieved)
            ):
                candidate_trimmed_inside_added = True

    assert nothing_rendered, "no case rendered nothing; the tiny budgets are not tiny"
    assert baseline_trimmed, "no baseline case was trimmed; budgets never land mid-baseline"
    assert candidate_trimmed, "no candidate rendered fewer rows than it was given"
    assert candidate_trimmed_inside_added, "no case trimmed after the baseline rows"


def test_negative_control_added_row_before_baseline_violates_the_property() -> None:
    """Proves the subset assertion can fail: an added-first merge must break it."""
    violations: list[tuple[int, int, int]] = []
    for baseline_n, arms_n, content_mode, with_summary, with_l0, budget in _cases():
        if arms_n == 0 or baseline_n == 0:
            continue
        baseline_inputs, candidate_inputs = _build_inputs(
            baseline_n, arms_n, content_mode, with_summary, with_l0
        )
        added = [
            row
            for row in candidate_inputs.retrieved
            if row["id"] not in {base["id"] for base in baseline_inputs.retrieved}
        ]
        if not added:
            continue
        reordered = [added[0], *(row for row in candidate_inputs.retrieved if row is not added[0])]
        control = MemoryContextInputs(
            l0_memories=candidate_inputs.l0_memories,
            retrieved=reordered,
            summaries=candidate_inputs.summaries,
            retrieved_limit=len(reordered) if len(reordered) > MAX_MEMORY_ITEMS else None,
        )
        baseline_rendered = _retrieved_rendered_ids(baseline_inputs, budget)
        control_rendered = set(_retrieved_rendered_ids(control, budget))
        if any(mid not in control_rendered for mid in baseline_rendered):
            violations.append((baseline_n, arms_n, budget))

    assert violations, "an added-first merge never violated the subset property"
