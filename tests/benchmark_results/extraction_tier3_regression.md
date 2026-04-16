# Extraction Tier 3 Regression — Fresh 3-Run Results

**Date**: 2026-04-14
**Artifact**: `extraction_tier3_regression.json`
**Source**: Same verified fresh 3-run deterministic replay results as `extraction_benchmark_results.json`
**Benchmark**: `tests/benchmark_extraction.py` (v2.3, 8 scenarios)
**Extraction Model**: `gpt-4o-mini`
**Harness**: Deterministic transcript replay (no `/chat` assistant generation contamination)
**Status**: ✅ **COMPLETE — Fresh 3-run regression pass**

---

## Guardrails

| Metric | Threshold | Actual | Status |
|--------|-----------|--------|--------|
| Precision | ≥ 0.90 | 1.0 | ✅ |
| Recall | ≥ 0.90 | 1.0 | ✅ |
| Adversarial FP | = 0 | 0 | ✅ |

---

## Fresh 3-Run Results (April 14)

| Run | Date | P | R | Adversarial FP | Passed |
|-----|------|----|----|---------------|--------|
| 1 | 2026-04-14 21:44 | 1.0 | 1.0 | 0 | ✅ |
| 2 | 2026-04-14 21:47 | 1.0 | 1.0 | 0 | ✅ |
| 3 | 2026-04-14 21:50 | 1.0 | 1.0 | 0 | ✅ |
| **Median** | — | **1.0** | **1.0** | **0** | ✅ |

**Guardrail Status**: ✅ **PASSES** (P=1.0 ≥ 0.90, R=1.0 ≥ 0.90, AdvFP=0)

---

## Per-Scenario Breakdown (Run 1)

| Scenario | Expected | Extracted | TP | FP | FN | P | R |
|----------|----------|-----------|----|----|----|----|---|
| 1: Dense Personal Facts | 9 | 12 | 9 | 0 | 0 | 1.00 | 1.00 |
| 2: Ephemeral vs Durable | 1 | 2 | 1 | 0 | 0 | 1.00 | 1.00 |
| 3: Corrections/Supersession | 1 | 2 | 1 | 0 | 0 | 1.00 | 1.00 |
| 4: Projects/Goals | 3 | 6 | 3 | 0 | 0 | 1.00 | 1.00 |
| 5: Hedged Statements | 6 | 6 | 6 | 0 | 0 | 1.00 | 1.00 |
| 6: Multi-Turn Session | 7 | 29 | 7 | 0 | 0 | 1.00 | 1.00 |
| 7: Explicit Memory | 3 | 6 | 3 | 0 | 0 | 1.00 | 1.00 |
| 8: Adversarial Empty | 0 | 0 | 0 | 0 | 0 | 1.00 | 1.00 |

---

## Notes

- Deterministic transcript replay harness eliminates `/chat` assistant generation contamination
- S6 achieves perfect TP=7 FP=0 FN=0 in all 3 runs (vs historical runs where S6 was the weak point)
- All scenarios pass perfectly across all 3 runs
- Same verified results as `extraction_benchmark_results.json` — this artifact provides the expected regression artifact name per reviewer expectations

---

## Source Data

- `extraction_tier3_regression.json` — This artifact (machine-readable)
- `extraction_benchmark_results.json` — Original source of verified results
- `tests/results/bench_20260414_214436.json` — Fresh Run 1
- `tests/results/bench_20260414_214718.json` — Fresh Run 2
- `tests/results/bench_20260414_215000.json` — Fresh Run 3
