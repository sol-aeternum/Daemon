# Harness Parity Baseline Decision — T15

- **Generated at**: 2026-05-06T02:49:00Z
- **Status**: HALT
- **Decision**: baseline undeterminable

## Inputs

- T15 plan rule: `.sisyphus/plans/longmemeval-harness-production-parity.md:681-717`
- T14 artifact: `tests/benchmark_results/harness_parity_baseline_run.json`
- Aggregate comparison baseline: `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md`
- Rank-order source: `tests/benchmark_results/wave0_closure_memo.md`

## T15 anomaly criteria

Per the plan, T15 would only compute anomaly math from a **completed** T14 full-corpus baseline:

1. Aggregate anomaly if the T14 aggregate deviates by **more than 10 percentage points absolute** from the Wave 0 Option A production-aligned baseline of **49 / 473 = 0.10359408033826638** (rounded in plan text as **10.4%**).
2. Rank-order anomaly if **more than 3 of 6 reported category ranks move** relative to the closure-memo category numbers.
3. Confirmation run only if an anomalous completed baseline exists.
4. Default declaration rule:
   - no anomaly = T14 number
   - anomaly + agreement within ±2pp = mean of the two aggregate scores
   - anomaly + disagreement = **HALT baseline undeterminable**

## Authoritative T14 status

`tests/benchmark_results/harness_parity_baseline_run.json` is authoritative and reports:

- `status: "halt"`
- `halt_reason: "Full haystack-bearing LongMemEval_S corpus unavailable"`
- `aggregate_adjusted_score: null`
- `per_category_scores: null`
- `records: null`

The same artifact states that the parity harness is implemented and ready, and that the HALT is caused by corpus unavailability rather than parity-harness defects.

## What is not executable

T15 does **not** silently skip anomaly math or confirmation. Those steps are **not executable** because T14 did not produce a completed first full-corpus baseline:

- Aggregate deviation cannot be computed because `aggregate_adjusted_score` is `null`.
- Per-category rank movement cannot be computed because `per_category_scores` is `null`.
- Raw replay math cannot be computed because `records` is `null`.
- A confirmation run cannot be launched because there is no completed first full-corpus run to confirm, and the full haystack-bearing LongMemEval_S corpus is still unavailable.

## Decision

**HALT — baseline undeterminable.**

T15 is blocked by the T14 HALT. No new production-faithful full-corpus baseline can be declared from the current artifact set, and no confirmation run can be performed. The Wave 0 Option A number remains the historical comparison anchor only; it is **not** replaced by a new T15 baseline because no valid T14 completed run exists.

## Dependency chain

- **T14** halted because the full haystack-bearing LongMemEval_S corpus is unavailable.
- **T15** therefore cannot execute anomaly math or conditional confirmation.
- **T16-T20** remain blocked until a real completed T14 full-corpus baseline exists.

## Required resolution

Restore access to the full 500-question LongMemEval_S corpus with `haystack_sessions`, rerun T14 to produce fresh full-corpus records and aggregate/per-category outputs, and only then rerun T15.
