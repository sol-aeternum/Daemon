from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast


ANALYSIS_PATH = Path("tests/benchmark_results/composition/ANALYSIS.md")


def _as_dict_list(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [cast(dict[str, object], item) for item in cast(list[object], value)]


def _as_str_list(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(item) for item in cast(list[object], value)]


def _as_int(value: object) -> int:
    assert isinstance(value, int)
    return value


def _load_summary() -> dict[str, object]:
    text = ANALYSIS_PATH.read_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "ANALYSIS.md is missing its JSON summary"
    return cast(dict[str, object], json.loads(match.group(1)))


def test_composition_candidate_eligibility_artifact_records_literal_gate() -> None:
    summary = _load_summary()
    assert summary["status"] == "blocked_insufficient_clean_candidates"
    assert summary["minimum_clean_candidates_required"] == 3
    assert summary["eligible_candidate_count"] == 0
    assert summary["composition_run_executed"] is False
    assert summary["combined_strict_score"] is None

    reviews = {
        str(item["candidate_key"]): item for item in _as_dict_list(summary["candidate_reviews"])
    }
    assert _as_str_list(reviews["top_k_memories:k06"]["subset_regressions"]) == [
        "primary_cell:knowledge-update 33.3% -> 22.2% (-11.1pp)",
        "primary_cell:temporal-reasoning 30.0% -> 10.0% (-20.0pp)",
        "target_cell:retrieval-miss x temporal-reasoning 1/5 -> 0/5 (-1)",
    ]
    assert reviews["hybrid_weights:balanced"]["rejection_reason"] == (
        "phase3_closed_non_promotable_primary_target_flat"
    )
    assert reviews["dedup_thresholds:tight_01"]["work_order_status"] == (
        "blocked_insufficient_target_cell"
    )


def test_composition_candidate_eligibility_allows_truthful_underfilled_stop() -> None:
    summary = _load_summary()
    minimum_required = _as_int(summary["minimum_clean_candidates_required"])
    eligible_keys = _as_str_list(summary["eligible_candidate_keys"])
    candidate_reviews = _as_dict_list(summary["candidate_reviews"])

    flagged_eligible_keys = sorted(
        str(review["candidate_key"])
        for review in candidate_reviews
        if bool(review["eligible_for_composition"])
    )
    assert flagged_eligible_keys == sorted(eligible_keys)
    assert len(eligible_keys) == _as_int(summary["eligible_candidate_count"])

    for review in candidate_reviews:
        candidate_key = str(review["candidate_key"])
        subset_regressions = _as_str_list(review["subset_regressions"])
        if candidate_key in eligible_keys:
            assert review["meets_lift_gate"] is True, candidate_key
            assert subset_regressions == [], candidate_key

    if len(eligible_keys) < minimum_required:
        assert summary["status"] == "blocked_insufficient_clean_candidates"
        assert summary["composition_run_executed"] is False
        assert summary["combined_strict_score"] is None
        assert summary["sum_individual_lifts_pp"] is None
        assert summary["combined_lift_pp"] is None
        assert summary["additivity_ratio"] is None
        assert "Fewer than three clean" in str(summary["blocked_reason"])
    else:
        assert summary["composition_run_executed"] is True
