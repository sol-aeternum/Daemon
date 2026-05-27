# Post-Mortem: 81.1% LongMemEval Artifact

Date: 2026-04-19

## What was real

- The **fast harness executed correctly** and produced clean result rows for all 500 questions. No inherent memory-layer bug exists.
- The **memory retrieval infrastructure** — Voyage embeddings, composite scoring, conversation-scoped `allowed_source_conversation_ids` isolation — works as designed on current `main`.
- The **current clean benchmark baseline** (`67.8%` strict correct-only, `339/33/128` split) is the honest current state of the memory layer.

## What was inflated or contaminated

### 1. The headline number itself (81.1%) is not from the committed scorer

The committed `score_accuracy()` implementation counts only `correct` judgments. That scorer produces `311/500 = 62.2%` from the same artifact rows. The `81.1%` figure was written manually into the summary/accounting layer as a weighted value, adding `+18.9pp` over the raw strict grade.

### 2. Split provenance inside the preserved bundle

The saved `run.log` records 11 FK failures starting from `0 already checkpointed`. The final clean checkpoint/results bundle contains 500 error-free rows for those same QIDs. The two artifacts are not from one fully traceable execution — they are from different phases or runs. This makes the bundle **artifact-trust contaminated** even though the individual rows are clean.

### 3. Judge and bundle-level grading drift

The saved historical bundle (`tier2_fast`) is simultaneously:
- **Stricter** on many exact/paraphrase answers that current `main` now grades correct (older harsher judge downgraded 172/339 current-correct rows).
- **Looser** on most current failures (the bundle graded 137/161 current non-correct rows more leniently, flipping many to `correct`).

This two-sided drift means the historical bundle is not a stable "better judge" or "worse judge" story — it is internally inconsistent, and its advantage cannot be attributed to any single causal lever.

### 4. Contamination vectors confirmed in the broader benchmark stack

Canonical shared-user persistence without teardown and legacy-evaluator bypass of `allowed_source_conversation_ids` are real contamination vectors. They do not explain the 81.1% artifact specifically (which used the fast lane), but they confirm the benchmark tooling was not clean.

## What lessons transferred

No portable historical advantages survive subtraction. Every candidate knob from the historical run either loses to the current clean baseline when replayed honestly or is already at parity:

| Candidate | Historical value | Current value | Replay result |
|---|---|---|---|
| Fast chunking | `4000 chars / 2 overlap` | `2000 / 0` | `-14.2pp` vs clean baseline |
| Retrieval depth | `top_k=5` | `top_k=10` | Lower ceiling, not a lift |

All remaining explanations — contamination, split provenance, judge drift, fixture quirks — are non-portable. Future improvement must start from the current clean baseline.

## Final approved outcome

**`no_shippable_composition`**

C2 Oracle approved this outcome. No Phase 3 candidate satisfied the literal composition gate (≥+2pp strict lift AND no measured-subset regression). No full-corpus triple-run was executed. No composition was shipped.

## Process changes that now prevent recurrence

1. **Fast lane per-run isolated users**: The fast harness creates a unique benchmark user per run and scopes retrieval to that user plus current-question conversations, preventing shared-state accumulation across questions.

2. **Canonical `allowed_source_conversation_ids` isolation**: The canonical runner now scopes retrieval to question-specific conversation IDs, preventing off-question memory exposure even when the shared benchmark user accumulates state.

3. **Question-level pre/post cleanup in fast lane**: The fast harness cleans up before and after each question, returning synchronous benchmark tables to zero between cases.

4. **Mandatory teardown before canonical re-runs**: Any canonical re-run must explicitly destroy and recreate the shared benchmark user to reset accumulated state.

5. **No historical artifact as shipping evidence**: The 81.1% bundle is classified as provenance-contaminated and non-portable. Future benchmark improvement work starts from the current clean baseline, not from historical artifacts.

## Machine-checkable summary

```json
{
  "artifact": "81.1% LongMemEval (longmemeval_tier2_fast)",
  "headline_number_source": "summary_layer_manual_weighting",
  "committed_scorer_output": 0.622,
  "current_clean_baseline_strict": 0.678,
  "was_real": "memory_retrieval_infrastructure_works_correctly",
  "was_inflated": true,
  "inflation_mechanism": "summary_layer_weighting_adds_18.9pp_over_strict",
  "was_contaminated": true,
  "contamination_type": "split_provenance_run_log_vs_final_bundle",
  "judge_drift": "two_sided_cannot_be_reduced_to_single_cause",
  "canonical_contamination_vectors_confirmed": [
    "shared_user_persistence_without_teardown",
    "legacy_evaluator_allowlist_bypass"
  ],
  "portable_historical_advantages": [],
  "final_outcome": "no_shippable_composition",
  "oracle_verdict": "approved_no_shippable_composition",
  "process_changes": [
    "fast_lane_per_run_isolated_users",
    "canonical_allowed_source_conversation_ids_isolation",
    "question_level_fast_lane_cleanup",
    "mandatory_teardown_before_canonical_reruns",
    "no_historical_artifact_as_shipping_evidence"
  ]
}
```
