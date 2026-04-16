# LongMemEval Tier 2 Fast Baseline

**Date**: 2026-04-14  
**Status**: ✅ TRUSTED BASELINE — Clean fast-harness benchmark run  
**Harness**: `orchestrator.eval.longmemeval_fast`  
**Questions**: 500  
**Overall Accuracy**: 81.1%

---

## Category Breakdown

| Category | Accuracy | Questions |
|----------|----------|-----------|
| MR (Memory Reasoning) | 86.1% | 133 |
| TR (Temporal Reasoning) | 84.6% | 133 |
| KU (Knowledge Update) | 79.5% | 78 |
| IE-assistant | 82.1% | 56 |
| IE-user | 76.4% | 70 |
| IE-preference | 56.7% | 30 |

**IE-preference** is the weakest category at 56.7%, suggesting the system still struggles with extracting and applying user preferences from assistant messages.

---

## Judgment Distribution

| Judgment | Count | Percentage |
|----------|-------|------------|
| Correct | 311 | 62.2% |
| Partially Correct | 189 | 37.8% |
| Incorrect | 0 | 0.0% |

---

## Retrieval Statistics

| Metric | Average |
|--------|---------|
| Memories Used per Question | 4.8 |
| Chunks Retrieved | 331.7 |
| Sessions Scoped | 47.7 |

---

## Notes

- The fast harness now uses an **isolated per-run benchmark user**, eliminating the shared-user cleanup race that caused intermittent `memories_source_conversation_id_fkey` import failures
- The 11 previously tainted questions were **rerun cleanly via checkpoint resume**, and the raw 500-question result set now contains **zero harness error rows**
- **IE-user underperformance** (76.4%) indicates room for improvement in extracting and retrieving user facts
- **MR and TR remain strongest** (86.1% / 84.6%)

---

## Files

- `longmemeval_tier2_fast.json` — This summary in JSON form
- `longmemeval_tier2_fast/longmemeval_fast_checkpoint.json` — Per-question checkpoint with judgments
- `longmemeval_tier2_fast/longmemeval_fast_results.jsonl` — Raw results (500 questions, zero harness-error rows)
- `longmemeval_tier2_fast/run.log` — Execution log

---

## Benchmark Contract

This is the **trusted Tier 2 baseline** for the memory-system-tier3-reasoning-layer-full-benchmark plan.

The slow baseline path was intentionally abandoned after the benchmark-architecture redesign. All future comparisons should use this repaired fast harness baseline.
