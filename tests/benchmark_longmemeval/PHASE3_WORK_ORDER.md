# Phase 3 Work Order

Date: 2026-04-19
Status: Oracle-reviewed queue only. This file authorizes ranking and gating; it does **not** authorize Phase 3 sweep execution.

## Inputs reviewed

- `tests/benchmark_results/dev_subset_baseline/taxonomy.md`
- `tests/benchmark_longmemeval/ABLATION_PRIORITIES.md`
- `tests/benchmark_longmemeval/JUDGE_DRIFT.md`
- `tests/benchmark_longmemeval/CONTAMINATION_ANALYSIS.md`
- `tests/benchmark_longmemeval/PORTABLE_ADVANTAGES.md`
- `tests/benchmark_longmemeval/fixtures/dev_subset_coverage.md`

## Non-negotiable guardrails

1. **Subset-veto rule**: first-wave promotion requires **at least 5 locked failures in the exact target failure cell**. Primary-cell fixture floors are helpful context, but they do not override the target-cell gate.
2. **No-model-swap rule**: answer-model, judge-model, and any other model swap stay out of Phase 3.
3. **History is veto-only here**: `PORTABLE_ADVANTAGES.md` found no clean portable historical survivors, so history can block or constrain a candidate but cannot auto-promote one.
4. **Carry forward the live-knob correction**: LongMemEval retrieval ceiling work must target caller-side `TOP_K_MEMORIES`; literal `MAX_RETURNED_MEMORIES` is inactive on the live benchmark path.

## Oracle reconciliation — contradictions that stay visible

| Conflict | Taxonomy signal | History / forensic signal | Resolution |
| --- | --- | --- | --- |
| `retrieval_ceiling_direction_conflict` | Dense retrieval-miss cells argue for testing a higher live retrieval ceiling on current main. | The preserved historical k=5 shape is negative on current main, and literal MAX_RETURNED_MEMORIES is inactive for LongMemEval. | Approve only a forward caller-side TOP_K_MEMORIES sweep and keep historical k=5 / MAX_RETURNED_MEMORIES restores blocked. |
| `ranking_knob_without_historical_delta` | Threshold, pool-size, and hybrid-ordering changes line up with the three dense retrieval-heavy cells. | The archaeology preserved no clean benchmark-specific historical values for these ranking defaults, and judge drift makes artifact-level wins easy to over-credit. | Keep these as current-main, single-variable experiments only; do not describe them as restored historical behavior. |
| `dedup_freshness_conflict` | Knowledge-update wrong-answer rows leave room for a freshness or supersession hypothesis. | The 81.1 fast artifact bypassed dedup, the target cell has only 2 locked failures, and retrieval snapshots are missing. | Document dedup sensitivity but block promotion until the locked evidence surface grows or the forensics get richer. |
| `model_swap_conflict` | Some artifact paths could improve headline score by moving answer or judge behavior rather than retrieval behavior. | JUDGE_DRIFT shows two-sided artifact drift, CONTAMINATION_ANALYSIS marks the 81.1 bundle provenance-contaminated, and the task contract forbids model swaps. | Keep all answer/judge/model swaps out of Phase 3 and restrict approved work to retrieval-side current-main levers. |

## Ordered queue at a glance

| rank | candidate | status | estimated leverage | dependency |
| ---: | --- | --- | --- | --- |
| 1 | `top_k_memories_sweep` | `approved` | medium | none; run first so later threshold or ranking results are not misread while the active benchmark ceiling is still unknown |
| 2 | `min_final_score_sweep` | `approved` | medium | after #1; interpret only once the active retrieval ceiling is no longer the obvious limiter |
| 3 | `initial_vector_candidates_sweep` | `approved` | small-medium | after #1; run before #4 and #5 so temporal misses are not blamed on ranking or filtering before the candidate pool is widened |
| 4 | `hybrid_ranking_weight_sweep` | `approved` | small-medium | after #1 through #3; only run once ceiling, threshold, and pool-size sweeps say ordering is still the likely limiter |
| 5 | `temporal_filter_integration` | `approved` | medium | after #1 through #4; only promote if generic retrieval tuning still leaves the temporal cell stubbornly behind |
| 6 | `entity_alias_audit_toggle` | `blocked_audit_first` | small | after #1 through #5 and only if an audit proves a distinct missing alias path not already covered by current retrieval behavior |
| 7 | `dedup_threshold_sensitivity` | `blocked_insufficient_target_cell` | small-medium | not before a richer locked target cell or better retrieval snapshots exist; current evidence is too thin for first-wave promotion |

## Ranked current-main work order

### 1. `top_k_memories_sweep`

- **Status**: `approved`
- **Target failure cell**: `retrieval-miss × multi-session` (6 locked failures; representative IDs: `ba358f49`, `6cb6f249`, `2318644b`)
- **Estimated leverage**: medium
- **Dependency**: none; run first so later threshold or ranking results are not misread while the active benchmark ceiling is still unknown
- **Evidence basis**: The densest locked retrieval cell is fully extracted yet still answers as if facts are unavailable, while PORTABLE_ADVANTAGES rules out the historical k=5 replay as a portable lift.
- **Contradiction to keep visible**: Taxonomy says a larger retrieval ceiling may help, but history says the preserved k=5 shape was worse and the literal MAX_RETURNED_MEMORIES constant is inactive on the live LongMemEval path.
- **Promotion rationale**: This is the highest-leverage live knob on a 6-case cell and the cleanest way to replace the stale MAX_RETURNED_MEMORIES plan wording with the caller-side TOP_K_MEMORIES authority.
- **Blocking rationale**: Keep it blocked if anyone tries to execute it as a historical k=5 restore or as a MAX_RETURNED_MEMORIES-only code change instead of a caller-side TOP_K_MEMORIES sweep.

### 2. `min_final_score_sweep`

- **Status**: `approved`
- **Target failure cell**: `retrieval-miss × single-session-user` (6 locked failures; representative IDs: `8550ddae`, `86f00804`, `19b5f2b3`)
- **Estimated leverage**: medium
- **Dependency**: after #1; interpret only once the active retrieval ceiling is no longer the obvious limiter
- **Evidence basis**: These 6 locked rows fully extract their answer sessions and still say the fact is unavailable, which matches a score-threshold hypothesis better than a historical artifact replay.
- **Contradiction to keep visible**: Taxonomy points toward score filtering, but history preserved no benchmark-specific threshold delta, so this stays a current-main experiment rather than a restoration claim.
- **Promotion rationale**: Low-cost, live retrieval threshold work on a dense target cell makes sense once truncation risk from #1 has been bounded.
- **Blocking rationale**: Do not promote any threshold result as a historical explanation; the history lane contributes only a veto against artifact replay, not a positive threshold target.

### 3. `initial_vector_candidates_sweep`

- **Status**: `approved`
- **Target failure cell**: `retrieval-miss × temporal-reasoning` (5 locked failures; representative IDs: `8c18457d`, `6613b389`, `gpt4_af6db32f`)
- **Estimated leverage**: small-medium
- **Dependency**: after #1; run before #4 and #5 so temporal misses are not blamed on ranking or filtering before the candidate pool is widened
- **Evidence basis**: Temporal questions repeatedly miss sparse dated facts after full extraction, and no clean historical pool-size setting survives subtraction, so the live candidate-pool knob is the honest test surface.
- **Contradiction to keep visible**: Taxonomy suggests more dated candidates may be needed, but the historical 4000/2 + k=5 replay already loses on current main, so any benefit here must come from forward-looking pool expansion rather than artifact recovery.
- **Promotion rationale**: This is the lowest-cost way to test whether temporal misses fail before ranking ever sees the right dated memories.
- **Blocking rationale**: Block any attempt to fold chunking or historical retrieval-depth restores into this sweep; those are separately vetoed nonportable candidates.

### 4. `hybrid_ranking_weight_sweep`

- **Status**: `approved`
- **Target failure cell**: `retrieval-miss × multi-session` (6 locked failures; representative IDs: `ba358f49`, `6cb6f249`, `2318644b`)
- **Estimated leverage**: small-medium
- **Dependency**: after #1 through #3; only run once ceiling, threshold, and pool-size sweeps say ordering is still the likely limiter
- **Evidence basis**: The densest retrieval-heavy cell is where vector, lexical, and freshness tradeoffs can surface the wrong subset, but history preserved no clean ranking-weight delta to restore.
- **Contradiction to keep visible**: Taxonomy says ordering could matter, while history offers no portable ranking default and artifact-level judge drift makes score-only wins easy to overread.
- **Promotion rationale**: Still approved because it operates on a dense 6-case cell and remains inside current-main retrieval logic after simpler knobs are exhausted.
- **Blocking rationale**: Do not run this before the simpler ceiling, threshold, and pool-size sweeps; otherwise overlap makes the result hard to attribute.

### 5. `temporal_filter_integration`

- **Status**: `approved`
- **Target failure cell**: `retrieval-miss × temporal-reasoning` (5 locked failures; representative IDs: `8c18457d`, `6613b389`, `gpt4_af6db32f`)
- **Estimated leverage**: medium
- **Dependency**: after #1 through #4; only promote if generic retrieval tuning still leaves the temporal cell stubbornly behind
- **Evidence basis**: Temporal-reasoning is the third dense locked cell and the current retrieval path has recency boosting but no explicit query-time temporal gating, while history preserves no portable temporal feature to replay.
- **Contradiction to keep visible**: Taxonomy gives a real temporal signal, but history gives silence rather than support, so this must stay deterministic and query-scoped rather than drifting into a model or prompt swap.
- **Promotion rationale**: Approved because it directly addresses a 5-case dense cell with a current-main feature gap and does not require a forbidden model change.
- **Blocking rationale**: Keep it blocked if the design relies on model-side temporal inference or if earlier generic retrieval sweeps already close the temporal gap.

### 6. `entity_alias_audit_toggle`

- **Status**: `blocked_audit_first`
- **Target failure cell**: `retrieval-miss × single-session-user` (6 locked failures; representative IDs: `8550ddae`, `86f00804`, `19b5f2b3`)
- **Estimated leverage**: small
- **Dependency**: after #1 through #5 and only if an audit proves a distinct missing alias path not already covered by current retrieval behavior
- **Evidence basis**: Single-session user facts remain the cleanest alias-sensitive cell, but the current retrieval path already calls _get_entity_expanded_candidates(), so there is no demonstrated missing behavior to ablate yet.
- **Contradiction to keep visible**: Coverage is good enough, but the implementation surface already exists, so ranking pressure alone is not sufficient evidence for promotion.
- **Promotion rationale**: Promote only if the audit finds a real behavior gap that earlier retrieval sweeps cannot explain.
- **Blocking rationale**: Blocked because this is currently an audit question, not a code-change hypothesis; implementing it now risks duplicating behavior that already exists.

### 7. `dedup_threshold_sensitivity`

- **Status**: `blocked_insufficient_target_cell`
- **Target failure cell**: `generation-error × knowledge-update` (2 locked failures; representative IDs: `852ce960`, `3ba21379`)
- **Estimated leverage**: small-medium
- **Dependency**: not before a richer locked target cell or better retrieval snapshots exist; current evidence is too thin for first-wave promotion
- **Evidence basis**: Knowledge-update wrong-answer rows are freshness-sensitive, but the exact target cell has only 2 locked failures and the fast historical artifact bypassed dedup entirely.
- **Contradiction to keep visible**: Taxonomy hints at stale-memory risk, but history says dedup was not part of the 81.1 artifact path and the locked evidence lacks retrieval snapshots to show whether the right memory was seen or merely absent.
- **Promotion rationale**: Promote only if a later locked subset pushes the target cell to at least 5 or if richer retrieval evidence removes the ambiguity.
- **Blocking rationale**: Blocked by the user's subset-veto rule: 2 locked failures is below the 5-case minimum for first-wave promotion.

## Additional coverage vetoes required by the subset-veto rule

These plain-language coverage vetoes are **abstention prompt hardening** and **extraction-miss-only sweeps**: both stay blocked because their locked target surfaces remain below the 5-case gate.

| Candidate | Coverage reference | Status | Why blocked |
| --- | --- | --- | --- |
| `abstention_prompt_hardening` | `abstention` total = 2; nearby `generation-error × temporal-reasoning` = 3 | `blocked_insufficient_target_cell` | Locked abstention coverage is too small for first-wave promotion. |
| `extraction_miss_only_retrieval_or_dedup_sweeps` | `extraction-miss × single-session-assistant` = 4 | `blocked_insufficient_target_cell` | The densest extraction-only cell is still below the 5-case gate. |

## History-driven vetoes that must stay out of Phase 3 promotion

| Candidate | Status | Why vetoed |
| --- | --- | --- |
| `historical_fast_chunking_4000_2` | `blocked_nonportable_or_negative` | The only clean current-main replay of the visible 4000/2 shape loses to the clean reference, and the preserved 81.1 bundle does not even preserve the same chunk geometry. |
| `historical_top_k_5_restore` | `blocked_nonportable_or_negative` | Historical k=5 is a lower ceiling than the current clean baseline and points the wrong way under current main. |
| `literal_MAX_RETURNED_MEMORIES_sweep` | `blocked_inactive_knob` | LongMemEval uses the caller-side TOP_K_MEMORIES limit, so a literal MAX_RETURNED_MEMORIES sweep is inactive on the live benchmark path. |
| `embedding_route_changes` | `blocked_no_historical_delta` | Historical and current embedding routes are already at parity, so there is no clean historical delta to port. |
| `answer_or_judge_model_swaps` | `blocked_no_model_swap_rule` | Judge drift is two-sided, the artifact bundle is provenance-contaminated, and the task contract forbids model swaps. |
| `portable_advantages_phase2_replay` | `blocked_no_residual_portable_advantage` | PORTABLE_ADVANTAGES concluded that no clean portable historical advantages survive subtraction. |

## Bottom line for execution planning

- **Approved first-wave queue**: `top_k_memories_sweep`, `min_final_score_sweep`, `initial_vector_candidates_sweep`, `hybrid_ranking_weight_sweep`, then `temporal_filter_integration`.
- **Blocked but still tracked**: `entity_alias_audit_toggle` stays audit-first; `dedup_threshold_sensitivity` stays blocked because the target cell is only 2 locked cases.
- **Do not reopen historical restores**: `historical_fast_chunking_4000_2`, `historical_top_k_5_restore`, literal `MAX_RETURNED_MEMORIES`, embedding-route changes, and any answer/judge/model swap remain vetoed.

## Machine-checkable summary

```json
{
  "source_reports": [
    "tests/benchmark_results/dev_subset_baseline/taxonomy.md",
    "tests/benchmark_longmemeval/ABLATION_PRIORITIES.md",
    "tests/benchmark_longmemeval/JUDGE_DRIFT.md",
    "tests/benchmark_longmemeval/CONTAMINATION_ANALYSIS.md",
    "tests/benchmark_longmemeval/PORTABLE_ADVANTAGES.md",
    "tests/benchmark_longmemeval/fixtures/dev_subset_coverage.md"
  ],
  "guardrails": {
    "subset_veto_rule_preserved": true,
    "subset_veto_min_locked_cases": 5,
    "dev_subset_primary_cell_floor": 5,
    "no_model_swaps": true,
    "no_residual_portable_historical_advantages": true,
    "active_retrieval_ceiling_authority": "tests.longmemeval.evaluate.TOP_K_MEMORIES",
    "inactive_literal_max_returned_memories": true
  },
  "ordered_candidates": [
    {
      "rank": 1,
      "name": "top_k_memories_sweep",
      "status": "approved",
      "coverage_gate": "pass",
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
      "estimated_leverage": "medium",
      "dependency": "none; run first so later threshold or ranking results are not misread while the active benchmark ceiling is still unknown",
      "evidence_sources": [
        "taxonomy",
        "portable_advantages",
        "ablation_priorities"
      ],
      "evidence_basis": "The densest locked retrieval cell is fully extracted yet still answers as if facts are unavailable, while PORTABLE_ADVANTAGES rules out the historical k=5 replay as a portable lift.",
      "contradiction_to_keep_visible": "Taxonomy says a larger retrieval ceiling may help, but history says the preserved k=5 shape was worse and the literal MAX_RETURNED_MEMORIES constant is inactive on the live LongMemEval path.",
      "promotion_rationale": "This is the highest-leverage live knob on a 6-case cell and the cleanest way to replace the stale MAX_RETURNED_MEMORIES plan wording with the caller-side TOP_K_MEMORIES authority.",
      "blocking_rationale": "Keep it blocked if anyone tries to execute it as a historical k=5 restore or as a MAX_RETURNED_MEMORIES-only code change instead of a caller-side TOP_K_MEMORIES sweep."
    },
    {
      "rank": 2,
      "name": "min_final_score_sweep",
      "status": "approved",
      "coverage_gate": "pass",
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
      "estimated_leverage": "medium",
      "dependency": "after #1; interpret only once the active retrieval ceiling is no longer the obvious limiter",
      "evidence_sources": [
        "taxonomy",
        "portable_advantages",
        "ablation_priorities"
      ],
      "evidence_basis": "These 6 locked rows fully extract their answer sessions and still say the fact is unavailable, which matches a score-threshold hypothesis better than a historical artifact replay.",
      "contradiction_to_keep_visible": "Taxonomy points toward score filtering, but history preserved no benchmark-specific threshold delta, so this stays a current-main experiment rather than a restoration claim.",
      "promotion_rationale": "Low-cost, live retrieval threshold work on a dense target cell makes sense once truncation risk from #1 has been bounded.",
      "blocking_rationale": "Do not promote any threshold result as a historical explanation; the history lane contributes only a veto against artifact replay, not a positive threshold target."
    },
    {
      "rank": 3,
      "name": "initial_vector_candidates_sweep",
      "status": "approved",
      "coverage_gate": "pass",
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
      "estimated_leverage": "small-medium",
      "dependency": "after #1; run before #4 and #5 so temporal misses are not blamed on ranking or filtering before the candidate pool is widened",
      "evidence_sources": [
        "taxonomy",
        "portable_advantages",
        "ablation_priorities"
      ],
      "evidence_basis": "Temporal questions repeatedly miss sparse dated facts after full extraction, and no clean historical pool-size setting survives subtraction, so the live candidate-pool knob is the honest test surface.",
      "contradiction_to_keep_visible": "Taxonomy suggests more dated candidates may be needed, but the historical 4000/2 + k=5 replay already loses on current main, so any benefit here must come from forward-looking pool expansion rather than artifact recovery.",
      "promotion_rationale": "This is the lowest-cost way to test whether temporal misses fail before ranking ever sees the right dated memories.",
      "blocking_rationale": "Block any attempt to fold chunking or historical retrieval-depth restores into this sweep; those are separately vetoed nonportable candidates."
    },
    {
      "rank": 4,
      "name": "hybrid_ranking_weight_sweep",
      "status": "approved",
      "coverage_gate": "pass",
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
      "estimated_leverage": "small-medium",
      "dependency": "after #1 through #3; only run once ceiling, threshold, and pool-size sweeps say ordering is still the likely limiter",
      "evidence_sources": [
        "taxonomy",
        "portable_advantages",
        "ablation_priorities"
      ],
      "evidence_basis": "The densest retrieval-heavy cell is where vector, lexical, and freshness tradeoffs can surface the wrong subset, but history preserved no clean ranking-weight delta to restore.",
      "contradiction_to_keep_visible": "Taxonomy says ordering could matter, while history offers no portable ranking default and artifact-level judge drift makes score-only wins easy to overread.",
      "promotion_rationale": "Still approved because it operates on a dense 6-case cell and remains inside current-main retrieval logic after simpler knobs are exhausted.",
      "blocking_rationale": "Do not run this before the simpler ceiling, threshold, and pool-size sweeps; otherwise overlap makes the result hard to attribute."
    },
    {
      "rank": 5,
      "name": "temporal_filter_integration",
      "status": "approved",
      "coverage_gate": "pass",
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
      "estimated_leverage": "medium",
      "dependency": "after #1 through #4; only promote if generic retrieval tuning still leaves the temporal cell stubbornly behind",
      "evidence_sources": [
        "taxonomy",
        "portable_advantages",
        "ablation_priorities"
      ],
      "evidence_basis": "Temporal-reasoning is the third dense locked cell and the current retrieval path has recency boosting but no explicit query-time temporal gating, while history preserves no portable temporal feature to replay.",
      "contradiction_to_keep_visible": "Taxonomy gives a real temporal signal, but history gives silence rather than support, so this must stay deterministic and query-scoped rather than drifting into a model or prompt swap.",
      "promotion_rationale": "Approved because it directly addresses a 5-case dense cell with a current-main feature gap and does not require a forbidden model change.",
      "blocking_rationale": "Keep it blocked if the design relies on model-side temporal inference or if earlier generic retrieval sweeps already close the temporal gap."
    },
    {
      "rank": 6,
      "name": "entity_alias_audit_toggle",
      "status": "blocked_audit_first",
      "coverage_gate": "pass",
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
      "estimated_leverage": "small",
      "dependency": "after #1 through #5 and only if an audit proves a distinct missing alias path not already covered by current retrieval behavior",
      "evidence_sources": [
        "taxonomy",
        "ablation_priorities"
      ],
      "evidence_basis": "Single-session user facts remain the cleanest alias-sensitive cell, but the current retrieval path already calls _get_entity_expanded_candidates(), so there is no demonstrated missing behavior to ablate yet.",
      "contradiction_to_keep_visible": "Coverage is good enough, but the implementation surface already exists, so ranking pressure alone is not sufficient evidence for promotion.",
      "promotion_rationale": "Promote only if the audit finds a real behavior gap that earlier retrieval sweeps cannot explain.",
      "blocking_rationale": "Blocked because this is currently an audit question, not a code-change hypothesis; implementing it now risks duplicating behavior that already exists."
    },
    {
      "rank": 7,
      "name": "dedup_threshold_sensitivity",
      "status": "blocked_insufficient_target_cell",
      "coverage_gate": "blocked_insufficient_target_cell",
      "target_failure_cell": {
        "stage": "generation-error",
        "category": "knowledge-update",
        "count": 2,
        "representative_ids": [
          "852ce960",
          "3ba21379"
        ]
      },
      "estimated_leverage": "small-medium",
      "dependency": "not before a richer locked target cell or better retrieval snapshots exist; current evidence is too thin for first-wave promotion",
      "evidence_sources": [
        "taxonomy",
        "portable_advantages",
        "ablation_priorities"
      ],
      "evidence_basis": "Knowledge-update wrong-answer rows are freshness-sensitive, but the exact target cell has only 2 locked failures and the fast historical artifact bypassed dedup entirely.",
      "contradiction_to_keep_visible": "Taxonomy hints at stale-memory risk, but history says dedup was not part of the 81.1 artifact path and the locked evidence lacks retrieval snapshots to show whether the right memory was seen or merely absent.",
      "promotion_rationale": "Promote only if a later locked subset pushes the target cell to at least 5 or if richer retrieval evidence removes the ambiguity.",
      "blocking_rationale": "Blocked by the user's subset-veto rule: 2 locked failures is below the 5-case minimum for first-wave promotion."
    }
  ],
  "history_vetoes": [
    {
      "name": "historical_fast_chunking_4000_2",
      "status": "blocked_nonportable_or_negative",
      "estimated_leverage": "negative_or_nonportable",
      "dependency": "none; keep excluded from Phase 3 sweeps",
      "evidence_sources": [
        "portable_advantages"
      ],
      "blocking_rationale": "The only clean current-main replay of the visible 4000/2 shape loses to the clean reference, and the preserved 81.1 bundle does not even preserve the same chunk geometry."
    },
    {
      "name": "historical_top_k_5_restore",
      "status": "blocked_nonportable_or_negative",
      "estimated_leverage": "negative",
      "dependency": "none; do not treat it as an approved Phase 3 candidate",
      "evidence_sources": [
        "portable_advantages"
      ],
      "blocking_rationale": "Historical k=5 is a lower ceiling than the current clean baseline and points the wrong way under current main."
    },
    {
      "name": "literal_MAX_RETURNED_MEMORIES_sweep",
      "status": "blocked_inactive_knob",
      "estimated_leverage": "none",
      "dependency": "replaced by the caller-side TOP_K_MEMORIES authority",
      "evidence_sources": [
        "portable_advantages",
        "ablation_priorities"
      ],
      "blocking_rationale": "LongMemEval uses the caller-side TOP_K_MEMORIES limit, so a literal MAX_RETURNED_MEMORIES sweep is inactive on the live benchmark path."
    },
    {
      "name": "embedding_route_changes",
      "status": "blocked_no_historical_delta",
      "estimated_leverage": "none",
      "dependency": "none; keep excluded",
      "evidence_sources": [
        "portable_advantages"
      ],
      "blocking_rationale": "Historical and current embedding routes are already at parity, so there is no clean historical delta to port."
    },
    {
      "name": "answer_or_judge_model_swaps",
      "status": "blocked_no_model_swap_rule",
      "estimated_leverage": "forbidden",
      "dependency": "none; prohibited by task contract",
      "evidence_sources": [
        "portable_advantages",
        "judge_drift",
        "contamination_analysis"
      ],
      "blocking_rationale": "Judge drift is two-sided, the artifact bundle is provenance-contaminated, and the task contract forbids model swaps."
    },
    {
      "name": "portable_advantages_phase2_replay",
      "status": "blocked_no_residual_portable_advantage",
      "estimated_leverage": "none",
      "dependency": "none; no clean survivor remains to replay",
      "evidence_sources": [
        "portable_advantages"
      ],
      "blocking_rationale": "PORTABLE_ADVANTAGES concluded that no clean portable historical advantages survive subtraction."
    }
  ],
  "additional_coverage_vetoes": [
    {
      "name": "abstention_prompt_hardening",
      "status": "blocked_insufficient_target_cell",
      "estimated_leverage": "small",
      "dependency": "not before a larger abstention-locked subset exists",
      "evidence_sources": [
        "taxonomy",
        "ablation_priorities"
      ],
      "coverage_reference": {
        "type": "category_total",
        "category": "abstention",
        "count": 2,
        "secondary_cell": {
          "stage": "generation-error",
          "category": "temporal-reasoning",
          "count": 3
        }
      },
      "blocking_rationale": "The locked abstention surface is too small to outrank the three retrieval-heavy cells; even the nearby generation-error \u00d7 temporal-reasoning cell reaches only 3 failures."
    },
    {
      "name": "extraction_miss_only_retrieval_or_dedup_sweeps",
      "status": "blocked_insufficient_target_cell",
      "estimated_leverage": "unknown",
      "dependency": "not before extraction barrier work reopens with a denser locked target cell",
      "evidence_sources": [
        "taxonomy",
        "ablation_priorities"
      ],
      "coverage_reference": {
        "type": "target_failure_cell",
        "stage": "extraction-miss",
        "category": "single-session-assistant",
        "count": 4
      },
      "blocking_rationale": "The densest extraction-only cell still lands below the 5-case subset-veto floor, so retrieval/dedup ablations cannot honestly claim first-wave support there."
    }
  ],
  "contradictions": [
    {
      "name": "retrieval_ceiling_direction_conflict",
      "taxonomy_signal": "Dense retrieval-miss cells argue for testing a higher live retrieval ceiling on current main.",
      "history_signal": "The preserved historical k=5 shape is negative on current main, and literal MAX_RETURNED_MEMORIES is inactive for LongMemEval.",
      "resolution": "Approve only a forward caller-side TOP_K_MEMORIES sweep and keep historical k=5 / MAX_RETURNED_MEMORIES restores blocked."
    },
    {
      "name": "ranking_knob_without_historical_delta",
      "taxonomy_signal": "Threshold, pool-size, and hybrid-ordering changes line up with the three dense retrieval-heavy cells.",
      "history_signal": "The archaeology preserved no clean benchmark-specific historical values for these ranking defaults, and judge drift makes artifact-level wins easy to over-credit.",
      "resolution": "Keep these as current-main, single-variable experiments only; do not describe them as restored historical behavior."
    },
    {
      "name": "dedup_freshness_conflict",
      "taxonomy_signal": "Knowledge-update wrong-answer rows leave room for a freshness or supersession hypothesis.",
      "history_signal": "The 81.1 fast artifact bypassed dedup, the target cell has only 2 locked failures, and retrieval snapshots are missing.",
      "resolution": "Document dedup sensitivity but block promotion until the locked evidence surface grows or the forensics get richer."
    },
    {
      "name": "model_swap_conflict",
      "taxonomy_signal": "Some artifact paths could improve headline score by moving answer or judge behavior rather than retrieval behavior.",
      "history_signal": "JUDGE_DRIFT shows two-sided artifact drift, CONTAMINATION_ANALYSIS marks the 81.1 bundle provenance-contaminated, and the task contract forbids model swaps.",
      "resolution": "Keep all answer/judge/model swaps out of Phase 3 and restrict approved work to retrieval-side current-main levers."
    }
  ]
}
```
