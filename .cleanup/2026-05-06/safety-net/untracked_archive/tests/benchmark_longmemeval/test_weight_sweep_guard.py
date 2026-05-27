from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from tests.benchmark_longmemeval.weight_sweep import (
    ANALYSIS_FILENAME,
    CURRENT_BASELINE_ROOT,
    CURRENT_WEIGHTS,
    MANIFEST_FILENAME,
    OUTPUT_ROOT,
    RUN_SUMMARY_FILENAME,
    TOP_K_OVERRIDE,
    WEIGHT_RUNS,
    expected_weight_warnings,
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
    ranking.pop("hybrid_vector_weight", None)
    ranking.pop("hybrid_bm25_weight", None)
    ranking.pop("hybrid_recency_confidence_weight", None)
    return effective


def test_weight_sum_guard_artifacts_exist() -> None:
    manifest = _load_manifest()

    assert ANALYSIS_PATH.exists()
    assert manifest["shared_overrides"]["top_k_memories"] == TOP_K_OVERRIDE
    assert manifest["shared_overrides"]["temporal_filter_enabled"] is True
    assert manifest["current_baseline"]["output_dir"] == str(CURRENT_BASELINE_ROOT)
    assert manifest["current_baseline"]["weights"] == CURRENT_WEIGHTS
    assert math.isclose(manifest["current_baseline"]["weight_sum"], 1.0, abs_tol=1e-9)
    assert len(manifest["runs"]) == len(WEIGHT_RUNS)

    expected_names = [run_name for run_name, _ in WEIGHT_RUNS]
    assert [run["run_name"] for run in manifest["runs"]] == expected_names
    assert [entry["run_name"] for entry in manifest["weight_sets"]] == expected_names

    for run in manifest["runs"]:
        run_dir = Path(run["output_dir"])
        assert run_dir.exists()
        assert Path(run["checkpoint_path"]).exists()
        assert Path(run["results_path"]).exists()
        assert Path(run["score_path"]).exists()
        assert Path(run["run_summary_path"]).exists()
        assert run_dir / RUN_SUMMARY_FILENAME == Path(run["run_summary_path"])


def test_weight_sum_guard_preserves_weight_normalization_and_non_weight_knobs() -> None:
    manifest = _load_manifest()
    current_normalized = manifest["current_baseline"]["normalized_effective_config"]

    for run, (_, weights) in zip(manifest["runs"], WEIGHT_RUNS, strict=True):
        assert math.isclose(run["weight_sum"], 1.0, abs_tol=1e-9)
        assert math.isclose(
            run["weights"]["vector"]
            + run["weights"]["bm25"]
            + run["weights"]["recency_confidence"],
            1.0,
            abs_tol=1e-9,
        )
        checkpoint = _load_checkpoint(run["checkpoint_path"])
        assert checkpoint["benchmark_config_drift_warnings"] == expected_weight_warnings(weights)
        assert _normalized_effective_config(checkpoint) == current_normalized
        assert run["top_k"] == TOP_K_OVERRIDE
        assert run["temporal_filter_enabled"] is True
        assert run["benchmark_config_drift_warnings"] == expected_weight_warnings(weights)
