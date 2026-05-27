# Wave 0 Path A — PA4 Aligned Score Sanity Memo

**Date**: 2026-04-30
**Task**: 6 — PA4 sanity checks and extraction non-regression (corrected from stale 404 wording)
**Artifact source**: `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_score.json`

---

## aggregate

**Value**: 0.0 (all categories reported as 0.0)

**Status**: NOT VALID AS A BASELINE — 482/500 rows failed with `Benchmark fingerprint drift` before producing valid judgments; 18 rows had no error but still produced no correct answers.

**Reason**: The PA3 full-corpus run evaluated all 500 questions. 482 rows errored at answer/judge stage due to `Benchmark fingerprint drift` (system fingerprint mismatch). 18 rows reached judge without error but still returned incorrect/partially-correct judgments. No row achieved `status="complete"` because the `status` field is absent from all result rows. Raw `status="complete"` rate is 0/500.

**Evidence**:
- `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl`: 500 rows total
  - 482 rows have `error` field with `Benchmark fingerprint drift` (213 answer-stage, 269 judge-stage)
  - 18 rows have no `error` field but still have `judgment: incorrect` (17) or `judgment: partially_correct` (1)
  - 0 rows have `status` field; `status="complete"` count is literally 0/500
- `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_score.json`: `"accuracy"` block with all 7 categories at 0.0

**Interpretation**: The 0.0 aggregate is not a measure of model quality — it is a measure of benchmark fingerprint instability. The answer/judge model IS reachable and returns outputs, but its `system_fingerprint` differs from the harness's expected fingerprint, triggering the benchmark's fail-fast on fingerprint drift.

Historical score values from prior harness runs (e.g., 67.8, 81.1, 28-34, 22.4) are not comparable and are superseded by this finding — those scores measured the old thin-prompt harness, not the production-aligned path.

---

## per-category

**Values from artifact**:
| Category | Score |
|---|---|
| IE-user | 0.0 |
| IE-assistant | 0.0 |
| IE-preference | 0.0 |
| MR | 0.0 |
| KU | 0.0 |
| TR | 0.0 |
| ABS | 0.0 |

**Status**: All 0.0 — NOT meaningful performance data. Every category reflects the same fingerprint-drift / no-valid-answer failure, not model behavior in any category.

---

## baseline

**Status**: NO VALID BASELINE — the full aligned corpus was evaluated but 0/500 rows produced a valid completed answer.

**What happened**:
- Task 5's run against the recovered checkpoint reached answer/judge calls for all 500 questions
- 482 rows failed with `Benchmark fingerprint drift in [answer|judge]: expected 'fp_XXX', got 'fp_YYY'`
- 18 rows reached judge without error but still produced no correct answers (499 incorrect, 1 partially_correct)
- No result row contains a `status` field, so raw `status="complete"` is literally 0/500
- Score artifact was written (all categories 0.0), but this is a technical output, not a valid aligned baseline

**What was established by prior tasks**:
- Task 1: Harness adapter implemented — production `[system, user]` prompt path confirmed
- Task 2: Question-ID targeting operational
- Task 3: Provider pinning tests pass (20/20); implementation defaults documented
- Task 4: Smoke test confirmed harness alignment but blocked by missing corpus sessions (earlier blocker, now resolved)
- Task 5: Full run reached answer/judge stage but fingerprint drift blocked valid answer capture

**What is needed**: A valid baseline requires the answer/judge model's system fingerprint to match the harness's expected fingerprint, or the fingerprint-fail-fast check must be disabled for benchmark mode.

---

## sanity

**Verdict**: FAIL / BLOCKED — fingerprint drift on 482/500 rows; 0/500 `status="complete"`; no valid baseline established.

**Score gates**:
- Aggregate above guessing floor: N/A (aggregate is 0.0 due to fingerprint drift, not model behavior)
- No category <5%: All categories at 0.0 — technically above floor but meaningless
- No category >95%: PASS (none at >95%)
- Raw `status="complete"` rate: 0/500 — hard-fail per plan monitoring guardrail (all 500 rows omit the `status` field entirely)

**Blocking cause**: `Benchmark fingerprint drift in [answer|judge]: expected 'fp_XXX', got 'fp_YYY'` — the answer/judge model returns different `system_fingerprint` values than the harness expects, causing fail-fast on 482/500 rows. The model IS reachable and produces outputs, but the benchmark fingerprint contract is violated.

**Note**: The 0.0 scores do not represent model quality. They represent 100% answer-stage failure to produce valid captured answers.

---

## extraction

**Command**: `PYTHONPATH=. python tests/benchmark_extraction.py --json`
**Run timestamp**: 2026-04-30T20:05:36Z
**Result file**: `tests/results/bench_20260430_200545.json`

### Results

| Scenario | Expected | Extracted | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|---|---|
| 1: Dense Personal Facts | 9 | 12 | 9 | 0 | 0 | 1.00 | 1.00 |
| 2: Ephemeral vs Durable | 1 | 1 | 1 | 0 | 0 | 1.00 | 1.00 |
| 3: Corrections and Supersession | 1 | 3 | 1 | 0 | 0 | 1.00 | 1.00 |
| 4: Projects and Goals | 3 | 6 | 3 | 0 | 0 | 1.00 | 1.00 |
| 5: Hedged Statements | 6 | 6 | 6 | 0 | 0 | 1.00 | 1.00 |
| 6: Realistic Multi-Turn Session | 7 | 23 | 7 | 0 | 0 | 1.00 | 1.00 |
| 7: Explicit Memory Instructions | 3 | 6 | 3 | 0 | 0 | 1.00 | 1.00 |
| 8: Adversarial Empty | 0 | 0 | 0 | 0 | 0 | 1.00 | 1.00 |
| **TOTAL** | **30** | **57** | **30** | **0** | **0** | **1.00** | **1.00** |

### Totals
- **Precision**: 1.00 (threshold: ≥0.95) ✓
- **Recall**: 1.00 (threshold: ≥0.85) ✓
- **Adversarial FP**: 0 (threshold: ≤2) ✓
- **Passed**: true

---

## summary

- **aggregate**: 0.0 — NOT VALID; 482/500 rows failed with `Benchmark fingerprint drift`, 18 rows had no error but still no correct answers, 0/500 have `status="complete"`
- **per-category**: all 0.0 — NOT MEANINGFUL; same fingerprint-drift failure, not category performance
- **baseline**: NO VALID BASELINE — full corpus run completed but 0/500 rows have `status="complete"`; fingerprint drift blocked valid answer capture
- **sanity verdict**: FAIL / BLOCKED — fingerprint drift (482/500 rows), raw complete rate 0/500, no valid aligned baseline established
- **extraction**: PASS — P=1.00, R=1.00, A=0; all task thresholds met