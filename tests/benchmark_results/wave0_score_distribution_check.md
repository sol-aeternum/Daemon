# Wave 0 Score Distribution Check

## Retrieval Log Schema

Relevant fields in the retrieval log:

- `candidate_memory_ids`
- `candidate_scores`
- `selected_memory_ids`
- `query_text`
- `query_embedding_model`
- `l0_included`
- `latency_ms`

## Query Correction

The initial query referenced `selected_final_scores`, which does not exist in the schema. Analysis was corrected by using `candidate_scores` instead.

## Score Distribution Summary (First 50 Evaluation Queries)

| Metric | Value |
|---|---|
| `queries_with_logs` | 50 |
| `candidate_score_count` | 246 |
| `median_final_score` | 0.2269718514630993 |
| `p95_final_score` | 0.32697719369003636 |
| `max_final_score` | 0.7098268976720472 |
| `median_top_score_per_query` | 0.26315226417823157 |
| `median_candidate_count_per_query` | 5.0 |

## Top Candidate Score Range

| Metric | Value |
|---|---|
| `top_score_min` | 0.16636434086348661 |
| `top_score_max` | 0.7098268976720472 |
| `top_score_median` | 0.26315226417823157 |

## Threshold Analysis

All top candidate scores sit above `MIN_FINAL_SCORE = 0.15`. Even the weakest sampled top score (0.1664) exceeds the threshold. This means the 0.15 threshold is not the dominant explanation for the observed low score distribution in this sample.

Threshold calibration may still matter at the margin, but current evidence does not support the hypothesis that "everything is being filtered out by 0.15."
