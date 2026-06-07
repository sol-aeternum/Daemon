"""Backwards-compatibility shim for the legacy ``longmemeval_fast`` module name.

The LongMemEval chunk-substrate harness was renamed from
``orchestrator.eval.longmemeval_fast`` to ``orchestrator.eval.chunk_harness``
on 2026-06-04. Several historical docs and operator muscle-memory references
the old module path (e.g. ``python -m orchestrator.eval.longmemeval_fast ...``).

This shim re-exports the chunk harness symbols under the legacy name so the
old CLI invocations and imports keep working. It forwards to chunk_harness
without modification — substrate is still ``"chunk"`` and the score JSON
schema includes the ``substrate`` field.
"""

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

LongMemEvalFastRunner = LongMemEvalChunkRunner

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
