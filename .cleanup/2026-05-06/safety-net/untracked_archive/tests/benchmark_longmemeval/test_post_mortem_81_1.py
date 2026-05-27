from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast


POST_MORTEM_PATH = Path(__file__).with_name("POST_MORTEM_81_1.md")


def _load_text() -> str:
    return POST_MORTEM_PATH.read_text()


def _load_summary() -> dict[str, object]:
    text = _load_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "POST_MORTEM_81_1.md is missing its JSON summary"
    return cast(dict[str, object], json.loads(match.group(1)))


def test_post_mortem_states_81_1_was_inflated_and_contaminated() -> None:
    summary = _load_summary()

    assert summary["was_inflated"] is True
    assert summary["was_contaminated"] is True
    assert summary["headline_number_source"] == "summary_layer_manual_weighting"
    assert summary["committed_scorer_output"] == 0.622
    assert summary["inflation_mechanism"] == "summary_layer_weighting_adds_18.9pp_over_strict"


def test_post_mortem_states_current_baseline() -> None:
    summary = _load_summary()

    assert summary["was_real"] == "memory_retrieval_infrastructure_works_correctly"
    assert summary["current_clean_baseline_strict"] == 0.678


def test_post_mortem_names_final_outcome() -> None:
    text = _load_text()
    summary = _load_summary()

    assert "no_shippable_composition" in text
    assert summary["final_outcome"] == "no_shippable_composition"
    assert summary["oracle_verdict"] == "approved_no_shippable_composition"


def test_post_mortem_lists_process_changes() -> None:
    summary = _load_summary()

    process_changes = cast(list[object], summary["process_changes"])
    process_change_names = [str(c) for c in process_changes]

    assert "fast_lane_per_run_isolated_users" in process_change_names
    assert "canonical_allowed_source_conversation_ids_isolation" in process_change_names
    assert "question_level_fast_lane_cleanup" in process_change_names
    assert "mandatory_teardown_before_canonical_reruns" in process_change_names
    assert "no_historical_artifact_as_shipping_evidence" in process_change_names


def test_post_mortem_confirms_no_portable_historical_advantages() -> None:
    summary = _load_summary()

    assert summary["portable_historical_advantages"] == []
    assert summary["judge_drift"] == "two_sided_cannot_be_reduced_to_single_cause"


def test_post_mortem_confirms_canonical_contamination_vectors() -> None:
    summary = _load_summary()

    vectors = cast(list[object], summary["canonical_contamination_vectors_confirmed"])
    vector_names = [str(v) for v in vectors]

    assert "shared_user_persistence_without_teardown" in vector_names
    assert "legacy_evaluator_allowlist_bypass" in vector_names
