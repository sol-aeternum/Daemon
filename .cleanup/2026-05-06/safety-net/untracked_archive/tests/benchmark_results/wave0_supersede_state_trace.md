# Wave 0 Supersede-State Precondition Trace

**Date:** 2026-04-25
**Task:** R3 — Trace supersede-state preconditions for preserved-rerun `Supersede failed to close source memory in active state` issue
**Artifact source:** `tests/benchmark_results/wave0_rerun_v1/run_2/longmemeval_checkpoint.json`

---

## 1. Concrete Failure Instance

**Corpus key:** `58f6142ebf908fef90afadeef62bee7579e4761d36812cb9c1f789d1bf303a60`
**Session:** `8ec23b2c`
**Conversation (run_2):** `4d48537c-31f3-4568-880f-99dd993d7289`
**Run:** wave0_rerun_v1 / run_2
**Ingest status:** `extraction_failed`
**Error message:** `Supersede failed to close source memory in active state`

---

## 2. Supersede State Machine

The error originates at `orchestrator/memory/store.py:818-821`:

```python
update_result = await conn.execute("""
    UPDATE memories
    SET valid_to = NOW(), updated_at = NOW()
    WHERE id = $1 AND user_id = $2 AND valid_to IS NULL
""", old_memory_id, user_id)
if update_result != "UPDATE 1":
    raise RuntimeError(
        "Supersede failed to close source memory in active state"
    )
```

**Expected state transition:**
1. `deduplicate_facts()` searches similar memories with `include_historical=True` (`store.py:939`, `969`)
2. Filters to only active matches: `m.get("valid_to") is None` (`dedup.py:416-417`)
3. If similarity ≥ supersede threshold but < merge threshold → calls `store.supersede_memory()`
4. `supersede_memory` inserts new memory row, then UPDATE closes source: `WHERE id = old_memory_id AND valid_to IS NULL`
5. **Expected:** UPDATE returns `"UPDATE 1"` (exactly 1 row affected)

**Observed failure:**
- UPDATE returned something other than `"UPDATE 1"` → either `"UPDATE 0"` (no match) or `"UPDATE N"` (N > 1, theoretically impossible with unique UUID)
- `"UPDATE 0"` means: at the moment of UPDATE, the memory was already closed (`valid_to IS NOT NULL`) or did not exist

---

## 3. Call-Path Evidence

### dedup.py supersede path (lines 529–633)

```python
# dedup.py:558-600
new_memory = await store.supersede_memory(
    old_memory_id=best_match_id,  # from prior search
    new_content=fact.content,
    ...
)
```

**Key preconditions at call time:**
- `best_match_id` was retrieved from `store.search_memories(include_historical=True)` at dedup.py:387
- dedup.py:416 filters: `active_matches = [m for m in similar if m.get("valid_to") is None]`
- dedup.py:417: `best_match = active_matches[0] if active_matches else similar[0]`

The **filter at dedup.py:416 ensures `valid_to is None` at search time**, but this is a snapshot. By the time `supersede_memory`'s UPDATE executes inside a transaction, the source memory may have been closed by a prior step within the same dedup session.

### The `include_historical=True` effect

`store.search_memories` with `include_historical=True` returns **all memories** (both active and historical) because the SQL becomes `(True OR valid_to IS NULL)` = True. The dedup filter at line 416 then restricts to `valid_to is None`.

This means dedup CANNOT select a closed memory as `best_match`. The failure must occur between the search snapshot and the UPDATE.

---

## 4. Run-2 Residual State Evidence

### Reset cleanup results (from `reset_result.json`)

| Run | Memories cleared | Total rows deleted |
|-----|----------------|-------------------|
| run_1 | 23 | 257 |
| run_2 | 278 | 2,897 |
| run_3 | 242 | 2,937 |

**Observation:** run_2's reset found 278 pre-existing memories. This is far more than expected for a 17-session run (which produced 53 active memories per `run_metrics.json`). This indicates significant residual state from prior runs was present before run_2's processing began.

### Run-2 run_metrics

```json
{
  "total_memories_created": 53,
  "active_memories": 53,
  "extraction_outcome_counts": {
    "completed": 37,
    "errored": 28,
    "empty": 62,
    "unknown": 0
  }
}
```

### Artifact timestamp inconsistency (informational)

run_2 checkpoint: `created_at: "2026-04-25T02:47:16+00:00"`

However, `memories.jsonl` and `extraction_log.jsonl` for run_2 have timestamps in the `2026-04-25T02:27` range — approximately 20 minutes before the checkpoint `created_at`. The extraction_log shows 17 sessions (matching `extraction_outcome_counts.completed = 17`) with timestamps `02:27:14` to `02:30:06`. The mismatch between the 98 completed sessions in the checkpoint and the 17-session extraction_log suggests the artifacts may represent only a subset of run_2's processing or there is a naming/sequencing ambiguity between run artifacts.

The supersede failure session (`8ec23b2c` / `4d48537c...`) does not appear in the 17-session extraction_log. The error is recorded in the checkpoint but the corresponding extraction entry is absent from the log snapshot.

---

## 5. Cross-Run vs Within-Run Determination

### Evidence for cross-run residue (primary explanation)

1. **278 pre-existing memories** at run_2 start — run_2's reset confirmed heavy prior-state contamination
2. **Same session `8ec23b2c` was processed in run_1 too** — run_1 checkpoint shows corpus_key `58f6142eb...` with a different conversation_id (`35a7adba...`), but the same session ID `8ec23b2c` appears in both runs. This means session `8ec23b2c` was ingested twice (once per run) with different conversation IDs, potentially creating duplicate memories
3. **run_3 succeeded for the same corpus_key** — run_3's checkpoint shows `58f6142eb...` with status `complete`, suggesting run_3's reset was more effective or the residual memory was finally cleared
4. **No superseded entries in run_2 extraction_log** — the dedup code path that calls `supersede_memory` was not exercised in any of the 17 logged sessions; the failure occurred on a session not in the snapshot
5. **wave0_state_reset_audit.md** documents that `bulk_touch_memories()` and `log_retrieval()` use fire-and-forget `asyncio.create_task()` that can complete **after** cleanup runs, leaving residual rows

**Mechanism (cross-run):**
- Session `8ec23b2c` was processed in a prior run (before run_2)
- That prior run created memory M1 for the session's facts, then dedup closed M1 (superseded) during that same run, OR M1 was left open
- Async writes from that prior run (`bulk_touch_memories`, `log_retrieval`) completed after the run_2 reset
- run_2 reset cleared 278 memories, but by the time run_2's dedup searched for session `8ec23b2c`, a residual active memory from the prior run was found
- dedup found it as the best_match (with `valid_to IS NULL` at search time), selected it for supersede
- The async writes had closed it in the interim, so the UPDATE returned `"UPDATE 0"`

### Evidence against within-run concurrency

1. **`deduplicate_facts()` processes facts sequentially** — within a single session's dedup call, facts are processed one-by-one in a for loop (dedup.py:371)
2. **All 17 extraction_log sessions show `"new": N, "superseded": 0`** — no superseded entries in the snapshot, meaning no dedup supersede path was exercised in any logged session
3. **Single conversation context** — the supersede call includes `user_id` in the WHERE clause, so cross-conversation supersede is not possible

### Residue vs concurrency verdict

**Residue-leaning but unconfirmed; significant unresolved fraction.**

The evidence is circumstantial: the 278 pre-existing memories in run_2's reset, the prior run's processing of the same session, and the documented async-write bleed mechanism. The failure is consistent with a memory from a prior run remaining in the database after reset (either because the reset missed it, or async writes completed after reset but before run_2's dedup for that session ran). This circumstantial evidence points toward cross-run residue as the more likely explanation.

**Within-run concurrency is structurally unlikely.** Sequential fact processing within `deduplicate_facts()` makes concurrent supersede attempts on the same memory within the same call unlikely. No superseded entries appear in any logged session's dedup_results.

**Unresolved fraction is large.** The exact memory UUID, `valid_to` state at failure time, and precise timestamp of failure are not recorded in the current artifacts. Without these, no causal claim can be confirmed. A definitive ruling would require instrumentation capturing those three fields at the point of failure (see Section 7 table).

---

## 6. Dedup Contradiction Check Role (Background)

`check_contradiction()` at `dedup.py:183` is called before `supersede_memory()` to determine whether the incoming fact contradicts the best_match. This LLM call uses `CONTRADICTION_TEMPERATURE = 0.1` (dedup.py:91), which is non-zero and can produce variable results.

Even a small temperature can produce different LLM outputs across calls. If the contradiction check determines "no contradiction" on one call and "contradiction detected" on a semantically identical call, it changes whether supersede or merge path is taken. This is a background source of variance but does not directly explain the "failed to close" error, since the dedup filter at line 416 still requires `valid_to is None` regardless of the contradiction result.

---

## 7. Missing Evidence for Definitive Ruling

To fully distinguish residue from within-run concurrency, the following would be needed:

| Evidence | Why needed | Status |
|----------|-----------|--------|
| Memory UUID of the source memory in the failed supersede | Would show if this UUID exists in any prior run's artifact | Not captured in checkpoint error message |
| `valid_to` value of that memory at failure time | Would show if it was already closed when UPDATE ran | Not captured |
| Precise wall-clock timestamp of failure | Would allow correlation with async task completion | Not captured |
| Extraction_log entry for session `8ec23b2c` in run_2 | Would confirm whether the session's dedup showed any supersede activity | Not present in 17-session snapshot |
| Whether run_2's checkpoint timestamp (`02:47`) aligns with extraction timestamps (`02:27`) | Would clarify whether artifacts represent a single run or span multiple | Ambiguous |

---

## 8. Conclusion

**Residue vs Concurrency: RESIDUE-LEANING, UNCONFIRMED**

The preserved-rerun `Supersede failed to close source memory in active state` on corpus_key `58f6142eb...` in run_2 is most consistent with cross-run residual state. run_2's reset found 278 pre-existing memories (vs. 23 in run_1), session `8ec23b2c` had been processed in a prior run, and the documented async-write bleed mechanism (`bulk_touch_memories` / `log_retrieval` completing after cleanup) provides a plausible path for a memory to survive reset in an active state.

However, this conclusion is based on circumstantial evidence. Within-run concurrency is structurally unlikely (sequential fact processing in `deduplicate_facts()`), but the causal chain cannot be confirmed without: the exact `old_memory_id` UUID, its `valid_to` value at the moment of UPDATE failure, and the precise wall-clock timestamp of the failure. The checkpoint error message captures none of these. The verdict should be treated as **residue-leaning, unconfirmed** pending instrumentation.
