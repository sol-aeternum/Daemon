# Wave 0 State Reset Audit

**Date:** 2026-04-21
**Scope:** Canonical vs Fast-lane DB reset, Redis/arq reset, checkpoint reset, and async carryover behavior for Wave 0 LongMemEval baseline reproducibility.

---

## 1. DB Tables in Scope

All tables below are populated by canonical and/or fast-lane benchmark runs. "Canonical" means `orchestrator/eval/runner.py` / `tests/longmemeval/ingest.py` / `tests/longmemeval/evaluate.py`. "Fast" means `orchestrator/eval/longmemeval_fast.py`.

| Table | Canonical ingest | Canonical evaluate | Fast lane | Notes |
|---|---|---|---|---|
| `users` | Creates single `TEST_USER_ID` (uuid: `12345678-1234-5678-1234-567812345678`, email `longmemeval@daemon.test`) | Reads via store | Creates per-run `BenchmarkUser` with unique email `longmemeval+fast-{run_id}@daemon.test`; deleted at end | Fast uses fresh user per run; canonical reuses same user across all cases |
| `conversations` | One row per corpus session ingested | None written | One row per question (per-session conversation for chunked haystack) | Fast: deleted per-case by `cleanup_benchmark_state()` |
| `messages` | One row per message in each ingested session | None written | None written (direct memory insert bypasses message persistence) | Fast: deleted per-case by `cleanup_benchmark_state()` |
| `memories` | Extracted via `process_extraction()` (canonical extraction pipeline) | None written | Direct insert via `insert_chunk_memories()` with benchmark metadata | Fast: deleted per-case by `cleanup_benchmark_state()` |
| `memory_extraction_log` | Written by `process_extraction()` polling path | None written | None written (direct insert bypasses extraction pipeline) | Fast: deleted per-case by `cleanup_benchmark_state()` |
| `retrieval_log` | Written by `log_retrieval()` via `asyncio.create_task()` — **async, fire-and-forget** | Written by `log_retrieval()` via `asyncio.create_task()` | Same: `log_retrieval()` via `asyncio.create_task()` | **Async bleed risk**: write may land after DB cleanup / pool close |
| `entities` | Written by `resolve_entities_job()` (arq background job) | None written | None written | Fast: deleted per-case by `cleanup_benchmark_state()` |
| `dream_log` | Written by `run_dreaming()` if dreaming is enabled | None written | None written | Fast: deleted per-case by `cleanup_benchmark_state()` |

### Fast lane `cleanup_benchmark_state()` — exact SQL

Defined at `orchestrator/eval/longmemeval_fast.py:242–251`:

```python
async def cleanup_benchmark_state(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    _ = await pool.execute("DELETE FROM retrieval_log WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM dream_log WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM entities WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM memories WHERE user_id = $1", user_id)
    _ = await pool.execute(
        "DELETE FROM memory_extraction_log WHERE user_id = $1", user_id
    )
    _ = await pool.execute("DELETE FROM messages WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM conversations WHERE user_id = $1", user_id)
```

**Canonical lane has NO equivalent per-case cleanup.** The canonical runner (`runner.py`) creates the test user once in `create_test_user()` and never deletes or cleans up benchmark state between cases.

---

## 2. Checkpoint Architecture

### Canonical checkpoint (`runner.py`)

- **File:** `tests/benchmark_results/longmemeval_checkpoint.json` (or custom via `--checkpoint`)
- **Version:** `CHECKPOINT_VERSION = 2` (hardcoded at `runner.py:88`)
- **Schema:**
  ```json
  {
    "version": 2,
    "dataset_path": "...",
    "created_at": "ISO timestamp",
    "updated_at": "ISO timestamp",
    "phases": {
      "ingest": { "status": "completed", "started_at": ..., "updated_at": ..., "completed_count": N, "results": { "corpus_key": { "session_id", "conversation_id", "status", ... } } },
      "evaluate": { "status": "completed", "started_at": ..., "updated_at": ..., "completed_count": N, "results": { "question_id": { "question_id", "question", "reference", "hypothesis", "category", "judgment", ... } } },
      "score": { "status": "completed", "updated_at": ..., "accuracy": { "IE-user": 0.x, ... }, "result_count": N }
    }
  }
  ```
- **Checkpoint written:** After every session ingest (`runner.py:726`) and after every question evaluate (`runner.py:840`)
- **Load behavior (`load_runner_checkpoint`, `runner.py:478–531`):** Returns existing checkpoint if version matches and dataset_path matches. If version mismatch → raises `ValueError` with explicit message: "Old per-question session checkpoints are not compatible with the shared-corpus flow. Delete the checkpoint and rerun ingestion/evaluation."

### Fast lane checkpoint (`longmemeval_fast.py`)

- **File:** `tests/benchmark_results/longmemeval_fast_checkpoint.json` (or custom via `--checkpoint`)
- **Schema (from `tests/longmemeval/evaluate.py:161–173`):**
  ```json
  {
    "dataset_path": "...",
    "results": [ { "question_id", "question", "reference", "hypothesis", "category", "judgment", ... }, ... ]
  }
  ```
- **Checkpoint written:** After every question via `save_checkpoint()` (`evaluate.py:551–555`)
- **Load behavior (`load_checkpoint`, `evaluate.py:123–158`):** Returns `{}` if file missing; raises `ValueError` if dataset_path mismatch; silently skips malformed entries.

### Canonical vs Fast Checkpoint Differences

| Property | Canonical | Fast |
|---|---|---|
| Schema | Multi-phase (ingest/evaluate/score) with per-corpus-session results | Flat per-question results array |
| Versioning | `CHECKPOINT_VERSION = 2` enforced on load | No version field |
| Dataset mismatch | Raises `ValueError` | Raises `ValueError` |
| Stale checkpoint after DB wipe | **Will NOT self-correct**: runner loads checkpoint, sees completed_count > 0, skips sessions/questions | **Will NOT self-correct**: same behavior |
| Resilience to partial runs | Higher (phases tracked independently) | Lower (flat list, question-level only) |

---

## 3. Redis / Arq State

### Benchmark-related Redis keys

The extraction benchmark (`tests/benchmark_extraction.py`) manages these Redis keys directly:

| Pattern | Purpose | Wipe method |
|---|---|---|
| `extract:*` | Extraction job result cache | `wipe_redis_extract_keys()` scans and deletes |
| `arq:job:extract:*` | Pending ARQ extraction jobs | Same |
| `arq:result:extract:*` | Completed ARQ job results | Same |
| `arq:retry:extract:*` | ARQ retry counters | Same |

### `flush_redis_db()` (benchmark_extraction.py:241–265)

Aggressive full-FlushDB wipe. Clears entire Redis DB. Safety-gated to localhost/redis/docker hosts only.

### LongMemEval (runner.py / longmemeval_fast.py) Redis usage

- **Canonical runner:** Does NOT interact with Redis directly. Extraction runs via `process_extraction()` (inline, not queued). `log_retrieval()` is async fire-and-forget via `asyncio.create_task()` — not an ARQ job.
- **Fast runner:** Same. No ARQ queue interaction.
- **No ARQ queue drain helper exists.** Prior research confirmed there is no `drain_arq_jobs()` or equivalent.

### Redis cleanup gap for LongMemEval

Neither `runner.py` nor `longmemeval_fast.py` issue any Redis cleanup. The extraction benchmark's `wipe_redis_extract_keys()` and `flush_redis_db()` are only used by `tests/benchmark_extraction.py`, NOT by LongMemEval. If ARQ jobs for extraction are queued during a LongMemEval run, they are NOT cleaned up by the harness.

---

## 4. Async Carryover: `log_retrieval()` and `bulk_touch_memories()`

### Call paths

Both are invoked via `asyncio.create_task()` (fire-and-forget) in `orchestrator/memory/retrieval.py`:

**Path 1 — `bulk_touch_memories()` (retrieval.py:942–948):**
```python
async def _touch() -> None:
    try:
        await store.bulk_touch_memories(memory_ids)
    except Exception:
        logger.exception("Failed to update memory access timestamps")

_ = asyncio.create_task(_touch())  # line 948
```
Triggered when: `memory_ids` is non-empty AND `_is_retrieval_logging_enabled(log_retrieval)` is True.

**Path 2 — `log_retrieval()` (retrieval.py:975–994):**
```python
async def _persist_log() -> None:
    try:
        await store.log_retrieval(
            user_id=effective_user_id,
            query_text=normalized_query or "",
            ...
        )
    except Exception:
        logger.exception("Failed to persist retrieval log")

_ = asyncio.create_task(_persist_log())  # line 994
```
Triggered when: `_is_retrieval_logging_enabled(log_retrieval)` is True (which is always when `force_retrieval_logging=True`).

**L0 path — `log_retrieval()` (retrieval.py:595–616):**
Same fire-and-forget pattern for L0 memory retrieval logging.

### Teardown sequence in harness

**Canonical (`runner.py:727–728`, `runner.py:841–842`):**
```python
finally:
    await pool.close()   # Pool closes here
```
After `pool.close()`, the async tasks `(_touch(), _persist_log())` still reference the now-closed pool.

**Fast (`longmemeval_fast.py:506–507`, `longmemeval_fast.py:521–522`):**
```python
finally:
    await cleanup_benchmark_state(pool, benchmark_user_id)  # Deletes first
    await pool.close()  # Pool closes after cleanup
```
But `cleanup_benchmark_state()` issues synchronous DELETEs — it does NOT await or cancel the fire-and-forget `asyncio.create_task()` calls that were already queued before teardown began.

### Evidence from TEARDOWN_AUDIT

`tests/benchmark_longmemeval/TEARDOWN_AUDIT.md` (dated 2026-04-19) confirms:
- Fast lane: "Post-case cleanup removes the synchronous tables (`conversations`, `memories`, etc.) back to zero. Releasing the delayed retrieval-log task **after** cleanup recreates a single `retrieval_log` row (`conversation_id IS NULL`). That row survives the post-case cleanup because it lands after the deletes have already run."
- Canonical: "`retrieval_log` rows were written with `conversation_id IS NULL` ... The canonical lane leaks benchmark state between cases because it never tears the benchmark user down between cases."

### Async carryover verdict

| Path | Can land after teardown? | Evidence |
|---|---|---|
| `bulk_touch_memories()` | **YES** | Fire-and-forget `asyncio.create_task()`, no await before pool close |
| `log_retrieval()` (standard) | **YES** | Same — fire-and-forget, pool closes before task completes |
| `log_retrieval()` (L0 path) | **YES** | Same — fire-and-forget, pool closes before task completes |

The benchmark harness **does NOT** wait for these tasks before closing the DB pool. This means `retrieval_log` rows (and `memories.access_count` / `memories.last_accessed_at` updates via `bulk_touch_memories`) can be written **after** the harness believes cleanup has occurred.

---

## 5. Checkpoint Risk After DB Cleanup

### The stale-checkpoint problem

**Scenario:**
1. Run canonical benchmark for N questions → checkpoint has N completed results
2. Someone manually wipes DB (or runs `cleanup_test_user()`)
3. Next run with same checkpoint file:
   - `load_runner_checkpoint()` returns the stale checkpoint with completed_count > 0
   - `runner.py:684`: `if corpus_session.corpus_key in existing_results: continue` → **skips already-ingested sessions**
   - `runner.py:782`: `if question_id in completed_results: continue` → **skips already-evaluated questions**
   - But DB has no data for these skipped cases → evaluation returns empty results or wrong results

**Canonical runner specifically allows this** because:
- `load_runner_checkpoint()` does NOT verify that the DB rows referenced by the checkpoint actually exist
- No cross-check between checkpoint `completed_count` and actual DB row counts

**Fast lane specifically prevents this** because:
- Fresh user per run (`build_benchmark_user()` generates uuid + unique email)
- Checkpoint carries results from prior fast runs but they don't mix with canonical data

### Checkpoint version enforcement

`runner.py:501–508` raises `ValueError` if checkpoint version != 2:
```
"Checkpoint version mismatch: ... Old per-question session checkpoints are not
compatible with the shared-corpus flow. Delete the checkpoint and rerun
ingestion/evaluation."
```

This prevents mixing old-format checkpoints, but does NOT prevent using a valid-format checkpoint whose referenced DB state has been wiped.

---

## 6. Reset Contract for Wave 0

The reset contract specifies what a compliant reset must achieve before starting a Wave 0 baseline run:

### Target tables — exact cleanup SQL

A compliant reset for the **canonical lane** must execute (in order):

```sql
-- 1. Retrieval log (async writes can land after these, so repeat or wait)
DELETE FROM retrieval_log WHERE user_id = '12345678-1234-5678-1234-567812345678';

-- 2. Dream log
DELETE FROM dream_log WHERE user_id = '12345678-1234-5678-1234-567812345678';

-- 3. Entities
DELETE FROM entities WHERE user_id = '12345678-1234-5678-1234-567812345678';

-- 4. Memories (cascades to memory_extraction_log via FK, or explicit delete)
DELETE FROM memories WHERE user_id = '12345678-1234-5678-1234-567812345678';

-- 5. Messages
DELETE FROM messages WHERE user_id = '12345678-1234-5678-1234-567812345678';

-- 6. Conversations
DELETE FROM conversations WHERE user_id = '12345678-1234-5678-1234-567812345678';

-- 7. Users (optional — only if single-user isolation is required)
DELETE FROM users WHERE email = 'longmemeval@daemon.test';
```

### Checkpoint file handling

| Scenario | Required action |
|---|---|
| Fresh run (no checkpoint) | Use default empty checkpoint |
| Resume after interrupted run | Load checkpoint, verify DB rows exist for completed entries |
| DB wiped but checkpoint exists | **MUST delete checkpoint** before resuming |
| Checkpoint version mismatch | **MUST delete checkpoint** — explicit error in `runner.py:505` |

### Redis/arq cleanup

For Wave 0 runs that use the extraction pipeline (not direct insert), the following should also be executed:

```python
# From tests/benchmark_extraction.py:241–265 (flush_redis_db)
redis_client.flushdb()

# Or targeted (less aggressive):
patterns = ["extract:*", "arq:job:extract:*", "arq:result:extract:*", "arq:retry:extract:*"]
for pattern in patterns:
    for key in redis_client.scan(match=pattern):
        redis_client.delete(key)
```

Note: LongMemEval canonical runner does NOT use ARQ-queued extraction, but if any background jobs were enqueued during a prior run, they may still complete and write to DB after teardown.

### Async bleed prevention

To prevent `log_retrieval()` and `bulk_touch_memories()` from landing after teardown:

1. **Await all pending tasks** before closing pool:
   ```python
   # Before await pool.close()
   pending = asyncio.all_tasks()
   done, pending = await asyncio.wait(pending, timeout=5.0)
   ```
2. **OR** disable retrieval logging during reset runs: `force_retrieval_logging=False`
3. **OR** accept async bleed and accept `retrieval_log` may have post-cleanup rows

---

## 7. Canonical vs Fast Lane Summary

| Property | Canonical | Fast |
|---|---|---|
| Per-case teardown | **NONE** — accumulates across all cases | Full `cleanup_benchmark_state()` before+after each question |
| User identity | Single shared `TEST_USER_ID` | Fresh `BenchmarkUser` per run, deleted at end |
| Retrieval logging | Fire-and-forget `asyncio.create_task()` | Same |
| Async bleed risk | **HIGH** — pool closes, async tasks still pending | **MEDIUM** — cleanup before pool close, but task can still bleed |
| Checkpoint self-heal after DB wipe | **NO** — skips based on checkpoint alone | **NO** — same |
| Checkpoint version enforcement | YES (`version=2` enforced) | NO |
| Redis/arq cleanup | None | None |
| Can resume from stale checkpoint after DB cleanup | **NO** — would produce wrong results | **NO** — same |
| Suitable for Wave 0 validation | Requires per-case teardown to be added | Fast lane IS the stronger cleanup pattern |

---

## 8. Blocking Issues for Wave 0

1. **Missing per-case teardown in canonical runner** — `runner.py` has no `cleanup_benchmark_state()` equivalent. All DB tables accumulate across cases. This makes canonical unsuitable for Wave 0 without adding teardown.

2. **Async carryover after teardown** — `log_retrieval()` and `bulk_touch_memories()` use fire-and-forget `asyncio.create_task()`. Even fast lane's `cleanup_benchmark_state()` cannot prevent these writes from landing after DELETE statements execute.

3. **Stale checkpoint after DB wipe** — No cross-check between checkpoint completed_count and actual DB row existence. A wiped DB + existing checkpoint = silent skip of all previously-completed work.

4. **No Redis/arq cleanup in LongMemEval harness** — Unlike `benchmark_extraction.py` which has `flush_redis_db()` and `wipe_redis_extract_keys()`, LongMemEval runners (`runner.py`, `longmemeval_fast.py`) issue no Redis cleanup. If ARQ jobs were queued, they are not drained.

5. **Fast lane cleanup still leaks `retrieval_log`** — TEARDOWN_AUDIT confirms: after post-case cleanup reaches zero, releasing the delayed retrieval-log task recreates a single `retrieval_log` row. Next pre-case cleanup removes it, but it is a visible bleed event.
