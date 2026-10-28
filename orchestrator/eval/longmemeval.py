from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Sequence

from orchestrator.eval.bench_db import BenchmarkDatabaseGuardError
from orchestrator.eval.interrupts import StopController, run_interruptible
from orchestrator.eval.run_manifest import ManifestPinError
from orchestrator.eval.fact_harness import (
    CHECKPOINT_FILENAME,
    DEFAULT_OUTPUT_DIR,
    RESULTS_FILENAME,
    SCORE_FILENAME,
    LongMemEvalFactRunner,
    parse_positive_int,
    resolve_output_paths,
)


def add_shared_path_arguments(parser: argparse.ArgumentParser, *, require_dataset: bool) -> None:
    parser.add_argument(
        "--dataset",
        type=Path,
        required=require_dataset,
        help="Path to the LongMemEval dataset JSON file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            f"Directory for {RESULTS_FILENAME}, {CHECKPOINT_FILENAME}, and {SCORE_FILENAME} "
            f"(default: {DEFAULT_OUTPUT_DIR})"
        ),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help=(f"Optional checkpoint file path (default: <output-dir>/{CHECKPOINT_FILENAME})"),
    )
    parser.add_argument(
        "--score-output",
        type=Path,
        default=None,
        help=(f"Optional score summary file path (default: <output-dir>/{SCORE_FILENAME})"),
    )
    parser.add_argument(
        "--limit",
        type=parse_positive_int,
        default=None,
        help="Limit the number of dataset entries/questions processed.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.eval.longmemeval",
        description=(
            "Fact-substrate LongMemEval benchmark entrypoint. Substrate=fact; "
            "use this CLI for wave-gate evaluation that mirrors production memory substrate."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run LongMemEval ingest, evaluate, and score in sequence",
        description=(
            "Run LongMemEval with an explicit dataset path. Results are written "
            f"to <output-dir>/{RESULTS_FILENAME}, checkpoint state is written to "
            f"<output-dir>/{CHECKPOINT_FILENAME} unless overridden, score output "
            f"is written to <output-dir>/{SCORE_FILENAME}, and retrieval logging "
            "is forced on for benchmark phases."
        ),
    )
    add_shared_path_arguments(run_parser, require_dataset=True)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Run only the LongMemEval ingestion phase",
    )
    add_shared_path_arguments(ingest_parser, require_dataset=True)

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Run only the LongMemEval evaluation phase",
    )
    add_shared_path_arguments(evaluate_parser, require_dataset=True)

    score_parser = subparsers.add_parser(
        "score",
        help="Score previously evaluated LongMemEval results",
    )
    add_shared_path_arguments(score_parser, require_dataset=False)

    for subparser in (run_parser, ingest_parser, evaluate_parser, score_parser):
        subparser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose logging.",
        )

    for subparser in (run_parser, ingest_parser, evaluate_parser):
        subparser.add_argument(
            "--allow-code-change",
            action="store_true",
            help="Resume even though the checkout differs from the SHA pinned in the run "
            "manifest (recorded in the manifest).",
        )
    for subparser in (run_parser, ingest_parser):
        subparser.add_argument(
            "--retry-failed-sessions",
            action="store_true",
            help="Roll back and re-ingest sessions recorded as failed; the run is then no "
            "longer baseline-qualified.",
        )
        subparser.add_argument(
            "--concurrency",
            type=parse_positive_int,
            default=1,
            help="Questions ingested side by side (default 1). Each question has its own "
            "benchmark user and ingests one session at a time, so N changes no user's "
            "session order or dedup candidates; provider nondeterminism still applies.",
        )
    evaluate_parser.add_argument(
        "--from-ingest",
        type=Path,
        default=None,
        help="Evaluate a completed ingest from another output directory's checkpoint "
        "(use a fresh --output-dir).",
    )
    evaluate_parser.add_argument(
        "--ingest-snapshot",
        type=Path,
        default=None,
        help="pg_dump file the database was restored from; hashed into the run manifest.",
    )
    evaluate_parser.add_argument(
        "--allow-state-mismatch",
        action="store_true",
        help="Evaluate even if the database fingerprint differs from the ingest's "
        "(recorded in the manifest; results are not comparable).",
    )

    return parser


def _build_runner(args: argparse.Namespace, stop: StopController | None) -> LongMemEvalFactRunner:
    output_path, checkpoint_path, score_path = resolve_output_paths(
        output_dir=args.output_dir,
        checkpoint_path=args.checkpoint,
        score_path=args.score_output,
    )
    return LongMemEvalFactRunner(
        dataset_path=args.dataset or Path("."),
        output_path=output_path,
        checkpoint_path=checkpoint_path,
        score_path=score_path,
        limit=args.limit,
        force_retrieval_logging=True,
        from_ingest=getattr(args, "from_ingest", None),
        ingest_snapshot=getattr(args, "ingest_snapshot", None),
        retry_failed_sessions=getattr(args, "retry_failed_sessions", False),
        allow_code_change=getattr(args, "allow_code_change", False),
        allow_state_mismatch=getattr(args, "allow_state_mismatch", False),
        concurrency=getattr(args, "concurrency", 1),
        stop=stop,
    )


async def _run_phase(args: argparse.Namespace, stop: StopController) -> Any:
    runner = _build_runner(args, stop)
    if args.command == "run":
        return await runner.run()
    if args.command == "ingest":
        return await runner.ingest()
    return await runner.evaluate()


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.command == "score":
        _ = _build_runner(args, None).score()
        return
    if args.command not in ("run", "ingest", "evaluate"):
        parser.error(f"Unsupported command: {args.command}")

    try:
        _result, stop = asyncio.run(run_interruptible(lambda ctl: _run_phase(args, ctl)))
    except (BenchmarkDatabaseGuardError, ManifestPinError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        sys.exit(2)
    if stop.requested:
        logging.getLogger(__name__).warning(
            "Stopped by %s with completed work checkpointed; rerun the same command to resume.",
            stop.signal_name,
        )
        sys.exit(128 + int(stop.signal_number or 0))


if __name__ == "__main__":
    main()
