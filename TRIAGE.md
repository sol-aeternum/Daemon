# Triage Log

> Auto-generated diagnostic capture. Items here were encountered during task
> execution but fall outside the immediate task scope. Review and action as needed.

---

## [2026-03-21T05:54:00+10:30] — Scenario 3 dedup check regressed: Corolla remained active after Tesla correction

- **Severity**: warning
- **Scope**: project
- **Encountered during**: `[benchmark verification] run Scenario 3 + Scenario 1 after raising supersede threshold to 0.82 — expect threshold fix without supersession regressions`
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `tests/benchmark_extraction.py --scenarios 3,1 --json` completed, but Scenario 3 dedup validation failed because `2019 Toyota Corolla` remained active when the scenario expected it to be superseded by the corrected Tesla fact.
- **Evidence**:
  - Output: `✗ DEDUP: '2019 Toyota Corolla' active=True, expected active=False`
  - Saved result: `tests/results/bench_20260321_055345.json` (`dedup_results[0].pass=false`)
  - Related slots in result: Corolla `vehicle.model`, Tesla `vehicle`
- **Likely cause**: Correction extraction produced a broader slot (`vehicle`) while older fact used `vehicle.model`; current supersession path appears sensitive to slot-family/slot-shape mismatch in this pattern rather than the 0.82 threshold itself. [~80% confidence]
- **Suggested action**: Investigate supersession logic for same-family slot variants (`vehicle.model` vs `vehicle`) and add a regression test covering this exact correction path.
- **Seen again**: 2026-03-21T05:57:00+10:30 rerun of Scenario 3 passed (`tests/results/bench_20260321_055733.json`) with both facts extracted as slot `vehicle`, indicating behavior is likely extraction-slot variability rather than deterministic threshold breakage.

---

## [2026-03-20T18:54:00+10:30] — Benchmark health check fails when run inside `daemon-worker-1`

- **Severity**: info
- **Scope**: host
- **Encountered during**: `[benchmark verification] run extraction benchmark inside Docker worker for threshold recalibration — expect full benchmark metrics`
- **Category**: config
- **Blocked current task**: no
- **What happened**: Running `python tests/benchmark_extraction.py --json` inside `daemon-worker-1` failed the benchmark health check (`is Daemon running?`) because the script targets `http://localhost:8000`, which resolves to the worker container itself rather than the backend service.
- **Evidence**:
  - `docker exec daemon-worker-1 python tests/benchmark_extraction.py --json`
  - Output: `❌ Health check failed — is Daemon running?`
- **Likely cause**: Container-local networking mismatch; benchmark default base URL assumes host context (`localhost:8000`) instead of Docker service hostname (`backend:8000`) when executed inside the worker container. [~95% confidence]
- **Suggested action**: Run benchmark from host shell (as done) or add a container-aware base URL override when running inside compose services.

---

## [2026-03-20T17:53:18+10:30] — `glob` tool unavailable because `rg` binary is missing

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: `[repo docs/config] Read project context and threshold inputs to understand existing dedup settings — expect clear empirical basis for update`
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Attempting to list `.sisyphus/notepads/voyage-threshold-recalibration/*.md` with the `glob` tool failed before search execution because the tool could not spawn `/usr/bin/rg` in this environment.
- **Evidence**:
  - `Error: ENOENT: no such file or directory, posix_spawn '/usr/bin/rg'`
- **Likely cause**: The workspace tooling wrapper for `glob` depends on `ripgrep`, but `rg` is not installed or not available at the expected path on this host. [~95% confidence]
- **Suggested action**: Restore `rg` availability for agent search tools or document an alternate supported file-discovery path for this environment.

---

## [2026-03-20T17:59:34+10:30] — Supersede threshold `0.80` still admits the known `0.8046` false-positive pair

- **Severity**: warning
- **Scope**: project
- **Encountered during**: `[verification/notepads] Run diagnostics/build checks, triage anomalies, and append findings to voyage-threshold-recalibration notes — expect clean validation and recorded rationale`
- **Category**: config
- **Blocked current task**: no
- **What happened**: The requested default `dedup_supersede_threshold=0.80` was applied, but reviewing the diagnostic data against current dedup logic shows the known cross-scenario false-positive pair (`adelaide` ↔ `move melbourne`, similarity `0.8046`) still sits above the inclusive supersede comparison and would remain in the supersede band.
- **Evidence**:
  - `tests/results/voyage_similarity_analysis.json:106` -> `"similarity": 0.8046`
  - `orchestrator/memory/dedup.py:354` -> `elif similarity >= supersede_threshold:`
  - `orchestrator/config.py:260` -> `default=0.80`
- **Likely cause**: Threshold recalibration alone cannot exclude that edge case unless the supersede cutoff moves above `0.8046` or the comparison semantics/slot logic become more selective. [~95% confidence]
- **Suggested action**: Re-run the benchmark with `0.80`, then decide whether to raise generic supersede slightly above `0.8046` or tighten the supersede decision rule beyond a pure similarity cutoff.
- **Seen again**: 2026-03-20T18:53:00+10:30 during host-run benchmark verification after slot-aware matcher update; cross-scenario max in `tests/results/voyage_similarity_analysis.json` remains `0.8046`, still above configured `dedup_supersede_threshold=0.80`.

---

## [2026-03-20T14:11:00+10:30] — Chat history tests emit unawaited AsyncMock runtime warnings in memory injection path

- **Severity**: warning
- **Scope**: project
- **Encountered during**: `[verification] [HOW] to run targeted latency check plus diagnostics/tests — expect title update starts within ~2-3s and no regressions`
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `uv run pytest tests/test_chat_history.py -q` passed (`5 passed`) but emitted repeated runtime warnings that AsyncMock coroutines were never awaited while executing memory injection preference formatting code.
- **Evidence**:
  - `/home/sol/daemon/orchestrator/memory/injection.py:104 RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - `/home/sol/daemon/orchestrator/memory/injection.py:106 RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - `/home/sol/daemon/orchestrator/main.py:1458 RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Likely cause**: Existing test double setup in chat history tests returns AsyncMock values where sync-style data is expected in injection formatting paths. [~80% confidence]
- **Suggested action**: Audit the affected tests/mocks to ensure awaited async call chains are modeled correctly and warnings are eliminated.

---

## [2026-03-20T14:08:00+10:30] — `tests/test_chat_stream.py` mock-mode assertions failing in baseline

- **Severity**: warning
- **Scope**: project
- **Encountered during**: `[verification] [HOW] to run targeted latency check plus diagnostics/tests — expect title update starts within ~2-3s and no regressions`
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Running `uv run pytest tests/test_chat_stream.py -q` produced 2 failing assertions expecting `"(mock)"` in mock-mode output, while the stream payload/content did not include that marker.
- **Evidence**:
  - `FAILED tests/test_chat_stream.py::test_chat_stream_emits_done_mock_mode` (`assert "(mock)" in body`)
  - `FAILED tests/test_chat_stream.py::test_openai_chat_completions_non_streaming_mock_mode` (`assert "(mock)" in ''`)
  - Command summary: `2 failed, 11 passed`
- **Likely cause**: Existing mismatch between test expectations and current mock response formatting in chat stream code, unrelated to title enqueue timing changes. [~80% confidence]
- **Suggested action**: Update mock-mode fixtures/assertions or restore expected mock marker semantics, then re-run stream suite.

---

## [2026-03-20T14:09:00+10:30] — Frontend `typecheck` npm script missing

- **Severity**: info
- **Scope**: project
- **Encountered during**: `[verification] [HOW] to run targeted latency check plus diagnostics/tests — expect title update starts within ~2-3s and no regressions`
- **Category**: config
- **Blocked current task**: no
- **What happened**: Verification command `npm --prefix frontend run typecheck` failed because `package.json` has no `typecheck` script.
- **Evidence**:
  - `npm error Missing script: "typecheck"`
  - `npm error To see a list of scripts, run: npm run`
- **Likely cause**: Frontend package scripts do not define a standalone typecheck target; type checking currently runs as part of `next build`. [~95% confidence]
- **Suggested action**: Add an explicit `typecheck` script (for example `tsc --noEmit` or Next-supported equivalent) to make CI/local verification clearer.

---

## [2026-03-19T11:26:00+10:30] — LSP diagnostics unavailable for `.gitignore` files

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Gitignore hardening and artifact untracking verification
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: Running `lsp_diagnostics` on `.gitignore` files returned an error because no language server is configured for gitignore/extensionless files in this environment.
- **Evidence**:
  - `Error: No LSP server configured for extension:`
  - Available servers list excluded a gitignore-specific LSP
- **Likely cause**: Workspace LSP config only includes language servers for code files (TypeScript/Python/etc.), not gitignore syntax. [~99% confidence]
- **Suggested action**: Use direct file review for `.gitignore` verification or configure a gitignore-capable LSP if lint-style diagnostics are required.

---

## [2026-03-19T10:50:00+00:00] — GitHub push warning for large tracked build artifact

- **Severity**: warning
- **Scope**: project
- **Encountered during**: Commit and push of completed fetch-service work
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: `git push` succeeded but GitHub warned that a tracked build artifact exceeded the recommended file size threshold, indicating generated frontend artifacts were included in history.
- **Evidence**:
  - `remote: warning: File frontend/.next/cache/webpack/server-production/0.pack is 55.08 MB; this is larger than GitHub's recommended maximum file size of 50.00 MB`
  - `remote: warning: GH001: Large files detected. You may want to try Git Large File Storage - https://git-lfs.github.com.`
- **Likely cause**: Committed `.next` cache artifacts include large webpack pack files that should not usually be versioned. [~95% confidence]
- **Suggested action**: Remove generated `.next` artifacts from version control on a follow-up cleanup commit and enforce ignore rules (or adopt Git LFS only if intentional binary tracking is required).

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
- **Seen again**: 2026-03-19T23:56:58+10:30 during frontend verification for YouTube embed/TTS bugfix (`npm run lint` still resolves `next lint` as invalid project directory `/home/sol/daemon/frontend/lint`).

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
- **Seen again**: 2026-03-20T19:01:00+10:30 during post-fix syntax verification for matcher/embedding updates; `uv run python -m py_compile ...` failed with the same `Permission denied` on `orchestrator/memory/__pycache__/dedup...pyc`, then AST parse validation succeeded.

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
- **Seen again**: 2026-03-19T23:32:00+10:30 during memory migration regression suite (`75 passed`) with repeated warning from `litellm_core_utils/logging_utils.py:273`.
- **Seen again**: 2026-03-19T23:48:27+10:30 during post-fix targeted regression (`37 passed`) with same deprecation warning source.
- **Seen again**: 2026-03-20T18:58:00+10:30 during targeted dedup regression (`14 passed`) after embedding-input update; same warning source and message persisted.

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

## [2026-03-19T23:10:11+10:30] — Voyage SDK import requires transitive `numpy` at test collection

- **Severity**: warning
- **Scope**: project
- **Encountered during**: `tests/test_embeddings.py tests/memory/test_embedding.py tests/test_dedup_slot_fallback.py tests/test_dedup_bitemporal.py tests/memory/test_tools.py tests/memory/test_retrieval.py + verification commands`
- **Category**: dependency
- **Blocked current task**: yes
- **What happened**: `uv run pytest` failed at collection after embedding migration because importing `voyageai` raised `ModuleNotFoundError: No module named 'numpy'` from `voyageai/util.py`.
- **Evidence**:
  - `orchestrator/memory/embedding.py:11 import voyageai`
  - `.venv/lib/python3.14/site-packages/voyageai/util.py:8: import numpy as np`
  - `E ModuleNotFoundError: No module named 'numpy'`
- **Likely cause**: `numpy` is required transitively by the installed `voyageai` package in this environment but was not present in the project dependency set. [~95% confidence]
- **Suggested action**: Add `numpy` to `pyproject.toml` runtime dependencies and re-run tests in the managed environment.

---

## [2026-03-19T23:17:00+10:30] — Voyage Python SDK incompatible with Python 3.14 + current pydantic stack

- **Severity**: warning
- **Scope**: upstream
- **Encountered during**: `tests/test_embeddings.py tests/memory/test_embedding.py tests/test_dedup_slot_fallback.py tests/test_dedup_bitemporal.py tests/memory/test_tools.py tests/memory/test_retrieval.py + verification commands`
- **Category**: dependency
- **Blocked current task**: yes
- **What happened**: After installing `numpy`, test collection still failed on `import voyageai` with a pydantic v1 schema constraint error (`min_items` unenforced) under Python 3.14.
- **Evidence**:
  - `.venv/lib/python3.14/site-packages/voyageai/object/multimodal_embeddings.py:89 class MultimodalInput(BaseModel)`
  - `ValueError: On field "content" the following field constraints are set but not enforced: min_items`
  - `UserWarning: Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater`
- **Likely cause**: Current `voyageai` SDK release imports pydantic v1 model definitions that are not compatible with this Python/pydantic runtime combination. [~90% confidence]
- **Suggested action**: Use direct Voyage REST calls in production code until the SDK publishes a Python 3.14/pydantic-v2-compatible release.

---

## [2026-03-19T23:18:30+10:30] — Existing dedup test emits unawaited AsyncMock warning

- **Severity**: info
- **Scope**: project
- **Encountered during**: `tests/test_dedup_bitemporal.py` verification run
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The targeted suite passed but pytest emitted a runtime warning that an `AsyncMock` coroutine was not awaited in `tests/test_dedup_bitemporal.py:130`.
- **Evidence**:
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - `tests/test_dedup_bitemporal.py::test_dedup_same_slot_mid_similarity_supersedes`
- **Likely cause**: Pre-existing test double behavior in this module; warning is not introduced by the embedding migration change set. [~80% confidence]
- **Suggested action**: Audit AsyncMock setup in that test to ensure all mocked async call paths are awaited.
- **Seen again**: 2026-03-19T23:32:00+10:30 during broader regression run (`tests/test_dedup_bitemporal.py::test_dedup_same_slot_mid_similarity_supersedes`).

---

## [2026-03-20T13:49:07+10:30] — LSP diagnostics unavailable for SQL and `.example` files

- **Severity**: info
- **Scope**: tooling
- **Encountered during**: 1024d embedding migration verification (`lsp_diagnostics` sweep)
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` returned extension support errors for SQL migration files and `.env.example`, so those files could not be validated with LSP.
- **Evidence**:
  - `Error: No LSP server configured for extension: .sql`
  - `Error: No LSP server configured for extension: .example`
- **Likely cause**: Workspace LSP configuration includes code-language servers but not SQL or dotenv-example syntax. [~99% confidence]
- **Suggested action**: Keep manual review for SQL/env examples or configure SQL/dotenv-capable language servers.

---

## [2026-03-20T13:49:07+10:30] — Migration runner requires explicit `DATABASE_URL` in local shell

- **Severity**: info
- **Scope**: host
- **Encountered during**: 1024d migration verification via `uv run python scripts/migrate.py`
- **Category**: config
- **Blocked current task**: yes
- **What happened**: Initial migration verification attempt failed immediately with `DATABASE_URL not set`; re-running with explicit `DATABASE_URL=postgresql://daemon:daemon@localhost:5432/daemon` succeeded and applied `019_voyage_embedding_migration.sql`.
- **Evidence**:
  - `❌ DATABASE_URL not set`
  - `▶️  Applying 019_voyage_embedding_migration.sql... ✓`
- **Likely cause**: Current shell session did not export DB env vars even though compose services were running. [~95% confidence]
- **Suggested action**: Source `.env` or pass `DATABASE_URL` explicitly for local migration commands.

---
