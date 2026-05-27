# Dev-Subset Reproducibility Baseline Variance Gate

Date: 2026-04-18

## Locked command shape

Canonical lane only:

```text
DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon PYTHONPATH=. python -m orchestrator.eval.longmemeval run --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json --output-dir tests/benchmark_results/dev_subset_baseline/runN
```

Dataset authority: `tests/benchmark_longmemeval/fixtures/dev_subset.json`

## Clean run boundaries

- `run1` was the first clean canonical dev-subset pass after an explicit cleanup reset.
- `run2` was also started only after an explicit cleanup reset of the shared benchmark user/state.
- `run3` was **not** started because the variance gate was already violated after `run2`.

## Run status

| Run | Outcome | Artifacts present | Strict score | Notes |
| --- | --- | --- | --- | --- |
| run1 | completed | `longmemeval_checkpoint.json`, `longmemeval_results.jsonl`, `longmemeval_score.json` | `32.0%` | Checkpoint phases complete: ingest `2079`, evaluate `50`, score `50`. Judgment mix: `16 correct`, `32 incorrect`, `2 partially_correct`. |
| run2 | completed | `longmemeval_checkpoint.json`, `longmemeval_results.jsonl`, `longmemeval_score.json` | `22.0%` | Checkpoint phases complete: ingest `2079`, evaluate `50`, score `50`. Judgment mix: `11 correct`, `37 incorrect`, `2 partially_correct`. |
| run3 | not started | none | n/a | Not started because the revised dev-subset reproducibility gate was already violated after two completed scored runs. |

## Scored artifact details

### run1

- `tests/benchmark_results/dev_subset_baseline/run1/longmemeval_score.json`
- `result_count = 50`
- Category accuracies:
  - `IE-user = 44.4%`
  - `IE-assistant = 44.4%`
  - `IE-preference = 0.0%`
  - `MR = 30.0%`
  - `KU = 44.4%`
  - `TR = 10.0%`
  - `ABS = 0.0%`

### run2

- `tests/benchmark_results/dev_subset_baseline/run2/longmemeval_score.json`
- `result_count = 50`
- Category accuracies:
  - `IE-user = 22.2%`
  - `IE-assistant = 33.3%`
  - `IE-preference = 0.0%`
  - `MR = 20.0%`
  - `KU = 33.3%`
  - `TR = 10.0%`
  - `ABS = 0.0%`

## Variance calculation

- `run1 strict_accuracy = 16 / 50 = 0.32 = 32.0%`
- `run2 strict_accuracy = 11 / 50 = 0.22 = 22.0%`
- `run3 strict_accuracy = n/a` (not started)
- Current spread across completed scored runs = `32.0pp - 22.0pp = 10.0pp`
- Required gate: `<= 3pp`

## Gate verdict

**Phase 0 incomplete**

Reason: the revised dev-subset reproducibility gate is already violated. The first two fully completed canonical dev-subset passes differ by **10.0 percentage points** on strict correct-only accuracy (`32.0%` vs `22.0%`), which is well outside the allowed `<= 3pp` envelope. Per the revised plan semantics, Phase 0 should be reopened rather than continuing blindly into `run3`.
