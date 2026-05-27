# Harness Parity Baseline Stability

Date: 2026-05-27

## Decision

**ARTIFACT_DECISION**: declare-new-w1-anchor

## 1. Scope and inputs

This artifact computes the Task 8 stability gate and baseline anchor using the two authorized inputs below:

- Run1 full summary: `tests/benchmark_results/harness_parity_baseline/run1/summary.json`
- Run2 headline-only metadata: `tests/benchmark_results/harness_parity_baseline/run2/headline_summary.json`

Run2 is intentionally headline-only because Task 7 closed on the user-authorized waiver path after the original raw run2 artifacts were lost:

> do not begin another run 2, we can move ahead with the headline numbers only. a full rerun is 48 hours of wasted time given the headline numbers

## 2. Run summaries used for the stability gate

| Run | Source | Aggregate | Correct | Denominator / Total | Excluded runtime errors | Notes |
|---|---|---:|---:|---:|---:|---|
| Run1 | `run1/summary.json` | 0.1342685370741483 | 67 | 499 denominator from 500 submitted | 1 | Full raw summary available |
| Run2 | `run2/headline_summary.json` | 0.146 | 73 | 500 total | 0 | Headline-only; raw artifacts unavailable |

### Run1 raw counts

- Correct: **67**
- Denominator: **499**
- Total submitted: **500**
- Runtime / excluded errors: **1**

### Run2 headline counts

- Correct: **73**
- Total: **500**
- Excluded runtime errors: **0**
- Raw artifacts available: **false**
- Per-category data available: **false**

## 3. Aggregate stability computation

- Run1 aggregate: `0.1342685370741483`
- Run2 aggregate: `0.146`
- Aggregate delta: `0.011731462925851695`
- Aggregate threshold: `0.02`

Computation recorded for Task 8:

`abs(0.1342685370741483 - 0.146) = 0.011731462925851695`

Because `0.011731462925851695 <= 0.02`, the aggregate stability gate **passes**.

## 4. Anchor computation

Task 8 uses the arithmetic mean of the stable run pair for the aggregate anchor:

`(0.1342685370741483 + 0.146) / 2 = 0.14013426853707415`

Declared aggregate anchor: `0.14013426853707415`

## 5. Category delta status

Category deltas are **waived / unavailable** for Task 8.

- Task 7 closed on the user-authorized headline-only path.
- The original raw run2 `results.jsonl` and full `summary.json` were lost before repo copy-back.
- `run2/headline_summary.json` is intentionally non-raw and does not contain per-category metrics.
- No per-category run2 values are invented here.

Run1 remains the only available source for per-category raw counts:

| Category | Correct | Incorrect | Excluded |
|---|---:|---:|---:|
| single-session-user | 16 | 53 | 1 |
| multi-session | 16 | 117 | 0 |
| single-session-preference | 4 | 26 | 0 |
| temporal-reasoning | 8 | 125 | 0 |
| knowledge-update | 15 | 63 | 0 |
| single-session-assistant | 8 | 48 | 0 |

## 6. Run3 disposition

No run3 will be started.

- The user explicitly forbade another rerun.
- The aggregate stability gate passes.
- Task 8 therefore closes on the authorized headline-only stability path.

## 7. Final declaration

- Aggregate stable: **true**
- Category delta status: **waived-unavailable-headline-only-run2**
- Run3 required: **false**
- Declared aggregate baseline anchor: **0.14013426853707415**
- Final Task 8 decision: **declare-new-w1-anchor**
