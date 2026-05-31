from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import asyncpg

import orchestrator.config as config_module
import orchestrator.eval.runner as runner_module
import orchestrator.memory.retrieval as retrieval_module
import tests.longmemeval.evaluate as evaluate_module
from orchestrator.eval.runner import LongMemEvalRunner
from orchestrator.memory.dedup import deduplicate_facts
from orchestrator.memory.extraction import ExtractedFact
from tests.benchmark_longmemeval.top_k_sweep import (
    CHECKPOINT_FILENAME,
    RESULTS_FILENAME,
    RUN_SUMMARY_FILENAME,
    SCORE_FILENAME,
    baseline_payload,
    build_seed_checkpoint,
    judgment_map,
    open_store,
    read_json,
    read_jsonl,
    strict_accuracy,
    taxonomy_lookup,
    utc_now_iso,
    wait_for_retrieval_logs,
    write_json,
)
from tests.longmemeval.ingest import TEST_USER_ID, build_corpus_plan


DATASET_PATH = Path("tests/benchmark_longmemeval/fixtures/dev_subset.json")
OUTPUT_ROOT = Path("tests/benchmark_results/dev_sweep_dedup")
SOURCE_BASELINE_ROOT = Path("tests/benchmark_results/dev_sweep_temporal/on")
SOURCE_CHECKPOINT_PATH = SOURCE_BASELINE_ROOT / CHECKPOINT_FILENAME
BASELINE_ROOT = Path("tests/benchmark_results/dev_subset_baseline")

ANALYSIS_FILENAME = "ANALYSIS.md"
MANIFEST_FILENAME = "sweep_manifest.json"

CURRENT_RUN_NAME = "current"
TOP_K_OVERRIDE = 6

PROTECTED_CELLS: dict[str, str] = {
    "IE-user": "single-session-user",
    "IE-assistant": "single-session-assistant",
    "MR": "multi-session",
    "TR": "temporal-reasoning",
    "KU": "knowledge-update",
}

APPROVED_RETRIEVAL_TARGETS: tuple[str, ...] = (
    "retrieval_miss_multi_session",
    "retrieval_miss_single_session_user",
    "retrieval_miss_temporal_reasoning",
)
TRACKED_TARGET_SUBSET = "generation_error_knowledge_update"

CURRENT_THRESHOLDS: dict[str, float] = {
    "merge": 0.90,
    "supersede": 0.82,
    "same_slot": 0.65,
}

SWEEP_RUNS: tuple[tuple[str, dict[str, float]], ...] = (
    (
        "tight_01",
        {
            "merge": 0.92,
            "supersede": 0.85,
            "same_slot": 0.70,
        },
    ),
    (
        "tight_02",
        {
            "merge": 0.95,
            "supersede": 0.88,
            "same_slot": 0.75,
        },
    ),
)


@dataclass(frozen=True)
class ReplayConversation:
    corpus_key: str
    conversation_id: str | None
    extracted_facts: list[dict[str, Any]]


def _run_output_dir(run_name: str) -> Path:
    return OUTPUT_ROOT / run_name


def _run_is_complete(output_dir: Path) -> bool:
    required = (
        output_dir / CHECKPOINT_FILENAME,
        output_dir / RESULTS_FILENAME,
        output_dir / SCORE_FILENAME,
        output_dir / RUN_SUMMARY_FILENAME,
    )
    return all(path.exists() for path in required)


def _protected_cells_from_accuracy(accuracy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        label: {
            "metric_key": metric_key,
            "accuracy": float(accuracy.get(metric_key, 0.0)),
        }
        for metric_key, label in PROTECTED_CELLS.items()
    }


def _subset_definitions(taxonomy_by_qid: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    subsets: dict[str, list[str]] = {"locked_failure_union": sorted(taxonomy_by_qid)}
    for name, stage, category in (
        ("retrieval_miss_multi_session", "retrieval-miss", "multi-session"),
        ("retrieval_miss_single_session_user", "retrieval-miss", "single-session-user"),
        ("retrieval_miss_temporal_reasoning", "retrieval-miss", "temporal-reasoning"),
        (TRACKED_TARGET_SUBSET, "generation-error", "knowledge-update"),
    ):
        subsets[name] = sorted(
            qid
            for qid, entry in taxonomy_by_qid.items()
            if entry["stage"] == stage and entry["category"] == category
        )
    return subsets


def _normalize_effective_config(checkpoint: dict[str, Any]) -> dict[str, Any]:
    effective = copy.deepcopy(checkpoint["benchmark_effective_config"])
    effective["runtime"]["output_path"] = "<output>"
    effective["runtime"]["checkpoint_path"] = "<checkpoint>"
    effective["runtime"]["score_path"] = "<score>"
    dedup = effective["pinned_authority"]["canonical"]["dedup"]
    dedup.pop("merge_threshold", None)
    dedup.pop("supersede_threshold", None)
    dedup.pop("supersede_same_slot_threshold", None)
    return effective


def _expected_drift_warnings(thresholds: dict[str, float]) -> list[str]:
    warnings: list[str] = []
    if thresholds["merge"] != CURRENT_THRESHOLDS["merge"]:
        warnings.append(
            "canonical.dedup.merge_threshold: "
            f"pinned={CURRENT_THRESHOLDS['merge']} effective={thresholds['merge']}"
        )
    if thresholds["same_slot"] != CURRENT_THRESHOLDS["same_slot"]:
        warnings.append(
            "canonical.dedup.supersede_same_slot_threshold: "
            f"pinned={CURRENT_THRESHOLDS['same_slot']} effective={thresholds['same_slot']}"
        )
    if thresholds["supersede"] != CURRENT_THRESHOLDS["supersede"]:
        warnings.append(
            "canonical.dedup.supersede_threshold: "
            f"pinned={CURRENT_THRESHOLDS['supersede']} effective={thresholds['supersede']}"
        )
    warnings.append("shared.retrieval.call_contract.top_k_memories: pinned=5 effective=6")
    return warnings


@contextmanager
def patched_benchmark_state(thresholds: dict[str, float]) -> Iterator[None]:
    env_keys = {
        "DEDUP_MERGE_THRESHOLD": str(thresholds["merge"]),
        "DEDUP_SUPERSEDE_THRESHOLD": str(thresholds["supersede"]),
        "DEDUP_SUPERSEDE_SAME_SLOT_THRESHOLD": str(thresholds["same_slot"]),
    }
    original_env = {key: os.environ.get(key) for key in env_keys}
    original_values = {
        "evaluate_top_k": evaluate_module.TOP_K_MEMORIES,
        "runner_top_k": runner_module.TOP_K_MEMORIES,
        "temporal_filter": retrieval_module.TEMPORAL_QUERY_FILTER_ENABLED,
    }
    try:
        for key, value in env_keys.items():
            os.environ[key] = value
        config_module.get_settings.cache_clear()
        evaluate_module.TOP_K_MEMORIES = TOP_K_OVERRIDE
        runner_module.TOP_K_MEMORIES = TOP_K_OVERRIDE
        retrieval_module.TEMPORAL_QUERY_FILTER_ENABLED = True
        yield
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config_module.get_settings.cache_clear()
        evaluate_module.TOP_K_MEMORIES = cast(int, original_values["evaluate_top_k"])
        runner_module.TOP_K_MEMORIES = cast(int, original_values["runner_top_k"])
        retrieval_module.TEMPORAL_QUERY_FILTER_ENABLED = bool(original_values["temporal_filter"])


def _coerce_fact_list(value: Any) -> list[dict[str, Any]]:
    raw_value = value
    if isinstance(value, str):
        raw_value = json.loads(value)
    if not isinstance(raw_value, list):
        return []
    return [dict(item) for item in raw_value if isinstance(item, dict)]


async def _fetch_replay_plan(
    pool: asyncpg.Pool,
    *,
    dataset: list[dict[str, Any]],
    checkpoint: dict[str, Any],
) -> list[ReplayConversation]:
    corpus_plan = build_corpus_plan(dataset)
    ingest_results = checkpoint["phases"]["ingest"]["results"]
    conversation_ids = [
        value["conversation_id"]
        for value in ingest_results.values()
        if isinstance(value, dict) and value.get("conversation_id")
    ]

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (conversation_id) conversation_id, extracted_facts
            FROM memory_extraction_log
            WHERE user_id = $1::uuid
              AND conversation_id = ANY($2::uuid[])
            ORDER BY conversation_id, created_at DESC
            """,
            TEST_USER_ID,
            conversation_ids,
        )

    facts_by_conversation = {
        str(row["conversation_id"]): _coerce_fact_list(row["extracted_facts"])
        for row in rows
        if row.get("conversation_id") is not None
    }

    replay_plan: list[ReplayConversation] = []
    for session in corpus_plan.corpus_sessions:
        ingest_result = ingest_results.get(session.corpus_key, {})
        conversation_id = ingest_result.get("conversation_id")
        replay_plan.append(
            ReplayConversation(
                corpus_key=session.corpus_key,
                conversation_id=conversation_id,
                extracted_facts=facts_by_conversation.get(str(conversation_id), []),
            )
        )
    return replay_plan


async def _reset_memory_state(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retrieval_log WHERE user_id = $1::uuid AND retrieval_triggered_by = $2",
            TEST_USER_ID,
            "longmemeval",
        )
        await conn.execute("DELETE FROM memories WHERE user_id = $1::uuid", TEST_USER_ID)


async def _replay_extracted_facts(
    store: Any,
    replay_plan: list[ReplayConversation],
) -> dict[str, Any]:
    fact_count = 0
    conversations_with_facts = 0
    dedup_totals = {"merged": 0, "superseded": 0, "new": 0}
    replay_failures: dict[str, str] = {}

    for entry in replay_plan:
        if entry.conversation_id is None or not entry.extracted_facts:
            continue
        facts = [
            ExtractedFact(
                content=str(fact.get("content", "")),
                category=str(fact.get("category", "fact")),
                confidence=float(fact.get("confidence", 0.8)),
                slot=(str(fact["slot"]) if fact.get("slot") is not None else None),
            )
            for fact in entry.extracted_facts
        ]
        if not facts:
            continue
        try:
            result = await deduplicate_facts(
                store=store,
                user_id=TEST_USER_ID,
                facts=facts,
                conversation_id=uuid.UUID(entry.conversation_id),
                status="active",
            )
        except RuntimeError as error:
            replay_failures[entry.corpus_key] = str(error)
            continue
        fact_count += len(facts)
        conversations_with_facts += 1
        dedup_totals["merged"] += len(result.merged)
        dedup_totals["superseded"] += len(result.superseded)
        dedup_totals["new"] += len(result.new)

    return {
        "replayed_conversations": len(replay_plan),
        "conversations_with_facts": conversations_with_facts,
        "replayed_fact_count": fact_count,
        "replay_failures": replay_failures,
        "failed_conversations": len(replay_failures),
        "dedup_totals": dedup_totals,
    }


def _apply_replay_failures_to_checkpoint(
    checkpoint: dict[str, Any], replay_stats: dict[str, Any]
) -> None:
    ingest_results = checkpoint["phases"]["ingest"]["results"]
    for corpus_key, error in replay_stats.get("replay_failures", {}).items():
        ingest_result = ingest_results.get(corpus_key)
        if not isinstance(ingest_result, dict):
            continue
        ingest_result["status"] = "extraction_failed"
        ingest_result["error"] = error


async def _memory_counts(pool: asyncpg.Pool) -> dict[str, int]:
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE user_id = $1::uuid", TEST_USER_ID
        )
        active = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE user_id = $1::uuid AND valid_to IS NULL",
            TEST_USER_ID,
        )
        historical = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE user_id = $1::uuid AND valid_to IS NOT NULL",
            TEST_USER_ID,
        )
    return {
        "total": int(total or 0),
        "active": int(active or 0),
        "historical": int(historical or 0),
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

    approved_target_cells = {
        subset_name: _subset_delta(
            subsets[subset_name],
            current_judgments=current_judgments,
            sweep_judgments=sweep_judgments,
        )
        for subset_name in APPROVED_RETRIEVAL_TARGETS
    }
    tracked_target = _subset_delta(
        subsets[TRACKED_TARGET_SUBSET],
        current_judgments=current_judgments,
        sweep_judgments=sweep_judgments,
    )
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
        for subset_name, values in approved_target_cells.items()
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
        f"approved_target:{subset_name} {delta:+d}"
        for subset_name, delta in negative_target_deltas.items()
    )

    qualifying_improvement = tracked_target["delta_vs_current"] > 0 and not regressions

    return {
        "strict_accuracy": strict,
        "strict_delta_vs_current": strict_delta_vs_current,
        "approved_target_cells": approved_target_cells,
        "tracked_target_cell": tracked_target,
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
    run_name: str,
    thresholds: dict[str, float],
    replay_stats: dict[str, Any],
    memory_counts: dict[str, int],
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
    current_memory_counts = current["memory_counts"]
    summary = {
        "run_name": run_name,
        "thresholds": thresholds,
        "top_k": TOP_K_OVERRIDE,
        "temporal_filter_enabled": True,
        "result_count": len(results),
        "strict_accuracy": comparison["strict_accuracy"],
        "strict_delta_vs_current": comparison["strict_delta_vs_current"],
        "strict_delta_vs_run1": comparison["strict_accuracy"] - baseline["run1"]["strict_accuracy"],
        "strict_delta_vs_run2": comparison["strict_accuracy"] - baseline["run2"]["strict_accuracy"],
        "accuracy": score_payload["accuracy"],
        "benchmark_config_drift_warnings": checkpoint["benchmark_config_drift_warnings"],
        "expected_drift_warnings": _expected_drift_warnings(thresholds),
        "approved_target_cells": comparison["approved_target_cells"],
        "tracked_target_cell": comparison["tracked_target_cell"],
        "locked_failure_union": comparison["locked_failure_union"],
        "protected_cells": comparison["protected_cells"],
        "protected_deltas_vs_current": comparison["protected_deltas_vs_current"],
        "negative_protected_deltas": comparison["negative_protected_deltas"],
        "negative_target_deltas": comparison["negative_target_deltas"],
        "regressions": comparison["regressions"],
        "qualifying_improvement": comparison["qualifying_improvement"],
        "memory_counts": memory_counts,
        "memory_count_delta_vs_current": {
            key: memory_counts[key] - current_memory_counts[key] for key in current_memory_counts
        },
        "replay_stats": replay_stats,
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
        **summary,
    }


def _recommendation(current: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    alternatives = [run for run in runs if run["run_name"] != CURRENT_RUN_NAME]
    qualifying = [run for run in alternatives if run["qualifying_improvement"]]
    if not qualifying:
        best_non_qualifying = max(
            alternatives,
            key=lambda run: (
                run["tracked_target_cell"]["delta_vs_current"],
                run["strict_accuracy"],
                run["locked_failure_union"]["sweep_correct"],
                -run["memory_count_delta_vs_current"]["total"],
            ),
        )
        return {
            "current_thresholds_remain": True,
            "recommended_run": CURRENT_RUN_NAME,
            "recommended_thresholds": current["thresholds"],
            "reason": (
                "No tighter dedup point improved the tracked `generation-error × knowledge-update` subset "
                "without introducing strict-score, locked-failure, protected-cell, or approved-target regressions."
            ),
            "best_non_qualifying_run": best_non_qualifying["run_name"],
            "best_non_qualifying_thresholds": best_non_qualifying["thresholds"],
        }

    recommended = max(
        qualifying,
        key=lambda run: (
            run["tracked_target_cell"]["delta_vs_current"],
            run["strict_accuracy"],
            run["locked_failure_union"]["sweep_correct"],
            -run["memory_count_delta_vs_current"]["total"],
        ),
    )
    return {
        "current_thresholds_remain": False,
        "recommended_run": recommended["run_name"],
        "recommended_thresholds": recommended["thresholds"],
        "reason": (
            "This tighter point improved the tracked freshness-sensitive subset while avoiding the locked dev-subset regression gates."
        ),
    }


def build_analysis_markdown(manifest: dict[str, Any]) -> str:
    current = manifest["current_baseline"]
    runs = manifest["runs"]
    recommendation = manifest["recommendation"]
    lines = [
        "# Dedup Threshold Sensitivity Dev Sweep Analysis",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "This sweep replays the locked canonical extracted facts through live `dedup.py` while pinning `TOP_K_MEMORIES = 6`, `TEMPORAL_QUERY_FILTER_ENABLED = True`, `MIN_FINAL_SCORE = 0.15`, and the current hybrid ranking weights.",
        "That keeps extraction output fixed so dedup thresholds are the only moving part.",
        "",
        "## Sweep gate",
        "",
        "A second tighter point only runs if the first tighter point improves the tracked `generation-error × knowledge-update` subset and avoids regressions in strict accuracy, locked-failure-union recovery, protected primary-category accuracy, and the approved retrieval-miss target cells.",
        "",
        "## Score and memory-count summary",
        "",
        "| Run | Thresholds (`merge / supersede / same-slot`) | Strict score | Δ vs current | Tracked KU generation-error | Locked failure union | Total memories | Δ memories | Active memories | Δ active | Regressions |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    current_regressions = "none"
    lines.append(
        "| current | "
        f"{current['thresholds']['merge']:.2f} / {current['thresholds']['supersede']:.2f} / {current['thresholds']['same_slot']:.2f} | "
        f"{current['strict_accuracy']:.1%} | +0.0% | "
        f"{current['tracked_target_cell']['current_correct']}/{current['tracked_target_cell']['total']} | "
        f"{current['locked_failure_union']['current_correct']}/{current['locked_failure_union']['total']} | "
        f"{current['memory_counts']['total']} | +0 | {current['memory_counts']['active']} | +0 | {current_regressions} |"
    )

    for run in runs:
        if run["run_name"] == CURRENT_RUN_NAME:
            continue
        regressions = ", ".join(run["regressions"]) if run["regressions"] else "none"
        lines.append(
            "| "
            f"{run['run_name']} | "
            f"{run['thresholds']['merge']:.2f} / {run['thresholds']['supersede']:.2f} / {run['thresholds']['same_slot']:.2f} | "
            f"{run['strict_accuracy']:.1%} | "
            f"{run['strict_delta_vs_current']:+.1%} | "
            f"{run['tracked_target_cell']['sweep_correct']}/{run['tracked_target_cell']['total']} "
            f"({run['tracked_target_cell']['delta_vs_current']:+d}) | "
            f"{run['locked_failure_union']['sweep_correct']}/{run['locked_failure_union']['total']} "
            f"({run['locked_failure_union']['delta_vs_current']:+d}) | "
            f"{run['memory_counts']['total']} | "
            f"{run['memory_count_delta_vs_current']['total']:+d} | "
            f"{run['memory_counts']['active']} | "
            f"{run['memory_count_delta_vs_current']['active']:+d} | "
            f"{regressions} |"
        )

    lines.extend(
        [
            "",
            "## Replay integrity",
            "",
            f"- Source canonical state: `{manifest['source_reference']['output_dir']}` using `{manifest['source_reference']['checkpoint_path']}`.",
            f"- Replayed corpus conversations: `{manifest['source_reference']['replayed_conversations']}` with `{manifest['source_reference']['conversations_with_facts']}` conversations carrying extracted facts.",
            f"- Current replay post-ingestion memories: `{current['memory_counts']['total']}` total / `{current['memory_counts']['active']}` active / `{current['memory_counts']['historical']}` historical.",
            f"- Current replay failures carried into ingest metadata: `{current['replay_stats'].get('failed_conversations', 0)}`.",
            "",
            "## Recommendation",
            "",
        ]
    )

    if recommendation["current_thresholds_remain"]:
        lines.extend(
            [
                "- Verdict: `current dedup thresholds remain`",
                f"- Reason: {recommendation['reason']}",
                f"- Best non-qualifying alternative: `{recommendation['best_non_qualifying_run']}` at `{recommendation['best_non_qualifying_thresholds']}`",
            ]
        )
    else:
        lines.extend(
            [
                "- Verdict: `a tighter dedup point qualifies for follow-up`",
                f"- Recommended run: `{recommendation['recommended_run']}`",
                f"- Recommended thresholds: `{recommendation['recommended_thresholds']}`",
                f"- Reason: {recommendation['reason']}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


async def _run_single_point(
    pool: asyncpg.Pool,
    store: Any,
    *,
    run_name: str,
    thresholds: dict[str, float],
    replay_plan: list[ReplayConversation],
    seed_checkpoint: dict[str, Any],
    current: dict[str, Any],
    baseline: dict[str, Any],
    subsets: dict[str, list[str]],
) -> dict[str, Any]:
    output_dir = _run_output_dir(run_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    if _run_is_complete(output_dir):
        summary = read_json(output_dir / RUN_SUMMARY_FILENAME)
        return _build_manifest_entry(run_name, output_dir, summary)

    await _reset_memory_state(pool)

    with patched_benchmark_state(thresholds):
        replay_stats = await _replay_extracted_facts(store, replay_plan)
        memory_counts = await _memory_counts(pool)

        checkpoint_path = output_dir / CHECKPOINT_FILENAME
        results_path = output_dir / RESULTS_FILENAME
        checkpoint_payload = build_seed_checkpoint(seed_checkpoint)
        _apply_replay_failures_to_checkpoint(checkpoint_payload, replay_stats)
        write_json(checkpoint_path, checkpoint_payload)

        runner = LongMemEvalRunner(
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

    checkpoint = read_json(output_dir / CHECKPOINT_FILENAME)
    results = read_jsonl(output_dir / RESULTS_FILENAME)
    score_payload = read_json(output_dir / SCORE_FILENAME)
    summary = _build_run_summary(
        run_name=run_name,
        thresholds=thresholds,
        replay_stats=replay_stats,
        memory_counts=memory_counts,
        results=results,
        score_payload=score_payload,
        checkpoint=checkpoint,
        current=current,
        baseline=baseline,
        subsets=subsets,
        output_dir=output_dir,
    )
    return _build_manifest_entry(run_name, output_dir, summary)


async def run_sweep() -> dict[str, Any]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    dataset = read_json(DATASET_PATH)
    if not isinstance(dataset, list):
        raise ValueError(f"Dataset must be a JSON list: {DATASET_PATH}")

    seed_checkpoint = read_json(SOURCE_CHECKPOINT_PATH)
    baseline = baseline_payload()
    taxonomy_by_qid = taxonomy_lookup()
    subsets = _subset_definitions(taxonomy_by_qid)

    pool, store = await open_store()
    try:
        replay_plan = await _fetch_replay_plan(pool, dataset=dataset, checkpoint=seed_checkpoint)
        source_reference = {
            "output_dir": str(SOURCE_BASELINE_ROOT),
            "checkpoint_path": str(SOURCE_CHECKPOINT_PATH),
            "replayed_conversations": len(replay_plan),
            "conversations_with_facts": sum(1 for entry in replay_plan if entry.extracted_facts),
        }

        current_output_dir = _run_output_dir(CURRENT_RUN_NAME)
        if _run_is_complete(current_output_dir):
            current_summary = read_json(current_output_dir / RUN_SUMMARY_FILENAME)
        else:
            await _reset_memory_state(pool)
            with patched_benchmark_state(CURRENT_THRESHOLDS):
                replay_stats = await _replay_extracted_facts(store, replay_plan)
                memory_counts = await _memory_counts(pool)

                checkpoint_path = current_output_dir / CHECKPOINT_FILENAME
                results_path = current_output_dir / RESULTS_FILENAME
                current_output_dir.mkdir(parents=True, exist_ok=True)
                checkpoint_payload = build_seed_checkpoint(seed_checkpoint)
                _apply_replay_failures_to_checkpoint(checkpoint_payload, replay_stats)
                write_json(checkpoint_path, checkpoint_payload)

                runner = LongMemEvalRunner(
                    dataset_path=DATASET_PATH,
                    output_path=results_path,
                    checkpoint_path=checkpoint_path,
                    score_path=current_output_dir / SCORE_FILENAME,
                    limit=None,
                    force_retrieval_logging=True,
                )
                results = await runner.evaluate()
                await wait_for_retrieval_logs(pool, expected_count=len(results))
                _ = runner.score()

            checkpoint = read_json(current_output_dir / CHECKPOINT_FILENAME)
            results = read_jsonl(current_output_dir / RESULTS_FILENAME)
            score_payload = read_json(current_output_dir / SCORE_FILENAME)
            current_seed = {
                "summary": {
                    "strict_accuracy": strict_accuracy(results),
                },
                "score_payload": score_payload,
                "results": results,
                "judgments": judgment_map(results),
                "protected_cells": _protected_cells_from_accuracy(score_payload["accuracy"]),
                "memory_counts": memory_counts,
                "thresholds": CURRENT_THRESHOLDS,
            }
            current_summary = _build_run_summary(
                run_name=CURRENT_RUN_NAME,
                thresholds=CURRENT_THRESHOLDS,
                replay_stats=replay_stats,
                memory_counts=memory_counts,
                results=results,
                score_payload=score_payload,
                checkpoint=checkpoint,
                current=current_seed,
                baseline=baseline,
                subsets=subsets,
                output_dir=current_output_dir,
            )

        current_results = read_jsonl(current_output_dir / RESULTS_FILENAME)
        current_score = read_json(current_output_dir / SCORE_FILENAME)
        current = {
            "summary": current_summary,
            "results": current_results,
            "score_payload": current_score,
            "judgments": judgment_map(current_results),
            "protected_cells": _protected_cells_from_accuracy(current_score["accuracy"]),
            "memory_counts": current_summary["memory_counts"],
            "thresholds": current_summary["thresholds"],
        }

        manifest: dict[str, Any] = {
            "generated_at": utc_now_iso(),
            "dataset_path": str(DATASET_PATH),
            "source_reference": source_reference,
            "baseline": {
                "run1_strict_accuracy": baseline["run1"]["strict_accuracy"],
                "run2_strict_accuracy": baseline["run2"]["strict_accuracy"],
                "mean_strict_accuracy": baseline["mean_strict_accuracy"],
            },
            "shared_overrides": {
                "top_k_memories": TOP_K_OVERRIDE,
                "temporal_filter_enabled": True,
                "initial_vector_candidates": retrieval_module.INITIAL_VECTOR_CANDIDATES,
                "min_final_score": retrieval_module.MIN_FINAL_SCORE,
                "hybrid_vector_weight": retrieval_module.HYBRID_VECTOR_WEIGHT,
                "hybrid_bm25_weight": retrieval_module.HYBRID_BM25_WEIGHT,
                "hybrid_recency_confidence_weight": retrieval_module.HYBRID_RECENCY_CONFIDENCE_WEIGHT,
            },
            "current_baseline": {
                "run_name": CURRENT_RUN_NAME,
                "output_dir": str(current_output_dir),
                "checkpoint_path": str(current_output_dir / CHECKPOINT_FILENAME),
                "results_path": str(current_output_dir / RESULTS_FILENAME),
                "score_path": str(current_output_dir / SCORE_FILENAME),
                "run_summary_path": str(current_output_dir / RUN_SUMMARY_FILENAME),
                **current_summary,
            },
            "runs": [_build_manifest_entry(CURRENT_RUN_NAME, current_output_dir, current_summary)],
        }

        first_run_name, first_thresholds = SWEEP_RUNS[0]
        first_entry = await _run_single_point(
            pool,
            store,
            run_name=first_run_name,
            thresholds=first_thresholds,
            replay_plan=replay_plan,
            seed_checkpoint=seed_checkpoint,
            current=current,
            baseline=baseline,
            subsets=subsets,
        )
        manifest["runs"].append(first_entry)

        first_summary = read_json(Path(first_entry["run_summary_path"]))
        manifest["second_point_executed"] = bool(first_summary["qualifying_improvement"])

        if manifest["second_point_executed"]:
            second_run_name, second_thresholds = SWEEP_RUNS[1]
            second_entry = await _run_single_point(
                pool,
                store,
                run_name=second_run_name,
                thresholds=second_thresholds,
                replay_plan=replay_plan,
                seed_checkpoint=seed_checkpoint,
                current=current,
                baseline=baseline,
                subsets=subsets,
            )
            manifest["runs"].append(second_entry)

        manifest["recommendation"] = _recommendation(current_summary, manifest["runs"])
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
