# Judge Drift vs Current Clean Path

## Scope

- **Task**: measure historical judge/matcher looseness/strictness on sampled current failures.
- **Not in scope**: changing models, changing scoring, or rerunning the full benchmark.
- **Goal**: separate what is directly measured from what is only inferred.

## Inputs used

- `tests/benchmark_longmemeval/81_1_DIFF.md`
- `tests/benchmark_results/task16_summary.md`
- `tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_results.jsonl`
- `tests/benchmark_results/longmemeval_optimized_retry/longmemeval_fast_results.jsonl`
- `tests/benchmark_results/longmemeval_optimized_judge_restore/longmemeval_fast_results.jsonl`
- `tests/benchmark_results/longmemeval_repro_91ab1662/longmemeval_fast_results.jsonl`
- `tests/benchmark_results/task15_failure_analysis.json`
- `tests/longmemeval/evaluate.py`

## Measurement method

### A. Strictness isolate: current clean path vs judge-restore replay

Use the saved clean run `longmemeval_optimized_retry` as the current reference and compare it to `longmemeval_optimized_judge_restore` question-by-question.

This is the closest repo-local comparator for “older harsher judge behavior on the clean path”, but it is **not** treated as a perfect 91ab replay because the repo does not document its exact provenance and some rows disagree with `longmemeval_repro_91ab1662`.

### B. Looseness sample: current failures vs the historical 81.1 artifact bundle

Take the current clean run's non-correct rows (`incorrect` or `partially_correct`) and compare them to the saved historical artifact `longmemeval_tier2_fast`.

This measures whether the historical bundle graded the same QIDs more leniently. Because the answer text sometimes differs across runs, this is evidence of **bundle-level** looseness (answer + judge + matcher/accounting), not proof about the judge prompt alone.

### C. Positive-control archaeology for known too-harsh judgments

Use the already-curated Task 15 judge-error QIDs from `task15_failure_analysis.json` plus the `91ab1662` prompt change described in `81_1_DIFF.md` to confirm that older judging semantics were materially stricter on paraphrase / extra-detail answers.

## Measured results

### 1. Older-style judging is materially stricter than the current clean path

Comparing `longmemeval_optimized_retry` to `longmemeval_optimized_judge_restore`:

- Current clean run has **339 `correct`** rows.
- Judge-restore downgrades **172 / 339 = 50.7%** of those correct rows.
  - **149** became `partially_correct`
  - **23** became `incorrect`

Category split for those downgrades:

| Category | Downgraded current-correct rows |
|---|---:|
| TR | 45 |
| IE-user | 43 |
| KU | 30 |
| MR | 26 |
| IE-assistant | 21 |
| IE-preference | 7 |

Representative strictness samples:

| QID | Category | Current clean | Judge-restore | Why it matters |
|---|---|---|---|---|
| `58bf7951` | IE-user | `correct` | `partially_correct` | Exact fact with extra context (“production of The Glass Menagerie”) was downgraded |
| `c5e8278d` | IE-user | `correct` | `partially_correct` | Bare-name answer wrapped in a sentence was downgraded |
| `6f9b354f` | IE-user | `correct` | `partially_correct` | Exact color fact in sentence form was downgraded |
| `6aeb4375` | KU | `correct` | `partially_correct` | Exact count in sentence form was downgraded |

These samples line up with the `81_1_DIFF.md` archaeology: pre-`91ab1662` judging was less generous with paraphrase / extra detail, while the current prompt explicitly says “Be generous with CORRECT.”

### 2. The historical 81.1 bundle is also materially looser on current failures

Comparing the current clean run's non-correct rows to `longmemeval_tier2_fast`:

- Current clean run has **161 non-correct** rows (`128 incorrect`, `33 partially_correct`).
- Historical `tier2_fast` grades **137 / 161 = 85.1%** of those rows **more leniently**.
  - **102** `incorrect -> correct`
  - **26** `incorrect -> partially_correct`
  - **9** `partially_correct -> correct`

Category split for those leniency flips:

| Category | Historical bundle graded more leniently |
|---|---:|
| TR | 67 |
| MR | 37 |
| KU | 25 |
| IE-user | 4 |
| IE-assistant | 3 |
| IE-preference | 1 |

Representative leniency samples from current failures:

| QID | Category | Current clean | Historical `tier2_fast` | Judge-restore | Why it matters |
|---|---|---|---|---|---|
| `0a995998` | MR | `partially_correct` | `correct` | `incorrect` | Same task family ranges from strict (`incorrect`) to lenient (`correct`) across saved paths |
| `6d550036` | MR | `incorrect` | `correct` | `incorrect` | Historical bundle marked an abstention-like answer as fully correct |
| `gpt4_5501fe77` | MR | `incorrect` | `correct` | `partially_correct` | Historical bundle accepted the wrong platform answer as correct |
| `gpt4_31ff4165` | MR | `partially_correct` | `correct` | `incorrect` | Same underlying shortfall spans all three labels across saved artifacts |
| `f4f1d8a4_abs` | IE-user | `incorrect` | `correct` | `incorrect` | Historical bundle credited a clean abstention on an abstention question; current clean path did not |
| `66f24dbb` | IE-user | `incorrect` | `partially_correct` | `partially_correct` | Extra unsupported detail softened the verdict historically |

### 3. The 81.1 artifact is not a pure “better judge” story

`81_1_DIFF.md` already established that:

- the committed scorer stayed correct-only,
- the saved `81.1%` number lives in the summary/accounting layer,
- the historical bundle has split provenance,
- and `tier2_fast` recorded **311 correct / 189 partially_correct / 0 incorrect**.

This drift check adds two concrete consequences:

1. **Historical semantics were stricter** on many exact/paraphrastic answers that the current clean path now marks `correct`.
2. **The saved 81.1 bundle was simultaneously looser overall** on many QIDs that the current clean path now marks `incorrect` or `partially_correct`.

So the residual “81.1 advantage” cannot be reduced to a single clean causal statement like “the old judge was looser” or “the old judge was stricter.” The evidence shows **both** behaviors, depending on which artifact path is being compared.

## Best-supported interpretation

- **Measured**: older-style judging on the clean path is stricter than the current clean judge on paraphrase / sentence-form exact answers.
- **Measured**: the saved historical 81.1 bundle is more lenient than the current clean path on most current failure QIDs.
- **Inferred**: the historical bundle's advantage came from a mix of judge/matcher behavior plus artifact-level answer/accounting differences, not a single stable judge policy.
- **Uncertain**: exactly how much of the historical leniency was caused by judge prompt wording vs answer-generation variance vs lost retrieval/judge traces, because the raw historical retrieval logs and exact judgment responses were not preserved.

## Sampling notes

- Strictness samples were chosen from exact QIDs where the current clean path is `correct` but judge-restore is lower.
- Looseness samples were chosen from current clean failures where the historical bundle is higher.
- The sample QIDs above are locked by `tests/benchmark_longmemeval/test_judge_drift_samples.py` so the report's examples stay reproducible.

## Validation commands

```bash
pytest tests/benchmark_longmemeval/test_judge_drift_samples.py
```

That test file validates:

- the measured strictness totals against `optimized_retry` vs `optimized_judge_restore`
- the measured leniency totals against current non-correct rows vs `tier2_fast`
- the representative sample QID transitions cited in this report
