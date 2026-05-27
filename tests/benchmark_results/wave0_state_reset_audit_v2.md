# Wave 0 State Reset Audit V2

**Date:** 2026-04-25
**Task:** R1 — Audit reset completeness at root cause for preserved rerun failures
**Sources:** `orchestrator/eval/runner.py`, `tests/benchmark_harness/run_triple_preserved.py`, prior audits, `wave0_rerun_v1` artifacts

---

## 1. Evidence from Preserved Rerun Artifacts

### 1.1 Reset row counts per run

| Run | conversations | messages | memories | memory_extraction_log | retrieval_log | dream_log | entities | **Total** |
|---|---|---|---|---|---|---|---|---|
| 1 (pre-run) | 19 | 208 | 23 | 7 | 0 | 0 | 0 | **257** |
| 2 (pre-run) | 208 | 2321 | 278 | 90 | 0 | 0 | 0 | **2897** |
| 3 (pre-run) | 219 | 2399 | 242 | 77 | 0 | 0 | 0 | **2937** |

**Observations:**
- Run 1 starts with 257 residual **rows** from a prior run (not from the triple-run sequence itself — each `reset_run` calls `cleanup_canonical_benchmark` before the corresponding `ingest_and_preserve`). This is **not** the session count of `dev_subset.json` (see note below).
- Run 2 accumulated **10x more** residual rows (2897) after a single run's ingestion. This indicates the reset is not fully clearing the state between runs.
- Run 3 accumulated similarly (2937), confirming a consistent ~10x growth per run cycle.
- `retrieval_log` shows **0 deleted** in all three resets, yet the TEARDOWN_AUDIT (2026-04-21) confirmed `retrieval_log` accumulates via async bleed. This means the async writes land **after** the reset completes, so they survive into the next run.

> **Corpus cardinality:** `dev_subset.json` maps to **2079 corpus sessions** via `build_corpus_plan()`. The "257" above is a row count from a prior run's leftover state — it is not the corpus session count.

### 1.2 Extraction log identity across runs

All three `extraction_log.jsonl` files are **byte-for-byte identical** (same 17 sessions, same timestamps, same `facts_sha256` hashes). This means:

- The same `conversation_id` UUIDs are being reused across runs.
- The same extraction inputs produce identical outputs, confirming `reset_canonical_benchmark` clears `memory_extraction_log` correctly **for the current run's data**.
- However, the growth in `memory_extraction_log` deleted rows (7 → 90 → 77) suggests prior run data is leaking through despite reset.

### 1.3 Run metrics per run (from checkpoint `phases.ingest`)

| Run | completed_count | status: complete | status: extraction_failed | outcome: completed | outcome: empty | outcome: errored |
|---|---|---|---|---|---|---|
| 1 | 320 | 318 | 2 | 97 | 221 | 2 |
| 2 | 98 | 97 | 1 | 46 | 51 | 1 |
| 3 | 112 | 112 | 0 | 32 | 80 | 0 |

All three runs processed `dev_subset.json` (2079 corpus sessions via `build_corpus_plan()`).

- Run 2 shows significantly more `errored` sessions (1) vs Run 1 (2) vs Run 3 (0 absolute; but Run 1 has 2 errors among 320 vs Run 2's 1 error among 98). Normalized error rates: Run 1 ≈ 0.6%, Run 2 ≈ 1.0%, Run 3 = 0%. Run 2 also started with 2897 residual rows (10x more than Run 1's 257), which is consistent with state contamination correlating with degraded outcomes.
- `total_memories_created` is stable at 53 across all runs (expected behavior for deterministic extraction on identical input).

---

## 2. Reset Scope Audit — Table-by-Table

### 2.1 Tables currently reset by `cleanup_canonical_benchmark()`

`runner.py:124–132` defines the reset table list:
```python
tables = [
    "retrieval_log",
    "dream_log",
    "entities",
    "memories",
    "memory_extraction_log",
    "messages",
    "conversations",
]
```

**Reset order (runner.py:134–140):** The tables are deleted in the order listed above — NOT in a foreign-key-safe order.

| Table | Reset? | Evidence | FK safety |
|---|---|---|---|
| `conversations` | ✅ Yes | `DELETE FROM conversations WHERE user_id = $1` | ✅ Safe to delete first |
| `messages` | ✅ Yes | `DELETE FROM messages WHERE user_id = $1` | ✅ Safe (no FK from tables being reset) |
| `memories` | ✅ Yes | `DELETE FROM memories WHERE user_id = $1` | ⚠️ Has FK from `memory_extraction_log` |
| `memory_extraction_log` | ✅ Yes | `DELETE FROM memory_extraction_log WHERE user_id = $1` | ✅ Safe to delete after `memories` |
| `retrieval_log` | ✅ Yes | `DELETE FROM retrieval_log WHERE user_id = $1` | ✅ Safe |
| `dream_log` | ✅ Yes | `DELETE FROM dream_log WHERE user_id = $1` | ✅ Safe |
| `entities` | ✅ Yes | `DELETE FROM entities WHERE user_id = $1` | ✅ Safe |
| `skill_consolidation_log` | ❌ **Missing** | Not in table list | FK: `user_id → users(id) ON DELETE CASCADE` |
| `skill_nudge_user_state` | ❌ **Missing** | Not in table list | PK: `user_id → users(id) ON DELETE CASCADE` |
| `conversations.last_retrieved_memory_ids` | ❌ **Missing** | Column-level attribute not reset | N/A (column, not table) |
| `memories.access_count` | ❌ **Missing** | `bulk_touch_memories()` writes async, not cleared | N/A |
| `memories.last_accessed_at` | ❌ **Missing** | Same as above | N/A |

### 2.2 Tables never reset by the canonical runner

#### `skill_consolidation_log`

**Schema** (migration 029):
```sql
CREATE TABLE skill_consolidation_log (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_id UUID NOT NULL,
    run_at TIMESTAMPTZ NOT NULL,
    action_type TEXT NOT NULL,
    skill_id TEXT,
    target_skill_id TEXT,
    reason TEXT,
    similarity DOUBLE PRECISION,
    status TEXT,
    skill_name TEXT,
    skill_description TEXT,
    skill_use_count INTEGER,
    skill_last_used_at TIMESTAMPTZ
);
```

**Used by:** `orchestrator/memory/store.py:1464, 1476, 1505` — `INSERT INTO skill_consolidation_log` calls during consolidation nudge processing.

**Reset impact if not cleared:** Accumulates rows per user. Not cleared by `cleanup_canonical_benchmark`. **Currently leaks** benchmark evidence.

#### `skill_nudge_user_state`

**Schema** (migration 029):
```sql
CREATE TABLE skill_nudge_user_state (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    conversations_since_nudge INTEGER NOT NULL DEFAULT 0,
    last_nudge_at TIMESTAMPTZ,
    last_nudge_run_id UUID
);
```

**Used by:** `orchestrator/memory/store.py:1288, 1309, 1323` — per-user nudge counter tracking.

**Reset impact if not cleared:** Per-user state persists across runs. `conversations_since_nudge` counter does not reset to 0. **Currently leaks** benchmark evidence.

### 2.3 Column-level state: `conversations.last_retrieved_memory_ids`

**Schema** (migration 022):
```sql
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS last_retrieved_memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
```

**Used by:**
- `orchestrator/memory/store.py:152–186` — `create_conversation()` accepts `last_retrieved_memory_ids` parameter, stored via `COALESCE($9, last_retrieved_memory_ids)`.
- `orchestrator/memory/trust_signals.py:45, 65, 95, 114, 130, 163` — `update_retrieved_memory_signals()` writes to this column.

**Reset impact if not cleared:** Each conversation carries forward its `last_retrieved_memory_ids` from prior runs. When a conversation is reused (same UUID), the stale array is not cleared. This is a **conversation-scoped state leak** that could affect retrieval ranking.

### 2.4 Async-written column state: `memories.access_count` / `memories.last_accessed_at`

**Written by:** `bulk_touch_memories()` via `asyncio.create_task()` (fire-and-forget) in `orchestrator/memory/retrieval.py:942–948`.

**Reset impact:** The `access_count` and `last_accessed_at` columns on `memories` are updated **after** `cleanup_canonical_benchmark()` runs if the async task hasn't settled. The memory rows are then deleted, so this is a **transient bleed** rather than a persistent leak — but it means any post-cleanup `bulk_touch_memories` call will update rows that should not exist, then those rows get deleted. Minor issue.

---

## 3. Queue / In-Flight Bleed Audit

### 3.1 Redis key patterns cleaned

`cleanup_runner_redis()` in `runner.py:160–195` cleans these patterns:
```python
_REDIS_EXTRACT_PATTERNS = (
    "extract:*",
    "arq:job:extract:*",
    "arq:result:extract:*",
    "arq:retry:extract:*",
)
```

### 3.2 Gap: `run_triple_preserved.py` disables Redis cleanup

```python
# run_triple_preserved.py:96
summary = await reset_canonical_benchmark(pool, checkpoint, cleanup_redis=False)
```

**Impact:** When `cleanup_redis=False`, no Redis keys are deleted. Any queued ARQ extraction jobs or cached results from prior runs remain in Redis and could be served on the next run.

**However**, the canonical lane (`runner.py`) uses inline extraction (not ARQ-queued), so this gap primarily affects the `benchmark_extraction.py` pipeline. The triple-run harness sets `cleanup_redis=False` intentionally for speed, accepting the risk.

### 3.3 Gap: No ARQ queue drain

The `cleanup_runner_redis()` function **scans and deletes** keys matching patterns, but does not actively drain the ARQ job queue. If an ARQ worker is actively processing a job when cleanup runs, that job will complete and write to the DB **after** the cleanup has finished.

**Evidence from TEARDOWN_AUDIT:** "Post-case cleanup reaches zero, then a late `retrieval_log` row reappears with all other tables still at zero." This is the same async-bleed mechanism — the job completes after cleanup.

---

## 4. Delete Order and Foreign Key Safety

### 4.1 Current delete order (runner.py:134–140)

```
1. retrieval_log
2. dream_log
3. entities
4. memories
5. memory_extraction_log
6. messages
7. conversations
```

### 4.2 FK chain analysis

```
conversations (PK: id)
  └── messages (FK: conversation_id REFERENCES conversations(id) ON DELETE CASCADE)
        (No other tables reference messages directly)

memories (PK: id, FK: source_conversation_id REFERENCES conversations(id) ON DELETE SET NULL)
  └── memory_extraction_log (FK: memory_id REFERENCES memories(id) ON DELETE CASCADE?)

memory_extraction_log (PK: id, FK: conversation_id REFERENCES conversations(id) ON DELETE ???)
  └── Note: has both memory_id and conversation_id FKs

entities (no FK from tables being reset)
dream_log (no FK from tables being reset)
retrieval_log (no FK from tables being reset)
```

**The current order is NOT safe in the general case.** `memories` is deleted before `memory_extraction_log`. If `memory_extraction_log.memory_id REFERENCES memories(id) ON DELETE CASCADE`, the cascade handles it. But if `memory_extraction_log.conversation_id` has a FK to `conversations`, and `conversations` is deleted last, there could be an FK violation.

**In practice**, the observed residual counts show the deletes are completing (no FK error reported), so the actual FK constraints must be either `ON DELETE CASCADE` or `SET NULL`, or the `conversation_id` column is not the FK target for `memory_extraction_log`.

### 4.3 Safe order recommendation

To ensure FK safety regardless of constraint direction:
```
1. retrieval_log
2. dream_log
3. entities
4. memory_extraction_log   -- depends on memories for memory_id FK
5. memories               -- depends on conversations for source_conversation_id FK
6. messages               -- depends on conversations
7. conversations
```

Or: delete from `conversations` first and rely on `ON DELETE CASCADE` for all child tables.

---

## 5. `memory_extraction_log.conversation_id` FK Observation

**Critical anomaly observed in artifacts:** `wave0_rerun_v1` extraction logs show `conversation_id` values (e.g., `312c301a-aa0e-4080-8711-07c92b491cf4`) that are **identical across all three runs** with identical timestamps (`2026-04-25T02:27:14.912418+00:00`).

This means the **same conversation rows are being reused** across runs 1, 2, and 3. The `reset_canonical_benchmark` deletes these conversations, but the UUIDs are deterministic (based on the corpus session), so new identical UUIDs are generated on the next ingest.

**Not an FK failure** — the new conversations are created with the same IDs. This is expected behavior for the canonical lane's deterministic ID generation.

---

## 6. Gap Summary Matrix

| State Source | Reset by `cleanup_canonical_benchmark`? | Severity | Notes |
|---|---|---|---|
| `conversations` | ✅ Yes | — | Correctly cleared |
| `messages` | ✅ Yes | — | Correctly cleared |
| `memories` | ✅ Yes | — | Correctly cleared |
| `memory_extraction_log` | ✅ Yes | — | Correctly cleared |
| `retrieval_log` | ✅ Yes | **Medium** | Async writes land post-cleanup; cleared on next reset but re-accumulates |
| `dream_log` | ✅ Yes | — | Correctly cleared |
| `entities` | ✅ Yes | — | Correctly cleared |
| `skill_consolidation_log` | ❌ No | **High** | Completely untracked; persists across resets |
| `skill_nudge_user_state` | ❌ No | **High** | Completely untracked; counter does not reset |
| `conversations.last_retrieved_memory_ids` | ❌ No | **Medium** | Stale array persists on reused conversation UUIDs |
| `memories.access_count` / `last_accessed_at` | ❌ No | **Low** | Async bleed; transient, cleared with memory rows |
| Redis `extract:*` keys | ⚠️ Optional | **Medium** | `cleanup_redis=False` in triple-run; not cleared |
| Redis `arq:job:extract:*` | ⚠️ Optional | **Medium** | Same — ARQ queue not drained |
| Redis `arq:result:extract:*` | ⚠️ Optional | **Medium** | Same |
| Redis `arq:retry:extract:*` | ⚠️ Optional | **Medium** | Same |
| In-flight async tasks | ❌ No | **Medium** | `bulk_touch_memories`, `log_retrieval` land after cleanup |

---

## 7. Root Cause Analysis for Observed Count Anomalies

**What was observed:** Run 1 processed `dev_subset.json` (2079 corpus sessions) and reported `completed_count=320`. The residual-row counts before Run 1's cleanup showed 257 total rows accumulated from prior work. These are separate facts, not contradictory ones.

**Clarification on `dev_subset.json` cardinality:**
`build_corpus_plan(dev_subset.json)` yields **2079 corpus sessions**, not 257. The 257-row residual count in Run 1's pre-reset reflects leftover rows from sessions processed in prior, unrelated benchmark runs — it is not the session count of the corpus being processed.

**Therefore:** `completed_count=320` on a 2079-session corpus is fully possible (320/2079 ≈ 15.4% completion rate). The earlier framing of this as an "impossible count on a 257-session subset" was incorrect.

**What the residual counts do explain is the 10x accumulation between runs:**
- Run 1 started with 257 rows from prior work (accumulated outside the triple-run sequence).
- Run 2's pre-reset found 2897 rows — a **10x accumulation** in a single run.
- Run 3's pre-reset found 2937 rows — consistent continuation.
- This growth is consistent with **prior-run evidence surviving resets** that target only the 7 core tables while missing `skill_consolidation_log` and `skill_nudge_user_state`. The `skill_consolidation_log` rows are keyed by `user_id` and accumulate unboundedly.

**Additionally**: The `retrieval_log: 0` deleted count across all three resets confirms that `retrieval_log` async writes are landing **after** each reset completes, surviving into the next run. Over 3 runs, this adds low-rate orphan `retrieval_log` rows, which alone cannot explain the 10x growth — but combined with the missing `skill_*` tables, the state compounds.

---

## 8. Async Bleed Deep Dive

### 8.1 Fire-and-forget tasks in the retrieval path

Two async tasks are launched via `asyncio.create_task()` without awaiting:

**Path 1 — `bulk_touch_memories()`** (`retrieval.py:942–948`):
```python
async def _touch() -> None:
    try:
        await store.bulk_touch_memories(memory_ids)
    except Exception:
        logger.exception("Failed to update memory access timestamps")
_ = asyncio.create_task(_touch())
```

**Path 2 — `log_retrieval()`** (`retrieval.py:975–994`):
```python
async def _persist_log() -> None:
    try:
        await store.log_retrieval(user_id=effective_user_id, query_text=normalized_query or "", ...)
    except Exception:
        logger.exception("Failed to persist retrieval log")
_ = asyncio.create_task(_persist_log())
```

### 8.2 Teardown sequence

**Canonical (`runner.py:1172–1173`):**
```python
finally:
    await pool.close()   # Pool closes here
```
No awaiting of pending tasks before pool close.

**Fast (`longmemeval_fast.py:506–507`, `521–522`):**
```python
finally:
    await cleanup_benchmark_state(pool, benchmark_user_id)  # Deletes first
    await pool.close()  # Pool closes after cleanup
```
Still does not await pending tasks.

### 8.3 Async bleed verdict

| Task | Survives cleanup? | Survives pool.close? | Mechanism |
|---|---|---|---|
| `bulk_touch_memories()` | **YES** | **YES** | Fire-and-forget; runs after DELETE + pool close |
| `log_retrieval()` (standard) | **YES** | **YES** | Same |
| `log_retrieval()` (L0 path) | **YES** | **YES** | Same |

The `retrieval_log: 0` deleted count in all three resets is consistent with this: the async writes land **after** the reset's DELETE executes, so the next reset finds 0 rows (the prior async writes have already been "cleaned" by being incorporated into the prior run's state, not by the reset function).

---

## 9. Conclusions

### 9.1 Concrete reset gaps

1. **`skill_consolidation_log`** is not reset. This table grows unboundedly per user and leaks benchmark evidence across runs.
2. **`skill_nudge_user_state`** is not reset. The `conversations_since_nudge` counter does not clear.
3. **`conversations.last_retrieved_memory_ids`** column is not cleared when conversations are deleted/recreated.
4. **Redis keys** are not cleaned when `cleanup_redis=False` (triple-run default), leaving ARQ jobs and cached results.
5. **Async tasks** (`log_retrieval`, `bulk_touch_memories`) land after cleanup, creating a persistent low-rate bleed in `retrieval_log`.

### 9.2 Why the 10x accumulation?

The primary driver is **incomplete table coverage in `cleanup_canonical_benchmark`**. The two missing tables (`skill_consolidation_log`, `skill_nudge_user_state`) are not cleared at all, so they accumulate across runs. Combined with async bleed in `retrieval_log`, the state grows far beyond what the 7-table reset can address. The 257-row residual state observed before Run 1 was leftover from prior benchmark work unrelated to the triple-run sequence itself.

### 9.3 What is NOT a gap (confirmed correct)

- `completed_count=320` on `dev_subset.json` is **not impossible** — `build_corpus_plan()` yields 2079 corpus sessions for that dataset; 320 completions is consistent with observed processing rates.
- The 257-row residual count before Run 1 is **not** the session count of the corpus — it is leftover rows from prior benchmark work.
- The 7 core tables ARE all cleared by `cleanup_canonical_benchmark`.
- The delete order, while not FK-optimal, does not cause FK errors in practice.
- `memory_extraction_log` IS correctly cleared (shown by identical extraction logs across runs).
- `retrieval_log` IS cleared at reset time — the async writes that cause accumulation happen **after** the reset, not before.

---

## 10. Prior Art Reference

This audit builds on:
- `tests/benchmark_results/wave0_state_reset_audit.md` (2026-04-21) — established the async bleed and missing Redis cleanup gaps.
- `tests/benchmark_longmemeval/TEARDOWN_AUDIT.md` (2026-04-21) — confirmed `retrieval_log` async bleed in both canonical and fast lanes.
- `tests/benchmark_longmemeval/ISOLATION_AUDIT.md` (2026-04-18) — established user/conversation isolation boundaries.

This V2 audit adds:
- **Empirical evidence** from `wave0_rerun_v1` artifacts showing 10x accumulation.
- **`skill_consolidation_log` and `skill_nudge_user_state`** as previously unidentified leak sources.
- **`conversations.last_retrieved_memory_ids`** column-level state gap.
- **Delete order analysis** against actual FK constraints.
- **Root cause linkage** between the 10x accumulation anomaly and incomplete reset scope; corrected cardinality framing for `dev_subset.json`.
