"""Backwards-compatibility shim for the legacy ``runner`` module name.

The LongMemEval fact-substrate harness was renamed from
``orchestrator.eval.runner`` to ``orchestrator.eval.fact_harness`` on
2026-06-04. Several historical docs (HARNESS.md, BARRIER_AUDIT.md,
ISOLATION_AUDIT.md, CONFIG_PINNING.md, 81_1_DIFF.md) and any external
operator automation still reference the old module path
(``from orchestrator.eval.runner import LongMemEvalRunner``).

This shim re-exports the fact harness symbols under the legacy name so
the old CLI invocations and imports keep working. It forwards to
fact_harness without modification — substrate is still ``"fact"`` and
the score JSON schema includes the ``substrate`` field.
"""

from orchestrator.eval.fact_harness import (
    BENCHMARK_NAME,
    BENCHMARK_SUBSTRATE,
    CANONICAL_RESET_STATEMENTS,
    CANONICAL_RESET_TABLES,
    CHECKPOINT_VERSION,
    DEFAULT_OUTPUT_DIR,
    HARNESS_BANNER,
    LongMemEvalFactRunner,
    PhaseName,
    ResetSummary,
    SCORE_FILENAME,
    build_corpus_results_lookup,
    build_question_order,
    build_score_payload,
    load_dataset,
    load_runner_checkpoint,
    mark_phase_completed,
    mark_phase_started,
    ordered_results,
    parse_positive_int,
    read_json,
    reset_canonical_benchmark,
    resolve_output_paths,
    resolve_question_conversation_ids,
    resolve_question_corpus_refs,
    save_runner_checkpoint,
    utc_now_iso,
    write_json,
)

LongMemEvalRunner = LongMemEvalFactRunner

__all__ = [
    "BENCHMARK_NAME",
    "BENCHMARK_SUBSTRATE",
    "CANONICAL_RESET_STATEMENTS",
    "CANONICAL_RESET_TABLES",
    "CHECKPOINT_VERSION",
    "DEFAULT_OUTPUT_DIR",
    "HARNESS_BANNER",
    "LongMemEvalFactRunner",
    "LongMemEvalRunner",
    "PhaseName",
    "ResetSummary",
    "SCORE_FILENAME",
    "build_corpus_results_lookup",
    "build_question_order",
    "build_score_payload",
    "load_dataset",
    "load_runner_checkpoint",
    "mark_phase_completed",
    "mark_phase_started",
    "ordered_results",
    "parse_positive_int",
    "read_json",
    "reset_canonical_benchmark",
    "resolve_output_paths",
    "resolve_question_conversation_ids",
    "resolve_question_corpus_refs",
    "save_runner_checkpoint",
    "utc_now_iso",
    "write_json",
]
