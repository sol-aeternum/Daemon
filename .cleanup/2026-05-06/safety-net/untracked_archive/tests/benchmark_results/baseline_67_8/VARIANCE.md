# Baseline 67.8 Variance Gate

Date: 2026-04-18

## Locked command shape

Canonical lane only:

```text
DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon PYTHONPATH=. python -m orchestrator.eval.longmemeval run --dataset /tmp/longmemeval-review/data/longmemeval_s.json --output-dir tests/benchmark_results/baseline_67_8/runN
```

Dataset authority: `/tmp/longmemeval-review/data/longmemeval_s.json`

## Evidence preservation

- The blocked **pre-fix** attempt was preserved under `tests/benchmark_results/baseline_67_8/pre_fix_attempt/`.
- That preserved bundle contains:
  - `pre_fix_attempt/run1/longmemeval_checkpoint.json`
  - `pre_fix_attempt/VARIANCE.md`
- Fresh baseline execution below was started only **after** preserving that evidence and deliberately resetting the shared benchmark user/state to zero rows.

## Current run status

| Run | Outcome | Artifacts present | Score | Notes |
| --- | --- | --- | --- | --- |
| run1 | interrupted after corrected-barrier blocker evidence | `longmemeval_checkpoint.json` only | n/a | Fresh run after explicit cleanup. Preserved checkpoint state at stop: `ingest.completed_count = 20`, with `12 complete` and `8 extraction_timeout`. |
| run2 | not started | none | n/a | Not started because corrected run1 still projected multi-day total runtime for a truthful three-run lock. |
| run3 | not started | none | n/a | Not started because corrected run1 still projected multi-day total runtime for a truthful three-run lock. |

## Corrected-barrier run1 evidence

- The live default canonical barrier was confirmed in log output as the corrected contract:
  - `Extraction polling timed out ... after 5.0s (start interval 0.1s, backoff x2.0, cap 2.0s)`
- Start log line: `13:32:11` (`[ingest] [1/18464] sharegpt_yywfIrx_0 ingesting`)
- By `13:34:16`, the log had completed 20 corpus sessions and moved into session 21.
- Preserved checkpoint summary at stop:
  - `ingest.completed_count = 20`
  - status mix: `12 complete`, `8 extraction_timeout`
  - `ingest.status = "running"`
- Shared benchmark user state after the interrupted corrected attempt:
  - `users = 1`
  - `conversations = 21`
  - `messages = 206`
  - `memories = 37`
  - `retrieval_logs = 0`
  - `extraction_logs = 12`

## Throughput calculation (corrected barrier)

- Total canonical corpus sessions for this dataset: `18,464`
- Observed completed sessions before stop: `20`
- Observed wall time for those 20 completed sessions: `125s`
- Observed average per completed session: `125 / 20 = 6.25s`
- Projected single-run ingest duration if sustained: `18,464 * 6.25s = 115,400s = 32.06h`
- Projected ingest-only time for three consecutive runs: `96.17h`

This estimate is a **lower bound** because it covers ingest only and excludes later evaluate/score time.

## Variance calculation

- `run1 score = n/a` (no `longmemeval_score.json` produced)
- `run2 score = n/a` (not started)
- `run3 score = n/a` (not started)
- `spread_pp = n/a` (fewer than two completed scored runs)

## Gate verdict

**Phase 0 incomplete**

Reason: even with the corrected `5.0s / 0.1s / x2.0 / 2.0s` default barrier, the locked canonical baseline still did not produce a full scored run. The fresh clean run1 improved materially versus the old 90-second barrier, but the observed corrected-barrier pace still projects to roughly **32.06 hours for ingest alone** and **96.17 hours for three ingest passes alone**, before evaluation/scoring. A truthful three-run variance lock is therefore not feasible from this session.
