from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast


PORTABLE_REPORT_PATH = Path(__file__).with_name("PORTABLE_ADVANTAGES.md")
SKIPPED_PATH = Path("tests/benchmark_results/dev_sweep_portable/SKIPPED.md")


def _as_dict_list(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [cast(dict[str, object], item) for item in cast(list[object], value)]


def _as_str_list(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(item) for item in cast(list[object], value)]


def _load_skipped_text() -> str:
    return SKIPPED_PATH.read_text()


def _load_skipped_summary() -> dict[str, object]:
    text = _load_skipped_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "SKIPPED.md is missing its JSON summary"
    return cast(dict[str, object], json.loads(match.group(1)))


def _load_portable_summary() -> dict[str, object]:
    text = PORTABLE_REPORT_PATH.read_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "PORTABLE_ADVANTAGES.md is missing its JSON summary"
    return cast(dict[str, object], json.loads(match.group(1)))


def _portable_candidate_names(summary: dict[str, object]) -> set[str]:
    names: set[str] = set()
    for item in _as_dict_list(summary["portable_candidate_review"]):
        names.add(str(item["name"]))
    return names


def _skipped_candidate_statuses(summary: dict[str, object]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for item in _as_dict_list(summary["portable_history_candidates"]):
        statuses[str(item["name"])] = str(item["status"])
    return statuses


def test_portable_unique_knobs_skip_artifact_exists_and_records_no_rerun() -> None:
    assert SKIPPED_PATH.is_file()

    text = _load_skipped_text()
    summary = _load_skipped_summary()

    assert "Implementation was intentionally skipped after the required portability audit" in text
    assert (
        "Distinct portable historical candidates remaining after 3a-3g reconciliation: **none**."
        in text
    )
    assert "Benchmark rerun executed: **no**." in text
    assert summary["status"] == "skipped_no_distinct_portable_candidate"
    assert summary["distinct_portable_candidate_remaining"] is False
    assert summary["benchmark_rerun_executed"] is False
    assert summary["distinct_survivors"] == []


def test_portable_unique_knobs_reconciles_all_portable_history_candidates() -> None:
    portable_summary = _load_portable_summary()
    skipped_summary = _load_skipped_summary()

    assert portable_summary["residual_portable_advantages"] == []

    portable_names = _portable_candidate_names(portable_summary)
    assert portable_names == {
        "fast_chunking",
        "top_k_memories",
        "embedding_models",
        "retrieval_ranking_defaults",
        "dedup_thresholds",
    }

    skipped_statuses = _skipped_candidate_statuses(skipped_summary)
    assert skipped_statuses == {
        "historical_fast_chunking_4000_2": "vetoed_negative_replay",
        "historical_top_k_5_restore": "vetoed_negative_replay",
        "embedding_route_changes": "vetoed_parity_no_delta",
        "retrieval_ranking_defaults": "vetoed_no_preserved_historical_delta",
        "dedup_thresholds": "vetoed_not_applicable_to_fast_artifact",
    }


def test_portable_unique_knobs_references_completed_phase3_reconciliation_artifacts() -> None:
    text = _load_skipped_text()
    summary = _load_skipped_summary()

    artifacts = _as_str_list(summary["phase3_reconciliation_artifacts"])
    assert artifacts == [
        "dev_sweep_max_returned",
        "dev_sweep_temporal",
        "dev_sweep_alias",
        "dev_sweep_abstention",
        "dev_sweep_weights",
        "dev_sweep_min_score",
        "dev_sweep_dedup",
    ]

    for artifact in artifacts:
        if artifact == "dev_sweep_alias":
            analysis_path = Path("tests/benchmark_results") / artifact / "SKIPPED.md"
        else:
            analysis_path = Path("tests/benchmark_results") / artifact / "ANALYSIS.md"
        assert analysis_path.is_file(), artifact
        assert artifact in text
