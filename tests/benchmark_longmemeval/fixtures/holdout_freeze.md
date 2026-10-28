# LongMemEval_S holdout freeze — 2026-10-28

Status: Phase 0 selection frozen; **no ingest, evaluation or tuning on this holdout**.
The owner approved proportional allocation before selection. This does not ratify
the primary metric or `ACCEPTANCE_RULE_PROPOSED.md`, or authorize final holdout inference.

## Inputs and method

- Full public dataset: [upstream LongMemEval_S cleaned file](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/blob/main/longmemeval_s_cleaned.json),
  500 rows, SHA-256 `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`.
  Download bytes matched the upstream LFS hash. The old documented `longmemeval_s.json`
  download URL returns HTTP 404; that is not an alternative dataset.
- Dev input: `dev_subset.json`, 50 rows,
  SHA-256 `6612e3a03c79b92bab2f1750d4661908e3dc37359715b7fb6b62c6f758bddb34`.
  All dev IDs, question types and answer-session sets match the full input.
- Selector: `tests/benchmark_longmemeval/holdout.py`, algorithm
  `type-x-abstention-largest-remainder-sha256-v1`, seed **42**, size **100**.
- Exclude dev question IDs and every candidate sharing an `answer_session_ids` value
  with dev. There are 450 eligible rows; no additional non-dev rows share those IDs.
- Stratify by question type × abstention (`"_abs" in question_id`, matching the
  official evaluator). Allocate proportionally to eligible counts with integer
  largest remainders; ties use ascending stratum name. Within each stratum, sort by
  SHA-256 of compact JSON `[algorithm, seed, stratum, question_id]`, then question ID.
  No score-based selection, seed search, category substitution or fallback sizing.

The complete IDs-only manifest is `holdout_ids.json`. Its selection hash is
`58ac5a5dd4c9f3f4af5ef123be04cd977f1ad2a854253ba3b93b91f2641bc502`
(sorted UTF-8 IDs, one per line, including the final newline).

| Type | Answerable | Abstention | Total |
|---|---:|---:|---:|
| single-session-user | 12 | 1 | 13 |
| single-session-assistant | 11 | 0 | 11 |
| single-session-preference | 6 | 0 | 6 |
| multi-session | 25 | 2 | 27 |
| temporal-reasoning | 27 | 1 | 28 |
| knowledge-update | 14 | 1 | 15 |
| Total | 95 | 5 | 100 |

Proportional sampling preserves the eligible distribution approximately; category
and especially abstention estimates remain small-sample diagnostics. Shared
supporting sessions among holdout rows are not excluded; this is dev/holdout
disjointness, not a claim of independent supporting corpora within holdout.

## No-inspection record

Before freezing, the agent received only field names, aggregate counts and hashes.
The download and JSON parser handled complete bytes locally, but selection accessed
only `question_id`, `question_type` and `answer_session_ids`. No holdout question,
answer, haystack message, hypothesis or score was printed, read with file tools,
sent to a model, or used to select rows. Historical dev results were already inspected;
they were not used for allocation or within-stratum ranking. No holdout payload is
committed. The local full input is retained outside the checkout as recovery evidence.

Reproduce without printing content (output must not already exist):

```sh
python -m tests.benchmark_longmemeval.holdout \
  --dataset /path/to/longmemeval_s_cleaned.json \
  --dev tests/benchmark_longmemeval/fixtures/dev_subset.json \
  --output /path/to/new-holdout-manifest.json --size 100 --seed 42
```

The output is byte-deterministic; a second freeze must use a different output path.
The CLI never downloads, ingests, calls a provider, or overwrites the frozen manifest.
