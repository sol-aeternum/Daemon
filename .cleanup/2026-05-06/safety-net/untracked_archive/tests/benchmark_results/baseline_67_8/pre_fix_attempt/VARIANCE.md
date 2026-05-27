# Baseline 67.8 Variance Gate

Date: 2026-04-18

## Locked command shape

Canonical lane only:

```text
DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon PYTHONPATH=. python -m orchestrator.eval.longmemeval run --dataset /tmp/longmemeval-review/data/longmemeval_s.json --output-dir tests/benchmark_results/baseline_67_8/runN
```

Dataset authority: `/tmp/longmemeval-review/data/longmemeval_s.json`

## Run status

| Run | Outcome | Artifacts present | Score | Notes |
| --- | --- | --- | --- | --- |
| run1 | interrupted after blocker evidence | `longmemeval_checkpoint.json` only | n/a | Checkpoint preserved `ingest.completed_count = 4` with 4 recorded corpus results (`3 extraction_timeout`, `1 complete`) and `ingest.status = "running"` at stop time. |
| run2 | not started | none | n/a | Not started because run1 did not complete and the observed canonical ingest rate made a three-run baseline infeasible in this task window. |
| run3 | not started | none | n/a | Not started because run1 did not complete and the observed canonical ingest rate made a three-run baseline infeasible in this task window. |

## Run1 blocker evidence

- Start log line: `13:08:28` (`[ingest] [1/18464] sharegpt_yywfIrx_0 ingesting`)
- By `13:13:10`, the log had already advanced to `[5/18464]`, meaning **4 canonical corpus sessions completed** in **282 seconds**.
- Three of those four completed sessions paid the full fixed extraction poll timeout:
  - `Extraction polling timed out for conversation bf67164b-ef2d-45a2-b6af-430ee7d66177 after 90.0s`
  - `Extraction polling timed out for conversation b1179c6d-2aea-4921-b19e-9390ff26134e after 90.0s`
  - `Extraction polling timed out for conversation 196fbe70-1e28-4c58-8be4-bded11040274 after 90.0s`
- The preserved checkpoint contains 4 completed ingest rows, while the shared benchmark user already held partial interrupted state after the stop (`5 conversations`, `34 messages`, `1 memory`, `1 extraction_log` row).

## Throughput calculation

- Total canonical corpus sessions for this dataset: `18,464`
- Observed completed sessions before stop: `4`
- Observed wall time for those 4 completed sessions: `282s`
- Observed average per completed session: `282 / 4 = 70.5s`
- Projected single-run ingest duration if sustained: `18,464 * 70.5s = 1,301,712s = 361.59h ≈ 15.07 days`

This estimate is only for the observed ingest pace and does **not** include later evaluate/score time, so it is a lower bound on total run duration.

## Variance calculation

- `run1 score = n/a` (no `longmemeval_score.json` produced)
- `run2 score = n/a` (not started)
- `run3 score = n/a` (not started)
- `spread_pp = n/a` (fewer than two completed scored runs)

## Gate verdict

**Phase 0 incomplete**

Reason: the locked canonical baseline did not produce even one full scored run. The preserved run1 evidence shows repeated fixed `90.0s` extraction-poll timeouts and an observed ingest pace that projects to roughly **15 days for one run** under current conditions, so a three-run variance lock cannot be completed truthfully from this session.
