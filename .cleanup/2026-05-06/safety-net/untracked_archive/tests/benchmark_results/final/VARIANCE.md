# Final Composition Variance Report

Generated: 2026-04-19T21:30:00+00:00

## Verdict

**`no_shippable_composition`**

## Rationale

Task 4b found `eligible_candidate_count = 0`. No dev-subset composition was executed because no Phase 3 ablation result satisfied the literal composition gate (≥+2pp strict lift AND no measured-subset regression).

The composition eligibility review examined six Phase 3 candidates:

| Candidate | Strict Lift | Clean? | Rejection Reason |
|---|---|---|---|
| `top_k_memories:k06` | +2.0pp | no | Subset regressions on knowledge-update, temporal-reasoning, and retrieval-miss × temporal-reasoning |
| `hybrid_weights:balanced` | +6.0pp | no | Phase 3 closed as non-promotable; primary target cell flat at 1/6 |
| `min_final_score:score_0.05` | +4.0pp | no | Protected-cell regressions on single-session-user and multi-session |
| `dedup_thresholds:tight_01` | +6.0pp | no | Work order blocked; protected cell regression on single-session-assistant |
| `temporal_filter:on` | +0.0pp | no | Failed lift gate |
| `abstention_guardrail:on` | -6.0pp | no | Backed out after protected-cell regressions |

No composition run was executed because no three clean candidates existed.

## Full-Corpus Runs

- `composition_run_executed`: **false**
- `full_corpus_triple_run_executed`: **false**

No full-corpus triple-run was executed because there was no composition configuration to verify.

## Machine-checkable summary

```json
{
  "status": "no_shippable_composition",
  "composition_run_executed": false,
  "full_corpus_triple_run_executed": false,
  "eligible_candidate_count": 0,
  "blocking_reason": "Fewer than three clean +2pp / no-regression dev-subset wins in committed Phase 3 artifacts."
}
```
