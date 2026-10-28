"""Judge saved Phase D DEV hypotheses only, without retrieval or database writes.

One fresh output directory per run; deliberately no automatic resume or retry.
On failure, retain the pending call and its whole-run cost reservation for operator
reconciliation. Never use this lane as evidence of a new reader/retrieval result.
An explicitly authorized fresh residual run can exclude verified completed receipts;
its uncertain question requires a separately declared new replacement judgment.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from orchestrator.eval.bench_db import check_benchmark_dsn
from orchestrator.eval.durable_io import append_jsonl_record, atomic_write_json
from orchestrator.eval.run_manifest import git_state
from tests.longmemeval import evaluate
from tests.longmemeval.official_judge import (
    OFFICIAL_MAX_TOKENS,
    OFFICIAL_SOURCE_COMMIT,
    build_official_prompt,
    score_dual_judgments,
)

ROOT = Path(__file__).resolve().parents[2]
DEV_PATH = ROOT / "tests/benchmark_longmemeval/fixtures/dev_subset.json"
DEV_SHA256 = "6612e3a03c79b92bab2f1750d4661908e3dc37359715b7fb6b62c6f758bddb34"
BASELINE = ROOT / "tests/benchmark_results/longmemeval_phase_d_2026-10-27"
ARMS = {"a": "eval_a_qualified", "a2": "eval_a2_qualified", "b": "eval_b_lexical"}
# Public Azure endpoint quote checked 2026-10-28, undiscounted USD/token.
# https://openrouter.ai/api/v1/models/openai/gpt-4o-2024-08-06/endpoints
INPUT_RATE = Decimal("0.0000025")
OUTPUT_RATE = Decimal("0.00001")
AUTHORIZATION_USD = Decimal("10")


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(arm: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reject anything except the pinned 50-row dev set and complete source arm."""
    if sha256(DEV_PATH) != DEV_SHA256:
        raise ValueError("Dev dataset pin changed")
    dev = json.loads(DEV_PATH.read_bytes())
    references = {row["question_id"]: row for row in dev}
    source = BASELINE / ARMS[arm]
    rows = [
        json.loads(line) for line in (source / evaluate.RESULTS_FILENAME).read_text().splitlines()
    ]
    manifest = json.loads((source / "run_manifest.json").read_bytes())
    if not manifest.get("baseline_qualified") or manifest.get("disqualifications"):
        raise ValueError("Source arm is not qualified")
    if len(dev) != 50 or len(rows) != 50 or len(references) != 50:
        raise ValueError("Expected the frozen 50-row dev arm")
    if {row["question_id"] for row in rows} != set(references):
        raise ValueError("Source arm IDs differ from dev")
    for row in rows:
        reference = references[row["question_id"]]
        if (
            "error" in row
            or not isinstance(row.get("hypothesis"), str)
            or row.get("question") != reference["question"]
            or row.get("reference") != reference["answer"]
            or row.get("category") != evaluate.CATEGORY_MAP[reference["question_type"]]
        ):
            raise ValueError("Source arm question/reference/category or completion is invalid")
        row["question_type"] = reference["question_type"]
        if (
            hashlib.sha256(row["memory_context"].encode()).hexdigest()
            != row["memory_context_sha256"]
        ):
            raise ValueError("Source memory-context hash mismatch")
    return rows, {
        "source_run_id": manifest["run_id"],
        "source_results_sha256": sha256(source / evaluate.RESULTS_FILENAME),
        "source_manifest_sha256": sha256(source / "run_manifest.json"),
        "dev_sha256": DEV_SHA256,
    }


def build_prompt(row: dict[str, Any], metric: str) -> str:
    if metric == "generic":
        return evaluate.build_generic_judge_prompt(
            row["question"], row["hypothesis"], row["reference"]
        )
    if metric != "official":
        raise ValueError("Unknown judge metric")
    return build_official_prompt(
        row["question_type"],
        row["question"],
        row["reference"],
        row["hypothesis"],
        question_id=row["question_id"],
    )


def cost_reservation(rows: list[dict[str, Any]], metric: str, prior: Decimal) -> Decimal:
    if not prior.is_finite() or prior < 0:
        raise ValueError("Prior cumulative cost reservation must be finite and nonnegative")
    cap = evaluate.JUDGE_MAX_TOKENS if metric == "generic" else OFFICIAL_MAX_TOKENS
    # GPT-4o byte-level tokenization cannot use more prompt tokens than UTF-8
    # bytes. Add 64 tokens/call for the single-user-message chat envelope.
    prompt_bound = sum(len(build_prompt(row, metric).encode()) + 64 for row in rows)
    reserve = prompt_bound * INPUT_RATE + len(rows) * cap * OUTPUT_RATE
    if prior + reserve > AUTHORIZATION_USD:
        raise ValueError("Run exceeds cumulative $10 authorization; obtain owner approval")
    return reserve


def reconcile_residual(
    rows: list[dict[str, Any]],
    source: dict[str, Any],
    arm: str,
    metric: str,
    failed_dir: Path,
    replacement_question_id: str,
    prior: Decimal,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate a stopped first-run prefix without modifying or replaying receipts."""
    manifest_path = failed_dir / "run_manifest.json"
    results_path = failed_dir / evaluate.RESULTS_FILENAME
    raw_manifest, raw_results = manifest_path.read_bytes(), results_path.read_bytes()
    manifest = json.loads(raw_manifest)
    receipts = [json.loads(line) for line in raw_results.splitlines()]
    if (
        metric != "generic"
        or manifest.get("metric") != metric
        or manifest.get("arm") != arm
        or manifest.get("mode") != "judge_only_saved_dev_hypotheses"
        or manifest.get("status") != "stopped_reconciliation_required"
        or manifest.get("source") != source
        or manifest.get("calls_planned") != len(rows)
        or manifest.get("calls_completed") != len(receipts)
        or manifest.get("residual") is not None
        or not isinstance(manifest.get("run_id"), str)
        or not manifest["run_id"]
    ):
        raise ValueError("Failed run does not match a stopped original generic dev arm")
    held = Decimal(manifest["cost"]["cumulative_reserved_usd"])
    if not held.is_finite() or held <= 0 or not prior.is_finite() or prior < held:
        raise ValueError("Retain at least the failed run's cumulative reservation")
    if not 0 <= len(receipts) < len(rows):
        raise ValueError("No bounded residual remains")
    fields = (
        "question_id",
        "question",
        "reference",
        "hypothesis",
        "category",
        "question_type",
        "memory_context_sha256",
        "system_prompt_sha256",
    )
    for receipt, original in zip(receipts, rows, strict=False):
        if (
            any(receipt.get(key) != original[key] for key in fields)
            or receipt.get("original_judgment") != original["judgment"]
            or receipt.get("judgment") not in {"correct", "partially_correct", "incorrect"}
            or receipt.get("run_id") != manifest["run_id"]
        ):
            raise ValueError("Completed receipts are not the verified original dev prefix")
    pending = rows[len(receipts)]["question_id"]
    if manifest.get("pending_question_id") != pending or replacement_question_id != pending:
        raise ValueError("Explicit new replacement must match the uncertain next question")
    remaining = rows[len(receipts) :]
    return remaining, {
        "original_run_id": manifest["run_id"],
        "failed_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "failed_results_sha256": hashlib.sha256(raw_results).hexdigest(),
        "verified_completed_question_ids": [r["question_id"] for r in receipts],
        "remaining_question_ids": [r["question_id"] for r in remaining],
        "uncertain_original_question_id": pending,
        "original_request_outcome": "unknown; original whole-run reservation retained",
        "new_replacement_explicitly_authorized": True,
        "note": "Fresh residual measurement, not resume/replay of the original batch",
    }


async def run(
    arm: str,
    metric: str,
    output: Path,
    prior: Decimal,
    *,
    residual_from: Path | None = None,
    replacement_question_id: str | None = None,
) -> dict[str, Any]:
    rows, source = load_inputs(arm)
    residual = None
    if (residual_from is None) != (replacement_question_id is None):
        raise ValueError("Residual source and explicit replacement must be supplied together")
    if residual_from is not None and replacement_question_id is not None:
        rows, residual = reconcile_residual(
            rows, source, arm, metric, residual_from, replacement_question_id, prior
        )
    reserve = cost_reservation(rows, metric, prior)
    # No DB connection is made. Still refuse production settings, and require
    # the same benchmark-only environment as the canonical runner.
    check_benchmark_dsn(evaluate.get_settings().database_url)
    if (ROOT / ".env").exists():
        raise ValueError("Run from a checkout without .env")
    code = git_state(ROOT)
    if code.get("dirty"):
        raise ValueError("Commit the measurement code before a paid run")
    output.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "run_id": uuid.uuid4().hex,
        "started_at": now(),
        "status": "running",
        "mode": "judge_only_saved_dev_hypotheses",
        "arm": arm,
        "metric": metric,
        "residual": residual,
        "source": source,
        "git": code,
        "calls_planned": len(rows),
        "calls_completed": 0,
        "database_access": False,
        "reader_calls": 0,
        "memory_context": "unchanged saved Phase D contexts; no retrieval",
        "generic_comparator": "stored Phase D judgment"
        if metric == "official"
        else "fresh generic",
        "route": {
            "model": evaluate.BENCHMARK_JUDGE_MODEL,
            "endpoint_slug": evaluate.BENCHMARK_ANSWER_ENDPOINT_SLUG,
            "temperature": 0,
            "seed": evaluate.BENCHMARK_SEED,
            "max_tokens": evaluate.JUDGE_MAX_TOKENS if metric == "generic" else OFFICIAL_MAX_TOKENS,
            "num_retries": 0,
            "allow_fallbacks": False,
            "official_prompt_source_commit": OFFICIAL_SOURCE_COMMIT
            if metric == "official"
            else None,
        },
        "cost": {
            "authorization_usd": str(AUTHORIZATION_USD),
            "prior_cumulative_reserved_usd": str(prior),
            "whole_run_reservation_usd": str(reserve),
            "cumulative_reserved_usd": str(prior + reserve),
            "undiscounted_input_usd_per_token": str(INPUT_RATE),
            "undiscounted_output_usd_per_token": str(OUTPUT_RATE),
            "price_quote_checked": "2026-10-28",
            "price_quote_source": "https://openrouter.ai/api/v1/models/openai/gpt-4o-2024-08-06/endpoints",
            "usage_cost_estimate_usd": "0",
            "actual_billed_cost_usd": None,
            "note": "Reservation retained even on failure; usage-based undiscounted estimate is not a billing receipt.",
        },
    }
    path = output / "run_manifest.json"
    atomic_write_json(path, manifest)
    completed: list[dict[str, Any]] = []
    usage_cost = Decimal("0")
    usage_complete = True
    evaluate.reset_benchmark_tracking()
    try:
        for row in rows:
            manifest["pending_question_id"] = row["question_id"]
            manifest["pending_started_at"] = now()
            atomic_write_json(path, manifest)
            # Keep original three-way judgment and both source hashes intact.
            result = {
                key: row[key]
                for key in (
                    "question_id",
                    "question",
                    "reference",
                    "hypothesis",
                    "category",
                    "question_type",
                    "judgment",
                    "memory_context_sha256",
                    "system_prompt_sha256",
                )
            }
            result["original_judgment"] = row["judgment"]
            if residual is not None:
                result["residual_of_run_id"] = residual["original_run_id"]
                result["new_replacement_for_uncertain_request"] = (
                    row["question_id"] == residual["uncertain_original_question_id"]
                )
            if metric == "official":
                result.update(
                    await evaluate.judge_answer_official(
                        row["question"],
                        row["hypothesis"],
                        row["reference"],
                        question_id=row["question_id"],
                        question_type=row["question_type"],
                    )
                )
                usage = result["official_judge_usage"]
            else:
                judgment, usage = await evaluate._judge_with_usage(
                    row["question"],
                    row["hypothesis"],
                    row["reference"],
                    benchmark_mode=True,
                )
                metadata = evaluate.get_benchmark_tracking().get("judge", {})
                result.update(
                    {
                        "judgment": judgment,
                        "judge_usage": usage,
                        "judge_raw_output": metadata.get("raw_output"),
                        "judge_finish_reason": metadata.get("finish_reason"),
                        "judge_fingerprint": metadata.get("fingerprint"),
                        "judge_model": metadata.get("model"),
                    }
                )
            if "prompt_tokens" in usage and "completion_tokens" in usage:
                usage_cost += (
                    usage["prompt_tokens"] * INPUT_RATE + usage["completion_tokens"] * OUTPUT_RATE
                )
            else:
                usage_complete = False
            result["run_id"] = manifest["run_id"]
            result["evaluated_at"] = now()
            append_jsonl_record(output / evaluate.RESULTS_FILENAME, result)
            completed.append(result)
            manifest["calls_completed"] = len(completed)
            if usage_complete:
                manifest["pending_question_id"] = None
            manifest["cost"]["usage_cost_estimate_usd"] = (
                str(usage_cost) if usage_complete else None
            )
            atomic_write_json(path, manifest)
            if not usage_complete:
                raise RuntimeError("Missing judge usage; retain receipt and reconcile cost")
            if usage_cost > reserve:
                raise RuntimeError("Observed usage exceeded reservation; stop and reconcile cost")
        summary = score_dual_judgments(completed)
        atomic_write_json(output / "dual_judge_score.json", summary)
    except BaseException as exc:
        manifest["status"] = "stopped_reconciliation_required"
        manifest["error_type"] = type(exc).__name__
        manifest["failure_diagnostics"] = evaluate.benchmark_failure_diagnostics(exc)
        manifest["ended_at"] = now()
        atomic_write_json(path, manifest)
        raise
    manifest["status"] = "completed"
    manifest["ended_at"] = now()
    atomic_write_json(path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=tuple(ARMS))
    parser.add_argument("--metric", required=True, choices=("generic", "official"))
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prior-reserved-usd", required=True, type=Decimal)
    parser.add_argument("--residual-from", type=Path)
    parser.add_argument("--replacement-question-id")
    args = parser.parse_args()
    try:
        manifest = asyncio.run(
            run(
                args.arm,
                args.metric,
                args.output_dir,
                args.prior_reserved_usd,
                residual_from=args.residual_from,
                replacement_question_id=args.replacement_question_id,
            )
        )
    except Exception as exc:
        # No provider exception text, headers, keys or response bodies in stderr.
        raise SystemExit(
            f"Judge-only run stopped: {type(exc).__name__}; inspect retained manifest"
        ) from None
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "calls_completed": manifest["calls_completed"],
                "cost": manifest["cost"],
            }
        )
    )


if __name__ == "__main__":
    main()
