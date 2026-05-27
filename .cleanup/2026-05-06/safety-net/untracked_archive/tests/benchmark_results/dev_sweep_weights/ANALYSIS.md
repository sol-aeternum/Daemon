# Ranking Weight Dev Sweep Analysis

Generated: 2026-04-18T16:46:08+00:00

This dev-subset ablation keeps the canonical lane pinned to the current retrieval state: `TOP_K_MEMORIES = 6`, `TEMPORAL_QUERY_FILTER_ENABLED = True`, `INITIAL_VECTOR_CANDIDATES = 10`, and `MIN_FINAL_SCORE = 0.15`.
The current-weight comparator is reused from `tests/benchmark_results/dev_sweep_temporal/on/`, which already reflects the current-main non-weight retrieval configuration with weights `0.5 / 0.3 / 0.2`.

## Qualification rule

An alternative weight set is only considered better if it improves the primary approved target cell `retrieval-miss × multi-session` **and** avoids dev-subset regressions in strict accuracy, locked-failure-union recovery, protected primary-category accuracy, and the other approved retrieval-miss target cells.

## Current comparator and alternatives

| Run | Weights (vector / BM25 / recency-confidence) | Strict score | Δ vs current | Locked failure union | Multi-session retrieval-miss | Single-session-user retrieval-miss | Temporal retrieval-miss | Regressions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current | 0.50 / 0.30 / 0.20 | 26.0% | +0.0% | 2/39 | 1/6 | 1/6 | 0/5 | none |
| vector_heavy | 0.60 / 0.25 / 0.15 | 22.0% | -4.0% | 0/39 (-2) | 0/6 (-1) | 0/6 (-1) | 0/5 (+0) | strict_accuracy -4.0%, locked_failure_union -2/39, protected_cell:single-session-user -11.1%, protected_cell:multi-session -10.0%, target_cell:retrieval_miss_multi_session -1, target_cell:retrieval_miss_single_session_user -1 |
| balanced | 0.40 / 0.40 / 0.20 | 32.0% | +6.0% | 5/39 (+3) | 1/6 (+0) | 1/6 (+0) | 1/5 (+1) | none |
| bm25_heavy | 0.30 / 0.50 / 0.20 | 26.0% | +0.0% | 3/39 (+1) | 0/6 (-1) | 0/6 (-1) | 1/5 (+1) | protected_cell:single-session-user -11.1%, protected_cell:multi-session -10.0%, protected_cell:knowledge-update -11.1%, target_cell:retrieval_miss_multi_session -1, target_cell:retrieval_miss_single_session_user -1 |

## Recommendation

- Verdict: `current weights remain locally optimal`
- Reason: No approved alternative improved the primary retrieval-miss × multi-session target cell without introducing a strict-score, locked-failure, protected-cell, or other approved-target regression on the locked dev subset.
- Best non-qualifying alternative: `balanced`
