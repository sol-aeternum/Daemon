# Triage Log

> Auto-generated diagnostic capture. Items here were encountered during task
> execution but fall outside the immediate task scope. Review and action as needed.

---

## [2026-03-12T11:58:00+10:30] — Runtime warnings in existing chat-history tests

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Studio verification (`pytest tests/test_store.py tests/test_chat_history.py -q`)
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Test suite passed, but runtime warnings were emitted from mocked async call paths (`AsyncMockMixin._execute_mock_call` never awaited) in memory injection and chat history assembly.
- **Evidence**:
  - `orchestrator/memory/injection.py:104` RuntimeWarning: coroutine `AsyncMockMixin._execute_mock_call` was never awaited
  - `orchestrator/memory/injection.py:106` RuntimeWarning: coroutine `AsyncMockMixin._execute_mock_call` was never awaited
  - `orchestrator/main.py:1354` RuntimeWarning: coroutine `AsyncMockMixin._execute_mock_call` was never awaited
- **Likely cause**: Existing test mocking setup returns AsyncMock objects where sync formatting paths expect concrete values; unrelated to Studio feature changes. [~85% confidence]
- **Suggested action**: Harden test fixtures/mocks for settings/preferences injection to avoid leaking unawaited coroutine objects into sync formatting functions.

## [2026-03-12T11:42:00+10:30] — Wave 3 router integration surfaced transient syntax/import/type wiring issues

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Studio Wave 3 backend API router and app wiring
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Static/runtime checks surfaced three transient issues while wiring `/api/images`: router signature ordering produced a temporary syntax error (`non-default follows default`), `main.py` import style for `backend.image_gen.router` triggered unresolved import diagnostics, and ARQ worker function registration flagged a parameter-name/signature mismatch for the new cleanup job.
- **Evidence**:
  - LSP + runtime import error around `backend/image_gen/router.py` function signature.
  - LSP `reportMissingImports` on `orchestrator/main.py` for `backend.image_gen.router`.
  - LSP `reportArgumentType` in `orchestrator/worker/worker.py` for `cleanup_generated_images` registration.
- **Likely cause**: Refactor churn while aligning dependency annotations and package/module import paths during incremental edits. [~95% confidence]
- **Suggested action**: Keep router signature ordering conservative when mixing `Annotated` dependencies and defaults, prefer dynamic module import for non-package-root modules in this workspace layout, and keep new ARQ job signatures identical to existing function contract.

## [2026-03-12T10:46:00+10:30] — Studio kickoff quality warnings caught and fixed

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Start plan execution for Studio Wave 1 (catalog + storage)
- **Category**: other
- **Blocked current task**: no
- **What happened**: Local quality checks flagged a non-essential module docstring in `backend/image_gen/__init__.py` and a strict-typing warning (`reportAny`) in `backend/image_gen/storage.py`.
- **Evidence**: Hook output flagged `Comment/docstring detected in code changes`; `lsp_diagnostics` flagged `backend/image_gen/storage.py:62` (`reportAny`).
- **Likely cause**: Initial scaffolding included descriptive module text and uncast `json.loads` return type. [~99% confidence]
- **Suggested action**: Keep bootstrap modules minimal and run diagnostics immediately after scaffolding to resolve strict typing issues before broader verification.

## [2026-03-12T10:58:00+10:30] — Frontend lint command mismatch with current tooling

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Verification for Studio page scaffold and provider
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `npm run lint` failed because `next lint` in the project script is incompatible with current Next.js CLI behavior in this environment (`invalid project directory .../frontend/lint`). Direct ESLint fallback (`npx eslint`) also failed due missing `eslint.config.*` under ESLint 9 flat-config defaults.
- **Evidence**:
  - `npm run lint` output: `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`.
  - `npx eslint app/studio --ext .ts,.tsx` output: `ESLint couldn't find an eslint.config.(js|mjs|cjs) file`.
- **Likely cause**: Existing repo lint tooling config drift (Next 16 + ESLint 9) unrelated to Studio feature code. [~90% confidence]
- **Suggested action**: Align lint pipeline by either migrating to flat `eslint.config.*` or pinning supported ESLint/Next lint command path; keep `npm run build` as temporary verification gate.

## [2026-03-10T19:27:00+10:30] — background task cancel returns error once task already completed

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: DOCX preview loading fix follow-up
- **Category**: other
- **Blocked current task**: no
- **What happened**: Attempting to cancel a background task after it had already completed returned an error response instead of a no-op success.
- **Evidence**: `background_cancel(taskId="bg_c1bf6dde")` returned `Cannot cancel task: current status is "completed". Only running or pending tasks can be cancelled.`
- **Likely cause**: Tool enforces strict state transitions and rejects cancellation for terminal states. [~100% confidence]
- **Suggested action**: Treat this as expected behavior; check task status first or ignore cancel requests for completed tasks.

## [2026-03-11T23:08:06+10:30] — Benchmark matcher semantics drifted from documented ALL-keyword behavior

- **Severity**: warning
- **Scope**: project
- **Encountered during**: extraction recall re-audit (Wave 1 rerun)
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Benchmark scoring logic now matches expected facts when **any** keyword matches, while the `ExpectedFact.keywords` contract/comment still states that **all** keywords must match.
- **Evidence**: `tests/benchmark_extraction.py` line 399 comment says `ALL must match`, but line 633 uses `if any(_keyword_matches(kw, content) for kw in expected.keywords):`.
- **Likely cause**: Prior patch changed matcher behavior to reduce false negatives without updating benchmark contract. [~95% confidence]
- **Suggested action**: Decide intended scoring contract explicitly; if strict contract is desired, restore `all(...)` and keep prompt/atomicity fixes as the primary path.

## [2026-03-11T23:08:06+10:30] — Workspace includes large unrelated autogenerated diffs from prior delegated run

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: extraction recall re-audit (Wave 1 rerun)
- **Category**: other
- **Blocked current task**: no
- **What happened**: Working tree contains massive unrelated changes (`frontend/.next/*`, `node_modules/*`, root `package.json`/`package-lock.json`, debug scripts), which obscures extraction-focused diffs and increases risk of accidental commits.
- **Evidence**: `git status --short` shows hundreds of unrelated modified/untracked artifacts outside extraction pipeline files.
- **Likely cause**: Prior delegated task executed commands that generated build/install artifacts outside requested scope. [~90% confidence]
- **Suggested action**: Isolate extraction work on a clean branch/worktree or explicitly stage only targeted files before any commit.

## [2026-03-11T23:28:45+10:30] — Full extraction benchmark still fails recall target after restoring strict matcher

- **Severity**: warning
- **Scope**: project
- **Encountered during**: matcher contract fix + full benchmark rerun
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: After restoring strict keyword matching (`all(...)`) in benchmark scoring, full benchmark still failed target recall. Precision remained high, but recall is well below required threshold.
- **Evidence**: `python benchmark_extraction.py` output saved `tests/results/bench_20260311_232822.json`; TOTAL recall `0.57` (< `0.90` target). S6 had `TP=0 FN=7` (recall 0.00).
- **Likely cause**: Extraction quality/prompt decomposition is still missing multi-turn durable facts (especially S6), not a scoring artifact. [~90% confidence]
- **Suggested action**: Focus next on prompt atomicity/decomposition examples and S6-specific extraction behavior (why no durable facts are emitted for dense multi-turn hardware session).

## [2026-03-12T00:29:15+10:30] — Deferred extraction jobs still fail with conversation FK violations

- **Severity**: warning
- **Scope**: project
- **Encountered during**: deep S6 isolation/interference audit + full benchmark rerun (`--wait 90`)
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: Worker logs show `extract_memories` failures writing to `memories` due `source_conversation_id` FK violations, indicating extraction jobs can execute after the benchmark deletes conversations.
- **Evidence**: `docker logs daemon-worker-1` includes `ForeignKeyViolationError: insert or update on table "memories" violates foreign key constraint "memories_source_conversation_id_fkey"` for `extract:<conversation_uuid>:extract_memories`; benchmark deletes conversations at `tests/benchmark_extraction.py:851`; extraction is deferred 60s in `orchestrator/daemon.py:785`.
- **Likely cause**: Deferred extraction timing plus post-scenario conversation deletion races background writes that retain `source_conversation_id`. [~95% confidence]
- **Suggested action**: For benchmark reliability, delay conversation deletion until extraction job completion (barrier/poll) or disable per-scenario deletion during benchmark runs; for runtime robustness, guard insert path when source conversation no longer exists.

## [2026-03-12T00:29:15+10:30] — Local DB inspection tooling unavailable in container shell

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: deep S6 isolation/interference audit
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Direct DB inspection via `psycopg2` and `psql` failed because those tools are not installed in this runtime.
- **Evidence**: `ModuleNotFoundError: No module named 'psycopg2'`; shell error `psql: command not found`.
- **Likely cause**: Minimal runtime image/toolchain without optional DB clients. [~100% confidence]
- **Suggested action**: Keep using `asyncpg` for ad-hoc DB queries in this environment or add optional tooling image for diagnostics.

## [2026-03-12T08:34:36+10:30] — Benchmark still below target after FK-race hardening; S6 noisy extraction hurts precision

- **Severity**: warning
- **Scope**: project
- **Encountered during**: post-fix verification (`python benchmark_extraction.py`)
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: After disabling per-scenario conversation deletion and adding FK-fallback inserts, benchmark improved but still failed target gates due S6 over-extraction noise and missed durable facts.
- **Evidence**: `tests/results/bench_20260312_083344.json` reports TOTAL `Precision=0.88`, `Recall=0.70`; S6 `TP=2 FP=3 FN=5` (precision 0.40, recall 0.29). Worker log check after run showed no new `memories_source_conversation_id_fkey` errors.
- **Likely cause**: Prompt/model extraction quality for dense multi-turn sessions (S6) remains under-constrained (noise + missing key durable facts). [~90% confidence]
- **Suggested action**: Add S6-specific decomposition and anti-noise constraints (UPS sizing chatter should be filtered unless explicitly durable), then rerun full benchmark.

## [2026-03-12T09:14:06+10:30] — S6 extraction shows severe run-to-run variance and assistant-turn contamination

- **Severity**: warning
- **Scope**: project
- **Encountered during**: 3x isolated S6 variance audit with DB snapshots
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: Three identical isolated S6 runs produced highly variable outcomes (R=0.43, 0.00, 0.14), with noisy UPS/power facts often extracted while durable user hardware/OS/network facts were missed. One run produced zero extraction_log rows/memories (no validated facts).
- **Evidence**: Results files `tests/results/bench_20260312_090532.json`, `tests/results/bench_20260312_090757.json`, `tests/results/bench_20260312_091026.json`; worker path builds extraction text from all roles in `orchestrator/worker/jobs.py` (`_parse_messages`, `_messages_to_text`), and decrypted extraction snippet previews include both `user:` and `assistant:` content.
- **Likely cause**: Extraction prompt receives assistant completions (variable, verbose) in addition to user messages, introducing stochastic salience drift and false-positive durable facts. [~95% confidence]
- **Suggested action**: Restrict extraction input to user-role messages (or heavily downweight assistant role) and rerun S6 variance test before further prompt tuning.

## [2026-03-12T09:52:46+10:30] — Benchmark default wait underestimates recall after defer-based extraction scheduling

- **Severity**: warning
- **Scope**: project
- **Encountered during**: post-fix full benchmark verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: With extraction defer at 60s, default benchmark wait (45s) still under-captures some short scenarios, producing an apparent recall failure despite stable extraction quality when wait is extended.
- **Evidence**: `python benchmark_extraction.py` -> `tests/results/bench_20260312_093431.json` with `Recall=0.70` (fail); `python benchmark_extraction.py --wait 90` -> `tests/results/bench_20260312_095148.json` with `Recall=0.93` (pass).
- **Likely cause**: Fixed wait is shorter than defer+processing envelope for shorter scenarios; benchmark scores before extraction completion. [~90% confidence]
- **Suggested action**: Set benchmark default wait >= defer window (e.g., 90s) or add a completion barrier based on extraction job completion/extraction_log row before scoring.

## [2026-03-10T19:16:00+10:30] — `sg` command resolves to system utility, not ast-grep CLI

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Search-mode investigation for DOCX preview rendering hang
- **Category**: config
- **Blocked current task**: no
- **What happened**: The requested `sg` direct search command does not map to ast-grep in this environment; it resolves to a different system command (`group` utility), so AST search had to be performed with the dedicated `ast_grep_search` tool instead.
- **Evidence**: Command `sg -p 'renderAsync($A, $B, $$$)' frontend --lang tsx` returned `Usage: sg group [[-c] command]`.
- **Likely cause**: Host PATH maps `sg` to an unrelated binary; ast-grep CLI alias is not installed/exposed as `sg`. [~95% confidence]
- **Suggested action**: Keep using `ast_grep_search` tool (or install/alias ast-grep CLI as `sg` if shell parity is required).

## [2026-03-09T22:34:47+10:30] — Integration test mock targeted nonexistent skills_store function

- **Severity**: info
- **Scope**: project
- **Encountered during**: Verification for document filename + skill-limit changes
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: The new test for orchestrator skill injection limit initially patched `orchestrator.skills_store.list_enabled_skills`, which does not exist, causing an `AttributeError` and one failing test.
- **Evidence**: `pytest` failure: `<module 'orchestrator.skills_store' ...> does not have the attribute 'list_enabled_skills'` in `test_orchestrator_skill_injection_limit_remains_2000`.
- **Likely cause**: Incorrect assumption about skills_store API in test code (actual path uses `list_skills` + `get_skill`). [~100% confidence]
- **Suggested action**: Keep the test patched against real APIs (`list_skills`, `get_skill`) to validate 2000-char truncation behavior.

## [2026-03-09T21:54:34+10:30] — Frontend lint command incompatibility (seen again)

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Verification after generated-files proxy and inline document card fixes
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `npm run lint` in `frontend/` still fails immediately with Next CLI path parsing error instead of executing lint checks.
- **Evidence**:
  - Command: `npm run lint` (workdir `frontend`)
  - Output: `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`
- **Likely cause**: Existing Next.js 16 lint workflow mismatch already tracked in TRIAGE (legacy `next lint` script behavior). [~95% confidence]
- **Suggested action**: Keep using `npm run build` + LSP for verification until lint script is migrated to a Next 16-compatible setup.
- **Seen again**: Matches prior lint mismatch entries (`next lint` path parsing failure).

## [2026-03-09T21:38:41+10:30] — Document execute lost sandbox output before persistence

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Live DOCX generation debugging after model/interpreter fixes
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: `spawn_agent` returned `Generated file not found` even after code generation and execution succeeded. The output file path from sandbox was no longer present when `DocumentSubagent.execute` validated it.
- **Evidence**: User runtime output showed `"error": "Generated file not found"` for `agent_type=document`. Code inspection showed `_execute_sandbox` returned `output.{format}` inside `TemporaryDirectory`, then `execute()` checked existence after function return.
- **Likely cause**: Temp directory lifecycle bug: `_execute_sandbox` returned a path inside `TemporaryDirectory` that was deleted on function exit before persistence stage. [~98% confidence]
- **Suggested action**: Return a copied persistent temp file from sandbox and clean it after `_persist_file` completes.

## [2026-03-09T21:26:43+10:30] — DOCX sandbox executed with interpreter missing python-docx

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Debugging live document generation failure from user runtime screenshot
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: Document generation code execution failed in sandbox with `ModuleNotFoundError: No module named 'docx'`. The sandbox used `python` directly, which can resolve to a different interpreter than the running service environment.
- **Evidence**: User runtime output from `spawn_agent` showed `Code execution failed ... from docx import Document ... ModuleNotFoundError: No module named 'docx'`.
- **Likely cause**: Interpreter mismatch (`python` binary not matching app runtime interpreter), plus potential stale container builds without refreshed dependencies. [~88% confidence]
- **Suggested action**: Run sandbox with `sys.executable` and keep dependency install/rebuild instructions in error message when `docx` is missing.

## [2026-03-09T21:19:41+10:30] — Document subagent returned OpenRouter HTTP 400 for DOCX request

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Debugging document skill file blocker follow-up and live DOCX generation validation
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: A live `spawn_agent` call with `agent_type="document"` failed with `Client error '400 Bad Request'` from OpenRouter, preventing DOCX generation. Investigation indicated a model identifier mismatch in direct OpenRouter calls.
- **Evidence**: User-provided runtime output showed `error: "Client error '400 Bad Request' for url 'https://openrouter.ai/api/v1/chat/completions'"` from the document agent path.
- **Likely cause**: Document subagent used a hyphenated model version string (`...claude-sonnet-4-5`) rather than dot notation used by current OpenRouter IDs (`...claude-sonnet-4.5`). [~85% confidence]
- **Suggested action**: Keep model ID pinned to `anthropic/claude-sonnet-4.5` for direct OpenRouter API calls and preserve detailed HTTP error body surfacing for faster diagnosis.

## [2026-03-09T20:58:40+10:30] — Pyright warning for private helper import in integration test

- **Severity**: info
- **Scope**: project
- **Encountered during**: Verification for TODO 20 integration test — full generation flow
- **Category**: other
- **Blocked current task**: no
- **What happened**: LSP diagnostics reported a private-usage warning because the integration test imports `_persist_file_result` from `orchestrator.tools.spawn`.
- **Evidence**: `lsp_diagnostics` on `tests/test_document_integration.py` showed `reportPrivateUsage: "_persist_file_result" is private and used outside of the module in which it is declared`.
- **Likely cause**: Test intentionally validates spawn-level metadata behavior by asserting current private helper behavior. [~90% confidence]
- **Suggested action**: If warning-free diagnostics are required, cover this behavior through a public API path (e.g., tool invocation flow) or expose a public helper for result post-processing.

## [2026-03-09T20:56:11+10:30] — Document integration test mocked nonexistent file path

- **Severity**: warning
- **Scope**: project
- **Encountered during**: TODO 20 integration test — full generation flow
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: `tests/test_document_integration.py` initially failed because mocks returned `/tmp/output.docx`, but `DocumentSubagent.execute` validates file existence before persistence. The path did not exist, so execute returned failure.
- **Evidence**: `python -m pytest tests/test_document_integration.py -q` reported two failures with `assert result.success is True` failing and error payload containing `Generated file not found: /tmp/output.docx`.
- **Likely cause**: Test assumptions diverged from current execution order in `DocumentSubagent.execute` (existence check happens before `_persist_file`). [~98% confidence]
- **Suggested action**: Keep integration tests returning real temporary file paths from `_execute_sandbox` mocks whenever execute-flow behavior is tested.

## [2026-03-09T17:53:28+10:30] — Existing test warning patterns seen again after fallback voice prompt update

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Run diagnostics and targeted tests for updated fallback style behavior
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted tests passed and behavior update compiled, but known LiteLLM deprecation and AsyncMock coroutine warnings repeated.
- **Evidence**:
  - `litellm_core_utils/logging_utils.py:273` deprecation warning for `asyncio.iscoroutinefunction`.
  - Runtime warnings in `orchestrator/memory/injection.py:104,106` and `orchestrator/main.py:1326` during chat history tests.
- **Likely cause**: Existing upstream warning + test-mock coroutine behavior, unchanged by this patch. [~95% confidence]
- **Suggested action**: Keep tracked; address AsyncMock handling and monitor LiteLLM upgrades.
- **Seen again**: duplicate warning family, no behavior change.

## [2026-03-09T17:46:50+10:30] — Existing test warning patterns seen again after provider normalization fix

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Run diagnostics and targeted tests for fallback flow
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted tests passed, but known LiteLLM deprecation and AsyncMock runtime warnings repeated.
- **Evidence**:
  - LiteLLM deprecation warning in `litellm_core_utils/logging_utils.py:273`.
  - Runtime warnings in `orchestrator/memory/injection.py:104,106` and `orchestrator/main.py:1322`.
- **Likely cause**: Existing upstream warning + test-mock coroutine behavior. [~95% confidence]
- **Suggested action**: Track as known; cleanup AsyncMock usage and monitor LiteLLM updates.
- **Seen again**: duplicate warning family, no behavior change.

## [2026-03-09T17:45:17+10:30] — Vision fallback model/provider mismatch (fixed)

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Trace fallback model/provider mismatch causing LiteLLM BadRequestError
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: Non-vision model image requests could fail fallback calls with LiteLLM error indicating provider was not inferred for fallback model ID.
- **Evidence**: User-visible error: `litellm.BadRequestError: LLM Provider NOT provided... model=google/gemini-2.5-flash-image`.
- **Likely cause**: Tier image fallback model defaults use raw `google/...` IDs while active provider is often OpenRouter; fallback path previously passed model unchanged. [~98% confidence]
- **Suggested action**: Keep provider-aware normalization in fallback path (`openrouter/...` when provider is OpenRouter, strip prefixes otherwise) and add regression test coverage for fallback model normalization.

## [2026-03-09T17:39:05+10:30] — Existing deprecation/runtime warning patterns seen again in targeted fallback verification

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Run diagnostics and targeted verification for Kimi non-vision image path
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted backend tests passed but repeated known warning patterns from LiteLLM deprecation and AsyncMock coroutine handling in test paths.
- **Evidence**:
  - `/home/sol/.local/lib/python3.14/site-packages/litellm/litellm_core_utils/logging_utils.py:273` deprecation warning (`asyncio.iscoroutinefunction`).
  - Runtime warnings again in `orchestrator/memory/injection.py:104,106` and `orchestrator/main.py:1297` during `tests/test_chat_history.py`.
- **Likely cause**: Existing upstream/library + test-mocking behavior, unchanged by fallback patch. [~95% confidence]
- **Suggested action**: Keep tracked and address in dedicated warning cleanup.
- **Seen again**: duplicate of prior warning entries.

## [2026-03-09T17:31:00+10:30] — Pytest invocation without module path fails import resolution

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Run diagnostics/build/tests and verify image handling behavior
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Running `pytest ...` from repo root failed to import project package modules in `tests/conftest.py`.
- **Evidence**: `ModuleNotFoundError: No module named 'orchestrator'` from `tests/conftest.py:8` when executing `pytest tests/test_chat_stream.py tests/test_completion_with_tools.py tests/test_chat_history.py tests/test_store.py`.
- **Likely cause**: Python path resolution differs when invoking bare `pytest` in current environment; `python -m pytest` resolves package import correctly. [~95% confidence]
- **Suggested action**: Standardize test invocation in docs/CI to `python -m pytest ...` (or set `PYTHONPATH=.` consistently).

## [2026-03-09T17:33:00+10:30] — Existing mock-mode stream tests failing with current baseline behavior

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Run diagnostics/build/tests and verify image handling behavior
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Two tests in `tests/test_chat_stream.py` failed due expectation mismatch for mock response content marker `(mock)`; current emitted mock token stream text does not include that marker.
- **Evidence**:
  - `tests/test_chat_stream.py::test_chat_stream_emits_done_mock_mode` failed on `assert "(mock)" in body`.
  - `tests/test_chat_stream.py::test_openai_chat_completions_non_streaming_mock_mode` failed on `assert "(mock)" in choice["message"]["content"]`.
- **Likely cause**: Pre-existing mismatch between test expectations and current mock token text (`"Mock response tokens from Daemon"` in `orchestrator/daemon.py`) and/or non-stream collector behavior in mock mode. [~85% confidence]
- **Suggested action**: Align mock implementation and test expectations (either restore `(mock)` marker in emitted content or update assertions).
- **Seen again**: Appears unrelated to multimodal attachment implementation.

## [2026-03-09T17:34:00+10:30] — Existing runtime/deprecation warnings seen again in backend tests

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Run diagnostics/build/tests and verify image handling behavior
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Backend tests passed but emitted known warning patterns from LiteLLM and AsyncMock-injected settings.
- **Evidence**:
  - LiteLLM deprecation warning: `asyncio.iscoroutinefunction` slated for removal in Python 3.16.
  - Runtime warnings from `orchestrator/memory/injection.py:104,106` and `orchestrator/main.py:1258` about coroutine mocks not awaited during tests.
- **Likely cause**: Existing test harness/mocking behavior and upstream library warning on Python 3.14. [~90% confidence]
- **Suggested action**: Keep tracked; fix AsyncMock usage in tests and monitor LiteLLM update.
- **Seen again**: Matches known historical warning patterns.

## [2026-03-09T17:23:00+10:30] — Existing basedpyright warning debt seen again during multimodal attachment implementation

- **Severity**: info
- **Scope**: project
- **Encountered during**: Run diagnostics/build/tests and verify image handling behavior
- **Category**: other
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` on modified backend files returned many existing basedpyright warnings (`reportExplicitAny`, `reportAny`, `reportUnknown*`, `reportCallInDefaultInitializer`) unrelated to this feature work.
- **Evidence**:
  - `orchestrator/main.py` warnings include existing strict-typing debt (`reportExplicitAny`, `reportAny`, and unknown-member type warnings).
  - `orchestrator/daemon.py` and `orchestrator/tools/completion.py` diagnostics similarly report pre-existing strict typing issues.
- **Likely cause**: Known baseline strict-typing debt in backend modules with broad dynamic typing patterns. [~95% confidence]
- **Suggested action**: Keep this as non-blocking; schedule focused basedpyright cleanup across backend modules.
- **Seen again**: Similar basedpyright warning pattern already present in TRIAGE history.

## [2026-03-09T16:28:21+10:30] — Existing pytest warning patterns seen again after standard-format upload support

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Verify standardized skill markdown upload support
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted backend tests passed after upload parser updates, but recurring LiteLLM deprecation and unawaited AsyncMock warnings were emitted.
- **Evidence**:
  - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Likely cause**: Existing upstream LiteLLM warning + existing async mock setup in chat-history test path. [~90% confidence]
- **Suggested action**: Track upstream LiteLLM fixes and clean AsyncMock usage in tests.
- **Seen again**: Same warning classes already tracked in TRIAGE.

---

## [2026-03-09T16:17:29+10:30] — Backend hot-reload crash from invalid FastAPI Form annotation default

- **Severity**: critical
- **Scope**: project
- **Encountered during**: Implement markdown skill upload endpoint
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: Backend crashed during reload after updating `/skills/upload` route signature, making API endpoints temporarily unresponsive.
- **Evidence**:
  - `AssertionError: Form default value cannot be set in Annotated for 'overwrite'. Set the default value with = instead.`
  - Stack trace points to `orchestrator/routes/skills.py` at `@router.post("/upload")`.
- **Likely cause**: FastAPI disallows `Form(False)` inside `Annotated` metadata for defaults; default must be declared as parameter default (`= False`). [~99% confidence]
- **Suggested action**: Use `overwrite: Annotated[bool, Form()] = False` in route signature. Applied; backend health and upload endpoint recovered.

---

## [2026-03-09T16:17:29+10:30] — Python bytecode write permission denied during compile check

- **Severity**: warning
- **Scope**: host
- **Encountered during**: Verify backend Python module syntax after skills updates
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `python -m py_compile` failed because environment could not write `.pyc` into `orchestrator/routes/__pycache__`.
- **Evidence**:
  - `PermissionError: [Errno 13] Permission denied: 'orchestrator/routes/__pycache__/skills.cpython-314.pyc....tmp'`
- **Likely cause**: Host/container filesystem ownership mismatch for `__pycache__` in this workspace. [~90% confidence]
- **Suggested action**: Use non-bytecode syntax validation (`ast.parse`) or correct ownership/permissions for write checks.

---

## [2026-03-09T16:17:29+10:30] — Existing pytest warning patterns seen again after upload feature fix

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Re-run backend chat tests after skills upload endpoint fix
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted backend tests passed, but recurring LiteLLM deprecation and AsyncMock runtime warnings were emitted.
- **Evidence**:
  - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Likely cause**: Known upstream LiteLLM warning and existing test mock patterns in chat-history tests. [~90% confidence]
- **Suggested action**: Track upstream LiteLLM updates and clean up AsyncMock usage in tests.
- **Seen again**: Same warning classes already logged in TRIAGE.

---

## [2026-03-09T15:56:03+10:30] — Backend startup crash from TypedDict compatibility in skills store

- **Severity**: critical
- **Scope**: project
- **Encountered during**: Diagnose actual API errors behind settings/chats failures
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: Backend failed to start after skills feature changes. All frontend API calls (`/conversations`, `/users/me/settings`, `/memories`, `/skills`) failed because the server process crashed during import.
- **Evidence**:
  - `pydantic.errors.PydanticUserError: Please use typing_extensions.TypedDict instead of typing.TypedDict on Python < 3.12.`
  - Trace points to `orchestrator/skills_store.py` import path from `orchestrator/routes/skills.py`.
  - Direct probes before fix failed with connection resets on `http://localhost:8000/*`.
- **Likely cause**: `TypedDict` imported from `typing` in Python 3.11 is incompatible with Pydantic v2 model generation path. [~99% confidence]
- **Suggested action**: Use `from typing_extensions import TypedDict` in `skills_store.py` and restart backend. Applied in this task; backend recovered and endpoints returned 200.

---

## [2026-03-09T15:56:03+10:30] — Existing pytest warning patterns seen again after backend recovery

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Verify backend after startup crash fix
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted backend tests passed post-fix, but recurring LiteLLM deprecation and AsyncMock runtime warnings were emitted.
- **Evidence**:
  - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Likely cause**: Known upstream LiteLLM warning and existing async mock patterns in test suite. [~90% confidence]
- **Suggested action**: Continue tracking upstream LiteLLM updates and clean async mock usage in tests.
- **Seen again**: Same warning classes already logged previously.

---

## [2026-03-09T15:01:09+10:30] — Existing pytest warning patterns seen again during skills feature verification

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Verify Skills API/routes + settings Skills tab integration
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted backend chat tests passed, with recurring LiteLLM deprecation warnings and unawaited AsyncMock runtime warnings from existing chat-history test paths.
- **Evidence**:
  - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Likely cause**: Known upstream LiteLLM deprecation + existing AsyncMock patterns in tests, unrelated to new Skills implementation. [~90% confidence]
- **Suggested action**: Keep tracking upstream LiteLLM update and clean up AsyncMock setup in affected tests to reduce warning noise.
- **Seen again**: Same warning classes already logged previously in TRIAGE.

---

## [2026-03-09T14:12:58+10:30] — Settings Memory tab failed due to frontend-relative API path

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Center settings content across all tabs
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: Memory tab threw `Failed to fetch memories` because it requested `/memories?...` relative to the frontend app instead of the backend API origin.
- **Evidence**:
  - Browser error overlay at `components/settings/MemoryTab.tsx:42` with message `Failed to fetch memories`
  - `fetch('/memories?limit=1')` / `fetch('/memories?confirm=true')` observed in MemoryTab before fix
- **Likely cause**: Endpoint URL mismatch in frontend component; unlike other settings tabs, MemoryTab was not using `NEXT_PUBLIC_API_URL` + bearer auth headers. [~97% confidence]
- **Suggested action**: Keep MemoryTab calls on `${NEXT_PUBLIC_API_URL}/memories` with auth headers; verify API-key availability and surface a friendlier error message when unauthorized.

---

## [2026-03-09T11:14:44+10:30] — Next.js prerender blocked by missing Suspense around useSearchParams

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Frontend build verification after adding chats/projects/artifacts pages
- **Category**: build-error
- **Blocked current task**: yes
- **What happened**: `npm run build` failed while prerendering `/artifacts` because `useSearchParams()` (inside shared conversation history hook/provider) was not wrapped in a Suspense boundary on the new page routes.
- **Evidence**:
  - `Error occurred prerendering page "/artifacts"`
  - `useSearchParams() should be wrapped in a suspense boundary at page "/artifacts"`
- **Likely cause**: New routes used `ConversationHistoryProvider` (which depends on `useSearchParams`) without route-level Suspense wrappers required by Next.js App Router static prerendering. [~98% confidence]
- **Suggested action**: Wrap route content in `Suspense` for pages using the provider/hook. Applied for `/chats`, `/projects`, and `/artifacts`; subsequent build passed.

---

## [2026-03-08T17:41:00+10:30] - Transient frontend typecheck failure after widening tool_result payload shape
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Fixing React crash for object tool results in chat timeline
- **Category**: build-error
- **Blocked current task**: yes
- **What happened**: After changing `tool_result.result` type to `unknown`, `next build` failed in `frontend/app/page.tsx` where `isChatEvent` is used as a type predicate over `data` values. The predicate requires `ChatEvent` to remain assignable to `JSONValue`.
- **Evidence**:
  - `Type error: A type predicate's type must be assignable to its parameter's type.`
  - At `./app/page.tsx:469:30` with note `Type 'ChatEvent' is not assignable to type 'JSONValue'` due `result: unknown`.
- **Likely cause**: Overly strict event type update (`unknown`) broke compatibility with AI SDK `JSONValue` data contract [~98% confidence].
- **Suggested action**: Keep `tool_result.result` JSON-compatible (`any`/serializable) in event type definitions and normalize at render boundaries.

---

## [2026-03-08T17:28:00+10:30] - basedpyright strict typing warnings in `tests/test_chat_stream.py`
- **Severity**: info
- **Scope**: project
- **Encountered during**: LSP verification for subagent spawn pipeline fix
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` on `tests/test_chat_stream.py` returned numerous basedpyright warnings (unknown/missing parameter types and unknown member types) in existing async pytest patterns.
- **Evidence**:
  - Repeated warnings such as `reportUnknownParameterType`, `reportMissingParameterType`, and `reportUnknownMemberType` across many test lines.
  - Seen again during graceful tool-failure implementation verification (2026-03-08T18:03+10:30).
  - Seen again during pending-tool finalization guard verification (2026-03-08T18:12+10:30).
  - Seen again while adding `completion_with_tools` max-round synthesis regression coverage (2026-03-08T18:24+10:30).
- **Likely cause**: Test module is not fully typed for strict basedpyright settings; this predates current fix and does not block runtime behavior [~95% confidence].
- **Suggested action**: Add explicit type annotations for pytest fixtures/monkeypatch/client helpers or reduce strictness for test modules.

---

## [2026-03-08T17:08:00+10:30] - Existing `/chat` mock stream test expectation mismatch
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Verification for runtime datetime context injection
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Running `tests/test_chat_stream.py::test_chat_stream_emits_done_mock_mode` failed on an assertion expecting `"(mock)"` in SSE body; targeted new datetime-context tests still passed.
- **Evidence**:
  - `AssertionError: assert '(mock)' in 'event: conversation\n...event: done\n...'`
  - Same run also logged `WARNING orchestrator.main - No conversation_id provided; title generation disabled`.
- **Likely cause**: Test assertion drift from current mock stream payload format in `/chat` endpoint, unrelated to datetime-context injection changes [~85% confidence].
- **Suggested action**: Update the mock stream test to assert stable fields/events (`token/final/done`) and expected payload shape instead of legacy literal marker text.

---

## [2026-03-08T16:47:00+10:30] - TypeScript hints seen again in `frontend/app/page.tsx` during diagnostics
- **Severity**: info
- **Scope**: project
- **Encountered during**: Chat formatting update verification (thinking/user bubble UI)
- **Category**: other
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` for `frontend/app/page.tsx` reported pre-existing TypeScript hints (unused declarations and deprecated API hints) while validating this UI-only change.
- **Evidence**:
  - `(6133)` unused declarations such as `useConversationHistory`, `RetryButton`, `MicButton`, `error`, `currentId`.
  - `(6385)` deprecated usages including `FormEvent` and `isLoading` references.
  - Seen again during final verification for streaming-thinking/scroll fix task (2026-03-08T16:59+10:30).
- **Likely cause**: Existing chat-page technical debt and stale imports/variables unrelated to the current UI style adjustments [~95% confidence].
- **Suggested action**: Run a focused cleanup pass on `frontend/app/page.tsx` to remove unused symbols and address deprecated APIs.

---

## [2026-03-08T16:29:00+10:30] - Frontend lint script incompatible with Next.js 16 CLI behavior
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Additional verification after ConversationList key fix
- **Category**: config
- **Blocked current task**: no
- **What happened**: Lint verification commands failed. `npm run lint -- --file components/ConversationList.tsx` returned `unknown option '--file'`, and `npm run lint` returned `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`.
- **Evidence**:
  - `next lint --file components/ConversationList.tsx` -> `error: unknown option '--file'`
  - `next lint` -> `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`
- **Likely cause**: Legacy `next lint` script no longer aligns with current Next.js 16 CLI semantics in this project setup [~90% confidence].
- **Suggested action**: Update lint script to a supported command (e.g., direct ESLint invocation) and document the new workflow.

---

## [2026-03-08T16:23:00+10:30] - Transient frontend JSX parse failure after key-fix edit
- **Severity**: warning
- **Scope**: project
- **Encountered during**: ConversationList key warning remediation verification
- **Category**: build-error
- **Blocked current task**: yes
- **What happened**: A verification build failed with a JSX parse error in `frontend/components/ConversationList.tsx` after updating grouped list rendering to use keyed elements. The unpinned-section fragment was left unclosed.
- **Evidence**: `x Expected '</', got ')'` at `./components/ConversationList.tsx:283:13` during `next build`.
- **Likely cause**: Incomplete refactor while replacing mapped fragment with keyed group container [~98% confidence].
- **Suggested action**: Keep a focused syntax verification pass (`lsp_diagnostics` + `npm run build`) immediately after JSX list refactors.

---

## [2026-03-08T16:09:00+10:30] - Markdown LSP unavailable for diagnostics verification
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Post-fix verification (`lsp_diagnostics` on modified file)
- **Category**: config
- **Blocked current task**: no
- **What happened**: Running `lsp_diagnostics` on `TRIAGE.md` returned an error because no LSP server is configured for `.md` files in this environment.
- **Evidence**: `Error: No LSP server configured for extension: .md`.
- **Likely cause**: Markdown LSP not configured in `oh-my-opencode.json` (expected in this toolchain) [~95% confidence].
- **Suggested action**: Optional: configure a markdown-capable LSP if markdown diagnostics are desired.
- **Seen again**: 2026-03-09T21:08:47+10:30 — `lsp_diagnostics` on `data/skills/document-docx.md` and `data/skills/document-csv.md` returned the same `.md` LSP-not-configured error during skill-file blocker verification.

---

## [2026-03-08T16:06:00+10:30] - Reported frontend module-not-found for `next-themes` not reproducible after dependency verification
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Frontend build error triage (`Can't resolve 'next-themes'`)
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: A user-reported Next.js build error showed `Module not found: Can't resolve 'next-themes'` from `frontend/components/AccountWidget.tsx`. In this workspace, `next-themes` is present in `package.json`/lockfiles, installed in `node_modules`, and `npm run build` completed successfully.
- **Evidence**:
  - Reported trace: `AccountWidget.tsx -> ConversationList.tsx -> app/page.tsx` with missing `next-themes`.
  - `npm ls next-themes` => `next-themes@0.4.6` under `frontend/`.
  - `npm run build` in `frontend/` succeeded (Next.js 16.1.6, compiled and generated static pages).
- **Likely cause**: Dependency installation drift in the failing environment (e.g., `node_modules` not up to date for `frontend/`) rather than a source-code import defect in this snapshot [~85% confidence].
- **Suggested action**: Reinstall dependencies in `frontend/` (`npm install` or `bun install`), then restart dev/build process.

---

## [2026-03-08T22:24:52+10:30] - Correction: Kimi canonical OpenRouter ID is moonshotai variant
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Verify model-id remediation correctness
- **Category**: config
- **Blocked current task**: no
- **What happened**: External verification showed the prior replacement to `openrouter/kimi/kimi-k2.5` was incorrect. OpenRouter currently resolves Kimi K2.5 under `moonshotai/kimi-k2.5`, so code/docs/tests were corrected back to `openrouter/moonshotai/kimi-k2.5`.
- **Evidence**:
  - `https://openrouter.ai/kimi/kimi-k2.5` reports model unavailable.
  - `https://openrouter.ai/moonshotai/kimi-k2.5` resolves and shows `moonshotai/kimi-k2.5`.
  - OpenRouter `/api/v1/models` listing contains `"id":"moonshotai/kimi-k2.5"`.
  - Repo now uses `openrouter/moonshotai/kimi-k2.5` across config/catalog/main/tests/docs.
- **Likely cause**: Earlier remediation followed an outdated triage inference rather than authoritative OpenRouter slug evidence [~90% confidence].
- **Suggested action**: Add a startup/model-validation check to assert configured model IDs exist in provider model catalog before deployment.

---

## [2026-03-08T22:11:37+10:30] - Critical triage review: actionable project issue fixed
- **Severity**: info
- **Scope**: project
- **Encountered during**: Fix actionable critical project-scope issues
- **Category**: config
- **Blocked current task**: no
- **What happened**: Reviewed all current critical entries and remediated the actionable project-scope issue: stale OpenRouter model id references were replaced with `openrouter/kimi/kimi-k2.5` across runtime defaults, model catalog, fallback response payload, tests, and technical specs.
- **Evidence**:
  - `openrouter/moonshotai/kimi-k2.5` now appears only in `TRIAGE.md` historical evidence.
  - `PYTHONPATH=/home/sol/daemon uv run pytest tests/test_featured_models.py::test_catalog_endpoint_returns_featured -v` passed.
  - `frontend` build succeeded (`npm run build`, Next.js 16.1.6).
  - `stream_sse_chat` signature includes `ping_interval_s` and persistence calls use supported `metadata` fields.
- **Likely cause**: Legacy identifier drift remained in config/docs/tests after provider model path change [~95% confidence].
- **Suggested action**: Keep model IDs centralized and add a unit test asserting configured featured IDs map to catalog IDs.

---

## [2026-03-08T22:09:39+10:30] - Pytest import path failure in uv run
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Verify fixes (diagnostics/build/tests) and update triage status
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Running `uv run pytest tests/test_featured_models.py::test_catalog_endpoint_returns_featured -v` failed before test execution because pytest could not import the `orchestrator` package from `tests/conftest.py`.
- **Evidence**: `ModuleNotFoundError: No module named 'orchestrator'` during `from orchestrator.main import app` in `tests/conftest.py`.
- **Likely cause**: Python module path is not set for direct pytest invocation in this environment (missing project root in `PYTHONPATH`) [~90% confidence].
- **Suggested action**: Run pytest with `PYTHONPATH=/home/sol/daemon` or ensure package install/editable mode in test environment setup.

---

## [2026-03-08T22:09:39+10:30] - basedpyright warnings seen again in touched backend files
- **Severity**: info
- **Scope**: project
- **Encountered during**: Verify fixes (diagnostics/build/tests) and update triage status
- **Category**: other
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` surfaced many existing basedpyright warnings in `orchestrator/main.py`, `orchestrator/config.py`, and `tests/test_featured_models.py` while validating this change.
- **Evidence**: Warnings include `reportExplicitAny`, `reportCallInDefaultInitializer`, `reportUnusedImport`, and `reportAny` (e.g., `orchestrator/main.py`, `orchestrator/config.py`).
  - Seen again in `orchestrator/daemon.py` during graceful tool-failure verification (2026-03-08T18:03+10:30).
  - Seen again in `orchestrator/daemon.py` during pending-tool finalization verification (2026-03-08T18:12+10:30).
  - Seen again in `orchestrator/tools/completion.py` during max-round synthesis fix verification (2026-03-08T18:24+10:30).
- **Likely cause**: Pre-existing strict typing/lint debt in backend/test modules, not introduced by the model-id replacement [~95% confidence].
- **Suggested action**: Schedule a dedicated basedpyright cleanup pass and narrow CI policy for high-signal warnings first.

---

## [2026-03-08T22:11:37+10:30] - Config access command used wrong symbol name
- **Severity**: info
- **Scope**: project
- **Encountered during**: Verify fixes (diagnostics/build/tests) and update triage status
- **Category**: other
- **Blocked current task**: no
- **What happened**: A verification one-liner attempted `from orchestrator.config import settings` and failed because the module exports `get_settings()` instead of a `settings` symbol.
- **Evidence**: `ImportError: cannot import name 'settings' from 'orchestrator.config'`.
- **Likely cause**: Verification command used outdated import assumption; module API exposes `get_settings()` factory [~95% confidence].
- **Suggested action**: Use `from orchestrator.config import get_settings` and call `get_settings()` in ad-hoc checks/scripts.

---

## 2026-03-08T20:46:08+10:30 — `bun test` fails: no frontend test files detected

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Frontend code-quality verification (build/test/lint)
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Running `bun test` in `frontend/` exits with an error because no files match Bun's default test globs.
- **Evidence**: `error: 0 test files matching **{.test,.spec,_test_,_spec_}.{js,ts,jsx,tsx} in --cwd="/home/sol/daemon/frontend"`
- **Likely cause**: No unit test files exist in the frontend tree, or the project uses a different runner (e.g. Playwright / Next experimental test mode) without wiring `bun test` to it. [~85% confidence]
- **Suggested action**: Either add at least one `*.test.ts(x)` file (or adjust `bun test` patterns), or change the documented frontend test command to the runner actually used (e.g. `bunx playwright test`, `next experimental-test`, etc.).

---

## 2026-03-08T20:46:08+10:30 — `console.log` left in UI component

- **Severity**: info
- **Scope**: project
- **Encountered during**: Frontend code-quality verification (anti-pattern scan)
- **Category**: lint
- **Blocked current task**: no
- **What happened**: UI code contains a `console.log` call that will fire in the browser.
- **Evidence**: `frontend/components/AccountWidget.tsx:84` contains `console.log("Logout clicked")`
- **Likely cause**: Debug logging left behind during UI development. [~90% confidence]
- **Suggested action**: Remove the log, or gate it behind a dev-only flag (e.g. `if (process.env.NODE_ENV !== 'production')`).

---

## 2026-03-08T17:49:00+10:30 — `next dev` lockfile permission denied on alternate port

- **Severity**: warning
- **Scope**: host
- **Encountered during**: TODO 27 route smoke verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: `bunx next dev -p 3001 --webpack` started but aborted with lockfile acquisition error (`Permission denied`).
- **Evidence**: `/tmp/daemon-frontend-dev-3001.log` lines 6-11: `An IO error occurred while attempting to create and acquire the lockfile` / `Permission denied (os error 13)`.
- **Likely cause**: stale lockfile ownership/permissions from prior process or mixed user contexts. [~75% confidence]
- **Suggested action**: clear stale Next lock artifacts and normalize ownership in frontend workspace before dev-server workflows.

---

## 2026-03-08T17:44:00+10:30 — Direct `eslint` run fails due flat-config migration

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: TODO 27 verification run
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Running `bunx eslint app components hooks lib --ext .ts,.tsx` failed because ESLint v9 expects `eslint.config.js` and did not detect a flat-config file.
- **Evidence**: `ESLint couldn't find an eslint.config.(js|mjs|cjs) file`.
- **Seen again**: 2026-03-08T20:46:08+10:30 during frontend code-quality verification.
- **Likely cause**: repository still relies on Next lint integration or legacy `.eslintrc` setup while global/local eslint is v9 flat-config mode. [~80% confidence]
- **Suggested action**: add flat config (`eslint.config.js`) or use project-supported lint command/tooling path.

---

## 2026-03-08T17:31:00+10:30 — `bun run lint` invokes invalid Next CLI path

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: TODO 24/27 verification run
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Running `bun run lint` in `frontend/` executes `next lint` but fails with `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`.
- **Evidence**: command output: `$ next lint` then `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`.
- **Seen again**: 2026-03-08T20:46:08+10:30; additionally, `next lint --help` does not list `lint` as a supported command under Next.js `16.1.6` in this environment.
- **Likely cause**: Next.js CLI/lint command incompatibility with current toolchain (Next 16 + bun invocation), where `lint` is parsed as a directory argument. [~75% confidence]
- **Suggested action**: switch lint script to explicit eslint command (e.g. `eslint .`) or invoke Next lint with confirmed supported syntax for this Next version.

---

## 2026-03-07T10:42:00Z — TypeScript LSP unavailable in environment

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Validation after stream/remount and settings fixes
- **Category**: config
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` for `frontend/app/page.tsx` could not run because `typescript-language-server` is not installed in this environment.
- **Evidence**: `Error: LSP server 'typescript' is configured but NOT INSTALLED. Command not found: typescript-language-server`
- **Likely cause**: Missing global TypeScript language server/tooling in host environment. [~95% confidence]
- **Suggested action**: Install with `npm install -g typescript-language-server typescript` (or provide workspace-local LSP wiring).

---

## 2026-03-07T10:42:30Z — `uv run pytest` failed due venv permissions

- **Severity**: warning
- **Scope**: host
- **Encountered during**: Validation after backend store parsing hardening
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Running `uv run pytest tests/test_store.py -q` failed before tests executed because uv tried to recreate `.venv` and could not remove `/home/sol/daemon/.venv/CACHEDIR.TAG`.
- **Evidence**: `error: failed to remove file '/home/sol/daemon/.venv/CACHEDIR.TAG': Permission denied (os error 13)`
- **Likely cause**: Project `.venv` ownership/permissions mismatch from container/host cross-usage. [~90% confidence]
- **Suggested action**: Repair ownership/permissions of `.venv` or recreate virtual env with host user before running uv-managed tests.

---

## 2026-02-20T03:23:07Z — File glob searches timing out / needing recursive pattern

- **Severity**: info
- **Encountered during**: TODO 1 — Inspect current model selection handling
- **Category**: other
- **Blocked current task**: no
- **What happened**: Several `glob` tool calls against `/home/sol` timed out, and non-recursive patterns (e.g. `orchestrator/main.py`) returned no matches until rewritten with a recursive `**/` prefix.
- **Evidence**: `Error: Glob search timeout after 60000ms` (multiple calls)
- **Likely cause**: Large search root + tool implementation expects recursive patterns for nested paths. [~70% confidence]
- **Suggested action**: Prefer scoping glob searches to `/home/sol/daemon` and use `**/` patterns when matching nested files.

---

## 2026-02-20T03:29:24Z — basedpyright LSP server missing (lsp_diagnostics unavailable)

- **Severity**: warning
- **Encountered during**: TODO 4 — Run lsp_diagnostics on changed files and run pytest
- **Category**: dependency
- **Blocked current task**: yes
- **What happened**: `lsp_diagnostics` failed because the configured Python LSP server (`basedpyright`) is not installed in this environment.
- **Evidence**: `LSP server 'basedpyright' is configured but NOT INSTALLED. Command not found: basedpyright-langserver`
- **Likely cause**: Dev tooling dependency not installed (not listed in `pyproject.toml` dev deps). [~85% confidence]
- **Suggested action**: Install `basedpyright` (e.g. `pip install basedpyright`) or switch LSP config to use `pyright`.

---

## 2026-02-20T03:42:21Z — pytest suite fails on unmarked async test + runtime warnings

- **Severity**: warning
- **Encountered during**: TODO 4 — Run lsp_diagnostics on changed files and run pytest
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Running `pytest` fails on `test_subagents.py::test_subagents` because it's an `async def` test without an async plugin marker under `pytest-asyncio` strict mode. Test run also emits multiple deprecation/runtime warnings (unawaited AsyncMock calls).
- **Evidence**:
  - Failure:
    ```
    FAILED test_subagents.py::test_subagents - Failed: async def functions are not natively supported.
    You need to install a suitable plugin for your async framework...
    ```
  - Warnings include:
    - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated...` from `litellm_core_utils/logging_utils.py:273`
    - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited` from `tests/memory/test_extraction.py` and `orchestrator/memory/injection.py`
- **Likely cause**: `pytest-asyncio` is installed but configured in strict mode; `async def` tests require `@pytest.mark.asyncio` (or equivalent) and/or proper async fixtures. Unawaited AsyncMock warnings indicate mocks configured as async but used sync. [~90% confidence]
- **Suggested action**: Mark `test_subagents.py::test_subagents` with `@pytest.mark.asyncio` (or convert to sync), and audit/await AsyncMock usages to eliminate runtime warnings.

---

## 2026-02-20T03:44:30Z — basedpyright LSP initially used wrong environment until restart

- **Severity**: info
- **Encountered during**: TODO 4 — Run lsp_diagnostics on changed files and run pytest
- **Category**: config
- **Blocked current task**: no
- **What happened**: After installing `basedpyright`, `lsp_diagnostics` initially reported missing third-party imports (e.g. `fastapi`) until the `basedpyright-langserver` process was restarted.
- **Evidence**: `error[basedpyright] (reportMissingImports): Import "fastapi" could not be resolved` in `orchestrator/main.py`
- **Likely cause**: LSP server started before venv/config discovery; server did not reload environment settings without a restart. [~70% confidence]
- **Suggested action**: Ensure venv discovery (e.g. `pyrightconfig.json`) is present before starting the LSP, or restart `basedpyright-langserver` when changing env/config.

---

## Triage Summary

- 4 issues logged during this session
- Critical: 0 | Warning: 2 | Info: 2
- Items requiring attention:
  - basedpyright LSP server missing (lsp_diagnostics unavailable)
  - pytest suite fails on unmarked async test + runtime warnings

---

## 2026-02-20T04:26:15Z — pytest collection fails under system python (fastapi missing)

- **Severity**: critical
- **Encountered during**: TODO 4 — Run pytest: cd /home/sol/daemon && python -m pytest tests/ -v -k model
- **Category**: dependency
- **Blocked current task**: yes
- **What happened**: Running `python -m pytest tests/ -v -k model` uses `/usr/bin/python` and fails during collection because `fastapi` is not installed in that interpreter.
- **Evidence**:
  ```
  platform linux -- Python 3.14.3 ... -- /usr/bin/python
  ModuleNotFoundError: No module named 'fastapi'
  ```
- **Likely cause**: Project deps are installed in `/home/sol/daemon/.venv`, but the shell `python` points at system Python. [~90% confidence]
- **Suggested action**: Activate the venv (`source .venv/bin/activate`) before running `python -m pytest ...`, or call `/home/sol/daemon/.venv/bin/python -m pytest ...` directly. Also ensure CI uses the same interpreter.

---

## Triage Summary

- 1 issue logged during this session
- Critical: 1 | Warning: 0 | Info: 0
- Items requiring attention:
  - pytest collection fails under system python (fastapi missing)

## 2026-02-20T06:34:13Z — LiteLLM DeprecationWarning from asyncio.iscoroutinefunction

- **Severity**: warning
- **Encountered during**: Verification — `python -m pytest tests/test_model_override.py -v`
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Pytest emitted repeated `DeprecationWarning` warnings from LiteLLM due to use of `asyncio.iscoroutinefunction`, which is deprecated in Python 3.14 and slated for removal in Python 3.16.
- **Evidence**:
  ```
  DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead
    /home/sol/.local/lib/python3.14/site-packages/litellm/litellm_core_utils/logging_utils.py:273
  ```
- **Likely cause**: LiteLLM dependency uses a deprecated asyncio API. [~90% confidence]
- **Suggested action**: Upgrade LiteLLM to a version compatible with Python 3.14+ (or patch to use `inspect.iscoroutinefunction`).

---

## 2026-02-20T07:05:18Z — basedpyright warnings in orchestrator/config.py

- **Severity**: info
- **Encountered during**: Verification — `lsp_diagnostics` on `orchestrator/config.py`
- **Category**: lint
- **Blocked current task**: no
- **What happened**: basedpyright reports existing warnings about `Any` usage and unannotated class attributes in `orchestrator/config.py`.
- **Evidence**:
  - `reportExplicitAny` at lines 34, 153, 168, 281
  - `reportUnannotatedClassAttribute` at line 49
- **Likely cause**: current codebase uses `Any` in settings models and lacks class attribute annotations. [~80% confidence]
- **Suggested action**: Annotate class attributes and reduce `Any` usage or relax basedpyright settings for config modules.

---

## 2026-02-20T14:40:00Z — Messages schema mismatch: code references updated_at but migration lacks it

- **Severity**: warning
- **Encountered during**: TODO 1 — Read context and message store code
- **Category**: config
- **Blocked current task**: no
- **What happened**: The runtime persistence code updates `messages.updated_at`, but the schema in `migrations/004_create_messages.sql` does not define an `updated_at` column for `messages`.
- **Evidence**:
  - `migrations/004_create_messages.sql` defines `messages` without `updated_at`.
  - `orchestrator/daemon.py` runs:
    ```sql
    UPDATE messages
    SET tokens_in = $2,
        tokens_out = $3,
        model = COALESCE($4, model),
        updated_at = NOW()
    WHERE id = $1
    ```
- **Likely cause**: `messages.updated_at` was added manually or intended in a later migration that was never committed. [~70% confidence]
- **Suggested action**: Confirm actual production schema for `messages` and either (a) add an explicit migration adding `updated_at` or (b) remove/guard the `updated_at` assignment in the backfill query.

---

## 2026-02-20T14:45:00Z — pytest fails when DAEMON_API_KEY set (featured models tests get 401)

- **Severity**: warning
- **Encountered during**: TODO 4 — Verify (pytest)
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `uv run pytest` fails in `tests/test_featured_models.py` because the API enforces bearer auth when `DAEMON_API_KEY` is set, but the test client does not send an `Authorization` header.
- **Evidence**:
  ```
  E   AssertionError: Model openrouter/moonshotai/kimi-k2.5 failed: {"detail":"Missing bearer token"}
  E   assert 401 == 200
  tests/test_featured_models.py:42
  ```
- **Likely cause**: `.env` contains `DAEMON_API_KEY` (so auth is enabled) and `get_settings()` is cached across tests; `tests/test_chat_stream.py::test_api_key_authentication` sets `DAEMON_API_KEY` and clears the cache once, but never clears it again after the env var is restored, so later tests still see an auth-enabled settings object. [~80% confidence]
- **Suggested action**: Make tests hermetic by ensuring `get_settings.cache_clear()` runs after tests that mutate `DAEMON_API_KEY` (or add an autouse fixture that clears settings cache per-test), and/or have `tests/test_featured_models.py` include the auth header when auth is enabled.

---

## 2026-02-20T14:48:00Z — compileall fails due to unwritable __pycache__ directories

- **Severity**: warning
- **Encountered during**: TODO 4 — Verify (build/compile)
- **Category**: config
- **Blocked current task**: no
- **What happened**: `uv run python -m compileall orchestrator` reports `PermissionError` when trying to write `.pyc` files into `orchestrator/**/__pycache__/`.
- **Evidence**:
  ```
  *** PermissionError: [Errno 13] Permission denied: 'orchestrator/memory/__pycache__/store.cpython-314.pyc.139667023900528'
  ```
- **Likely cause**: `__pycache__` directories/files were created by a different user (e.g., root via Docker) and are not writable by the current user. [~75% confidence]
- **Suggested action**: Fix ownership/permissions for `orchestrator/**/__pycache__` (or run compile checks without writing bytecode).

---

## 2026-02-20T15:00:00Z — Sisyphus plan/notepad paths missing from repo

- **Severity**: info
- **Encountered during**: TODO 1 — Read current project issues/context + thinking-modal-persistence plan for guardrail expectations
- **Category**: config
- **Blocked current task**: no
- **What happened**: The repo does not contain `.sisyphus/plans/` or `.sisyphus/notepads/`, so the referenced plan file and notepad location could not be found.
- **Evidence**:
  - `glob .sisyphus/plans/*.md` → `No files found`
  - `glob .sisyphus/**/*` → `No files found`
- **Likely cause**: This workspace snapshot was created without Sisyphus plan artifacts (or they live outside the repo). [~80% confidence]
- **Suggested action**: If plan/notepads are required for this workflow, ensure `.sisyphus/` is present in the working copy or provide the plan name/path explicitly.

---

## 2026-02-20T15:24:38Z — pytest fails: featured models tests get 401 Missing bearer token

- **Severity**: warning
- **Encountered during**: TODO 5 — Run builds/tests to ensure green
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Running the full pytest suite fails in `tests/test_featured_models.py` because `/v1/chat/completions` requires bearer auth (401) but the test requests do not send an `Authorization` header.
- **Evidence**:
  ```
  E       AssertionError: Model openrouter/moonshotai/kimi-k2.5 failed: {"detail":"Missing bearer token"}
  E       assert 401 == 200
  tests/test_featured_models.py:42
  ```
- **Likely cause**: `get_settings()` is cached (lru_cache). A prior test enables auth by setting `DAEMON_API_KEY` and clearing the cache once, but does not clear it again after restoring the env var. Subsequent tests still see an auth-enabled settings object, so requests without bearer tokens get 401. [~80% confidence]
- **Suggested action**: Make tests hermetic: add an autouse fixture that clears `get_settings.cache_clear()` per-test, and/or have `tests/test_featured_models.py` include the auth header when auth is enabled.

---

## 2026-03-07T10:32:00+10:30 — Git stash/pull emits permission warnings on root-owned Next build artifacts

- **Severity**: warning
- **Scope**: host
- **Encountered during**: User request — pull latest changes from GitHub
- **Category**: config
- **Blocked current task**: no
- **What happened**: Pull was initially blocked by local changes. During `git stash push` (with and without `-u`), Git emitted many `Permission denied` unlink warnings for `frontend/.next.root.*` and `frontend/.next_broken/*` files owned by `root`, and stash reapply produced unmerged entries that required manual conflict resolution.
- **Evidence**:
  - `warning: unable to unlink 'frontend/.next.root.1770199650/...': Permission denied`
  - `warning: unable to unlink 'frontend/.next_broken/...': Permission denied`
  - `ls -ld frontend/.next.root.1770199650` => `drwxr-xr-x ... root root ...`
- **Likely cause**: Frontend build artifacts were previously generated by a root-owned process (likely containerized tooling), so current user cannot clean/update those paths during stash/pull workflows. [~95% confidence]
- **Suggested action**: Normalize ownership for generated frontend artifact directories (or remove them from tracking/working tree usage) so regular git operations do not hit permission errors.

---

## 2026-02-20T15:30:56Z — pytest passes but emits aiohttp DeprecationWarning (enable_cleanup_closed)

- **Severity**: info
- **Encountered during**: TODO 5 — Run builds/tests to ensure green
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Pytest run succeeds, but emits an `aiohttp` `DeprecationWarning` about `enable_cleanup_closed` being ignored on Python 3.14.3.
- **Evidence**:
  ```
  /home/sol/daemon/.venv/lib/python3.14/site-packages/aiohttp/connector.py:993: DeprecationWarning: enable_cleanup_closed ignored because https://github.com/python/cpython/pull/118960 is fixed in Python version sys.version_info(major=3, minor=14, micro=3, releaselevel='final', serial=0)
  ```
- **Likely cause**: aiohttp version still emits compatibility warning for a Python issue that is already fixed in 3.14.3. [~70% confidence]
- **Suggested action**: Upgrade `aiohttp` (or adjust warning filters in tests) to reduce noise.

---

## 2026-02-21T02:44:20Z — Playwright Chrome browser missing (MCP launch fails)

- **Severity**: critical
- **Encountered during**: TODO 1 — Open http://localhost:3000 and confirm frontend is reachable
- **Category**: dependency
- **Blocked current task**: yes
- **What happened**: Playwright MCP failed to launch a persistent Chromium context because the configured Chrome binary was not found.
- **Evidence**:
  ```
  Error: browserType.launchPersistentContext: Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome
  Run "npx playwright install chrome"
  ```
- **Likely cause**: Playwright is configured to use the `chrome` channel, but the Chrome distribution is not installed in this environment. [~90% confidence]
- **Suggested action**: Install the browser via MCP (`browser_install`) or run `npx playwright install chrome` in the repo.

---

## [2026-03-07T11:47:55Z] — Playwright delegation produced no usable output

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: reasoning stream regression verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: A delegated Playwright verification task completed under supervised mode but returned no text output/evidence; follow-up continuation request timed out, so UI assertion used stream/code evidence instead of delegated browser evidence.
- **Evidence**:
  - Delegation session `ses_3380012f9ffeJdUoy49vTgJ1kN` reported `RESULT: (No text output)`
  - Follow-up continuation call returned `Poll timeout reached after 600000ms`
- **Likely cause**: Unstable delegated model/runtime in monitored mode dropped final report payload. [~70% confidence]
- **Suggested action**: Re-run browser QA with direct local Playwright tooling or a stable agent route when strict visual evidence is required.

---

## [2026-03-07T13:03:00Z] — Frontend lint command fails due Next CLI project-dir parsing

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: thinking regressions verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `next lint` failed in this environment with `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint` for both `npm --prefix frontend run lint` and `npm run lint` in `frontend`.
- **Evidence**:
  - Command output: `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`
  - `frontend/package.json` script: `"lint": "next lint"`
- **Likely cause**: Tooling/environment quirk in Next.js 16 lint CLI invocation under this workspace runtime. [~70% confidence]
- **Suggested action**: Verify lint via alternate invocation (`npx next lint .`) or project-specific Next config; keep this as non-blocking while runtime smoke validations pass.

---

## 2026-02-21T02:46:10Z — Cannot install Playwright `chrome` channel (sudo required)

- **Severity**: warning
- **Encountered during**: TODO 1 — Open http://localhost:3000 and confirm frontend is reachable
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: Attempts to install the Playwright `chrome` channel (via MCP `browser_install` and `npx playwright install chrome`) failed because the installer tries to use `sudo`/`apt-get`, which is not available (no password / no TTY).
- **Evidence**:
  ```
  Switching to root user to install dependencies...
  sudo: a terminal is required to read the password; either use the -S option to read from standard input or configure an askpass helper
  sudo: a password is required
  Failed to install browsers
  Error: Failed to install chrome
  ```
- **Likely cause**: Playwright's `chrome` channel installer for Linux uses system package installation (apt) and requires root privileges; this environment does not permit sudo. [~90% confidence]
- **Suggested action**: Reconfigure the Playwright MCP to launch `chromium` (downloaded) instead of `channel: "chrome"`, or preinstall Google Chrome in the environment image.

---

## 2026-02-21T02:52:40Z — Playwright MCP/Node usage expects browser rev 1212 (not installed)

- **Severity**: warning
- **Encountered during**: TODO 2 — Send a message using a reasoning-capable model; open thinking modal and capture baseline text + screenshot
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: A direct Node usage of the Playwright dependency bundled with `@playwright/mcp` failed to launch because it expects Chromium Headless Shell revision `1212`, but only revision `1208` browsers are installed.
- **Evidence**:
  ```
  browserType.launchPersistentContext: Executable doesn't exist at /home/sol/.cache/ms-playwright/chromium_headless_shell-1212/chrome-headless-shell-linux64/chrome-headless-shell
  Looks like Playwright was just installed or updated.
  Please run the following command to download new browsers:
      npx playwright install
  ```
- **Likely cause**: Version mismatch: the Playwright MCP package (`@playwright/mcp@0.0.68`) depends on `playwright@1.59.0-alpha...` (rev 1212), but the environment has browsers installed for `playwright@1.58.1...` (rev 1208). [~85% confidence]
- **Suggested action**: Install matching browsers for the MCP Playwright version (rev 1212), or pin `@playwright/mcp` to a version compatible with the installed Playwright/browser revisions.

---

## 2026-02-21T02:58:10Z — lsp_diagnostics unavailable for Markdown files

- **Severity**: info
- **Encountered during**: Verification — `lsp_diagnostics` on `TRIAGE.md`
- **Category**: config
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` failed because no LSP server is configured for the `.md` extension.
- **Evidence**: `Error: No LSP server configured for extension: .md`
- **Likely cause**: The OpenCode LSP configuration does not include a Markdown language server. [~95% confidence]
- **Suggested action**: Add a Markdown LSP to `oh-my-opencode.json` (or treat Markdown files as exempt from LSP verification requirements).
- **Seen again**: 2026-03-08T14:33:00+10:30 — `lsp_diagnostics` on `TRIAGE.md` returned `No LSP server configured for extension: .md` during this task's verification pass.

---

## 2026-02-21T06:32:58Z — pytest deprecation warnings from litellm

- **Severity**: warning
- **Encountered during**: Run reasoning persistence tests
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: pytest emitted deprecation warnings about `asyncio.iscoroutinefunction` in litellm logging utils.
- **Evidence**: `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead`
- **Likely cause**: litellm uses deprecated asyncio API. [~80% confidence]
- **Suggested action**: Update litellm to a version that uses `inspect.iscoroutinefunction`, or patch logging_utils.py.

---

## 2026-02-21T06:48:48Z — frontend runtime ReferenceError: useMemo is not defined

- **Severity**: warning
- **Encountered during**: Fix thinking duration persistence
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: Chat page crashed at runtime because `useMemo` was used without being imported in `frontend/app/page.tsx`.
- **Evidence**: `ReferenceError: useMemo is not defined` at `app/page.tsx:149:35`.
- **Likely cause**: Missing `useMemo` import after adding persisted message map. [~95% confidence]
- **Suggested action**: Import `useMemo` from React where it is used.

---

## 2026-02-21T06:50:52Z — frontend build failed (Map entries typing)

- **Severity**: warning
- **Encountered during**: `npm run build`
- **Category**: build-error
- **Blocked current task**: yes
- **What happened**: Next.js build failed with a TypeScript error creating `new Map(entries)` because the entries array had an imprecise tuple type.
- **Evidence**: `Type error: No overload matches this call ... Argument of type '(string | Message)[][]' is not assignable to parameter of type 'Iterable<readonly [unknown, unknown]>'.`
- **Likely cause**: Map entries inferred as `Array<(string|Message)[]>` due to tuple typing. [~85% confidence]
- **Suggested action**: Explicitly type entries as `[string, ReasoningMessage]` tuples (or filter/flatten) before constructing the Map.

---

## 2026-02-21T17:31:00+10:30 — pytest import fails without PYTHONPATH in shell

- **Severity**: warning
- **Encountered during**: Add/adjust tests and run benchmark targets (TODOs 32-35)
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Running `pytest tests/test_extraction.py tests/test_retrieval.py tests/test_dedup_bitemporal.py tests/memory/test_extraction.py` failed at collection because `tests/conftest.py` could not import `orchestrator.config`.
- **Evidence**:
  ```
  ModuleNotFoundError: No module named 'orchestrator'
  ```
- **Likely cause**: Pytest launched with environment missing project root on `PYTHONPATH` in this shell invocation. [~85% confidence]
- **Suggested action**: Run tests via `PYTHONPATH=. pytest ...` (or `.venv/bin/python -m pytest ...`) to ensure package imports resolve consistently.

---

## 2026-02-21T17:38:00+10:30 — benchmark script syntax break after threshold edit

- **Severity**: warning
- **Encountered during**: Add/adjust tests and run benchmark targets (TODOs 32-35)
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Running `tests/benchmark_extraction.py` failed with `IndentationError` in the rich-table summary block after modifying precision/recall thresholds.
- **Evidence**:
  ```
  IndentationError: expected an indented block after 'for' statement on line 660
  ```
- **Likely cause**: Two lines (`p_style` / `r_style`) were unintentionally de-indented during patching. [~95% confidence]
- **Suggested action**: Re-indent the loop body and rerun benchmark script.

---

## 2026-02-21T18:06:00+10:30 — py_compile cannot write pyc in workspace

- **Severity**: warning
- **Encountered during**: Run full verification, update TRIAGE.md, and report results
- **Category**: config
- **Blocked current task**: no
- **What happened**: Direct `python -m py_compile ...` verification failed because Python could not write `.pyc` files under `__pycache__` in this environment.
- **Evidence**:
  ```
  [Errno 13] Permission denied: 'orchestrator/memory/__pycache__/store.cpython-314.pyc.140149622752336'
  ```
- **Likely cause**: Filesystem permission policy for generated bytecode in this runtime context. [~90% confidence]
- **Suggested action**: Use source `compile()` checks (no pyc write) or adjust cache write permissions if py_compile is required.

---

## 2026-02-21T18:55:00+10:30 — transient dedup regression: unbound `fact_slot`

- **Severity**: warning
- **Encountered during**: Run full verification, update TRIAGE.md, and report results
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: A refactor temporarily referenced `fact_slot` before assignment in dedup search, breaking all bitemporal dedup tests.
- **Evidence**:
  ```
  UnboundLocalError: cannot access local variable 'fact_slot' where it is not associated with a value
  orchestrator/memory/dedup.py:43
  ```
- **Likely cause**: Variable initialization order bug during slot-filtering patch. [~95% confidence]
- **Suggested action**: Keep `fact_slot` assignment adjacent to usage and preserve regression coverage in `tests/test_dedup_bitemporal.py` (fixed in this task).

---

## 2026-02-21T19:08:00+10:30 — extraction benchmark still misses strict scenario-level targets

- **Severity**: warning
- **Encountered during**: Add/adjust tests and run benchmark targets (TODOs 32-35)
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Final 3 benchmark reruns still fail strict scenario-level acceptance checks, despite two runs meeting aggregate precision/recall and all runs meeting adversarial=0.
- **Evidence**:
  - `post_hardening_run_1.json`: P=1.000, R=0.967, adversarial=0; scenario 3/5/6 hard checks fail.
  - `post_hardening_run_2.json`: P=1.000, R=0.967, adversarial=0; scenario 3/5/6 hard checks fail.
  - `post_hardening_run_3.json`: P=0.955, R=0.700, adversarial=0.
  - Representative detail: `✗ DEDUP: 'User sold the Corolla last month' active=True, expected active=False`.
- **Likely cause**: Live benchmark server still exhibits slot supersession drift, confidence collapse around 0.8 in hedged/allergy scenarios, and general-knowledge contamination in scenario 6. [~80% confidence]
- **Suggested action**: Continue targeted tuning of live extraction behavior (slot emission reliability, stricter user-provenance validation, and confidence calibration under benchmark runtime) and rerun benchmark against the same running backend process.

---

## 2026-02-21T19:18:00+10:30 — local uvicorn startup cannot reach configured DB/Redis hosts

- **Severity**: warning
- **Encountered during**: Run full verification, update TRIAGE.md, and report results
- **Category**: config
- **Blocked current task**: yes
- **What happened**: Attempting to run `python -m uvicorn orchestrator.main:app --port 8010` for a local benchmark target started the app but could not resolve configured Postgres and Redis hosts, forcing degraded startup without DB/Redis connectivity.
- **Evidence**:
  - `socket.gaierror: [Errno -2] Name or service not known` (PostgreSQL host resolution)
  - `redis.exceptions.TimeoutError: Timeout connecting to server`
- **Likely cause**: Local shell environment lacks reachable service hostnames from project runtime config (likely container-network-only names). [~90% confidence]
- **Suggested action**: Run benchmark against the active deployment backend, or provide locally reachable DB/Redis endpoints in env before starting uvicorn.

---

## 2026-02-22T01:07:00+10:30 — benchmark v2 runtime missing psycopg2 module

- **Severity**: warning
- **Encountered during**: Investigate scenario 3 supersession with evidence (slots, similarity, dedup branch)
- **Category**: dependency
- **Blocked current task**: yes
- **What happened**: Running `PYTHONPATH=. python tests/benchmark_extraction.py --json --scenarios 3` failed immediately due missing `psycopg2` import in benchmark v2 script.
- **Evidence**:
  ```
  ModuleNotFoundError: No module named 'psycopg2'
  ```
- **Likely cause**: Environment does not have psycopg2/psycopg installed; project runtime uses asyncpg, so benchmark's direct DB dependency is unavailable. [~95% confidence]
- **Suggested action**: Update benchmark script to use asyncpg fallback (already available) for DB wipe/query helpers, or run benchmark in environment with psycopg2 installed.

---

## 2026-02-22T06:15:00+10:30 — full benchmark recall target still below v3.1 threshold

- **Severity**: warning
- **Encountered during**: Run full benchmark 3x, assess targets/variance, and capture evidence
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: After tuning and service restarts, 3 full benchmark runs still missed recall target (>=0.97), though precision and adversarial targets passed.
- **Evidence**:
  - `tests/results/post_tuning_run_1.json`: precision=1.0000, recall=0.8333, adversarial_fp=0
  - `tests/results/post_tuning_run_2.json`: precision=1.0000, recall=0.8333, adversarial_fp=0
  - `tests/results/post_tuning_run_3.json`: precision=1.0000, recall=0.8667, adversarial_fp=0
- **Likely cause**: Benchmark expected-fact matcher still penalizes decomposed/variant phrasing in scenarios outside supersession tuning scope (notably scenarios 1/4/6). [~80% confidence]
- **Suggested action**: Calibrate scenario expected keyword matching rules (or extraction phrasing consistency) in a dedicated recall-focused pass.

---

## 2026-02-22T06:20:00+10:30 — Oracle review sessions timed out / unavailable

- **Severity**: warning
- **Encountered during**: Run Oracle review on dedup+validator updates and apply fixes
- **Category**: tooling
- **Blocked current task**: yes
- **What happened**: Multiple Oracle invocations did not return a finalized review; background retrieval either timed out or returned `Task not found` for prior task ids.
- **Evidence**:
  - `Poll timeout reached after 600000ms`
  - `Task not found: bg_ccc59467`
- **Likely cause**: Oracle agent session/task instability in this runtime. [~75% confidence]
- **Suggested action**: Re-run Oracle review in a fresh session or use synchronous deep-reasoning review as fallback.

---

## 2026-02-22T06:22:00+10:30 — test warning: unawaited AsyncMock in dedup test

- **Severity**: info
- **Encountered during**: Run final verification suite, update TRIAGE.md, and report
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Focused pytest runs pass but emit a runtime warning in one dedup test about an unawaited AsyncMock coroutine.
- **Evidence**:
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - Location: `tests/test_dedup_bitemporal.py::test_dedup_same_slot_mid_similarity_supersedes`
- **Likely cause**: Mock interaction pattern in test setup/assertion, not production code path. [~70% confidence]
- **Suggested action**: Tighten mock expectations or replace with explicit awaited mock methods to eliminate warning noise.

---

## 2026-02-22T15:48:00+10:30 — pytest import path failure from repo root

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Run targeted tests for extraction pipeline refactor
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Running `pytest tests/test_extraction.py tests/test_dedup_bitemporal.py tests/test_chat_stream.py` from repo root failed before tests started because `orchestrator` was not importable.
- **Evidence**:
  - `ModuleNotFoundError: No module named 'orchestrator'`
  - Import path: `tests/conftest.py:8` (`from orchestrator.config import get_settings`)
- **Likely cause**: PYTHONPATH/project module path not set in this shell invocation. [~90% confidence]
- **Suggested action**: Run tests with `PYTHONPATH=/home/sol/daemon` (or install package in editable mode) for this environment.
- **Seen again**: 2026-02-25 — `./.uv-venv/bin/pytest -q` failed with `ModuleNotFoundError: No module named 'orchestrator'`; the `./.uv-venv/bin/pytest` shebang points at `/home/sol/daemon/.venv/bin/python`. Workaround: `./.uv-venv/bin/python -m pytest -q`.

---

## 2026-02-22T15:50:00+10:30 — warnings seen again during targeted pytest run

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Run targeted tests for extraction pipeline refactor
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Existing warning patterns reappeared: LiteLLM coroutine deprecation warnings and one unawaited AsyncMock runtime warning in dedup test.
- **Evidence**:
  - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Likely cause**: Known upstream dependency warning + existing test mock issue. [~85% confidence]
- **Suggested action**: Track upstream LiteLLM fix and clean up AsyncMock usage in `tests/test_dedup_bitemporal.py`.
- **Seen again**: 2026-02-25 — Full suite via `./.uv-venv/bin/python -m pytest -q` passed (`96 passed, 1 skipped`) but still emitted the same warning patterns (LiteLLM `asyncio.iscoroutinefunction` deprecation, aiohttp `enable_cleanup_closed` deprecation, and unawaited `AsyncMockMixin._execute_mock_call` runtime warnings).

---

## 2026-02-22T15:52:00+10:30 — py_compile blocked by __pycache__ permissions

- **Severity**: info
- **Scope**: host
- **Encountered during**: Compile-check modified Python files
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `python -m py_compile` failed because Python could not write `.pyc` into `orchestrator/memory/__pycache__`.
- **Evidence**:
  - `[Errno 13] Permission denied: 'orchestrator/memory/__pycache__/extraction.cpython-314.pyc...'
- **Likely cause**: Mixed file ownership/permissions in repo working tree (container vs host writes). [~85% confidence]
- **Suggested action**: Use non-writing syntax checks (AST parse) or fix ownership of `__pycache__` directories.

---

## 2026-02-22T21:50:00+10:30 — benchmark still below recall target after lock refactor

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Extraction lock fix validation (3 benchmark runs)
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Benchmark v2.2 passed precision and adversarial checks but failed recall in all three runs after lock/job refactor.
- **Evidence**:
  - Run1 (`tests/results/bench_20260222_212518.json`): `P=1.00 R=0.80 Adversarial=0`
  - Run2 (`tests/results/bench_20260222_213502.json`): `P=1.00 R=0.80 Adversarial=0`
  - Run3 (`tests/results/bench_20260222_214516.json`): `P=1.00 R=0.87 Adversarial=0`
  - Persistent misses: S1 `['python']`, S4 `['memory','active']`, and variable S6 misses.
- **Likely cause**: Extraction model variance + strict keyword expectations (notably S4 whole-word pair) dominate recall outcomes despite queue/lock changes. [~80% confidence]
- **Suggested action**: Add deterministic lock-fix verification via extraction_log/message-cursor assertions; reconsider benchmark expected keywords for S4 wording variance (`memory` vs `memories`).
- **Seen again**: 2026-02-25 — Running `python tests/benchmark_extraction.py --wait 45` (benchmark v2.3) still fails recall: `TOTAL P=1.00 R=0.73`, S8 extracted=0. Biggest miss remains S6 (`P=0.00 R=0.00`, 7 FNs); also S1 recall `R=0.89` due to missing `['python']`. Evidence: `tests/results/bench_20260225_205900.json` and `benchmark_extraction_output.txt`.

---

## 2026-02-26T22:27:21+10:30 — Subagent delegation repeatedly timed out (no code changes)

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: recall-duplicate-remediation TODOs 3-6 execution
- **Category**: tooling
- **Blocked current task**: yes
- **What happened**: Multiple `task()` delegations (quick/business-logic categories) ran for 10 minutes and returned poll-timeout with no file changes, preventing orchestrated progress until direct fallback edits were used.
- **Evidence**:
  - Repeated responses: `Poll timeout reached after 600000ms` with `No file changes detected`
  - Sessions included `ses_3692a29faffeEmONTJ9N98A3QT` and `ses_369039542ffeY3W16oNaa8FdFx`
- **Likely cause**: intermittent subagent runtime instability/hang in this environment. [~75% confidence]
- **Suggested action**: retry with alternate agent/backend, shorter scoped prompts, or allow orchestrator direct-fix fallback when two consecutive no-change timeouts occur.

---

## 2026-03-04T13:36:00+10:30 — existing test warning patterns seen again during extraction regression fix

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: targeted extraction pipeline pytest validation
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted pytest run passed, but previously known warning patterns reappeared (LiteLLM coroutine deprecation + one unawaited AsyncMock warning in dedup test).
- **Evidence**:
  - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Likely cause**: known upstream dependency warning and existing test mock usage issue. [~85% confidence]
- **Suggested action**: no change for this fix; keep tracking until upstream/test cleanup is performed.

---

## 2026-03-04T16:45:00+10:30 — Extraction regression root cause identified and resolved

- **Severity**: warning
- **Scope**: project
- **Encountered during**: extraction pipeline regression remediation
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: Extraction jobs were enqueued with conversation-level IDs, but worker behavior caused unstable/partial extraction. Two concrete failures were found: (1) invalid model category `intent` triggered DB check violations and aborted extraction passes, and (2) long multi-turn deltas were sent as one large text, truncating earlier context and hurting S6 recall.
- **Evidence**:
  - Worker log: `extract_memories failed ... CheckViolationError ... failing row contains ... category=intent ... violates constraint "memories_category_check"`
  - Constraint definition (`migrations/009_update_memories_constraints.sql`): allowed categories are `fact|preference|project|summary|correction`
  - Benchmark artifacts after fix set in `tests/tests/results/`:
    - `bench_20260304_162123.json`: `P=1.00 R=0.90 A=0`
    - `bench_20260304_163148.json`: `P=1.00 R=0.93 A=0`
    - `bench_20260304_164257.json`: `P=1.00 R=0.87 A=0`
- **Likely cause**: refactor path lacked category normalization guardrails and chunking semantics equivalent to prior behavior. [~90% confidence]
- **Suggested action**: keep category normalization (`intent->project`, unknown->fact), keep chunked extraction in worker for long deltas, and keep benchmark Redis extraction-key cleanup enabled between scenarios/runs.

---

## 2026-03-07T15:16:25+10:30 — Background task result retrieval returned "Task not found"

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Analyze-mode context gathering for memory tool review-fix TODOs
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Two explore background tasks were launched successfully but subsequent `background_output` retrieval returned `Task not found` for both task IDs.
- **Evidence**:
  - `Task not found: bg_a7797d67`
  - `Task not found: bg_7dd75101`
- **Likely cause**: Transient task/session lifecycle mismatch in tool runtime. [~70% confidence]
- **Suggested action**: Re-launch exploration tasks if results are required; otherwise continue with direct file inspection (used in this task).

---

## 2026-03-07T15:16:25+10:30 — pytest unavailable in system python; uv blocked by .venv permissions

- **Severity**: warning
- **Scope**: host
- **Encountered during**: Verification — collect/run `tests/memory/test_tools.py`
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: `python -m pytest` failed because pytest is not installed in `/usr/bin/python`. `uv run pytest` then failed because uv could not remove `/home/sol/daemon/.venv/CACHEDIR.TAG` due to permission denied.
- **Evidence**:
  - `/usr/bin/python: No module named pytest`
  - `failed to remove file /home/sol/daemon/.venv/CACHEDIR.TAG: Permission denied (os error 13)`
- **Likely cause**: System interpreter lacks project test deps; project `.venv` is partially root-owned/unwritable in this environment. [~90% confidence]
- **Suggested action**: Fix ownership/permissions for `.venv` or run tests in a writable environment-specific venv before relying on local test execution.

---

## 2026-03-07T15:23:00+10:30 — memory tools test failed once due stale expected format (fixed)

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Verification — `tests/memory/test_tools.py` run in temporary uv environment
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: First test run failed in `test_memory_read_temporal_mode_slot_post_filter_with_increased_limit` because assertions expected legacy output (`[FACT] [None -> None] ...`) while tool output format now emits bullet+slot (`- [FACT] slot=...`).
- **Evidence**:
  - `AssertionError: assert '[FACT] [None -> None] memory 1' in '- [FACT] slot=slot_a memory 1\n- [FACT] slot=slot_a memory 3'`
- **Likely cause**: Test expectation drift after formatting behavior changed in `MemoryReadTool` output representation. [~95% confidence]
- **Suggested action**: Keep assertions aligned to current renderer output and include explicit line-count limit check (applied; rerun passed 13/13).

---

## 2026-03-07T15:23:00+10:30 — LiteLLM asyncio deprecation warning seen again during pytest

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Verification — `uv run pytest tests/memory/test_tools.py`
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Test runs passed but emitted repeated LiteLLM deprecation warnings about `asyncio.iscoroutinefunction` on Python 3.14.
- **Evidence**:
  - `/tmp/daemon-uv-testenv/lib/python3.14/site-packages/litellm/litellm_core_utils/logging_utils.py:273: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated ...`
- **Likely cause**: Known upstream LiteLLM warning pattern on Python 3.14. [~90% confidence]
- **Suggested action**: Track/upgrade LiteLLM to a version using `inspect.iscoroutinefunction`.
- **Seen again**: Matches prior TRIAGE entries for the same warning class.

---

## 2026-03-07T09:42:08Z — ChatContent remount risk on null→UUID URL update

- **Severity**: critical
- **Scope**: project
- **Encountered during**: TODO 10 (final) — conversation-fragmentation-fix review
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: The URL-sync effect updates `/?id=...` when a `conversation` SSE event arrives while `currentId` is null, and `ChatContent` is keyed by `currentId || "new"`. When `currentId` flips from null to UUID, React remounts `ChatContent`, which can terminate an in-flight `useChat` stream.
- **Evidence**:
  - `frontend/app/page.tsx:253` (`router.replace` on conversation event when `!currentId`)
  - `frontend/app/page.tsx:493` (`<ChatContent key={currentId || "new"} />`)
- **Likely cause**: Keyed remount strategy is coupled to URL param transitions, including the transition used for fallback conversation capture, not just deliberate conversation switching. [~95% confidence]
- **Suggested action**: Keep a stable key during the first null→UUID transition (or decouple `useChat` state from `ChatContent` key) so streaming is not interrupted.

---

## 2026-03-07T09:42:08Z — New-chat precreate flow still has stale-ID send window

- **Severity**: warning
- **Scope**: project
- **Encountered during**: TODO 10 (final) — conversation-fragmentation-fix review
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: `handleNewChat` calls `createConversation()` without awaiting completion. During the async create+push window, sending a message uses the previous `currentId` (or null), so the first message can land in the wrong conversation or trigger backend fallback conversation creation.
- **Evidence**:
  - `frontend/app/page.tsx:222` (`createConversation();` not awaited)
  - `frontend/hooks/useConversationHistory.ts:101` (`router.push` only after create response)
  - `frontend/app/page.tsx:125` (`body: { id: currentId || null }`)
  - `frontend/components/ChatInputBar.tsx:96` (submit disable does not depend on `currentId`)
- **Likely cause**: UI allows submit while navigation/conversation-id update is in flight. [~90% confidence]
- **Suggested action**: Introduce a "creating conversation" gate (disable submit) and/or await `createConversation()` before allowing first send.

---

## 2026-03-07T09:42:08Z — Conversation polling can overwrite optimistic list state

- **Severity**: warning
- **Scope**: project
- **Encountered during**: TODO 10 (final) — conversation-fragmentation-fix review
- **Category**: other
- **Blocked current task**: no
- **What happened**: Conversation polling and manual refresh replace the full `conversations` array from server data, which can temporarily drop locally optimistic entries (including freshly pre-created conversations) if backend list reads lag.
- **Evidence**:
  - `frontend/hooks/useConversationHistory.ts:61` (`setConversations(formattedConversations)` full replace)
  - `frontend/hooks/useConversationHistory.ts:72` (30s polling)
  - `frontend/app/page.tsx:262` (`refreshConversations()` after URL update)
- **Likely cause**: Last-write-wins replacement strategy for list hydration instead of merge/reconciliation by id. [~85% confidence]
- **Suggested action**: Reconcile by ID (merge server snapshot with optimistic entries) or temporarily suppress refresh until created conversation is confirmed in list payload.

---

## 2026-03-07T09:42:08Z — `stream_sse_chat` call/signature mismatch (`ping_interval_s`)

- **Severity**: critical
- **Scope**: project
- **Encountered during**: TODO 10 (final) — conversation-fragmentation-fix review
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: `/chat` route passes `ping_interval_s` into `stream_sse_chat`, but `stream_sse_chat` currently has no `ping_interval_s` parameter in its signature. This would raise `TypeError: got an unexpected keyword argument 'ping_interval_s'` when the call path executes.
- **Evidence**:
  - `orchestrator/main.py:1010` (`ping_interval_s=settings.stream_ping_interval_s`)
  - `orchestrator/daemon.py:121` (function signature lacks `ping_interval_s`)
- **Likely cause**: Drift between caller and callee after a refactor where ping handling was removed or not threaded through consistently. [~90% confidence]
- **Suggested action**: Align caller/callee signatures (add `ping_interval_s` param or remove the call argument) and run chat-stream tests immediately.
## [2026-03-07T20:35:00+10:30] — Invalid OpenRouter Model ID in Runtime Defaults

- **Severity**: critical
- **Scope**: project
- **Encountered during**: Backend `/chat` smoke verification after conversation-fragmentation fixes
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: `/chat` stream emitted `conversation` and `routing` events, then failed with OpenRouter 400 due to invalid model id `openrouter/moonshotai/kimi-k2.5`.
- **Evidence**: Backend error payload included `BadRequestError ... Requested model: openrouter/moonshotai/kimi-k2.5`.
- **Likely cause**: Stale model identifiers remained in `orchestrator/config.py` defaults and `orchestrator/catalog.py` featured list after provider namespace changes. [~96% confidence]
- **Suggested action**: Normalize defaults/catalog/tests to `openrouter/kimi/kimi-k2.5` and re-run `/chat` smoke + log checks.

## [2026-03-07T20:41:00+10:30] — Final Message Persistence Argument Mismatch

- **Severity**: critical
- **Scope**: project
- **Encountered during**: Backend `/chat` smoke verification logs review
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: After `final` SSE emission, backend logged `Failed to persist final message: MemoryStore.insert_message() got an unexpected keyword argument 'finish_reason'`, risking dropped assistant persistence/title side-effects.
- **Evidence**: Backend logs showed warning exactly: `Failed to persist final message: MemoryStore.insert_message() got an unexpected keyword argument 'finish_reason'`.
- **Likely cause**: `stream_sse_chat` passed unsupported kwargs (`finish_reason`, `usage`, plus update-call arg mismatch) to `MemoryStore` methods with narrower signatures. [~99% confidence]
- **Suggested action**: Keep persistence call contract aligned to store signatures: write `finish_reason`/`usage` under `metadata`, use `status="complete"`, and use dict-key access for insert return IDs.

---

## [2026-03-08T14:12:00+10:30] — Background task retrieval returned "Task not found" (seen again)

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Review project context + exploration for conversation-delete frontend bug
- **Category**: other
- **Blocked current task**: no
- **What happened**: Explore background tasks were created successfully but later retrieval via `background_output` returned `Task not found` for both task IDs.
- **Evidence**:
  - `Task ID: bg_380e497c` launch succeeded, then `Task not found: bg_380e497c`
  - `Task ID: bg_0e6221a6` launch succeeded, then `Task not found: bg_0e6221a6`
- **Likely cause**: Task-tracking/session state inconsistency in background task tooling (known recurring issue). [~80% confidence]
- **Suggested action**: Investigate background task persistence and retrieval consistency; correlate task IDs with session IDs in task backend logs.
- **Seen again**: 2026-03-08T14:30:00+10:30 — newly launched explore/librarian tasks (`bg_218c13ef`, `bg_1610b0f8`, `bg_9e47d1b0`, `bg_3a104fe9`, `bg_36c21ef5`, `bg_1f20fd2f`, `bg_29c27c9f`) all returned `Task not found` on `background_output`; fallback used direct code search.

---

## [2026-03-08T14:13:00+10:30] — `/tmp` screenshot/glob access produced host-level file errors

- **Severity**: info
- **Scope**: host
- **Encountered during**: Screenshot inspection for conversation-delete UI state
- **Category**: config
- **Blocked current task**: no
- **What happened**: One screenshot path became unavailable (`File not found`) and a broad glob scan under `/tmp` emitted multiple permission/no-such-file errors from system-private directories.
- **Evidence**:
  - `Error: File not found: /tmp/Spectacle.JCYldh`
  - `rg: /tmp/systemd-private-...: Permission denied (os error 13)`
  - `rg: /tmp/.../SingletonCookie: No such file or directory (os error 2)`
- **Likely cause**: Ephemeral temp files cleaned up and restricted system-managed directories under `/tmp`. [~95% confidence]
- **Suggested action**: Capture screenshots in stable workspace paths for debugging and avoid broad `/tmp` glob scans; target explicit known directories.

---

## [2026-03-08T14:19:00+10:30] — Frontend lint tooling mismatch (seen again + variant)

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Verification after conversation-delete state-sync fix
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `npm run lint` again failed with the known Next CLI path parsing error, and direct `npx eslint` execution failed because ESLint v9 expects `eslint.config.*` while the project uses Next-managed lint config.
- **Evidence**:
  - `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`
  - `ESLint couldn't find an eslint.config.(js|mjs|cjs) file.`
- **Likely cause**: Existing lint command/config drift in this environment (Next 16 lint command behavior + ESLint v9 flat-config expectations). [~85% confidence]
- **Suggested action**: Update lint workflow to a Next 16-compatible command or add flat-config migration path; keep as non-blocking when typecheck/build pass.
- **Seen again**: 2026-03-08T14:31:00+10:30 — same lint failures reproduced during this task while verifying `ModelSelector` UI formatting changes.

---

## [2026-03-08T14:22:00+10:30] — Background task cancel also returned "Task not found" (seen again)

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Background task cleanup before completing conversation-delete fix
- **Category**: other
- **Blocked current task**: no
- **What happened**: Explicit cancellation attempts for prior explore tasks also returned `Task not found`, consistent with earlier retrieval failures.
- **Evidence**:
  - `background_cancel(taskId="bg_380e497c")` -> `[ERROR] Task not found: bg_380e497c`
  - `background_cancel(taskId="bg_0e6221a6")` -> `[ERROR] Task not found: bg_0e6221a6`
- **Likely cause**: Same background task registry inconsistency noted earlier. [~80% confidence]
SY|- **Suggested action**: Correlate task lifecycle events (create/get/cancel) and retention in task backend.

---

## 2026-03-08T07:55:00Z — Frontend build failures (pre-existing syntax errors)

- **Severity**: critical
- **Scope**: project
- **Encountered during**: TODO 7 — Create account widget component
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Frontend build failed with multiple syntax errors in unrelated files. These are pre-existing issues not caused by AccountWidget changes.
- **Evidence**:
  - `ChatInputBar.tsx:104` — Unterminated regexp literal
  - `ConversationList.tsx:338` — Unterminated regexp literal (structural issue with conditional rendering)
  - `ErrorBoundary.tsx:57` — Duplicate `<svg` tag causing Expression expected error
- **Likely cause**: Pre-existing syntax errors in codebase, likely from recent refactoring or incomplete edits. [~95% confidence]
- **Suggested action**: Fix the syntax errors in these three files before the next build. The ConversationList.tsx issue is a structural problem where closing tags are not properly nested within conditionals.


## [2026-03-08T08:15:00Z] — Pre-existing TypeScript errors in frontend components

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Build verification for ProfileTab component
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Running `npx tsc --noEmit` revealed existing TypeScript errors in unrelated frontend files (ChatInputBar.tsx, ConversationList.tsx, ErrorBoundary.tsx) that predate the ProfileTab work.
- **Evidence**:
  ```
  components/ChatInputBar.tsx(59,5): error TS2657: JSX expressions must have one parent element
  components/ChatInputBar.tsx(104,11): error TS1005: ':' expected
  components/ConversationList.tsx(269,11): error TS1005: '}' expected
  components/ErrorBoundary.tsx(58,19): error TS1005: '>' expected
  ```
- **Likely cause**: Existing files have syntax/JSX errors unrelated to current task. [~90% confidence]
- **Suggested action**: Fix these TypeScript errors in a separate maintenance pass; they are pre-existing issues not caused by ProfileTab changes.

---

## [2026-03-09T10:56:00+10:30] — pytest import path failure without PYTHONPATH (seen again)

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Run diagnostics/tests/build for tool-call and image refresh persistence fix
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Initial pytest invocation failed at collection because `tests/conftest.py` could not import the `orchestrator` package when run without `PYTHONPATH`.
- **Evidence**:
  - `ModuleNotFoundError: No module named 'orchestrator'`
  - `ImportError while loading conftest '/home/sol/daemon/tests/conftest.py'`
- **Likely cause**: Known shell/env path issue where repo root is not on Python import path for direct `pytest` calls. [~95% confidence]
- **Suggested action**: Continue running tests with `PYTHONPATH=/home/sol/daemon` (or install package editable) in this environment.
- **Seen again**: Matches prior TRIAGE entries documenting the same `ModuleNotFoundError` pattern.

---

## [2026-03-09T10:58:00+10:30] — Existing pytest warning patterns seen again during targeted run

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Run diagnostics/tests/build for tool-call and image refresh persistence fix
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Targeted backend tests passed, but emitted recurring LiteLLM deprecation warnings and unawaited `AsyncMock` runtime warnings.
- **Evidence**:
  - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Likely cause**: Known upstream LiteLLM warning + existing async mock patterns in chat-history test path. [~85% confidence]
- **Suggested action**: Track upstream LiteLLM fix and clean up AsyncMock usage in test fixtures/mocks to reduce warning noise.
- **Seen again**: Same warning classes already tracked in earlier TRIAGE entries.

---

## [2026-03-09T19:33:10+10:30] — Comment/docstring hook warning triggered while adding DocumentSubagent

- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Create `orchestrator/subagents/document.py` (document-generation-subagent TODO 8)
- **Category**: other
- **Blocked current task**: no
- **What happened**: The repository hook emitted an immediate warning after file creation because the requested implementation includes many inline comments and docstrings.
- **Evidence**:
  - Hook output: `COMMENT/DOCSTRING DETECTED - IMMEDIATE ACTION REQUIRED`
  - File: `orchestrator/subagents/document.py` (new file created exactly as requested implementation)
- **Likely cause**: Tooling hook enforces strict comment/docstring policy and flagged the required template content. [~95% confidence]
- **Suggested action**: Keep as-is for this task because implementation was explicitly mandated; if policy should be strict, align plan templates with hook expectations.

---

## [2026-03-09T19:34:30+10:30] — py_compile blocked by __pycache__ permissions (seen again)

- **Severity**: warning
- **Scope**: host
- **Encountered during**: Run diagnostics/build verification for `orchestrator/subagents/document.py`
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Direct compile verification with `python -m py_compile orchestrator/subagents/document.py` failed because Python could not write bytecode in `orchestrator/subagents/__pycache__`.
- **Evidence**:
  - `[Errno 13] Permission denied: 'orchestrator/subagents/__pycache__/document.cpython-314.pyc.139752436077872'`
- **Likely cause**: Existing workspace ownership/permissions issue for `__pycache__` writes in this environment. [~95% confidence]
- **Suggested action**: Use non-bytecode compile verification (`compile()`/AST) or fix filesystem ownership for Python cache directories.
- **Seen again**: Matches prior TRIAGE entries for py_compile permission-denied behavior.

---
