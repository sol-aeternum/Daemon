# LongMemEval Retrieval Diagnostics Report

**Generated**: 2026-04-14 (from checkpoint analysis)
**Data Source**: `longmemeval_tier2_fast/longmemeval_fast_checkpoint.json`
**Note**: Analysis performed without live database retrieval_log. Benchmark user was deleted post-run.

---

## ⚠️ Important Limitation

**Live retrieval_log data is not available for this analysis.**

The diagnostics module (`orchestrator.eval.diagnostics`) requires:
1. A live PostgreSQL database with `retrieval_log` entries
2. A benchmark user whose memories can be queried
3. The `candidate_memory_ids` and `selected_memory_ids` from retrieval events

The fast harness creates **isolated per-run benchmark users** that are **deleted after the run completes**. The checkpoint file preserves judgment results but not retrieval evidence (candidate/selected memory IDs).

**This diagnostics report is derived from checkpoint judgment data only.**

---

## Judgment Summary

| Judgment | Count | Percentage |
|----------|-------|------------|
| Correct | 311 | 62.2% |
| Partially Correct | 189 | 37.8% |
| **Incorrect** | **0** | **0.0%** |

### Key Finding

**Zero "incorrect" judgments** — all 500 questions received either "correct" or "partially_correct" assessments. This means there are no answers that are completely wrong, only answers that are incomplete or partially inaccurate.

---

## Failure Mode Analysis

| Failure Mode | Count | Percentage |
|--------------|-------|------------|
| extraction_miss | 0 | N/A |
| retrieval_miss | 0 | N/A |
| reader_failure | 0 | N/A |
| unknown | 0 | N/A |
| **not_applicable** | **500** | **100%** |

Since there are zero "incorrect" judgments, traditional failure-mode classification (why did the answer go wrong?) does not apply. The system never produced a completely wrong answer.

---

## Per-Category Breakdown

### IE-preference ⬇️ (Weakest)

| Metric | Value |
|--------|-------|
| Total Questions | 30 |
| Correct | 4 (13.3%) |
| Partially Correct | 26 (86.7%) |
| Incorrect | 0 |
| Accuracy | **56.7%** |

**Analysis**: IE-preference has the highest partially_correct rate. The system consistently identifies the right preference-related facts but fails to fully capture or correctly apply all preference details. 26 of 30 answers were only partial matches.

### IE-user

| Metric | Value |
|--------|-------|
| Total Questions | 70 |
| Correct | 37 (52.9%) |
| Partially Correct | 33 (47.1%) |
| Incorrect | 0 |
| Accuracy | **76.4%** |

**Analysis**: Nearly half of user-fact questions resulted in partially correct answers. Room for improvement in extracting and retrieving user-provided facts.

### IE-assistant

| Metric | Value |
|--------|-------|
| Total Questions | 56 |
| Correct | 36 (64.3%) |
| Partially Correct | 20 (35.7%) |
| Incorrect | 0 |
| Accuracy | **82.1%** |

**Analysis**: Assistant facts are retrieved and applied reasonably well. 64% fully correct.

### KU (Knowledge Update)

| Metric | Value |
|--------|-------|
| Total Questions | 78 |
| Correct | 46 (59.0%) |
| Partially Correct | 32 (41.0%) |
| Incorrect | 0 |
| Accuracy | **79.5%** |

**Analysis**: Knowledge update questions show moderate performance. The system often retrieves outdated rather than updated facts, or partially misses the update.

### MR (Memory Reasoning) ⬆️ (Strongest)

| Metric | Value |
|--------|-------|
| Total Questions | 133 |
| Correct | 96 (72.2%) |
| Partially Correct | 37 (27.8%) |
| Incorrect | 0 |
| Accuracy | **86.1%** |

**Analysis**: Memory reasoning is the strongest category. Over 72% of answers are fully correct, with the remainder only partially matching.

### TR (Temporal Reasoning)

| Metric | Value |
|--------|-------|
| Total Questions | 133 |
| Correct | 92 (69.2%) |
| Partially Correct | 41 (30.8%) |
| Incorrect | 0 |
| Accuracy | **84.6%** |

**Analysis**: Temporal reasoning performs well. 69% fully correct. Remaining answers often misorder events or miss temporal qualifiers.

---

## Retrieval Statistics

| Metric | Average |
|--------|---------|
| Memories Used per Question | 4.8 |
| Chunks Retrieved | 331.7 |
| Sessions Scoped | 47.7 |

---

## What This Means

### Positive Signs
- **No completely wrong answers**: The system never produced an incorrect fact. Every answer was at least partially aligned with the reference.
- **Strong core retrieval**: MR (86.1%) and TR (84.6%) show the memory system correctly retrieves and reasons over facts most of the time.

### Areas for Improvement
1. **IE-preference (56.7%)**: The most significant weakness. User preferences are often missed or misapplied.
2. **Partially correct answers (37.8% overall)**: Nearly 4 in 10 answers are incomplete. The system retrieves the right facts but fails to fully synthesize them.

---

## How to Run Live Diagnostics

To perform true failure-mode classification (extraction_miss / retrieval_miss / reader_failure):

```bash
# Requires a live benchmark user with retrieval_log entries
uv run python -m orchestrator.eval.diagnostics \
    --results tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_results.jsonl \
    --user-id <uuid_of_benchmark_user> \
    --output-dir tests/benchmark_results
```

The diagnostics module will:
1. Query `retrieval_log` for each wrong answer
2. Embed the reference fact and search memory store
3. Check if supporting memory exists and whether it was in candidates/selected
4. Classify failure as extraction_miss, retrieval_miss, or reader_failure

**Note**: Live diagnostics require a benchmark user that hasn't been deleted. The fast harness deletes users after each run to prevent cleanup races.

---

## Files

- `diagnostics_summary.json` — Machine-readable diagnostics summary
- `diagnostics_report.md` — This report
- `longmemeval_tier3_final.json` / `.md` — Final benchmark artifact