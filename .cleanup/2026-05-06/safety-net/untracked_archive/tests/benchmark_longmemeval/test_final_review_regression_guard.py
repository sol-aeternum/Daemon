from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast


FINAL_REVIEW_PATH = Path(__file__).with_name("FINAL_REVIEW.md")


def _as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _as_dict_list(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [cast(dict[str, object], item) for item in cast(list[object], value)]


def _as_str_list(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(item) for item in cast(list[object], value)]


def _load_text() -> str:
    return FINAL_REVIEW_PATH.read_text()


def _load_summary() -> dict[str, object]:
    text = _load_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "FINAL_REVIEW.md is missing its JSON summary"
    return cast(dict[str, object], json.loads(match.group(1)))


def test_final_review_regression_guard_approves_truthful_no_composition() -> None:
    assert FINAL_REVIEW_PATH.is_file()

    text = _load_text()
    summary = _load_summary()
    checks = _as_dict(summary["explainability_checks"])

    assert "Oracle approves the `no_shippable_composition` outcome." in text
    assert "invent a combined benchmark score" in text

    assert summary["status"] == "approved_no_shippable_composition"
    assert summary["oracle_verdict"] == "approve_no_composition"
    assert summary["review_target"] == "no_shippable_composition"
    assert summary["composition_run_executed"] is False
    assert summary["full_corpus_triple_run_executed"] is False
    assert summary["clean_historical_advantages_available"] is False
    assert summary["blocking_concerns"] == []

    assert checks["all_measured_lifts_accounted_for"] is True
    assert checks["unexplained_lifts"] == []
    assert checks["contamination_like_behavior_shipped"] is False
    assert checks["shipped_change_exists"] is False
    assert _as_str_list(checks["approval_basis"]) == [
        "4b_zero_clean_candidates",
        "4c_truthful_skip_without_composition",
        "4d_no_shippable_composition_closeout",
        "portable_advantages_none",
        "judge_drift_two_sided",
        "artifact_trust_contaminated_81_1",
    ]


def test_final_review_regression_guard_accounts_for_subset_regressions() -> None:
    summary = _load_summary()
    analyses = {
        str(item["candidate_key"]): item
        for item in _as_dict_list(summary["subset_regression_analysis"])
    }

    assert set(analyses) == {
        "top_k_memories:k06",
        "hybrid_weights:balanced",
        "min_final_score:score_0.05",
        "dedup_thresholds:tight_01",
        "temporal_filter:on",
        "abstention_guardrail:on",
    }

    assert analyses["top_k_memories:k06"]["ship_decision"] == "blocked_subset_regression"
    assert _as_str_list(analyses["top_k_memories:k06"]["subset_regressions"]) == [
        "primary_cell:knowledge-update 33.3% -> 22.2% (-11.1pp)",
        "primary_cell:temporal-reasoning 30.0% -> 10.0% (-20.0pp)",
        "target_cell:retrieval-miss x temporal-reasoning 1/5 -> 0/5 (-1)",
    ]

    assert analyses["hybrid_weights:balanced"]["ship_decision"] == "blocked_primary_target_flat"
    assert analyses["hybrid_weights:balanced"]["target_cell_result"] == (
        "retrieval-miss x multi-session 1/6 -> 1/6"
    )
    assert _as_str_list(analyses["hybrid_weights:balanced"]["subset_regressions"]) == []

    assert analyses["min_final_score:score_0.05"]["ship_decision"] == "blocked_subset_regression"
    assert _as_str_list(analyses["min_final_score:score_0.05"]["subset_regressions"]) == [
        "protected_cell:single-session-user 33.3% -> 22.2% (-11.1pp)",
        "protected_cell:multi-session 30.0% -> 20.0% (-10.0pp)",
        "target_cell:retrieval-miss x multi-session 1/6 -> 0/6 (-1)",
        "target_cell:retrieval-miss x single-session-user 1/6 -> 0/6 (-1)",
    ]

    assert analyses["dedup_thresholds:tight_01"]["ship_decision"] == (
        "blocked_insufficient_target_cell_and_subset_regression"
    )
    assert analyses["dedup_thresholds:tight_01"]["target_cell_result"] == (
        "generation-error x knowledge-update 1/2 -> 1/2"
    )
    assert _as_str_list(analyses["dedup_thresholds:tight_01"]["subset_regressions"]) == [
        "protected_cell:single-session-assistant 44.4% -> 33.3% (-11.1pp)"
    ]

    assert analyses["temporal_filter:on"]["ship_decision"] == "failed_lift_gate"
    assert analyses["temporal_filter:on"]["headline_lift_pp"] == 0.0
    assert _as_str_list(analyses["temporal_filter:on"]["subset_regressions"]) == [
        "locked_failure_union 3/39 -> 2/39 (-1)"
    ]

    assert analyses["abstention_guardrail:on"]["ship_decision"] == "backed_out_subset_regression"
    assert analyses["abstention_guardrail:on"]["headline_lift_pp"] == -6.0
    assert _as_str_list(analyses["abstention_guardrail:on"]["subset_regressions"]) == [
        "protected_cell:single-session-assistant 44.4% -> 0.0% (-44.4pp)",
        "protected_cell:knowledge-update 33.3% -> 22.2% (-11.1pp)",
    ]
