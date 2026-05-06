# T12 — Harness Parity Inline Extraction Check

**Date**: 2026-05-06T10:57:32 (initial run) | **Rerun**: 2026-05-06T11:06:55
**Script**: `tests/longmemeval/t12_inline_extraction_check.py`

## Objective

Verify synchronous inline production extraction for haystack messages across a bounded 3-question sample spanning IE-user, MR, and TR categories. Prove zero pre-extracted/oracle memory loading and same-user retrieval.

## Sample Questions

| # | Question ID | Category | Question Type | Fresh ID |
|---|-------------|----------|---------------|----------|
| 1 | b86304ba | IE-user | single-session-user | b86304ba_t12ie |
| 2 | 28dc39ac | MR | multi-session | 28dc39ac_t12mr |
| 3 | 8c18457d | TR | temporal-reasoning | 8c18457d_t12tr |

## Per-Question Results

### 1. IE-user (b86304ba)

| Metric | Value |
|--------|-------|
| Synthetic User | a22c9f41-4d1f-5622-91b1-1b46553ac386 |
| Haystack Sessions | 3 (27 messages) |
| Extraction Invocations | 3 |
| Extraction Completed | 2 |
| Extraction Empty | 1 |
| Extraction Errors | 0 |
| Memories Created | 10 |
| Memories Retrieved | 1 |
| Provenance Intersection | 1 |
| Same-User Retrieval | PASS (1/1 same user) |
| Overall Gates | **PASS** |

### 2. MR (28dc39ac)

| Metric | Value |
|--------|-------|
| Synthetic User | 3af2c441-6d31-5d63-be17-2e6b4ed3bbd6 |
| Haystack Sessions | 3 (36 messages) |
| Extraction Invocations | 3 |
| Extraction Completed | 1 |
| Extraction Empty | 2 |
| Extraction Errors | 0 |
| Memories Created | 6 |
| Memories Retrieved | 3 |
| Provenance Intersection | 3 |
| Same-User Retrieval | PASS (3/3 same user) |
| Overall Gates | **PASS** |

### 3. TR (8c18457d)

| Metric | Value |
|--------|-------|
| Synthetic User | ed47d6ee-93a5-5bbf-8de4-651a74136aa3 |
| Haystack Sessions | 3 (33 messages) |
| Extraction Invocations | 3 |
| Extraction Completed | 1 |
| Extraction Empty | 2 |
| Extraction Errors | 0 |
| Memories Created | 4 |
| Memories Retrieved | 1 |
| Provenance Intersection | 1 |
| Same-User Retrieval | PASS (1/1 same user) |
| Overall Gates | **PASS** |

## Gate Definitions

Each sample passes the inline extraction sanity check when ALL gates are green:

| Gate | Meaning |
|------|---------|
| `extraction_invoked` | At least one session called `process_extraction` inline |
| `extraction_completed_nonzero` | At least one session completed extraction successfully |
| `retrieval_latency_ok` | Retrieval completed in < 1500ms |
| `same_user_retrieval` | All retrieved memories belong to the sample synthetic user |
| `created_memories_belong_to_synthetic_user` | All created memories have correct `user_id` |
| `current_run_provenance` | If memories retrieved, they intersect current-run created IDs; if 0 retrieved, vacuously true |

The `current_run_provenance` gate is **conditional**: it passes when no memories are retrieved (vacuously true) OR when retrieved memories intersect with the set of memories created by the current run's inline extraction.

## Inline Extraction Verification

**Confirmed Working**: All 3 questions exercised synchronous inline `process_extraction()` via `ingest_session()`. No arq/background job, no debounce, no async queue.

| Question | Category | Extraction Invoked | Extraction Completed | Memories Created | Memories Retrieved |
|----------|----------|-------------------|---------------------|-------------------|-------------------|
| b86304ba | IE-user | 3 | 2 | 10 | 1 |
| 28dc39ac | MR | 3 | 1 | 6 | 3 |
| 8c18457d | TR | 3 | 1 | 4 | 1 |

## Pre-Extraction Oracle Load Check

**Confirmed: No Pre-Extracted / Oracle Memory Loading**

1. **Inline extraction only**: `ingest_session()` calls `process_extraction()` synchronously — awaiting completion before returning. No ARQ worker, no 30-second debounce.
2. **Fresh synthetic users**: Each question uses `uuid.uuid5(SYNTHETIC_USER_NAMESPACE, fresh_question_id)` — unique per run, scoped cleanup applied before each run.
3. **benchmark_extraction.py**: Exists as a separate extraction benchmark; confirmed **not imported** by `tests/longmemeval/**`.

## Overall Result

**PASS — 3/3 questions passed all gates**

| Question | Category | Extraction | Same-User | Provenance | Overall |
|----------|----------|-----------|-----------|------------|---------|
| b86304ba | IE-user | PASS | PASS | PASS | **PASS** |
| 28dc39ac | MR | PASS | PASS | PASS | **PASS** |
| 8c18457d | TR | PASS | PASS | PASS | **PASS** |

**Atlas verification**: `overall_pass: true` — script exits 0.

---

## T12 Note on `tests/benchmark_extraction.py`

`tests/benchmark_extraction.py` is a separate extraction benchmark (v2.4, 1288 lines). It is **explicitly out of scope** for T12 and the broader harness-parity task. It was not rebuilt, not run, and not imported. Discovery/rebuild is separately commissioned.

---

_Note: answer/judge calls mocked after prompt capture. Extraction, embedding, and retrieval used real providers (GPT-4o-mini, Voyage AI)._
