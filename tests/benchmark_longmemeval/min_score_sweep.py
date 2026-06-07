from __future__ import annotations

import asyncio
import copy
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import tests.longmemeval.evaluate as evaluate_module
import orchestrator.eval.fact_harness as runner_module
import orchestrator.memory.retrieval as retrieval_module
from orchestrator.eval.fact_harness import LongMemEvalFactRunner
from tests.benchmark_longmemeval.top_k_sweep import (
    CHECKPOINT_FILENAME,
    RESULTS_FILENAME,
    SCORE_FILENAME,
    baseline_payload,
    build_seed_checkpoint,
    judgment_map,
    open_store,
    read_json,
    read_jsonl,
    reset_retrieval_side_effects,
    strict_accuracy,
    subset_definitions,
    taxonomy_lookup,
    utc_now_iso,
    wait_for_retrieval_logs,
    write_json,
)

TOP_K_OVERRIDE = 6
DATASET_PATH = Path("tests/benchmark_longmemeval/fixtures/dev_subset.json")
OUTPUT_ROOT = Path("tests/benchmark_results/dev_sweep_min_score")
BASELINE_ROOT = Path("tests/benchmark_results/dev_subset_baseline")
SEED_CHECKPOINT_PATH = BASELINE_ROOT / "run2" / CHECKPOINT_FILENAME
CURRENT_BASELINE_ROOT = Path("tests/benchmark_results/dev_sweep_temporal/on")
RUN_SUMMARY_FILENAME = "run_summary.json"
MANIFEST_FILENAME = "sweep_manifest.json"
ANALYSIS_FILENAME = "ANALYSIS.md"

PROTECTED_CELLS: dict[str, str] = {
    "IE-user": "single-session-user",
    "IE-assistant": "single-session-assistant",
    "MR": "multi-session",
    "TR": "temporal-reasoning",
    "KU": "knowledge-update",
}

TARGET_SUBSETS: tuple[str, ...] = (
    "retrieval_miss_multi_session",
    "retrieval_miss_single_session_user",
    "retrieval_miss_temporal_reasoning",
)
PRIMARY_TARGET_SUBSET = "retrieval_miss_single_session_user"

CURRENT_MIN_FINAL_SCORE = 0.15

# Threshold values to sweep: below and above current 0.15
# to test whether lower thresholds rescue candidates or merely admit noise
MIN_FINAL_SCORE_VALUES: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20, 0.25)


def expected_min_score_warnings(min_final_score: float) -> list[str]:
    warnings = ["shared.retrieval.call_contract.top_k_memories: pinned=5 effective=6"]
    if not math.isclose(min_final_score, CURRENT_MIN_FINAL_SCORE, abs_tol=1e-9):
        warnings.append(
            "shared.retrieval.ranking.min_final_score: "
            f"pinned={CURRENT_MIN_FINAL_SCORE} effective={min_final_score}"
        )
    return warnings


@contextmanager
def patched_retrieval_min_score(min_final_score: float) -> Iterator[None]:
    original_values = {
        "retrieval_min_final_score": retrieval_module.MIN_FINAL_SCORE,
        "runner_min_final_score": getattr(runner_module, "MIN_FINAL_SCORE", None),
        "evaluate_top_k": evaluate_module.TOP_K_MEMORIES,
        "runner_top_k": runner_module.TOP_K_MEMORIES,
        "temporal_filter": retrieval_module.TEMPORAL_QUERY_FILTER_ENABLED,
        "runner_temporal_filter": getattr(runner_module, "TEMPORAL_QUERY_FILTER_ENABLED", None),
    }
    try:
        retrieval_module.MIN_FINAL_SCORE = float(min_final_score)
        if hasattr(runner_module, "MIN_FINAL_SCORE"):
            runner_module.MIN_FINAL_SCORE = float(min_final_score)
        evaluate_module.TOP_K_MEMORIES = TOP_K_OVERRIDE
        runner_module.TOP_K_MEMORIES = TOP_K_OVERRIDE
        retrieval_module.TEMPORAL_QUERY_FILTER_ENABLED = True
        if hasattr(runner_module, "TEMPORAL_QUERY_FILTER_ENABLED"):
            setattr(runner_module, "TEMPORAL_QUERY_FILTER_ENABLED", True)
        yield
    finally:
        retrieval_module.MIN_FINAL_SCORE = cast(float, original_values["retrieval_min_final_score"])
        runner_min_fs = original_values["runner_min_final_score"]
        if hasattr(runner_module, "MIN_FINAL_SCORE") and runner_min_fs is not None:
            runner_module.MIN_FINAL_SCORE = float(runner_min_fs)
        evaluate_module.TOP_K_MEMORIES = cast(int, original_values["evaluate_top_k"])
        runner_module.TOP_K_MEMORIES = cast(int, original_values["runner_top_k"])
        retrieval_module.TEMPORAL_QUERY_FILTER_ENABLED = bool(original_values["temporal_filter"])
        runner_temporal = original_values["runner_temporal_filter"]
        if hasattr(runner_module, "TEMPORAL_QUERY_FILTER_ENABLED") and runner_temporal is not None:
            setattr(runner_module, "TEMPORAL_QUERY_FILTER_ENABLED", bool(runner_temporal))


def _protected_cells_from_accuracy(accuracy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        label: {
            "metric_key": metric_key,
            "accuracy": float(accuracy.get(metric_key, 0.0)),
        }
        for metric_key, label in PROTECTED_CELLS.items()
    }


def _normalize_effective_config(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return _normalize_effective_payload(checkpoint["benchmark_effective_config"])


def _normalize_effective_payload(effective_config: dict[str, Any]) -> dict[str, Any]:
    effective = copy.deepcopy(effective_config)
    effective["runtime"]["output_path"] = "<output>"
    effective["runtime"]["checkpoint_path"] = "<checkpoint>"
    effective["runtime"]["score_path"] = "<score>"
    ranking = effective["pinned_authority"]["shared"]["retrieval"]["ranking"]
    ranking.pop("min_final_score", None)
    return effective


def _current_normalized_effective_config() -> dict[str, Any]:
    with patched_retrieval_min_score(CURRENT_MIN_FINAL_SCORE):
        effective = runner_module.build_effective_benchmark_config(
            dataset_path=DATASET_PATH,
            checkpoint_dataset_path=None,
            output_path=Path("<output>"),
            checkpoint_path=Path("<checkpoint>"),
            score_path=Path("<score>"),
            limit=None,
            force_retrieval_logging=True,
        )
    return _normalize_effective_payload(effective)


def _run_output_dir(min_final_score: float) -> Path:
    return OUTPUT_ROOT / f"score_{min_final_score:.2f}"


def _run_is_complete(output_dir: Path) -> bool:
    required = (
        output_dir / CHECKPOINT_FILENAME,
        output_dir / RESULTS_FILENAME,
        output_dir / SCORE_FILENAME,
        output_dir / RUN_SUMMARY_FILENAME,
    )
    return all(path.exists() for path in required)


def _load_current_baseline() -> dict[str, Any]:
    checkpoint_path = CURRENT_BASELINE_ROOT / CHECKPOINT_FILENAME
    summary_path = CURRENT_BASELINE_ROOT / RUN_SUMMARY_FILENAME
    results_path = CURRENT_BASELINE_ROOT / RESULTS_FILENAME
    score_path = CURRENT_BASELINE_ROOT / SCORE_FILENAME

    summary = read_json(summary_path)
    results = read_jsonl(results_path)
    score_payload = read_json(score_path)

    if summary["top_k"] != TOP_K_OVERRIDE:
        raise ValueError(
            "Current baseline comparator must keep TOP_K_MEMORIES pinned to 6 "
            f"(saw {summary['top_k']})"
        )
    if summary["temporal_filter_enabled"] is not True:
        raise ValueError("Current baseline comparator must have temporal filter enabled")

    return {
        "output_dir": str(CURRENT_BASELINE_ROOT),
        "checkpoint_path": str(checkpoint_path),
        "results_path": str(results_path),
        "score_path": str(score_path),
        "run_summary_path": str(summary_path),
        "min_final_score": CURRENT_MIN_FINAL_SCORE,
        "summary": summary,
        "results": results,
        "score_payload": score_payload,
        "judgments": judgment_map(results),
        "protected_cells": _protected_cells_from_accuracy(score_payload["accuracy"]),
        "normalized_effective_config": _current_normalized_effective_config(),
    }


def _subset_delta(
    question_ids: list[str],
    *,
    current_judgments: dict[str, str],
    sweep_judgments: dict[str, str],
) -> dict[str, Any]:
    current_correct = sum(current_judgments.get(qid) == "correct" for qid in question_ids)
    sweep_correct = sum(sweep_judgments.get(qid) == "correct" for qid in question_ids)
    total = len(question_ids)
    return {
        "total": total,
        "current_correct": current_correct,
        "sweep_correct": sweep_correct,
        "delta_vs_current": sweep_correct - current_correct,
        "current_accuracy": 0.0 if total == 0 else current_correct / total,
        "sweep_accuracy": 0.0 if total == 0 else sweep_correct / total,
    }


def _comparison_for_run(
    *,
    current: dict[str, Any],
    score_payload: dict[str, Any],
    results: list[dict[str, Any]],
    subsets: dict[str, list[str]],
) -> dict[str, Any]:
    current_summary = current["summary"]
    current_protected = current["protected_cells"]
    sweep_protected = _protected_cells_from_accuracy(score_payload["accuracy"])
    sweep_judgments = judgment_map(results)
    current_judgments = current["judgments"]

    target_cells = {
        subset_name: _subset_delta(
            subsets[subset_name],
            current_judgments=current_judgments,
            sweep_judgments=sweep_judgments,
        )
        for subset_name in TARGET_SUBSETS
    }
    locked_failure_union = _subset_delta(
        subsets["locked_failure_union"],
        current_judgments=current_judgments,
        sweep_judgments=sweep_judgments,
    )
    protected_deltas = {
        label: sweep_protected[label]["accuracy"] - current_protected[label]["accuracy"]
        for label in current_protected
    }
    negative_protected_deltas = {
        label: delta for label, delta in protected_deltas.items() if delta < 0.0
    }
    negative_target_deltas = {
        subset_name: values["delta_vs_current"]
        for subset_name, values in target_cells.items()
        if values["delta_vs_current"] < 0
    }

    strict = strict_accuracy(results)
    strict_delta_vs_current = strict - float(current_summary["strict_accuracy"])
    regressions: list[str] = []
    if strict_delta_vs_current < 0.0:
        regressions.append(f"strict_accuracy {strict_delta_vs_current:+.1%}")
    if locked_failure_union["delta_vs_current"] < 0:
        regressions.append(
            "locked_failure_union "
            f"{locked_failure_union['delta_vs_current']:+d}/{locked_failure_union['total']}"
        )
    regressions.extend(
        f"protected_cell:{label} {delta:+.1%}" for label, delta in negative_protected_deltas.items()
    )
    regressions.extend(
        f"target_cell:{subset_name} {delta:+d}"
        for subset_name, delta in negative_target_deltas.items()
    )

    qualifying_improvement = (
        target_cells[PRIMARY_TARGET_SUBSET]["delta_vs_current"] > 0 and not regressions
    )

    return {
        "strict_accuracy": strict,
        "strict_delta_vs_current": strict_delta_vs_current,
        "target_cells": target_cells,
        "locked_failure_union": locked_failure_union,
        "protected_cells": sweep_protected,
        "protected_deltas_vs_current": protected_deltas,
        "negative_protected_deltas": negative_protected_deltas,
        "negative_target_deltas": negative_target_deltas,
        "regressions": regressions,
        "qualifying_improvement": qualifying_improvement,
    }


def _build_run_summary(
    *,
    min_final_score: float,
    results: list[dict[str, Any]],
    score_payload: dict[str, Any],
    checkpoint: dict[str, Any],
    current: dict[str, Any],
    baseline: dict[str, Any],
    subsets: dict[str, list[str]],
    output_dir: Path,
) -> dict[str, Any]:
    comparison = _comparison_for_run(
        current=current,
        score_payload=score_payload,
        results=results,
        subsets=subsets,
    )
    strict = strict_accuracy(results)
    summary = {
        "min_final_score": min_final_score,
        "run_name": f"score_{min_final_score:.2f}",
        "top_k": TOP_K_OVERRIDE,
        "temporal_filter_enabled": True,
        "result_count": len(results),
        "strict_accuracy": strict,
        "strict_delta_vs_current": comparison["strict_delta_vs_current"],
        "strict_delta_vs_run1": strict - baseline["run1"]["strict_accuracy"],
        "strict_delta_vs_run2": strict - baseline["run2"]["strict_accuracy"],
        "accuracy": score_payload["accuracy"],
        "benchmark_config_drift_warnings": checkpoint["benchmark_config_drift_warnings"],
        "expected_drift_warnings": expected_min_score_warnings(min_final_score),
        "approved_target_cells": comparison["target_cells"],
        "locked_failure_union": comparison["locked_failure_union"],
        "protected_cells": comparison["protected_cells"],
        "protected_deltas_vs_current": comparison["protected_deltas_vs_current"],
        "negative_protected_deltas": comparison["negative_protected_deltas"],
        "negative_target_deltas": comparison["negative_target_deltas"],
        "regressions": comparison["regressions"],
        "qualifying_improvement": comparison["qualifying_improvement"],
        "normalized_effective_config": _normalize_effective_config(checkpoint),
    }
    write_json(output_dir / RUN_SUMMARY_FILENAME, summary)
    return summary


def _build_manifest_entry(
    min_final_score: float, output_dir: Path, summary: dict[str, Any]
) -> dict[str, Any]:
    return {
        "min_final_score": min_final_score,
        "run_name": f"score_{min_final_score:.2f}",
        "output_dir": str(output_dir),
        "checkpoint_path": str(output_dir / CHECKPOINT_FILENAME),
        "results_path": str(output_dir / RESULTS_FILENAME),
        "score_path": str(output_dir / SCORE_FILENAME),
        "run_summary_path": str(output_dir / RUN_SUMMARY_FILENAME),
        **summary,
    }


def _recommendation(current: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    qualifying = [run for run in runs if run["qualifying_improvement"]]
    if not qualifying:
        best_primary = max(
            runs,
            key=lambda run: (
                run["approved_target_cells"][PRIMARY_TARGET_SUBSET]["delta_vs_current"],
                run["strict_accuracy"],
                run["locked_failure_union"]["sweep_correct"],
            ),
        )
        return {
            "current_threshold_remains": True,
            "recommended_threshold": None,
            "reason": (
                "No threshold value improved the primary approved target cell "
                "`retrieval-miss × single-session-user` without introducing "
                "a strict-score, locked-failure, protected-cell, or other "
                "approved-target regression on the locked dev subset."
            ),
            "best_non_qualifying_run": best_primary["run_name"],
            "best_non_qualifying_threshold": best_primary["min_final_score"],
        }

    recommended = max(
        qualifying,
        key=lambda run: (
            run["approved_target_cells"][PRIMARY_TARGET_SUBSET]["delta_vs_current"],
            run["strict_accuracy"],
            run["locked_failure_union"]["sweep_correct"],
        ),
    )
    return {
        "current_threshold_remains": False,
        "recommended_threshold": recommended["min_final_score"],
        "recommended_run": recommended["run_name"],
        "reason": (
            f"This threshold ({recommended['min_final_score']}) improved the primary "
            f"approved target cell while avoiding strict-score, locked-failure, "
            f"protected-cell, and approved-target-cell regressions on the locked dev subset."
        ),
        "current_baseline_strict_accuracy": current["strict_accuracy"],
        "recommended_strict_accuracy": recommended["strict_accuracy"],
    }


def build_analysis_markdown(manifest: dict[str, Any]) -> str:
    current = manifest["current_baseline"]
    runs = manifest["runs"]
    recommendation = manifest["recommendation"]

    current_row = (
        f"| current | {CURRENT_MIN_FINAL_SCORE:.2f} | "
        f"{float(current['strict_accuracy']):.1%} | +0.0% | "
        f"{current['locked_failure_union']['current_correct']}/{current['locked_failure_union']['total']} | "
        f"{current['approved_target_cells'][PRIMARY_TARGET_SUBSET]['current_correct']}/6 | "
        f"{current['approved_target_cells']['retrieval_miss_multi_session']['current_correct']}/6 | "
        f"{current['approved_target_cells']['retrieval_miss_temporal_reasoning']['current_correct']}/5 | none |"
    )

    lines = [
        "# MIN_FINAL_SCORE Dev Sweep Analysis",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "This dev-subset ablation keeps the canonical lane pinned to the current retrieval state: `TOP_K_MEMORIES = 6`, `TEMPORAL_QUERY_FILTER_ENABLED = True`, `INITIAL_VECTOR_CANDIDATES = 10`, and varies only `MIN_FINAL_SCORE`.",
        "The current-threshold comparator is reused from `tests/benchmark_results/dev_sweep_temporal/on/`, which already reflects the current-main non-threshold retrieval configuration.",
        "",
        "## Qualification rule",
        "",
        "An alternative threshold is only considered better if it improves the primary approved target cell `retrieval-miss × single-session-user` **and** avoids dev-subset regressions in strict accuracy, locked-failure-union recovery, protected primary-category accuracy, and the other approved retrieval-miss target cells.",
        "",
        "## Score deltas and target-cell results",
        "",
        "| Threshold | Strict score | Δ vs current | Locked failure union | Single-session-user retrieval-miss | Multi-session retrieval-miss | Temporal retrieval-miss | Regressions |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        current_row,
    ]

    for run in runs:
        regressions = ", ".join(run["regressions"]) if run["regressions"] else "none"
        lines.append(
            f"| {run['min_final_score']:.2f} | "
            f"{run['strict_accuracy']:.1%} | "
            f"{run['strict_delta_vs_current']:+.1%} | "
            f"{run['locked_failure_union']['sweep_correct']}/{run['locked_failure_union']['total']} "
            f"({run['locked_failure_union']['delta_vs_current']:+d}) | "
            f"{run['approved_target_cells'][PRIMARY_TARGET_SUBSET]['sweep_correct']}/6 "
            f"({run['approved_target_cells'][PRIMARY_TARGET_SUBSET]['delta_vs_current']:+d}) | "
            f"{run['approved_target_cells']['retrieval_miss_multi_session']['sweep_correct']}/6 "
            f"({run['approved_target_cells']['retrieval_miss_multi_session']['delta_vs_current']:+d}) | "
            f"{run['approved_target_cells']['retrieval_miss_temporal_reasoning']['sweep_correct']}/5 "
            f"({run['approved_target_cells']['retrieval_miss_temporal_reasoning']['delta_vs_current']:+d}) | "
            f"{regressions} |"
        )

    lines.extend(
        [
            "",
            "## Recommendation",
            "",
        ]
    )

    if recommendation["current_threshold_remains"]:
        lines.extend(
            [
                "- Verdict: `current threshold (0.15) remains`",
                f"- Reason: {recommendation['reason']}",
                f"- Best non-qualifying alternative: `{recommendation['best_non_qualifying_run']}` at {recommendation['best_non_qualifying_threshold']}",
            ]
        )
    else:
        lines.extend(
            [
                "- Verdict: `an approved alternative improves the target cell on the locked dev subset`",
                f"- Recommended threshold: `{recommendation['recommended_threshold']}`",
                f"- Reason: {recommendation['reason']}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


async def run_sweep() -> dict[str, Any]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    seed_checkpoint = read_json(SEED_CHECKPOINT_PATH)
    baseline = baseline_payload()
    current = _load_current_baseline()
    taxonomy_by_qid = taxonomy_lookup()
    subsets = subset_definitions(taxonomy_by_qid)
    current_vs_current = _comparison_for_run(
        current=current,
        score_payload=current["score_payload"],
        results=current["results"],
        subsets=subsets,
    )

    current_summary = current["summary"]
    current_manifest_summary = {
        "output_dir": current["output_dir"],
        "checkpoint_path": current["checkpoint_path"],
        "results_path": current["results_path"],
        "score_path": current["score_path"],
        "run_summary_path": current["run_summary_path"],
        "min_final_score": current["min_final_score"],
        "top_k": current_summary["top_k"],
        "temporal_filter_enabled": current_summary["temporal_filter_enabled"],
        "strict_accuracy": current_summary["strict_accuracy"],
        "approved_target_cells": {
            subset_name: {
                "total": values["total"],
                "current_correct": values["current_correct"],
                "current_accuracy": values["current_accuracy"],
            }
            for subset_name, values in current_vs_current["target_cells"].items()
        },
        "locked_failure_union": {
            "total": current_vs_current["locked_failure_union"]["total"],
            "current_correct": current_vs_current["locked_failure_union"]["current_correct"],
            "current_accuracy": current_vs_current["locked_failure_union"]["current_accuracy"],
        },
        "protected_cells": current["protected_cells"],
        "normalized_effective_config": current["normalized_effective_config"],
    }

    pool, _store = await open_store()
    try:
        manifest: dict[str, Any] = {
            "generated_at": utc_now_iso(),
            "dataset_path": str(DATASET_PATH),
            "seed_checkpoint_path": str(SEED_CHECKPOINT_PATH),
            "current_baseline": current_manifest_summary,
            "baseline": {
                "run1_strict_accuracy": baseline["run1"]["strict_accuracy"],
                "run2_strict_accuracy": baseline["run2"]["strict_accuracy"],
                "mean_strict_accuracy": baseline["mean_strict_accuracy"],
            },
            "shared_overrides": {
                "top_k_memories": TOP_K_OVERRIDE,
                "temporal_filter_enabled": True,
                "initial_vector_candidates": retrieval_module.INITIAL_VECTOR_CANDIDATES,
            },
            "current_min_final_score": CURRENT_MIN_FINAL_SCORE,
            "threshold_values": list(MIN_FINAL_SCORE_VALUES),
            "runs": [],
        }

        for min_final_score in MIN_FINAL_SCORE_VALUES:
            output_dir = _run_output_dir(min_final_score)
            output_dir.mkdir(parents=True, exist_ok=True)

            if _run_is_complete(output_dir):
                summary = read_json(output_dir / RUN_SUMMARY_FILENAME)
                manifest["runs"].append(_build_manifest_entry(min_final_score, output_dir, summary))
                continue

            checkpoint_path = output_dir / CHECKPOINT_FILENAME
            results_path = output_dir / RESULTS_FILENAME
            write_json(checkpoint_path, build_seed_checkpoint(seed_checkpoint))
            await reset_retrieval_side_effects(pool)

            with patched_retrieval_min_score(min_final_score):
                runner = LongMemEvalFactRunner(
                    dataset_path=DATASET_PATH,
                    output_path=results_path,
                    checkpoint_path=checkpoint_path,
                    score_path=output_dir / SCORE_FILENAME,
                    limit=None,
                    force_retrieval_logging=True,
                )
                results = await runner.evaluate()
                await wait_for_retrieval_logs(pool, expected_count=len(results))
                _ = runner.score()

            checkpoint = read_json(checkpoint_path)
            results = read_jsonl(results_path)
            score_payload = read_json(output_dir / SCORE_FILENAME)
            summary = _build_run_summary(
                min_final_score=min_final_score,
                results=results,
                score_payload=score_payload,
                checkpoint=checkpoint,
                current=current,
                baseline=baseline,
                subsets=subsets,
                output_dir=output_dir,
            )
            manifest["runs"].append(_build_manifest_entry(min_final_score, output_dir, summary))
            write_json(OUTPUT_ROOT / MANIFEST_FILENAME, manifest)

        manifest["runs"] = sorted(manifest["runs"], key=lambda run: run["min_final_score"])
        manifest["recommendation"] = _recommendation(current_manifest_summary, manifest["runs"])
        write_json(OUTPUT_ROOT / MANIFEST_FILENAME, manifest)
        (OUTPUT_ROOT / ANALYSIS_FILENAME).write_text(build_analysis_markdown(manifest))
        return manifest
    finally:
        await pool.close()


def main() -> None:
    manifest = asyncio.run(run_sweep())
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
