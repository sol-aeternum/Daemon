# LongMemEval Runner Isolation Audit

Date: 2026-04-18

## Scope

This audit is sourced from the live harness files, not from `tests/benchmark_longmemeval/`:

- `orchestrator/eval/longmemeval.py`
- `orchestrator/eval/runner.py`
- `orchestrator/eval/longmemeval_fast.py`
- `tests/longmemeval/ingest.py`
- `tests/longmemeval/evaluate.py`

`tests/benchmark_longmemeval/` is an artifact directory only.

## Executive summary

| Lane | User isolation | Conversation isolation | Cleanup model | Main risk |
| --- | --- | --- | --- | --- |
| Canonical (`python -m orchestrator.eval.longmemeval`) | Shared fixed benchmark user `longmemeval@daemon.test` / `12345678-1234-5678-1234-567812345678` from `tests/longmemeval/ingest.py:37-39,176-199` | Ingest creates one conversation per deduped corpus session in `tests/longmemeval/ingest.py:252-321`; evaluate narrows retrieval to checkpoint-derived `allowed_source_conversation_ids` in `orchestrator/eval/runner.py:423-438` | No automatic cleanup in the canonical runner; destructive cleanup exists only in `tests/longmemeval/ingest.py:202-206,324-349` | Persistent shared-user benchmark state can leak across runs unless operators clean it explicitly |
| Fast (`python -m orchestrator.eval.longmemeval_fast`) | Fresh per-run UUID user from `orchestrator/eval/longmemeval_fast.py:105-111,431-433` | Each question creates fresh conversations for that question's session ids in `orchestrator/eval/longmemeval_fast.py:348-386`; evaluate passes those IDs in `orchestrator/eval/longmemeval_fast.py:478-487` | Per-question wipe before and after each question via `cleanup_benchmark_state(...)` in `orchestrator/eval/longmemeval_fast.py:242-251,467,507`, then best-effort user deletion in `:520` | Isolation is stronger, but benchmark evidence is intentionally destroyed after each question and final user deletion is not in an outer `finally` |

## Canonical lane audit

### 1. Entrypoint and runner wiring

- `orchestrator/eval/longmemeval.py:63-144` is the canonical CLI. It always instantiates `LongMemEvalRunner(..., force_retrieval_logging=True)` and dispatches `run`, `ingest`, `evaluate`, or `score`.
- `orchestrator/eval/runner.py:267-362` drives ingest; `:364-482` drives evaluation; `:518-526` runs ingest → evaluate → score sequentially.

### 2. Where `user_id` isolation is established

- The canonical lane does **not** mint a fresh user per run.
- `tests/longmemeval/ingest.py:37-39` hard-codes:
  - `TEST_USER_EMAIL = "longmemeval@daemon.test"`
  - `TEST_USER_ID = 12345678-1234-5678-1234-567812345678`
- `tests/longmemeval/ingest.py:176-199` (`create_test_user`) reuses that same user whenever the email already exists.
- `orchestrator/eval/runner.py:289-290` calls `create_test_user(pool)` during ingest, so all canonical ingests share one benchmark user unless someone has manually cleaned it up first.
- `tests/longmemeval/evaluate.py:363-372` defines `evaluate_single(..., user_id: uuid.UUID = TEST_USER_ID)`. `orchestrator/eval/runner.py:429-439` does not override `user_id`, so canonical evaluation also runs against the same fixed benchmark user.

**Isolation boundary:** canonical isolation is therefore **logical/question scoped**, not **per-run user scoped**.

### 3. Where `conversation_id` isolation is established

- `tests/longmemeval/ingest.py:81-147` builds a `CorpusPlan` that deduplicates raw haystack sessions by normalized message content. Multiple raw `haystack_session_ids` can collapse into one `corpus_key`.
- `tests/longmemeval/ingest.py:252-321` (`ingest_session`) creates exactly one Daemon conversation per deduped corpus session via `store.create_conversation(...)` at `:261-265`, then returns the created `conversation_id` at `:307-320`.
- `orchestrator/eval/runner.py:341-347` stores that `conversation_id` in the ingest checkpoint result keyed by `corpus_key`.
- `orchestrator/eval/runner.py:213-243` resolves the question's checkpointed conversation ids from the corpus references.
- `orchestrator/eval/runner.py:423-438` passes those conversation ids to `evaluate_single(..., allowed_source_conversation_ids=[...])`.
- `tests/longmemeval/evaluate.py:340-360` forwards `allowed_source_conversation_ids` into `retrieve_memories_for_text(...)`.

**Isolation boundary:** canonical retrieval is narrowed to the question's deduped corpus conversations, even though all data lives under one shared user.

### 4. Cleanup boundaries

- There is **no automatic canonical cleanup** in `orchestrator/eval/longmemeval.py` or `orchestrator/eval/runner.py`.
- The only explicit canonical cleanup helper in the audited source is `tests/longmemeval/ingest.py:202-206` (`cleanup_test_user`), which deletes the shared benchmark user by email.
- That helper is reachable only through the legacy ingestion adapter path `tests/longmemeval/ingest.py:324-349,440-467` (`run_ingestion(cleanup=True)` / `--cleanup`).

**Canonical shared-user cleanup:** running this cleanup is destructive across the entire canonical lane because every canonical run shares the same benchmark user.

### 5. Leak and destruction risks

1. **Persistent shared-user evidence leak across runs**
   - Source: `tests/longmemeval/ingest.py:176-199`, `orchestrator/eval/runner.py:289-290`, `tests/longmemeval/evaluate.py:371-372`.
   - Risk: old benchmark conversations/memories remain under the shared benchmark user until `cleanup_test_user` is called.

2. **Standalone legacy evaluation can read the whole shared user**
   - Source: `tests/longmemeval/evaluate.py:458-573`.
   - Detail: `run_evaluation(...)` calls `evaluate_single(...)` without `allowed_source_conversation_ids` at `:519-527`.
   - Risk: if someone uses the legacy evaluation adapter directly instead of the canonical runner, retrieval is scoped only by the shared `TEST_USER_ID`, not by the per-question conversation allowlist.

3. **Shared-user cleanup can destroy all canonical benchmark state at once**
   - Source: `tests/longmemeval/ingest.py:202-206,324-349`.
   - Risk: deleting the shared benchmark user invalidates every checkpointed `conversation_id` the canonical runner expects to reuse or resume.

4. **Checkpoint skip preserves dependence on prior DB state**
   - Source: `orchestrator/eval/runner.py:304-313,401-410`.
   - Risk: ingest/evaluate checkpoint skips assume the previously created shared-user conversations still exist. If cleanup happened out-of-band, the checkpoint can survive while the backing DB evidence is gone.

## Fast lane audit

### 1. Entrypoint and runner wiring

- `orchestrator/eval/longmemeval_fast.py:537-599` is the fast CLI.
- `orchestrator/eval/longmemeval_fast.py:403-534` runs a per-question loop: cleanup → direct ingest → retrieve → answer → judge → cleanup.

### 2. Where `user_id` isolation is established

- `orchestrator/eval/longmemeval_fast.py:105-111` (`build_benchmark_user`) creates a fresh benchmark user object per run using:
  - `user_id = uuid.uuid4()`
  - `email = f"longmemeval+fast-{run_id}@daemon.test"`
  - `name = f"{TEST_USER_NAME}_fast_{run_id}"`
- `orchestrator/eval/longmemeval_fast.py:431-433` generates `run_id = uuid.uuid4().hex[:12]`, builds that unique user, and ensures it exists in the database.
- `orchestrator/eval/longmemeval_fast.py:254-282` (`ensure_benchmark_user`) inserts or reuses only that run-specific email.

**Isolation boundary:** fast-lane state is isolated at the **per-run user** level.

### 3. Where `conversation_id` isolation is established

- `orchestrator/eval/longmemeval_fast.py:205-239` builds per-question `SessionChunk` objects from that question's haystack sessions only.
- `orchestrator/eval/longmemeval_fast.py:348-386` (`ingest_question_chunks`) creates a fresh conversation per session id for the active question via `store.create_conversation(...)` at `:369-374`.
- The same function returns the list of those conversation ids at `:386`.
- `orchestrator/eval/longmemeval_fast.py:478-487` passes both `user_id=benchmark_user_id` and `allowed_source_conversation_ids=conversation_ids` into `evaluate_single(...)`.
- `tests/longmemeval/evaluate.py:340-360,363-372` then retrieves only for that user and forwarded allowlist.

**Isolation boundary:** fast retrieval is both **run-user scoped** and **current-question conversation scoped**.

### 4. Cleanup boundaries

- `orchestrator/eval/longmemeval_fast.py:242-251` (`cleanup_benchmark_state`) deletes `retrieval_log`, `dream_log`, `entities`, `memories`, `memory_extraction_log`, `messages`, and `conversations` for the run user.
- `orchestrator/eval/longmemeval_fast.py:467` calls that cleanup **before** each question.
- `orchestrator/eval/longmemeval_fast.py:507` calls it again in the per-question `finally`, so the active question is cleaned even when answer/judge fails.
- `orchestrator/eval/longmemeval_fast.py:520` then deletes the run-specific user row via `delete_benchmark_user(...)`.

**Fast-run unique-user deletion:** unlike the canonical lane, the fast harness is designed to erase its benchmark state continuously and then drop the per-run user at the end.

### 5. Leak and destruction risks

1. **Expected evidence destruction after every question**
   - Source: `orchestrator/eval/longmemeval_fast.py:467,507`.
   - Risk: retrieval logs, conversations, inserted memories, and any question-local state are intentionally deleted before the next question. This prevents cross-question leakage, but it also removes database evidence needed for after-the-fact debugging unless it was already written to the checkpoint/results files.

2. **Direct insert path bypasses extraction/dedup semantics**
   - Source: `orchestrator/eval/longmemeval_fast.py:289-345`.
   - Detail: the fast lane embeds chunk text and inserts rows straight into `memories`; it does not call `process_extraction(...)` from `tests/longmemeval/ingest.py:298-304`.
   - Risk: this lane should only be used for retrieval/answer/judge/chunking studies. It cannot support claims about the canonical extraction/dedup pipeline.

3. **Final user deletion is not protected by an outer `finally`**
   - Source: `orchestrator/eval/longmemeval_fast.py:428-522`.
   - Detail: `delete_benchmark_user(...)` runs inside the outer `try`, but not in a dedicated outer `finally`.
   - Risk: an unexpected exception after user creation but before line 520 can leave the run-specific user behind. That leak is narrower than the canonical shared-user leak because the email and UUID are unique to one run.

4. **Hard interruption can strand current-run state**
   - Source: `orchestrator/eval/longmemeval_fast.py:466-507`.
   - Risk: the per-question `finally` protects normal exceptions, but a process kill or interpreter crash can still leave transient data under that run's unique user until manual cleanup.

## Boundary-by-boundary comparison

| Boundary type | Canonical lane | Fast lane |
| --- | --- | --- |
| `user_id` | Shared fixed benchmark user from `tests/longmemeval/ingest.py:37-39,176-199` | Fresh UUID user per run from `orchestrator/eval/longmemeval_fast.py:105-111,431-433` |
| `conversation_id` creation | One conversation per deduped corpus session from `tests/longmemeval/ingest.py:252-321` | Fresh conversations per active question/session from `orchestrator/eval/longmemeval_fast.py:348-386` |
| Retrieval scoping | Question-specific allowlist only when using `orchestrator/eval/runner.py:423-438` | Run-specific user + question-specific allowlist from `orchestrator/eval/longmemeval_fast.py:478-487` |
| Cleanup boundary | Manual shared-user cleanup only in `tests/longmemeval/ingest.py:202-206,324-349` | Automatic per-question cleanup in `orchestrator/eval/longmemeval_fast.py:242-251,467,507` plus end-of-run unique-user deletion at `:520` |
| Biggest failure mode | Shared benchmark state persists and can contaminate non-allowlisted legacy evaluation paths | Evidence is safer from leakage but easier to destroy before debugging |

## Bottom line

- The **canonical lane** is a shared-user harness with question-level conversation scoping layered on top. Its cleanup boundary is manual and globally destructive for canonical benchmark state.
- The **fast lane** is a unique-user harness with aggressive per-question teardown. Its isolation is stronger, but its cleanup strategy intentionally destroys benchmark evidence and does not prove canonical extraction/dedup behavior.
