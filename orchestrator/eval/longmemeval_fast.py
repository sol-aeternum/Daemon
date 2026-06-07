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

from pathlib import Path
from typing import Any

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
    main,
    normalize_question_id,
    parse_positive_int,
    resolve_output_paths,
)


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
    "main",
    "normalize_question_id",
    "parse_positive_int",
    "resolve_output_paths",
]


if __name__ == "__main__":
    main()
