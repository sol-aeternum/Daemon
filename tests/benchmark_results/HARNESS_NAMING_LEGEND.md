# LongMemEval Harness Naming Legend

One-liner old→new mapping for readers of historical artifacts.

## Modules

| Old | New | Substrate |
|---|---|---|
| `orchestrator.eval.longmemeval_fast` | `orchestrator.eval.chunk_harness` | chunk |
| `orchestrator.eval.runner` | `orchestrator.eval.fact_harness` | fact |

## Classes

| Old | New |
|---|---|
| `LongMemEvalFastRunner` | `LongMemEvalChunkRunner` |
| `LongMemEvalRunner` | `LongMemEvalFactRunner` |

## CLI

| Old | New |
|---|---|
| `python -m orchestrator.eval.longmemeval_fast` | `python -m orchestrator.eval.chunk_harness` |
| `python -m orchestrator.eval.longmemeval` (unchanged) | `python -m orchestrator.eval.longmemeval` (now wraps `fact_harness`) |

## Filenames

| Old | New |
|---|---|
| `longmemeval_fast_results.jsonl` | `longmemeval_chunk_results.jsonl` |
| `longmemeval_fast_checkpoint.json` | `longmemeval_chunk_checkpoint.json` |
| *(no score JSON — gap)* | `longmemeval_chunk_score.json` |
| `longmemeval_score.json` | `longmemeval_score.json` (canonical contract preserved; only the JSON content gained `substrate`/`benchmark_name` fields) |

## User pattern

| Old | New |
|---|---|
| `longmemeval+fast-{run_id}@daemon.test` | `longmemeval+chunk-{run_id}@daemon.test` |
| `longmemeval@daemon.test` (unchanged) | `longmemeval@daemon.test` |

## Log tags

| Old | New |
|---|---|
| `[fast]` | `[chunk]` |
| `[ingest]`, `[evaluate]`, `[score]` (unchanged) | `[ingest]`, `[evaluate]`, `[score]` (unchanged) |

## Constants

| Old | New |
|---|---|
| `BENCHMARK_NAME = "longmemeval_fast"` | `BENCHMARK_NAME = "longmemeval_chunk"` |
| `BENCHMARK_CATEGORY = "fact"` *(bug)* | `BENCHMARK_CATEGORY = "chunk"` *(fixed)* |
| `BENCHMARK_CATEGORY = "fact"` (fact path) | `BENCHMARK_CATEGORY = "fact"` (unchanged) |
| *(no `BENCHMARK_SUBSTRATE`)* | `BENCHMARK_SUBSTRATE = "chunk"` / `"fact"` |

## Importer map (20 files updated)

| File | Old import | New import |
|---|---|---|
| `test_preserve.py` | `from orchestrator.eval.runner import LongMemEvalRunner` | `from orchestrator.eval.fact_harness import LongMemEvalFactRunner` |
| `tests/test_longmemeval_fast.py` | `from orchestrator.eval.longmemeval_fast import ...` | `from orchestrator.eval.chunk_harness import ...` |
| `tests/test_longmemeval_runner.py` | `from orchestrator.eval.runner import ...` | `from orchestrator.eval.fact_harness import ...` |
| `tests/benchmark_harness/ingestion_rerun.py` | same | same |
| `tests/benchmark_harness/ingestion_rerun_preserved.py` | same | same |
| `tests/benchmark_harness/ingestion_rerun_recovery.py` | same | same |
| `tests/benchmark_harness/ingestion_rerun_full_corpus.py` | same | same |
| `tests/benchmark_harness/reset_verify_helper.py` | same | same |
| `tests/benchmark_harness/run_single_preserved.py` | same | same |
| `tests/benchmark_harness/run_triple_preserved.py` | same | same |
| `tests/benchmark_harness/run_triple_preserved_clean.py` | same | same |
| `tests/benchmark_harness/run_triple_rerun.py` | same | same |
| `tests/benchmark_longmemeval/abstention_sweep.py` | same | same |
| `tests/benchmark_longmemeval/dedup_sweep.py` | same | same |
| `tests/benchmark_longmemeval/min_score_sweep.py` | same | same |
| `tests/benchmark_longmemeval/test_config_pinning.py` | same | same |
| `tests/benchmark_longmemeval/test_teardown_audit.py` | `from orchestrator.eval.longmemeval_fast import ...` | `from orchestrator.eval.chunk_harness import ...` |
| `tests/benchmark_longmemeval/top_k_sweep.py` | `from orchestrator.eval.runner import ...` | `from orchestrator.eval.fact_harness import ...` |
| `tests/benchmark_longmemeval/weight_sweep.py` | same | same |
| `orchestrator/eval/longmemeval.py` (CLI entry) | `from orchestrator.eval.runner import ...` | `from orchestrator.eval.fact_harness import ...` |

## Pre-existing bug fixed in this refactor

- `BENCHMARK_CATEGORY = "fact"` in `longmemeval_fast.py` was wrong (chunk-based file called itself "fact"). Fixed to `"chunk"`.
- `longmemeval.py` CLI imported `DEFAULT_OUTPUT_DIR` from `runner.py`, but `DEFAULT_OUTPUT_DIR` was never defined there. Fixed by adding it to `fact_harness.py`.
- Chunk harness never wrote a score JSON. Filled the gap with `longmemeval_chunk_score.json` carrying `substrate: "chunk"`.

## Pre-existing bug NOT fixed (out of scope, triaged)

- `reset_canonical_benchmark` is imported from `orchestrator.eval.runner` in 5 files but never defined anywhere. The imports are broken regardless of this refactor. See TRIAGE.md.

## Substrate gate-guard

| Symbol | Location | Purpose |
|---|---|---|
| `Substrate` literal type | `orchestrator/eval/substrate.py` | `"chunk" \| "fact"` |
| `SubstrateMismatchError` | `orchestrator/eval/substrate.py` | Raised on cross-substrate comparison |
| `assert_substrate_match(path_a, path_b)` | `orchestrator/eval/substrate.py` | Single delta-computation site |
| `tests/test_substrate_gate.py` | new | 11 unit tests for the gate-guard |
