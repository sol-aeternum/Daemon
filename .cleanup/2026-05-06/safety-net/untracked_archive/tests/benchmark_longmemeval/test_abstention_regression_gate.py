from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

MANIFEST_PATH = Path("tests/benchmark_results/dev_sweep_abstention/sweep_manifest.json")


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


def _load_checkpoint(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text())
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _normalized_effective_config(checkpoint: dict[str, Any]) -> dict[str, Any]:
    effective = copy.deepcopy(checkpoint["benchmark_effective_config"])
    effective["runtime"]["output_path"] = "<output>"
    effective["runtime"]["checkpoint_path"] = "<checkpoint>"
    effective["runtime"]["score_path"] = "<score>"
    effective["pinned_authority"]["shared"]["answer"]["prompt_sha256"] = "<prompt>"
    return effective


def test_abstention_regression_gate_is_enforced() -> None:
    manifest = _load_manifest()
    off = _run_by_name(manifest, "off")
    on = _run_by_name(manifest, "on")
    comparison = cast(dict[str, Any], manifest["comparison"])
    coverage_gate = cast(dict[str, Any], manifest["coverage_gate"])

    assert Path("tests/benchmark_results/dev_sweep_abstention/ANALYSIS.md").exists()
    assert cast(dict[str, Any], manifest["shared_overrides"])["top_k_memories"] == 6
    assert coverage_gate["status"] == "blocked_insufficient_target_cell"
    assert coverage_gate["subset_veto_min_locked_cases"] == 5
    assert cast(dict[str, Any], coverage_gate["target_failure_cell"])["count"] == 2
    assert cast(dict[str, Any], coverage_gate["secondary_cell"])["count"] == 3
    assert comparison["eligible_for_full_corpus_promotion"] is False

    negative_deltas = cast(dict[str, float], comparison["negative_protected_cell_deltas"])
    if negative_deltas:
        assert comparison["recommendation"] == "back_out"
    else:
        assert comparison["recommendation"] != "back_out"


def test_abstention_sweep_changes_prompt_only_between_off_and_on() -> None:
    manifest = _load_manifest()
    off = _run_by_name(manifest, "off")
    on = _run_by_name(manifest, "on")

    off_checkpoint = _load_checkpoint(str(off["checkpoint_path"]))
    on_checkpoint = _load_checkpoint(str(on["checkpoint_path"]))

    assert off["prompt_guardrail_enabled"] is False
    assert on["prompt_guardrail_enabled"] is True
    assert off["prompt_guardrail_sha256"] != on["prompt_guardrail_sha256"]
    assert _normalized_effective_config(off_checkpoint) == _normalized_effective_config(
        on_checkpoint
    )
    assert len(off["benchmark_config_drift_warnings"]) == off["expected_drift_warning_count"]
    assert len(on["benchmark_config_drift_warnings"]) == on["expected_drift_warning_count"]
