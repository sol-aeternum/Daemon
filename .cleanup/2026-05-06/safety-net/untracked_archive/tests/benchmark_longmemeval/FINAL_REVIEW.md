# Oracle Final Review — Task C2

Generated: 2026-04-19T22:00:00+00:00

## Review target

This review evaluates the already-recorded `no_shippable_composition` closeout in `tests/benchmark_results/final/VARIANCE.md`.
It does **not** invent a combined benchmark score, a fabricated composition, or a final shipped lift.

## Verdict

**Oracle approves the `no_shippable_composition` outcome.**

The evidence chain is internally consistent and sufficiently complete for a truthful stop:

- `tests/benchmark_results/composition/ANALYSIS.md` records `eligible_candidate_count = 0` and `composition_run_executed = false`.
- `tests/benchmark_results/composition/ADDITIVE.md` truthfully skips pairwise/additivity diagnosis because no composition ran.
- `tests/benchmark_results/final/VARIANCE.md` closes as `no_shippable_composition` and correctly states that no full-corpus triple-run was executed.

No shipped change smuggles in contamination-like behavior here because no composition was promoted for shipping.

## Artifacts reviewed

- `tests/benchmark_results/final/VARIANCE.md`
- `tests/benchmark_results/composition/ANALYSIS.md`
- `tests/benchmark_results/composition/ADDITIVE.md`
- `tests/benchmark_results/dev_subset_baseline/taxonomy.md`
- `tests/benchmark_longmemeval/PORTABLE_ADVANTAGES.md`
- `tests/benchmark_longmemeval/PHASE3_WORK_ORDER.md`
- `tests/benchmark_longmemeval/JUDGE_DRIFT.md`
- `tests/benchmark_longmemeval/CONTAMINATION_ANALYSIS.md`

## Explainability screen

### 1. The candidate pool is explainable from taxonomy evidence

The Phase 3 candidates were not arbitrary. `taxonomy.md` and `PHASE3_WORK_ORDER.md` show why these current-main levers were tested:

- dense `retrieval-miss × multi-session` and `retrieval-miss × single-session-user` cells justify retrieval-ceiling, score-threshold, and ranking experiments;
- the dense `retrieval-miss × temporal-reasoning` cell justifies the candidate-pool and temporal-filter experiments;
- the small `generation-error × knowledge-update` cell explains why dedup remained tracked but blocked rather than promoted;
- the tiny abstention overlay explains why abstention hardening was allowed as a bounded check but not as a composition-ready survivor.

So every measured lift in the Phase 3 record is traceable to a current-main taxonomy hypothesis. None require inventing a hidden historical advantage.

### 2. No clean historical advantage exists to rescue a composition

`PORTABLE_ADVANTAGES.md` concludes that no clean portable historical advantages survive subtraction.
That matters here because any attempt to override the failed composition gate with "history says this should still ship" would be unsupported.

The historical-forensic documents reinforce that veto:

- `JUDGE_DRIFT.md` shows two-sided grading drift, so the old bundle is not a stable "better judge" story.
- `CONTAMINATION_ANALYSIS.md` classifies the preserved `81.1%` artifact as provenance-contaminated and fast-lane only, which makes it unsuitable as shipping evidence.

History therefore contributes only **negative constraints** in the final review: it can block over-claiming, but it cannot donate a clean composition candidate.

### 3. The no-composition stop is stricter than a headline-lift read, and that is correct

Several Phase 3 runs produced headline strict-score gains, but the final decision is not based on headline lift alone.
The active gate requires the lift to remain explainable **and** survive subset-regression and promotability checks.
The review below confirms that every apparent lift is already accounted for by the committed artifacts and that none can honestly graduate into a shipped composition.

## Subset-regression analysis

| Candidate | Why it was reasonable to test | Headline lift | Why Oracle still blocks shipping |
| --- | --- | ---: | --- |
| `top_k_memories:k06` | Dense `retrieval-miss × multi-session` cell justified a caller-side retrieval-ceiling sweep. | `+2.0pp` | Regressed measured subsets: `knowledge-update 33.3% -> 22.2%`, `temporal-reasoning 30.0% -> 10.0%`, and `retrieval-miss × temporal-reasoning 1/5 -> 0/5`. |
| `hybrid_weights:balanced` | Same dense retrieval-heavy multi-session cell justified a ranking-order test. | `+6.0pp` | `ANALYSIS.md` correctly keeps it non-shippable because the approved primary target cell stayed flat at `retrieval-miss × multi-session 1/6 -> 1/6`; the lift is real but not promotable under the work order. |
| `min_final_score:score_0.05` | Dense `retrieval-miss × single-session-user` cell justified threshold testing. | `+4.0pp` | Regressed protected and target cells: `single-session-user 33.3% -> 22.2%`, `multi-session 30.0% -> 20.0%`, `retrieval-miss × multi-session 1/6 -> 0/6`, `retrieval-miss × single-session-user 1/6 -> 0/6`. |
| `dedup_thresholds:tight_01` | Weak freshness signal existed, but only on a 2-case `generation-error × knowledge-update` cell. | `+6.0pp` | Work order kept dedup blocked below the 5-case subset-veto floor, and the run still regressed `single-session-assistant 44.4% -> 33.3%` while leaving the tracked KU cell flat at `1/2 -> 1/2`. |
| `temporal_filter:on` | Dense `retrieval-miss × temporal-reasoning` cell justified a deterministic temporal-window test. | `+0.0pp` | Failed the lift gate and reduced locked-failure-union recovery from `3/39` to `2/39`. |
| `abstention_guardrail:on` | The abstention overlay was small but relevant enough for a bounded prompt hardening check. | `-6.0pp` | Backed out after protected-cell regressions: `single-session-assistant 44.4% -> 0.0%` and `knowledge-update 33.3% -> 22.2%`. |

## Oracle accounting of every apparent lift

1. **`k06`** is a real current-main lift, but it is fully explained by a taxonomy-driven retrieval-ceiling experiment and already disqualified by measured subset regressions.
2. **`balanced`** is also a real current-main lift, but it does not improve the exact target cell that justified the sweep, so shipping it would over-credit a score gain that the work order never authorized as promotable.
3. **`score_0.05`** and **`tight_01`** are headline gains that collapse under subset protection and evidence-floor rules.
4. **`temporal_filter:on`** and **`abstention_guardrail:on`** never become composition candidates at all.

That means the final record has no unexplained residual lift and no missing candidate that should have been composed. The zero-candidate outcome is supported, not accidental.

## Contamination-like behavior screen

Oracle specifically checked for the kinds of contamination-like behavior that would make a final benchmark conclusion unsafe to ship.

- **No historical contamination is being promoted.** The review does not use the provenance-split `81.1%` artifact as positive evidence for any candidate.
- **No scoring/accounting drift is being promoted.** `JUDGE_DRIFT.md` shows that historical grading behavior is internally inconsistent, so no candidate is excused by a looser or harsher old bundle.
- **No canonical shared-state risk is being smuggled into a shipped change.** `CONTAMINATION_ANALYSIS.md` confirms real canonical contamination vectors elsewhere in the toolchain, but this task ships no composition and therefore does not convert any contamination-prone artifact into approved benchmark evidence.

If Oracle had approved a composition despite the failed subset gate, the review would have been smuggling in contamination-like behavior by treating unstable or under-justified evidence as ship-ready. The recorded `no_shippable_composition` stop avoids that mistake.

## Final decision

Oracle approves the `no_shippable_composition` outcome.

There are **no blocking concerns against the no-composition verdict itself**. The blockers apply only to any hypothetical attempt to ship a composition anyway.

If future work wants a shippable composition, it first needs either:

1. new clean candidates that satisfy the literal lift/no-regression gate, or
2. a deliberately revised gate supported by new evidence rather than by contaminated or non-portable historical artifacts.

## Machine-checkable summary

```json
{
  "task": "C2",
  "status": "approved_no_shippable_composition",
  "oracle_verdict": "approve_no_composition",
  "review_target": "no_shippable_composition",
  "composition_run_executed": false,
  "full_corpus_triple_run_executed": false,
  "clean_historical_advantages_available": false,
  "blocking_concerns": [],
  "reviewed_artifacts": [
    "tests/benchmark_results/final/VARIANCE.md",
    "tests/benchmark_results/composition/ANALYSIS.md",
    "tests/benchmark_results/composition/ADDITIVE.md",
    "tests/benchmark_results/dev_subset_baseline/taxonomy.md",
    "tests/benchmark_longmemeval/PORTABLE_ADVANTAGES.md",
    "tests/benchmark_longmemeval/PHASE3_WORK_ORDER.md",
    "tests/benchmark_longmemeval/JUDGE_DRIFT.md",
    "tests/benchmark_longmemeval/CONTAMINATION_ANALYSIS.md"
  ],
  "subset_regression_analysis": [
    {
      "candidate_key": "top_k_memories:k06",
      "headline_lift_pp": 2.0,
      "evidence_basis": "taxonomy_supported_current_main",
      "historical_support": "veto_only_negative_historical_k5",
      "ship_decision": "blocked_subset_regression",
      "subset_regressions": [
        "primary_cell:knowledge-update 33.3% -> 22.2% (-11.1pp)",
        "primary_cell:temporal-reasoning 30.0% -> 10.0% (-20.0pp)",
        "target_cell:retrieval-miss x temporal-reasoning 1/5 -> 0/5 (-1)"
      ]
    },
    {
      "candidate_key": "hybrid_weights:balanced",
      "headline_lift_pp": 6.0,
      "evidence_basis": "taxonomy_supported_current_main",
      "historical_support": "none",
      "ship_decision": "blocked_primary_target_flat",
      "subset_regressions": [],
      "target_cell_result": "retrieval-miss x multi-session 1/6 -> 1/6"
    },
    {
      "candidate_key": "min_final_score:score_0.05",
      "headline_lift_pp": 4.0,
      "evidence_basis": "taxonomy_supported_current_main",
      "historical_support": "none",
      "ship_decision": "blocked_subset_regression",
      "subset_regressions": [
        "protected_cell:single-session-user 33.3% -> 22.2% (-11.1pp)",
        "protected_cell:multi-session 30.0% -> 20.0% (-10.0pp)",
        "target_cell:retrieval-miss x multi-session 1/6 -> 0/6 (-1)",
        "target_cell:retrieval-miss x single-session-user 1/6 -> 0/6 (-1)"
      ]
    },
    {
      "candidate_key": "dedup_thresholds:tight_01",
      "headline_lift_pp": 6.0,
      "evidence_basis": "taxonomy_signal_below_subset_veto_floor",
      "historical_support": "not_applicable_fast_artifact_bypassed_dedup",
      "ship_decision": "blocked_insufficient_target_cell_and_subset_regression",
      "subset_regressions": [
        "protected_cell:single-session-assistant 44.4% -> 33.3% (-11.1pp)"
      ],
      "target_cell_result": "generation-error x knowledge-update 1/2 -> 1/2"
    },
    {
      "candidate_key": "temporal_filter:on",
      "headline_lift_pp": 0.0,
      "evidence_basis": "taxonomy_supported_current_main",
      "historical_support": "none",
      "ship_decision": "failed_lift_gate",
      "subset_regressions": [
        "locked_failure_union 3/39 -> 2/39 (-1)"
      ]
    },
    {
      "candidate_key": "abstention_guardrail:on",
      "headline_lift_pp": -6.0,
      "evidence_basis": "taxonomy_signal_below_subset_veto_floor",
      "historical_support": "none",
      "ship_decision": "backed_out_subset_regression",
      "subset_regressions": [
        "protected_cell:single-session-assistant 44.4% -> 0.0% (-44.4pp)",
        "protected_cell:knowledge-update 33.3% -> 22.2% (-11.1pp)"
      ]
    }
  ],
  "explainability_checks": {
    "all_measured_lifts_accounted_for": true,
    "unexplained_lifts": [],
    "contamination_like_behavior_shipped": false,
    "shipped_change_exists": false,
    "approval_basis": [
      "4b_zero_clean_candidates",
      "4c_truthful_skip_without_composition",
      "4d_no_shippable_composition_closeout",
      "portable_advantages_none",
      "judge_drift_two_sided",
      "artifact_trust_contaminated_81_1"
    ]
  }
}
```
