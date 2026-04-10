# LongMemEval Tier 1 Upgrade Comparison

**Date**: 2026-04-05
**Baseline Run**: 2026-04-05 (clean state, no Tier 1 features)
**Tier 1 Run**: 2026-04-05 (with L0 injection, BM25 hybrid, contradiction detection)

---

## Summary

| Metric | Baseline | Tier 1 | Change |
|--------|----------|--------|--------|
| **Questions Answered** | 15/50 (30%) | 50/50 (100%) | +70% |
| **Abstentions** | 35/50 (70%) | 0/50 (0%) | -70% |
| **Correct Answers** | 0 | 0 | — |
| **Accuracy (answered)** | 0% | 0% | — |
| **Accuracy (all)** | 0% | 0% | — |

---

## Category Breakdown

### IE (Information Extraction) — 50 questions

| Metric | Baseline | Tier 1 | Change |
|--------|----------|--------|--------|
| Total | 50 | 50 | — |
| Answered | 15 | 50 | +35 |
| Abstained | 35 | 0 | -35 |
| Correct | 0 | 0 | — |

---

## Key Findings

### 1. Answer Rate Improvement
The Tier 1 run shows a **+70% improvement in answer rate** (30% → 100%). This is a significant improvement in the system's willingness to respond to questions.

### 2. Retrieval Quality Issue
Despite the improved answer rate, **49/50 answers are refusals** stating "I cannot answer based on the available information." Only 1 question received a non-refusal answer ("Where do I take yoga classes?" → "You take yoga classes at a studio.").

### 3. Retrieval Path Mismatch
**Critical finding**: The evaluation script (`evaluate.py`) calls `store.search_memories()` directly, which is the **vector-only baseline retrieval path**. This bypasses:
- `retrieve_memories()` in `retrieval.py` which implements BM25 hybrid search
- L0 memory injection in `injection.py`
- Contradiction detection

Therefore, this run **does not exercise Tier 1 features** in the retrieval path. The improvement comes from the re-ingestion with updated extraction pipeline, not from Tier 1 retrieval upgrades.

### 4. Ingestion Success Rate
The re-ingestion had a **32.4% success rate** (789/2436 sessions completed):
- 1647 sessions failed extraction
- This created only 525 memories (vs 7881 in original baseline)
- The reduced memory corpus may have affected retrieval relevance

---

## Comparison of Approaches

| Aspect | Baseline | Tier 1 Run |
|--------|----------|------------|
| **Test User Data** | Previous run with 7881 memories | Clean state, 525 memories after re-ingestion |
| **Retrieval Method** | `store.search_memories()` (vector-only) | `store.search_memories()` (vector-only) — **same as baseline** |
| **BM25 Hybrid** | Not exercised | Not exercised |
| **L0 Injection** | Not exercised | Not exercised |
| **Contradiction Detection** | Not exercised | Not exercised |
| **Extraction Pipeline** | Previous version | Updated with conjunction decomposition + dedup threshold 0.85 |

---

## Recommendations

1. **Modify evaluation to use Tier 1 retrieval path**: The `evaluate.py` script should call `retrieve_memories()` instead of `store.search_memories()` to properly test BM25 hybrid and L0 injection features.

2. **Investigate ingestion failures**: 1647/2436 sessions failed during extraction. Root cause analysis needed to improve memory corpus quality.

3. **Re-run with proper Tier 1 retrieval**: Once evaluation is updated to use the hybrid retrieval path, re-run to get accurate Tier 1 performance numbers.

---

## Files

- `tests/benchmark_results/longmemeval_baseline.json` — Original baseline results
- `tests/benchmark_results/longmemeval_tier1.json` — Tier 1 run results
- `/tmp/longmemeval_results.jsonl` — Raw evaluation output (50 question responses)
- `/tmp/longmemeval_ingestion_results.json` — Ingestion session results