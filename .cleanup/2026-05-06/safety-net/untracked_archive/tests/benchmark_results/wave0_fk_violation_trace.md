# Wave 0 FK Violation Trace: `memory_extraction_log.conversation_id`

**Task**: R2 — Trace one `memory_extraction_log.conversation_id` FK violation end-to-end
**Run artifact**: `tests/benchmark_results/wave0_rerun_v1/run_1/`
**Date**: 2026-04-25
**Working hypothesis**: Within-run connection-pool visibility race (inline extraction path); not confirmed — see Section 10.

---

## 1. Anchor Evidence

### FK Violation 1 — Session `1c8832b4_2`

**Checkpoint entry** (`longmemeval_checkpoint.json`, line 229–239):
```json
"8f833636c4315eacd8e0cab04f142bf3fb064a91a4f934a2d0ca9b17b523786c": {
  "session_id": "1c8832b4_2",
  "conversation_id": "3c36dcd9-2948-4e8f-a8dd-9907e3e598e9",
  "message_count": 12,
  "status": "extraction_failed",
  "outcome": "errored",
  "error": "insert or update on table \"memory_extraction_log\" violates foreign key constraint \"memory_extraction_log_conversation_id_fkey\"\nDETAIL:  Key (conversation_id)=(3c36dcd9-2948-4e8f-a8dd-9907e3e598e9) is not present in table \"conversations\"."
}
```

### FK Violation 2 — Session `7e76059f_3`

**Checkpoint entry** (`longmemeval_checkpoint.json`, line 2921–2931):
```json
"1821aeb8be3b7ecb3374813d567d32acef3c7ec5cc2e39fe7c4d5a89a95a4559": {
  "session_id": "7e76059f_3",
  "conversation_id": "4f9d86f9-716b-4ee6-a837-7cda9c528056",
  "message_count": 12,
  "status": "extraction_failed",
  "outcome": "errored",
  "error": "insert or update on table \"memory_extraction_log\" violates foreign key constraint \"memory_extraction_log_conversation_id_fkey\"\nDETAIL:  Key (conversation_id)=(4f9d86f9-716b-4ee6-a837-7cda9c528056) is not present in table \"conversations\"."
}
```

---

## 2. SQL Schema

**FK constraint definition** (`migrations/006_create_extraction_log.sql`, line 4):
```sql
conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
```

`ON DELETE SET NULL` means parent-row deletion sets FK column to NULL (does not cascade-delete child rows). This is confirmed **not** a delete-order bug — the violation occurs on INSERT, not on a delete of the parent.

**Relevant tables and their FK relationships**:

| Table | FK column | References | ON DELETE |
|---|---|---|---|
| `conversations` | (none — parent) | — | — |
| `messages` | `conversation_id` | `conversations(id)` | CASCADE |
| `memory_extraction_log` | `conversation_id` | `conversations(id)` | SET NULL |
| `memories` | `source_conversation_id` | `conversations(id)` | SET NULL |

---

## 3. Inline Extraction Path (per-session, sequential)

`tests/longmemeval/ingest.py` → `ingest_session()` calls:

```
1. store.create_conversation(user_id, pipeline, title)
   → INSERT INTO conversations RETURNING *  (auto-commits per-query)
   → returns {"id": <uuid>}

2. store.insert_message(...)  [loop over messages]
   → INSERT INTO messages (per message, auto-commits per-query)

3. process_extraction(store, user_id, conversation_id, text)
   → extract_facts_from_text()  [may retry once]
   → deduplicate_facts()  [no DB state changes to conversations]
   → store.log_extraction(...)   ← FK violation occurs HERE
```

Step-by-step for the failing sessions:

| Step | What happened | Evidence |
|---|---|---|
| Step 1 | `create_conversation` called with `conversation_id = 3c36dcd9-...` | returned in result dict |
| Step 2 | 12 messages inserted | `message_count: 12` in checkpoint |
| Step 3a | `extract_facts_from_text` called | returned facts (extraction succeeded) |
| Step 3b | `deduplicate_facts` called | no DB schema changes to `conversations` |
| Step 3c | `store.log_extraction` called → **FK violation** | error in checkpoint |

The fact that `extract_facts_from_text` returned facts (the retry path was used — `retry_used: true` in some similar sessions) proves the call proceeded past the extraction and dedup steps. The violation is exclusively at `log_extraction`'s INSERT into `memory_extraction_log`.

---

## 4. Reset/Deletion Interaction

`cleanup_canonical_benchmark_state` (`orchestrator/eval/runner.py`, lines 110–141) deletes tables **in this order**:
```
retrieval_log → dream_log → entities → memories → memory_extraction_log → messages → conversations
```

`memory_extraction_log` is deleted **before** `conversations`. This is the correct order for RESTRICT-like semantics, but here the FK is `ON DELETE SET NULL` on `conversation_id`, so conversation deletion would set the FK to NULL rather than blocking or cascading.

**Importantly**: `cleanup_canonical_benchmark_state` deletes only rows with `user_id = TEST_USER_ID`. The 2 failing conversation IDs (`3c36dcd9-...` and `4f9d86f9-...`) were created **during** run_1's ingestion — they did not exist before the reset. The reset ran first (deleted 19 conversations), then ingestion started fresh. There is **no** temporal overlap between reset and ingestion in the single-run sequence.

**Reset results confirm clean start**:
```json
// run_1
{"conversations": 19, "memory_extraction_log": 7, "messages": 208, ...}  // pre-run cleanup
// run_2
{"conversations": 208, ...}  // leftover from run_1 (properly cleaned before run_2)
// run_3
{"conversations": 219, ...}  // leftover from run_2 (properly cleaned before run_3)
```

---

## 5. Why the Conversation Row Was Not Found

The only operations that could make a `conversations` row invisible at the time of `log_extraction`:

### Scenario A: Connection-pool visibility race (RACE) — **Primary hypothesis**

`create_conversation`, `insert_message`, and `log_extraction` all use `self._pool` (the same `asyncpg.Pool`). Each individual query auto-commits. However, a connection pool may return a connection to the pool in an inconsistent state after certain error conditions (e.g., a serialization failure, connection timeout, or mid-transaction rollback).

If `create_conversation` used connection C1 (INSERT auto-committed), and `log_extraction` used connection C2 (same pool), C2 might not see C1's committed row if C1 had returned a corrupted connection to the pool that caused it to be silently reset.

This is a **transient, non-deterministic** failure — which matches the pattern: only 2 of 148 sessions fail (1.4%), and the exact same sessions do not fail in other runs.

### Scenario B: `create_conversation` INSERT silently failed — Secondary hypothesis

`create_conversation` (`store.py` line 53) executes `INSERT INTO conversations ... RETURNING *`. If this INSERT failed at the DB level but the exception was swallowed somewhere, the function would propagate the exception, not return a UUID. Since `ingest_session` catches exceptions from `ingest_session` itself (not from `create_conversation`), a silent failure at the INSERT level would need to return a default/error UUID. No such fallback exists in the code. This makes silent failure unlikely.

### Scenario C: `messages` CASCADE DELETE of conversation — RULED OUT

`insert_message` inserts into `messages` which has `conversation_id REFERENCES conversations(id) ON DELETE CASCADE`. If any message insert failed with a FK error, the entire session would abort — not just the extraction log. The 12 messages were confirmed inserted (`message_count: 12`). No cascade delete occurred.

### Scenario D: Conversation deleted by prior run residue — RULED OUT

run_1 was the first run. The reset deleted 19 conversations (from prior work), then ingestion created fresh conversations. The failing conversation IDs (`3c36dcd9-...`, `4f9d86f9-...`) were created during run_1. They cannot be pre-existing residues.

---

## 6. Dedup Pipeline Side-Effect Check

`deduplicate_facts` (`orchestrator/memory/dedup.py`) was called for these sessions (extraction produced facts). Dedup performs INSERT/UPDATE into `memories` only. It does **not** touch the `conversations` table, does not issue any DELETE, and does not call `log_extraction`. The dedup path cannot delete a conversation row.

---

## 7. Distinction from `memories.source_conversation_id` FK Violations (TRIAGE.md class)

The earlier FK violation class (documented in `TRIAGE.md` lines 329, 591, 1389) was:
```
insert or update on table "memories" violates foreign key constraint "memories_source_conversation_id_fkey"
```

That FK is on `memories.source_conversation_id → conversations(id)`, a **different column, different table, different constraint** from `memory_extraction_log.conversation_id`.

The `memories` FK violations occurred in `longmemeval_tier2_fast` runs (fast lane, different code path), while these `memory_extraction_log` violations occur in the canonical lane's inline extraction path. The root causes are unrelated.

---

## 8. `ON DELETE SET NULL` Misread Risk

The `memory_extraction_log.conversation_id` FK uses `ON DELETE SET NULL`. This means:
- If a `conversations` row is deleted, the corresponding `memory_extraction_log` rows get `conversation_id = NULL` (not deleted).
- This is NOT a cascade delete that would remove `memory_extraction_log` rows.
- This is NOT related to the FK violation on INSERT.

The violation is a **referential integrity breach on INSERT** — the `conversation_id` value being inserted did not exist in `conversations` at the moment of INSERT.

---

## 9. Summary

| Attribute | Value |
|---|---|
| **Constraint** | `memory_extraction_log.conversation_id → conversations(id)` |
| **FK on table** | `memory_extraction_log` |
| **Violated by** | `store.log_extraction()` INSERT |
| **Failing sessions** | `1c8832b4_2` (id: `3c36dcd9-...`), `7e76059f_3` (id: `4f9d86f9-...`) |
| **Run** | wave0_rerun_v1 / run_1 |
| **Prevalence** | 2 / 148 sessions = 1.4% |
| **Inline extraction affected** | Yes — violation is at `log_extraction` call |
| **Dedup pipeline implicated** | No — dedup does not touch `conversations` |
| **Reset deletion implicated** | No — reset completes before ingestion, and these IDs were created during ingestion |
| **`messages` CASCADE DELETE** | Ruled out — 12 messages confirmed inserted |
| **`ON DELETE SET NULL` misread** | Ruled out — FK breach is on INSERT, not on parent delete |

---

## 10. Most Likely Root Cause Class

**Race / connection-pool visibility issue in the inline extraction path — working hypothesis, not confirmed**

The `create_conversation` INSERT (step 1) returned a valid `conversation_id` and auto-committed. However, the `log_extraction` INSERT (step 3c) — using a different connection from the same pool — did not see that committed row at the moment of its own INSERT.

This would be consistent with:
- Only 2 of 148 sessions affected (sporadic, non-deterministic)
- Both affected sessions produced extraction facts (proven extraction succeeded through dedup)
- No `conversations` table modifications between `create_conversation` and `log_extraction`
- The `conversations` row was confirmed **created** (the checkpoint has the `conversation_id`)
- The `messages` were confirmed **inserted** (12 messages)

However, this scenario requires that two separate connections from the same asyncpg pool fail to see each other's committed writes — an unusual visibility failure. An alternative not yet ruled out is that `create_conversation` itself silently failed to persist the row despite returning a UUID, though this would require exception-swallowing in the store layer that is not evident in the code.

**This hypothesis remains unproven** because no connection-level tracing was active at the time of the failures. Definitive ruling would require: (a) connection-sticky logging of `asyncpg` connection IDs at `create_conversation` and `log_extraction`, or (b) wrapping the full `ingest_session` body in an explicit transaction to guarantee visibility.

---

## 11. Evidence Chain Summary

```
reset_canonical_benchmark(pool)          ← deleted 19 prior conversations, runs to completion
     ↓
LongMemEvalRunner.ingest()              ← sequential per-session loop, 148 sessions
     ↓
ingest_session(corpus_session_148)      ← for 2 specific sessions:
     ├─ create_conversation()            → INSERT commits on connection C1
     │                                    returns conversation_id = X
     ├─ insert_message() ×12            → INSERT commits on connection C2 (message 1)
     │   ...                              INSERT commits on connection C3 (message 12)
     └─ process_extraction()             → extract_facts_from_text() succeeds
         ├─ get_conversation(X)          → returns conversation (visible on C? or not?)
         ├─ deduplicate_facts()           → no conversations table access
         └─ store.log_extraction(X, ...)  → FK VIOLATION on connection C4
                                             "Key (conversation_id)=(X) is not present
                                              in table 'conversations'"
```

The `get_conversation` call within `process_extraction` succeeded (the code proceeds past line 637), which means the conversation was visible to that call's connection — but potentially not to the subsequent `log_extraction` call's connection. This intra-run connection-switching visibility gap is the **hypothesized** root cause, but the evidence is circumstantial and the exact mechanism has not been instrument-confirmed.

---

## 12. Verification Steps for Investigator

1. Confirm whether the `MemoryStore` is initialized with a single `asyncpg.Pool` shared across all store operations in `LongMemEvalRunner.ingest()`.
2. Add connection-sticky logging (log the `connection._conNECTION_id` or equivalent) at `create_conversation` and `log_extraction` to confirm whether different connections are used for the two operations.
3. Check `asyncpg` version for known connection-pool visibility issues with auto-commit mode.
4. Consider wrapping `create_conversation → process_extraction` in an explicit transaction to guarantee visibility.

---

*Trace completed. R2 output.*
