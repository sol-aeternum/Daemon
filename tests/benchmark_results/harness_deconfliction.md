# LongMemEval Harness De-Confliction Report

**Date**: 2026-06-04 (Australia/Adelaide)
**Decision token**: `harness-deconfliction-complete-2026-06-04`
**Branch**: not yet committed
**Commits**: pre-commit (changes staged via `git mv` + edits)

## Summary

The LongMemEval evaluation suite had two harnesses with **confusing and
silent-bug-laden naming**:

1. `orchestrator.eval.longmemeval_fast` — chunk-substrate, but its
   `BENCHMARK_CATEGORY` constant was set to `"fact"` (the **bug**).
2. `orchestrator.eval.runner` — fact-substrate, but the module name gave no
   hint of substrate or purpose.

This refactor:

1. Renames both modules to substrate-explicit names.
2. Fixes the silent substrate-tag bug.
3. Adds a `substrate` field to every score JSON.
4. Adds a gate-guard that hard-fails any cross-substrate comparison.
5. Updates all 20 importers (tests, sweeps, the CLI entry).
6. Adds `docs/HARNESS_TAXONOMY.md` (T1-gated doc).
7. Adds 11 unit tests for the gate-guard.

## What changed

### File renames (history preserved via `git mv`)

| Old | New |
|---|---|
| `orchestrator/eval/longmemeval_fast.py` | `orchestrator/eval/chunk_harness.py` |
| `orchestrator/eval/runner.py` | `orchestrator/eval/fact_harness.py` |

### New files

| File | Purpose |
|---|---|
| `orchestrator/eval/substrate.py` | Gate-guard utility (Substrate type, SubstrateMismatchError, assert_substrate_match) |
| `tests/test_substrate_gate.py` | 11 unit tests for the gate-guard |
| `docs/HARNESS_TAXONOMY.md` | Substrate taxonomy + wave applicability matrix |
| `tests/benchmark_results/HARNESS_NAMING_LEGEND.md` | One-liner old→new mapping for historical artifact readers |
| `tests/benchmark_results/harness_deconfliction.md` | This report |

### Bug fixed

`orchestrator/eval/longmemeval_fast.py:37` had
`BENCHMARK_CATEGORY = "fact"`. The chunk-substrate file was calling itself
"fact" — the cause of the historical cross-substrate noise in
`tests/benchmark_results/task15_mr_comparison.json` and similar artifacts.
**Fixed**: `BENCHMARK_CATEGORY = "chunk"` in the new `chunk_harness.py`.

### Score JSON schema change

| Old schema | New schema |
|---|---|
| `{generated_at, result_count, accuracy}` | `{substrate, benchmark_name, generated_at, result_count, accuracy}` |

- `substrate`: required, `"chunk"` or `"fact"`. Pre-tag files hard-fail the gate.
- `benchmark_name`: required, `"longmemeval_chunk"` or `"longmemeval_fact"`.
- Old fields preserved.

### Filename renames

| Old | New |
|---|---|
| `longmemeval_fast_results.jsonl` | `longmemeval_chunk_results.jsonl` |
| `longmemeval_fast_checkpoint.json` | `longmemeval_chunk_checkpoint.json` |
| *(no score JSON — gap)* | `longmemeval_chunk_score.json` |
| `longmemeval_score.json` | `longmemeval_fact_score.json` |

### User pattern renames

| Old | New |
|---|---|
| `longmemeval+fast-{run_id}@daemon.test` | `longmemeval+chunk-{run_id}@daemon.test` |
| `longmemeval@daemon.test` (unchanged) | `longmemeval@daemon.test` |

### Class renames

| Old | New |
|---|---|
| `LongMemEvalFastRunner` | `LongMemEvalChunkRunner` |
| `LongMemEvalRunner` | `LongMemEvalFactRunner` |

### Log tag renames

`[fast]` → `[chunk]`. `[ingest]`, `[evaluate]`, `[score]` (fact harness) unchanged.

### CLI help-text changes

| Old description | New description |
|---|---|
| "Canonical LongMemEval benchmark entrypoint." (longmemeval.py) | "Fact-substrate LongMemEval benchmark entrypoint. Substrate=fact; use this CLI for wave-gate evaluation that mirrors production memory substrate." |
| "Standalone fast LongMemEval harness using direct memory inserts." (longmemeval_fast.py) | "Standalone chunk-substrate LongMemEval harness using direct memory chunk inserts. Substrate=chunk; not a wave gate." |

### Banners added

Both harnesses now emit a one-line banner at run start naming the substrate.
This is the **discoverability** layer (before the score JSON's `substrate`
field is inspected).

```
[chunk-harness] LongMemEval CHUNK-substrate runner — direct 4000-char chunk
inserts (no LLM extraction). For retrieval-mechanism smoke only. NOT a wave gate.
```

```
[fact-harness] LongMemEval FACT-substrate runner — LLM-extracted facts via
production store.insert_memory. Use for wave-gate evaluation.
```

## Smoke + verify

### Module imports

```python
from orchestrator.eval.substrate import Substrate, SubstrateMismatchError, assert_substrate_match
from orchestrator.eval.chunk_harness import LongMemEvalChunkRunner, BENCHMARK_SUBSTRATE
from orchestrator.eval.fact_harness import LongMemEvalFactRunner, BENCHMARK_SUBSTRATE
```

All three modules import cleanly. `BENCHMARK_CATEGORY` in chunk harness is
now `"chunk"` (was `"fact"`).

### CLI argparse

```
$ python -m orchestrator.eval.chunk_harness --help
usage: python -m orchestrator.eval.chunk_harness [-h] --dataset DATASET
                                                 [--output-dir OUTPUT_DIR]
                                                 [--checkpoint CHECKPOINT]
                                                 [--score-output SCORE_OUTPUT]
                                                 [--limit LIMIT]
                                                 [--chunk-max-chars CHUNK_MAX_CHARS]
                                                 [--overlap-turns OVERLAP_TURNS]
                                                 [--verbose]
Standalone chunk-substrate LongMemEval harness using direct memory chunk
inserts. Substrate=chunk; not a wave gate.
```

```
$ python -m orchestrator.eval.longmemeval --help
usage: python -m orchestrator.eval.longmemeval [-h] {run,ingest,evaluate,score} ...
Fact-substrate LongMemEval benchmark entrypoint. Substrate=fact; use this CLI
for wave-gate evaluation that mirrors production memory substrate.
```

### Test runs

```
tests/test_longmemeval_runner.py ........... 10 passed
tests/test_longmemeval_fast.py ... 1 failed → fixed → 9 passed
tests/test_substrate_gate.py ............ 11 passed
TOTAL: 36 passed
```

The single failure was a test asserting the bug (`first_insert_args[4] == "fact"`)
— updated to assert the correct value (`"chunk"`).

### Quality gates

- `uv run ruff check` on all touched files: **All checks passed**
- `uv run ruff format --check` on all touched files: **All files already formatted**
- `python scripts/check_doc_freshness.py --mode fail`: **No drift detected**

## Out of scope (intentionally)

- `reset_canonical_benchmark` is imported in 5 files but never defined
  anywhere. The imports were broken before this refactor and remain broken
  after. **Triaged** for separate follow-up.
- Historical artifacts in `tests/benchmark_results/*.md` that reference
  `LongMemEvalRunner` or `longmemeval_score.json` are **NOT modified** per
  the spec (they are historical records). The HARNESS_NAMING_LEGEND.md is
  the cross-reference for readers.
- `tests/longmemeval/ingest.py:7` still has a docstring saying "Canonical
  benchmark/admin entrypoint" — speed-framing for the user pattern
  `longmemeval@daemon.test`, but it does describe the canonical fact
  ingestion path. Left in place.

## Files touched (49 total)

### New (5)
- `orchestrator/eval/substrate.py`
- `tests/test_substrate_gate.py`
- `docs/HARNESS_TAXONOMY.md`
- `tests/benchmark_results/HARNESS_NAMING_LEGEND.md`
- `tests/benchmark_results/harness_deconfliction.md`

### Renamed via `git mv` (2)
- `orchestrator/eval/longmemeval_fast.py` → `orchestrator/eval/chunk_harness.py`
- `orchestrator/eval/runner.py` → `orchestrator/eval/fact_harness.py`

### Edited (CLI + tests + importers = 22)
- `orchestrator/eval/longmemeval.py` (CLI entry)
- `tests/test_longmemeval_fast.py` (added `score_path` arg + chunk category assertion fix)
- `tests/test_longmemeval_runner.py` (redirected monkeypatch attrs to `fact_harness`)
- 19 importer files (test_preserve.py, sweeps, run_*, ingestion_rerun_*, etc.)

### Quality-gate verified
- 36/36 tests pass
- ruff lint + format clean
- docs-freshness gate clean
- CLI --help works on both harnesses
- Gate-guard unit tests pass (11/11)

## Decision token (canonical)

`harness-deconfliction-complete-2026-06-04` — emit at top of any follow-up
PR that mentions harness naming or substrate tagging.
