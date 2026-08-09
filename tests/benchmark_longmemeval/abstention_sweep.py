from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import tests.longmemeval.evaluate as evaluate_module
import orchestrator.eval.fact_harness as runner_module
from orchestrator.eval.fact_harness import LongMemEvalFactRunner
from orchestrator.eval.substrate import SubstrateMismatchError, load_tagged_score
from orchestrator.prompts import MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL  # noqa: F401  # re-exported for backwards-compatible imports
from tests.benchmark_longmemeval._guardrail import apply_guardrail as _apply_guardrail
from tests.benchmark_longmemeval.dev_subset import load_fixture
from tests.benchmark_longmemeval.taxonomy import build_taxonomy_entries, load_failure_rows
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
OUTPUT_ROOT = Path("tests/benchmark_results/dev_sweep_abstention")
BASELINE_ROOT = Path("tests/benchmark_results/dev_subset_baseline")
SEED_CHECKPOINT_PATH = BASELINE_ROOT / "run2" / CHECKPOINT_FILENAME
RUN_SUMMARY_FILENAME = "run_summary.json"
BEHAVIOR_DIAGNOSTICS_FILENAME = "behavior_diagnostics.json"
MANIFEST_FILENAME = "sweep_manifest.json"
ANALYSIS_FILENAME = "ANALYSIS.md"

RUN_MODES: tuple[tuple[str, bool], ...] = (("off", False), ("on", True))
PROTECTED_CELLS: dict[str, str] = {
    "IE-user": "single-session-user",
    "IE-assistant": "single-session-assistant",
    "MR": "multi-session",
    "TR": "temporal-reasoning",
    "KU": "knowledge-update",
    "ABS": "abstention",
}
ABSTENTION_PATTERN = re.compile(
    r"(i(?:'m| am)? sorry|i do not have|i don't have|there is no information|"
    r"not enough information|cannot determine|can't determine|insufficient|"
    r"not available|provided memories do not contain)",
    re.IGNORECASE,
)

_ORIGINAL_EVALUATE_BUILD_ANSWER_PROMPT = evaluate_module.build_answer_prompt
_ORIGINAL_RUNNER_BUILD_ANSWER_PROMPT = runner_module.build_answer_prompt


def _fixture_question_ids() -> tuple[str, ...]:
    return tuple(
        str(entry.get("question_id", ""))
        for entry in load_fixture()
        if str(entry.get("question_id", ""))
    )


def _taxonomy_entries() -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], entry)
        for entry in build_taxonomy_entries(failure_rows=load_failure_rows())
    ]


FIXTURE_QUESTION_IDS = _fixture_question_ids()
ABSTENTION_CASE_IDS = tuple(qid for qid in FIXTURE_QUESTION_IDS if qid.endswith("_abs"))
LOCKED_ABSTENTION_FAILURE_IDS = tuple(
    str(entry["question_id"])
    for entry in _taxonomy_entries()
    if str(entry["category"]) == "abstention"
)
SECONDARY_TEMPORAL_GENERATION_ERROR_IDS = tuple(
    str(entry["question_id"])
    for entry in _taxonomy_entries()
    if str(entry["stage"]) == "generation-error" and str(entry["category"]) == "temporal-reasoning"
)


def _guarded_build_answer_prompt(question: str, memories: list[dict[str, Any]]) -> str:
    return _apply_guardrail(_ORIGINAL_EVALUATE_BUILD_ANSWER_PROMPT(question, memories))


@contextmanager
def patched_answer_prompt(*, enabled: bool) -> Iterator[None]:
    try:
        if enabled:
            evaluate_module.build_answer_prompt = _guarded_build_answer_prompt
            runner_module.build_answer_prompt = _guarded_build_answer_prompt
        else:
            evaluate_module.build_answer_prompt = _ORIGINAL_EVALUATE_BUILD_ANSWER_PROMPT
            runner_module.build_answer_prompt = _ORIGINAL_RUNNER_BUILD_ANSWER_PROMPT
        yield
    finally:
        evaluate_module.build_answer_prompt = _ORIGINAL_EVALUATE_BUILD_ANSWER_PROMPT
        runner_module.build_answer_prompt = _ORIGINAL_RUNNER_BUILD_ANSWER_PROMPT


def _normalize_effective_config(checkpoint: dict[str, Any]) -> dict[str, Any]:
    effective = copy.deepcopy(checkpoint["benchmark_effective_config"])
    effective["runtime"]["output_path"] = "<output>"
    effective["runtime"]["checkpoint_path"] = "<checkpoint>"
    effective["runtime"]["score_path"] = "<score>"
    effective["pinned_authority"]["shared"]["answer"]["prompt_sha256"] = "<prompt>"
    return effective


def _is_abstention_like(hypothesis: object) -> bool:
    return bool(ABSTENTION_PATTERN.search(str(hypothesis or "")))


def _protected_accuracy(
    results: list[dict[str, Any]], score_payload: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    raw_accuracy = cast(dict[str, Any], score_payload["accuracy"])
    protected = {
        label: {
            "metric_key": metric_key,
            "accuracy": float(raw_accuracy.get(metric_key, 0.0)),
        }
        for metric_key, label in PROTECTED_CELLS.items()
        if metric_key != "ABS"
    }
    abstention_total = len(ABSTENTION_CASE_IDS)
    abstention_correct = sum(
        str(row.get("question_id", "")).endswith("_abs")
        and str(row.get("judgment", "")) == "correct"
        for row in results
    )
    protected["abstention"] = {
        "metric_key": "abstention_overlay",
        "accuracy": 0.0 if abstention_total == 0 else abstention_correct / abstention_total,
    }
    return protected


def _run_output_dir(run_name: str) -> Path:
    return OUTPUT_ROOT / run_name


def _run_is_complete(output_dir: Path) -> bool:
    required = (
        output_dir / CHECKPOINT_FILENAME,
        output_dir / RESULTS_FILENAME,
        output_dir / SCORE_FILENAME,
        output_dir / RUN_SUMMARY_FILENAME,
        output_dir / BEHAVIOR_DIAGNOSTICS_FILENAME,
    )
    if not all(path.exists() for path in required):
        return False
    # Reject cached scores from a different substrate (e.g. legacy
    # fast/chunk outputs left from a previous harness version); the
    # substrate guard would let them pass silently otherwise.
    try:
        score = load_tagged_score(output_dir / SCORE_FILENAME)
    except (FileNotFoundError, ValueError, SubstrateMismatchError):
        return False
    return score.get("substrate") == "fact"


def _expected_warning_count(*, prompt_enabled: bool) -> int:
    return 2 if prompt_enabled else 1


def _build_run_summary(
    *,
    run_name: str,
    prompt_enabled: bool,
    results: list[dict[str, Any]],
    score_payload: dict[str, Any],
    checkpoint: dict[str, Any],
    taxonomy_by_qid: dict[str, dict[str, str]],
    subsets: dict[str, list[str]],
    output_dir: Path,
) -> dict[str, Any]:
    judgments = judgment_map(results)
    abstention_rows = [row for row in results if str(row.get("question_id", "")).endswith("_abs")]
    false_abstention_rows = [
        {
            "question_id": str(row.get("question_id", "")),
            "category": str(row.get("category", "")),
            "judgment": str(row.get("judgment", "")),
            "hypothesis": str(row.get("hypothesis", "")),
        }
        for row in results
        if not str(row.get("question_id", "")).endswith("_abs")
        and str(row.get("judgment", "")) != "correct"
        and _is_abstention_like(row.get("hypothesis", ""))
    ]
    behavior_diagnostics = [
        {
            "question_id": str(row.get("question_id", "")),
            "category": str(row.get("category", "")),
            "judgment": str(row.get("judgment", "")),
            "is_abstention_variant": str(row.get("question_id", "")).endswith("_abs"),
            "abstention_like": _is_abstention_like(row.get("hypothesis", "")),
            "hypothesis": str(row.get("hypothesis", "")),
            "taxonomy": taxonomy_by_qid.get(str(row.get("question_id", ""))),
        }
        for row in results
    ]
    write_json(output_dir / BEHAVIOR_DIAGNOSTICS_FILENAME, behavior_diagnostics)

    protected_cells = _protected_accuracy(results, score_payload)
    locked_failure_union_ids = subsets["locked_failure_union"]
    summary = {
        "run_name": run_name,
        "prompt_guardrail_enabled": prompt_enabled,
        "prompt_guardrail_sha256": checkpoint["benchmark_effective_config"]["pinned_authority"][
            "shared"
        ]["answer"]["prompt_sha256"],
        "benchmark_config_drift_warnings": checkpoint["benchmark_config_drift_warnings"],
        "expected_drift_warning_count": _expected_warning_count(prompt_enabled=prompt_enabled),
        "strict_accuracy": strict_accuracy(results),
        "result_count": len(results),
        "accuracy": score_payload["accuracy"],
        "mean_memories_used": sum(int(row.get("memories_used", 0)) for row in results)
        / max(1, len(results)),
        "max_memories_used": max(int(row.get("memories_used", 0)) for row in results),
        "protected_cells": protected_cells,
        "abstention_metrics": {
            "fixture_case_count": len(ABSTENTION_CASE_IDS),
            "correct_fixture_cases": sum(
                judgments.get(question_id) == "correct" for question_id in ABSTENTION_CASE_IDS
            ),
            "locked_failure_case_count": len(LOCKED_ABSTENTION_FAILURE_IDS),
            "locked_failure_correct": sum(
                judgments.get(question_id) == "correct"
                for question_id in LOCKED_ABSTENTION_FAILURE_IDS
            ),
            "locked_failure_question_ids": list(LOCKED_ABSTENTION_FAILURE_IDS),
            "abstention_like_fixture_answers": sum(
                _is_abstention_like(row.get("hypothesis", "")) for row in abstention_rows
            ),
        },
        "false_abstention_metrics": {
            "count": len(false_abstention_rows),
            "question_ids": [row["question_id"] for row in false_abstention_rows],
            "rows": false_abstention_rows,
        },
        "locked_failure_union": {
            "total": len(locked_failure_union_ids),
            "correct": sum(
                judgments.get(question_id) == "correct" for question_id in locked_failure_union_ids
            ),
        },
        "secondary_cell": {
            "stage": "generation-error",
            "category": "temporal-reasoning",
            "total": len(SECONDARY_TEMPORAL_GENERATION_ERROR_IDS),
            "correct": sum(
                judgments.get(question_id) == "correct"
                for question_id in SECONDARY_TEMPORAL_GENERATION_ERROR_IDS
            ),
        },
        "normalized_effective_config": _normalize_effective_config(checkpoint),
    }
    write_json(output_dir / RUN_SUMMARY_FILENAME, summary)
    return summary


def _build_manifest_entry(
    run_name: str, output_dir: Path, summary: dict[str, Any]
) -> dict[str, Any]:
    return {
        "run_name": run_name,
        "output_dir": str(output_dir),
        "checkpoint_path": str(output_dir / CHECKPOINT_FILENAME),
        "results_path": str(output_dir / RESULTS_FILENAME),
        "score_path": str(output_dir / SCORE_FILENAME),
        "run_summary_path": str(output_dir / RUN_SUMMARY_FILENAME),
        "behavior_diagnostics_path": str(output_dir / BEHAVIOR_DIAGNOSTICS_FILENAME),
        **summary,
    }


def _comparison(off: dict[str, Any], on: dict[str, Any]) -> dict[str, Any]:
    negative_protected_cell_deltas = {
        label: on["protected_cells"][label]["accuracy"] - off["protected_cells"][label]["accuracy"]
        for label in off["protected_cells"]
        if on["protected_cells"][label]["accuracy"] < off["protected_cells"][label]["accuracy"]
    }
    false_abstention_delta = (
        on["false_abstention_metrics"]["count"] - off["false_abstention_metrics"]["count"]
    )
    if negative_protected_cell_deltas:
        recommendation = "back_out"
        reason = (
            "Prompt hardening regressed protected dev-subset cell accuracy, so the subset-veto "
            "rule requires backout before any downstream composition work."
        )
    elif false_abstention_delta > 0:
        recommendation = "hold"
        reason = (
            "Prompt hardening did not trip the protected-cell veto, but it increased false-"
            "abstention risk on answerable non-ABS questions, so it should not advance."
        )
    else:
        recommendation = "hold"
        reason = (
            "Prompt hardening can only remain a guarded dev-subset observation here because the "
            "locked abstention failure surface is still below the 5-case promotion floor."
        )
    return {
        "strict_accuracy_delta_on_minus_off": on["strict_accuracy"] - off["strict_accuracy"],
        "abstention_accuracy_delta_on_minus_off": on["protected_cells"]["abstention"]["accuracy"]
        - off["protected_cells"]["abstention"]["accuracy"],
        "locked_abstention_failure_delta_on_minus_off": on["abstention_metrics"][
            "locked_failure_correct"
        ]
        - off["abstention_metrics"]["locked_failure_correct"],
        "locked_failure_union_delta_on_minus_off": on["locked_failure_union"]["correct"]
        - off["locked_failure_union"]["correct"],
        "false_abstention_delta_on_minus_off": false_abstention_delta,
        "negative_protected_cell_deltas": negative_protected_cell_deltas,
        "new_false_abstention_question_ids_on_minus_off": sorted(
            set(on["false_abstention_metrics"]["question_ids"])
            - set(off["false_abstention_metrics"]["question_ids"])
        ),
        "eligible_for_full_corpus_promotion": False,
        "recommendation": recommendation,
        "recommendation_reason": reason,
    }


def build_analysis_markdown(manifest: dict[str, Any]) -> str:
    runs = cast(list[dict[str, Any]], manifest["runs"])
    by_name = {str(run["run_name"]): run for run in runs}
    off = by_name["off"]
    on = by_name["on"]
    comparison = cast(dict[str, Any], manifest["comparison"])
    coverage_gate = cast(dict[str, Any], manifest["coverage_gate"])

    lines = [
        "# Abstention Prompt Dev Sweep Analysis",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "This dev-subset ablation keeps the canonical lane pinned to `TOP_K_MEMORIES = 6` from Task 3a and compares the shared abstention guardrail off vs on.",
        "The benchmark-side on arm patches the LongMemEval answer prompt with the exact same guardrail text now injected by `assemble_system_prompt()`, so the checkpoint prompt hash reflects a real prompt-only delta.",
        "",
        "## Coverage gate",
        "",
        f"- Work-order status: `{coverage_gate['status']}`.",
        f"- Locked abstention failures in the taxonomy: `{coverage_gate['target_failure_cell']['count']}` (promotion floor `{coverage_gate['subset_veto_min_locked_cases']}`).",
        f"- Nearby secondary cell `generation-error × temporal-reasoning`: `{coverage_gate['secondary_cell']['count']}`.",
        "- Because the abstention target surface is still below the 5-case floor, this sweep can only inform guarded dev-subset judgment; it cannot justify full-corpus promotion by itself.",
        "",
        "## On/off summary",
        "",
        "| Run | Guardrail | Strict score | ABS accuracy | Locked abstention failures correct | False abstentions on non-ABS questions | Locked failure union correct | Drift warnings |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
        f"| off | disabled | {off['strict_accuracy']:.1%} | {off['protected_cells']['abstention']['accuracy']:.1%} | {off['abstention_metrics']['locked_failure_correct']}/{off['abstention_metrics']['locked_failure_case_count']} | {off['false_abstention_metrics']['count']} | {off['locked_failure_union']['correct']}/{off['locked_failure_union']['total']} | {len(off['benchmark_config_drift_warnings'])} |",
        f"| on | enabled | {on['strict_accuracy']:.1%} | {on['protected_cells']['abstention']['accuracy']:.1%} | {on['abstention_metrics']['locked_failure_correct']}/{on['abstention_metrics']['locked_failure_case_count']} | {on['false_abstention_metrics']['count']} | {on['locked_failure_union']['correct']}/{on['locked_failure_union']['total']} | {len(on['benchmark_config_drift_warnings'])} |",
        "",
        "## Protected dev-subset cells",
        "",
        "Any negative delta on these locked cells is a backout condition for this prompt ablation.",
        "",
        "| Cell | off | on | Δ on-off |",
        "| --- | --- | --- | --- |",
    ]
    for label in (
        "single-session-user",
        "single-session-assistant",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
        "abstention",
    ):
        off_accuracy = float(off["protected_cells"][label]["accuracy"])
        on_accuracy = float(on["protected_cells"][label]["accuracy"])
        lines.append(
            f"| {label} | {off_accuracy:.1%} | {on_accuracy:.1%} | {on_accuracy - off_accuracy:+.1%} |"
        )

    lines.extend(
        [
            "",
            "## False-abstention risk",
            "",
            f"- off false-abstention count: `{off['false_abstention_metrics']['count']}`",
            f"- on false-abstention count: `{on['false_abstention_metrics']['count']}`",
            f"- delta on-off: `{comparison['false_abstention_delta_on_minus_off']:+d}`",
            f"- new false-abstention QIDs in the on arm: `{comparison['new_false_abstention_question_ids_on_minus_off'] or 'none'}`",
            "",
            "False abstentions here mean answerable non-ABS questions whose hypothesis still took an abstention-like shape (`I don't have...`, `not enough information`, `cannot determine`, etc.) and remained non-correct.",
            "",
            "## Recommendation",
            "",
            f"- Recommendation: `{comparison['recommendation']}`",
            f"- Reason: {comparison['recommendation_reason']}",
            "",
        ]
    )

    if comparison["negative_protected_cell_deltas"]:
        lines.extend(
            [
                "Protected-cell regressions that triggered backout:",
                "",
            ]
        )
        for label, delta in cast(
            dict[str, float], comparison["negative_protected_cell_deltas"]
        ).items():
            lines.append(f"- `{label}`: `{delta:+.1%}`")
        lines.append("")

    lines.append(
        "Even without a protected-cell regression, this ablation stays non-promotable until the abstention target surface reaches the approved locked-case floor."
    )
    lines.append("")
    return "\n".join(lines)


async def run_sweep() -> dict[str, Any]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    baseline = baseline_payload()
    seed_checkpoint = read_json(SEED_CHECKPOINT_PATH)
    taxonomy_by_qid = taxonomy_lookup()
    subsets = subset_definitions(taxonomy_by_qid)

    evaluate_module.TOP_K_MEMORIES = TOP_K_OVERRIDE
    runner_module.TOP_K_MEMORIES = TOP_K_OVERRIDE

    pool, _store = await open_store()
    try:
        manifest: dict[str, Any] = {
            "generated_at": utc_now_iso(),
            "dataset_path": str(DATASET_PATH),
            "seed_checkpoint_path": str(SEED_CHECKPOINT_PATH),
            "shared_overrides": {
                "top_k_memories": TOP_K_OVERRIDE,
                "answer_prompt_guardrail": "memory_evidence_abstention_guardrail",
            },
            "coverage_gate": {
                "status": "blocked_insufficient_target_cell",
                "subset_veto_min_locked_cases": 5,
                "target_failure_cell": {
                    "category": "abstention",
                    "count": len(LOCKED_ABSTENTION_FAILURE_IDS),
                    "question_ids": list(LOCKED_ABSTENTION_FAILURE_IDS),
                },
                "secondary_cell": {
                    "stage": "generation-error",
                    "category": "temporal-reasoning",
                    "count": len(SECONDARY_TEMPORAL_GENERATION_ERROR_IDS),
                    "question_ids": list(SECONDARY_TEMPORAL_GENERATION_ERROR_IDS),
                },
            },
            "baseline": {
                "run1_strict_accuracy": baseline["run1"]["strict_accuracy"],
                "run2_strict_accuracy": baseline["run2"]["strict_accuracy"],
                "mean_strict_accuracy": baseline["mean_strict_accuracy"],
            },
            "runs": [],
        }

        for run_name, prompt_enabled in RUN_MODES:
            output_dir = _run_output_dir(run_name)
            output_dir.mkdir(parents=True, exist_ok=True)

            if _run_is_complete(output_dir):
                checkpoint = read_json(output_dir / CHECKPOINT_FILENAME)
                results = read_jsonl(output_dir / RESULTS_FILENAME)
                score_payload = read_json(output_dir / SCORE_FILENAME)
                summary = _build_run_summary(
                    run_name=run_name,
                    prompt_enabled=prompt_enabled,
                    results=results,
                    score_payload=score_payload,
                    checkpoint=checkpoint,
                    taxonomy_by_qid=taxonomy_by_qid,
                    subsets=subsets,
                    output_dir=output_dir,
                )
                manifest["runs"].append(_build_manifest_entry(run_name, output_dir, summary))
                continue

            checkpoint_path = output_dir / CHECKPOINT_FILENAME
            results_path = output_dir / RESULTS_FILENAME
            write_json(checkpoint_path, build_seed_checkpoint(seed_checkpoint))
            await reset_retrieval_side_effects(pool)

            with patched_answer_prompt(enabled=prompt_enabled):
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
                score_payload = runner.score()

            checkpoint = read_json(checkpoint_path)
            summary = _build_run_summary(
                run_name=run_name,
                prompt_enabled=prompt_enabled,
                results=results,
                score_payload=score_payload,
                checkpoint=checkpoint,
                taxonomy_by_qid=taxonomy_by_qid,
                subsets=subsets,
                output_dir=output_dir,
            )
            manifest["runs"].append(_build_manifest_entry(run_name, output_dir, summary))
            write_json(OUTPUT_ROOT / MANIFEST_FILENAME, manifest)

        manifest["runs"] = sorted(
            cast(list[dict[str, Any]], manifest["runs"]),
            key=lambda run: str(run["run_name"]),
        )
        runs_by_name = {
            str(run["run_name"]): run for run in cast(list[dict[str, Any]], manifest["runs"])
        }
        manifest["comparison"] = _comparison(runs_by_name["off"], runs_by_name["on"])
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
