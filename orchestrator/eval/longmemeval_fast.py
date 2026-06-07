"""Backwards-compatibility shim for the legacy ``longmemeval_fast`` module name.

The LongMemEval chunk-substrate harness was renamed from
``orchestrator.eval.longmemeval_fast`` to ``orchestrator.eval.chunk_harness``
on 2026-06-04. Several historical docs and operator muscle-memory references
the old module path (e.g. ``python -m orchestrator.eval.longmemeval_fast ...``).

This shim re-exports the chunk harness symbols under the legacy name so the
old CLI invocations and imports keep working. It forwards to chunk_harness
without modification — substrate is still ``"chunk"`` and the score JSON
schema includes the ``substrate`` field.

The legacy ``LongMemEvalFastRunner`` class accepted three positional path
arguments (``dataset_path``, ``output_path``, ``checkpoint_path``). The new
``LongMemEvalChunkRunner`` adds a required ``score_path`` field, so the
alias is implemented as a thin wrapper that derives a default score path
from ``output_path`` when callers omit it.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orchestrator.eval import chunk_harness as _chunk_harness
from orchestrator.eval.chunk_harness import (
    BENCHMARK_MEMORY_CATEGORY,
    BENCHMARK_NAME,
    BENCHMARK_SOURCE_TYPE,
    BENCHMARK_SUBSTRATE,
    CHECKPOINT_FILENAME,
    DEFAULT_CHUNK_MAX_CHARS,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_OVERLAP_TURNS,
    HARNESS_BANNER,
    LongMemEvalChunkRunner,
    RESULTS_FILENAME,
    SCORE_FILENAME,
    build_benchmark_user,
    build_parser,
    build_question_chunks,
    build_score_payload,
    chunk_session_messages,
    cleanup_benchmark_state,
    delete_benchmark_user,
    ensure_benchmark_user,
    ingest_question_chunks,
    insert_chunk_memories,
    normalize_question_id,
    parse_positive_int,
    resolve_output_paths,
)

LEGACY_RESULTS_FILENAME = "longmemeval_fast_results.jsonl"
LEGACY_CHECKPOINT_FILENAME = "longmemeval_fast_checkpoint.json"
LEGACY_SCORE_FILENAME = "longmemeval_fast_score.json"


def resolve_output_paths_legacy(
    output_dir: Path,
    checkpoint_path: Path | None = None,
) -> tuple[Path, Path]:
    """Legacy 2-tuple variant of ``resolve_output_paths`` with the original
    ``longmemeval_fast_*`` artifact filenames.

    The pre-2026-06-04 resolver returned
    ``(results_path, checkpoint_path)`` using filenames prefixed with
    ``longmemeval_fast_``. The substrate-tag refactor renamed those
    artifacts to ``longmemeval_chunk_*``. This wrapper restores the
    legacy 2-tuple arity AND the legacy filenames so existing automation
    that reads from the documented ``longmemeval_fast_results.jsonl``
    files keeps working.
    """
    results = output_dir / LEGACY_RESULTS_FILENAME
    checkpoint = checkpoint_path or (output_dir / LEGACY_CHECKPOINT_FILENAME)
    return results, checkpoint


class LongMemEvalFastRunner:
    """Back-compat wrapper preserving the legacy 3-arg constructor.

    Forwards to ``LongMemEvalChunkRunner`` with a default ``score_path``
    derived from ``output_path.parent / SCORE_FILENAME`` when callers
    omit it. This keeps previously valid 3-arg legacy imports working
    (e.g. ``LongMemEvalFastRunner(dataset, results, checkpoint)``).
    """

    def __init__(
        self,
        dataset_path: Path,
        output_path: Path,
        checkpoint_path: Path,
        limit: int | None = None,
        chunk_max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
        overlap_turns: int = DEFAULT_OVERLAP_TURNS,
        force_retrieval_logging: bool = True,
        *,
        score_path: Path | None = None,
    ) -> None:
        effective_score_path = score_path or (output_path.parent / SCORE_FILENAME)
        self._impl = LongMemEvalChunkRunner(
            dataset_path=dataset_path,
            output_path=output_path,
            checkpoint_path=checkpoint_path,
            score_path=effective_score_path,
            limit=limit,
            chunk_max_chars=chunk_max_chars,
            overlap_turns=overlap_turns,
            force_retrieval_logging=force_retrieval_logging,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._impl, name)


__all__ = [
    "BENCHMARK_MEMORY_CATEGORY",
    "BENCHMARK_NAME",
    "BENCHMARK_SOURCE_TYPE",
    "BENCHMARK_SUBSTRATE",
    "CHECKPOINT_FILENAME",
    "DEFAULT_CHUNK_MAX_CHARS",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_OVERLAP_TURNS",
    "HARNESS_BANNER",
    "LEGACY_CHECKPOINT_FILENAME",
    "LEGACY_RESULTS_FILENAME",
    "LEGACY_SCORE_FILENAME",
    "LongMemEvalChunkRunner",
    "LongMemEvalFastRunner",
    "RESULTS_FILENAME",
    "SCORE_FILENAME",
    "build_benchmark_user",
    "build_parser",
    "build_question_chunks",
    "build_score_payload",
    "chunk_session_messages",
    "cleanup_benchmark_state",
    "delete_benchmark_user",
    "ensure_benchmark_user",
    "ingest_question_chunks",
    "insert_chunk_memories",
    "normalize_question_id",
    "parse_positive_int",
    "resolve_output_paths",
    "resolve_output_paths_legacy",
]


def main(argv: Sequence[str] | None = None) -> None:
    """Legacy CLI entrypoint.

    Translates the documented ``run`` subcommand shape
    (``python -m orchestrator.eval.longmemeval_fast run --dataset ...``) by
    stripping the leading ``run`` token and forwarding the remaining args to
    ``chunk_harness.main``. Argv without the ``run`` prefix is forwarded as-is
    so any modern direct-flag invocation also works.
    """
    import sys

    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "run":
        args_list = args_list[1:]
    _chunk_harness.main(args_list)


if __name__ == "__main__":
    main()
