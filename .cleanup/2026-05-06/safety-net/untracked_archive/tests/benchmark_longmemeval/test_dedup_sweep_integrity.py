from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from tests.benchmark_longmemeval.dedup_sweep import (
    ANALYSIS_FILENAME,
    CURRENT_RUN_NAME,
    MANIFEST_FILENAME,
    OUTPUT_ROOT,
    SWEEP_RUNS,
    _expected_drift_warnings,
)


ANALYSIS_PATH = OUTPUT_ROOT / ANALYSIS_FILENAME
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
    dedup = effective["pinned_authority"]["canonical"]["dedup"]
    dedup.pop("merge_threshold", None)
    dedup.pop("supersede_threshold", None)
    dedup.pop("supersede_same_slot_threshold", None)
    return effective


def test_config_backed_dedup_only_artifacts_exist() -> None:
    manifest = _load_manifest()

    assert ANALYSIS_PATH.exists()
    assert manifest["current_baseline"]["run_name"] == CURRENT_RUN_NAME
    assert manifest["runs"][0]["run_name"] == CURRENT_RUN_NAME

    expected_run_count = 3 if manifest["second_point_executed"] else 2
    assert len(manifest["runs"]) == expected_run_count

    expected_names = [CURRENT_RUN_NAME, SWEEP_RUNS[0][0]]
    if manifest["second_point_executed"]:
        expected_names.append(SWEEP_RUNS[1][0])
    assert [run["run_name"] for run in manifest["runs"]] == expected_names

    for run in manifest["runs"]:
        run_dir = Path(run["output_dir"])
        assert run_dir.exists()
        assert Path(run["checkpoint_path"]).exists()
        assert Path(run["results_path"]).exists()
        assert Path(run["score_path"]).exists()
        assert Path(run["run_summary_path"]).exists()


def test_config_backed_dedup_only_changes_only_dedup_thresholds() -> None:
    manifest = _load_manifest()
    checkpoints = [_load_checkpoint(run["checkpoint_path"]) for run in manifest["runs"]]
    normalized = [_normalized_effective_config(checkpoint) for checkpoint in checkpoints]

    assert normalized[1:] == [normalized[0]] * (len(normalized) - 1)

    threshold_by_run = {run["run_name"]: run["thresholds"] for run in manifest["runs"]}
    for run, checkpoint in zip(manifest["runs"], checkpoints, strict=True):
        assert checkpoint["benchmark_config_drift_warnings"] == _expected_drift_warnings(
            threshold_by_run[run["run_name"]]
        )
        retrieval = checkpoint["benchmark_effective_config"]["pinned_authority"]["shared"][
            "retrieval"
        ]
        assert retrieval["call_contract"]["top_k_memories"] == 6
        assert retrieval["call_contract"]["include_l0"] is True
        assert retrieval["call_contract"]["include_dream_observations"] is True
        assert retrieval["call_contract"]["retrieval_triggered_by"] == "longmemeval"


def test_config_backed_dedup_only_second_point_gate_respected() -> None:
    manifest = _load_manifest()
    first_tight = next(run for run in manifest["runs"] if run["run_name"] == SWEEP_RUNS[0][0])

    assert first_tight["tracked_target_cell"]["total"] == 2
    if manifest["second_point_executed"]:
        assert first_tight["qualifying_improvement"] is True
        assert any(run["run_name"] == SWEEP_RUNS[1][0] for run in manifest["runs"])
    else:
        assert first_tight["qualifying_improvement"] is False
        assert all(run["run_name"] != SWEEP_RUNS[1][0] for run in manifest["runs"])
