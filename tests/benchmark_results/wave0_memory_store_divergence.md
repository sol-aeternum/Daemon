# Wave 0 Memory Store Divergence Analysis

## Executive Summary

This document analyzes whether the observed 4–6 percentage point (pp) answer-hash variance across Wave 0 ABL runs can be attributed to memory-store ingestion/state divergence (B.1), retrieval nondeterminism (B.2), or is merely a measurement artifact (B.3).

**Current Decision: INSUFFICIENT EVIDENCE — fresh targeted ingestion-only snapshot experiment required.**

The existing Wave 0 validation artifacts are insufficient to make a B.1/B.2/B.3 classification because all three validation runs stored **zero memories** (`total_memories_at_end = 0`, `errored = 2079` across all sessions). Without any memory content stored, there is no memory-store state to compare between runs, and therefore no basis to distinguish between ingestion divergence, retrieval nondeterminism, or measurement artifact.

---

## Existing Artifacts

### Available Artifact Sets

| Artifact Set | Location | Contents |
|---|---|---|
| ABL-1 deterministic | `tests/benchmark_results/wave0_attribution/abl1_deterministic/` | `run_metrics.json`, `longmemeval_checkpoint.json`, `longmemeval_results.jsonl`, `longmemeval_score.json` |
| ABL-2 residual | `tests/benchmark_results/wave0_attribution/abl2_residual/` | `run_metrics.json`, `longmemeval_checkpoint.json`, `longmemeval_results.jsonl`, `longmemeval_score.json` |
| Validation run 1 | `tests/benchmark_results/wave0_validation_run_1/` | `run_metrics.json`, `longmemeval_checkpoint.json`, `longmemeval_results.jsonl`, `longmemeval_score.json` |
| Validation run 2 | `tests/benchmark_results/wave0_validation_run_2/` | `run_metrics.json`, `longmemeval_checkpoint.json`, `longmemeval_results.jsonl`, `longmemeval_score.json` |
| Validation run 3 | `tests/benchmark_results/wave0_validation_run_3/` | `run_metrics.json`, `longmemeval_checkpoint.json`, `longmemeval_results.jsonl`, `longmemeval_score.json` |

### What Artifacts Contain

Each artifact set includes:
- `run_metrics.json` — aggregate metrics including `total_memories_at_end` counts
- `longmemeval_checkpoint.json` — per-question checkpoint data including `retrieved_memory_ids`
- `longmemeval_results.jsonl` — per-question results with retrieved memory IDs and scores
- `longmemeval_score.json` — scoring output

These artifacts contain **per-question `retrieved_memory_ids`** and **total memory counts**, enabling retrieval-level comparison between runs.

### What Artifacts Do NOT Contain

No artifact set contains:
- Full memory-table snapshots or content tuples from the `user_memories` table
- `(content, category, slot, confidence)` tuples suitable for byte-identical comparison
- Memory-content snapshots (glob for `**/*mem*snapshot*` returned no matches)

The retrieved-memory IDs in the artifacts are insufficient for memory-store comparison because they only reflect what was retrieved at query time — not the underlying stored state.

---

## Why Current Artifacts Are Insufficient for B.1/B.2/B.3 Classification

### Zero Memories Stored

All three validation runs (runs 1–3) produced:
- `total_memories_at_end` active/historical/total = **0**
- `errored = 2079` extraction failures across all sessions

This means the memory pipeline failed to store any memories during validation runs. With zero stored memories, there is no memory-store state to compare between runs. The retrieved memory IDs in the artifacts reflect an empty store, not divergence in content.

### No Baseline for Comparison

ABL runs (abl1, abl2) stored memories successfully, but the **existing artifacts do not include memory-table content snapshots**. Without a byte-identical comparison of stored `(content, category, slot, confidence)` tuples, it is impossible to determine whether:

- **B.1 applies**: The stores differ in count or content (ingestion/state divergence)
- **B.2 applies**: The stores are identical but retrieval returns different IDs (retrieval nondeterminism)
- **B.3 applies**: The stores and retrieved IDs are identical, indicating the variance is a measurement artifact

### Reset Audit Findings

Prior reset audit findings indicate:
- `tables_cleared` is not recorded in metrics, so clean-state cannot be verified from artifacts alone
- `retrieval_log` async bleed can recreate rows after cleanup
- Stale-checkpoint / reset interactions remain a documented risk

These findings further complicate retrospective analysis and reinforce that existing artifacts cannot support a clean comparison.

---

## Benchmark User Identifier

The canonical benchmark user for Wave 0 is:

```
12345678-1234-5678-1234-567812345678
```

This UUID identifies the benchmark user whose memories are the subject of the divergence analysis.

---

## Minimal Harness-Only Snapshot Procedure

The following procedure is sufficient to establish B.1/B.2/B.3 classification. **It is a harness-only experiment and does not require modifications to production code.**

### Prerequisites

- Clean database state (reset before each run)
- `dev_subset.json` as the ingestion target
- SHA256 hashing utility for tuple serialization

### Steps

1. **Clean reset**: Reset the database to a known-clean state before each run.

2. **Ingestion-only run**: Run memory ingestion on `dev_subset.json` **without any query/retrieval**. Record:
   - Count of memories stored in `user_memories` table
   - Snapshot of all `(content, category, slot, confidence)` tuples
   - SHA256 of the sorted tuple set (for content-level comparison)

3. **Repeat from clean reset**: Perform a second identical ingestion-only run from a clean reset, producing an independent snapshot.

4. **Compare**:
   - **If counts differ or tuple SHA256s differ** → **B.1**: ingestion/state divergence confirmed
   - **If counts and tuple SHA256s are identical but `retrieved_memory_ids` differ across re-runs** → **B.2**: retrieval nondeterminism confirmed
   - **If counts and tuple SHA256s are identical AND `retrieved_memory_ids` are identical across re-runs** → **B.3**: retrieval is deterministic and prior halt was a measurement artifact

### What This Procedure Does NOT Require

- No production code changes
- No modifications to `orchestrator/memory/`
- No new SSE events or API changes
- Only harness-level orchestration and database comparison

---

## Explicit Current Decision

**INSUFFICIENT EVIDENCE — fresh targeted ingestion-only snapshot experiment required.**

The existing Wave 0 artifacts cannot support a B.1/B.2/B.3 classification because:

1. Validation runs 1–3 stored **zero memories**, leaving no state to compare
2. ABL artifacts contain retrieved IDs but **no memory-content snapshots**
3. No byte-identical memory-store comparison is possible with current artifacts
4. Reset audit findings confirm that clean-state cannot be verified retrospectively from metrics alone

A targeted ingestion-only snapshot experiment as described above is the minimum required to establish B.1/B.2/B.3.

---

## Important Note on Answer-Hash Variance

> **The current 4 pp / 6 pp answer-hash variance is NOT itself a memory-store divergence measurement.**

Answer-hash variance measures differences in **final model outputs** (completion text hashed for comparison). It captures the end-to-end effect of any upstream nondeterminism — including:

- Embedding nondeterminism (voyage-4-lite has no seed/fingerprint support)
- Answer temperature variation
- Retrieval nondeterminism
- Memory ingestion nondeterminism

The answer-hash variance alone cannot disambiguate which source is responsible. It is a **symptom**, not a **diagnosis**. The memory-store comparison procedure above is required to isolate the memory subsystem as the cause (or rule it out).

---

## Summary Table

| Question | Answer |
|---|---|
| Can existing artifacts classify B.1/B.2/B.3? | **No** — zero memories stored in validation runs, no content snapshots in ABL runs |
| What is the current decision? | **INSUFFICIENT EVIDENCE** |
| What is needed? | Fresh ingestion-only snapshot experiment with `(content, category, slot, confidence)` tuple comparison |
| Is B.1/B.2/B.3 proven? | **No** |
| Are production code changes required? | **No** — harness-only experiment |
