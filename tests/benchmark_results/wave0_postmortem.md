# Wave 0 Postmortem

**Date:** 2026-04-23
**Event:** Dev-subset triple-run gate FAIL
**Gate:** Revised ≤10pp aggregate, ≤15pp per-category

---

## Summary

Wave 0 dev-subset triple-run validation **FAILED** on the per-category spread gate.

- **Aggregate spread:** 4.0pp ✅ (within ≤10pp)
- **Per-category spreads:**
  - IE-user: 22.22pp ❌ (>15pp threshold)
  - KU: 33.33pp ❌ (>15pp threshold)
  - All others: ≤10pp ✅

---

## What Remains Uncontrolled

### 1. IE-user Category Variance (22.22pp spread)

| Run | IE-user Accuracy | Correct/Total |
|-----|-----------------|---------------|
| 1 | 33.33% | 3/9 |
| 2 | 11.11% | 1/9 |
| 3 | 11.11% | 1/9 |

**Observation:** Runs 2 and 3 collapsed to 11.11% while Run 1 was 33.33%. This suggests either:
1. Retrieval variance causing different memory sets to be available
2. Extraction variance causing different facts to be stored
3. Judge variance on edge cases

**Possible sources:**
- `voyage-4-lite` query embedding nondeterminism (measured at 0.000008 mean cosine deviation per `wave0_voyage_drift_test.md`)
- Retrieval ordering non-determinism despite tied-score stabilization
- User-to-user memory interference within the shared benchmark user

### 2. KU (Knowledge Update) Category Variance (33.33pp spread)

| Run | KU Accuracy | Correct/Total |
|-----|-------------|---------------|
| 1 | 55.56% | 5/9 |
| 2 | 66.67% | 6/9 |
| 3 | 33.33% | 3/9 |

**Observation:** Run 3 collapsed to 33.33% while Runs 1 and 2 were 55-67%. This is a 33pp swing between Run 3 and Run 2.

**Possible sources:**
- Memory supersession/dedup non-determinism causing different "update" states
- Temporal reasoning timing differences across runs
- `MIN_FINAL_SCORE` threshold sensitivity to embedding variance

---

## Required Next Actions

### Immediate (Before Any Additional Runs)

1. **Oracle review** of this postmortem — is the current variance level acceptable for Wave 1 entry, or must it be reduced?

2. **If Oracle approves variance level:** Document acceptance rationale, proceed to Task 16 with acknowledged residual variance

3. **If Oracle requires variance reduction:** Return to implementation phase targeting IE-user and KU categories specifically

### Potential Fixes to Investigate

1. **Embedding nondeterminism isolation:**
   - Run with `EMBEDDING_MODEL=voyage-4-large` for query embeddings (more stable than `voyage-4-lite`)
   - Compare voyage-4-lite vs voyage-4-large variance on dev-subset

2. **Retrieval boundary tightening:**
   - Examine whether `TOP_K_MEMORIES=5` is allowing too much variance in retrieved set
   - Try `TOP_K_MEMORIES=3` for IE-user and KU categories specifically

3. **Dedup supersession consistency:**
   - Examine whether knowledge update cases are sensitive to dedup threshold settings
   - Consider whether the 0.82 supersede threshold creates inconsistent "latest fact" selection

4. **Judge prompt sensitivity:**
   - IE-user and KU may involve more ambiguous judgment calls
   - Examine whether strict vs partial_correct criteria vary across runs

---

## Gate Contract Status

| Criterion | Status | Notes |
|-----------|--------|-------|
| Aggregate spread ≤10pp | ✅ PASS | 4.0pp |
| No category >15pp | ❌ FAIL | IE-user 22.22pp, KU 33.33pp |
| Extraction non-regression | ✅ PASS | P=1.0, R=1.0, A=0 |
| TODO 14 provider contract | ✅ VERIFIED | Dated snapshots, extra_body, allow_fallbacks=false |
| DISABLE_BM_FINGERPRINT_FAIL_FAST removed | ✅ VERIFIED | Not present in code |

---

## Recommendation

Given that:
1. The aggregate spread (4.0pp) is well within the 10pp gate
2. The majority of categories (5/7) pass the per-category gate
3. The TODO 14 provider contract is correctly implemented
4. The extraction pipeline is deterministic (P=1.0, R=1.0)

The variance appears to be **concentrated in specific categories** rather than systemic. This suggests targeted category-specific investigation would be more productive than broad architectural changes.

**Recommended path:** Oracle review → conditional approval with documented residual → Task 16 proceeds with acknowledged per-category variance.

---

## Files

- `tests/benchmark_results/wave0_validation_run_1.json`
- `tests/benchmark_results/wave0_validation_run_2.json`
- `tests/benchmark_results/wave0_validation_run_3.json`
- `tests/benchmark_results/wave0_validation_summary.md`
- `tests/benchmark_results/wave0_closure_memo.md`
