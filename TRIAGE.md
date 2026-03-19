# Triage Log

> Auto-generated diagnostic capture. Items here were encountered during task
> execution but fall outside the immediate task scope. Review and action as needed.

---

## [2026-03-19T09:15:00+00:00] — YouTube fetch runtime failure due transcript snippet shape mismatch

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Fetch-service YouTube summarization regression diagnosis
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: `web_fetch` failed for YouTube URLs even when URL routing correctly selected `YouTubeTranscriptStrategy`, because transcript formatting assumed dict-style segments while runtime library returned `FetchedTranscriptSnippet` objects.
- **Evidence**:
  - `WARNING:orchestrator.services.fetch.strategies.youtube:YouTube transcript fetch failed for https://www.youtube.com/watch?v=VZfW3YTJ5Eg: 'FetchedTranscriptSnippet' object is not subscriptable`
  - `tool output: {"error": "Failed to fetch content from URL"}`
- **Likely cause**: `youtube-transcript-api` current return type uses object attributes (`.start`, `.text`) not mapping keys; formatter accessed `segment["start"]` / `segment["text"]`. [~98% confidence]
- **Suggested action**: Normalize transcript segment access to support both mapping and attribute-based snippet objects; add regression test covering object snippet return type.

---

## [2026-03-19T05:57:00+00:00] — System Python verification misses declared `trafilatura` dependency

- **Severity**: info
- **Scope**: host
- **Encountered during**: `[orchestrator/services/fetch/service.py + verification] Run diagnostics and targeted validation for new fetch service — expect clean diagnostics and passing compile/checks`
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: Import verification with the bare `python` interpreter failed while importing the new fetch service because `trafilatura` was unavailable in that interpreter, even though the project declares it as a dependency.
- **Evidence**:
  - `ModuleNotFoundError: No module named 'trafilatura'`
  - `pyproject.toml:19` -> `"trafilatura>=1.12.0",`
- **Likely cause**: The host `python` executable is not running inside the project's managed dependency environment; `uv run python` or the project venv should be used for runtime verification. [~90% confidence]
- **Suggested action**: Run Python verification commands through `uv run` (or activate the project environment) so declared dependencies are available consistently.

---

## [2026-03-15T03:40:00+00:00] — Sora estimate verification command in plan misses required `user_id`

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Final Verification Wave F3/F4 manual API smoke checks
- **Category**: config
- **Blocked current task**: no
- **What happened**: Running the plan's documented verification command for `/video-credits/estimate` without `user_id` returned FastAPI validation error (`query.user_id` required). Re-running with `user_id` succeeded and returned a valid estimate payload.
- **Evidence**:
  - Request: `/video-credits/estimate?provider=openai_sora&duration=10&resolution=720p&tier=pro`
  - Response: `{"detail":[{"type":"missing","loc":["query","user_id"],"msg":"Field required","input":null}]}`
  - Request with user: `/video-credits/estimate?...&user_id=00000000-0000-0000-0000-000000000001`
  - Response: `{"credits_required":60,"current_balance":0,"sufficient":false}`
- **Likely cause**: Endpoint contract requires `user_id`, but plan verification snippet was not updated to include it. [~95% confidence]
- **Suggested action**: Update `.sisyphus/plans/sora-video-provider.md` verification command to include `user_id`.

---

## [2026-03-15T03:41:00+00:00] — Local backend smoke run degraded without Postgres/Redis services

- **Severity**: info
- **Scope**: host
- **Encountered during**: Final Verification Wave F3 manual API smoke checks
- **Category**: config
- **Blocked current task**: no
- **What happened**: Temporary `uvicorn` smoke run logged startup degradation because local hostnames `postgres` and `redis` were unavailable in this environment. API endpoints still responded for lightweight checks, but full credit-backed/manual stack behavior was not representatively validated.
- **Evidence**:
  - `Failed to connect to PostgreSQL — running without DB`
  - `Failed to connect to Redis — running without Redis`
  - `socket.gaierror: [Errno -2] Name or service not known`
- **Likely cause**: Local runtime launched without docker-compose network/services, so service hostnames were not resolvable. [~95% confidence]
- **Suggested action**: Run final manual QA against full compose stack (`postgres` + `redis`) for production-representative verification.

## [2026-03-15T02:58:00+00:00] — LSP diagnostics unavailable for Markdown files

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: F4 Scope Fidelity Check verification step
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` cannot run on `.md` files (`TRIAGE.md`, notepad) because no Markdown LSP is configured in this environment.
- **Evidence**:
  - `Error: No LSP server configured for extension: .md`
  - Available servers list excludes Markdown
- **Likely cause**: Workspace LSP configuration does not include a Markdown language server. [~99% confidence]
- **Suggested action**: Add a Markdown-capable LSP in `oh-my-opencode.json` if Markdown diagnostics are required in verification checklists.

---

## [2026-03-15T02:55:00+00:00] — Scope-fidelity baseline includes massive unrelated change set

- **Severity**: warning
- **Scope**: project
- **Encountered during**: F4 Scope Fidelity Check for `sora-video-provider`
- **Category**: other
- **Blocked current task**: no
- **What happened**: Scope verification found a very large mixed diff (`git diff --name-only HEAD~20` = 234 files, 227 out of Sora-plan scope) plus 53 untracked paths including build artifacts, dependencies, debug scripts, and non-Sora feature work. This makes strict task-to-task attribution noisy and indicates heavy scope contamination.
- **Evidence**:
  - `git diff --name-only HEAD~20` -> `total_changed=234`
  - Scope allowlist check -> `unaccounted=227`
  - `git status --short` -> `modified_or_deleted=121`, `untracked=53`
- **Likely cause**: Multiple independent workstreams and generated artifacts coexisting in one branch/worktree while final Sora scope verification was run. [~95% confidence]
- **Suggested action**: Re-run F4 from a clean branch/worktree containing only Sora TODO 1-14 changes (or isolate via precise commit range/tag) before approving.
- **Seen again**: 2026-03-15T03:42:00+00:00 during repeated F4 scope diff (`git diff --name-only HEAD~20`) while closing Final Verification Wave; unrelated change inventory remains broad and non-isolated.

---

## [2026-03-14T10:02:21+10:30] — `next dev` lockfile permission error in local QA run

- **Severity**: warning
- **Scope**: host
- **Encountered during**: Task 20 hands-on QA setup for Studio mode toggle
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Launching `npm run dev -- --port 3001` failed before serving pages due a lockfile creation/acquisition IO permission error.
- **Evidence**:
  - `Error: An IO error occurred while attempting to create and acquire the lockfile`
  - `Permission denied (os error 13)`
  - Source: `/tmp/daemon-frontend-dev.log`
- **Likely cause**: Host filesystem permissions on Next.js lockfile/cache paths in this environment. [~80% confidence]
- **Suggested action**: Check ownership/permissions of frontend runtime lock/cache directories; use `next start` fallback (worked) for QA until corrected.
- **Seen again**: 2026-03-14T23:17:58+10:30 during Task 24 regression fix runtime probe when attempting `npm --prefix frontend run dev -- --port 3300`; same lockfile `Permission denied (os error 13)` behavior.

---

## [2026-03-14T10:02:21+10:30] — Studio QA emits repeated 404 console errors without backend API routing

- **Severity**: info
- **Scope**: project
- **Encountered during**: Task 19/20 Playwright verification against local `next start`
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: Playwright Studio smoke test passed UI assertions, but browser console reported repeated 404 fetch failures because Studio controls attempted backend credit/model endpoints not available on the standalone frontend host.
- **Evidence**:
  - Multiple `Failed to load resource: the server responded with a status of 404 (Not Found)` messages in Playwright output (desktop and mobile)
  - QA command still concluded `OK video=2 credit=2 models=2 compact=2 mobileControls=1`
- **Likely cause**: Frontend-only runtime (`http://localhost:3001`) lacked reachable backend base URL/API proxy for `/video-credits/*` and related calls. [~90% confidence]
- **Suggested action**: Run integrated frontend+backend stack (or set `NEXT_PUBLIC_API_URL`) for clean end-to-end Studio verification.
- **Seen again**: 2026-03-14T10:35:00+10:30 during Task 21 runtime probe on `/studio` before request mocking; same missing backend/proxy behavior reproduced.
- **Seen again**: 2026-03-14T12:38:39+10:30 during Task 22 final runtime QA (`/studio` on port 3300) with one residual browser `404` console line while flow assertions passed (`complete`, `Cost: 6 credits`, local video visible); likely ancillary asset request noise.
- **Seen again**: 2026-03-14T12:50:00+10:30 during Task 24 navigation QA on `/studio` (`3400`), with explicit endpoint 404s for `/video-credits/balance?user_id=00000000-0000-0000-0000-000000000001` and `/conversations?limit=100` in standalone frontend runtime.

---

## [2026-03-14T10:37:00+10:30] — `npx tsc --noEmit` fails on missing `.next/types` route files

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 21 verification (Studio image generation/provider selection)
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Frontend typecheck failed with multiple TS6053 errors because `tsconfig` include paths reference generated `.next/types/**` files that were not present in this environment at check time.
- **Evidence**:
  - `error TS6053: File '/home/sol/daemon/frontend/.next/types/app/api/audio/tts/route.ts' not found.`
  - `error TS6053: File '/home/sol/daemon/frontend/.next/types/app/api/chat/route.ts' not found.`
  - `error TS6053: File '/home/sol/daemon/frontend/.next/types/app/studio/page.ts' not found.`
- **Likely cause**: TypeScript configuration depends on Next-generated type artifacts that can drift or be absent until a fresh generation step aligns `.next/types` with current route structure. [~80% confidence]
- **Suggested action**: Ensure deterministic generation of `.next/types` before standalone `tsc --noEmit` (or adjust CI/typecheck script to use the supported Next.js typecheck path).

---

## [2026-03-14T10:38:00+10:30] — Frontend QA server start failed due port conflict on 3001

- **Severity**: info
- **Scope**: host
- **Encountered during**: Task 21 runtime QA setup
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Starting `next start` on port 3001 failed because another process was already bound to that port; subsequent QA used port 3100 successfully.
- **Evidence**:
  - `Error: listen EADDRINUSE: address already in use :::3001`
  - Source: `/tmp/daemon-frontend-start.log`
- **Likely cause**: Pre-existing local process occupying port 3001 from prior session. [~95% confidence]
- **Suggested action**: Free/standardize QA port usage before starting local servers, or auto-select a free port in verification scripts.

---

## [2026-03-14T09:51:07+10:30] — Frontend lint script invokes invalid Next.js command

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 19 verification (`CreditBalance` frontend component)
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Running frontend lint via `npm run lint` failed immediately because the `next lint` invocation was interpreted as an invalid project directory path (`.../frontend/lint`) instead of executing a lint command.
- **Evidence**:
  - `> daemon-frontend@0.1.0 lint`
  - `> next lint`
  - `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`
- **Likely cause**: Project lint script is outdated for the current Next.js CLI behavior/version and should use the supported linting entrypoint for this setup. [~85% confidence]
- **Suggested action**: Update `frontend/package.json` lint script to the supported command for Next.js 16 in this repo, then re-run lint in CI/local checks.

---

## [2026-03-14T13:41:46+00:00] — Permission denied errors during py_compile cache writes

- **Severity**: info
- **Scope**: host
- **Encountered during**: Final verification of image provider abstraction implementation
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Running `python -m py_compile` on modified modules produced `PermissionError` when trying to write `.pyc` files to `__pycache__` directories, though syntax checking itself succeeded.
- **Evidence**: 
  - `PermissionError: [Errno 13] Permission denied: 'orchestrator/subagents/__pycache__/image.cpython-314.pyc.139957611976096'`
  - `PermissionError: [Errno 13] Permission denied: 'orchestrator/__pycache__/config.cpython-314.pyc.140261654692848'`
- **Likely cause**: Host/container filesystem ownership mismatch for `__pycache__` directories in development environment. [~90% confidence]
- **Suggested action**: Use non-bytecode syntax validation (`ast.parse`) or correct ownership/permissions for cache directories. Does not affect runtime behavior or implementation correctness.

---

## [2026-03-14T12:45:59+10:30] — `tsx` import interop mismatch for Next client component in QA harness

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 23 inline video rendering verification script
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: A direct named import from `frontend/components/ToolCallBlock.tsx` failed under `npx tsx` (`does not provide an export named 'ToolCallBlock'`). Using default import and destructuring (`default.ToolCallBlock`) worked.
- **Evidence**:
  - `SyntaxError: The requested module './components/ToolCallBlock.tsx' does not provide an export named 'ToolCallBlock'`
  - `npx tsx` export probe returned keys: `['default', 'module.exports']`
- **Likely cause**: Module interop behavior in ad-hoc `tsx` execution for Next client modules wraps exports under default/module.exports. [~85% confidence]
- **Suggested action**: Keep QA harness imports resilient (`default` fallback) or add a dedicated frontend test runner config for stable TSX module resolution.

---

## [2026-03-14T12:58:22+10:30] — Stale `next start` instance served Studio chunk 500 during QA

- **Severity**: info
- **Scope**: host
- **Encountered during**: Task 24/25 Playwright runtime QA on Studio
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: A running server on port `3300` returned HTTP 500 for `/_next/static/chunks/app/studio/page-*.js`, leaving the page effectively blank to Playwright. Restarting on a fresh port with a fresh build (`3600`) resolved it.
- **Evidence**:
  - `HTTP 500 http://127.0.0.1:3300/_next/static/chunks/app/studio/page-5478ccd3425bf002.js`
  - Browser error: `Failed to load resource: the server responded with a status of 500 (Internal Server Error)`
- **Likely cause**: Stale `next start` process serving build artifacts out of sync with recent frontend changes. [~80% confidence]
- **Suggested action**: Restart `next start` after rebuilds (or use a fresh QA port) before browser verification to avoid stale chunk mismatches.

---

## [2026-03-14T15:46:21+10:30] — Basedpyright reports local test typing noise in `test_xai_imagine.py`

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 28 unit tests for xAI Imagine client
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `tests/test_xai_imagine.py` passes under pytest (`11 passed`), but `lsp_diagnostics` still reports many basedpyright warnings about partially unknown lambda parameter types and lightweight mock helper annotations.
- **Evidence**:
  - `warning[basedpyright] (reportUnknownLambdaType) ... DummyAsyncClient`
  - `warning[basedpyright] (reportUnannotatedClassAttribute) at 19:13 ... status_code`
- **Likely cause**: Strict local basedpyright settings are flagging lightweight test doubles and monkeypatch lambdas in a passing test module. [~90% confidence]
- **Suggested action**: If desired, introduce more fully typed mock helpers or relax strictness for test files; not required for runtime correctness.

---

## [2026-03-14T16:20:00+10:30] — DAL test/migration amount-sign semantics disagree on spend transactions

- **Severity**: info
- **Scope**: project
- **Encountered during**: Task 29 unit tests for video credits
- **Category**: other
- **Blocked current task**: no
- **What happened**: The DAL stores `spend` transaction amounts as positive integers (`db/video_credits.py`), while the original migration comment in `migrations/017_video_credits.sql` says spent amounts are negative. The tests passed against current DAL behavior, but the comment and implementation disagree.
- **Evidence**:
  - `db/video_credits.py`: `INSERT INTO video_credit_transactions (user_id, type, amount, ...) VALUES ($1, 'spend', $2, ...)`
  - `migrations/017_video_credits.sql`: `amount INTEGER NOT NULL,  -- Positive for credits added, negative for credits spent`
- **Likely cause**: The schema comment drifted from the implemented ledger convention after the DAL was written. [~95% confidence]
- **Suggested action**: Decide on one canonical sign convention and update either the migration comment/docs or the DAL so transaction exports and future analytics are not misleading.

---

## [2026-03-14T13:48:31+10:30] — Basedpyright reports broad legacy typing noise in backend files

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 26 backend integrity verification
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: LSP diagnostics on touched backend files reported a large volume of pre-existing basedpyright warnings/errors unrelated to the Task 26 logic itself, including unknown `AppState.video_credits_dal`, missing `asyncpg` stubs, and pervasive `Any` / override / default-initializer complaints.
- **Evidence**:
  - `Cannot access attribute "video_credits_dal" for class "AppState"`
  - `Stub file not found for "asyncpg"`
  - `Type 'Any' is not allowed`
- **Likely cause**: Existing backend typing baseline is not aligned with current basedpyright strictness, especially around dynamic app-state attributes and legacy utility modules. [~90% confidence]
- **Suggested action**: Add typed `video_credits_dal` to `AppState`, install/provide `asyncpg` stubs or suppress missing-stub noise, and reduce legacy `Any` usage so LSP can become signal-bearing again.
- **Seen again**: 2026-03-14T13:02:00+10:30 during Task 27 security hardening; backend LSP still reports broad legacy noise (including stale `daemon_admin_api_key` / `Any` / missing-stub complaints) despite successful AST parse and passing frontend build/typecheck.

---

## [2026-03-14T13:48:31+10:30] — LiteLLM emits Python 3.14 deprecation warnings in tests

- **Severity**: info
- **Scope**: upstream
- **Encountered during**: Task 26 chat-stream pytest verification
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: The relevant `tests/test_chat_stream.py` subset passed, but pytest emitted repeated LiteLLM warnings about `asyncio.iscoroutinefunction` deprecation on Python 3.14.
- **Evidence**:
  - `/home/sol/.local/lib/python3.14/site-packages/litellm/litellm_core_utils/logging_utils.py:273`
  - `DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead`
- **Likely cause**: Upstream LiteLLM still uses deprecated asyncio API on newer Python versions. [~95% confidence]
- **Suggested action**: Track LiteLLM upgrade/fix upstream or patch around the warning if Python 3.16 compatibility becomes urgent.

---

## [2026-03-14T23:23:11+10:30] — Video credits estimate proxy returns backend 500 in standalone local probe

- **Severity**: info
- **Scope**: project
- **Encountered during**: Task 24 post-fix runtime verification of Studio 404 regression
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: After adding the frontend `/api/video-credits/[...path]` proxy, local `next start` probes no longer returned route-level 404s, but the estimate request still returned `500 Internal Server Error` from backend processing.
- **Evidence**:
  - `GET /api/video-credits/estimate?...` -> `estimate_status=500`
  - Response body: `Internal Server Error`
  - `GET /api/video-credits/notallowed` -> `400` with `{"error":"Unsupported video credits API path"}` (proxy path validation active)
- **Likely cause**: Proxy routing is fixed, but backend estimate handling failed in this local runtime (possible missing backend dependency/config/auth context). [~75% confidence]
- **Suggested action**: Verify backend logs for `/video-credits/estimate`, then run full stack (frontend + backend) to confirm end-to-end estimate response semantics.

---

## [2026-03-19T07:36:00+00:00] — Scope-check diagnostics surface existing basedpyright violations in changed backend files

- **Severity**: warning
- **Scope**: project
- **Encountered during**: F4 scope fidelity check for `fetch-service`
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` on fetch-related changed files reported non-clean diagnostics, including hard errors in `orchestrator/tools/builtin.py`, so verification cannot be considered clean even though this audit task did not modify code.
- **Evidence**:
  - `orchestrator/tools/builtin.py`: `error[basedpyright] (reportAttributeAccessIssue) at 101:16: "Num" is not a known attribute of module "ast"`
  - `orchestrator/tools/builtin.py`: `error[basedpyright] (reportArgumentType) at 129:83` (`eval` locals mapping type mismatch)
  - `orchestrator/services/fetch/*`: multiple basedpyright warnings (`reportAny`, `reportExplicitAny`, etc.)
- **Likely cause**: Existing strict basedpyright baseline and legacy typing debt in touched modules; not introduced by this scope-audit pass. [~90% confidence]
- **Suggested action**: Triage and fix `orchestrator/tools/builtin.py` typing errors first, then re-run LSP verification on fetch-related files for a clean gate.

---

## [2026-03-19T07:37:00+00:00] — Pytest invocation fails due import path setup (`orchestrator` module not found)

- **Severity**: warning
- **Scope**: host
- **Encountered during**: F4 scope fidelity verification command run (`pytest tests/test_fetch_strategies.py tests/test_url_extract.py -q`)
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Direct pytest execution failed before running tests because Python could not import the project package from the current runtime environment.
- **Evidence**:
  - `ImportError while loading conftest '/home/sol/daemon/tests/conftest.py'`
  - `ModuleNotFoundError: No module named 'orchestrator'`
- **Likely cause**: Tests were run outside the configured project environment/module path setup (e.g., missing `uv run` or equivalent editable install context). [~90% confidence]
- **Suggested action**: Re-run tests via project runtime (`uv run pytest ...`) or ensure `PYTHONPATH`/editable install includes repository root.

---
