from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import tests.longmemeval.evaluate as evaluate_module
from tests.benchmark_longmemeval.top_k_sweep import (
    MANIFEST_FILENAME,
    OUTPUT_ROOT,
    TOP_K_VALUES,
    expected_top_k_warning,
)


ANALYSIS_PATH = OUTPUT_ROOT / "ANALYSIS.md"
MANIFEST_PATH = OUTPUT_ROOT / MANIFEST_FILENAME


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def _load_checkpoint(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def _normalized_effective_config(checkpoint: dict[str, Any]) -> dict[str, Any]:
    effective = copy.deepcopy(checkpoint["benchmark_effective_config"])
    effective["runtime"]["output_path"] = "<output>"
    effective["runtime"]["checkpoint_path"] = "<checkpoint>"
    effective["runtime"]["score_path"] = "<score>"
    effective["pinned_authority"]["shared"]["retrieval"]["call_contract"].pop(
        "top_k_memories"
    )
    return effective


def test_single_variable_top_k_artifacts_exist() -> None:
    manifest = _load_manifest()

    assert ANALYSIS_PATH.exists()
    assert manifest["current_return_limit"] == evaluate_module.TOP_K_MEMORIES
    assert manifest["top_k_values"] == list(TOP_K_VALUES)
    assert len(manifest["runs"]) == len(TOP_K_VALUES)

    for run in manifest["runs"]:
        run_dir = Path(run["output_dir"])
        assert run_dir.exists()
        assert Path(run["checkpoint_path"]).exists()
        assert Path(run["results_path"]).exists()
        assert Path(run["score_path"]).exists()
        assert Path(run["run_summary_path"]).exists()
        assert Path(run["retrieval_diagnostics_path"]).exists()


def test_single_variable_top_k_only_changes_return_limit() -> None:
    manifest = _load_manifest()

    checkpoints = [_load_checkpoint(run["checkpoint_path"]) for run in manifest["runs"]]
    normalized = [_normalized_effective_config(checkpoint) for checkpoint in checkpoints]

    for checkpoint, top_k in zip(checkpoints, TOP_K_VALUES, strict=True):
        assert checkpoint["benchmark_config_drift_warnings"] == expected_top_k_warning(top_k)

    assert normalized[1:] == [normalized[0]] * (len(normalized) - 1)

    observed_top_k = [
        checkpoint["benchmark_effective_config"]["pinned_authority"]["shared"][
            "retrieval"
        ]["call_contract"]["top_k_memories"]
        for checkpoint in checkpoints
    ]
    assert observed_top_k == list(TOP_K_VALUES)
    assert observed_top_k[0] == evaluate_module.TOP_K_MEMORIES


def test_single_variable_top_k_authority_derived_baseline_wording() -> None:
    manifest = _load_manifest()
    baseline_run = next(
        run for run in manifest["runs"] if run["top_k"] == evaluate_module.TOP_K_MEMORIES
    )
    analysis_text = ANALYSIS_PATH.read_text()
    baseline_k_label = f"k{evaluate_module.TOP_K_MEMORIES:02d}"
    assert baseline_k_label in analysis_text, (
        f"Analysis should contain authority-derived baseline label '{baseline_k_label}'"
    )
    assert f"rank {evaluate_module.TOP_K_MEMORIES}" in analysis_text, (
        f"Analysis should contain authority-derived rank wording 'rank {evaluate_module.TOP_K_MEMORIES}'"
    )
    header_delta_label = f"Δ tokens vs {baseline_k_label}"
    assert header_delta_label in analysis_text, (
        f"Analysis score-table header should contain authority-derived delta label "
        f"'{header_delta_label}', not hardcoded 'k05'"
    )
    assert baseline_run["top_k"] == evaluate_module.TOP_K_MEMORIES
    compared_vals = [v for v in TOP_K_VALUES if v != evaluate_module.TOP_K_MEMORIES]
    compared_set_label = f"`k{min(compared_vals):02d}`..`k{max(compared_vals):02d}`"
    assert compared_set_label in analysis_text, (
        f"Analysis should contain authority-derived compared-set label {compared_set_label!r}, "
        "not hardcoded `k06`..`k09`"
    )


def test_single_variable_top_k_keeps_non_target_knobs_pinned() -> None:
    manifest = _load_manifest()

    for run in manifest["runs"]:
        checkpoint = _load_checkpoint(run["checkpoint_path"])
        pinned = checkpoint["benchmark_effective_config"]["pinned_authority"]
        retrieval = pinned["shared"]["retrieval"]

        assert retrieval["ranking"] == {
            "hybrid_vector_weight": 0.5,
            "hybrid_bm25_weight": 0.3,
            "hybrid_recency_confidence_weight": 0.2,
            "initial_vector_candidates": 10,
            "min_final_score": 0.15,
        }
        assert retrieval["inactive_controls"] == {
            "active_in_longmemeval": False,
            "effective_limit_authority": "TOP_K_MEMORIES",
            "max_returned_memories": 5,
        }
        assert retrieval["call_contract"]["include_l0"] is True
        assert retrieval["call_contract"]["include_dream_observations"] is True
        assert retrieval["call_contract"]["retrieval_triggered_by"] == "longmemeval"
