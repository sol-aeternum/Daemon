# Portable LongMemEval Advantages After Subtraction

## Scope

- **Task**: subtract non-portable explanations from the historical `81.1%` LongMemEval gap and keep only residual advantages that can be tested cleanly on current `main`.
- **Historical headline artifact**: `81.1%` weighted summary, but only `62.2%` strict correct-only (`311 correct / 189 partially_correct / 0 incorrect`).
- **Current clean reference**: `67.8%` strict correct-only from `longmemeval_optimized_retry` (`339 correct / 33 partially_correct / 128 incorrect`) at `2000/0 + k=10 + generous judge`.
- **Current replay of the visible `91ab1662` shape**: `53.6%` strict correct-only from `longmemeval_repro_91ab1662`, with `~5` memories returned per question and only `267.562` average chunks.

## Non-portable explanations that must be subtracted first

### 1. Summary-layer weighting and bundle leniency

- The historical artifact's own summary/accounting layer adds **`81.1 - 62.2 = 18.9` points** over its strict raw judgments.
- The entire headline gap versus the current clean reference is only **`81.1 - 67.8 = 13.3` points**.
- That means the weighted/manual artifact uplift is already larger than the claimed historical advantage over today's clean benchmark, so the headline gap cannot be credited to a portable runtime lever.

### 2. Judge drift and bundle-level grading drift

- `JUDGE_DRIFT.md` shows the old-style harsher comparator (`optimized_judge_restore`) downgrades **`172 / 339`** current-correct rows, so old judging was not a stable portable boost.
- The saved historical `tier2_fast` bundle is simultaneously more lenient on **`137 / 161`** current non-correct rows, proving the artifact bundle also contains looser grading/accounting behavior that cannot be treated as a clean knob.
- Result: judge behavior is two-sided artifact drift, not a portable historical advantage.

### 3. Contamination and split provenance

- `CONTAMINATION_ANALYSIS.md` ranks the preserved `81.1%` bundle as **artifact-trust contaminated**: the saved `run.log` and the final clean checkpoint/results are not one preserved execution trace.
- Historical retrieval logs were not retained, so specific retrieval-state claims remain unprovable.
- Canonical shared-user contamination vectors exist elsewhere in the stack, but the `81.1%` artifact itself belongs to the fast lane and cannot donate clean leverage to today's main branch.

### 4. Historical-only fixture quirks that do not replay cleanly

- The preserved historical bundle reports **`avg_chunks = 331.74`**, matching the current clean `2000/0` run.
- The current replay of the visible `4000/2 + k=5` shape reports only **`avg_chunks = 267.562`** while keeping the expected `~5` retrieved memories.
- So the preserved bundle mixes current-clean chunk geometry with historical-low retrieval depth. That combination is not a portable, single-knob state that current `main` can exercise honestly.

## Residual verdict

No clean portable historical advantages survive subtraction.

After removing weighting drift, judge/bundle drift, contamination, split provenance, and historical-only fixture quirks, every evidence-backed historical difference either disappears, becomes non-portable, or points the wrong direction under current `main`.

## Portable current-main knobs reviewed anyway

| Candidate | Historical value | Current value | Where the knob lives now | Expected effect if the historical value is re-applied on current `main` | Merge hazard / overlap risk | Disposition |
| --- | --- | --- | --- | --- | --- | --- |
| Fast chunking | visible replay shape `chunk_max_chars=4000`, `overlap_turns=2` | clean reference `chunk_max_chars=2000`, `overlap_turns=0` | `orchestrator/eval/longmemeval_fast.py` (`DEFAULT_CHUNK_MAX_CHARS`, `DEFAULT_OVERLAP_TURNS`, CLI args, runner fields) | **Not a portable lift.** The only clean current-main replay of the visible historical shape lands at `53.6%`, which is **`-14.2` points** below the clean `67.8%` reference. The preserved `81.1` bundle's chunk stats do not even match that replay. | **High** — overlaps with `TOP_K_MEMORIES` and with the artifact's mixed provenance. | Skip / no-op |
| Retrieval depth | `TOP_K_MEMORIES=5` | clean reference `TOP_K_MEMORIES=10` | `tests/longmemeval/evaluate.py` (`TOP_K_MEMORIES`, `retrieve_user_memories()`, `evaluate_single()`) | **Negative if re-applied.** Historical-style runs return `~5` memories (`4.81` in the preserved bundle, `4.996` in the clean replay), while the clean reference returns `9.996`. The historical value is a lower ceiling, not a residual advantage. | **High** — saved evidence changes chunking, top-k, and judge/accounting context together. | Skip / no-op |
| Embedding route parity | `embedding_document_model=voyage-4-large`, `embedding_query_model=voyage-4-lite` | same values on current `main` | `orchestrator/config.py` (`embedding_document_model`, `embedding_query_model`, `embedding_dimensions`) | **None.** The historical fast artifact already used the same general Voyage embedding route, so there is no historical->current advantage to port. | **Medium** — changing this would affect both canonical and fast lanes, but parity means there is no evidenced lift here. | Skip / no-op |
| Retrieval ranking defaults | no preserved benchmark-specific historical delta | current `INITIAL_VECTOR_CANDIDATES=10`, `MIN_FINAL_SCORE=0.15`, hybrid weights `0.5/0.3/0.2` | `orchestrator/memory/retrieval.py` | **Unproven.** Current `main` exposes portable ranking knobs, but the archaeology did not preserve any alternate historical values and `81_1_DIFF.md` found no benchmark-only config switch explaining `81.1`. | **High** — cross-cutting retrieval change that would overlap with chunking, top-k, and answer/judge variance. | Skip / unproven |
| Dedup thresholds | not applicable to the fast artifact | current `0.90 / 0.82 / 0.65` | `orchestrator/config.py` (`dedup_merge_threshold`, `dedup_supersede_threshold`, `dedup_supersede_same_slot_threshold`) | **None for this artifact.** The `81.1%` bundle came from the fast direct-insert lane, which bypasses extraction/dedup. | **High** for canonical work, but irrelevant to this fast-artifact subtraction task. | Skip / not applicable |

## Bottom line for follow-up work

- There is **no evidence-backed historical setting that should be promoted as a clean portable advantage** on current `main`.
- The only knobs with preserved historical values (`4000/2` chunking and `k=5`) already lose to the current clean reference when replayed honestly.
- Any future improvement work should start from the clean current baseline, not from the historical `81.1%` artifact.

## Machine-checkable summary

```json
{
  "headline_gap_vs_clean": 0.133,
  "historical_weighted_over_strict": 0.189,
  "historical_strict_vs_current_clean": -0.056,
  "residual_portable_advantages": [],
  "excluded_non_portable_explanations": [
    "judge_drift_and_bundle_leniency",
    "contamination_vectors",
    "split_provenance",
    "historical_fixture_quirks"
  ],
  "portable_candidate_review": [
    {
      "name": "fast_chunking",
      "historical_value": {
        "chunk_max_chars": 4000,
        "overlap_turns": 2
      },
      "current_value": {
        "chunk_max_chars": 2000,
        "overlap_turns": 0
      },
      "status": "skip_noop",
      "expected_lift_if_ported_today": "negative_or_unproven",
      "merge_hazard": "high_overlap_with_top_k_and_artifact_provenance"
    },
    {
      "name": "top_k_memories",
      "historical_value": 5,
      "current_value": 10,
      "status": "skip_noop",
      "expected_lift_if_ported_today": "negative",
      "merge_hazard": "high_overlap_with_chunking_and_judge_bundle"
    },
    {
      "name": "embedding_models",
      "historical_value": {
        "embedding_document_model": "voyage-4-large",
        "embedding_query_model": "voyage-4-lite"
      },
      "current_value": {
        "embedding_document_model": "voyage-4-large",
        "embedding_query_model": "voyage-4-lite"
      },
      "status": "skip_noop",
      "expected_lift_if_ported_today": "none",
      "merge_hazard": "medium_cross_lane_if_changed"
    },
    {
      "name": "retrieval_ranking_defaults",
      "historical_value": "no_preserved_benchmark_specific_delta",
      "current_value": {
        "initial_vector_candidates": 10,
        "min_final_score": 0.15,
        "hybrid_vector_weight": 0.5,
        "hybrid_bm25_weight": 0.3,
        "hybrid_recency_confidence_weight": 0.2
      },
      "status": "skip_unproven",
      "expected_lift_if_ported_today": "unknown",
      "merge_hazard": "high_cross_cutting_overlap"
    },
    {
      "name": "dedup_thresholds",
      "historical_value": "fast_artifact_bypassed_dedup",
      "current_value": {
        "dedup_merge_threshold": 0.9,
        "dedup_supersede_threshold": 0.82,
        "dedup_supersede_same_slot_threshold": 0.65
      },
      "status": "skip_not_applicable",
      "expected_lift_if_ported_today": "none",
      "merge_hazard": "high_for_canonical_lane_but_irrelevant_here"
    }
  ]
}
```
