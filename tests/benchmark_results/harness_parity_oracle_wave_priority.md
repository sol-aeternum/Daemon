# Harness Parity Oracle Wave Priority Review

**Artifact decision**: `binding-priority-refresh`  
**Date**: 2026-05-27  
**Scope**: Binding W1 task-priority/order guidance only for `.sisyphus/plans/wave1-prompt-surface-changes.md`.  
**Measurement status**: This oracle review is interpretive guidance, not benchmark measurement evidence.

## Inputs reviewed

- Stable baseline declaration: `tests/benchmark_results/harness_parity_baseline_stability.md`
- Run1 full per-category source: `tests/benchmark_results/harness_parity_baseline/run1/summary.json`
- Run2 headline-only source: `tests/benchmark_results/harness_parity_baseline/run2/headline_summary.json`
- Current W1 plan boundary: `.sisyphus/plans/wave1-prompt-surface-changes.md`
- Supporting context: `tests/benchmark_results/wave1_benchmark_consumer_path.md`, `tests/benchmark_results/harness_parity_static_check.md`, and `.sisyphus/plans/longmemeval-parity-baseline-completion.md:480-516`

## Binding baseline anchor

The binding aggregate anchor for W1 priority planning is `0.14013426853707415`.

- Run1 aggregate: `0.1342685370741483` from `67 / 499` after `1` runtime exclusion.
- Run2 headline aggregate: `0.146` from `73 / 500` with `0` runtime exclusions.
- Aggregate delta: `0.011731462925851695`, which passed the Task 8 `0.02` threshold.
- Run2 raw artifacts and per-category metrics are unavailable; per-category run2 deltas are waived and must not be reconstructed.

## Run1 category profile used for priority only

This is the only surviving per-category raw profile. It is useful for W1 prioritization, but it is not a per-category stability result.

| Category | Correct / usable | Excluded | Run1 rate | Binding W1 priority interpretation |
|---|---:|---:|---:|---|
| `temporal-reasoning` | `8 / 133` | `0` | `6.02%` | Highest opportunity and highest fragility; prioritize in probe, prompt-smoke, and gate-sample selection. |
| `multi-session` | `16 / 133` | `0` | `12.03%` | Highest-volume reasoning category; prioritize alongside temporal for Chain-of-Note and budget-risk checks. |
| `single-session-preference` | `4 / 30` | `0` | `13.33%` | Small denominator; include as protected coverage, but do not overfit W1 ordering to this category alone. |
| `single-session-assistant` | `8 / 56` | `0` | `14.29%` | Protected coverage with known policy-mismatch risk; classify failures, but do not authorize extraction-policy changes in W1. |
| `knowledge-update` | `15 / 78` | `0` | `19.23%` | Stronger than aggregate but semantically important; prioritize timestamp/source/confidence visibility and non-regression. |
| `single-session-user` | `16 / 69` | `1` | `23.19%` | Best surviving category; treat as regression guardrail/protection, not the main lift target. |

## Binding W1 priority refresh

1. **Keep W1 bounded to prompt-surface work in `orchestrator/memory/injection.py`.** No producer, retrieval, schema, reranker, benchmark-adapter, or category-specific implementation is authorized by this review.

2. **Before implementation, keep the diagnostic/audit tasks high priority rather than optional.** TODOs 1-4 should remain ahead of TODOs 6-11 because the low anchor can still be dominated by retrieval/extraction absence rather than prompt formatting. The R/F/A probe must cover all six available corpus categories when feasible, with temporal-reasoning and multi-session weighted first for manual attention.

3. **Preserve the current serial implementation order for TODOs 6-11.** The order is technically correct: schema/confidence helpers → JSON array rendering → Chain-of-Note → abstention/confidence guidance → L0 integration → token-budget recalibration. Reordering these would increase risk because later instructions depend on the JSON evidence fields being stable.

4. **Treat TODOs 7-9 as the highest-payoff W1 implementation cluster.** JSON evidence, Chain-of-Note, and confidence/hedge guidance are the W1 changes most plausibly relevant to temporal-reasoning, multi-session, knowledge-update, and preference failures where the right evidence may be present but underused.

5. **Treat TODO 11 as a high-risk validation priority, not a speculative optimization.** The 1500-token budget can hurt large-context temporal-reasoning and multi-session cases if it drops needed L1 evidence. Its evidence must show L0/instructions retained and L1 tail-dropping behavior checked on long-context examples.

6. **Require category-complete validation sampling where the existing W1 plan allows samples.** For TODO 14/TODO 15 prompt-surface samples, prefer at least one prompt from each of `single-session-user`, `multi-session`, `single-session-preference`, `temporal-reasoning`, `knowledge-update`, and `single-session-assistant`; if a task still uses five samples, temporal-reasoning and multi-session should not be the omitted categories.

7. **Use the anchor only as the aggregate comparison basis.** If W1 keeps a +2pp aggregate lift rule, compare against `0.14013426853707415`; exact count thresholds must be recomputed from raw W1 denominators/exclusions at gate time. Do not compare the new W1 baseline to the historical pre-parity baseline framing as anomaly/failure math.

## Category-specific binding guidance

| Priority | Categories | W1 task emphasis | Binding disposition |
|---|---|---|---|
| P0 | `temporal-reasoning`, `multi-session` | TODO 2 R/F/A classification; TODO 8 Chain-of-Note; TODO 11 budget; TODO 14/15 sample coverage | Main lift/opportunity focus. Do not skip these in probe or smoke samples. |
| P1 | `knowledge-update`, `single-session-preference` | TODO 6/7 timestamp/source/confidence fields; TODO 8 evidence comparison; TODO 9 cautious hedge/abstain guidance | Secondary lift focus with non-regression checks. Avoid overfitting to the small preference denominator. |
| P1-protected | `single-session-assistant` | TODO 2 failure classification; TODO 7 source_type visibility; TODO 9 confidence guidance | Protect and observe. Do not change assistant-extraction policy under W1. |
| P2-protected | `single-session-user` | TODO 14/15 non-regression sample; gate reporting | Best current category; prioritize protection over targeted intervention. |

## De-prioritized or explicitly out of scope

- Do not start W2/retrieval work merely because temporal-reasoning or multi-session are weak. W1 may only reroute after its own R/F/A gate says retrieval absence dominates.
- Do not add category-specific prompt branches. W1 should ship one general memory-evidence surface for all categories.
- Do not use run2 per-category values, per-category deltas, or category stability claims; they do not exist in surviving artifacts.
- Do not treat this review as permission to edit `.sisyphus/plans/wave1-prompt-surface-changes.md`; Task 10 owns that surgical patch.

## Non-binding observations

- The run1 profile suggests W1's best chance is improving evidence use on large reasoning categories, not extracting more facts or widening retrieval. That hypothesis must be tested by TODO 2/3 rather than assumed.
- `single-session-assistant` may remain constrained by Daemon's assistant-content extraction policy; that is useful context for interpretation, but it is outside W1 prompt-surface implementation scope.
- Because run2 is headline-only, category-level prioritization should be conservative: use run1 as a profile, not as a stable category baseline.

## Final oracle disposition

`binding-priority-refresh`: W1 should proceed, after Task 10 patches stale plan framing, with the existing dependency order preserved but with evidence attention concentrated on temporal-reasoning and multi-session first, knowledge-update/preference/assistant protected next, and single-session-user treated as the primary regression guard. All recommendations are bounded to W1 prompt-surface priority/order guidance and authorize no implementation outside the existing W1 boundary.
