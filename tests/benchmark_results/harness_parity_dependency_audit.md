# Harness Parity Dependency Audit — T3 Artifact

**Task**: Audit production assembly dependencies with bounded D-chain  
**Date**: 2026-05-06  
**Scope**: Static dependency audit for one synthetic LongMemEval question/user before production `build_memory_context()` and `assemble_system_prompt()` run.

---

## 1. Frame

### Observed

- The live production prompt path is `orchestrator/main.py:1755-1770`: `get_user_settings()` → `format_preferences_block()` → `build_memory_context()` → `assemble_system_prompt()`.
- `build_memory_context()` is conversation-scoped: it loads the conversation row, derives `user_id` from that row, reads L0 memories, recent messages, recent summaries, embeds the latest user query text, and retrieves memories (`orchestrator/memory/injection.py:168-308`).
- The current LongMemEval answer path does **not** call production `build_memory_context()`. It calls production `retrieve_memories_for_text()` and production `assemble_system_prompt()`, but it formats `memory_context` via benchmark-local `_format_eval_memory_block()` (`tests/longmemeval/evaluate.py:434-490, 627-652`).

### Expected

- For production-faithful parity, scope isolation must come from the same mechanism production uses: **the conversation row's `user_id`**, not an aggregated allowlist over one shared benchmark user.
- Any state needed by `build_memory_context()` / `assemble_system_prompt()` must either already exist trivially, be pre-populatable through existing code paths, or be declared a HALT blocker if production changes are required.

### Surface area audited

- `orchestrator/memory/injection.py`
- `orchestrator/memory/store.py`
- `orchestrator/memory/retrieval.py`
- `orchestrator/memory/extraction.py`
- `orchestrator/memory/dedup.py`
- `orchestrator/memory/summary.py`
- `orchestrator/memory/consolidation.py`
- `orchestrator/memory/entities.py`
- `orchestrator/worker/jobs.py`
- `tests/longmemeval/ingest.py`
- `tests/longmemeval/evaluate.py`
- `orchestrator/eval/runner.py`

### Constraints

- No edits to `orchestrator/memory/**` or other production code.
- No live DB population or benchmark run in this task.
- No aggregated unscoped retrieval across a shared benchmark user.

---

## 2. Scope ruling

### 2.1 Aggregated unscoped retrieval is explicitly rejected

The production-faithful mechanism is **deterministic synthetic-user isolation**, not `allowed_source_conversation_ids` over one shared benchmark user.

- `build_memory_context()` has signature `(store, conversation_id, max_tokens=...)` and **does not accept** `allowed_source_conversation_ids` (`orchestrator/memory/injection.py:168-172`).
- It derives `user_id` from `store.get_conversation(conversation_id)` (`orchestrator/memory/injection.py:173-179`).
- The canonical runner already treats unfiltered retrieval as forbidden for full-corpus evaluation: `"Will not proceed with unfiltered retrieval (allowed_source_conversation_ids=None)"` (`orchestrator/eval/runner.py:1678-1684`).

**Disposition:** Any future harness that wants production `build_memory_context()` parity must isolate each synthetic question/user at the **user-row** level (and therefore at that user's conversation/message/memory state level). The allowlist is a benchmark retrieval helper, not a production prompt-scope input.

### 2.2 Task-3 HALT ruling

This audit found **zero `(c)` dependencies**.

- No prerequisite state element requires modifying `orchestrator/memory/**` or other production code before a synthetic-user parity harness can populate it.
- This does **not** override the separate T1/T2 consumer-path gate: the benchmark still does not call production `build_memory_context()`. T3 only answers whether the required state surface itself demands production changes.

---

## 3. Bounded D-chain summary

### D1 — What production reads before prompt assembly

`build_memory_context()` requires:

1. a valid conversation row,
2. that row's `user_id`,
3. L0 memories for that user,
4. recent messages for that conversation,
5. optional recent summary memories for that user,
6. Voyage query embedding availability,
7. retrievable user memories in `memories`,
8. optional trust-signal writes,
9. token-budget constants.

`assemble_system_prompt()` itself only requires:

1. `DAEMON_SYSTEM_PROMPT`,
2. an optional `preferences_block`,
3. a `memory_context` string.

### D2 — What production does **not** read from prompt assembly surfaces

- `build_memory_context()` does **not** read `allowed_source_conversation_ids`.
- It does **not** read `memory_extraction_log` or `retrieval_log` as prerequisites.
- It does **not** require non-empty L0, summaries, or settings; empty/default state is valid.

### D3 — Existing write paths for each missing state element

- Messages/conversations/memories/extraction-log rows already have production write paths.
- Entity rows and summary-memory rows also have existing production write paths, but they are **not** populated by the inline `process_extraction()` path automatically.

### D4 — Root-cause classification

All dependencies are either `(a)` trivially available or `(b)` harness-prepopulatable through existing code paths. No `(c)` production-change blocker surfaced.

---

## 4. Exhaustive dependency inventory

Classification legend:

- **(a)** trivially available
- **(b)** requires harness pre-population using existing code paths
- **(c)** requires production change

### 4.1 Identity, scope, and user-profile state

| State element | Production reader / reason | Classification | Existing code path / table | Cadence | Notes |
|---|---|---:|---|---|---|
| Deterministic synthetic user UUID row | `build_memory_context()` scopes all reads through `conversation.user_id` | **(b)** | User-row creation contract already exists in `tests/longmemeval/ingest.py:create_test_user()` and `orchestrator/eval/longmemeval_fast.py:ensure_benchmark_user()`; both insert into `users` | **Once per synthetic user** | The codebase does not care whether the UUID is UUID4, fixed, or UUID5. Deterministic UUID5 generation is harness bookkeeping, not a production dependency. |
| Synthetic user email/name/username | Existing benchmark insert helpers require them | **(b)** | `users` row inserted by `create_test_user()` / `ensure_benchmark_user()` | **Once per synthetic user** | Required to materialize the user row cleanly and keep cleanup/select-by-email possible. |
| Default empty settings/profile state | `orchestrator/main.py` calls `store.get_user_settings()` before `assemble_system_prompt()` | **(a)** | `users.settings` defaults to `{}` via migration `010_update_users_schema.sql`; missing settings return `{}` via `MemoryStore.get_user_settings()` | N/A | Empty settings are valid; prompt assembly proceeds with no `preferences_block`. |
| Non-empty user settings / preferences block | `format_preferences_block()` only matters if parity needs non-empty profile instructions | **(b)** | `MemoryStore.update_user_settings()` updates `users.settings` | **Once per synthetic user** or whenever profile changes | Production `assemble_system_prompt()` accepts a plain string; the caller populates it separately. |
| Empty L0 state | `build_memory_context()` tolerates no L0 memories | **(a)** | `store.get_l0_memories()` simply returns `[]` | N/A | This is the production default for users with no frozen memories. |
| Non-empty L0 state | L0 memories are injected before dynamic retrieval | **(b)** | Existing memory insert paths + `MemoryStore.update_memory_tier(..., 'l0')` | **Whenever harness wants persistent always-inject facts** (typically once per synthetic user) | Not required for the function to run; only required to test non-empty frozen-memory behavior. |

### 4.2 Conversation and message state

| State element | Production reader / reason | Classification | Existing code path / table | Cadence | Notes |
|---|---|---:|---|---|---|
| Conversation row with `user_id` and `pipeline` | `build_memory_context()` loads it first (`get_conversation`) and derives both scope and `include_local` | **(b)** | `MemoryStore.create_conversation()` inserts into `conversations`; canonical ingest uses it in `tests/longmemeval/ingest.py:295-300` | **Once per haystack session / synthetic conversation** | This is the key production scope anchor. |
| Message rows with encrypted content, roles, timestamps, metadata | `build_memory_context()` builds query text from `get_recent_messages(conversation_id, limit=20)` | **(b)** | `MemoryStore.insert_message()` writes encrypted `messages.content`; canonical ingest does this per turn in `tests/longmemeval/ingest.py:302-324` | **Once per haystack message** | Required if parity wants production query-text derivation from the last user turn instead of direct `question_text`. |
| `pipeline` value on conversation (`cloud`/`local`) | Controls `include_local` in retrieval | **(b)** | Stored by `create_conversation(pipeline=...)` | **Once per haystack session / synthetic conversation** | Synthetic harness should set `pipeline='cloud'` unless it intentionally wants local-only memories included. |

### 4.3 Retrieved memory surface

| State element | Production reader / reason | Classification | Existing code path / table | Cadence | Notes |
|---|---|---:|---|---|---|
| Extracted active memory rows (`memories`) | `retrieve_memories_for_text()` / `search_memories()` / `search_memories_bm25()` need retrievable active memories | **(b)** | Inline production extraction path: `tests/longmemeval/ingest.py:336` → `orchestrator/memory/extraction.py:519-609` → `dedup.py:265-604` → `MemoryStore.insert_memory()` / `supersede_memory()` | **Once per haystack session** | This is the production write path that creates the main L1 retrieval corpus. |
| Encrypted memory content at rest | Store decrypts memory content before formatting | **(a)** | `ContentEncryption.encrypt/decrypt` + `MemoryStore.insert_memory()` / readers | N/A after key exists | If `DAEMON_ENCRYPTION_KEY` is set, content is encrypted at write time and decrypted transparently at read time. |
| `content_tsv` for BM25 | `search_memories_bm25()` queries `content_tsv` | **(b)** | Populated automatically by `insert_memory()` / `supersede_memory()` using `to_tsvector('english', plaintext)` | **Whenever memory rows are written** | No separate harness step required if memory writes go through existing store code. |
| Embeddings on memory rows | `search_memories()` uses pgvector similarity | **(b)** | Populated automatically by dedup path via `embed_documents()` then `insert_memory()` / `supersede_memory()` | **Whenever extracted memories are written** | Existing extraction/dedup path already does this. |
| Empty default memory state | Functions can still run and return empty context | **(a)** | No memories required to avoid crash | N/A | Empty result is valid production behavior, but not sufficient for meaningful parity. |

### 4.4 Extraction bookkeeping and incremental state

| State element | Production reader / reason | Classification | Existing code path / table | Cadence | Notes |
|---|---|---:|---|---|---|
| `memory_extraction_log` rows | Not read by `build_memory_context()`, but part of the production extraction surface and used by worker incremental extraction (`get_last_extraction_time`) | **(b)** | `process_extraction()` calls `MemoryStore.log_extraction()` (`orchestrator/memory/extraction.py:572-596`) | **Once per haystack session extraction call** | Not a prompt prerequisite, but automatically created if harness uses the inline production extraction path. |
| `retrieval_log` rows | Not a prerequisite to run prompt assembly; optional diagnostics surface | **(a)** by default / **(b)** if explicit evidence desired | Written only when retrieval logging is enabled (`retrieval_logging_enabled`, `retrieval_logging_debug`, or explicit benchmark flag) via `MemoryStore.log_retrieval()` | **Once per retrieval call when enabled** | Optional for prompt assembly; required only if later tasks need production retrieval evidence rows. |

### 4.5 Summaries and summary-memory behavior

| State element | Production reader / reason | Classification | Existing code path / table | Cadence | Notes |
|---|---|---:|---|---|---|
| Empty summary state | `build_memory_context()` tolerates `get_recent_summaries(user_id) == []` | **(a)** | No special setup | N/A | Production prompt assembly still works with no summaries. |
| Conversation summary text (`conversations.summary`) | Used by extraction retry context, **not** by `get_recent_summaries()` | **(b)** but auto-populated by inline extraction | `process_extraction()` calls `generate_or_update_summary()` which updates `conversations.summary` | **Once per haystack session extraction call** (then incrementally) | Important distinction: this does **not** satisfy `get_recent_summaries()`. |
| Summary memories (`memories.category='summary'`) | This is what `build_memory_context()` actually reads through `get_recent_summaries()` | **(b)** | Existing production path is `orchestrator/memory/consolidation.py:471-503`, which inserts `category='summary'` memories | **Per synthetic user / per consolidation run**, not per message | `process_extraction()` does not create summary memories. If non-empty summary parity is desired, harness must explicitly prepopulate them through an existing consolidation path or accept empty summary state. |

### 4.6 Entity-expansion behavior

| State element | Production reader / reason | Classification | Existing code path / table | Cadence | Notes |
|---|---|---:|---|---|---|
| Empty entity store | Retrieval still works without entity expansion | **(a)** | No setup needed | N/A | Vector + BM25 retrieval continue to function. |
| Populated `entities` / alias links | `_get_entity_expanded_candidates()` can add entity-linked retrieval candidates | **(b)** | Existing production path is `resolve_entities_job` (`orchestrator/worker/jobs.py:877-981`) which calls `extract_and_resolve_entities()` + `persist_extraction_result()` | **Typically once per haystack session after new memories exist** | Inline `process_extraction()` does **not** enqueue or persist entities by itself. Harness must run an existing entity-resolution path explicitly if alias/entity parity matters. |

### 4.7 Static prompt/runtime constants and provider assumptions

| State element | Production reader / reason | Classification | Existing code path / table | Cadence | Notes |
|---|---|---:|---|---|---|
| `DAEMON_SYSTEM_PROMPT` | Base system prompt for `assemble_system_prompt()` | **(a)** | `orchestrator/prompts.py` module constant | N/A | Always present in code. |
| Token budgets | `DEFAULT_MAX_TOKENS=2500`, `L0_TOKEN_BUDGET=200`, truncation constants shape `build_memory_context()` | **(a)** | `orchestrator/memory/injection.py` module constants | N/A | No prepopulation required; just part of production behavior. |
| Voyage query embedding provider | `build_memory_context()` calls `embed_query(query_text)` | **(a)** | `orchestrator/memory/embedding.py` reads `voyage_api_key`, `embedding_query_model`, `embedding_dimensions` | N/A | Required whenever query embedding is computed live. |
| Voyage document embedding provider | `deduplicate_facts()` embeds extracted facts before storing | **(a)** | `orchestrator/memory/dedup.py` → `embed_documents()` | N/A | Needed for production-faithful extracted memory writes. |
| OpenRouter extraction provider | `extract_facts_from_text()` uses `get_provider_config('openrouter')` and hardcoded `openrouter/openai/gpt-4o-mini` | **(a)** | `orchestrator/memory/extraction.py:19-45, 394-516, 535` | N/A | Provider/API key assumption, not a production-code blocker. |
| OpenRouter summary/consolidation/contradiction providers | Summary, consolidation, and contradiction checks are existing production surfaces | **(a)** | `summary.py`, `consolidation.py`, `dedup.py` | N/A | Relevant only if harness elects to populate those optional surfaces. |
| Trust-signal write side effects | `build_memory_context()` records retrieved memory IDs on the conversation after retrieval | **(a)** | `record_retrieved_memories()` via `orchestrator/memory/trust_signals.py` | **Once per prompt assembly retrieval that returns memories** | Best-effort side effect; no prepopulation required. |

---

## 5. Key parity dispositions

### 5.1 `build_memory_context()` itself has no allowlist slot

Because production prompt assembly is conversation-scoped and user-scoped, **synthetic-user isolation is the production-faithful scope mechanism**. The harness must not aggregate many questions under one shared user and then try to recover production fidelity with `allowed_source_conversation_ids`.

### 5.2 The synchronous inline production extraction path already exists

The existing harness can prepopulate production memory rows without ARQ or the 30-second debounce by calling:

- `tests/longmemeval/ingest.py:336` → `process_extraction()` inline,
- which uses production extraction, dedup, memory writes, extraction-log writes, and conversation-summary updates.

This is a `(b)` dependency with an existing path, not a `(c)` blocker.

### 5.3 Summary memories are separate from conversation summaries

This is the most important subtle dependency discovered in T3:

- `process_extraction()` updates `conversations.summary` via `generate_or_update_summary()`.
- `build_memory_context()` does **not** read that field.
- It instead reads `get_recent_summaries(user_id)` from `memories.category='summary'`.
- Existing summary-memory creation lives in consolidation, not inline extraction.

**Disposition:** non-empty summary parity is `(b)` through an existing consolidation path; empty summary parity is `(a)` and already production-valid.

### 5.4 Entity expansion is optional but separately populated

Entity-linked retrieval requires `entities` rows and linked memory IDs. Existing write paths exist (`resolve_entities_job`, `extract_and_resolve_entities`, `persist_extraction_result`), but they are not part of inline `process_extraction()`.

**Disposition:** optional `(b)` dependency if alias/entity parity matters; otherwise empty entity state is valid `(a)`.

---

## 6. Final classification result

| Class | Count | Notes |
|---|---:|---|
| **(a) trivially available** | 11 | Env/config/runtime constants and valid empty-state surfaces |
| **(b) harness pre-population using existing code paths** | 13 | User/conversation/message/memory/optional entity+summary surfaces |
| **(c) production change required** | **0** | No new production-code blocker discovered by this audit |

**T3 disposition:** `PROCEED` for dependency surface — no new production-change HALT.  
**Important:** the separate T1/T2 consumer-path gate still stands because the current benchmark does not yet call production `build_memory_context()`.

---

## 7. Evidence

- Static search evidence: `.sisyphus/evidence/task-3-dependency-static.txt`
- Halt check: `.sisyphus/evidence/task-3-halt-check.txt`
