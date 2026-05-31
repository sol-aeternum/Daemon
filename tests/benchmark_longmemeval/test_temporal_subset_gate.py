from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


MANIFEST_PATH = Path("tests/benchmark_results/dev_sweep_temporal/sweep_manifest.json")


def _load_manifest() -> dict[str, Any]:
    payload = json.loads(MANIFEST_PATH.read_text())
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _run_by_name(manifest: dict[str, Any], run_name: str) -> dict[str, Any]:
    runs = cast(list[dict[str, Any]], manifest["runs"])
    for run in runs:
        if run["run_name"] == run_name:
            return run
    raise AssertionError(f"Missing run {run_name}")


def test_temporal_subset_gate_artifact_is_consistent() -> None:
    manifest = _load_manifest()
    off = _run_by_name(manifest, "off")
    on = _run_by_name(manifest, "on")

    promotion_gate = cast(dict[str, Any], manifest["promotion_gate"])
    target = cast(dict[str, Any], manifest["target_failure_cell"])
    comparison = cast(dict[str, Any], manifest["comparison"])

    assert cast(dict[str, Any], manifest["shared_overrides"])["top_k_memories"] == 6
    assert target["stage"] == "retrieval-miss"
    assert target["category"] == "temporal-reasoning"
    assert target["count"] == 5
    assert promotion_gate["subset_veto_min_locked_cases"] == 5
    assert promotion_gate["subset_veto_pass"] is True

    assert off["temporal_filter_enabled"] is False
    assert on["temporal_filter_enabled"] is True
    assert comparison["strict_accuracy_delta_on_minus_off"] == (
        on["strict_accuracy"] - off["strict_accuracy"]
    )
    assert comparison["locked_failure_union_delta_on_minus_off"] == (
        on["subset_deltas"]["locked_failure_union"]["sweep_correct"]
        - off["subset_deltas"]["locked_failure_union"]["sweep_correct"]
    )
    assert comparison["retrieval_miss_temporal_reasoning_delta_on_minus_off"] == (
        on["subset_deltas"]["retrieval_miss_temporal_reasoning"]["sweep_correct"]
        - off["subset_deltas"]["retrieval_miss_temporal_reasoning"]["sweep_correct"]
    )

    assert comparison["eligible_for_full_corpus_promotion"] is False
    assert "should not advance to full-corpus promotion yet" in comparison["promotion_reason"]
