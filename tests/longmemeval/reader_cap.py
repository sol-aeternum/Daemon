"""Owner-approved saved-A-context cap diagnostic, not a canonical evaluate run."""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from orchestrator.eval.bench_db import check_benchmark_dsn
from orchestrator.eval.durable_io import append_jsonl_record, atomic_write_json
from orchestrator.eval.run_manifest import git_state
from tests.longmemeval import evaluate, rejudge
from tests.longmemeval.official_judge import OFFICIAL_MAX_TOKENS, build_official_prompt

QUESTION_ID = "0a34ad58"
CAPS = (256, 1024)


def reservation(row: dict[str, Any], prior: Decimal) -> Decimal:
    rejudge.cost_reservation([], "official", prior)  # validate cumulative authorization
    input_tokens = row["answer_usage"]["prompt_tokens"] + 64
    generic_base = (
        len(evaluate.build_generic_judge_prompt(row["question"], "", row["reference"]).encode())
        + 64
    )
    official_base = (
        len(
            build_official_prompt(
                row["question_type"],
                row["question"],
                row["reference"],
                "",
                question_id=row["question_id"],
            ).encode()
        )
        + 64
    )
    # Same pinned reader/judge tokenizer: bound each generated hypothesis by its
    # reader cap, with envelope/boundary slack above. Static judge text uses the
    # deliberately looser UTF-8 byte bound. Reader input is the identical saved
    # two-message prompt, whose historical input count is known (plus slack).
    reserve = sum(
        (
            input_tokens * rejudge.INPUT_RATE
            + cap * rejudge.OUTPUT_RATE
            + (generic_base + official_base + 2 * cap) * rejudge.INPUT_RATE
            + (evaluate.JUDGE_MAX_TOKENS + OFFICIAL_MAX_TOKENS) * rejudge.OUTPUT_RATE
            for cap in CAPS
        ),
        Decimal("0"),
    )
    if reserve > Decimal("0.05") or prior + reserve > rejudge.AUTHORIZATION_USD:
        raise ValueError("Cap diagnostic exceeds approved estimate or cumulative authorization")
    return reserve


async def run(output: Path, prior: Decimal) -> dict[str, Any]:
    rows, source = rejudge.load_inputs("a")
    row = next(row for row in rows if row["question_id"] == QUESTION_ID)
    messages = evaluate.build_parity_answer_messages(row["question"], row["memory_context"])
    if evaluate._sha256_text(messages[0]["content"]) != row["system_prompt_sha256"]:
        raise ValueError("Saved A system prompt no longer matches; do not change the diagnostic")
    reserve = reservation(row, prior)
    check_benchmark_dsn(evaluate.get_settings().database_url)
    code = git_state(rejudge.ROOT)
    if code.get("dirty") or (rejudge.ROOT / ".env").exists():
        raise ValueError("Cap diagnostic requires committed code and no checkout .env")
    output.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "mode": "saved_context_reader_cap_diagnostic_not_canonical_evaluate",
        "started_at": rejudge.now(),
        "status": "running",
        "git": code,
        "source": source,
        "question_id": QUESTION_ID,
        "caps": list(CAPS),
        "calls_completed": 0,
        "database_access": False,
        "retrieval_calls": 0,
        "memory_context_sha256": row["memory_context_sha256"],
        "system_prompt_sha256": row["system_prompt_sha256"],
        "route": {
            "reader": evaluate.BENCHMARK_ANSWER_MODEL,
            "judge": evaluate.BENCHMARK_JUDGE_MODEL,
            "endpoint_slug": evaluate.BENCHMARK_ANSWER_ENDPOINT_SLUG,
            "seed": evaluate.BENCHMARK_SEED,
            "temperature": 0,
            "num_retries": 0,
            "allow_fallbacks": False,
        },
        "cost": {
            "whole_run_reservation_usd": str(reserve),
            "prior_cumulative_reserved_usd": str(prior),
            "cumulative_reserved_usd": str(prior + reserve),
            "usage_cost_estimate_usd": "0",
            "actual_billed_cost_usd": None,
        },
    }
    path = output / "run_manifest.json"
    atomic_write_json(path, manifest)
    cost = Decimal("0")
    evaluate.reset_benchmark_tracking()

    def pending(kind: str, cap: int) -> None:
        manifest["pending_call"] = f"{kind}:{cap}"
        atomic_write_json(path, manifest)

    def receipt(kind: str, cap: int, record: dict[str, Any], usage: dict[str, int]) -> None:
        nonlocal cost
        append_jsonl_record(output / "calls.jsonl", {"kind": kind, "cap": cap, **record})
        if not {"prompt_tokens", "completion_tokens"} <= usage.keys():
            raise RuntimeError("Missing usage; stop with reservation and receipt retained")
        cost += (
            usage["prompt_tokens"] * rejudge.INPUT_RATE
            + usage["completion_tokens"] * rejudge.OUTPUT_RATE
        )
        manifest["calls_completed"] += 1
        manifest["pending_call"] = None
        manifest["cost"]["usage_cost_estimate_usd"] = str(cost)
        atomic_write_json(path, manifest)
        if cost > reserve:
            raise RuntimeError("Usage exceeded cap-diagnostic reservation")

    try:
        for cap in CAPS:
            pending("answer", cap)
            response = await evaluate._call_llm_with_provider_config(
                model=evaluate.ANSWER_MODEL,
                messages=messages,
                temperature=0,
                max_tokens=cap,
                bm_call_key="answer",
            )
            if response is None:
                raise evaluate.BenchmarkProviderError("Missing diagnostic answer")
            hypothesis = evaluate.parse_answer(evaluate._extract_content(response))
            usage = evaluate._usage_from_response(response)
            answer_metadata = evaluate.get_benchmark_tracking().get("answer", {})
            result = {
                "question_id": QUESTION_ID,
                "question": row["question"],
                "reference": row["reference"],
                "category": row["category"],
                "hypothesis": hypothesis,
                "answer_max_tokens": cap,
                "answer_usage": usage,
                "answer_finish_reason": answer_metadata.get("finish_reason"),
                "answer_raw_output": answer_metadata.get("raw_output"),
                "answer_fingerprint": answer_metadata.get("fingerprint"),
                "memory_context_sha256": row["memory_context_sha256"],
                "system_prompt_sha256": row["system_prompt_sha256"],
            }
            receipt("answer", cap, result, usage)
            if (
                usage["prompt_tokens"] > row["answer_usage"]["prompt_tokens"] + 64
                or usage["completion_tokens"] > cap
            ):
                raise RuntimeError("Diagnostic answer exceeded the estimated token bounds")
            pending("generic_judge", cap)
            judgment, usage = await evaluate._judge_with_usage(
                row["question"], hypothesis, row["reference"], benchmark_mode=True
            )
            metadata = evaluate.get_benchmark_tracking().get("judge", {})
            generic = {
                "judgment": judgment,
                "judge_usage": usage,
                "judge_raw_output": metadata.get("raw_output"),
                "judge_finish_reason": metadata.get("finish_reason"),
                "judge_fingerprint": metadata.get("fingerprint"),
            }
            receipt("generic_judge", cap, generic, usage)
            result.update(generic)
            pending("official_judge", cap)
            official = await evaluate.judge_answer_official(
                row["question"],
                hypothesis,
                row["reference"],
                question_id=QUESTION_ID,
                question_type=row["question_type"],
            )
            receipt("official_judge", cap, official, official["official_judge_usage"])
            result.update(official)
            append_jsonl_record(output / evaluate.RESULTS_FILENAME, result)
    except BaseException as exc:
        manifest.update(
            status="stopped_reconciliation_required",
            error_type=type(exc).__name__,
            ended_at=rejudge.now(),
        )
        atomic_write_json(path, manifest)
        raise
    manifest.update(status="completed", ended_at=rejudge.now())
    atomic_write_json(path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prior-reserved-usd", required=True, type=Decimal)
    args = parser.parse_args()
    try:
        manifest = asyncio.run(run(args.output_dir, args.prior_reserved_usd))
    except Exception as exc:
        raise SystemExit(
            f"Cap diagnostic stopped: {type(exc).__name__}; inspect retained manifest"
        ) from None
    print(json.dumps({"status": manifest["status"], "cost": manifest["cost"]}))


if __name__ == "__main__":
    main()
