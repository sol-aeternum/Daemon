# Wave 0 Closure — Aligned Score Sanity Memo

**Date**: 2026-05-02
**Task**: 13. E5 — Sanity-check aligned score and extraction non-regression
**Artifact source**: `tests/benchmark_results/wave0_closure_full_corpus_corrected/`
**Status**: ❌ **FAIL — aggregate/category sanity bounds failed; downstream closure halted**

---

## Verdict

The corrected aligned artifact is internally consistent, but it does **not** pass the Task 13 sanity gate.

- **Aggregate score** is `30/500 = 0.06`, which fails the required `> 0.15` bound.
- **Category minimum bound** fails for `IE-user = 0.04285714285714286` and `TR = 0.015037593984962405`.
- **Category maximum bound** passes; no scored corrected category exceeds `0.95`.
- **Accounting and score consistency** pass: 500 attempts, 500 unique question IDs, no duplicates, no missing IDs, all 500 successful answer rows accounted for, and category scores match raw rows plus `longmemeval_score.json`.
- **Extraction non-regression** passes from the approved artifact (`P=1.0`, `R=1.0`, `A=0`).
- This corrected aligned score is the first production-grounded measurement for this corrected artifact package and is recorded **as-is**, without using historical 67.8 / 81.1 / 28-34 / 22.4 figures as pass-fail comparators.

Because the failure is multi-category and coincides with `memories_used=0` / empty retrieval IDs on **all 500 rows**, the single fix-and-retry allowance does **not** apply. This is not a discoverable single-category evaluation bug. The aligned score is the score, and Task 13 halts Tasks 14-17.

---

## Score table

### Aggregate

| Metric | Value | Bound | Result |
|---|---:|---:|---|
| Correct answers | 30 | n/a | — |
| Successful answer rows | 500 | n/a | — |
| Aggregate score | 0.06 | > 0.15 | ❌ FAIL |

### Per-category

| Category | Support | Score | Min 5% | Max 95% | Result |
|---|---:|---:|---|---|---|
| IE-user | 70 | 0.04285714285714286 | ❌ | ✅ | ❌ FAIL |
| IE-assistant | 56 | 0.125 | ✅ | ✅ | ✅ PASS |
| IE-preference | 30 | 0.06666666666666667 | ✅ | ✅ | ✅ PASS |
| MR | 133 | 0.07518796992481203 | ✅ | ✅ | ✅ PASS |
| KU | 78 | 0.07692307692307693 | ✅ | ✅ | ✅ PASS |
| TR | 133 | 0.015037593984962405 | ❌ | ✅ | ❌ FAIL |

### ABS note

`longmemeval_score.json` also emits `ABS = 0.0`, but the corrected dataset/raw rows contain zero ABS-category questions. The scoring helper pre-seeds `ABS` and returns `0.0` when support is zero, so this is recorded as informational only rather than treated as a supported corrected category score.

---

## Question-ID accounting and score consistency

| Check | Result |
|---|---|
| Raw rows | 500 |
| Dataset rows | 500 |
| Unique question IDs | 500 |
| Duplicate question IDs | 0 |
| Raw IDs missing in dataset | 0 |
| Dataset IDs missing in raw | 0 |
| Success rows (`hypothesis != ""` and no raw error) | 500 |
| Error rows | 0 |
| Empty hypotheses | 0 |
| Score result count matches raw rows | ✅ |
| Score matches raw-derived category accuracy | ✅ |

Raw judgment distribution from the corrected JSONL:

| Judgment | Count |
|---|---:|
| correct | 30 |
| partially_correct | 21 |
| incorrect | 449 |

The corrected aggregate `30/500 = 0.06` matches the Oracle-cited value and the raw-row recomputation.

---

## Zero-memory-use caveat

Oracle's warning is confirmed directly from the corrected raw artifact:

- `memories_used = 0` on **500/500** rows
- `retrieved_memory_ids = []` on **500/500** rows
- `answer_prompt_metadata` in sampled rows shows empty memory content alongside the generic Daemon system prompt

This makes the low aligned score substantively concerning, but it does **not** indicate a score-accounting bug. It is a real measured outcome of the corrected run.

---

## Extraction non-regression spot-check

Approved artifact reviewed:

- `tests/benchmark_results/extraction_benchmark_results.md`
- `tests/benchmark_results/extraction_benchmark_results.json`

Wave 0 gate thresholds and artifact-backed medians:

| Metric | Threshold | Actual | Result |
|---|---:|---:|---|
| Precision | >= 0.95 | 1.0 | ✅ PASS |
| Recall | >= 0.85 | 1.0 | ✅ PASS |
| A | <= 2 | 0 | ✅ PASS |

The benchmark artifact's own stricter guardrails also pass (`P >= 0.90`, `R >= 0.90`, adversarial FP `= 0`). No rerun was required for Task 13 because the approved JSON artifact is self-contained and its referenced raw run files are present.

---

## Downstream decision

**Task 13 decision: HALT downstream closure.**

- Failed sanity bounds are **aggregate** and **minimum-category floor**.
- The failure is **not** isolated to a single category, and the zero-memory pattern affects the entire 500-row corrected corpus.
- Therefore the user's one-time fix-and-retry allowance for a discoverable **single-category evaluation bug** does **not** apply.
- The corrected aligned score stands as the score for this artifact package, and Tasks 14-17 must not proceed from this run.

---

## Files referenced

- `tests/benchmark_results/wave0_closure_full_corpus_corrected/longmemeval_results.jsonl`
- `tests/benchmark_results/wave0_closure_full_corpus_corrected/longmemeval_score.json`
- `tests/benchmark_results/wave0_closure_full_corpus_corrected/longmemeval_checkpoint.json`
- `tests/benchmark_results/wave0_closure_full_corpus_corrected/self_check_output.json`
- `/tmp/longmemeval_s_reconstructed_runner_native.json`
- `.sisyphus/evidence/task-11-full-corpus-counts.json`
- `.sisyphus/evidence/task-12-oracle-e4.md`
- `tests/benchmark_results/extraction_benchmark_results.md`
- `tests/benchmark_results/extraction_benchmark_results.json`
