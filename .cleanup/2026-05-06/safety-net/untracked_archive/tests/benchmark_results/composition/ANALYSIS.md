# Dev-Subset Composition Eligibility Analysis

Generated: 2026-04-19T21:05:00+00:00

## Bottom line

Task 4b cannot truthfully run a locked-dev top-3 composition yet.
The literal plan gate requires **three** dev-subset wins that each add at least **+2.0pp** strict accuracy **and** show **no negative measured-subset delta** versus their locked comparator. After re-checking the completed Phase 3 sweeps against that rule and the already-recorded Phase 3 closeout decisions, the current artifact set yields **0/3** clean candidates.

## Gate used

- Source of truth: `.sisyphus/plans/memory-benchmark-recovery.md:1255-1256`
- Literal gate: each composition candidate must deliver at least `+2.0pp` strict lift and **no** subset regression.
- Additional exclusion: do not reuse a Phase 3 result that already closed as non-promotable, blocked, or backed out.

## Candidate review

| Sweep | Candidate reviewed | Comparator | Strict lift | Clean? | Why it cannot enter the 4b top-3 |
| --- | --- | --- | ---: | --- | --- |
| `top_k_memories_sweep` | `k06` | `k05` | `+2.0pp` | no | Regressed `knowledge-update` (`33.3% -> 22.2%`), `temporal-reasoning` (`30.0% -> 10.0%`), and `retrieval-miss × temporal-reasoning` (`1/5 -> 0/5`). |
| `hybrid_ranking_weight_sweep` | `balanced` | `current` | `+6.0pp` | no | No subset regression, but Task 3e already closed it as **non-promotable** because the approved primary target cell `retrieval-miss × multi-session` stayed flat (`1/6 -> 1/6`). |
| `min_final_score_sweep` | `score_0.05` | `current 0.15` | `+4.0pp` | no | Regressed protected cells `single-session-user` (`33.3% -> 22.2%`) and `multi-session` (`30.0% -> 20.0%`), plus both approved retrieval-miss target cells (`1/6 -> 0/6`). |
| `dedup_threshold_sensitivity` | `tight_01` | `current` | `+6.0pp` | no | Work order kept dedup blocked on an insufficient 2-case target cell, and the sweep also regressed `single-session-assistant` (`44.4% -> 33.3%`) while leaving the tracked KU cell flat (`1/2 -> 1/2`). |
| `temporal_filter_integration` | `on` | `off` | `+0.0pp` | no | Failed the lift gate and reduced locked-failure-union recovery (`3/39 -> 2/39`); Phase 3 already marked it ineligible for promotion. |
| `abstention_prompt_hardening` | `on` | `off` | `-6.0pp` | no | Backed out in Task 3d after protected-cell regressions: `single-session-assistant` (`44.4% -> 0.0%`) and `knowledge-update` (`33.3% -> 22.2%`). |

## Selection outcome

- Required clean candidates: `3`
- Eligible clean candidates found: `0`
- Selected candidates: **none**

The closest thing to a composable candidate is `TOP_K_MEMORIES = 6`, but the literal 4b subset-veto rule still rejects it because its lift comes with negative measured-subset deltas on the locked dev subset. The other apparent headline gains (`balanced`, `score_0.05`, `tight_01`) were already closed out in Phase 3 as non-promotable for target-cell or regression reasons and cannot be revived here without violating the plan.

## Composition result

Because fewer than three clean wins exist, no combined dev-subset composition was executed.

- Combined strict score: `not run`
- Sum of individual lifts: `not applicable`
- Additivity ratio: `not applicable`

This is an intentional stop, not an omission: the required `composition_candidate_eligibility` pytest selector should pass when this blocked underfilled state is recorded truthfully, and fail only if the artifact marks an ineligible candidate as eligible or claims a composition run that did not occur.

## Machine-checkable summary

```json
{
  "status": "blocked_insufficient_clean_candidates",
  "minimum_clean_candidates_required": 3,
  "eligible_candidate_count": 0,
  "eligible_candidate_keys": [],
  "selection_attempt_order": [
    "top_k_memories:k06",
    "hybrid_weights:balanced",
    "min_final_score:score_0.05",
    "dedup_thresholds:tight_01",
    "temporal_filter:on",
    "abstention_guardrail:on"
  ],
  "candidate_reviews": [
    {
      "candidate_key": "top_k_memories:k06",
      "work_order_status": "approved",
      "candidate_run": "k06",
      "comparator_run": "k05",
      "mechanism": "caller-side TOP_K_MEMORIES 5 -> 6",
      "strict_lift_pp": 2.0,
      "meets_lift_gate": true,
      "phase3_promotable": false,
      "eligible_for_composition": false,
      "subset_regressions": [
        "primary_cell:knowledge-update 33.3% -> 22.2% (-11.1pp)",
        "primary_cell:temporal-reasoning 30.0% -> 10.0% (-20.0pp)",
        "target_cell:retrieval-miss x temporal-reasoning 1/5 -> 0/5 (-1)"
      ],
      "rejection_reason": "subset_regression"
    },
    {
      "candidate_key": "hybrid_weights:balanced",
      "work_order_status": "approved",
      "candidate_run": "balanced",
      "comparator_run": "current",
      "mechanism": "hybrid weights 0.5/0.3/0.2 -> 0.4/0.4/0.2",
      "strict_lift_pp": 6.0,
      "meets_lift_gate": true,
      "phase3_promotable": false,
      "eligible_for_composition": false,
      "subset_regressions": [],
      "rejection_reason": "phase3_closed_non_promotable_primary_target_flat",
      "closeout_note": "Task 3e kept current weights because retrieval-miss x multi-session stayed flat at 1/6 -> 1/6."
    },
    {
      "candidate_key": "min_final_score:score_0.05",
      "work_order_status": "approved",
      "candidate_run": "score_0.05",
      "comparator_run": "current_0.15",
      "mechanism": "MIN_FINAL_SCORE 0.15 -> 0.05",
      "strict_lift_pp": 4.0,
      "meets_lift_gate": true,
      "phase3_promotable": false,
      "eligible_for_composition": false,
      "subset_regressions": [
        "protected_cell:single-session-user 33.3% -> 22.2% (-11.1pp)",
        "protected_cell:multi-session 30.0% -> 20.0% (-10.0pp)",
        "target_cell:retrieval-miss x multi-session 1/6 -> 0/6 (-1)",
        "target_cell:retrieval-miss x single-session-user 1/6 -> 0/6 (-1)"
      ],
      "rejection_reason": "subset_regression"
    },
    {
      "candidate_key": "dedup_thresholds:tight_01",
      "work_order_status": "blocked_insufficient_target_cell",
      "candidate_run": "tight_01",
      "comparator_run": "current",
      "mechanism": "dedup thresholds 0.90/0.82/0.65 -> 0.92/0.85/0.70",
      "strict_lift_pp": 6.0,
      "meets_lift_gate": true,
      "phase3_promotable": false,
      "eligible_for_composition": false,
      "subset_regressions": [
        "protected_cell:single-session-assistant 44.4% -> 33.3% (-11.1pp)"
      ],
      "rejection_reason": "blocked_work_order_and_subset_regression",
      "closeout_note": "Tracked generation-error x knowledge-update cell stayed flat at 1/2 -> 1/2 and the work order never approved this sweep for first-wave promotion."
    },
    {
      "candidate_key": "temporal_filter:on",
      "work_order_status": "approved",
      "candidate_run": "on",
      "comparator_run": "off",
      "mechanism": "TEMPORAL_QUERY_FILTER_ENABLED false -> true",
      "strict_lift_pp": 0.0,
      "meets_lift_gate": false,
      "phase3_promotable": false,
      "eligible_for_composition": false,
      "subset_regressions": [
        "locked_failure_union 3/39 -> 2/39 (-1)"
      ],
      "rejection_reason": "failed_lift_gate"
    },
    {
      "candidate_key": "abstention_guardrail:on",
      "work_order_status": "blocked_insufficient_target_cell",
      "candidate_run": "on",
      "comparator_run": "off",
      "mechanism": "answer-prompt abstention guardrail off -> on",
      "strict_lift_pp": -6.0,
      "meets_lift_gate": false,
      "phase3_promotable": false,
      "eligible_for_composition": false,
      "subset_regressions": [
        "protected_cell:single-session-assistant 44.4% -> 0.0% (-44.4pp)",
        "protected_cell:knowledge-update 33.3% -> 22.2% (-11.1pp)"
      ],
      "rejection_reason": "backed_out_subset_regression"
    }
  ],
  "composition_run_executed": false,
  "combined_strict_score": null,
  "sum_individual_lifts_pp": null,
  "combined_lift_pp": null,
  "additivity_ratio": null,
  "blocked_reason": "Fewer than three clean +2pp/no-regression dev-subset wins exist in the committed Phase 3 artifacts."
}
```
