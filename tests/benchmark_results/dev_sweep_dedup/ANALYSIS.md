# Dedup Threshold Sensitivity Dev Sweep Analysis

Generated: 2026-04-18T20:18:07+00:00

This sweep replays the locked canonical extracted facts through live `dedup.py` while pinning `TOP_K_MEMORIES = 6`, `TEMPORAL_QUERY_FILTER_ENABLED = True`, `MIN_FINAL_SCORE = 0.15`, and the current hybrid ranking weights.
That keeps extraction output fixed so dedup thresholds are the only moving part.

## Sweep gate

A second tighter point only runs if the first tighter point improves the tracked `generation-error × knowledge-update` subset and avoids regressions in strict accuracy, locked-failure-union recovery, protected primary-category accuracy, and the approved retrieval-miss target cells.

## Score and memory-count summary

| Run | Thresholds (`merge / supersede / same-slot`) | Strict score | Δ vs current | Tracked KU generation-error | Locked failure union | Total memories | Δ memories | Active memories | Δ active | Regressions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current | 0.90 / 0.82 / 0.65 | 26.0% | +0.0% | 1/2 | 2/39 | 4286 | +0 | 4012 | +0 | none |
| tight_01 | 0.92 / 0.85 / 0.70 | 32.0% | +6.0% | 1/2 (+0) | 5/39 (+3) | 4323 | +37 | 4043 | +31 | protected_cell:single-session-assistant -11.1% |

## Replay integrity

- Source canonical state: `tests/benchmark_results/dev_sweep_temporal/on` using `tests/benchmark_results/dev_sweep_temporal/on/longmemeval_checkpoint.json`.
- Replayed corpus conversations: `2079` with `1311` conversations carrying extracted facts.
- Current replay post-ingestion memories: `4286` total / `4012` active / `274` historical.
- Current replay failures carried into ingest metadata: `0`.

## Recommendation

- Verdict: `current dedup thresholds remain`
- Reason: No tighter dedup point improved the tracked `generation-error × knowledge-update` subset without introducing strict-score, locked-failure, protected-cell, or approved-target regressions.
- Best non-qualifying alternative: `tight_01` at `{'merge': 0.92, 'same_slot': 0.7, 'supersede': 0.85}`
