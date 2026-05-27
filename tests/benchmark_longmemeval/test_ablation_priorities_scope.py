from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import cast

from tests.benchmark_longmemeval.taxonomy import build_taxonomy_entries, load_failure_rows


REPORT_PATH = Path(__file__).with_name("ABLATION_PRIORITIES.md")


def _load_report_text() -> str:
    return REPORT_PATH.read_text()


def _load_machine_summary() -> dict[str, object]:
    text = _load_report_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "ABLATION_PRIORITIES.md is missing its JSON summary"
    return cast(dict[str, object], json.loads(match.group(1)))


def _as_int(value: object) -> int:
    assert isinstance(value, int)
    return value


def _target_cell_counts() -> Counter[tuple[str, str]]:
    entries = build_taxonomy_entries(failure_rows=load_failure_rows())
    return Counter((str(entry["stage"]), str(entry["category"])) for entry in entries)


def test_ablation_priorities_scope_is_taxonomy_and_portability_bound() -> None:
    text = _load_report_text().lower()
    summary = _load_machine_summary()
    cell_counts = _target_cell_counts()

    assert "target failure cell" in text
    assert "expected direction" in text
    assert "expected magnitude" in text
    assert "implementation cost" in text
    assert "overlap notes" in text

    assert summary["baseline_failure_total"] == 39
    assert summary["promotion_gate_min_target_cell_count"] == 5

    portability_constraints = cast(dict[str, object], summary["portability_constraints"])
    assert portability_constraints["no_model_swaps"] is True
    assert portability_constraints["no_clean_portable_historical_advantages"] is True

    excluded_raw = cast(list[object], summary["excluded_from_ranked_queue"])
    excluded = [cast(dict[str, object], item) for item in excluded_raw]
    excluded_names = {str(item["name"]) for item in excluded}
    assert {
        "historical_fast_chunking_4000_2",
        "historical_top_k_5_restore",
        "MAX_RETURNED_MEMORIES_literal_sweep",
        "answer_or_judge_model_swaps",
        "portable_advantages_phase2_replay",
    } <= excluded_names

    ranked_raw = cast(list[object], summary["ranked_candidates"])
    ranked = [cast(dict[str, object], item) for item in ranked_raw]
    assert 5 <= len(ranked) <= 10
    assert [_as_int(item["rank"]) for item in ranked] == list(range(1, len(ranked) + 1))
    assert len({str(item["name"]) for item in ranked}) == len(ranked)

    allowed_directions = {"positive", "positive_if_distinct_gap_exists"}
    allowed_magnitudes = {"small", "small-medium", "medium"}
    allowed_costs = {"low", "low-medium", "medium"}

    for candidate in ranked:
        target = cast(dict[str, object], candidate["target_failure_cell"])
        stage = str(target["stage"])
        category = str(target["category"])
        count = _as_int(target["count"])

        assert cell_counts[(stage, category)] == count, (
            f"Target cell count mismatch for {candidate['name']}: "
            f"summary={count}, taxonomy={cell_counts[(stage, category)]}"
        )
        assert count > 0
        representative_ids = cast(list[object], target["representative_ids"])
        assert representative_ids, f"Missing representative IDs for {candidate['name']}"

        assert str(candidate["expected_direction"]) in allowed_directions
        assert str(candidate["expected_magnitude"]) in allowed_magnitudes
        assert str(candidate["implementation_cost"]) in allowed_costs
        assert str(candidate["overlap_notes"]).strip()
        assert str(candidate["taxonomy_basis"]).strip()
        assert str(candidate["portability_basis"]).strip()

        promotion_status = str(candidate["promotion_status"])
        if count < _as_int(summary["promotion_gate_min_target_cell_count"]):
            assert promotion_status.startswith("blocked_"), (
                f"Sub-threshold target cell must stay blocked: {candidate['name']}"
            )
