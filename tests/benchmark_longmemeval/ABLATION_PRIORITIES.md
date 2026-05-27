# Dev-Subset Ablation Priorities

## Scope

- Baseline evidence comes only from the locked `run1` + `run2` dev-subset failure union in `tests/benchmark_results/dev_subset_baseline/taxonomy.md` (**39** failures total).
- This is a **current-main, single-variable ablation queue**. It is not a restore-the-old-81.1%-artifact plan.
- `PORTABLE_ADVANTAGES.md` already concluded that there are **no clean portable historical advantages** left after subtracting judge drift, contamination, split provenance, and historical-only fixture quirks.
- **No model swaps** are in scope.
- Promotion gate for this bounded queue: if a candidate's primary target cell has **fewer than 5** locked failures, keep it documented but **blocked from first-wave promotion**.

## Ranking logic

1. Prefer the densest locked cells first (`retrieval-miss × single-session-user` = 6, `retrieval-miss × multi-session` = 6, `retrieval-miss × temporal-reasoning` = 5).
2. Prefer live current-main knobs or narrowly isolated logic changes over speculative or artifact-specific restores.
3. Defer candidates that either already exist in code (`entity` expansion), hit weak target cells (`<5`), or collide heavily with higher-priority sweeps.

## Ranked candidates

### 1. Retrieval ceiling sweep (`TOP_K_MEMORIES`, not `MAX_RETURNED_MEMORIES`)

- **Target failure cell**: `retrieval-miss × multi-session` (**6** / 39; representative IDs: `ba358f49`, `6cb6f249`, `2318644b`)
- **Why this cell**: the locked failures are fully extracted yet still answer with “I don't have information” language on questions that often need more than one memory to survive truncation.
- **Expected direction**: positive
- **Expected magnitude**: medium
- **Implementation cost**: low
- **Overlap notes**: high overlap with `INITIAL_VECTOR_CANDIDATES` and `MIN_FINAL_SCORE`; run early to learn whether the right memories are already ranked but cut off too soon.
- **Portability / scope note**: current-main only. `orchestrator/eval/runner.py` records `MAX_RETURNED_MEMORIES` as inactive for LongMemEval, so the live benchmark ceiling is the caller-side `TOP_K_MEMORIES` contract in `tests/longmemeval/evaluate.py`, not a historical `k=5` replay.
- **Promotion status**: approved

### 2. `MIN_FINAL_SCORE` threshold sweep

- **Target failure cell**: `retrieval-miss × single-session-user` (**6** / 39; representative IDs: `8550ddae`, `86f00804`, `19b5f2b3`)
- **Why this cell**: these rows fully extract the answer sessions and then still say the needed fact is unavailable, which is consistent with useful candidates being filtered away after scoring rather than absent from the corpus.
- **Expected direction**: positive
- **Expected magnitude**: medium
- **Implementation cost**: low
- **Overlap notes**: high overlap with retrieval-ceiling and weight sweeps; only informative if the candidate pool already contains the right memory.
- **Portability / scope note**: current-main live knob in `orchestrator/memory/retrieval.py`. This is a forward-looking threshold test, not a historical restore candidate.
- **Promotion status**: approved

### 3. `INITIAL_VECTOR_CANDIDATES` sweep

- **Target failure cell**: `retrieval-miss × temporal-reasoning` (**5** / 39; representative IDs: `8c18457d`, `6613b389`, `gpt4_af6db32f`)
- **Why this cell**: temporal questions often hinge on sparse date-bearing memories; if those memories never enter the first candidate pool, later scoring and thresholds cannot rescue them.
- **Expected direction**: positive
- **Expected magnitude**: small-medium
- **Implementation cost**: low
- **Overlap notes**: high overlap with retrieval ceiling and weight sweeps; expand the pool before concluding that later ranking is the only problem.
- **Portability / scope note**: current-main live knob in `orchestrator/memory/retrieval.py` (`INITIAL_VECTOR_CANDIDATES = 10`). No historical artifact state needs to be replayed.
- **Promotion status**: approved

### 4. Hybrid ranking-weight sweep

- **Target failure cell**: `retrieval-miss × multi-session` (**6** / 39; representative IDs: `ba358f49`, `6cb6f249`, `2318644b`)
- **Why this cell**: the densest retrieval-heavy cell is exactly where ranking tradeoffs matter most: multiple partially relevant memories exist, but the final mix of vector, BM25, and recency-confidence may be surfacing the wrong subset.
- **Expected direction**: positive
- **Expected magnitude**: small-medium
- **Implementation cost**: low-medium
- **Overlap notes**: very high overlap with `MIN_FINAL_SCORE`, temporal filtering, and candidate-pool size; run only after ceiling and threshold sweeps say ranking order is still the limiting factor.
- **Portability / scope note**: current-main live knobs in `orchestrator/memory/retrieval.py` (`HYBRID_VECTOR_WEIGHT`, `HYBRID_BM25_WEIGHT`, `HYBRID_RECENCY_CONFIDENCE_WEIGHT`). This stays within the no-model-swap rule.
- **Promotion status**: approved

### 5. Conservative temporal filter integration

- **Target failure cell**: `retrieval-miss × temporal-reasoning` (**5** / 39; representative IDs: `8c18457d`, `6613b389`, `gpt4_af6db32f`)
- **Why this cell**: temporal-reasoning is the third dense locked cell, and the current retrieval path has recency boosting but no explicit query-time temporal gating against `valid_from` / `valid_to`.
- **Expected direction**: positive
- **Expected magnitude**: medium on the temporal cell, neutral elsewhere if detection stays conservative
- **Implementation cost**: medium
- **Overlap notes**: overlaps with ranking-weight changes and dedup freshness work; keep it isolated behind an explicit temporal-language detector so non-temporal cells remain comparable.
- **Portability / scope note**: current-main feature gap, not a historical knob restore. It is portable only if the detector is rule-based and query-scoped rather than another model swap.
- **Promotion status**: approved

### 6. Query-side entity alias expansion audit / toggle

- **Target failure cell**: `retrieval-miss × single-session-user` (**6** / 39; representative IDs: `8550ddae`, `86f00804`, `19b5f2b3`)
- **Why this cell**: single-session user facts are the cleanest named-entity / alias lookup surface in the locked failures, so this is where an alias miss would show up first.
- **Expected direction**: positive if a distinct missing behavior exists, otherwise none
- **Expected magnitude**: small
- **Implementation cost**: medium
- **Overlap notes**: overlaps with BM25 weighting and broader candidate-pool changes; do not implement unless the audit proves a behavior gap that those earlier sweeps cannot already explain.
- **Portability / scope note**: audit-first only. `orchestrator/memory/retrieval.py` already calls `_get_entity_expanded_candidates()`, so there is no approved code change here until a distinct missing-path finding exists.
- **Promotion status**: blocked_audit_first

### 7. Config-backed dedup threshold sensitivity

- **Target failure cell**: `generation-error × knowledge-update` (**2** / 39; representative IDs: `852ce960`, `3ba21379`)
- **Why this cell**: the locked wrong-answer rows are update-sensitive, and the current canonical lane has separate supersede-closeout evidence outside this report, so dedup freshness is a plausible but not yet dominant explanation.
- **Expected direction**: positive
- **Expected magnitude**: small-medium
- **Implementation cost**: low-medium
- **Overlap notes**: overlaps with temporal filtering and ranking because dedup changes which memories exist at retrieval time; memory-count inflation is a required side metric.
- **Portability / scope note**: current-main only through `orchestrator/config.py` and live dedup accessors. `PORTABLE_ADVANTAGES.md` already says dedup was not a clean fast-artifact explanation, so this remains canonical-lane work only.
- **Promotion status**: blocked_insufficient_target_cell

## Explicit exclusions from the first ranked queue

- **Historical fast chunking (`4000/2`)** — excluded. `PORTABLE_ADVANTAGES.md` shows the honest replay is worse than the current clean reference and the preserved artifact has mixed provenance.
- **Historical `k=5` restore** — excluded. The preserved historical retrieval depth points the wrong way under current `main`; the approved portable ceiling test is a forward-looking sweep away from the current benchmark ceiling, not back toward the artifact.
- **Literal `MAX_RETURNED_MEMORIES` sweep** — excluded as a named candidate because LongMemEval currently uses the caller-side `TOP_K_MEMORIES` limit instead.
- **Embedding-route changes** — excluded. Historical and current embedding-route settings are already at parity, and broader embedding changes would violate the “no change justified solely because the old code did it” rule.
- **Answer-model, judge-model, or other model swaps** — excluded by task contract.
- **Standalone portable-advantages replay task** — excluded. Phase 2 found no clean residual historical survivors to promote.
- **Abstention prompt hardening** — excluded from the first bounded queue. The locked abstention surface is too small (`abstention` total = 2; `generation-error × temporal-reasoning` = 3) to outrank the three retrieval-heavy cells with 5-6 cases each.
- **Extraction-miss-only sweeps on the current retrieval/dedup surface** — excluded from this first list. The densest extraction cell (`extraction-miss × single-session-assistant` = 4) is below the promotion gate, and the required tool review for this task focused on retrieval/dedup levers rather than reopening the extraction barrier work.

## Machine-checkable summary

```json
{
  "baseline_failure_total": 39,
  "promotion_gate_min_target_cell_count": 5,
  "portability_constraints": {
    "no_model_swaps": true,
    "no_clean_portable_historical_advantages": true,
    "historical_fast_chunking_status": "excluded_negative_or_nonportable",
    "historical_top_k_restore_status": "excluded_negative",
    "embedding_route_changes_status": "excluded_no_historical_delta"
  },
  "excluded_from_ranked_queue": [
    {
      "name": "historical_fast_chunking_4000_2",
      "reason": "negative_or_nonportable"
    },
    {
      "name": "historical_top_k_5_restore",
      "reason": "negative_on_current_main"
    },
    {
      "name": "MAX_RETURNED_MEMORIES_literal_sweep",
      "reason": "inactive_in_longmemeval_use_TOP_K_MEMORIES_instead"
    },
    {
      "name": "embedding_route_changes",
      "reason": "already_at_parity_and_not_a_clean_historical_lift"
    },
    {
      "name": "answer_or_judge_model_swaps",
      "reason": "forbidden_by_task_contract"
    },
    {
      "name": "portable_advantages_phase2_replay",
      "reason": "no_residual_portable_historical_advantages"
    },
    {
      "name": "abstention_prompt_hardening",
      "reason": "target_cells_below_first_wave_density_gate"
    },
    {
      "name": "extraction_miss_only_retrieval_or_dedup_sweeps",
      "reason": "below_density_gate_and_not_the_reviewed_knob_surface"
    }
  ],
  "ranked_candidates": [
    {
      "rank": 1,
      "name": "top_k_memories_sweep",
      "knob_authority": "tests.longmemeval.evaluate.TOP_K_MEMORIES",
      "target_failure_cell": {
        "stage": "retrieval-miss",
        "category": "multi-session",
        "count": 6,
        "representative_ids": [
          "ba358f49",
          "6cb6f249",
          "2318644b"
        ]
      },
      "expected_direction": "positive",
      "expected_magnitude": "medium",
      "implementation_cost": "low",
      "overlap_notes": "High overlap with INITIAL_VECTOR_CANDIDATES and MIN_FINAL_SCORE; use it to test whether the right memories are already ranked but truncated.",
      "taxonomy_basis": "The densest multi-session failures are fully extracted retrieval misses that still answer as if supporting facts are unavailable.",
      "portability_basis": "Current-main forward-only benchmark call-limit knob; replaces the inactive MAX_RETURNED_MEMORIES idea and does not restore a historical artifact setting.",
      "promotion_status": "approved"
    },
    {
      "rank": 2,
      "name": "min_final_score_sweep",
      "knob_authority": "orchestrator.memory.retrieval.MIN_FINAL_SCORE",
      "target_failure_cell": {
        "stage": "retrieval-miss",
        "category": "single-session-user",
        "count": 6,
        "representative_ids": [
          "8550ddae",
          "86f00804",
          "19b5f2b3"
        ]
      },
      "expected_direction": "positive",
      "expected_magnitude": "medium",
      "implementation_cost": "low",
      "overlap_notes": "High overlap with top-k and ranking-weight sweeps; only useful when good candidates already exist in the pool.",
      "taxonomy_basis": "The locked rows fully extract answer sessions and then still respond with missing-information language, matching a threshold/filter hypothesis.",
      "portability_basis": "Live current-main retrieval threshold with no historical-restore dependency.",
      "promotion_status": "approved"
    },
    {
      "rank": 3,
      "name": "initial_vector_candidates_sweep",
      "knob_authority": "orchestrator.memory.retrieval.INITIAL_VECTOR_CANDIDATES",
      "target_failure_cell": {
        "stage": "retrieval-miss",
        "category": "temporal-reasoning",
        "count": 5,
        "representative_ids": [
          "8c18457d",
          "6613b389",
          "gpt4_af6db32f"
        ]
      },
      "expected_direction": "positive",
      "expected_magnitude": "small-medium",
      "implementation_cost": "low",
      "overlap_notes": "High overlap with top-k and ranking weights; increase the pool before concluding that later ranking alone is broken.",
      "taxonomy_basis": "Temporal rows repeatedly miss specific dated facts after full extraction, which is consistent with sparse date-bearing memories never entering the first candidate set.",
      "portability_basis": "Live current-main retrieval knob in retrieval.py; no historical artifact replay required.",
      "promotion_status": "approved"
    },
    {
      "rank": 4,
      "name": "hybrid_ranking_weight_sweep",
      "knob_authority": "orchestrator.memory.retrieval.HYBRID_*_WEIGHT",
      "target_failure_cell": {
        "stage": "retrieval-miss",
        "category": "multi-session",
        "count": 6,
        "representative_ids": [
          "ba358f49",
          "6cb6f249",
          "2318644b"
        ]
      },
      "expected_direction": "positive",
      "expected_magnitude": "small-medium",
      "implementation_cost": "low-medium",
      "overlap_notes": "Very high overlap with candidate-pool size, threshold, and temporal filtering; hold until simpler ceiling/threshold checks are exhausted.",
      "taxonomy_basis": "The densest retrieval cell is exactly where ranking-order tradeoffs between vector, lexical, and freshness signals can hide the right memory under the wrong mix.",
      "portability_basis": "Live current-main hybrid-score weights, fully inside the no-model-swap rule and independent of the rejected historical artifact explanations.",
      "promotion_status": "approved"
    },
    {
      "rank": 5,
      "name": "temporal_filter_integration",
      "knob_authority": "query_scoped_temporal_filter_against_valid_from_valid_to",
      "target_failure_cell": {
        "stage": "retrieval-miss",
        "category": "temporal-reasoning",
        "count": 5,
        "representative_ids": [
          "8c18457d",
          "6613b389",
          "gpt4_af6db32f"
        ]
      },
      "expected_direction": "positive",
      "expected_magnitude": "medium",
      "implementation_cost": "medium",
      "overlap_notes": "Overlaps with ranking-weight and freshness-sensitive dedup work; keep it isolated behind explicit temporal-language detection.",
      "taxonomy_basis": "Temporal-reasoning is a dense locked cell and the current retrieval path lacks explicit query-time temporal gating even though the failures ask for dated/order-aware facts.",
      "portability_basis": "Current-main feature gap that can be implemented as a deterministic retrieval change, not a model swap or artifact restore.",
      "promotion_status": "approved"
    },
    {
      "rank": 6,
      "name": "entity_alias_audit_toggle",
      "knob_authority": "orchestrator.memory.retrieval._get_entity_expanded_candidates",
      "target_failure_cell": {
        "stage": "retrieval-miss",
        "category": "single-session-user",
        "count": 6,
        "representative_ids": [
          "8550ddae",
          "86f00804",
          "19b5f2b3"
        ]
      },
      "expected_direction": "positive_if_distinct_gap_exists",
      "expected_magnitude": "small",
      "implementation_cost": "medium",
      "overlap_notes": "Overlaps with BM25 weighting and broader candidate-pool tuning; do not implement unless the audit proves a missing path that earlier sweeps cannot explain.",
      "taxonomy_basis": "Single-session user facts are the cleanest named-entity lookup cell in the locked failures, so this is where alias leverage would surface first.",
      "portability_basis": "Audit-first only because current retrieval already expands entity-linked candidates; no clean code-change candidate exists until the audit finds one.",
      "promotion_status": "blocked_audit_first"
    },
    {
      "rank": 7,
      "name": "dedup_threshold_sensitivity",
      "knob_authority": "orchestrator.config.dedup_*_threshold",
      "target_failure_cell": {
        "stage": "generation-error",
        "category": "knowledge-update",
        "count": 2,
        "representative_ids": [
          "852ce960",
          "3ba21379"
        ]
      },
      "expected_direction": "positive",
      "expected_magnitude": "small-medium",
      "implementation_cost": "low-medium",
      "overlap_notes": "Overlaps with temporal freshness work and changes corpus size; track memory-count inflation alongside score deltas.",
      "taxonomy_basis": "The primary target cell is small but update-sensitive, and the broader knowledge-update category totals 6 locked failures across stages.",
      "portability_basis": "Current-main config-backed dedup thresholds only; PORTABLE_ADVANTAGES already rules out dedup as a clean historical fast-artifact explanation.",
      "promotion_status": "blocked_insufficient_target_cell"
    }
  ]
}
```
