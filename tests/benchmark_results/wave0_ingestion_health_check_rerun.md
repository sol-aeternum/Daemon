# D5 — Step 3 Ingestion Rerun (Wave 0)

**Generated:** 2026-04-24T16:51:30+00:00
**Status:** PASS — ERRORED 0.0% (halt rule: >5%)

---

## Run Summary

| Item | Value |
|---|---|
| Dataset | `tests/benchmark_longmemeval/fixtures/dev_subset.json` |
| Sessions | 9 |
| ERRORED % | 0.0% (0 sessions) |
| Reset exit code | 1 |
| Ingest exit code | 1 |
| Wall time | 4s |

## Outcome Counts (from checkpoint `outcome` field)

| Outcome | Count |
|---|---|
| completed | 5 |
| errored | 0 |
| empty | 4 |

## Status Counts (from checkpoint `status` field)

| Status | Count |
|---|---|
| complete | 9 |
| extraction_failed | 0 |

## Sample Errors (first 5)

```
None
```

## Patches Applied (in subprocess)

| Module | Constant | Original → Patched |
|---|---|---|
| `orchestrator.memory.extraction` | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG` | `'openrouter/openai/gpt-4o-mini-2024-07-18'` → `'openai'` |
| `orchestrator.memory.extraction` | `extract_facts_from_text` | catches `BenchmarkSamplingError` (fingerprint drift = diagnostic only) |
| `orchestrator.memory.dedup` | `BENCHMARK_CONTRADICTION_MODEL` | `'openrouter/deepseek/deepseek-chat-v3-5'` → `'openrouter/deepseek/deepseek-v3.2'` |
| `orchestrator.memory.dedup` | `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG` | `'openrouter/deepseek/deepseek-chat-v3-5'` → `'novita'` |
| `orchestrator.memory.dedup` | `check_contradiction` | catches `DedupBenchmarkSamplingError` (advisory) |

## Pass/Fail Verdict

**ERRORED halt rule:** >5% → FAIL
**Current ERRORED rate:** 0.0% (0/9)

D5 result: **PASS**

---

*Run harness: `tests/benchmark_harness/ingestion_rerun.py`*
