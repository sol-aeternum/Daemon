from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast


REPORT_PATH = Path(__file__).with_name("PORTABLE_ADVANTAGES.md")


def _load_report_text() -> str:
    return REPORT_PATH.read_text()


def _load_machine_summary() -> dict[str, object]:
    text = _load_report_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "PORTABLE_ADVANTAGES.md is missing its JSON summary"
    return cast(dict[str, object], json.loads(match.group(1)))


def _candidate(summary: dict[str, object], name: str) -> dict[str, object]:
    portable_candidate_review_raw = summary["portable_candidate_review"]
    assert isinstance(portable_candidate_review_raw, list)
    portable_candidate_review = cast(list[object], portable_candidate_review_raw)

    for item_obj in portable_candidate_review:
        assert isinstance(item_obj, dict)
        item = cast(dict[str, object], item_obj)
        if item["name"] == name:
            return item
    raise AssertionError(f"Missing candidate summary for {name}")


def test_portable_advantages_scope_excludes_non_portable_explanations() -> None:
    text = _load_report_text()
    summary = _load_machine_summary()

    assert "No clean portable historical advantages survive subtraction." in text
    assert summary["headline_gap_vs_clean"] == 0.133
    assert summary["historical_weighted_over_strict"] == 0.189
    assert summary["historical_strict_vs_current_clean"] == -0.056
    assert summary["residual_portable_advantages"] == []
    assert summary["excluded_non_portable_explanations"] == [
        "judge_drift_and_bundle_leniency",
        "contamination_vectors",
        "split_provenance",
        "historical_fixture_quirks",
    ]


def test_portable_advantages_scope_marks_reviewed_knobs_as_skips() -> None:
    summary = _load_machine_summary()

    fast_chunking = _candidate(summary, "fast_chunking")
    assert fast_chunking["status"] == "skip_noop"
    assert fast_chunking["historical_value"] == {
        "chunk_max_chars": 4000,
        "overlap_turns": 2,
    }
    assert fast_chunking["current_value"] == {
        "chunk_max_chars": 2000,
        "overlap_turns": 0,
    }

    top_k = _candidate(summary, "top_k_memories")
    assert top_k["status"] == "skip_noop"
    assert top_k["historical_value"] == 5
    assert top_k["current_value"] == 10
    assert top_k["expected_lift_if_ported_today"] == "negative"

    embeddings = _candidate(summary, "embedding_models")
    assert embeddings["status"] == "skip_noop"
    assert embeddings["historical_value"] == embeddings["current_value"]

    ranking = _candidate(summary, "retrieval_ranking_defaults")
    assert ranking["status"] == "skip_unproven"
    assert ranking["historical_value"] == "no_preserved_benchmark_specific_delta"

    dedup = _candidate(summary, "dedup_thresholds")
    assert dedup["status"] == "skip_not_applicable"
    assert dedup["historical_value"] == "fast_artifact_bypassed_dedup"
