# Git State Cleanup Ledger
# Plan: git-state-cleanup-post-parity-ship
# Date: 2026-05-06

## Phase 1: T1 Snapshot (Completed)

### Timestamp
2026-05-06T11:36:00Z

### T1 Statement
**T1 performed no git mutation; only ledger/evidence files were written.**

### Git State Snapshot

| Ref | SHA | Type | Note |
|-----|-----|------|------|
| HEAD | d4e063fa | commit (detached) | Tag harness-parity-shipped |
| main | 290b7c02 | branch | Ahead 15 commits from baseline |
| origin/main | N/A | - | Not locally known (no remote tracking) |
| harness-parity-shipped | d4e063fa | tag | Current HEAD |
| pre-wave-1 | fdf97a75 | tag | Confirmed via high-accuracy resolve |

### Stash State
- stash@{0}: On main: wave0-task1-memory-preserve-20260501T094245Z
- stash@{1}: On main: build artifacts

### Working Tree Status
- Modified: 37 tracked files
- Untracked: 250+ files (benchmark artifacts, test fixtures, docs)

### Key Observation
The `pre-wave-1` tag resolves to `fdf97a75`, NOT `07e9e6e7` as originally alleged.
This matches the "high-accuracy review" finding in the plan context.

### Evidence Files
- `.sisyphus/evidence/task-1-snapshot-current-state.txt` - Raw command outputs
- `.sisyphus/evidence/task-1-snapshot-current-state-no-mutation.txt` - No-mutation proof

### Next Phase
T2: Diff suspicious commits and classify scope facts

---

## Phase 1: T2 Suspicious Commit Diff Classification (Completed)

### Timestamp
2026-05-06T11:50:00Z

### T2 Statement
**T2 performed no git mutation; only ledger/evidence files were written.**

### Commit 86ad9cf2 — Scope Analysis

**Subject:** fix(longmemeval): repair shipped parity harness scope
**Date:** Wed May 6 17:10:58 2026 +0930

#### File Changes
- **DELETED (41 files):** All `.sisyphus/evidence/task-*-*.txt` and `.sisyphus/evidence/task-*-*.json` — task-level evidence artifacts from prior cleanup work
- **MODIFIED (1 file):** `tests/longmemeval/parity_harness.py`

#### Scope Questions
- **Touched `orchestrator/memory/**`?** NO. Zero changes to `orchestrator/memory/`.
- **Touched files outside `tests/longmemeval/**`?** YES — deleted 41 files under `.sisyphus/evidence/`.

#### User-Owned Scope Questions (86ad9cf2)
The following paths outside `tests/longmemeval/**` were changed — disposition is a user decision:
1. **`.sisyphus/evidence/task-*-*.txt`** (37 deleted files) — Task-level evidence artifacts
2. **`.sisyphus/evidence/task-*-*.json`** (4 deleted files) — Task-level evidence JSON artifacts

**Surface question:** Are `.sisyphus/evidence/` deletions acceptable as "internal workflow debris" cleanup, or should they be preserved?
**Note:** Commission a separate defect-investigation plan, or ledger-only documentation for now?

---

### Commit 290b7c02 — Scope Analysis

**Subject:** docs(memory): restore roadmap baseline scope
**Date:** Wed May 6 16:35:25 2026 +0930

#### File Changes
- **MODIFIED (1 file):** `docs/MEMORY_UPGRADE_ROADMAP.md`

#### Diff Evidence Relevant to `docs(memory): restore roadmap baseline scope`

The diff rewrites `docs/MEMORY_UPGRADE_ROADMAP.md` from 205 lines to 140 lines.

**Opening note — BEFORE:**
```
> Baseline status (generated 2026-05-06): `tests/benchmark_results/harness_parity_baseline_decision.md`
> declares **HALT — baseline undeterminable**. The blocking T14 artifact
> `tests/benchmark_results/harness_parity_baseline_run.json` halted because the full haystack-bearing
> LongMemEval_S corpus is unavailable, so no numeric T15 production-aligned baseline exists today.
> The historical **10.4% adjusted** (49/473) Wave 0 Option A figure remains a pre-parity /
> harness-artifact comparison anchor only, not the current post-parity production baseline.
```

**Opening note — AFTER:**
```
> Derived from: research compass (`compass_artifact_wf-f8a390bb...md`), cross-checked against
> `MEMORY_LAYER.md` (authoritative) and userMemories. `TECHNICAL_SPECS.md` embedding section
> is stale (still lists `text-embedding-3-small`); Voyage-4 is live.
> Baseline: LongMemEval_S = 67.8%. Frontier: Mem0-new 93.4%, Hindsight 91.4%, Supermemory 85.2%.
> Target trajectory: Waves 1-4 plausibly close 67.8 -> 78-82%. Wave 5+ is the 82-88% tier
> and compounds slower.
```

#### Classification Against Atlas/status Claim

**Atlas/status claim:** "290b7c02 changed narrative from 'HALT — baseline undeterminable' to '67.8% baseline with trajectory to 78-82%'"

**Classification: CONTRADICTS the Atlas/status claim (diff evidence)**

The diff does NOT show a trajectory from HALT to post-parity baseline. Instead:
- **REMOVES** the HALT status note from the opening
- **REMOVES** all per-category scores (18.7%, 17.2%, 13.7%, 7.1%, 2.3%, 0.0%) that were the HALT-blocked production-aligned baseline
- **REMOVES** the "Production-aligned baseline structure" section entirely
- **ADDS** the original 67.8% figure (pre-adjustment, pre-parity baseline)

The 67.8% figure is the original LongMemEval baseline BEFORE the 10.4% (49/473) adjustment.
This is a NARRATIVE REGRESSION — replacing the HALT-blocked production-aligned figure with pre-parity numbers.

---

### Evidence Files
- `.sisyphus/evidence/task-2-suspicious-commits.txt` — Full diff outputs, file lists, scope analysis, Atlas classification
- `.sisyphus/evidence/task-2-no-repair-drift.txt` — No-mutation proof, before/after status comparison

### T2 Findings Summary
| Question | Answer |
|----------|--------|
| 86ad9cf2 touched `orchestrator/memory/**`? | NO |
| 86ad9cf2 touched files outside `tests/longmemeval/**`? | YES — `.sisyphus/evidence/` (41 deletions) |
| 290b7c02 Atlas/status classification | CONTRADICTS claim — diff shows regression to pre-parity 67.8%, not post-parity trajectory |
| Any git mutations by T2? | NO |

### Next Phase
T3: Inventory the dirty tree and categorize for potential cleanup

---

---

## Phase 1: T3 Dirty Tree Inventory (Completed - Third QA Repair)

### Timestamp
2026-05-08T10:00:00Z

### T3 Statement
**T3 performed no git mutation; only ledger/evidence files were written.**

### Inventory Summary (EXACT)

| Metric | Count |
|--------|-------|
| Modified tracked files (M) | 45 |
| Untracked files (??) | 391 |
| **Total dirty entries** | **436** |

### Verification

| Check | Value |
|-------|-------|
| Raw status path count | 436 |
| Table row count | 436 |
| Unique paths in table | 436 |
| Duplicate count | 0 |
| Missing count | 0 |
| Extra count | 0 |

### Bucket Counts (EXACT from table)

| Bucket | Count |
|--------|-------|
| parity-related | 3 |
| advisor feature | 10 |
| Wave 0 archaeology | 370 |
| modified core code | 25 |
| docs/config/other | 25 |
| unknown | 3 |
| **TOTAL** | **436** |

### Complete Per-File Bucket Table (436 rows)

#### parity-related (3 rows)
| Status | Path | Reason |
|--------|------|--------|
| ?? | .cleanup/2026-05-06/cleanup_ledger.md | cleanup ledger artifact |
| ?? | tests/benchmark_results/harness_parity_inventory_runner_consumers.tmp.md | explicit parity reference in path |
| ?? | tests/benchmark_results/wave1_benchmark_consumer_path.md | parity harness or wave1 benchmark |

#### advisor feature (10 rows)
| Status | Path | Reason |
|--------|------|--------|
| ?? | frontend/__tests__/advisor-events.test.ts | advisor system file |
| ?? | frontend/__tests__/chat-route-advisor-events.test.ts | advisor system file |
| ?? | frontend/lib/advisorEvents.ts | advisor system file |
| ?? | migrations/030_add_advisor_traces.sql | advisor system file |
| ?? | orchestrator/advisor_budget.py | advisor system file |
| ?? | orchestrator/prompts_advisor.py | advisor system file |
| ?? | orchestrator/tools/advisor.py | advisor system file |
| ?? | tests/test_advisor.py | advisor system file |
| ?? | tests/test_advisor_tool.py | advisor system file |
| ?? | tests/test_advisor_traces.py | advisor system file |

#### Wave 0 archaeology (370 rows)

| Status | Path | Reason |
|--------|------|--------|
| ?? | tests/benchmark_harness/contradiction_single_verify.py | benchmark harness script |
| ?? | tests/benchmark_harness/extraction_provider_override.py | benchmark harness script |
| ?? | tests/benchmark_harness/f1_fingerprint_stability.py | benchmark harness script |
| ?? | tests/benchmark_harness/f2_extraction_output_determinism.py | benchmark harness script |
| ?? | tests/benchmark_harness/guardrails.py | benchmark harness script |
| ?? | tests/benchmark_harness/ingestion_preserve_query.py | benchmark harness script |
| ?? | tests/benchmark_harness/ingestion_rerun.py | benchmark harness script |
| ?? | tests/benchmark_harness/ingestion_rerun_full_corpus.py | benchmark harness script |
| ?? | tests/benchmark_harness/ingestion_rerun_preserved.py | benchmark harness script |
| ?? | tests/benchmark_harness/ingestion_rerun_recovery.py | benchmark harness script |
| ?? | tests/benchmark_harness/preservation_query.py | benchmark harness script |
| ?? | tests/benchmark_harness/reset_verify_helper.py | benchmark harness script |
| ?? | tests/benchmark_harness/run_single_preserved.py | benchmark harness script |
| ?? | tests/benchmark_harness/run_triple_preserved.py | benchmark harness script |
| ?? | tests/benchmark_harness/run_triple_preserved_clean.py | benchmark harness script |
| ?? | tests/benchmark_harness/run_triple_rerun.py | benchmark harness script |
| ?? | tests/benchmark_harness/verify_recovery_logic.py | benchmark harness script |
| ?? | tests/benchmark_harness/verify_reset_completeness.py | benchmark harness script |
| ?? | tests/benchmark_harness/voyage_drift_test.py | benchmark harness script |
| ?? | tests/benchmark_longmemeval/81_1_DIFF.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/ABLATION_PRIORITIES.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/BARRIER_AUDIT.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/CONFIG_PINNING.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/CONTAMINATION_ANALYSIS.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/FINAL_REVIEW.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/HARNESS.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/ISOLATION_AUDIT.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/JUDGE_DRIFT.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/PHASE3_WORK_ORDER.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/PORTABLE_ADVANTAGES.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/POST_MORTEM_81_1.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/TEARDOWN_AUDIT.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/abstention_sweep.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/dedup_sweep.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/dev_subset.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/failure_dataset.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/fixtures/dev_subset.json | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/fixtures/dev_subset_coverage.md | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/longmemeval_config_pin.json | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/min_score_sweep.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/taxonomy.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_ablation_priorities_scope.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_abstention_regression_gate.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_alias_audit_scope.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_composition_candidate_eligibility.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_config_pinning.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_contamination_analysis.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_dedup_sweep_integrity.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_dev_subset_coverage.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_failure_dataset.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_final_review_regression_guard.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_judge_drift_samples.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_memory_layer_doc_sync.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_min_score_sweep_integrity.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_phase3_work_order_consistency.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_portable_advantages_scope.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_portable_unique_knobs.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_post_mortem_81_1.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_retrieval_log_smoke.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_taxonomy.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_teardown_audit.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_temporal_subset_gate.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_top_k_sweep_integrity.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/test_weight_sweep_guard.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/top_k_sweep.py | longmemeval code/docs |
| ?? | tests/benchmark_longmemeval/weight_sweep.py | longmemeval code/docs |
| ?? | tests/benchmark_results/_r4_verify/verification_result.json | R4 verification artifact |
| ?? | tests/benchmark_results/baseline_67_8/VARIANCE.md | baseline 67.8 result |
| ?? | tests/benchmark_results/baseline_67_8/pre_fix_attempt/VARIANCE.md | baseline 67.8 result |
| ?? | tests/benchmark_results/baseline_67_8/pre_fix_attempt/run1/longmemeval_checkpoint.json | baseline 67.8 result |
| ?? | tests/benchmark_results/baseline_67_8/run1/longmemeval_checkpoint.json | baseline 67.8 result |
| ?? | tests/benchmark_results/composition/ADDITIVE.md | composition sweep result |
| ?? | tests/benchmark_results/composition/ANALYSIS.md | composition sweep result |
| ?? | tests/benchmark_results/dev_subset_baseline/VARIANCE.md | dev subset baseline result |
| ?? | tests/benchmark_results/dev_subset_baseline/failures.jsonl | dev subset baseline result |
| ?? | tests/benchmark_results/dev_subset_baseline/run1/longmemeval_checkpoint.json | dev subset baseline result |
| ?? | tests/benchmark_results/dev_subset_baseline/run1/longmemeval_results.jsonl | dev subset baseline result |
| ?? | tests/benchmark_results/dev_subset_baseline/run1/longmemeval_score.json | dev subset baseline result |
| ?? | tests/benchmark_results/dev_subset_baseline/run2/longmemeval_checkpoint.json | dev subset baseline result |
| ?? | tests/benchmark_results/dev_subset_baseline/run2/longmemeval_results.jsonl | dev subset baseline result |
| ?? | tests/benchmark_results/dev_subset_baseline/run2/longmemeval_score.json | dev subset baseline result |
| ?? | tests/benchmark_results/dev_subset_baseline/taxonomy.md | dev subset baseline result |
| ?? | tests/benchmark_results/dev_sweep_abstention/ANALYSIS.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/off/behavior_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/off/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/off/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/off/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/off/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/on/behavior_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/on/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/on/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/on/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/on/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_abstention/sweep_manifest.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_alias/AUDIT.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_alias/SKIPPED.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/ANALYSIS.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/current/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/current/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/current/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/current/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/sweep_manifest.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/tight_01/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/tight_01/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/tight_01/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_dedup/tight_01/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/ANALYSIS.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k05/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k05/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k05/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k05/retrieval_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k05/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k06/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k06/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k06/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k06/retrieval_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k06/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k07/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k07/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k07/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k07/retrieval_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k07/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k08/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k08/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k08/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k08/retrieval_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k08/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k09/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k09/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k09/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k09/retrieval_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/k09/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_max_returned/sweep_manifest.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/ANALYSIS.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.05/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.05/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.05/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.05/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.10/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.10/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.10/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.10/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.15/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.15/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.15/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.15/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.20/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.20/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.20/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.20/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.25/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.25/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.25/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/score_0.25/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_min_score/sweep_manifest.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_portable/SKIPPED.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/ANALYSIS.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/off/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/off/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/off/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/off/retrieval_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/off/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/on/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/on/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/on/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/on/retrieval_diagnostics.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/on/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_temporal/sweep_manifest.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/ANALYSIS.md | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/balanced/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/balanced/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/balanced/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/balanced/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/bm25_heavy/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/bm25_heavy/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/bm25_heavy/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/bm25_heavy/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/sweep_manifest.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/vector_heavy/longmemeval_checkpoint.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/vector_heavy/longmemeval_results.jsonl | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/vector_heavy/longmemeval_score.json | dev sweep result |
| ?? | tests/benchmark_results/dev_sweep_weights/vector_heavy/run_summary.json | dev sweep result |
| ?? | tests/benchmark_results/final/VARIANCE.md | final sweep result |
| ?? | tests/benchmark_results/longmemeval_optimized/longmemeval_fast_checkpoint.json | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_optimized/longmemeval_fast_results.jsonl | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_optimized_judge_restore/longmemeval_fast_checkpoint.json | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_optimized_judge_restore/longmemeval_fast_results.jsonl | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_optimized_retry/longmemeval_fast_checkpoint.json | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_optimized_retry/longmemeval_fast_results.jsonl | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_optimized_retry_k5/longmemeval_fast_checkpoint.json | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_optimized_retry_k5/longmemeval_fast_results.jsonl | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_repro_91ab1662/longmemeval_fast_checkpoint.json | optimized/repro eval result |
| ?? | tests/benchmark_results/longmemeval_repro_91ab1662/longmemeval_fast_results.jsonl | optimized/repro eval result |
| ?? | tests/benchmark_results/wave0_aligned_score_sanity.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_attribution/abl1_deterministic/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_attribution/abl1_deterministic/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_attribution/abl1_deterministic/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_attribution/abl1_deterministic/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_attribution/abl2_residual/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_attribution/abl2_residual/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_attribution/abl2_residual/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_attribution/abl2_residual/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_benchmark_alignment_decision.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_benchmark_injection_origin.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_benchmark_vs_production_injection.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_benchmark_workload_divergence.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_28_errors_diagnosis.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_abs_zero_diagnosis.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_aligned_score_sanity.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_dirty_tree_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus/evaluate_resume.pid | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_corrected/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_corrected/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_corrected/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_corrected/self_check.py | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_corrected/self_check_output.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_memory_rerun/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_memory_rerun/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_memory_rerun/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_scoped_rerun/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_scoped_rerun/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_full_corpus_scoped_rerun/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_memories_used_zero_diagnosis.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_path_a_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_routing_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_smoke/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_smoke/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_closure_smoke/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_corpus_check.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_d4_extraction_provider_override.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_db_outage_diagnosis.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_deterministic_mode_coverage.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_diagnostic_mini_task.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_diagnostic_revised.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_dual_injection_test.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_embedding_determinism_refs.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_eval_state_connection_check.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_extraction_cache_feasibility.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_extraction_determinism_refs.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_extraction_output_determinism.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_fingerprint_policy_decision.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_fingerprint_stability_measurement.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_fk_violation_trace.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_flip_forensics.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_aligned/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_baseline.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_baseline/.run_pid | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_baseline/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_baseline/result.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_baseline_plan.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_checkpoint_amended.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_checkpoint_merged.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_filtered_dataset.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_recovery_report.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_full_corpus_sanity_check.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_halt_escalation.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_harness_monitoring_fix.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_infrastructure_guardrails.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_infrastructure_recovery.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_ingestion_health_check/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_ingestion_health_check_rerun.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_ingestion_health_check_rerun/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_injection_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_injection_budget_check.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_injection_historical_diff.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_injection_trace.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_judge_determinism_refs.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_low_score_diagnosis.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_mech_c_correlation.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_memory_content_quality.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_memory_store_divergence.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_memory_store_stats.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_nondeterminism_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_option_a_revised_sanity_assessment.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_oracle_checkpoint_1.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_patch_caching_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_path_a_implementation.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_path_a_routing_diagnosis.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_path_a_smoke_test.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_per_category_gate_math.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_postmortem.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_preservation_fix.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_provider_routing_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_content_comparison.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_content_comparison_v2.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_1/extraction_log.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_1/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_1/memories.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_1/reset_result.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_1/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_2/extraction_log.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_2/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_2/memories.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_2/reset_result.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_2/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_3/extraction_log.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_3/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_3/memories.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_3/reset_result.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1/run_3/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_1/extraction_log.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_1/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_1/memories.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_1/reset_result.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_1/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_2/extraction_log.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_2/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_2/memories.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_2/reset_result.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_2/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_3/extraction_log.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_3/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_3/memories.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_3/reset_result.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_3/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_rerun_v1_clean/run_summary.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_restoration_delta.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_retrieval_ordering_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_retrieval_trace.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_score_distribution_check.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_selective_recovery_design.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_state_isolation_post_mortem.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_state_reset_audit.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_state_reset_audit_v2.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_state_reset_verification.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_subset_rerun_plan.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_subset_rerun_run1/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_subset_rerun_run1/wave0_subset_rerun_run1.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_subset_rerun_run2/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_subset_rerun_run2/wave0_subset_rerun_run2.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_subset_rerun_run3/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_subset_rerun_run3/wave0_subset_rerun_run3.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_supersede_state_trace.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_todo14_verification.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_1.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_1/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_1/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_1/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_1/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_2.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_2/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_2/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_2/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_2/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_3.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_3/longmemeval_checkpoint.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_3/longmemeval_results.jsonl | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_3/longmemeval_score.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_validation_run_3/run_metrics.json | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_variance_attribution_design.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_variance_attribution_results.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_voyage_drift_test.md | wave0 benchmark result |
| ?? | tests/benchmark_results/wave0_voyage_restoration_check.md | wave0 benchmark result |
| M | tests/test_l0_injection.py | longmemeval test |
| M | tests/test_longmemeval_evaluate.py | longmemeval test |
| M | tests/test_longmemeval_fast.py | longmemeval test |
| M | tests/test_longmemeval_ingest.py | longmemeval test |
| M | tests/test_longmemeval_runner.py | longmemeval test |

#### modified core code (25 rows)

| Status | Path | Reason |
|--------|------|--------|
| M | frontend/app/api/chat/route.ts | modified tracked core file |
| M | frontend/app/page.tsx | modified tracked core file |
| M | frontend/components/ThinkingIndicator.tsx | modified tracked core file |
| M | frontend/components/ToolCallBlock.tsx | modified tracked core file |
| M | frontend/hooks/useEventArchive.ts | modified tracked core file |
| M | frontend/lib/events.ts | modified tracked core file |
| M | frontend/next-env.d.ts | modified tracked core file |
| M | frontend/public/sw.js | modified tracked core file |
| M | orchestrator/catalog.py | modified tracked core file |
| M | orchestrator/config.py | modified tracked core file |
| M | orchestrator/council/engine.py | modified tracked core file |
| M | orchestrator/daemon.py | modified tracked core file |
| M | orchestrator/eval/longmemeval.py | modified tracked core file |
| M | orchestrator/eval/longmemeval_fast.py | modified tracked core file |
| M | orchestrator/eval/runner.py | modified tracked core file |
| M | orchestrator/main.py | modified tracked core file |
| M | orchestrator/model_router.py | modified tracked core file |
| M | orchestrator/models_cache.py | modified tracked core file |
| M | orchestrator/prompts.py | modified tracked core file |
| M | orchestrator/routes/conversations.py | modified tracked core file |
| M | orchestrator/tools/builtin.py | modified tracked core file |
| M | orchestrator/tools/completion.py | modified tracked core file |
| M | orchestrator/tools/executor.py | modified tracked core file |
| M | orchestrator/tools/retry.py | modified tracked core file |
| M | orchestrator/worker/worker.py | modified tracked core file |

#### docs/config/other (25 rows)

| Status | Path | Reason |
|--------|------|--------|
| M | MEMORY_LAYER.md | modified config/doc |
| M | TRIAGE.md | modified config/doc |
| ?? | frontend/__tests__/tool-call-log.test.ts | untracked config/asset |
| M | pyproject.toml | modified config/doc |
| M | tests/benchmark_results/competitor_comparison.json | modified tracked config/test |
| M | tests/benchmark_results/competitor_comparison.md | modified tracked config/test |
| M | tests/benchmark_results/longmemeval_tier2_fast.json | modified tracked config/test |
| M | tests/benchmark_results/longmemeval_tier2_fast.md | modified tracked config/test |
| M | tests/benchmark_results/longmemeval_tier3_final.json | modified tracked config/test |
| M | tests/benchmark_results/longmemeval_tier3_final.md | modified tracked config/test |
| M | tests/benchmark_results/task16_summary.md | modified tracked config/test |
| M | tests/conftest.py | modified tracked config/test |
| ?? | tests/memory/test_dedup_thresholds.py | memory subsystem test |
| ?? | tests/memory/test_extraction_determinism.py | memory subsystem test |
| ?? | tests/memory/test_retrieval_ordering.py | memory subsystem test |
| ?? | tests/memory/test_temporal_filter.py | memory subsystem test |
| M | tests/test_chat_stream.py | modified tracked config/test |
| M | tests/test_completion_with_tools.py | modified tracked config/test |
| M | tests/test_featured_models.py | modified tracked config/test |
| ?? | tests/test_routing.py | untracked test file |
| ?? | tests/tests/benchmark_results/wave0_rerun_v1/run_1/reset_result.json | untracked config/asset |
| ?? | tests/tests/benchmark_results/wave0_rerun_v1/run_2/reset_result.json | untracked config/asset |
| ?? | tests/tests/benchmark_results/wave0_rerun_v1/run_3/reset_result.json | untracked config/asset |
| ?? | tests/tests/benchmark_results/wave0_rerun_v1/run_summary.json | untracked config/asset |
| M | uv.lock | modified config/doc |

#### unknown (3 rows)

| Status | Path | Reason |
|--------|------|--------|
| ?? | test_preserve.py | ambiguous: preservation artifact unclear origin |
| ?? | tests/benchmark/test_provider_pinning.py | ambiguous: benchmark directory but not wave0 |
| ?? | tests/benchmark_results/contradiction_single_verify.md | ambiguous: contradiction verification doc unclear category |

### Evidence Files
- `.sisyphus/evidence/task-3-dirty-inventory.txt` — Complete raw status output (436 entries) and parsed bucket table
- `.sisyphus/evidence/task-3-unknown-files.txt` — Unknown bucket analysis (3 files with reasons)
- `.sisyphus/evidence/task-3-no-mutation.txt` — No-mutation proof with reflog, refs, stash evidence

### T3 Findings Summary
| Question | Answer |
|----------|--------|
| Total dirty files | 436 (45 modified + 391 untracked) |
| orchestrator/memory/** modified? | NO (not in dirty list — clean per T2) |
| Any git mutations by T3? | NO |
| Final disposition chosen? | NO — user must confirm/correct bucket assignments |

### Bucket Scheme Question for User

The current bucket scheme uses 6 categories. Before Phase 3 disposition work, **user confirmation is required**:

1. **parity-related** — Files tied explicitly to parity ship (3 files)
2. **advisor feature** — Advisor system files (10 files)
3. **Wave 0 archaeology** — Wave 0 benchmark results and artifacts (370 files)
4. **modified core code** — Modified tracked files in orchestrator/ and frontend/ (25 files)
5. **docs/config/other** — Docs, configs, test files, lockfiles (25 files)
6. **unknown** — Files that don't fit clearly (3 files)

**Please confirm or correct the bucket assignments before T4 disposition work begins.**

### Next Phase
T4: User confirmation on bucket scheme and Phase 3 disposition work

---

*Ledger entry created by T3 implementation - no git refs were modified*
*Evidence preserved in `.sisyphus/evidence/task-3-*.txt`*

---

## Phase 1: T4 Audit Chain and Phase 1 Halt (Completed — QA Repair Applied)

### Timestamp
2026-05-08T10:25:00Z; QA repair 2026-05-08

### QA Repair Note (2026-05-08)
Corrected ancestry direction errors: `eb691e5d` is the immediate parent of `d4e063fa` (1 behind, not 2 after); `86ad9cf2` is 2 behind (not 3 after). Corrected tag description: `harness-parity-shipped` already points to current HEAD `d4e063fa`. Corrected origin/main description. Removed false "tag advance" option from tag-policy question.

### T4 Statement
**T4 performed no git mutation; only ledger/evidence files were written.**

### Audit Chain — SHA Cross-Reference Results

#### SHA Table

| Cited SHA | Source | Represents | Matches harness-parity-shipped (d4e063fa)? | Status |
|-----------|--------|-----------|------------------------------------------|--------|
| d4e063fa | authoritative | harness-parity-shipped = current HEAD | YES (IS the reference) | VERIFIED |
| 290b7c02 | refs/heads/main | Local main branch | NO (diverged) | 15 commits ahead |
| fdf97a75 | refs/tags/pre-wave-1 | pre-wave-1 tag | NO (older) | NOT 07e9e6e7 |
| eb691e5d | reflog HEAD@{1} | consume production prompt commit | NO — immediate parent of d4e063fa (1 behind) | On detached chain |
| 86ad9cf2 | reflog HEAD@{2} | repair parity harness scope | NO — 2 behind d4e063fa | On detached chain |
| 07e9e6e7 | wave1-prompt-surface-changes.md:788 | Alleged pre-wave-1 anchor | NO (wrong SHA) | **DEFECT** |

#### Key Findings

1. **harness-parity-shipped = d4e063fa is VERIFIED.** `git rev-parse harness-parity-shipped` = `git rev-parse HEAD` = d4e063fa. Confirmed via direct command and reflog.

2. **pre-wave-1 tag = fdf97a75 (NOT 07e9e6e7).** Plan wave1-prompt-surface-changes.md:788 contains an incorrect SHA for the pre-wave-1 tag. This is a plan artifact defect requiring correction.

3. **Evidence files deleted by 86ad9cf2.** All cited evidence files (task-17 through task-21) referenced in the harness_parity_postmortem.md do not exist in the working tree. They were deleted by commit 86ad9cf2 ("fix(longmemeval): repair shipped parity harness scope").

4. **F1-F4 final verification NOT EXECUTED.** Wave1 plan's F1-F4 final verification (lines 893-896) are unchecked TODOs. Per plan: "Do NOT auto-proceed after verification. Wait for user's explicit approval." These have not been run.

5. **Consumer-path gate result = halt-harness-parity-required.** tests/benchmark_results/wave1_benchmark_consumer_path.md correctly documents that LongMemEval_S does not consume the production prompt surface.

### Hard Halt — No-Mutation Proof

| Check | Result |
|-------|--------|
| Reflog | 20 entries — unchanged from T1 baseline |
| Tags | harness-parity-shipped=d4e063fa, pre-wave-1=fdf97a75 — unchanged |
| Branches | main=290b7c02 — unchanged |
| Stashes | 2 stashes — unchanged |
| HEAD | d4e063fa — unchanged |
| New refs created? | NONE |

**Phase 1 HALT confirmed. T5 blocked pending user approval.**

### Evidence Files
- `.sisyphus/evidence/task-4-audit-chain.txt` — Full SHA cross-reference with command outputs
- `.sisyphus/evidence/task-4-hard-halt.txt` — No-mutation proof with reflog, show-ref, stash, branch evidence

### T4 Findings Summary

| Question | Answer |
|----------|--------|
| harness-parity-shipped SHA | d4e063fa (verified) |
| pre-wave-1 tag SHA | fdf97a75 (NOT 07e9e6e7) |
| Plan artifact defect found? | YES — wave1-prompt-surface-changes.md:788 cites wrong SHA |
| Evidence files exist as cited? | NO — 86ad9cf2 deleted 41 .sisyphus/evidence/ files |
| F1-F4 verification executed? | NO — still unchecked TODOs in wave1 plan |
| Any git mutations by T4? | NONE |
| Phase 1 halted? | YES — T5 blocked |

---

## Phase 1 Summary — User Decision Points

### What Was Approved
- Plan git-state-cleanup-post-parity-ship, Phase 1 tasks T1-T4

### What Moved
- Nothing. Phase 1 was read-only investigation. No git state was modified.

### What Is Dirty
- **436 dirty entries** in working tree: 45 modified tracked + 391 untracked
- `orchestrator/memory/**` is **NOT** in dirty list (clean per T2/T3)
- See T3 ledger section for full per-file bucket table

### What Suspicious Commits Show
1. **86ad9cf2** ("fix longmemeval: repair parity harness scope"): deleted 41 evidence files; touched 0 orchestrator/memory/** files
2. **290b7c02** ("docs(memory): restore roadmap baseline scope"): CONTRADICTS Atlas/status narrative; regressed from HALT to 67.8% pre-parity figure

### What Requires User Decision

#### 1. Authoritative Shipped-State Story
**Question:** Which SHA is the authoritative "shipped" state for this cleanup work?

Options:
- **A.** `harness-parity-shipped` tag = `d4e063fa` (current HEAD; linear chain: 290b7c02 → 86ad9cf2 → eb691e5d → d4e063fa)
- **B.** `main` branch HEAD = `290b7c02` (divergent; includes 290b7c02 which modified roadmap, plus other commits)
- **C.** Something else — please specify

Evidence: `git tag --points-at HEAD` returns `harness-parity-shipped`; `harness-parity-shipped` = `d4e063fa` = current HEAD.

#### 2. Tag-Policy / Audit-History Documentation
**Question:** The current state is: `harness-parity-shipped` tag = `d4e063fa` = HEAD; `origin/main` = `91ab1662`. The linear chain is: 290b7c02 → 86ad9cf2 → eb691e5d → d4e063fa (tag at HEAD). No tag advance is needed. What is the preferred documentation path?

Options:
- **A.** Ledger-only: document the current state as settled in this ledger (no new plan)
- **B.** Future tag-policy/audit-history plan: commission a separate lightweight plan to formalize tag conventions and document the full ancestry chain
- **C.** Other — please specify

**Note:** `origin/main` resolves to `91ab1662` (not N/A). T1 noted origin/main as "not locally known"; this likely meant it was not explicitly checked, not that it was absent. No fetch or remote command was performed by T4.

#### 3. Defect Plan vs. Ledger-Only
**Question:** Plan artifact defect found: wave1-prompt-surface-changes.md:788 cites wrong SHA (07e9e6e7 instead of actual fdf97a75). What is the preferred disposition?

Options:
- **A.** Commission a separate defect-investigation plan for wave1 plan correction
- **B.** Document defect in this ledger only (no separate plan)
- **C.** Other — please specify

#### 4. Phase Cadence Preference
**Question:** Phase 2 (T5-T9 mutation tasks) requires explicit approval. What is the preferred phase cadence?

Options:
- **A.** Proceed to Phase 2 now; review all mutations before committing
- **B.** Wait for explicit Phase 2 go-ahead after reading this report
- **C.** Other — please specify

### T3 Bucket Confirmation Still Required

T3 identified 436 dirty entries in 6 buckets. **User confirmation on bucket assignments was requested in T3 and remains outstanding.** Phase 3 disposition work is blocked on bucket confirmation.

### Phase 1 Status: COMPLETE — AWAITING USER APPROVAL FOR PHASE 2

**HALT: No further git mutations will occur until user explicitly approves Phase 2.**

T5 (first mutation-bearing task) requires explicit user approval after reading this report.

---

---

## Phase 2: T5 Anchor Detached Follow-up Commits (Completed — QA Repair Applied)

### Timestamp
2026-05-08T10:28:00Z

### Approval Basis
System continuation directive received 2026-05-08 — explicit approval to proceed into Phase 2/T5 without asking for permission. Recorded as approval source for T5 only.

### T5 Ledger Contract (Pre-Mutation)

#### Exact Mutation Command
```
GIT_MASTER=1 git branch harness-parity-followups HEAD
```

#### Expected Effect
Creates a local branch ref `harness-parity-followups` pointing to current detached HEAD (d4e063fa).
Does NOT move HEAD, tags, main, or any working tree files.
Does NOT checkout or switch any branch.

#### Exact Rollback Command (DOCUMENT ONLY — DO NOT RUN UNLESS MUTATION FAILS)
```
GIT_MASTER=1 git branch -d harness-parity-followups
```

#### Evidence That Will Prove Success
1. `GIT_MASTER=1 git rev-parse harness-parity-followups` equals `d4e063fa`
2. `GIT_MASTER=1 git rev-parse harness-parity-followups` equals `GIT_MASTER=1 git rev-parse HEAD` (pre-mutation)
3. `GIT_MASTER=1 git log --oneline harness-parity-followups -3` shows d4e063fa, eb691e5d, 86ad9cf2
4. `GIT_MASTER=1 git merge-base --is-ancestor 86ad9cf2 harness-parity-followups` — exit code 0 (is ancestor)
5. `GIT_MASTER=1 git merge-base --is-ancestor eb691e5d harness-parity-followups` — exit code 0 (is ancestor)
6. `GIT_MASTER=1 git merge-base --is-ancestor d4e063fa harness-parity-followups` — exit code 0 (is ancestor)
7. `GIT_MASTER=1 git tag --points-at HEAD` shows `harness-parity-shipped` (tag unchanged)
 8. `GIT_MASTER=1 git show-ref --head --tags --heads` shows 5 refs (one new branch ref added; all protected refs unchanged)
9. `GIT_MASTER=1 git stash list` still shows 2 stashes (unchanged)
10. `GIT_MASTER=1 git status --short --branch --untracked-files=all` shows no checkout/switch

### Pre-Mutation State (Captured)

| Ref | SHA | Type |
|-----|-----|------|
| HEAD | d4e063fa | commit (detached) |
| main | 290b7c02 | branch |
| harness-parity-shipped | d4e063fa | tag |
| pre-wave-1 | fdf97a75 | tag |

Stashes: 2 (wave0 preservation, build artifacts)
Refs count: 4 (HEAD, main, harness-parity-shipped, pre-wave-1)

### Evidence Files
- `.sisyphus/evidence/task-5-rollback-contract.txt` — Pre-mutation rollback contract
- `.sisyphus/evidence/task-5-anchor-followups.txt` — Command outputs and verification results

### Post-Mutation Verification Results

#### git reflog -1
```
d4e063fa HEAD@{0}: commit: fix(tests): remove stale benchmark_mode args from harness parity artifact
```

#### git rev-parse (all refs)
```
d4e063fa074dcce81c8af509ae2a45b62c645569  (harness-parity-followups)
d4e063fa074dcce81c8af509ae2a45b62c645569  (HEAD)
290b7c0282922f75ec5e4a041ffe5978c7ab7861  (main)
d4e063fa074dcce81c8af509ae2a45b62c645569  (harness-parity-shipped)
fdf97a750d549c8ba40d9ff78fd92c135149448c  (pre-wave-1)
```

#### git log --oneline harness-parity-followups -3
```
d4e063fa fix(tests): remove stale benchmark_mode args from harness parity artifact
eb691e5d test(longmemeval): consume production prompt in parity answer
86ad9cf2 fix(longmemeval): repair shipped parity harness scope
```

#### Ancestor checks
- 86ad9cf2 ancestor of harness-parity-followups: **PASS**
- eb691e5d ancestor of harness-parity-followups: **PASS**
- d4e063fa ancestor of harness-parity-followups: **PASS** (reflexive)

#### git tag --points-at HEAD
```
harness-parity-shipped
```
Result: tag unchanged.

#### git show-ref --head --tags --heads (post-mutation)
```
d4e063fa074dcce81c8af509ae2a45b62c645569 HEAD
d4e063fa074dcce81c8af509ae2a45b62c645569 refs/heads/harness-parity-followups
290b7c0282922f75ec5e4a041ffe5978c7ab7861 refs/heads/main
d4e063fa074dcce81c8af509ae2a45b62c645569 refs/tags/harness-parity-shipped
fdf97a750d549c8ba40d9ff78fd92c135149448c refs/tags/pre-wave-1
```
Refs after T5: **5 total** (was 4 before; one new branch ref added)

#### git stash list
```
stash@{0}: On main: wave0-task1-memory-preserve-20260501T094245Z
stash@{1}: On main: build artifacts
```
Stashes: **2 (unchanged)**

#### git status --short --branch --untracked-files=all (summary)
```
## HEAD (no branch)
```
Result: HEAD is still detached. No checkout/switch occurred.

### T5 Final State Summary

| Ref | SHA | Status |
|-----|-----|--------|
| HEAD | d4e063fa | Detached (unchanged) |
| harness-parity-followups | d4e063fa | **NEW** |
| main | 290b7c02 | Unchanged |
| harness-parity-shipped | d4e063fa | Unchanged |
| pre-wave-1 | fdf97a75 | Unchanged |

- Stashes: 2 (unchanged)
- Refs total: 5 (was 4; one new branch ref added)
- Checkout/switch: none

### QA Repair Summary
- Changed title from "(In Progress)" to "(Completed — QA Repair Applied)"
- Fixed item 8: "shows 4 refs" corrected to "shows 5 refs (one new branch ref added; all protected refs unchanged)"
- Added full post-mutation verification subsection with all required command outputs

---

*Ledger entry created by T5 implementation - rollback contract written before mutation; post-mutation results added during QA repair*
*Evidence preserved in `.sisyphus/evidence/task-5-rollback-contract.txt` and `.sisyphus/evidence/task-5-anchor-followups.txt`*

*Ledger entry created by T4 implementation - no git refs were modified*
*Evidence preserved in `.sisyphus/evidence/task-4-audit-chain.txt` and `.sisyphus/evidence/task-4-hard-halt.txt`*

---

## Phase 2: T6 Protected Tags Verification (Completed)

### Timestamp
2026-05-08T10:40:00Z

### T6 Statement
**T6 performed no git mutation; only ledger/evidence files were written.**

### QA Repair Note
**2026-05-08** — Fixed command headings in `.sisyphus/evidence/task-6-tags-unchanged.txt`: all `Command: git ...` headings now prefixed with `GIT_MASTER=1` (5 headings corrected: rev-parse, show-ref, branch -avv, tag --points-at HEAD, stash list). No git mutations performed. Protected refs verified unchanged.

### Protected Tag Verification

#### Tag SHA Comparison

| Tag | Expected SHA | Actual SHA | Match |
|-----|-------------|------------|-------|
| harness-parity-shipped | d4e063fa074dcce81c8af509ae2a45b62c645569 | d4e063fa074dcce81c8af509ae2a45b62c645569 | YES |
| pre-wave-1 | fdf97a750d549c8ba40d9ff78fd92c135149448c | fdf97a750d549c8ba40d9ff78fd92c135149448c | YES |

### Current State After T5

| Ref | SHA | Note |
|-----|-----|------|
| HEAD | d4e063fa | Detached (unchanged from T1) |
| harness-parity-followups | d4e063fa | New T5 branch |
| main | 290b7c02 | 15 commits ahead of origin/main |
| origin/main | 91ab1662 | Remote baseline |
| harness-parity-shipped | d4e063fa | Protected tag — VERIFIED UNCHANGED |
| pre-wave-1 | fdf97a75 | Protected tag — VERIFIED UNCHANGED |

### Stash State
- stash@{0}: On main: wave0-task1-memory-preserve-20260501T094245Z
- stash@{1}: On main: build artifacts
- **2 stashes (unchanged)**

### Tag Movement Assertion
T6 performed **NO tag movement**. Protected tags remain at Phase 1/T4 snapshot values.

### Halt Status
**T6 verification PASSED.** Protected tags unchanged. No anomaly detected. Halt condition NOT triggered.

### Evidence Files
- `.sisyphus/evidence/task-6-tags-unchanged.txt` — Tag/ref verification evidence
- `.sisyphus/evidence/task-6-phase2-checkpoint.txt` — Branch anchor state, tag unchanged assertion, status summary, approval basis

### T6 Findings Summary

| Question | Answer |
|----------|--------|
| harness-parity-shipped unchanged? | YES (d4e063fa) |
| pre-wave-1 unchanged? | YES (fdf97a75) |
| Tag movement by T6? | NONE |
| HEAD still detached? | YES |
| harness-parity-followups exists? | YES at d4e063fa |
| main SHA | 290b7c02 |
| Stashes | 2 |
| Anomaly detected? | NO |
| T6 success? | YES — proceed to Phase 3 under continuation directive |

---

*Ledger entry created by T6 implementation - no git refs were modified*
*Evidence preserved in `.sisyphus/evidence/task-6-tags-unchanged.txt` and `.sisyphus/evidence/task-6-phase2-checkpoint.txt`*
