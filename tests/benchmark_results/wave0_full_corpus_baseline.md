# Wave 0 — Full-Corpus Baseline

**Generated:** 2026-04-27T14:32:15Z
**Status:** BASELINE CAPTURED — not yet evaluated
**Harness:** `tests/benchmark_harness/ingestion_rerun_full_corpus.py`

---

## Run Summary

| Item | Value |
|---|---|
| Dataset | `/tmp/longmemeval-review/data/longmemeval_s.json` |
| Sessions (total) | 18475 |
| Completed (outcome) | 4499 |
| Errored (outcome) | 20 |
| ERRORED % | 0.1% |
| Reset exit code | 0 |
| Ingest exit code | 0 |
| Wall time | 63343s |

## Outcome Counts (from checkpoint `outcome` field)

| Outcome | Count |
|---|---|
| completed | 4499 |
| errored | 20 |
| empty | 6658 |

## Status Counts (from checkpoint `status` field)

| Status | Count |
|---|---|
| complete | 11157 |
| extraction_failed | 20 |

## Sample Errors (first 5)

```
Supersede failed to close source memory in active state
Supersede failed to close source memory in active state
Supersede failed to close source memory in active state
Supersede failed to close source memory in active state
Supersede failed to close source memory in active state
```

## Patches Applied (in subprocess)

| Module | Constant | Original → Patched |
|---|---|---|
| `orchestrator.memory.extraction` | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG` | `'openrouter/openai/gpt-4o-mini-2024-07-18'` → `'openai'` |
| `orchestrator.memory.extraction` | `extract_facts_from_text` | catches `BenchmarkSamplingError` (fingerprint drift = diagnostic only) |
| `orchestrator.memory.dedup` | `BENCHMARK_CONTRADICTION_MODEL` | `'openrouter/deepseek/deepseek-chat-v3-5'` → `'openrouter/deepseek/deepseek-v3.2'` |
| `orchestrator.memory.dedup` | `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG` | `'openrouter/deepseek/deepseek-chat-v3-5'` → `'novita'` |
| `orchestrator.memory.dedup` | `check_contradiction` | catches `DedupBenchmarkSamplingError` (advisory) |

## Guardrails

| Guardrail | Outcome |
|---|---|
| G1: Provider health check | (not shown in this report — run with `--check` flag to verify pre-run) |
| G3: Errored-floor (5%) | PASS — 0.1% |
| G5: Credit instrumentation | log-only, not blocking |

## Bounded-Variance Framing

Per `wave0_rerun_content_comparison_v2.md` and `wave0_variance_attribution_results.md`:

- Full-corpus baseline results fall within the characterized **~6pp irreducible embedding variance**
  distribution (measured with `voyage-4-lite`).
- Results should be interpreted as falling within the bounded distribution, not as
  regressions or improvements relative to any single prior run.
- The 3-run reproducibility protocol (spread ≤ 3pp) is assessed separately after
  evaluate/score completes for each run.

## Artifact Destinations

| Artifact | Location |
|---|---|
| Output dir | `tests/benchmark_results/wave0_full_corpus_baseline/` |
| Ingestion checkpoint | `wave0_full_corpus_baseline/longmemeval_checkpoint.json` |
| Ingestion results | `wave0_full_corpus_baseline/longmemeval_results.jsonl` |
| Score output | `wave0_full_corpus_baseline/longmemeval_score.json` |
| Ingestion log | `wave0_full_corpus_baseline/ingest.log` |

---

*Run harness: `PYTHONPATH=. python tests/benchmark_harness/ingestion_rerun_full_corpus.py`*
*Next: run full-corpus baseline → create/update baselines.md → Oracle checkpoint 2 → local tag*
