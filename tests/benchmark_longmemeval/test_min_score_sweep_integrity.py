from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from tests.benchmark_longmemeval.min_score_sweep import (
    ANALYSIS_FILENAME,
    CURRENT_MIN_FINAL_SCORE,
    MANIFEST_FILENAME,
    MIN_FINAL_SCORE_VALUES,
    OUTPUT_ROOT,
    expected_min_score_warnings,
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
    ranking = effective["pinned_authority"]["shared"]["retrieval"]["ranking"]
    ranking.pop("min_final_score", None)
    return effective


def test_single_variable_min_score_artifacts_exist() -> None:
    manifest = _load_manifest()

    assert ANALYSIS_PATH.exists()
    assert manifest["current_min_final_score"] == CURRENT_MIN_FINAL_SCORE
    assert manifest["threshold_values"] == list(MIN_FINAL_SCORE_VALUES)
    assert len(manifest["runs"]) == len(MIN_FINAL_SCORE_VALUES)

    for run in manifest["runs"]:
        run_dir = Path(run["output_dir"])
        assert run_dir.exists()
        assert Path(run["checkpoint_path"]).exists()
        assert Path(run["results_path"]).exists()
        assert Path(run["score_path"]).exists()
        assert Path(run["run_summary_path"]).exists()


def test_single_variable_min_score_only_changes_threshold() -> None:
    manifest = _load_manifest()

    checkpoints = [_load_checkpoint(run["checkpoint_path"]) for run in manifest["runs"]]
    normalized = [_normalized_effective_config(checkpoint) for checkpoint in checkpoints]

    for checkpoint, threshold in zip(checkpoints, MIN_FINAL_SCORE_VALUES, strict=True):
        assert checkpoint["benchmark_config_drift_warnings"] == expected_min_score_warnings(
            threshold
        )

    assert normalized[1:] == [normalized[0]] * (len(normalized) - 1)

    observed_thresholds = [
        checkpoint["benchmark_effective_config"]["pinned_authority"]["shared"]["retrieval"][
            "ranking"
        ]["min_final_score"]
        for checkpoint in checkpoints
    ]
    assert observed_thresholds == list(MIN_FINAL_SCORE_VALUES)
    assert observed_thresholds[2] == CURRENT_MIN_FINAL_SCORE


def test_single_variable_min_score_keeps_non_target_knobs_pinned() -> None:
    manifest = _load_manifest()

    for run in manifest["runs"]:
        checkpoint = _load_checkpoint(run["checkpoint_path"])
        pinned = checkpoint["benchmark_effective_config"]["pinned_authority"]
        retrieval = pinned["shared"]["retrieval"]

        assert retrieval["ranking"]["min_final_score"] == run["min_final_score"]
        assert retrieval["call_contract"]["top_k_memories"] == 6
        assert retrieval["call_contract"]["include_l0"] is True
        assert retrieval["call_contract"]["include_dream_observations"] is True
        assert retrieval["call_contract"]["retrieval_triggered_by"] == "longmemeval"
