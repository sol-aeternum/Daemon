# Temporal Filter Dev Sweep Analysis

Generated: 2026-04-18T15:47:15+00:00

This dev-subset ablation keeps the canonical lane pinned while comparing the conservative temporal window filter off vs on. The shared dev configuration pins `TOP_K_MEMORIES = 6` from Task 3a, so the only on/off retrieval difference here is `TEMPORAL_QUERY_FILTER_ENABLED`.

## On/off summary

| Run | Temporal filter | Strict score | Δ vs off | Locked failure union correct | Temporal retrieval-miss correct | Armed temporal queries | Armed target-cell queries |
| --- | --- | --- | --- | --- | --- | --- | --- |
| off | disabled | 26.0% | +0.0% | 3/39 | 0/5 | 8 | 0 |
| on | enabled | 26.0% | +0.0% | 2/39 | 0/5 | 8 | 0 |

## Target-cell gate

- Locked target failure cell: `retrieval-miss × temporal-reasoning` with `5` cases.
- Subset-veto threshold: `5` locked cases.
- Subset gate passes: `True`.
- Armed target-cell QIDs on this dev subset: none.

## Promotion decision

- Eligible for full-corpus promotion: `False`.
- Reason: The locked dev subset did not show a clean temporal-cell win without broader regression, so the temporal filter should not advance to full-corpus promotion yet.

The promotion rule stays conservative: the temporal filter must keep the target cell above the subset-veto floor, improve the temporal retrieval-miss subset over the off arm, and avoid regressing strict accuracy or locked-failure-union recovery.
