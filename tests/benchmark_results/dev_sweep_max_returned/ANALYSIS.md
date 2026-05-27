# TOP_K_MEMORIES Dev Sweep Analysis

Generated: 2026-04-18T15:22:52+00:00

This dev-subset ablation keeps the canonical lane pinned and varies only caller-side `TOP_K_MEMORIES`. `k05` is the current return limit baseline; `k06`..`k09` measure whether returning more already-ranked memories improves strict score or retrieval-heavy failure cells.

## Score and token summary

| k | Strict score | Δ vs run1 | Δ vs run2 | Locked failure union correct | Retrieval-miss correct | Mean prompt tokens | Δ tokens vs k05 | Beyond-limit support matches | Recovered within top-k |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | 30.0% | -2.0% | +8.0% | 4/39 | 2/20 | 113.74 | +0.00 | 0 | 0 |
| 6 | 32.0% | +0.0% | +10.0% | 6/39 | 3/20 | 127.88 | +14.14 | 0 | 0 |
| 7 | 24.0% | -8.0% | +2.0% | 1/39 | 1/20 | 142.10 | +28.36 | 0 | 0 |
| 8 | 32.0% | +0.0% | +10.0% | 5/39 | 3/20 | 156.04 | +42.30 | 0 | 0 |
| 9 | 22.0% | -10.0% | +0.0% | 3/39 | 3/20 | 169.70 | +55.96 | 0 | 0 |

## Retrieval-heavy subset deltas

| k | Multi-session retrieval-miss | Single-session-user retrieval-miss | Temporal retrieval-miss |
| --- | --- | --- | --- |
| 5 | 1/6 (+1 vs run2) | 0/6 (+0 vs run2) | 1/5 (+1 vs run2) |
| 6 | 1/6 (+1 vs run2) | 2/6 (+2 vs run2) | 0/5 (+0 vs run2) |
| 7 | 0/6 (+0 vs run2) | 0/6 (+0 vs run2) | 1/5 (+1 vs run2) |
| 8 | 1/6 (+1 vs run2) | 0/6 (+0 vs run2) | 1/5 (+1 vs run2) |
| 9 | 0/6 (+0 vs run2) | 1/6 (+1 vs run2) | 2/5 (+2 vs run2) |

## Correct-memory beyond-limit evidence

- Exact-support diagnostics found `support_beyond_current_limit = 0` across the completed sweep and `support_recovered_within_top_k = 0`.
- In this dev subset, the current evidence method did **not** produce a case where an exact supporting memory first appeared below rank 5 and was then recovered by a higher `TOP_K_MEMORIES` setting.
- That means any score movement in this sweep is better explained by broader context changes, non-exact supporting memories, or normal answer/judge variance than by a clean truncation-recovery proof.

## Recommendation

Recommend `TOP_K_MEMORIES = 6` for follow-up work on the dev subset.
It delivered the best strict score in this sweep (`32.0%`) and the strongest locked-failure recovery signal (`6/39` locked failures answered correctly) while keeping token cost lower than the larger-return alternatives.
Against the current `k05` return limit, its mean estimated answer-prompt cost changed by `+14.14` tokens per question.
