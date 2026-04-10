# TRIAGE.md

## 2026-04-06 — LongMemEval Re-ingestion Blocked (TODO 5)
- **Severity**: critical
- **Scope**: host
- **Encountered during**: TODO 5 execution - Re-ingest LongMemEval with revised extraction
- **Category**: config
- **Blocked current task**: yes
- **What happened**: Database service not available in current environment. The ingestion script requires PostgreSQL at host `postgres:5432` but no database service is running or accessible from the current shell context.
- **Evidence**: 
  - `socket.gaierror: [Errno -2] Name or service not known` when trying to connect to postgres:5432
  - `psql: command not found` - no postgres client available
  - No Docker containers running postgres
- **Likely cause**: This is a development/host environment without the full Daemon stack running (postgres, redis, backend services).
- **Suggested action**: TODO 5 requires a full Daemon environment with PostgreSQL running. Options:
  1. Run in Docker Compose environment with postgres service
  2. Run against a cloud-hosted PostgreSQL instance
  3. Start local postgres: `docker run -d -p 5432:5432 -e POSTGRES_USER=daemon -e POSTGRES_PASSWORD=daemon -e POSTGRES_DB=daemon postgres:15`

## 2026-04-08 20:33 — FAL_KEY Compose Warning During QA Startup Check
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F3 manual QA - service status check
- **Category**: config
- **Blocked current task**: no
- **What happened**: `docker compose ps` emitted startup warnings because `FAL_KEY` is unset in the current environment, even though the core backend/frontend/postgres/redis/worker stack is running.
- **Evidence**:
  - `time="2026-04-08T20:33:09+09:30" level=warning msg="The \"FAL_KEY\" variable is not set. Defaulting to a blank string."`
- **Likely cause**: Docker Compose references `FAL_KEY` for Kling/fal.ai video configuration, but the local `.env` for this stack does not define it (confidence 95%).
- **Suggested action**: Decide whether `FAL_KEY` should be required only for Studio/video flows; if optional, suppress or scope the compose warning. If required for this environment, add it to the active env file.

## 2026-04-08 20:35 — Frontend Lint Script Broken Under Next 16
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F2 Code Quality Review - project quality checks
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: The configured frontend lint command fails immediately instead of running ESLint. `npm --prefix frontend run lint` invokes `next lint`, which Next interpreted as a directory argument and rejected.
- **Evidence**:
  - `> daemon-frontend@0.1.0 lint`
  - `> next lint`
  - `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`
- **Seen again**: 2026-04-10 during F2 rerun on current repository state.
- **Likely cause**: The project still uses the legacy `next lint` script shape, which is not behaving correctly under the current Next.js 16 CLI/runtime in this workspace (confidence 90%).
- **Suggested action**: Replace the frontend lint script with a supported ESLint invocation for Next 16 (for example an explicit `eslint` command/config) and re-run F2.

## 2026-04-08 20:35 — Frontend Standalone TSC Fails on Missing .next Type Artifacts
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F2 Code Quality Review - project quality checks
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Running `npx --prefix frontend tsc --noEmit -p frontend/tsconfig.json` failed because the tsconfig includes `.next/types/**/*.ts`, but TypeScript could not find many referenced generated files.
- **Evidence**:
  - `error TS6053: File '/home/sol/daemon/frontend/.next/types/app/api/chat/route.ts' not found.`
  - `The file is in the program because: Matched by include pattern '.next/types/**/*.ts' in 'frontend/tsconfig.json'`
  - Similar TS6053 errors were emitted for multiple `.next/types/app/**` entries.
- **Likely cause**: The frontend tsconfig assumes Next-generated `.next/types` artifacts exist with stable paths, but the standalone `tsc --noEmit` invocation sees stale or mismatched generated references in `.next/types` (confidence 85%).
- **Suggested action**: Adjust the typecheck workflow so generated Next types are recreated/cleaned before `tsc`, or rely on the framework-supported typecheck command rather than raw `tsc` against `.next/types` includes.

## 2026-04-08 20:35 — Pytest Collection Collision for Retrieval Quality Scripts
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F2 Code Quality Review - project quality checks
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Pytest failed during collection because two files resolve to the same module name `test_retrieval_quality`, causing an import mismatch between the root-level script and the copy under `scripts/`.
- **Evidence**:
  - `import file mismatch:`
  - `imported module 'test_retrieval_quality' has this __file__ attribute: /home/sol/daemon/scripts/test_retrieval_quality.py`
  - `which is not the same as the test file we want to collect: /home/sol/daemon/test_retrieval_quality.py`
- **Likely cause**: The branch introduced duplicate test-like filenames in importable locations, and pytest's default discovery/import rules are colliding on the shared basename (confidence 98%).
- **Suggested action**: Rename or exclude one of the files from pytest discovery, then rerun the suite.

## 2026-04-08 20:35 — tests/test_video_e2e.py Has Syntax Error
- **Severity**: critical
- **Scope**: project
- **Encountered during**: F2 Code Quality Review - project quality checks
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Pytest could not import `tests/test_video_e2e.py` because the file contains an unmatched closing parenthesis.
- **Evidence**:
  - `E File "/home/sol/daemon/tests/test_video_e2e.py", line 596`
  - `E   )`
  - `E   ^`
  - `E SyntaxError: unmatched ')'`
- **Seen again**: 2026-04-10 during F2 rerun on current repository state.
- **Likely cause**: A malformed edit left the test file syntactically invalid (confidence 99%).
- **Suggested action**: Fix the unmatched parenthesis in `tests/test_video_e2e.py:596` before relying on project-wide pytest results.

## 2026-04-08 20:35 — Python 3.14 Deprecation Warnings From litellm/arq During Pytest
- **Severity**: info
- **Scope**: upstream
- **Encountered during**: F2 Code Quality Review - project quality checks
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Pytest emitted repeated deprecation warnings from third-party dependencies that still call `asyncio.iscoroutinefunction`, which is deprecated on Python 3.14 and scheduled for removal in Python 3.16.
- **Evidence**:
  - `/home/sol/daemon/.venv/lib/python3.14/site-packages/litellm/litellm_core_utils/logging_utils.py:273: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead`
  - `/home/sol/daemon/.venv/lib/python3.14/site-packages/arq/cron.py:178: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead`
- **Likely cause**: Current pinned versions of LiteLLM and arq are not yet updated for Python 3.14's coroutine-inspection deprecation path (confidence 95%).
- **Suggested action**: Track dependency updates or pin compatible versions before Python 3.16 removes the deprecated API.

## 2026-04-08 20:35 — Biome LSP Missing for Frontend/Test Diagnostics
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: F2 Code Quality Review - LSP diagnostics on changed files
- **Category**: config
- **Blocked current task**: no
- **What happened**: LSP diagnostics for the changed test/frontend area could not run because the configured Biome language server is not installed in this environment.
- **Evidence**:
  - `Error: LSP server 'biome' is configured but NOT INSTALLED.`
  - `Command not found: biome`
- **Likely cause**: Workspace tooling expects Biome for TS/JS diagnostics, but the binary is absent from the host environment (confidence 98%).
- **Suggested action**: Install `@biomejs/biome` or remove the unused Biome LSP configuration so changed TS/JS/test files can be statically checked reliably.

## 2026-04-08 20:38 — Summary Worker Calls `should_summarize` With Wrong Arity
- **Severity**: critical
- **Scope**: project
- **Encountered during**: F3 manual QA - endpoint and job path audit
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: The summary worker job appears to call `should_summarize()` with parameters in the wrong shape, which would raise before summary generation can run on the live path.
- **Evidence**:
  - `orchestrator/worker/jobs.py:291` → `if not await should_summarize(conv_id, last_summary_time, store, settings):`
  - `orchestrator/memory/summarization.py:112-117` → `async def should_summarize(conversation_id, last_summary_time, last_summarized_msg_count, store, settings=None)`
- **Likely cause**: The summarization helper signature changed to include `last_summarized_msg_count`, but the worker callsite was not updated (confidence 99%).
- **Suggested action**: Update the summary job to pass the stored `last_summarized_message_count` (or equivalent) before relying on summary generation in production/manual QA.

## 2026-04-08 20:52 — Extraction Benchmark Did Not Complete Within 15 Minutes
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F3 manual QA - extraction benchmark clean-path check
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The live extraction benchmark command `python tests/benchmark_extraction.py --json` did not finish before a 900000 ms timeout. This prevented using the full benchmark as-is for manual QA.
- **Evidence**:
  - Bash metadata: `terminated command after exceeding timeout 900000 ms`
- **Likely cause**: The benchmark's per-scenario waits plus live API extraction latency make the full suite too slow for the current environment/configuration (confidence 75%).
- **Suggested action**: Profile the benchmark runtime, reduce per-scenario wait/poll settings for local QA, or provide a smaller smoke-benchmark mode for verification tasks.

## 2026-04-08 20:58 — Memories Table Rejects `qa_manual` Source Type
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F3 manual QA - trust signal runtime checks
- **Category**: config
- **Blocked current task**: no
- **What happened**: Inserting a manual QA memory directly through `MemoryStore.insert_memory()` failed because `source_type='qa_manual'` violates the database check constraint on `memories.source_type`.
- **Evidence**:
  - `asyncpg.exceptions.CheckViolationError: new row for relation "memories" violates check constraint "memories_source_type_check"`
  - `DETAIL: Failing row contains (..., fact, qa_manual, ..., preference.music.genre, l1, ..., 0.5, null).`
- **Likely cause**: The schema allows only a fixed enum-like set of source types and does not include a generic/manual QA value (confidence 95%).
- **Suggested action**: Document the allowed source_type values for operational scripts, or expose them in code/constants so QA/admin tooling can avoid invalid inserts.

## 2026-04-08 21:01 — Extraction Benchmark Smoke Run Failed on Scenario 1
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F3 manual QA - extraction benchmark clean-path smoke run
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: A live smoke run of `tests/benchmark_extraction.py` against scenario 1 completed but extracted zero memories, yielding recall 0.00 for dense personal facts.
- **Evidence**:
  - `Scenario: 1: Dense Personal Facts`
  - `Score: TP=0 FP=0 FN=9 P=0.00 R=0.00`
  - JSON totals: `"passed": false`, `"recall": 0.0`
- **Likely cause**: The 10-second smoke-run wait may be shorter than the extraction job's deferred execution window, or the live extraction queue/path is not producing memories fast enough for the benchmark harness (confidence 70%).
- **Suggested action**: Re-run with the production benchmark wait budget / extraction defer timing, then investigate worker enqueue/dequeue timing if recall remains zero.

## 2026-04-08 21:04 — Markdown LSP Diagnostics Unavailable
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: F3 manual QA - changed-file diagnostics verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` could not validate the changed markdown files because no markdown LSP server is configured in this environment.
- **Evidence**:
  - `Error: No LSP server configured for extension: .md`
- **Likely cause**: The local Oh My OpenCode LSP configuration only registers code-language servers, not Markdown (confidence 99%).
- **Suggested action**: Add a markdown-capable LSP if markdown diagnostics are expected as part of verification workflows.

## 2026-04-08 11:12 — `dedup.py` References Missing Trust Helper
- **Severity**: critical
- **Scope**: project
- **Encountered during**: F4 scope fidelity check - LSP diagnostics on changed files
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Static diagnostics report an undefined symbol inside the new trust-signal path in `orchestrator/memory/dedup.py`, indicating the explicit negative-signal hook cannot resolve its helper.
- **Evidence**:
  - `orchestrator/memory/dedup.py:497` → `error[basedpyright] (reportUndefinedVariable): "_lazy_import_trust_signals" is not defined`
- **Likely cause**: Trust-signal integration was added in `dedup.py` without defining or importing the lazy loader used elsewhere (confidence 98%).
- **Suggested action**: Add the missing helper/import in `orchestrator/memory/dedup.py` or route the call through an existing trust-signals import path, then rerun diagnostics.

## 2026-04-08 11:12 — Biome LSP Missing for Changed Test/JSON Files
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: F4 scope fidelity check - diagnostics verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: Diagnostics for `tests/` could not complete because the configured Biome language server is not installed in this environment.
- **Evidence**:
  - `Error: LSP server 'biome' is configured but NOT INSTALLED.`
  - `Command not found: biome`
- **Seen again**: 2026-04-10 during F4 scope fidelity rerun on current repository state.
- **Likely cause**: Local/tooling environment is missing the configured Biome binary required for JS/TS/JSON diagnostics (confidence 99%).
- **Suggested action**: Install `@biomejs/biome` or adjust tooling configuration before relying on LSP cleanliness for frontend/test files.

## 2026-04-10 00:00 — Pre-existing BasedPyright Errors Outside Changed Tier2 Files
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F4 scope fidelity rerun - changed-file diagnostics verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Directory-level Python diagnostics surfaced existing basedpyright errors in unrelated orchestrator files while verifying the changed files. The current Tier2 scope check did not modify these files, but the repository is not globally diagnostics-clean.
- **Evidence**:
  - `orchestrator/subagents/audio.py:137` → `error[basedpyright] (reportArgumentType)`
  - `orchestrator/subagents/base.py:187` → `error[basedpyright] (reportArgumentType)`
  - `orchestrator/subagents/image.py:263` → `error[basedpyright] (reportAttributeAccessIssue)`
  - `orchestrator/subagents/image.py:270` → `error[basedpyright] (reportAttributeAccessIssue)`
  - `orchestrator/subagents/image.py:349` → `error[basedpyright] (reportUndefinedVariable)`
  - `orchestrator/tools/reminder.py:25` → `error[basedpyright] (reportMissingTypeArgument)`
- **Likely cause**: Pre-existing type-check debt in unrelated subagent/tooling modules surfaced because directory diagnostics scan the whole orchestrator tree rather than only the Tier2-touched files (confidence 92%).
- **Suggested action**: Clean up the unrelated basedpyright errors or limit diagnostics verification to the actual changed files when running future review waves.

## 2026-04-10 15:15 — compileall Cannot Write __pycache__ Files
- **Severity**: warning
- **Scope**: host
- **Encountered during**: F2 Code Quality Review rerun - build verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: `uv run python -m compileall orchestrator tests` could not write bytecode because multiple `__pycache__` directories are not writable from the current shell.
- **Evidence**:
  - `PermissionError: [Errno 13] Permission denied: 'orchestrator/__pycache__/__init__.cpython-314.pyc...'`
  - Repeated `PermissionError` lines across `orchestrator/**/__pycache__`
- **Likely cause**: Existing cache directories are owned by a different user/container context in this workspace (confidence 95%).
- **Suggested action**: Fix ownership/permissions for Python cache directories or run verification in a clean writable environment before relying on compileall.

## 2026-04-10 15:15 — Session Alignment Diagnostic Script Breaks Pytest Discovery
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F2 Code Quality Review rerun - targeted branch blocker verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The new file `scripts/test_session_memory_alignment.py` is discovered by pytest as a test module, but its top-level async test function is not marked for pytest-asyncio execution, so collection/execution fails immediately.
- **Evidence**:
  - `scripts/test_session_memory_alignment.py::test_alignment - Failed: async def functions are not natively supported.`
  - `scripts/test_session_memory_alignment.py:67` defines `async def test_alignment()`
- **Likely cause**: A diagnostic helper script was added with a `test_*.py` name and pytest-compatible function name, so it is unintentionally participating in the suite (confidence 99%).
- **Suggested action**: Rename or exclude the script from pytest discovery, or convert it into a proper marked async test.

## 2026-04-10 15:15 — Retrieval Scoring Contract and Tests Diverged
- **Severity**: critical
- **Scope**: project
- **Encountered during**: F2 Code Quality Review rerun - targeted branch blocker verification
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: The changed retrieval scoring logic now multiplies in `trust_score` with a default of `0.5`, but the changed/related tests still assert the older formulas without trust, producing deterministic failures in both hybrid and base scoring checks.
- **Evidence**:
  - `tests/test_hybrid_search.py:24` → expected `0.760000...`, got `0.67`
  - `tests/test_retrieval.py:49` → expected `0.784080...`, got `0.392040...`
  - `orchestrator/memory/retrieval.py:97-99` defaults `trust_score` to `0.5`
  - `orchestrator/memory/retrieval.py:110-117` includes `trust` in `_hybrid_score(...)`
- **Likely cause**: Trust-scoring behavior was added in the branch without bringing the branch's test expectations and helper signatures into agreement (confidence 96%).
- **Suggested action**: Decide the intended scoring contract, then align `orchestrator/memory/retrieval.py`, `tests/test_hybrid_search.py`, and `tests/test_retrieval.py` to the same formula.

## 2026-04-10 15:15 — Memory-Write Filtering Drops Legitimate Assistant Context
- **Severity**: critical
- **Scope**: project
- **Encountered during**: F2 Code Quality Review rerun - targeted branch blocker verification
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: After stripping `memory_write` artifacts, the extraction job keeps only user-role messages when any remain, so legitimate assistant follow-up context is discarded from the extraction text.
- **Evidence**:
  - `tests/test_jobs_extraction_filtering.py:74` expected `assistant: Anything else?` in extracted text
  - `orchestrator/worker/jobs.py:168-171` reduces `parsed_messages` to `user_messages or parsed_messages`
  - Observed extracted text: `user: I live in Adelaide`
- **Likely cause**: The branch added an over-aggressive fallback that narrows extraction input to user messages instead of preserving non-artifact assistant context (confidence 97%).
- **Suggested action**: Preserve filtered assistant messages in the extraction text unless there is a more specific reason to exclude them.

## 2026-04-10 15:15 — Mock LLM Path No Longer Emits Expected Mock Content
- **Severity**: critical
- **Scope**: project
- **Encountered during**: F2 Code Quality Review rerun - targeted branch blocker verification
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: The changed mock streaming path emits `Mock response tokens from Daemon` character-by-character and leaves the OpenAI-compatible non-streaming content empty, so the mock-mode API tests no longer observe the expected `(mock)` payload.
- **Evidence**:
  - `tests/test_chat_stream.py:75` → `assert "(mock)" in body` failed
  - `tests/test_chat_stream.py:331` → `assert "(mock)" in choice["message"]["content"]` failed
  - `orchestrator/daemon.py:352-379` defines the mock branch and streams `"Mock response tokens from Daemon"`
- **Likely cause**: The branch rewired mock-mode streaming behavior without preserving the previously expected mock content contract used by the API tests (confidence 94%).
- **Suggested action**: Restore the expected mock content contract or update both streaming and non-streaming tests/handlers consistently.

## 2026-04-10 15:15 — evaluate_fix_section.py Checked In as Incomplete Python
- **Severity**: warning
- **Scope**: project
- **Encountered during**: F2 Code Quality Review rerun - changed-file diagnostics verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: The new helper file `tests/longmemeval/evaluate_fix_section.py` contains unresolved names throughout and is not statically valid as checked in.
- **Evidence**:
  - `tests/longmemeval/evaluate_fix_section.py:2` → `error[basedpyright] (reportUndefinedVariable): "Path" is not defined`
  - `tests/longmemeval/evaluate_fix_section.py:8` → `"get_settings" is not defined`
  - `tests/longmemeval/evaluate_fix_section.py:47` → `"evaluate_single" is not defined`
  - `tests/longmemeval/evaluate_fix_section.py:71` → `"score_accuracy" is not defined`
- **Likely cause**: A partial extraction/refactor snippet was committed as a standalone file without the imports and surrounding implementation it depends on (confidence 98%).
- **Suggested action**: Delete the scratch file, finish it, or merge the intended logic back into `tests/longmemeval/evaluate.py` before relying on changed-file diagnostics.

## 2026-04-09 15:23 — Live Extraction Path Still Does Not Persist Conversation Summaries
- **Severity**: critical
- **Scope**: project
- **Encountered during**: F3 manual QA - summary generation/incremental update integration
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: A fresh live QA conversation never populated `summary` or `summary_updated_at` after two `/chat` turns and two extraction-log rows. The current running backend/worker still is not completing the extraction-to-summary handoff on the real runtime path.
- **Evidence**:
  - Manual QA result: `{"conversation_id":"bb8a201e-f3e3-4d94-bb95-5415d20b043f","first_summary":null,"second_summary":null,"first_summary_updated_at":null,"second_summary_updated_at":null,"second_extraction_logs":2,"pass_initial_summary":false,"pass_incremental_update":false,"passed":false}`
  - `/status` immediately after repro: `{"status":"healthy","db_healthy":true,"redis_healthy":true,"memory_enabled":true,"embedding_retry_activations":0,"embedding_last_retry_at":null}`
- **Likely cause**: The live extraction path is still failing inside the best-effort summary update step after extraction, likely due to a swallowed exception or runtime mismatch in `orchestrator.memory.summary.generate_or_update_summary()` or its invocation path (confidence 85%).
- **Suggested action**: Reproduce with backend logs attached, then instrument the post-extraction summary call so failures are surfaced instead of silently ignored; verify `summary` and `summary_updated_at` are written after the first extraction and updated after the second.

## 2026-04-10 22:51 — Extraction Model Committed State Violates Plan Guardrail
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 1 prerequisite verification - implicit-preference-extraction plan
- **Category**: config
- **Blocked current task**: yes
- **What happened**: Plan implicit-preference-extraction.md (line 60, Must Have section) explicitly requires keeping the extraction model as gpt-4o-mini. The committed HEAD state (git commit 8dfb3047 "feat(memory): swap extraction model to gpt-5.4-nano") has gpt-5.4-nano in extraction.py. The working tree has uncommitted changes reverting to gpt-4o-mini, but those are not committed.
- **Evidence**:
  - orchestrator/memory/extraction.py (committed, git show HEAD:340): `model: str = "openrouter/openai/gpt-5.4-nano"`
  - orchestrator/memory/extraction.py (committed, git show HEAD:472): `model = "openrouter/openai/gpt-5.4-nano"`
  - orchestrator/memory/extraction.py (working tree:340): `model: str = "openrouter/openai/gpt-4o-mini"` (diff shows reverting)
  - orchestrator/memory/extraction.py (working tree:472): `model = "openrouter/openai/gpt-4o-mini"` (diff shows reverting)
  - git diff orchestrator/memory/extraction.py confirms: -gpt-5.4-nano +gpt-4o-mini on lines 340 and 472
- **Likely cause**: Commit 8dfb3047 (2026-03-27) intentionally swapped extraction to gpt-5.4-nano after SCORECARD showed it passing all benchmark gates (P=1.00, R=0.90, A=1.00). Uncommitted changes then reverted to gpt-4o-mini, possibly to align with the implicit-preference-extraction plan's guardrail.
- **Suggested action**: Clarify the extraction model policy: if gpt-5.4-nano is the intended production model (better benchmark scores), update the plan guardrail to reflect that. If gpt-4o-mini is required by the plan, the committed 8dfb3047 change should be reverted or the plan guardrail explicitly amended before proceeding to Task 2.
