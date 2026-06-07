from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Sequence

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

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    output_path, checkpoint_path, score_path = resolve_output_paths(
        output_dir=args.output_dir,
        checkpoint_path=args.checkpoint,
        score_path=args.score_output,
    )
    runner = LongMemEvalFactRunner(
        dataset_path=args.dataset or Path("."),
        output_path=output_path,
        checkpoint_path=checkpoint_path,
        score_path=score_path,
        limit=args.limit,
        force_retrieval_logging=True,
    )

    if args.command == "run":
        _ = asyncio.run(runner.run())
        return
    if args.command == "ingest":
        _ = asyncio.run(runner.ingest())
        return
    if args.command == "evaluate":
        _ = asyncio.run(runner.evaluate())
        return
    if args.command == "score":
        _ = runner.score()
        return

    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
