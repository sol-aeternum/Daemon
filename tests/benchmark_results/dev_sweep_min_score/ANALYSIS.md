# MIN_FINAL_SCORE Dev Sweep Analysis

Generated: 2026-04-18T17:12:18+00:00

This dev-subset ablation keeps the canonical lane pinned to the current retrieval state: `TOP_K_MEMORIES = 6`, `TEMPORAL_QUERY_FILTER_ENABLED = True`, `INITIAL_VECTOR_CANDIDATES = 10`, and varies only `MIN_FINAL_SCORE`.
The current-threshold comparator is reused from `tests/benchmark_results/dev_sweep_temporal/on/`, which already reflects the current-main non-threshold retrieval configuration.

## Qualification rule

An alternative threshold is only considered better if it improves the primary approved target cell `retrieval-miss × single-session-user` **and** avoids dev-subset regressions in strict accuracy, locked-failure-union recovery, protected primary-category accuracy, and the other approved retrieval-miss target cells.

## Score deltas and target-cell results

| Threshold | Strict score | Δ vs current | Locked failure union | Single-session-user retrieval-miss | Multi-session retrieval-miss | Temporal retrieval-miss | Regressions |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current | 0.15 | 26.0% | +0.0% | 2/39 | 1/6 | 1/6 | 0/5 | none |
| 0.05 | 30.0% | +4.0% | 4/39 (+2) | 0/6 (-1) | 0/6 (-1) | 1/5 (+1) | protected_cell:single-session-user -11.1%, protected_cell:multi-session -10.0%, target_cell:retrieval_miss_multi_session -1, target_cell:retrieval_miss_single_session_user -1 |
| 0.10 | 26.0% | +0.0% | 3/39 (+1) | 1/6 (+0) | 0/6 (-1) | 0/5 (+0) | protected_cell:multi-session -10.0%, target_cell:retrieval_miss_multi_session -1 |
| 0.15 | 22.0% | -4.0% | 1/39 (-1) | 1/6 (+0) | 0/6 (-1) | 0/5 (+0) | strict_accuracy -4.0%, locked_failure_union -1/39, protected_cell:single-session-assistant -11.1%, protected_cell:multi-session -10.0%, target_cell:retrieval_miss_multi_session -1 |
| 0.20 | 24.0% | -2.0% | 3/39 (+1) | 0/6 (-1) | 0/6 (-1) | 1/5 (+1) | strict_accuracy -2.0%, protected_cell:single-session-user -11.1%, protected_cell:multi-session -10.0%, target_cell:retrieval_miss_multi_session -1, target_cell:retrieval_miss_single_session_user -1 |
| 0.25 | 22.0% | -4.0% | 2/39 (+0) | 0/6 (-1) | 0/6 (-1) | 0/5 (+0) | strict_accuracy -4.0%, protected_cell:single-session-user -11.1%, protected_cell:multi-session -20.0%, target_cell:retrieval_miss_multi_session -1, target_cell:retrieval_miss_single_session_user -1 |

## Recommendation

- Verdict: `current threshold (0.15) remains`
- Reason: No threshold value improved the primary approved target cell `retrieval-miss × single-session-user` without introducing a strict-score, locked-failure, protected-cell, or other approved-target regression on the locked dev subset.
- Best non-qualifying alternative: `score_0.10` at 0.1
