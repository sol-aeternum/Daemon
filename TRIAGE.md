# TRIAGE.md

## 2026-06-14T01:26:26+09:30 — #45 PR Wrapper Refused On Existing Local Gate Debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #45 council read-only tool registry PR creation
- **Category**: build-error | test-failure | dependency | security
- **Blocked current task**: no
- **What happened**: `scripts/pr_create.sh` ran all local CI families and refused to call `gh pr create` because unrelated frontend blocking gates failed outside the backend-only #45 change surface. Backend and aggregate blocking gates passed; backend inventory completed and surfaced existing auth-scoping fixture errors plus additional unrelated entity/Google route failures.
- **Evidence**: Wrapper summary reported blocking failures `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`. Backend blocking `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect` passed. Backend inventory `PYTHONPATH=. uv run pytest -q` reported `3 failed, 1967 passed, 5 skipped, 100 warnings, 5 errors`: the known `tests/test_auth_user_scoping.py` async context-manager setup errors, `tests/test_entity_integration.py` `EncryptionKeyMissing` failures, and `tests/test_identity_google_routes.py` expected `["ip"]` but got `["ip", "ip"]`. Frontend type/build still failed on missing advisor event exports from `frontend/lib/events.ts`, and frontend inventory tests still had 19 advisor/tool-call failures.
- **Likely cause**: The wrapper enforces all families for a backend-only PR while main carries unrelated frontend advisor-event/lint/format debt and backend inventory test debt. Confidence: 95%.
- **Suggested action**: Open #45 directly after the documented wrapper refusal, rely on hosted branch-protection checks plus Codex review before any merge, and handle frontend advisor-event and backend inventory failures in dedicated cleanup issues.
## 2026-06-23 23:52 UTC — #108 backend inventory reproduced Google route duplicate-IP failure
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #108 advisor event type PR creation
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The PR wrapper's full backend inventory pytest completed and reported one unrelated Google auth route failure. The #108 frontend-focused type-check, advisor tests, full Vitest suite, and production build passed.
- **Evidence**: `tests/test_identity_google_routes.py::TestGoogleCompleteRoute::test_web_private_returns_access_only_and_refresh_cookie` failed because the test expected `["ip"]` but received `["ip", "ip"]`; the wrapper backend inventory summary reported `1 failed, 1967 passed, 5 skipped, 100 warnings, 5 errors`.
- **Likely cause**: Existing Google auth route/test fixture behavior is recording the same private web IP event twice, independent of the advisor event typing and frontend bridge changes (confidence 85%).
- **Suggested action**: Investigate Google complete route rate-limit/IP recording idempotency in a dedicated auth issue; do not broaden #108.

## 2026-06-12T22:34:10+09:30 — #54 PR Wrapper Refused On Existing Local Gate Debt
- **Severity**: warning
- **Scope**: project | host
- **Encountered during**: Issue #54 session cleanup grace-days PR creation
- **Category**: build-error | test-failure | dependency | security
- **Blocked current task**: no
- **What happened**: `scripts/pr_create.sh` ran all local CI families and refused to call `gh pr create` because existing local gate debt failed outside the #54 change surface. The issue-scoped session cleanup tests, changed-file backend lint/type checks, backend blocking gates, and aggregate gates had already passed.
- **Evidence**: `scripts/pr_create.sh -- --title "fix(auth): harden session cleanup grace safety" ...` reported blocking failures: `backend/basedpyright (exit=3)`, `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`. The backend type-check failure was the known worktree-local `.uv-venv` lookup: `venv .uv-venv subdirectory not found in venv path /tmp/daemon-54.` Full backend inventory reproduced the existing `tests/test_auth_user_scoping.py` fixture errors at `orchestrator/auth_runtime_state.py:97`; frontend type/build still failed on missing advisor event exports, lint still reported 28 errors / 13 warnings, format-check still reported 124 files, and frontend inventory tests still had 19 advisor/tool-call failures.
- **Likely cause**: The PR wrapper does not inherit the temporary basedpyright symlink workaround and still enforces unrelated frontend baseline debt for a backend-only session-cleanup PR. Confidence: 95%.
- **Suggested action**: Open #54 directly after the documented wrapper refusal, rely on active hosted branch-protection checks for merge eligibility, and keep wrapper env plus frontend baseline cleanup in dedicated work.

## 2026-06-12T22:29:00+09:30 — #54 Worktree Git Metadata Read-Only In Sandbox
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Issue #54 session cleanup grace-days commit
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: The temporary worktree could edit files but could not stage them because its git index lock lives under the root repository `.git/worktrees` directory, which is read-only to the managed sandbox.
- **Evidence**: `git add .env.example TRIAGE.md orchestrator/config.py orchestrator/main.py orchestrator/session_cleanup.py tests/test_session_cleanup.py` failed with `fatal: Unable to create '/home/sol/daemon/.git/worktrees/daemon-54/index.lock': Read-only file system`.
- **Likely cause**: The worktree's common git metadata is outside the writable sandbox roots even though the worktree files are writable. Confidence: 99%.
- **Suggested action**: Use escalated git commands for staging/committing/pushing temporary worktree branches, or include `.git/worktrees` in writable roots for this workflow.

## 2026-06-12T22:26:12+09:30 — #54 Session Cleanup Test Double Signature Drift
- **Severity**: info
- **Scope**: project
- **Encountered during**: Issue #54 session cleanup grace-days hardening
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The first focused session-cleanup test run failed after the production call path gained a `max_delete_fraction` argument but the lifecycle test's monkeypatched cleanup function still accepted only two positional arguments. The mock signature was updated before final verification.
- **Evidence**: `PYTHONPATH=. uv run pytest -q tests/test_session_cleanup.py` failed with `TypeError: mock_cleanup_stale_sessions() takes 2 positional arguments but 3 were given`; after updating the test double, the same focused file passed with `20 passed, 15 warnings`.
- **Likely cause**: Manual test double drift after extending the internal cleanup helper signature for the safety threshold. Confidence: 99%.
- **Suggested action**: Keep monkeypatched async helper signatures aligned with production helper signatures when adding internal parameters.

## 2026-06-13T08:34:50+09:30 — #56 PR Wrapper Refused On Existing Frontend Blocking Gates
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #56 session cleanup / refresh serialization PR creation
- **Category**: build-error | test-failure | dependency
- **Blocked current task**: no
- **What happened**: `scripts/pr_create.sh` ran all local CI families and refused to call `gh pr create` because existing frontend blocking gates failed outside the backend-only #56 change surface. Backend blocking gates and aggregate gates passed inside the wrapper run.
- **Evidence**: `scripts/pr_create.sh -- --title "fix(auth): serialize session cleanup with refresh rotation" ...` reported blocking failures: `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`. Backend `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect` passed. Frontend type/build still failed on missing advisor event exports from `frontend/lib/events.ts`; frontend inventory tests still had 19 advisor/tool-call failures; backend inventory reproduced existing `tests/test_auth_user_scoping.py` setup errors and the existing Google route rate-limit inventory failure.
- **Likely cause**: Main still carries unrelated frontend advisor-event type/build debt and repo-wide frontend lint/format debt; #56 only changes backend session cleanup and refresh rotation serialization. Confidence: 95%.
- **Suggested action**: Open #56 directly after the documented wrapper refusal, rely on hosted branch-protection checks plus Codex review before any merge, and keep frontend baseline cleanup in dedicated work.

## 2026-06-13T08:18:12+09:30 — #56 Changed-File Type Check Caught Narrow Lock Typing
- **Severity**: info
- **Scope**: project
- **Encountered during**: Issue #56 session cleanup / refresh serialization
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: The first changed-file basedpyright run failed after adding the advisory-lock helper because it accepted only `asyncpg.Connection`, while `asyncpg.Pool.acquire()` is typed as a pool connection proxy. The new concurrency test also mixed `Task[None]` and `Task[int]` in one `asyncio.gather` call.
- **Evidence**: `uv run basedpyright --level error orchestrator/session_cleanup.py orchestrator/routes/auth_setup.py tests/test_session_cleanup.py tests/test_refresh_flow.py` reported `Argument of type "PoolConnectionProxy | Unknown" cannot be assigned to parameter "conn" of type "Connection"` and `Task[int] is not assignable to parameter ... _FutureLike[None]`. After widening the helper to `Any` and wrapping cleanup in a `Task[None]`, the same command reported `0 errors, 0 warnings, 0 notes`.
- **Likely cause**: Asyncpg's pool proxy type differs from the concrete connection type at static-analysis time; the fake concurrency cleanup returned the production integer count while the refresh tasks returned `None`. Confidence: 99%.
- **Suggested action**: Keep transaction helper signatures compatible with asyncpg pool proxies, matching the existing `auth_runtime_state.lock_auth_runtime_state(conn: Any)` pattern.

## 2026-06-12T21:38:46+09:30 — #27 Frontend Local CI Blocked By Sandboxed npm-ci And Existing Frontend Debt
- **Severity**: warning
- **Scope**: host | project
- **Encountered during**: Issue #27 auth proxy forwarded-IP verification
- **Category**: dependency | build-error | test-failure
- **Blocked current task**: no
- **What happened**: `scripts/local_ci.sh frontend` could not complete cleanly in the sandbox. The inventory `npm ci` step failed while trying to execute esbuild's install check, which left `node_modules` unusable for subsequent local-CI steps; after restoring dependencies with escalated `npm ci`, focused auth-proxy tests and changed-file lint/type/format checks passed, while full frontend type/lint still reported existing advisor-event and React hook debt outside #27.
- **Evidence**: `npm ci --prefix frontend --no-audit --no-fund --prefer-offline` failed with `spawnSync /tmp/daemon-27/frontend/node_modules/esbuild/bin/esbuild EPERM`; subsequent local-CI blocking commands failed as `next: command not found`, `eslint: command not found`, and `prettier: command not found`. Direct full `npm --prefix frontend run type-check` still reports pre-existing `lib/advisorEvents.ts` / `__tests__/advisor-events.test.ts` errors, and `npm --prefix frontend run lint` still reports 28 errors / 13 warnings in unrelated React/UI files. Direct full `npm --prefix frontend run test:run` includes `__tests__/auth-proxy-route.test.ts` passing (8 tests) but still fails 19 existing advisor/tool-call tests across `__tests__/advisor-events.test.ts`, `__tests__/chat-route-advisor-events.test.ts`, and `__tests__/tool-call-log.test.ts`.
- **Likely cause**: The managed sandbox blocks executing esbuild's postinstall binary during `npm ci`; the full frontend type/lint failures are existing project debt and not caused by the changed auth proxy files. Confidence: 90%.
- **Suggested action**: Run frontend dependency installation outside the sandbox for local worktrees, and fix advisor-event/hook lint debt in dedicated frontend cleanup issues.

## 2026-06-12T21:45:22+09:30 — #27 PR Wrapper Refused On Existing Local Gate Debt
- **Severity**: warning
- **Scope**: project | host
- **Encountered during**: Issue #27 auth proxy forwarded-IP verification
- **Category**: build-error | test-failure | dependency | security
- **Blocked current task**: no
- **What happened**: `scripts/pr_create.sh` confirmed it would run all local CI families before PR creation, then refused to call `gh pr create` because blocking local gates failed outside the #27 change surface. GitHub branch protection was active via the `Main Protection` branch ruleset, so hosted protected checks remain the merge authority.
- **Evidence**: `gh api repos/sol-aeternum/Daemon/rulesets` returned `{"enforcement":"active","name":"Main Protection","target":"branch"}`. `scripts/pr_create.sh -- --title "Auth proxy trusts spoofed forwarded-IP headers" ...` refused with blocking failures: `backend/basedpyright (exit=3)`, `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`. The wrapper's backend env created `.venv` and `basedpyright` printed `venv .uv-venv subdirectory not found in venv path /tmp/daemon-27.` Full backend pytest also reproduced existing `tests/test_auth_user_scoping.py` fixture errors: `TypeError: 'coroutine' object does not support the asynchronous context manager protocol (missed __aexit__ method)` at `orchestrator/auth_runtime_state.py:97`. Full frontend test inventory passed `__tests__/auth-proxy-route.test.ts` but failed the existing advisor/tool-call tests, and `npm audit`/`pip-audit` reported existing dependency advisories.
- **Likely cause**: The PR wrapper does not inherit the audited temp-worktree backend env overrides, and main already carries frontend advisor/lint/format debt plus auth-scoping fixture debt. Confidence: 95%.
- **Suggested action**: Open #27 directly after the wrapper refusal, rely on active hosted branch-protection checks for merge eligibility, and keep the local wrapper/env and baseline gate debt in dedicated cleanup work.
- **Seen again**: 2026-06-12 during #113 changed-file type checking when `UV_PROJECT_ENVIRONMENT=/home/sol/daemon/.uv-venv uv run basedpyright --level error orchestrator/routes/auth_setup.py tests/test_refresh_flow.py` still used `pyrightconfig.json`'s worktree-local `.uv-venv` lookup and exited 3 with `venv .uv-venv subdirectory not found in venv path /tmp/daemon-113.`

## 2026-06-12T22:09:07+09:30 — #113 PR Wrapper Refused On Existing Frontend Blocking Gates
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #113 refresh rotation grace PR creation
- **Category**: build-error | test-failure | dependency
- **Blocked current task**: no
- **What happened**: `scripts/pr_create.sh` ran all local CI families and refused to call `gh pr create` because existing frontend blocking gates failed outside the backend-only #113 change surface. Backend blocking gates and aggregate gates passed in the wrapper run.
- **Evidence**: `timeout 360s scripts/pr_create.sh -- --title "fix(auth): tolerate lost rotation response within grace window" ...` reported `blocking failures: 3`: `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`. Backend `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect` passed. Aggregate `feature-matrix` and `pre-commit` passed. Frontend inventory also reproduced the existing 19 advisor/tool-call test failures and Next build failed on `Module '"./events"' has no exported member 'isAdvisorEvent'`.
- **Likely cause**: Main still carries the frontend advisor-event type/build debt and repo-wide lint/format debt already tracked by earlier Wave 0 entries; #113 changes only backend refresh rotation and migration docs. Confidence: 95%.
- **Suggested action**: Open #113 directly after the documented wrapper refusal and rely on active hosted branch-protection checks for merge eligibility; fix the frontend baseline in the existing Wave 0/#108/#111 work.

## 2026-06-12T21:20:00+09:30 — #24 Migration Metadata Needed Doc Freshness Update
- **Severity**: info
- **Scope**: project
- **Encountered during**: Issue #24 device-creation audit migration verification
- **Category**: config
- **Blocked current task**: yes
- **What happened**: Adding `migrations/034_device_creation_audit.sql` caused the aggregate pre-commit doc-freshness hook to fail until the structured migration metadata in `docs/TECHNICAL_SPECS.md` and `docs/PROJECT_CONTEXT.md` was updated.
- **Evidence**: `scripts/local_ci.sh aggregate` failed `doc-freshness` with `MIGRATION_COUNT expected='34' observed='33'` and `MIGRATION_LATEST expected='034_device_creation_audit.sql' observed='033_auth_runtime_state.sql'` for both docs.
- **Likely cause**: The migration count/latest fields are intentionally gated structured facts and must be updated with every new migration. Confidence: 99%.
- **Suggested action**: Keep migration metadata docs in the same PR as schema migrations.

## 2026-06-12T20:11:23+09:30 — #11 Backend Inventory Pytest Timed Out Locally
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Issue #11 setup-token redaction verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `timeout 300s scripts/local_ci.sh backend` passed all blocking backend gates, then timed out during the non-blocking full pytest inventory phase. The inventory run reached late-suite progress and stopped producing output before the timeout killed it; no `/tmp/daemon-11` pytest processes remained afterward.
- **Evidence**: Blocking local-CI gates passed: `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`. Inventory `pip-audit` failed with `Failed to resolve 'pypi.org' ([Errno -2] Name or service not known)` under sandboxed networking. Inventory pytest printed progress through `[ 91%]` and then the outer command exited `124`.
- **Likely cause**: Same local sandbox/network and long-running inventory-suite behavior seen on #23; hosted branch-protection CI remains the authoritative full gate. Confidence: 85%.
- **Suggested action**: Investigate the late-suite pytest inventory stall separately from issue-scoped auth fixes; continue relying on blocking local gates plus hosted protected CI before merge.
- **Seen again**: 2026-06-12T21:08+09:30 during #24 device-creation limit verification, and again after adding the `034_device_creation_audit.sql` migration. `timeout 300s env UV_PROJECT_ENVIRONMENT=/home/sol/daemon/.uv-venv UV_CACHE_DIR=/tmp/uv-cache scripts/local_ci.sh backend` passed blocking `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`; inventory `bandit` exited 1 with existing low/medium findings, `pip-audit` failed DNS resolution for `pypi.org`, and full inventory pytest printed progress through `[ 91%]` before the outer timeout exited `124`. Process inspection after both runs found no remaining `/tmp/daemon-24` pytest/local-CI processes.

## 2026-06-12T20:01:23+09:30 — #11 Worktree Missing `.uv-venv` Symlink Broke BasedPyright
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Issue #11 setup-token redaction verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: The first `basedpyright --level error` run in `/tmp/daemon-11` resolved dependencies against a missing worktree-local `.uv-venv` path and reported hundreds of missing imports. Creating `/tmp/daemon-11/.uv-venv -> /home/sol/daemon/.uv-venv` and rerunning the same command produced `0 errors, 0 warnings, 0 notes`.
- **Evidence**: Initial output began with `venv .uv-venv subdirectory not found in venv path /tmp/daemon-11.` and then missing imports for `fastapi`, `asyncpg`, `httpx`, `pytest`, and related pinned dependencies. Rerun output: `0 errors, 0 warnings, 0 notes`.
- **Likely cause**: The repository type-checker config expects a `.uv-venv` path relative to the active checkout, but new `/tmp` worktrees do not inherit the root checkout symlink. Confidence: 99%.
- **Suggested action**: Create the `.uv-venv` symlink immediately after adding future `/tmp` worktrees, or update agent worktree setup docs/scripts to do this automatically.

## 2026-06-12T07:04:00+09:30 — Main Protection Uses Rulesets Instead Of Classic Branch Protection
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: #112 branch protection verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: GitHub's classic branch-protection REST endpoint returned `Branch not protected`, but the repository has an active branch ruleset named `Main Protection` targeting `refs/heads/main`. The ruleset contains the required status checks from #112, and a deliberately-red draft PR was blocked before being closed without merge.
- **Evidence**: `gh api repos/sol-aeternum/Daemon/branches/main/protection` returned `Branch not protected (HTTP 404)`; `gh api repos/sol-aeternum/Daemon/rulesets/17593157` returned active required checks `Backend gates`, `Frontend gates`, `Feature matrix gate`, and `Pre-commit and secret scanning`; PR #122 reported `mergeStateStatus: BLOCKED` with `Feature matrix gate` failing before it was closed.
- **Likely cause**: GitHub rulesets are the active protection mechanism for `main`, not the older branch-protection endpoint. Confidence: 99%.
- **Suggested action**: Use repository ruleset APIs or GitHub UI ruleset checks when verifying required status checks for this repository.

## 2026-06-12T19:07:56+09:30 — #110 Backend Inventory Gates Hit Sandbox Network And Long Pytest Runtime
- **Severity**: warning
- **Scope**: host
- **Encountered during**: #110 streaming message persistence verification
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `scripts/local_ci.sh backend` passed its blocking ruff, ruff-format, BasedPyright, and pytest collection gates, but the non-blocking inventory phase could not complete cleanly in the sandbox. `pip-audit` could not resolve PyPI, and the full pytest inventory run continued for more than two hours after reaching late-suite progress, so the process group was terminated. Process inspection also found stale pytest process groups from earlier worktree verification runs, which were terminated to avoid resource contention.
- **Evidence**: `pip-audit` ended with `requests.exceptions.ConnectionError: HTTPSConnectionPool(host='pypi.org', port=443): Max retries exceeded with url: /pypi/aiohappyeyeballs/2.6.1/json (Caused by NameResolutionError("HTTPSConnection(host='pypi.org', port=443): Failed to resolve 'pypi.org' ([Errno -2] Name or service not known)"))`; `ps` showed `/tmp/daemon-110` process group `173416` running `uv run pytest -q` for `02:46:05`; after `kill -TERM -173416`, the tool session exited `143`; stale process groups `62941`, `85992`, `117432`, `64922`, and `83846` had been running previous pytest commands for roughly one to two days and were sent `SIGTERM`.
- **Likely cause**: Network is restricted inside the managed sandbox, stale pytest processes were consuming resources, and the full inventory test suite contains slow or hanging tests unrelated to the focused #110 persistence path. Confidence: 85%.
- **Suggested action**: Use the escalated PR wrapper for network-backed inventory gates and investigate long-running full-suite pytest inventory separately if hosted CI reproduces it.
- **Seen again**: 2026-06-12T19:19+09:30 after the #110 advisor-test correction, a direct full `PYTHONPATH=. uv run pytest -q` reached the late-suite progress marker and then stopped producing output for multiple minutes; process inspection found the current process group plus additional stale two-day-old pytest groups, which were terminated.

## 2026-06-12T19:18:31+09:30 — #110 PR Wrapper Hit Frontend Gate Debt And Full /tmp
- **Severity**: warning
- **Scope**: project | host
- **Encountered during**: #110 streaming message persistence PR creation
- **Category**: build-error | config
- **Blocked current task**: yes
- **What happened**: `scripts/pr_create.sh` refused to open the backend-only #110 PR because its all-family local CI run found unrelated frontend blocking failures, and the aggregate pre-commit pass also failed after `/tmp` filled. The same run surfaced an in-scope backend advisor-trace test assumption from #110's early assistant-row insert; that was corrected so advisor traces are asserted on the terminal `update_message` write.
- **Evidence**: Wrapper summary reported blocking failures `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, `frontend/format-check (exit=1)`, and `aggregate/pre-commit (exit=1)`. Frontend type/build evidence included `./lib/advisorEvents.ts:3:21 Type error: Module '"./events"' has no exported member 'isAdvisorEvent'`; lint reported `41 problems (28 errors, 13 warnings)`; format-check reported `Code style issues found in 124 files`. Aggregate pre-commit failed with `No space left on device (os error 28)` writing `.ruff_cache`, and `df -h /tmp` showed `7.7G 7.7G 0 100% /tmp` before clean temporary issue worktrees were pruned.
- **Likely cause**: The PR wrapper still runs all gate families for a backend-only branch; current main carries frontend type/lint/format debt, while old temporary issue worktrees consumed the `/tmp` tmpfs. Confidence: 90%.
- **Suggested action**: Fix the frontend family in its own issue/PR or teach the PR wrapper to run only affected families; periodically prune clean `/tmp` worktrees after issue PRs are merged.
- **Seen again**: 2026-06-12 during #24 setup. Adding the `/tmp/daemon-24` worktree initially hit the same full-`/tmp` condition from old temporary worktrees; pruning clean merged issue worktrees freed space and allowed #24 work to proceed.

## 2026-06-12T19:50:29+09:30 — #23 Backend Inventory Pytest Timed Out Locally
- **Severity**: warning
- **Scope**: host
- **Encountered during**: #23 shared setup token and development pepper verification
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `scripts/local_ci.sh backend` passed the blocking backend gates (`ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`) before entering non-blocking inventory gates. `pip-audit` failed because sandbox DNS could not resolve PyPI, and the full `pytest -q` inventory run reached late-suite progress then produced no terminal summary before the outer 300-second timeout killed it.
- **Evidence**: Command was `timeout 300s env UV_PROJECT_ENVIRONMENT=/home/sol/daemon/.uv-venv UV_CACHE_DIR=/tmp/uv-cache scripts/local_ci.sh backend`; exit code `124`. `pip-audit` ended with `requests.exceptions.ConnectionError: HTTPSConnectionPool(host='pypi.org', port=443): Max retries exceeded ... Failed to resolve 'pypi.org' ([Errno -2] Name or service not known)`. Full pytest printed progress through `[ 91%]` and earlier showed `EEEEE` around `[ 18%]`, but did not produce failure details before timeout. Post-timeout process inspection found no remaining `/tmp/daemon-23` pytest processes.
- **Likely cause**: The managed sandbox blocks outbound DNS for `pip-audit`, and this host has a recurring late-suite full-pytest inventory stall unrelated to the focused #23 auth path. Confidence: 85%.
- **Suggested action**: Treat GitHub Actions backend gates as the authoritative full-suite result for PRs while investigating the local late-suite stall separately; continue running focused auth slices and blocking collection locally.

## 2026-06-05 UTC — Worktree LSP import resolution misses project deps under /tmp review worktree
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: PR #7 review-comment fix verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` on changed Python test files in `/tmp/opencode/hosted-identity-f4-fix` reported missing imports for `fastapi`, `httpx`, `pytest`, `pytest_asyncio`, and `starlette.requests` even though the branch intentionally reuses the main repo's pinned backend/frontend environments. The same scan also reported broad frontend dependency/type resolution failures on TSX files because the `/tmp` worktree does not have a fully-resolved TypeScript language-server environment.
- **Evidence**: `lsp_diagnostics` returned `Import "fastapi" could not be resolved` for `orchestrator/routes/auth_setup.py`, `Import "httpx" could not be resolved` / `Import "pytest" could not be resolved` for changed backend tests, and TSX dependency errors like `Cannot find module 'react' or its corresponding type declarations` in `frontend/components/AuthLanding.tsx`.
- **Likely cause**: The OpenCode language-server environment for the detached `/tmp` worktree is not inheriting the repo's Python venv / frontend dependency graph the same way as runtime test commands do (confidence 90%).
- **Suggested action**: Treat runtime verification (`/home/sol/daemon/.venv/bin/python -m pytest`, `npm run ...` in the existing frontend install) as the authoritative gate for this worktree, or configure the LSP environment to resolve dependencies from the shared repo installation paths.
- **Seen again**: 2026-06-05 during the PR #7 current-head follow-up fix pass when `lsp_diagnostics` on `frontend/lib/deployment.ts` and `orchestrator/routes/auth_setup.py` again reported `/tmp` worktree dependency-resolution noise (`Cannot find name 'process'` / unresolved `fastapi`) while runtime frontend/backend verification passed from the shared repo toolchains.
- **Seen again**: 2026-06-07 during the final six-comment hosted-identity fix batch when changed-file diagnostics in `/tmp/opencode/hosted-identity-f4-fix` again reported unresolved `fastapi` / `pytest` / `pytest_asyncio` / `httpx` imports and no `.sql` LSP server for `migrations/032_hosted_identity_claim.sql`, while command-line verification in the shared backend/frontend environments remained the intended source of truth.

## 2026-06-05 UTC — Doc freshness migration metadata drift
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 20 — Update Setup, Hosted Identity, and Feature Matrix Documentation
- **Category**: config
- **Blocked current task**: no
- **What happened**: The required `python scripts/check_doc_freshness.py --mode fail` attempt failed on migration metadata in existing gated docs outside this task's requested edit set. The task evidence records the failure, but the current documentation synchronization scope only allows `AUTH_SETUP.md`, `HOSTED_IDENTITY.md`, `FEATURE_MATRIX.md`, optional matrix linter changes, and task evidence.
- **Evidence**: `/home/sol/daemon/docs/TECHNICAL_SPECS.md:1 [CheckId.MIGRATION_COUNT] expected='32' observed='31'; /home/sol/daemon/docs/TECHNICAL_SPECS.md:90 [CheckId.MIGRATION_LATEST] expected='032_hosted_identity_claim.sql' observed='031_auth_device_model.sql'; /home/sol/daemon/docs/PROJECT_CONTEXT.md:1 [CheckId.MIGRATION_COUNT] expected='32' observed='31'; /home/sol/daemon/docs/PROJECT_CONTEXT.md:71 [CheckId.MIGRATION_LATEST] expected='032_hosted_identity_claim.sql' observed='031_auth_device_model.sql`; command exit code 1.
- **Likely cause**: Hosted identity migration documentation was updated to expect migration `032_hosted_identity_claim.sql`, but the current repository migration set still reports latest applied/source migration `031_auth_device_model.sql` (confidence 85%).
- **Suggested action**: In a separate docs/schema synchronization task, reconcile `docs/TECHNICAL_SPECS.md` and `docs/PROJECT_CONTEXT.md` with the actual hosted identity migration source state, or restore the missing migration artifact if it should exist in this branch.
- **Seen again**: 2026-06-07 during PR follow-up for failing CI; `python scripts/check_doc_freshness.py --mode fail` failed on the same migration metadata drift and additionally reported stale literal route docs for `/api/v1/auth/*` in `docs/HOSTED_IDENTITY.md` and `docs/AUTH_SETUP.md`. This follow-up updates the gated docs instead of suppressing the check.

## 2026-06-05 09:31 UTC — TypeScript LSP all-severity output lagged behind file contents
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: TODO17 Frontend Google Sign-In Flow verification
- **Category**: other
- **Blocked current task**: no
- **What happened**: After fixing tuple casts in `frontend/__tests__/auth.test.ts` and removing an unused import from `frontend/components/AuthLanding.tsx`, an all-severity `lsp_diagnostics` call still reported the old TypeScript errors/hints. Reading the files showed the fixes were present, and rerunning `lsp_diagnostics` at error severity immediately returned no diagnostics for all changed files.
- **Evidence**: stale all-severity output included `frontend/__tests__/auth.test.ts` TypeScript 2352 tuple-cast errors at lines 210/274/397/430 and an unused `Mail` hint in `AuthLanding.tsx`; subsequent `lsp_diagnostics(filePath="/home/sol/daemon/frontend/__tests__/auth.test.ts", severity="error")` and error-level checks on all changed files returned `No diagnostics found`.
- **Likely cause**: LSP diagnostics cache or all-severity server state lag in the OpenCode TypeScript tooling (confidence 80%).
- **Suggested action**: When all-severity diagnostics contradict current file contents, rerun error-level diagnostics or restart the TypeScript language server before treating stale hints/errors as current.

## 2026-05-31 05:18 UTC — TOML diagnostics unavailable for pyproject changes
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 3 — Backend ruff config, autofix, and format ratchet
- **Category**: config
- **Blocked current task**: no
- **What happened**: Required changed-file diagnostics could not run on the modified root `pyproject.toml` because this environment has no LSP server configured for `.toml` files. Ruff gate verification still covered the TOML configuration by loading it successfully for `uv run ruff check .` and `uv run ruff format --check .`.
- **Evidence**: `lsp_diagnostics(filePath="/home/sol/daemon/pyproject.toml", severity="error")` returned `Error: No LSP server configured for extension: .toml` and listed available servers: `typescript, deno, vue, eslint, oxlint, biome, gopls, ruby-lsp, basedpyright, pyright...`.
- **Likely cause**: OpenCode LSP configuration in this workspace does not include a TOML language server (confidence 98%).
- **Suggested action**: Add a TOML LSP server to the workspace tooling if `pyproject.toml` changes are expected to satisfy changed-file diagnostics without relying on tool-specific validators.
- **Seen again**: 2026-05-31 during Task 6 pre-commit config verification when `lsp_diagnostics(filePath="/home/sol/daemon/pyproject.toml", severity="error")` again returned `No LSP server configured for extension: .toml`; `uv run pre-commit validate-config`, `uv run ruff check .`, and `uv run basedpyright` were used as tool-native validators instead.

## 2026-05-31 UTC — Vitest emits Node localStorage experimental warning in auth tests
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: PR #4 newest review-comment fix verification
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: The targeted frontend auth test suite passed, but Node/Vitest emitted an experimental warning because the runtime localStorage backing file was not configured. The test explicitly installs a mocked `globalThis.localStorage`, so this warning did not affect assertions.
- **Evidence**: `npm test -- --run __tests__/auth.test.ts` passed `19 tests` and printed `(node:3845559) ExperimentalWarning: localStorage is not available because --localstorage-file was not provided.`
- **Likely cause**: Current Vitest/jsdom/Node runtime exposes a localStorage-related experimental warning unless Node is launched with `--localstorage-file`, even when tests provide their own localStorage mock (confidence 80%).
- **Suggested action**: If the warning becomes noisy, configure the frontend test runner with an explicit localStorage file or suppress the Node experimental warning for this test environment.
- **Seen again**: 2026-06-05 during the PR #7 current-head follow-up fix pass when the targeted Vitest command for `__tests__/auth.test.ts`, `__tests__/auth-landing.test.tsx`, and `__tests__/deployment.test.ts` passed but still printed the same `ExperimentalWarning: localStorage is not available because --localstorage-file was not provided.`
- **Seen again**: 2026-06-23 during #108 frontend local CI. `scripts/local_ci.sh frontend` passed `test-run` but Vitest again printed `(node:518839) ExperimentalWarning: localStorage is not available because --localstorage-file was not provided.`

## 2026-05-29 UTC — Studio Kling provider value is not accepted or forwarded by backend video paths
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #4 final context gate rereview
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The Studio UI exposes and submits `videoProvider === "kling"`, but the backend video-credit estimate route only accepts providers `"xai"` and `"fal"`; the trusted video generation context also does not forward provider/model/audio fields to the image subagent. This means selecting Kling in Studio can fail credit estimation with `Invalid provider` and generated video requests cannot reliably preserve the selected Kling options.
- **Evidence**: `frontend/app/studio/page.tsx:200-205` sends `provider: videoProvider`; `frontend/app/studio/page.tsx:323-326` defines the UI option `{ id: "kling", label: "Kling 3.0" }`; `orchestrator/routes/video_credits.py:136-157` defines `VALID_VIDEO_PROVIDERS = {"xai", "fal"}` and rejects anything else; `orchestrator/main.py:271-280` builds trusted video context without `provider`, `kling_model`, or `audio_enabled`; `orchestrator/tools/spawn.py:232-244` forwards only duration/tier/user/source/reference fields.
- **Likely cause**: Frontend uses user-facing provider id `kling` while backend/provider pricing code uses canonical provider id `fal`, and the trusted spawn context whitelist was not updated for the Kling model/audio fields (confidence 95%).
- **Suggested action**: Normalize the Studio provider value to backend canonical `fal` before credit/video requests, or update backend credit/spawn handling to explicitly map `kling -> fal` and forward `kling_model`/`audio_enabled` through the trusted context.

## 2026-05-29 UTC — Aggregate auth suite fails user-scoping tests under production pepper environment
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #4 final security rereview gate
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The aggregate auth/backend pytest command failed only in `tests/test_auth_user_scoping.py` setup because its `authenticated_app` fixture does not set `DAEMON_ENVIRONMENT=development`; in the current shell, settings resolved to production without `DAEMON_AUTH_PEPPER`, so app lifespan startup rejected the missing production pepper. The narrower generated-artifact route hardening suite still passed.
- **Evidence**: `PYTHONPATH=. uv run pytest tests/test_auth_smoke.py tests/test_auth_middleware.py tests/test_auth_cookies_csrf.py tests/test_setup_flow.py tests/test_enrollment_flow.py tests/test_refresh_flow.py tests/test_device_management.py tests/test_auth_user_scoping.py tests/test_route_auth_hardening.py -q` ended `158 passed, 35 warnings, 5 errors`; each error raised `orchestrator.auth_pepper.PepperValidationError: daemon_auth_pepper is required in production` from `orchestrator/main.py:99` / `orchestrator/auth_pepper.py:45` while setting up `tests/test_auth_user_scoping.py:27`.
- **Likely cause**: `tests/test_auth_user_scoping.py` omits the `DAEMON_ENVIRONMENT=development` monkeypatch used by other auth route fixtures, making it sensitive to host/project environment defaults (confidence 92%).
- **Suggested action**: Update the `authenticated_app` fixture in `tests/test_auth_user_scoping.py` to set `DAEMON_ENVIRONMENT=development` (or provide a test pepper) before clearing settings, then rerun the aggregate auth suite.

## 2026-05-28 UTC — Frontend build blocked by pre-existing advisorEvents.ts type error
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 17 setup page verification (Playwright screenshot)
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `npm run build` in `frontend/` fails with `./lib/advisorEvents.ts:3:21 Type error: Module '"./events"' has no exported member 'isAdvisorEvent'`. This prevents building the frontend and thus prevents launching the dev server for a Playwright screenshot.
- **Evidence**: `next build --webpack` output shows `Failed to compile.` at `./lib/advisorEvents.ts:3:21`.
- **Likely cause**: Pre-existing frontend issue where `lib/advisorEvents.ts` imports `isAdvisorEvent` from `lib/events.ts`, but `events.ts` does not export that symbol. Documented in task context as "lib/advisorEvents.ts missing advisor guards and 19 advisor/tool-call test failures" (confidence 95%).
- **Suggested action**: Fix `lib/events.ts` to export `isAdvisorEvent` or remove the broken import from `lib/advisorEvents.ts`. Out of scope for Task 17.
- **Seen again**: 2026-05-28 during PR #4 review-fix QA when `npm run build` in `frontend/` failed at the same `./lib/advisorEvents.ts:3:21` missing `isAdvisorEvent` export after successful webpack compilation.
- **Seen again**: 2026-05-31 during PR #4 newest review-comment fix verification when `npx tsc --noEmit --project tsconfig.json --pretty false` failed only in the known advisor/tool-call event debt files: `__tests__/advisor-events.test.ts`, `__tests__/tool-call-log.test.ts`, and `lib/advisorEvents.ts`; changed files had clean LSP diagnostics and targeted auth tests passed.
- **Seen again**: 2026-06-07 during the final six-comment hosted-identity fix batch when `npm run build` in `frontend/` compiled the touched auth proxy route successfully, then failed in the pre-existing advisor path at `./lib/advisorEvents.ts:3:21` with `Module '"./events"' has no exported member 'isAdvisorEvent'`.
- **Seen again**: 2026-06-12 during #24 PR-wrapper creation. Frontend inventory/build still failed on `./lib/advisorEvents.ts:3:21`, and `npm run test:run` still showed 19 advisor/tool-call failures around missing `isAdvisorEvent` / advisor event guards.

## 2026-06-07 UTC — Frontend type-check expects missing .next cache-life type artifact
- **Severity**: warning
- **Scope**: project
- **Encountered during**: final six-comment hosted-identity fix batch verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `npm run type-check` in `frontend/` failed before reaching the changed auth proxy files because `tsc --noEmit` includes `.next/types/**/*.ts` and expected `.next/types/cache-life.d.ts`, but the generated `.next/types/` directory only contained `app/`, `package.json`, `routes.d.ts`, and `validator.ts`.
- **Evidence**: `error TS6053: File '/tmp/opencode/hosted-identity-f4-fix/frontend/.next/types/cache-life.d.ts' not found. The file is in the program because: Matched by include pattern '.next/types/**/*.ts' in '/tmp/opencode/hosted-identity-f4-fix/frontend/tsconfig.json'`; `ls .next/types` returned `app`, `package.json`, `routes.d.ts`, `validator.ts`.
- **Likely cause**: The current frontend `tsconfig.json`/Next 16 type-generation setup expects a `cache-life.d.ts` artifact that is not being emitted in this worktree's generated `.next/types` output (confidence 85%).
- **Suggested action**: Reconcile the `.next/types/**/*.ts` include with the actual Next-generated type artifacts (or ensure `next typegen` emits `cache-life.d.ts`) before treating full frontend type-check as green.

## 2026-05-28T12:56:31Z — Broad memories route tests now fail unauthorized after auth hardening

- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #4 review-fix QA targeted backend verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `PYTHONPATH=. uv run pytest tests/test_memories.py -q` failed 15 existing memory route tests because the routes now return `401 Unauthorized` before the mocked store assertions. Dream-specific tests in the same file still pass with `-k dream`, so this appears isolated to legacy unauthenticated memories endpoint expectations rather than the dream endpoint itself.
- **Evidence**: Failures include `tests/test_memories.py::test_get_memories_returns_memories_array` with `assert 401 == 200`, `tests/test_memories.py::test_get_memory_by_id_not_found` with `assert 401 == 404`, `tests/test_memories.py::test_get_memories_unavailable_store` with `assert 401 == 503`, and `tests/test_memories.py::test_reembed_memories_rejects_unknown_status` with `assert 401 == 422`. Summary: `15 failed, 8 passed, 15 warnings in 3.01s`.
- **Likely cause**: Auth/device-model hardening now protects memory routes, but older broad route tests were not updated to enroll/authenticate a test device or assert the new auth-first behavior (confidence 90%).
- **Suggested action**: Update the non-dream memories route tests to use the repo's authenticated test-client helpers or split explicit unauthenticated `401` contract tests from authenticated store-behavior tests.

## 2026-05-27 UTC — Repository LSP error scan surfaced unrelated dirty-tree Python errors
- **Severity**: warning
- **Scope**: project
- **Encountered during**: abstention-guardrail-wiring-audit Task 1 verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Atlas ran the required workspace Python LSP scan after an artifact-only Task 1 and the scan reported 22 error-level diagnostics in unrelated Python files, including existing/untracked advisor paths plus audio/base/image/reminder modules. Task 1 only created markdown/text evidence and did not modify these Python files.
- **Evidence**: `lsp_diagnostics(filePath=".", severity="error")` reported errors in `orchestrator/subagents/audio.py`, `orchestrator/subagents/base.py`, `orchestrator/subagents/image.py`, `orchestrator/tools/reminder.py`, and `orchestrator/tools/advisor.py` such as `FalKlingError is not defined` and unknown advisor imports.
- **Likely cause**: Pre-existing dirty-tree/project diagnostics unrelated to the abstention guardrail audit artifacts (confidence 90%).
- **Suggested action**: Re-run diagnostics from a clean tree or triage the advisor/Kling/reminder diagnostics separately before relying on workspace-wide LSP as a regression signal.

## 2026-05-27 UTC — Task 3 verification probe used stale ExpectedFact constructor
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 3 — Structural and Import Validation Atlas-side verification
- **Category**: other
- **Blocked current task**: no
- **What happened**: An Atlas-side supplemental verification snippet initially instantiated `ExpectedFact(content=...)`, but the recovered benchmark dataclass only accepts `keywords`, `description`, `min_confidence`, `max_confidence`, and `expected_category`. The corrected probe using `ExpectedFact(keywords=[...])` passed and did not affect benchmark source or evidence.
- **Evidence**: `TypeError: ExpectedFact.__init__() got an unexpected keyword argument 'content'`; `tests/benchmark_extraction.py:568-575`; corrected command output `match_fact keyword behavior OK`.
- **Likely cause**: Verification probe drifted from the canonical dataclass signature (confidence 99%).
- **Suggested action**: Prefer importing and inspecting dataclass signatures before constructing benchmark helper objects in ad hoc verification snippets.

## 2026-05-27 UTC — Markdown artifact diagnostics unavailable in current LSP setup
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Task 8 — harness parity baseline stability artifact verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Required changed-file diagnostics could not run on the two modified Markdown artifacts because this environment has no LSP server configured for the `.md` extension.
- **Evidence**: `lsp_diagnostics` on `tests/benchmark_results/harness_parity_baseline_stability.md` and `.sisyphus/notepads/longmemeval-parity-baseline-completion/learnings.md` returned `Error: No LSP server configured for extension: .md`.
- **Seen again**: 2026-05-28 during PR #4 review-fix QA when `lsp_diagnostics` on `TRIAGE.md` and `.sisyphus/notepads/pr-4-review-fix-qa/learnings.md` returned `Error: No LSP server configured for extension: .md`.
- **Seen again**: 2026-05-29 during generated-audio protection verification when `lsp_diagnostics` on the updated `TRIAGE.md` returned `Error: No LSP server configured for extension: .md`.
- **Seen again**: 2026-06-05 during Task 20 docs verification when `lsp_diagnostics` on `docs/AUTH_SETUP.md`, `docs/HOSTED_IDENTITY.md`, and `docs/FEATURE_MATRIX.md` returned `Error: No LSP server configured for extension: .md`.
- **Likely cause**: OpenCode LSP configuration in this workspace defines language servers for code and JSON-oriented extensions but does not include a Markdown-capable server such as Marksman (confidence 98%).
- **Suggested action**: Add a Markdown LSP server to the workspace tooling if artifact-only tasks are expected to satisfy the changed-file diagnostics requirement without fallback checks.

## 2026-05-27 UTC — Backend container restart wiped completed run2 artifacts from `/tmp/opencode`
- **Severity**: critical
- **Scope**: host
- **Encountered during**: Task 7 — longmemeval-parity-baseline-completion final run2 artifact copy-back
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: After Task 7 cleanup succeeded and run2 had been launched/detected as progressing, the Docker stack restarted. `daemon-backend-1` came back up only ~11 minutes before finalization, and its ephemeral `/tmp/opencode` directory no longer existed, so the completed in-container run2 artifacts were gone before they could be copied into `tests/benchmark_results/harness_parity_baseline/run2/`.
- **Evidence**: `docker ps --format '{{.Names}}\t{{.Status}}'` showed all long-running containers `Up 11 minutes`. `docker top daemon-backend-1` showed only the restarted uvicorn processes and no parity runner. `docker exec daemon-backend-1 sh -lc 'ls -la /tmp/opencode'` returned `ls: cannot access '/tmp/opencode': No such file or directory`. A direct file probe reported `results_exists=False`, `summary_exists=False`, `rows=0` for `/tmp/opencode/harness_parity_baseline_run2/{results.jsonl,summary.json}`.
- **Likely cause**: Host/container restart or compose recreation cleared the backend container's ephemeral `/tmp` filesystem before repo-side copy-back happened (confidence 96%).
- **Suggested action**: Do not rely on container `/tmp` as the only copy of long-running benchmark outputs. For future runs, stream or periodically copy artifacts to host storage during execution, or mount a persistent volume for `/tmp/opencode`/benchmark outputs.

## 2026-04-16 23:37 — Autonomous-edit toggle still crashes on live deprecated skills
- **Severity**: critical
- **Scope**: project
- **Encountered during**: autonomous-skill-creation final F3 hands-on QA verdict
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The current `/settings/skills` page loads and the list/detail/download/admin-sync paths work, but clicking **Allow autonomous edits** on a live system skill still fails end-to-end. The browser shows `Failed to update autonomous edit setting.` and the backing `PATCH /skills/{id}/autonomous-edit` route returns `500`.
- **Evidence**:
  - Browser QA on `http://127.0.0.1:3000/settings/skills` captured `toggleStatus: 500`, `errorVisible: true`, and `successVisible: false` immediately after clicking the checkbox for `pending-skill`.
  - Direct API call: `PATCH /skills/pending-skill/autonomous-edit HTTP/1.1` → `500 Internal Server Error`
  - Backend traceback: `asyncpg.exceptions.DataError: invalid input for query argument $13: {'deprecated': True, ...} (expected str, got dict)`
  - Trace path: `/app/orchestrator/routes/skills.py:273` → `store.upsert_projection(...)`
- **Likely cause**: The autonomous-edit route reuses `projection.get("pending_update")` as a Python dict when calling `SkillProjectionStore.upsert_projection()`, but that store path is still binding the JSONB argument as a string-encoded value rather than a native JSONB-compatible payload (confidence 93%).
- **Suggested action**: Fix the autonomous-edit projection upsert path to serialize/bind `pending_update` consistently with the successful list/detail reads, then re-run live browser QA on `/settings/skills` to verify the checkbox updates instead of surfacing the error banner.

## 2026-04-16 23:35 — Repository-Wide BasedPyright Warning Debt Dominates Backend Diagnostics
- **Severity**: warning
- **Scope**: project
- **Encountered during**: whole-repo audit verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Running `lsp_diagnostics` over `orchestrator/` returned zero errors but a very large warning backlog, making static-signal triage noisy and obscuring high-value regressions.
- **Evidence**:
  - `Directory: /home/sol/daemon/orchestrator ... Files scanned: 50 (capped at 50) ... Files with errors: 0 ... Total diagnostics: 2169`
  - Representative warnings concentrated in `orchestrator/db.py`, `orchestrator/memory/embedding.py`, `orchestrator/memory/summary.py`, `orchestrator/memory/summarization.py`, and `orchestrator/memory/trust*.py` (`reportExplicitAny`, `reportUnknown*`, `reportMissingTypeStubs`, `reportUnusedCallResult`).
- **Likely cause**: The repository keeps strict BasedPyright warning rules enabled while core data/integration modules intentionally use dynamic third-party APIs (LiteLLM/asyncpg dict payloads), accumulating long-lived warning debt (confidence 91%).
- **Suggested action**: Establish a warning-budget strategy (targeted suppressions, typed adapter layers, or staged cleanup) so CI/static checks can surface new high-impact issues instead of warning flood.

## 2026-04-15 12:57 — Subagent Task Delegation Hit Workspace CreditsError
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: autonomous-skill-creation Task 1 delegation retry gate
- **Category**: dependency
- **Blocked current task**: yes
- **What happened**: The first delegated attempt to write `.sisyphus/plans/skill-integration-decision.md` failed before producing usable work because the monitored subagent session returned `Unauthorized` with a nested `CreditsError` for insufficient workspace balance.
- **Evidence**:
  - `Unauthorized: {"type":"error","error":{"type":"CreditsError","message":"Insufficient balance. Manage your billing here: https://opencode.ai/workspace/wrk_01KFQJSNM8KAAP52SN02MF4GTQ/billing"}}`
- **Likely cause**: The selected subagent/model route for the first Task 1 delegation required workspace credits that are currently unavailable for that provider path (confidence 93%).
- **Suggested action**: Retry the task with an available agent/model route or restore workspace billing balance before relying on this provider for further delegations.
- **Seen again**: 2026-06-05 during PR #7 hosted-identity review-comment verification when a background `explore` audit of the uncommitted `/tmp/opencode/hosted-identity-f4-fix` diff failed immediately with the same `CreditsError` / insufficient workspace balance before returning any code review findings.

## 2026-04-14 23:47 — LSP JSON Diagnostics Blocked By Missing Biome Server
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: artifact-only Task 15/Task 16 trust refresh verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` could not run against `tests/benchmark_results/` because the configured JSON-capable LSP server (`biome`) is not installed in this environment.
- **Evidence**:
  - `Error: LSP server 'biome' is configured but NOT INSTALLED.`
  - `Command not found: biome`
- **Likely cause**: The workspace/tooling configuration expects Biome for JSON diagnostics, but the binary is unavailable in the current shell/runtime (confidence 97%).
- **Suggested action**: Install `@biomejs/biome` or adjust the diagnostics/tooling configuration so JSON artifact checks do not depend on an unavailable server.
- **Seen again**: 2026-05-27 during Task 8 artifact verification when `lsp_diagnostics` on `.sisyphus/evidence/task-8-stability.json` again returned `LSP server 'biome' is configured but NOT INSTALLED` / `Command not found: biome`.
- **Seen again**: 2026-05-31 during Task 3 Ruff verification when `lsp_diagnostics(filePath="/home/sol/daemon/tests", severity="error")` selected the configured Biome route and returned `LSP server 'biome' is configured but NOT INSTALLED` / `Command not found: biome`.
- **Seen again**: 2026-05-31 during Task 5 frontend tooling verification when `lsp_diagnostics(filePath="/home/sol/daemon/frontend/package.json", severity="all")` again returned `LSP server 'biome' is configured but NOT INSTALLED` / `Command not found: biome`; changed `.mjs` config diagnostics succeeded.
- **Seen again**: 2026-05-31 during Task 5 final diagnostics when `lsp_diagnostics(filePath="/home/sol/daemon/frontend/package-lock.json", severity="all")` again returned `LSP server 'biome' is configured but NOT INSTALLED` / `Command not found: biome`; `npm ci` was used as the lockfile/package validator instead.
- **Seen again**: 2026-05-31 during Task 6 pre-commit config verification when `lsp_diagnostics` on `frontend/package.json` and `frontend/package-lock.json` again returned `LSP server 'biome' is configured but NOT INSTALLED` / `Command not found: biome`; `npm --prefix frontend exec commitlint -- --version` and commitlint/pre-commit hook execution validated the package changes instead.

## 2026-05-31 05:55 UTC — LSP Diagnostics Unavailable For .prettierignore
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 5 — Frontend strict TS, ESLint flat config, Prettier, and scripts
- **Category**: config
- **Blocked current task**: no
- **What happened**: Changed-file diagnostics could not run for `frontend/.prettierignore` because this OpenCode LSP configuration has no server for extensionless ignore files. Prettier itself consumed the ignore file during `npm run format:check`.
- **Evidence**: `lsp_diagnostics(filePath="/home/sol/daemon/frontend/.prettierignore", severity="all")` returned `Error: No LSP server configured for extension:` and listed available servers; `.sisyphus/evidence/task-5-frontend-positive.txt` shows `prettier --check .` executed with exit code 1 due existing formatting debt, not ignore-file parse failure.
- **Likely cause**: Local LSP tooling coverage gap for extensionless config/ignore files (confidence 99%).
- **Suggested action**: Use tool-native validation for ignore/config files or configure an appropriate LSP if extensionless files must satisfy diagnostics checks.

## 2026-05-31 05:54 UTC — Frontend ESLint Gate Surfaces Existing React/Next Lint Debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 5 — Frontend strict TS, ESLint flat config, Prettier, and scripts
- **Category**: build-error
- **Blocked current task**: yes — `npm run lint` is wired but cannot pass until the existing app lint debt is remediated or deliberately baselined in a separate task
- **What happened**: Replacing `next lint` with direct `eslint . --max-warnings 0` successfully invokes ESLint flat config, but the first real run reports 39 existing problems (28 errors, 11 warnings) across frontend app/components/hooks files.
- **Evidence**: `.sisyphus/evidence/task-5-frontend-positive.txt` shows `npm run lint` exit code 1 with examples: `app/artifacts/page.tsx:193:55 react-hooks/purity`, `app/chats/page.tsx:54:7 react-hooks/set-state-in-effect`, `components/council/CouncilInterviewCard.tsx:35:47 react-hooks/rules-of-hooks`; `.sisyphus/evidence/task-5-eslint-negative.txt` shows the temporary probe added one additional `task-5-eslint-probe.tsx:5:5 react-hooks/rules-of-hooks` error and restored to the same 39-problem state after deletion.
- **Seen again**: 2026-05-31T06:31Z during Task 7 local CI parity; `.sisyphus/evidence/task-7-ci-local-parity.txt` shows `cd frontend && npm run lint` exit code 1 with existing React/Next lint errors.
- **Likely cause**: `next lint` was previously broken, so the repository accumulated lint debt that direct ESLint now reveals (confidence 95%).
- **Suggested action**: Commission a frontend lint-remediation/baseline task; do not weaken Task 5's ESLint config to hide these findings.

## 2026-05-31 05:54 UTC — Frontend Typecheck, Build, And Tests Blocked By Advisor Event Contract Drift
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 5 — Frontend strict TS, ESLint flat config, Prettier, and scripts
- **Category**: build-error
- **Blocked current task**: yes — `npm run type-check`, `npm run build`, and `npm run test:run` are wired but fail on pre-existing app/test contract drift
- **What happened**: The new `type-check` script runs `next typegen && tsc --noEmit`, but TypeScript and Next build fail because advisor event tests/code reference exports and event variants that are absent from `lib/events.ts`; Vitest fails 19 advisor/tool-log tests for the same drift. The typecheck also reports stale `.next/types` references for removed routes such as `app/api/v1/auth/[...path]/route.js` and `app/settings/devices/page.js`.
- **Evidence**: `.sisyphus/evidence/task-5-frontend-positive.txt` shows `npm run type-check` exit code 2 with `lib/advisorEvents.ts(3,21): error TS2305: Module '"./events"' has no exported member 'isAdvisorEvent'`; `npm run build` exit code 1 with the same missing export; `npm run test:run` exit code 1 with 3 failed test files / 19 failed tests including `(0 , isAdvisorEvent) is not a function` and `(0 , isAdvisorStartEvent) is not a function`.
- **Seen again**: 2026-05-31T06:31Z during Task 7 local CI parity; `.sisyphus/evidence/task-7-ci-local-parity.txt` shows `npm run type-check` exit 2, `npm run test:run` exit 1, and `npm run build` exit 1 on the same advisor-event contract drift family.
- **Likely cause**: Advisor SSE/event implementation and tests are ahead of or drifted from the typed `ChatEvent` contract in `frontend/lib/events.ts`, plus stale generated Next type artifacts remain in `.next/types` (confidence 90%).
- **Suggested action**: Commission a frontend advisor event contract repair and generated-type cleanup task, then rerun `npm run type-check`, `npm run test:run`, and `npm run build`.

## 2026-05-31 05:54 UTC — Frontend Prettier Gate Finds Existing Formatting Debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 5 — Frontend strict TS, ESLint flat config, Prettier, and scripts
- **Category**: config
- **Blocked current task**: yes — `npm run format:check` is wired but cannot pass until formatting debt is remediated or a baseline strategy is approved
- **What happened**: The new Prettier check ran successfully as a gate but reported code style issues in 120 existing frontend files. Task 5 scope forbids broad app-code reformatting unless required, so this was recorded as a blocker inventory instead of auto-writing changes.
- **Evidence**: `.sisyphus/evidence/task-5-frontend-positive.txt` shows `npm run format:check` exit code 1 and `Code style issues found in 120 files. Run Prettier with --write to fix.`
- **Seen again**: 2026-05-31T06:31Z during Task 7 local CI parity; `.sisyphus/evidence/task-7-ci-local-parity.txt` shows `cd frontend && npm run format:check` exit code 1 with existing formatting warnings.
- **Likely cause**: The frontend previously had no formatter config or Prettier gate, so existing files do not match the newly established Prettier style (confidence 98%).
- **Suggested action**: Commission a dedicated mechanical frontend formatting task, or establish a formatting baseline strategy before requiring this gate in CI.

## 2026-05-31 05:54 UTC — Frontend npm audit Reports 26 Vulnerabilities
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 5 — Frontend strict TS, ESLint flat config, Prettier, and scripts
- **Category**: security
- **Blocked current task**: yes — `npm run audit:ci` is wired but exits non-zero on current dependency vulnerabilities
- **What happened**: `npm audit --audit-level=high` reports 26 vulnerabilities (4 low, 8 moderate, 14 high). Some fixes require breaking changes, including `ai@6.0.193`, `next@16.2.6`, or `next-pwa@2.0.2`, so Task 5 records the inventory rather than upgrading runtime dependencies.
- **Evidence**: `.sisyphus/evidence/task-5-frontend-positive.txt` shows `npm run audit:ci` exit code 1 with high-severity advisories for `@ai-sdk/provider-utils`, `@babel/plugin-transform-modules-systemjs`, `fast-uri`, `flatted`, `lodash`, `minimatch`, `next`, `picomatch`, `rollup`, `serialize-javascript`, and `vite`.
- **Likely cause**: Current frontend direct/transitive dependency versions include known vulnerable packages; several remediation paths cross major/runtime dependency boundaries outside Task 5 scope (confidence 95%).
- **Suggested action**: Commission a frontend dependency remediation task using npm-managed upgrades and explicit regression testing for Next/PWA/AI SDK flows.
- **Seen again**: 2026-05-31 during Task 6 when `npm install --save-dev @commitlint/cli @commitlint/config-conventional` completed but again reported `26 vulnerabilities (4 low, 8 moderate, 14 high)`; commitlint installation proceeded, and vulnerability remediation remains out of scope for Task 6.
- **Seen again**: 2026-05-31T06:31Z during Task 7 local CI parity; `.sisyphus/evidence/task-7-ci-local-parity.txt` shows `npm ci` reporting 26 vulnerabilities and `npm run audit:ci` exiting 1.
- **Seen again / changed count**: 2026-06-05T03:34Z during F3 Real Manual QA, `npm ci` in `/tmp/opencode/hosted-identity-pr-f3/frontend` completed but reported `27 vulnerabilities (4 low, 8 moderate, 14 high, 1 critical)`. Install succeeded, but the audit inventory is worse than the previously recorded 26-vulnerability state.
- **Seen again**: 2026-06-08 during PR #21 Studio image API retirement follow-up; local `npm ci` reported the same `27 vulnerabilities (4 low, 8 moderate, 14 high, 1 critical)` inventory.

## 2026-05-31 05:54 UTC — npm ci Emits Deprecated Frontend Dependency Warnings
- **Severity**: warning
- **Scope**: upstream
- **Encountered during**: Task 5 — Frontend strict TS, ESLint flat config, Prettier, and scripts
- **Category**: deprecation
- **Blocked current task**: no — install succeeds, but warnings indicate dependency maintenance debt
- **What happened**: `npm ci` succeeded but emitted deprecation warnings for multiple transitive/frontend packages including `@types/dompurify`, `inflight`, `rimraf@2`, `rollup-plugin-terser`, `glob@7`, and Workbox packages.
- **Evidence**: `.sisyphus/evidence/task-5-frontend-positive.txt` lines 2-28 show `npm ci` exit code 0 and the warning list.
- **Likely cause**: Existing frontend dependency graph includes older transitive packages, especially from PWA/build tooling (confidence 90%).
- **Suggested action**: Address during the same frontend dependency remediation task as the npm audit findings.
- **Seen again**: 2026-06-08 during PR #21 Studio image API retirement follow-up; `npm ci` succeeded but emitted the same deprecated transitive dependency warnings (`@types/dompurify`, `inflight`, `rimraf@2`, `rollup-plugin-terser`, `glob@7`, Workbox packages) plus `sourcemap-codec` and `source-map@0.8.0-beta.0`.

## 2026-05-31 06:17 UTC — Gitleaks Negative Probe Requires Staged Content
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 6 — Root pre-commit config
- **Category**: config
- **Blocked current task**: no
- **What happened**: Running `uv run pre-commit run gitleaks --files .task6-gitleaks-probe.txt` against an unstaged temporary fake-secret file exited 0, because the configured gitleaks hook scans staged git content. Staging only the probe file and rerunning `uv run pre-commit run gitleaks` correctly blocked the fake AWS-style credentials with exit code 1.
- **Evidence**: `.sisyphus/evidence/task-6-gitleaks-negative.txt` shows the first `--files` run passed, then the staged retry failed with `RuleID: aws-access-token`, `RuleID: generic-api-key`, and `leaks found: 2`; cleanup removed `.task6-gitleaks-probe.txt` and `grep` found no planted fake secret strings afterward.
- **Likely cause**: The official gitleaks pre-commit hook is optimized for staged commit protection rather than arbitrary unstaged file scanning (confidence 95%).
- **Suggested action**: For future negative probes of this hook, stage the temporary probe file, run the hook, then unstage/delete the probe file and verify no residue.

## 2026-05-31 06:18 UTC — Pre-commit Temporarily Stashed Existing Dirty Tree During Hook Probes
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 6 — Root pre-commit config
- **Category**: config
- **Blocked current task**: no
- **What happened**: `uv run pre-commit run gitleaks` and the explicit commit-msg hook validation emitted `[WARNING] Unstaged files detected.` and temporarily stashed/restored the broad pre-existing dirty tree while running against staged or commit-msg inputs. The hook runs completed and pre-commit restored the changes.
- **Evidence**: `.sisyphus/evidence/task-6-gitleaks-negative.txt` shows `[INFO] Stashing unstaged files to /home/sol/.cache/pre-commit/patch1780208249-4012044` followed by `[INFO] Restored changes...`; `.sisyphus/evidence/task-6-precommit-positive.txt` shows the same pattern for `patch1780208317-4013104` during commitlint validation.
- **Likely cause**: The repository had a broad inherited unstaged diff from prior tasks, and pre-commit isolates staged checks from unstaged working-tree content by design (confidence 99%).
- **Suggested action**: Keep future hook probes aware that pre-commit may stash/restore dirty trees; run from a clean tree when possible for simpler evidence.
- **Seen again**: 2026-05-31T07:23Z during Task 11 commitlint hook verification; `uv run pre-commit run commitlint --hook-stage commit-msg --commit-msg-filename /tmp/opencode/task11-commit-msg.txt` emitted `[WARNING] Unstaged files detected.`, stashed to `/home/sol/.cache/pre-commit/patch1780212170-4071487`, passed, and restored changes.

## 2026-04-14 12:44 — LiteLLM Printed Repeated Provider Help During One-Scenario Benchmark Run
- **Severity**: warning
- **Scope**: upstream
- **Encountered during**: deterministic extraction benchmark one-scenario CLI verification
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: The one-scenario `tests/benchmark_extraction.py` verification run completed successfully and no longer hit the nested event-loop crash, but LiteLLM repeatedly printed `Provider List: https://docs.litellm.ai/docs/providers` to stdout during extraction calls.
- **Evidence**:
  - One-scenario direct run output printed the same `Provider List: https://docs.litellm.ai/docs/providers` line 12 times between transcript replay and result reporting.
- **Likely cause**: LiteLLM emitted provider-resolution/help output during repeated extraction-model calls even though the configured model still returned usable benchmark results (confidence 70%).
- **Suggested action**: If this keeps cluttering benchmark output, inspect the active LiteLLM/provider configuration for extraction-model resolution and suppress or redirect this help-text noise in benchmark runs.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 13 when `PYTHONPATH=. python tests/benchmark_extraction.py --json --no-save` passed but printed `Provider List: https://docs.litellm.ai/docs/providers` repeatedly throughout the 8-scenario run.

## 2026-05-28T02:05Z — httpx per-request cookies deprecation still emitted by auth tests

- **Severity**: warning
- **Scope**: upstream
- **Encountered during**: Task 21 amendment verification
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: Targeted auth pytest runs passed, but existing tests still emit `httpx` deprecation warnings because they use per-request `cookies={...}` arguments. The warning appears in enrollment, refresh, device-management, and route-hardening suites.
- **Evidence**: `DeprecationWarning: Setting per-request cookies=<...> is being deprecated, because the expected behaviour on cookie persistence is ambiguous. Set cookies directly on the client instance instead.` from `/home/sol/.local/lib/python3.14/site-packages/httpx/_client.py:1859` and `:1966` during `pytest tests/test_route_auth_hardening.py -q` and `pytest tests/test_auth_middleware.py tests/test_auth_cookies_csrf.py tests/test_setup_flow.py tests/test_enrollment_flow.py tests/test_refresh_flow.py tests/test_device_management.py tests/test_auth_user_scoping.py -q`.
- **Likely cause**: Existing tests were written against older `httpx` behavior and have not yet been updated to set cookies on the client/session instead of passing them per request (confidence 96%).
- **Suggested action**: Update affected auth tests to use client-level cookie state before `httpx` removes per-request cookie support.
- **Seen again**: 2026-05-29 during generated-audio protection verification when `PYTHONPATH=. uv run pytest tests/test_route_auth_hardening.py -q` passed but emitted the same httpx per-request cookies deprecation warnings in `TestCookieOnlyAuthRejected`.
- **Seen again**: 2026-06-12 during #24 focused enrollment verification when `PYTHONPATH=. uv run pytest -q tests/test_enrollment_flow.py` passed (19 tests) but emitted the same per-request cookie deprecation warning in `test_native_enroll_complete_returns_body_refresh_and_rejects_mixed_mode`.
- **Seen again**: 2026-06-12 during #113 focused refresh verification when `PYTHONPATH=. uv run pytest -q tests/test_refresh_flow.py` passed (28 tests) but emitted 20 `httpx` per-request cookie deprecation warnings from refresh-route tests.

## 2026-04-16 03:40 — Extraction Benchmark Dedup Supersession Still Leaves Corolla Facts Active
- **Severity**: warning
- **Scope**: project
- **Encountered during**: autonomous-skill-creation Task 13 memory extraction benchmark verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The full extraction benchmark passed its headline precision/recall guardrails, but Scenario 3 (Corrections and Supersession) still reported failed dedup expectations because the superseded Corolla fact and its correction remained active instead of being retired after the Tesla replacement fact was written.
- **Evidence**:
  - `Dedup post-close skipped family=vehicle keep_id=None`
  - `✗ DEDUP: 'User drives a 2019 Toyota Corolla' active=True, expected active=False`
  - `✗ DEDUP: 'User sold the Corolla last month' active=True, expected active=False`
  - Full run still ended with `TOTAL ... P=1.00 R=1.00` and `✅ BENCHMARK PASSED`
- **Likely cause**: The correction/supersession dedup path appears to miss the vehicle-family closeout in this benchmark replay, so retrieval-quality guardrails pass while bitemporal cleanup remains incomplete (confidence 81%).
- **Suggested action**: Inspect the correction/post-close dedup flow for `family=vehicle` in the replayed extraction path and decide whether the benchmark should fail when `dedup_results` contain `pass: false`.

## 2026-04-14 12:31 — BasedPyright Warning Debt Remains In Benchmark Harness Files
- **Severity**: warning
- **Scope**: project
- **Encountered during**: extraction benchmark harness stabilization
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Error-level diagnostics are clean on the modified benchmark files, but `lsp_diagnostics` still reports warning-level BasedPyright debt in `tests/benchmark_extraction.py` and the new focused test file due dynamic asyncpg imports, `Any`-heavy benchmark helpers, and test doubles.
- **Evidence**:
  - `tests/benchmark_extraction.py` warnings included `reportMissingTypeStubs` for `asyncpg`, `reportExplicitAny`, and `reportUnusedCallResult`
  - `tests/test_benchmark_extraction.py` warnings included `reportExplicitAny` around fake store/test-double helpers
- **Likely cause**: The benchmark harness intentionally uses dynamic runtime imports and thin test doubles around asyncpg/store behavior, while BasedPyright is configured to warn aggressively on `Any` and missing stubs in this repository (confidence 94%).
- **Suggested action**: If warning cleanup matters later, add narrower local helper types/protocols or targeted Pyright suppressions for benchmark-only harness/test code.

## 2026-04-14 08:28 — Tier 2 Fast Artifact Directory Was Root-Owned
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Task 6 fast baseline artifact repair
- **Category**: config
- **Blocked current task**: yes
- **What happened**: The existing `tests/benchmark_results/longmemeval_tier2_fast/` artifact files were owned by `root`, so the host-shell repair script could not remove the 11 tainted rows from `longmemeval_fast_results.jsonl` and `longmemeval_fast_checkpoint.json`.
- **Evidence**:
  - `PermissionError: [Errno 13] Permission denied: '/home/sol/daemon/tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_results.jsonl'`
  - `ls -l tests/benchmark_results/longmemeval_tier2_fast` showed `root root` ownership for the checkpoint/results files before repair.
- **Likely cause**: The original benchmark run was executed inside the backend container as root against a bind-mounted workspace, so the generated artifact files were written with root ownership on the host (confidence 98%).
- **Suggested action**: Either standardize benchmark execution on the host user or `chown` container-written artifact directories after completion so future resume/repair runs are writable from the shell.

## 2026-04-14 08:24 — Shared Benchmark User Caused Fast Harness FK Race
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 6 fast baseline closure repair
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The fast harness reused the global LongMemEval benchmark user (`TEST_USER_ID`), and its per-question cleanup deletes all conversations for that user. That made the harness vulnerable to overlapping cleanup from another LongMemEval process or repair run deleting freshly created conversations between conversation creation and direct memory insert, producing intermittent `memories_source_conversation_id_fkey` failures.
- **Evidence**:
  - 11 contiguous rows in `tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_results.jsonl` had `chunk_count = 0`, `session_count = 0`, and `error = insert or update on table "memories" violates foreign key constraint "memories_source_conversation_id_fkey"`
  - The failing `source_conversation_id` values differed per question, and the block later recovered without any data-specific harness change.
  - `orchestrator/eval/longmemeval_fast.py` previously called `cleanup_benchmark_state(... TEST_USER_ID)` and `evaluate_single(...)` against the shared benchmark user while using direct inserts that depend on conversation rows staying present.
- **Likely cause**: Shared mutable benchmark state across runs let another LongMemEval cleanup path delete the active fast harness conversations mid-import; this is consistent with the contiguous failure window and the fact that single-question isolated smoke runs passed (confidence 88%).
- **Suggested action**: Keep `longmemeval_fast` on an isolated per-run benchmark user and avoid reintroducing the shared `TEST_USER_ID` into this harness path.

## 2026-04-13 21:45 — LongMemEval Fast Insert SQL Placeholder Mismatch
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Small real fast-harness smoke run
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The first live `longmemeval_fast` smoke run failed before retrieval because the direct memory insert SQL referenced `to_tsvector('english', $15)` even though the statement only bound 13 parameters.
- **Evidence**:
  - `asyncpg.exceptions.IndeterminateDatatypeError: could not determine data type of parameter $13`
  - `orchestrator/eval/longmemeval_fast.py` insert statement originally used `to_tsvector('english', $15)` while passing 13 bind args.
- **Likely cause**: The new direct-insert SQL was adapted from the production insert shape, but the `content_tsv` placeholder index was not updated after trimming the parameter list for the standalone harness (confidence 98%).
- **Suggested action**: Keep the regression test assertion on the insert query placeholder and rerun a live smoke whenever this SQL changes.

## 2026-04-13 21:41 — BasedPyright Warning Debt In `longmemeval_fast.py`
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Changed-file diagnostics for the fast harness
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` on the new `orchestrator/eval/longmemeval_fast.py` file reported warning-level BasedPyright debt around dynamic `asyncpg` usage, argparse call-result handling, and strict `Any` rules, even after the file was brought to zero error-level diagnostics.
- **Evidence**:
  - `warning[basedpyright] (reportMissingTypeStubs): Stub file not found for "asyncpg"`
  - `warning[basedpyright] (reportUnknownMemberType): Type of "execute" is partially unknown`
  - `warning[basedpyright] (reportExplicitAny): Type 'Any' is not allowed`
- **Likely cause**: The project is running strict BasedPyright checks against dynamic asyncpg/argparse-heavy orchestration code without dedicated stubs or local type-narrowing for every call site (confidence 90%).
- **Suggested action**: If warning cleanup becomes important, add narrower local helper types/casts for pool operations and argparse parsing, or explicitly suppress this style of dynamic integration code.

## 2026-04-12 15:58 — LongMemEval Verbose Mode Leaks Provider API Key
- **Severity**: critical
- **Scope**: upstream
- **Encountered during**: Task 6 resume smoke verification
- **Category**: security
- **Blocked current task**: no
- **What happened**: Running the canonical LongMemEval command with `--verbose` enabled LiteLLM debug logging that printed the configured OpenRouter API key in plaintext in the process output while ingesting the benchmark dataset.
- **Evidence**:
  - `LiteLLM:DEBUG ... litellm.acompletion(... api_key='sk-or-v1-...')`
  - The leak appeared in the smoke command output for `python -m orchestrator.eval.longmemeval run ... --verbose`.
- **Likely cause**: LiteLLM debug logging includes raw request parameters, and the benchmark/runner logging path does not redact provider credentials before those parameters are logged (confidence 95%).
- **Suggested action**: Avoid `--verbose` in environments with live credentials, and add credential redaction or safer debug logging defaults around provider calls before relying on verbose mode for production/debug benchmark runs.

## 2026-04-13 11:12 — `uv run pytest` Misses Repo Root On PYTHONPATH
- **Severity**: warning
- **Scope**: project
- **Encountered during**: LongMemEval corpus-first redesign verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The first focused verification command failed during pytest startup before reaching the changed LongMemEval tests because `tests/conftest.py` could not import `orchestrator` when invoked as `uv run pytest ...` from the repo root.
- **Evidence**:
  - `ImportError while loading conftest '/home/sol/daemon/tests/conftest.py'.`
  - `tests/conftest.py:8: in <module>`
  - `E   ModuleNotFoundError: No module named 'orchestrator'`
- **Likely cause**: The host-shell `uv run pytest` invocation in this workspace is not adding the repository root to `sys.path`, so package-style imports rely on `PYTHONPATH=.` or an editable install to resolve local modules (confidence 90%).
- **Suggested action**: Standardize test invocation on `PYTHONPATH=. uv run pytest ...` or package the repo so `orchestrator` resolves consistently in host-shell verification flows.
- **Seen again**: 2026-04-15 during Task 1 regression gate verification — plain `pytest tests/test_longmemeval_fast.py tests/test_longmemeval_runner.py tests/test_longmemeval_ingest.py` failed with `ModuleNotFoundError: No module named 'orchestrator'`; confirmed `PYTHONPATH=. pytest ...` passes (29 tests).
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 11 focused suite verification — exact `pytest tests/test_skill_extraction_prompt.py tests/test_skill_dedup.py tests/test_skill_injection.py tests/test_skill_manage.py tests/test_skill_protection.py tests/test_skill_api_contracts.py -q` failed in `tests/conftest.py` with `ModuleNotFoundError: No module named 'orchestrator'`; confirmed the same six-file suite passes as `PYTHONPATH=. pytest ...` (88 tests).
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 13 when `python tests/benchmark_extraction.py --json --no-save` failed in `tests/benchmark_extraction.py:465` with `ModuleNotFoundError: No module named 'orchestrator'`; the benchmark script also requires `PYTHONPATH=.` in this host-shell setup.
- **Seen again**: 2026-06-08 during PR #18 follow-up verification — plain `uv run pytest tests/test_daemon_message_persistence.py -q` failed in `tests/conftest.py` with `ModuleNotFoundError: No module named 'orchestrator'`; rerunning as `PYTHONPATH=. uv run pytest tests/test_daemon_message_persistence.py -q` passed (3 tests).

## 2026-04-06 — LongMemEval Re-ingestion Blocked (TODO 5)
- **Severity**: critical
- **Scope**: host
- **Encountered during**: TODO 5 execution - Re-ingest LongMemEval with revised extraction
- **Category**: config
- **Blocked current task**: yes
- **Seen again**: 2026-04-10 during selective-assistant-extraction Task 9 when `PYTHONPATH=. python tests/longmemeval/evaluate.py --limit 10` failed with `socket.gaierror: [Errno -2] Name or service not known` while resolving the configured Postgres host.
- **Seen again**: 2026-04-12 during Task 15 reasoning-quality validation when host-side asyncpg inspection scripts failed with `socket.gaierror: [Errno -2] Name or service not known` against the configured Postgres host while the Docker Compose `postgres` service was up and mapped on localhost.
- **Seen again**: 2026-04-12 during Task 6 Tier 2 baseline prep when the host-shell runtime still resolved `DATABASE_URL` to `postgres:5432`; direct asyncpg connection failed with `gaierror: [Errno -2] Name or service not known`, while the same database was reachable at `127.0.0.1:5432`.
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
- **Seen again**: 2026-04-12 during Task 6 baseline runtime inspection when `docker compose ps` emitted the same `The "FAL_KEY" variable is not set. Defaulting to a blank string.` warning while the benchmark-related services were otherwise healthy.
- **Seen again**: 2026-04-16 during autonomous-skill-creation F3 runtime QA when both `docker compose ps` and `docker compose logs backend` emitted the same `The "FAL_KEY" variable is not set. Defaulting to a blank string.` warning while the core stack remained up.
- **Likely cause**: Docker Compose references `FAL_KEY` for Kling/fal.ai video configuration, but the local `.env` for this stack does not define it (confidence 95%).
- **Suggested action**: Decide whether `FAL_KEY` should be required only for Studio/video flows; if optional, suppress or scope the compose warning. If required for this environment, add it to the active env file.

## 2026-04-16 23:04 — Playwright MCP Requires Missing Chrome Binary In This Environment
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: autonomous-skill-creation F3 hands-on browser QA
- **Category**: config
- **Blocked current task**: no
- **What happened**: The required Playwright MCP could not launch a browser because it is hardwired to a Chrome binary path that does not exist in this environment. QA was still completed by switching to the known-good local Chromium binary (`/usr/bin/chromium`) through the installed Playwright package.
- **Evidence**:
  - `Error: server: Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`
  - `Run "npx playwright install chrome"`
- **Seen again**: 2026-04-16 during the final autonomous-skill-creation F3 verdict when `skill_mcp(playwright.browser_navigate)` still failed with the same missing Chrome path before QA continued via local `playwright` + `/usr/bin/chromium`.
- **Likely cause**: The MCP/browser tooling is configured for a Chrome install at `/opt/google/chrome/chrome`, but this host only has Chromium available at `/usr/bin/chromium` (confidence 98%).
- **Suggested action**: Point the Playwright MCP/browser launcher at `/usr/bin/chromium` in this environment or install the expected Chrome distribution so browser QA works without a manual workaround.

## 2026-04-16 23:05 — Skills List/Detail APIs Crash Because `pending_update` Is Stored As A JSON String
- **Severity**: critical
- **Scope**: project
- **Encountered during**: autonomous-skill-creation F3 hands-on runtime QA
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The live `/settings/skills` surface is broken because both `/skills` and `/skills/{skill_id}` return HTTP 500 when a skill projection has `pending_update`. FastAPI response validation rejects those values because the API contract expects a dictionary, but the persisted `pending_update` rows are double-encoded JSON strings instead.
- **Evidence**:
  - Chromium body text on `http://localhost:3000/settings/skills`: `0 total` and `Failed to load skills. Please verify API connectivity.`
  - Browser/runtime request: `http://localhost:3000/api/skills` → `500`
  - Backend log: `fastapi.exceptions.ResponseValidationError` with `loc: ('response', 'skills', 0, 'pending_update')` and `Input should be a valid dictionary`
  - Backend log: `GET /skills/pending-skill HTTP/1.1` → `500 Internal Server Error` with `loc: ('response', 'pending_update')`
  - Live projection query: `jsonb_typeof(pending_update)` returned `string` for `document-csv`, `document-docx`, `opencode-planner`, and `pending-skill`
- **Likely cause**: The pending-update write path is serializing upgrade metadata more than once before it reaches the `skill_projections.pending_update` JSONB column, so readback returns a JSON string literal instead of an object (confidence 95%).
- **Suggested action**: Trace the pending-update persistence path in the upgrade/projection store, stop double-encoding JSON before `pending_update` writes, and add a regression test that exercises deprecated/update rows through the live list/detail response models.

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
- **Seen again**: 2026-04-16 during whole-repo audit verification when `npm run lint` in `frontend/` still failed immediately with `Invalid project directory provided, no such directory: /home/sol/daemon/frontend/lint`.
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
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 13 when `PYTHONPATH=. pytest tests -q -k memory` failed during collection before reaching memory-filtered tests because `tests/test_video_e2e.py:596` still contains `SyntaxError: unmatched ')'`.
- **Seen again**: 2026-04-16 during whole-repo audit verification when `PYTHONPATH=. pytest -q` aborted during collection on the same `tests/test_video_e2e.py:596` unmatched `)` syntax error.
- **Likely cause**: A malformed edit left the test file syntactically invalid (confidence 99%).
- **Suggested action**: Fix the unmatched parenthesis in `tests/test_video_e2e.py:596` before relying on project-wide pytest results.

## 2026-04-08 20:35 — Python 3.14 Deprecation Warnings From litellm/arq During Pytest
- **Severity**: info
- **Scope**: upstream
- **Encountered during**: F2 Code Quality Review - project quality checks
- **Category**: deprecation
- **Blocked current task**: no
- **Seen again**: 2026-04-10 during retrieval/LongMemEval contract verification when `uv run pytest tests/memory/test_tools.py tests/test_hybrid_search.py tests/test_l0_injection.py tests/memory/test_retrieval.py tests/test_retrieval.py tests/test_longmemeval_ingest.py` emitted 15 LiteLLM deprecation warnings on Python 3.14.
- **Seen again**: 2026-04-10 during Task 4 LongMemEval runner repair when `uv run pytest tests/test_longmemeval_ingest.py tests/test_longmemeval_runner.py` passed but still emitted 15 LiteLLM deprecation warnings on Python 3.14.
- **Seen again**: 2026-04-10 during Task 9 dreaming verification when `uv run pytest tests/test_dreaming.py tests/test_memories.py tests/test_config.py` passed but still emitted the same LiteLLM/arq Python 3.14 deprecation warnings.
- **Seen again**: 2026-04-10 during Task 9 dreaming contract repair when `uv run pytest tests/test_dreaming.py tests/test_memories.py tests/test_config.py` passed with 31 tests but still emitted the same LiteLLM/arq Python 3.14 deprecation warnings.
- **Seen again**: 2026-04-15 during autonomous-skill-creation Task 2 regression verification when `PYTHONPATH=. pytest tests/test_store.py tests/test_chat_history.py tests/test_skill_projection_sync.py -q` passed with 32 tests but still emitted the same LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 6 evaluator verification when `PYTHONPATH=. pytest tests/test_skill_extraction_prompt.py tests/test_skill_evaluator.py -q` and `PYTHONPATH=. pytest tests/test_skill_extraction_prompt.py tests/test_skill_evaluator.py tests/test_skill_projection_sync.py -q -k 'skill_evaluator or skill_extraction_prompt or update_autonomous_metadata'` passed but still emitted the same LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 6 focused evaluator verification when `PYTHONPATH=. pytest tests/test_skill_extraction_prompt.py tests/test_skill_evaluator.py -q` passed with 7 tests but still emitted 15 LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 6 correction verification when `PYTHONPATH=. pytest tests/test_skill_extraction_prompt.py tests/test_skill_evaluator.py -q` passed with 9 tests but still emitted 15 LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 11 focused suite verification when `PYTHONPATH=. pytest tests/test_skill_extraction_prompt.py tests/test_skill_dedup.py tests/test_skill_injection.py tests/test_skill_manage.py tests/test_skill_protection.py tests/test_skill_api_contracts.py -q` passed with 88 tests but still emitted 15 LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 13 when both `PYTHONPATH=. pytest tests/test_skill*.py -q` (194 passed) and `PYTHONPATH=. pytest tests -q -k memory` emitted the same LiteLLM/arq `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-04-16 during whole-repo audit verification when `PYTHONPATH=. pytest -q` emitted the same LiteLLM/arq `asyncio.iscoroutinefunction` deprecation warnings before failing on unrelated test collection syntax errors.
- **Seen again**: 2026-05-28 during PR #4 review-fix QA when `PYTHONPATH=. uv run pytest tests/test_route_auth_hardening.py tests/test_skill_api_contracts.py -q`, `PYTHONPATH=. uv run pytest tests/test_dreaming.py -q`, and dream-focused memories tests passed but emitted the same LiteLLM/arq `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **What happened**: Pytest emitted repeated deprecation warnings from third-party dependencies that still call `asyncio.iscoroutinefunction`, which is deprecated on Python 3.14 and scheduled for removal in Python 3.16.
- **Evidence**:
  - `/home/sol/daemon/.venv/lib/python3.14/site-packages/litellm/litellm_core_utils/logging_utils.py:273: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead`
  - `/home/sol/daemon/.venv/lib/python3.14/site-packages/arq/cron.py:178: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead`
- **Likely cause**: Current pinned versions of LiteLLM and arq are not yet updated for Python 3.14's coroutine-inspection deprecation path (confidence 95%).
- **Suggested action**: Track dependency updates or pin compatible versions before Python 3.16 removes the deprecated API.
- **Seen again**: 2026-05-29 during generated-audio protection verification when `PYTHONPATH=. uv run pytest tests/test_route_auth_hardening.py -q` passed but emitted 15 LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-06-05 during hosted-identity Task 11 email-route verification when `PYTHONPATH=. uv run pytest tests/test_identity_email_routes.py tests/test_identity_email_challenge.py tests/test_identity_session_issuance.py tests/test_identity_account_service.py -q` passed (140 passed) but still emitted 15 LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14 from `litellm_core_utils/logging_utils.py:273`.
- **Seen again**: 2026-06-05 during hosted-identity Task 11 corrective verification when the same focused command passed again (`142 passed`) after the `daemon_email_enabled` route-gating fix, but still emitted the same 15 LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-06-08 during PR #20 benchmark replay follow-up verification when `PYTHONPATH=. DAEMON_ENVIRONMENT=development uv run pytest -q tests/memory/test_encryption.py tests/test_longmemeval_runner.py tests/test_benchmark_extraction.py` passed (38 passed) but still emitted the same 15 LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.
- **Seen again**: 2026-06-12 during #24 focused enrollment verification when `PYTHONPATH=. uv run pytest -q tests/test_enrollment_flow.py` passed (19 tests) but emitted the same 15 LiteLLM `asyncio.iscoroutinefunction` deprecation warnings on Python 3.14.

## 2026-04-10 12:30 — BasedPyright Warning Debt In Dreaming-Touched Python Modules
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 9 dreaming verification - changed-file diagnostics
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` reported warning-level BasedPyright debt in the touched Python modules even though there were no diagnostics errors and the focused dreaming tests passed. The warnings are mostly strict-type noise around dynamic `asyncpg`/LiteLLM interfaces and existing worker/route call-result patterns.
- **Evidence**:
  - `orchestrator/memory/dreaming.py` warnings including `Stub file not found for "asyncpg"` and unknown-member warnings around response extraction / `litellm.acompletion`
  - `orchestrator/worker/jobs.py` warnings including `reportUnusedCallResult` and existing protected `_pool` usage
  - `orchestrator/memory/retrieval.py` and `orchestrator/routes/memories.py` warnings including `reportUnusedCallResult`
- **Likely cause**: The project runs BasedPyright in a strict warning mode against modules that intentionally use dynamic libraries and loose dict-shaped payloads without dedicated stubs or local suppression strategy (confidence 90%).
- **Suggested action**: Decide whether these modules should gain stronger local typing / `_ =` cleanup, or whether the project wants targeted Pyright suppressions for dynamic memory/worker integration code.

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
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 11 changed-file diagnostics — `lsp_diagnostics` on `TRIAGE.md` returned `Error: No LSP server configured for extension: .md` while Python diagnostics remained clean.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 13 changed-file diagnostics — `lsp_diagnostics` could validate `tests/benchmark_skills.py`, but both `TRIAGE.md` and `.sisyphus/notepads/autonomous-skill-creation/learnings.md` again returned `Error: No LSP server configured for extension: .md`.
- **Seen again**: 2026-05-26 during final feature-matrix remediation — `lsp_diagnostics` on `/tmp/opencode/feature-matrix-2026-05-25/docs/FEATURE_MATRIX.md` returned `Error: No LSP server configured for extension: .md`, so Markdown verification again relied on readback/manual inspection plus repository-specific validators.

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

## 2026-05-27 UTC — auth-device-model branch missing Task 13 commit and auth test files
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 14 — legacy API-key removal verification
- **Category**: config
- **Blocked current task**: yes
- **What happened**: The current branch `auth-device-model-2026-05-27` is not descended from the expected Task 13 commit `6a8f64e6`, and all auth test files committed by Task 13 are absent from the working tree. Task 14 edits are applied atop an unexpected base, making safe commit/closure impossible without branch history reconciliation.
- **Evidence**:
  - `git branch --show-current` → `auth-device-model-2026-05-27`
  - `git rev-parse HEAD` → `cf1e163239e76feec95aacebd0d865046b5e4c5a`
  - `git rev-parse origin/auth-device-model-2026-05-27` → `cf1e163239e76feec95aacebd0d865046b5e4c5a`
  - `git show --stat --oneline 6a8f64e6` → commit exists
  - `git branch -a --contains 6a8f64e6` → no containing branch output (commit not reachable from current branch)
  - `git ls-files tests/test_enrollment_flow.py tests/test_refresh_flow.py tests/test_device_management.py tests/test_session_cleanup.py` → no files tracked
  - Directory listing confirms those expected auth test files are absent
- **Likely cause**: Branch history was rewritten or Task 13 was never merged to the current branch; plan assumed prior auth commits were present in the working tree (confidence 97%).
- **Suggested action**: Investigate whether Task 13 commit `6a8f64e6` should be cherry-picked or merged into `auth-device-model-2026-05-27`, and restore the auth test files before closing Task 14.
- **Note**: Verification also surfaced unrelated failures: `npm run build` failed on `lib/advisorEvents.ts` (missing `isAdvisorEvent` export), and `npm run test:run` failed 19 existing advisor/tool-call tests. Changed-file LSP diagnostics and Task 14 grep acceptance passed. These advisor/test failures are pre-existing and separate from the branch mismatch.

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
- **Seen again**: 2026-05-27 during Task 4 extraction-benchmark-recovery-rebuild verification. Directory-level `lsp_diagnostics` scanned 50 Python files and reported 22 error-level diagnostics in unrelated files including `orchestrator/subagents/audio.py`, `orchestrator/subagents/base.py`, `orchestrator/subagents/image.py`, `orchestrator/tools/reminder.py`, and dirty-tree `orchestrator/tools/advisor.py`; artifact JSON/manual validation still passed and current Task 4 did not modify those files.

## 2026-04-10 15:15 — compileall Cannot Write __pycache__ Files
- **Severity**: warning
- **Scope**: host
- **Encountered during**: F2 Code Quality Review rerun - build verification
- **Category**: config
- **Blocked current task**: no
- **Seen again**: 2026-04-10 during selective-assistant-extraction Task 1 verification with `python -m py_compile orchestrator/memory/extraction.py`.
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

## 2026-04-10 19:54 — Retrieval Score Test Expected Pre-Trust Formula
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 1 verification - retrieval and LongMemEval test run
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: The required retrieval test suite failed because `tests/test_retrieval.py::test_score_memory_multiplies_all_factors` still expected `_score_memory()` to omit the default `trust_score=0.5` factor, but the live retrieval contract includes trust in the product.
- **Evidence**:
  - `FAILED tests/test_retrieval.py::test_score_memory_multiplies_all_factors`
  - `E assert 0.3920400000000001 == 0.7840800000000002 ± 7.8e-07`
  - `tests/test_retrieval.py:48-49` sets `expected = 0.8 * 0.9 * 1.1 * 0.9 * 1.1` and asserts `_score_memory(memory) == pytest.approx(expected)`
  - `orchestrator/memory/retrieval.py:119-121` multiplies by `trust = _as_float(memory.get("trust_score"), 0.5)`
- **Likely cause**: The test was written before trust scoring became part of the retrieval score contract, so it now encodes a stale expectation rather than the intended behavior (confidence 98%).
- **Suggested action**: Update the test expectation to include the default trust multiplier so the suite verifies the preserved trust-scored contract instead of the old pre-trust formula.

## 2026-04-10 — Pre-existing test bug: `test_dedup_supersession_with_contradiction` asserts `metadata is None`
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 2 verification - BACKGROUND_REASONING_MODEL config test run
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The test `tests/test_contradiction.py::test_dedup_supersession_with_contradiction` fails because its assertion at line 109 checks `assert call_kwargs["metadata"] is None`, but the test name and LLM response ("YES. Fact B directly contradicts Fact A.") indicate contradiction WAS detected, so metadata should contain `contradiction_detected: True`, not None.
- **Evidence**:
  - `AssertionError: assert {'contradiction_detected': True, 'contradiction_explanation': 'YES. Fact B directly contradicts Fact A.'} is None`
  - The test patches litellm to return "YES..." response which triggers contradiction detection
- **Likely cause**: Test assertion was written incorrectly - it should check `assert call_kwargs["metadata"] is not None` and validate the contradiction content (confidence 99%).
- **Suggested action**: Fix the assertion in `test_dedup_supersession_with_contradiction` to check that metadata contains the expected contradiction detection fields, not None.

## 2026-04-10 — Contradiction-path tests emit unawaited coroutine warning in trust-signal hook
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 2 verification - contradiction test run
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The targeted contradiction test slice passed after fixing assertions, but pytest still emitted a runtime warning from the explicit negative trust-signal path in `orchestrator/memory/dedup.py` indicating an `AsyncMock` coroutine was never awaited.
- **Evidence**:
  - `tests/test_contradiction.py::test_dedup_supersession_with_contradiction`
  - `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - `orchestrator/memory/dedup.py:515` in the call to `await ts_module.apply_explicit_negative_signal(...)`
- **Likely cause**: The contradiction-path tests exercise the trust-signal hook with mocked collaborators in a way that surfaces an await/AsyncMock mismatch inside or below `apply_explicit_negative_signal` (confidence 80%).
- **Suggested action**: If later work touches dedup/trust-signal behavior, reproduce this warning directly and determine whether the bug is in the production hook or only in the test/mock setup.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 13 when `PYTHONPATH=. pytest tests/test_benchmark_extraction.py tests/test_memory_promote.py tests/test_memory_migrations.py tests/test_memories.py tests/test_retrieval.py tests/test_hybrid_search.py tests/test_l0_injection.py tests/test_store.py tests/test_chat_history.py tests/test_extraction.py tests/test_dedup_bitemporal.py tests/test_dedup_slot_fallback.py -q` passed with 130 tests but `tests/test_dedup_bitemporal.py` again emitted `orchestrator/memory/dedup.py:515: RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`.

## 2026-04-15 13:40 — Chat history regression tests emit unawaited AsyncMock warnings in settings/memory injection path
- **Severity**: warning
- **Scope**: project
- **Encountered during**: autonomous-skill-creation Task 2 regression verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The broader regression run passed, but `tests/test_chat_history.py` emitted runtime warnings that `AsyncMock` coroutines were never awaited while formatting user settings and retrieving expanded memory candidates during chat/system-prompt assembly.
- **Evidence**:
  - `orchestrator/memory/injection.py:138: RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - `orchestrator/memory/injection.py:140: RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - `orchestrator/main.py:1649: RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - `orchestrator/memory/retrieval.py:558: RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
  - Command: `PYTHONPATH=. pytest tests/test_store.py tests/test_chat_history.py tests/test_skill_projection_sync.py -q`
- **Likely cause**: Some chat-history tests are wiring async collaborators into settings/memory injection code paths with `AsyncMock` objects that are consumed like synchronous values, producing coroutine warnings even though assertions still pass (confidence 84%).
- **Suggested action**: When the chat history or memory injection path is next touched, inspect the relevant fixtures/mocks in `tests/test_chat_history.py` and the called settings/retrieval helpers to ensure async results are awaited or mocked with the correct sync/async shape.
- **Seen again**: 2026-04-16 during autonomous-skill-creation Task 13 when the targeted memory regression slice (130 passed) reproduced the same unawaited `AsyncMockMixin._execute_mock_call` warnings from `orchestrator/memory/injection.py:138`, `orchestrator/memory/injection.py:140`, `orchestrator/main.py:1652`, and `orchestrator/memory/retrieval.py:558`.

## 2026-04-16 03:44 — Targeted Memory Tests Emit New aiohttp/LiteLLM Runtime Warnings
- **Severity**: info
- **Scope**: upstream
- **Encountered during**: autonomous-skill-creation Task 13 targeted memory regression slice
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: The narrowed memory regression selection passed, but the dedup-focused tests emitted additional third-party warnings from aiohttp and LiteLLM's logging worker that are separate from the already-known project-side AsyncMock warnings.
- **Evidence**:
  - `/usr/lib/python3.14/site-packages/aiohttp/connector.py:1003: DeprecationWarning: enable_cleanup_closed ignored because https://github.com/python/cpython/pull/118960 is fixed in Python version sys.version_info(major=3, minor=14, micro=4, releaselevel='final', serial=0)`
  - `/home/sol/.local/lib/python3.14/site-packages/litellm/litellm_core_utils/logging_worker.py:75: RuntimeWarning: coroutine 'Logging.async_success_handler' was never awaited`
- **Likely cause**: Current aiohttp and LiteLLM versions are surfacing Python 3.14 compatibility/teardown noise in the memory dedup test path, likely independent of the autonomous-skill changes (confidence 78%).
- **Suggested action**: Track upstream dependency updates and, if these warnings become noisy enough, inspect whether the dedup tests need tighter teardown/mocking around LiteLLM logging hooks.

## 2026-04-16 02:55 — Playwright browser install blocked by interactive sudo requirement
- **Severity**: warning
- **Scope**: host
- **Encountered during**: autonomous-skill-creation Task 10 hands-on UI verification
- **Category**: tooling
- **Blocked current task**: yes
- **What happened**: Browser-level QA for `/settings/skills` could not run because the Playwright MCP requires a Chrome binary that is not installed, and `npx playwright install chrome` failed when the host asked for an interactive sudo password.
- **Evidence**:
  - Playwright MCP error: `Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`
  - Install attempt output: `sudo: a terminal is required to read the password; either use the -S option to read from standard input or configure an askpass helper`
  - Install attempt output: `Failed to install browsers` / `Error: Failed to install chrome`
- **Seen again**: 2026-04-16 during autonomous-skill-creation F3 manual QA when `skill_mcp` Playwright navigation again failed with `Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`; runtime browser QA continued via local `/usr/bin/chromium` instead of the MCP.
- **Likely cause**: This environment lacks the Playwright Chrome runtime and blocks non-interactive privilege escalation needed by the installer (confidence 96%).
- **Suggested action**: Preinstall the required Playwright browser/runtime on the host or provide a non-interactive browser image so mandatory browser QA can run in future UI tasks.

## 2026-04-16 13:26 — Skills projection mutation routes crash when projection table is missing
- **Severity**: critical
- **Scope**: project
- **Encountered during**: autonomous-skill-creation F3 real manual QA
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The required `pending-update` and `autonomous-edit` skill flows are not operational in the running app. Direct API exercise of both routes against a real skill returned `500 Internal Server Error`, and backend logs show both handlers crash while querying the projection store because the `skill_projections` relation does not exist.
- **Evidence**:
  - `POST /skills/pending-skill/pending-update HTTP/1.1` → `500 Internal Server Error`
  - `PATCH /skills/pending-skill/autonomous-edit HTTP/1.1` → `500 Internal Server Error`
  - Backend traceback: `asyncpg.exceptions.UndefinedTableError: relation "skill_projections" does not exist`
  - Trace paths: `/app/orchestrator/routes/skills.py:276` → `service.apply_pending_update(skill_id)` and `/app/orchestrator/routes/skills.py:231` → `store.get_projection(skill_id)`
- **Seen again**: 2026-04-16 during autonomous-skill-creation F3 rerun when live browser/API verification still showed every `/skills` and `/skills/{id}` response returning `source_type=null`, `allow_autonomous_edit=null`, and `pending_update=null`, and direct `psql -U daemon -d daemon -c "\dt *skill*"` in `daemon-postgres-1` reported `Did not find any relation named "*skill*".`
- **Likely cause**: The runtime database schema in this environment is missing the `skill_projections` table expected by the new autonomous-skill endpoints, so the read paths that rely on projection metadata crash instead of failing gracefully (confidence 98%).
- **Suggested action**: Apply/verify the migration that creates `skill_projections` (or add startup/schema guards) and re-run manual QA for `/settings/skills`, especially pending-update and autonomous-edit flows.

## 2026-04-16 03:11 — Skills settings page fails at runtime due to direct backend CORS in development
- **Severity**: warning
- **Scope**: project
- **Encountered during**: autonomous-skill-creation Task 10 hands-on Chromium QA
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The `/settings/skills` page loaded in Chromium, but skill data never loaded because the frontend attempted to fetch `http://localhost:8000/skills` directly from the browser and the backend response lacked a permissive CORS header for the page origin. The UI rendered `Failed to load skills. Please verify API connectivity.` and showed no skills.
- **Evidence**:
  - Browser console: `Access to fetch at 'http://localhost:8000/skills' from origin 'http://localhost:3000' has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource.`
  - Browser console: `GET http://localhost:8000/skills :: net::ERR_FAILED`
  - UI text: `0 total` and `Failed to load skills. Please verify API connectivity.`
  - `frontend/components/settings/SkillsTab.tsx:81-83` sets `apiBaseUrl` to `process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '')`
- **Likely cause**: In development the settings page prefers an absolute backend origin instead of same-origin requests, so browser calls bypass the frontend app and hit a backend that is not currently configured to allow that origin via CORS (confidence 94%).
- **Suggested action**: Update the skills settings fetch path so development uses a safe same-origin/proxied route or ensure the backend serves the required CORS headers for the configured frontend origin.

## 2026-04-16 03:18 — Skills settings proxy path double-prefixes `/skills` and 404s
- **Severity**: warning
- **Scope**: project
- **Encountered during**: autonomous-skill-creation Task 10 hands-on Chromium QA after proxy-route fix
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: After switching to the same-origin proxy, the settings page still failed to load skills because the frontend called `/api/skills/skills` instead of `/api/skills`. The page first showed `Loading skills...` and then fell back to `Failed to load skills. Please verify API connectivity.` / `No matching skills.` after search interaction.
- **Evidence**:
  - Browser request failure: `GET http://localhost:3000/api/skills/skills :: net::ERR_ABORTED`
  - Browser console: `Failed to load resource: the server responded with a status of 404 (Not Found)`
  - `frontend/components/settings/SkillsTab.tsx:141-146` calls `fetchWithTimeout('/skills', ...)`
  - `frontend/components/settings/SkillsTab.tsx:102-103` prepends `const proxyPath = "/api/skills" + normalizedPath`, turning `/skills` into `/api/skills/skills`
- **Likely cause**: The new proxy helper assumes callers pass resource paths relative to the skills root, but existing callers still pass paths beginning with `/skills`, causing the route prefix to be duplicated (confidence 98%).
- **Suggested action**: Normalize the proxy helper or its callers so list/detail requests map to `/api/skills`, `/api/skills/{id}`, etc. exactly once.

## 2026-04-16 03:22 — Skills proxy route does not handle `/api/skills` root path
- **Severity**: warning
- **Scope**: project
- **Encountered during**: autonomous-skill-creation Task 10 hands-on Chromium QA after proxy path fix
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: After fixing the double-prefix bug, the page still failed because the browser requested `/api/skills`, but the proxy only exists at `frontend/app/api/skills/[...path]/route.ts`. The root list request 404s, leaving the page in the same failed-load state.
- **Evidence**:
  - Browser request failure: `GET http://localhost:3000/api/skills :: net::ERR_ABORTED`
  - Browser console: `Failed to load resource: the server responded with a status of 404 (Not Found)`
  - UI text: `Failed to load skills. Please verify API connectivity.`
  - Existing proxy file: `frontend/app/api/skills/[...path]/route.ts` (catch-all for nested paths only)
- **Likely cause**: The Next.js catch-all route `[...path]` does not match the empty root path `/api/skills`, so the list endpoint has no same-origin handler (confidence 97%).
- **Suggested action**: Add a root `frontend/app/api/skills/route.ts` or switch to an optional catch-all so both `/api/skills` and `/api/skills/*` are proxied.

## 2026-04-12 14:57 — Live Postgres Schema/Data Lag Behind Reasoning Code
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 15 reasoning-quality validation
- **Category**: config
- **Blocked current task**: yes
- **What happened**: After working around the host `postgres` hostname issue by connecting to the mapped localhost database, the live Postgres instance contained the benchmark user row but no memories for `longmemeval@daemon.test`, and querying `dream_log` failed because the table does not exist. The running database is behind the reasoning-layer schema/data expected by the current code.
- **Evidence**:
  - `test_user_exists True`
  - `test_user_memories 0`
  - `test_user_conversations 0`
  - `asyncpg.exceptions.UndefinedTableError: relation "dream_log" does not exist`
- **Likely cause**: The active Docker-backed Postgres instance has not had the current reasoning migrations applied and does not yet contain the benchmark ingestion data needed for Task 15 validation (confidence 95%).
- **Suggested action**: Apply the current migrations to the active database, verify reasoning-layer tables (`dream_log`, retrieval/entity support) exist, then ingest/populate the benchmark data before running Task 15 validation.

## 2026-04-12 15:30 — Fast LongMemEval Harness Fails Memory Import FK Check
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 15 reasoning-quality validation
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The newly trusted `orchestrator.eval.longmemeval_fast` path ran against the 10-question MR subset but every question failed before retrieval because direct memory inserts hit `memories_source_conversation_id_fkey`. The harness created conversations, then attempted raw memory inserts whose `source_conversation_id` was not visible/present to the insert statement on the reachable DB path.
- **Evidence**:
  - `asyncpg.exceptions.ForeignKeyViolationError: insert or update on table "memories" violates foreign key constraint "memories_source_conversation_id_fkey"`
  - `DETAIL:  Key (source_conversation_id)=(85932bac-2532-4a30-ae22-4f8620c40e03) is not present in table "conversations".`
  - The same failure repeated for all 10 MR questions in `uv run python -m orchestrator.eval.longmemeval_fast --dataset tests/benchmark_results/task15_mr_subset_10.json --output-dir tests/benchmark_results/task15_mr_fast_standard`
- **Likely cause**: The fast harness mixes conversation creation through `MemoryStore.create_conversation()` with raw SQL inserts on pooled connections, and on this runtime path the referenced conversation rows are not available to the later insert operation as expected (confidence 80%).
- **Suggested action**: Fix the fast harness import path so conversation rows are durably present before raw memory inserts (for example by creating conversations on the same connection/transaction strategy used for the inserts, or by avoiding `source_conversation_id` references in the direct-import path if they are not required for the benchmark contract), then rerun the 10-question MR subset.

## 2026-04-13 22:03 — Live Dream HTTP Trigger Disabled by Missing Admin Key
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 15 reasoning-quality validation
- **Category**: config
- **Blocked current task**: no
- **What happened**: The dedicated manual dream endpoint is present, but the running backend rejects all requests with `403` because `daemon_admin_api_key` is unset in the live runtime. That disables the HTTP/manual admin path even though Redis and the worker are healthy.
- **Evidence**:
  - `POST http://127.0.0.1:8000/memories/dream` → `403 {"detail":"Admin dreaming trigger is disabled"}`
  - `orchestrator/routes/memories.py:52-55` returns 403 when `settings.daemon_admin_api_key` is falsy
- **Likely cause**: The backend container was started without `DAEMON_ADMIN_API_KEY`/`daemon_admin_api_key`, so the route-level guard disables the admin/debug dreaming endpoint by design (confidence 98%).
- **Suggested action**: Decide whether Task/QA environments should expose the manual dream trigger; if yes, provide `DAEMON_ADMIN_API_KEY` in the live env. If not, document that manual dreaming must be enqueued through the same Redis job contract outside HTTP.

## 2026-04-13 22:22 — Live Worker Missing `run_dreaming_job` Registration
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 15 reasoning-quality validation
- **Category**: config
- **Blocked current task**: yes
- **What happened**: Enqueuing the same Redis job contract used by the manual dreaming route succeeded, but the worker rejected the job because it did not have `run_dreaming_job` registered. The running worker process is behind the repo state that now includes the dreaming jobs.
- **Evidence**:
  - Worker log: `job task15-dream-92a4a9dc, function 'run_dreaming_job' not found`
  - No `dream_log` rows were created for the seeded review user after enqueueing the job
- **Likely cause**: The worker container was started before the dreaming job registration landed, or it is running a stale module state that has not reloaded to include `run_dreaming_job` (confidence 90%).
- **Suggested action**: Restart/reload the worker so it imports the current `orchestrator.worker.worker`/`jobs.py` state and confirms `run_dreaming_job` is registered before relying on manual dream enqueues.

## 2026-04-14 09:45 — Worker Entity Resolution Errors Block Fresh Extraction Benchmark
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 16 extraction benchmark closure
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: The `persist_extraction_result` function in `orchestrator/memory/entities.py` receives a Fernet-encrypted token string where a JSON-parseable value is expected for the `aliases` field, causing `asyncpg.exceptions.InvalidTextRepresentationError: invalid input syntax for type json`. This fills the ARQ queue with failing entity resolution jobs, causing extraction job processing delays. This prevents fresh 3-run extraction benchmark execution — individual scenario tests pass after worker restart, but the full 8-scenario × 3-run benchmark exceeds reasonable timeouts.
- **Evidence**:
  - Worker log: `asyncpg.exceptions.InvalidTextRepresentationError: invalid input syntax for type json. Token "gAAAAABp3YPXNc1liobZlRJPTaiWo6t" is invalid.`
  - Stack: `resolve_entities_job` → `persist_extraction_result` → `insert_entity` → `_pool.fetchrow`
  - Full 8-scenario benchmark with 40s wait times exceeds 480s timeout without completing
- **Likely cause**: Entity resolution is passing an encrypted field (or the wrong field entirely) to the JSONB column for `aliases`. The issue appears to be in how `persist_extraction_result` constructs the entity insert call, passing a Fernet token string instead of the unencrypted aliases JSON (confidence 85%).
- **Suggested action**: Investigate `persist_extraction_result` in `orchestrator/memory/entities.py` and `insert_entity` in `orchestrator/memory/store.py` to understand why encrypted values are reaching JSONB columns. This is a production code bug, not a benchmark infrastructure issue.
- **RESOLVED 2026-04-14**: Fix applied to `orchestrator/memory/store.py`. The bug was in `insert_entity` and `update_entity_aliases` passing Fernet token strings directly to JSONB columns. The fix: JSON-encode encrypted aliases before storage (`json.dumps(encrypted_aliases)`) and add `::jsonb` cast. Also updated all entity retrieval methods (`get_entity`, `get_entity_by_lookup_key`, `get_entities_for_user`, `find_entities_by_alias`) to handle new retrieval format requiring double `json.loads`. Added regression tests in `tests/test_entity_integration.py`. All 53 entity tests pass. Fresh extraction benchmark runs should no longer be blocked.

## 2026-04-14 11:15 — Scenario 6 Extraction Format Mismatch (Root Cause of Regression)
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 16 extraction benchmark closure - Scenario 6 missing early technical facts
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: `extract_memories` in `jobs.py` was converting messages to text using `_messages_to_text()` which produces `user: ...` / `assistant: ...` format, but `EXTRACTION_PROMPT` explicitly says "Input contains [User] and [Assistant] markers". This format contract mismatch caused the extraction model to misinterpret the role-labeled input on the live production path.
- **Evidence**:
  - `jobs.py:241` → `text = _messages_to_text(messages)` producing `user:` / `assistant:`
  - `extraction.py:147` → EXTRACTION_PROMPT says "Input contains [User] and [Assistant] markers"
  - `extraction.py:105-120` → `messages_to_extraction_text()` already produces correct `[User]:` / `[Assistant]:` format
  - Scenario 6 runs 2 and 3 missing early facts (`9950X3D`, `Be Quiet Light Base`, `CachyOS`, `Arch`) but keeping later facts
- **Likely cause**: The worker text formatting diverged from the extraction prompt contract after the extraction pipeline was rewritten, and no regression test existed to catch this mismatch (confidence 95%).
- **Suggested action**: Re-run fresh extraction benchmark to verify Scenario 6 now extracts all facts. The format mismatch fix should resolve the recall regression without any other changes.

## 2026-04-14 21:50 — Task 16 Extraction Benchmark COMPLETE
- **Severity**: info
- **Scope**: project
- **Encountered during**: Task 16 extraction benchmark closure
- **Category**: test-success
- **Blocked current task**: no — now resolved
- **What happened**: Fresh 3-run extraction benchmark completed successfully via deterministic transcript replay harness. All 3 runs (bench_20260414_214436, 214718, 215000) show P=1.0, R=1.0, adversarial_fp=0. S6 achieves TP=7 FP=0 FN=0 across all runs, confirming the Scenario 6 format fix resolved the recall regression.
- **Evidence**:
  - `tests/results/bench_20260414_214436.json`: P=1.0, R=1.0, passed=true, S6: TP=7 FP=0 FN=0
  - `tests/results/bench_20260414_214718.json`: P=1.0, R=1.0, passed=true, S6: TP=7 FP=0 FN=0
  - `tests/results/bench_20260414_215000.json`: P=1.0, R=1.0, passed=true, S6: TP=7 FP=0 FN=0
- **Likely cause**: Entity alias JSON persistence fix + extraction format fix + deterministic replay harness combination resolved all blockers.
- **Suggested action**: None — Task 16 extraction regression pass complete. Update artifact files with fresh results.

## 2026-04-14 12:55 — Bug 1 Fixed: persist_extraction_result() Collapsed Unrelated New Entities
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Final review of entity pipeline
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: `persist_extraction_result()` in `orchestrator/memory/entities.py` grouped all `merge_decision == "new"` entities under `resolved_entity_id=None`, collapsing unrelated new entities (e.g., "Alice" and "Bob") into one canonical entity with aliases. The loop picked the first resolution as canonical and added all others as aliases, destroying entity separation.
- **Evidence**:
  - Lines 932-940 in `entities.py`: `entities_by_canonical[canonical_id].append(resolution)` where `canonical_id=None` for all new resolutions
  - Lines 985-1010: only one entity created for the entire `None` bucket
- **Fix applied**: Separated `new_resolutions` from `merged_resolutions` upfront. Each `new` resolution now gets its own `insert_entity()` call with no alias consolidation. `merged` resolutions (with existing `resolved_entity_id`) continue to use the alias-consolidation grouping.
- **Files changed**: `orchestrator/memory/entities.py` (persist_extraction_result function)
- **Verification**: Added `test_two_unrelated_new_entities_remain_separate` in `tests/memory/test_entity_persistence.py`. All 26 memory tests pass.
- **RESOLVED 2026-04-14**

## 2026-04-16 13:56 — Settings sidebar conversation fetch still hits direct-backend CORS then 404 fallback
- **Severity**: warning
- **Scope**: project
- **Encountered during**: autonomous-skill-creation F3 rerun hands-on QA
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: While manually verifying `/settings/skills` in a real Chromium session, the page still emitted browser-side request failures unrelated to the skills proxy itself because the shared conversation history hook attempted `http://localhost:8000/conversations?limit=100` first, hit CORS, then fell back to same-origin `/conversations?limit=100`, which 404ed under Next. The skills pane rendered, but the settings surface still carries visible runtime noise and a broken sidebar data fetch path.
- **Evidence**:
  - Playwright/Chromium console: `Access to fetch at 'http://localhost:8000/conversations?limit=100' from origin 'http://127.0.0.1:3000' has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource.`
  - Playwright network failure: `http://localhost:8000/conversations?limit=100` → `net::ERR_FAILED`
  - Playwright bad response: `http://127.0.0.1:3000/conversations?limit=100` → `404`
  - `frontend/hooks/useConversationHistory.ts:43-45` sets `apiBaseUrl` to `process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "")`
  - `frontend/hooks/useConversationHistory.ts:57-61` falls back to the raw same-origin path `/conversations?limit=100` rather than a proxied API route
- **Likely cause**: The shared conversation-history client still uses the old development-time direct-backend base URL and a non-proxied fallback path, so settings pages inherit a lingering CORS/404 fetch path even after the skills-specific proxy work was repaired (confidence 94%).
- **Suggested action**: Route conversation history through a same-origin API proxy (or add a valid `/api/conversations` bridge) and stop preferring direct backend browser calls in development.
- **Seen again**: 2026-04-16 during the final autonomous-skill-creation F3 rerun — `/settings/skills` loaded and the skills-specific list/detail/download flows worked, but Chromium still logged the same `http://localhost:8000/conversations?limit=100` CORS failure followed by `http://127.0.0.1:3000/conversations?limit=100` returning `404`.

## 2026-04-14 12:55 — Bug 2 Fixed: _get_entity_expanded_candidates() Reintroduced Ineligible Memories
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Final review of retrieval path
- **Category**: runtime-error
- **Blocked current task**: yes
- **What happened**: `_get_entity_expanded_candidates()` in `orchestrator/memory/retrieval.py` called `store.get_memory()` which has no eligibility filtering, then only checked `source_type != "dream"` and `allowed_conversation_ids`. It did NOT filter out `status='deleted'`, `local_only=True`, or `valid_to IS NOT NULL` (superseded) memories. This let entity-linked memories bypass the retrieval contract that `search_memories()` enforces.
- **Evidence**:
  - `store.get_memory()` at line 460-469: `SELECT * FROM memories WHERE id = $1` — no WHERE clause filters
  - `search_memories()` at lines 820-905: enforces `status != 'deleted'`, `valid_to IS NULL`, `local_only = FALSE`, `source_type != 'dream'`
- **Fix applied**: Added inline eligibility checks after `get_memory()` inside `_get_entity_expanded_candidates()`:
  1. `status != 'deleted'`
  2. `valid_to IS NULL`
  3. `source_type != 'dream'`
  4. `local_only == False` (unless `include_local=True`)
  5. `source_conversation_id in allowed_conversation_ids` (if specified)
  Also added `include_local` parameter (default `False`) to `_get_entity_expanded_candidates()` and pass `effective_include_local` from `retrieve_memories()`.
- **Files changed**: `orchestrator/memory/retrieval.py` (`_get_entity_expanded_candidates` and its call site in `retrieve_memories`)
- **Verification**: Added 4 regression tests in `tests/memory/test_entity_persistence.py`:
  - `test_deleted_memory_not_returned`
  - `test_local_only_memory_excluded_when_include_local_false`
  - `test_superseded_memory_excluded`
  - `test_dream_memory_excluded`
  All 26 memory tests pass.
- **RESOLVED 2026-04-14**

## 2026-05-26 10:09 — Glob Tool Fails Because ripgrep Binary Is Missing
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: feature-matrix-review-fixes Task 1/Task 2 verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: The `glob` tool could not enumerate `.sisyphus/notepads/feature-matrix-review-fixes/*.md` because its underlying `/usr/bin/rg` binary is missing in this environment. Verification continued by reading known notepad paths directly.
- **Evidence**: `ENOENT: no such file or directory, posix_spawn '/usr/bin/rg'`
- **Likely cause**: The agent/tooling runtime expects ripgrep at `/usr/bin/rg`, but this host image does not provide that binary at the expected path (confidence 95%).
- **Suggested action**: Install ripgrep or adjust the file-search tooling configuration to point at the available binary so `glob` works reliably during orchestration.


## 2026-05-26 20:28 — Markdown LSP Diagnostics Unavailable For Evidence Files
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: F3. Real Manual QA — feature-matrix-review-fixes evidence artifact validation
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `lsp_diagnostics` could not run on the newly written markdown evidence and notepad files because this environment has no LSP server configured for `.md` extensions.
- **Evidence**:
  - `Error: No LSP server configured for extension: .md`
  - `Available servers: typescript, deno, vue, eslint, oxlint, biome, gopls, ruby-lsp, basedpyright, pyright...`
- **Likely cause**: The local Oh My OpenCode LSP configuration does not include a markdown-capable server, so artifact-only documentation files cannot participate in the standard changed-file diagnostics step (confidence 98%).
- **Suggested action**: Add a markdown-capable LSP server to the tool configuration or document that markdown evidence files should be verified by readback only in this environment.

## 2026-05-27 11:00 — Push of feature-matrix Review Commit Rejected Due to Diverged Remote
- **Severity**: critical
- **Scope**: tooling
- **Encountered during**: feature-matrix-review-fixes push to origin/main
- **Category**: config
- **Blocked current task**: yes
- **What happened**: `git push origin HEAD:main` was rejected with `non-fast-forward` error because `origin/main` has diverged — it contains commits from PR #1 merge that are not in our local branch. The exact commit `48859f43caa986d8a70500a1cc247bc4a7bd16c3` cannot be placed on origin/main without force-push or history rewrite, both of which are forbidden by task constraints.
- **Evidence**:
  - Push error: `! [rejected] HEAD -> main (non-fast-forward)`
  - `origin/main` = `a7bae08bb19189b54a0c75f9a3477ce40724e566` (contains PR #1 merge)
  - Our commit `48859f43` is NOT on origin/main
  - `git ls-remote origin refs/heads/main` = `a7bae08bb19189b54a0c75f9a3477ce40724e566`
- **Likely cause**: The local feature branch was created from an older main state, then PR #1 was merged separately. Our review-fix commit was made after the PR merge, creating a divergent history where our commit is not a descendant of origin/main (confidence 99%).
- **Suggested action**: To get `48859f43` onto origin/main without force-push: either (a) merge our branch into a fresh local main and push the merge commit (but this creates a new commit, not the exact 48859f43), or (b) cherry-pick 48859f43 onto a local copy of origin/main and push that (creates new hash), or (c) request force-push authorization. The exact commit cannot appear on origin/main without violating the no-force-push constraint.


## 2026-05-27T04:57:02Z — Evidence append shell heredoc syntax error
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 9 — append post-write validation evidence
- **Category**: other
- **Blocked current task**: no
- **What happened**: A shell/Python heredoc used to append validation details to the evidence artifact failed before modifying the evidence file. The command was retried with a simpler append payload.
- **Evidence**: `SyntaxError: unterminated triple-quoted string literal (detected at line 27)` followed by `/usr/bin/bash: -c: line 32: unexpected EOF while looking for matching ``'`.
- **Likely cause**: The append payload embedded a nested `PY` heredoc marker and fenced code block inside a triple-quoted Python string, prematurely terminating the outer heredoc (confidence 99%).
- **Suggested action**: For artifact append commands, avoid nested heredoc markers inside Python string literals or use a generated text file/string without matching delimiter text.
- **Seen again 2026-06-05T03:12:52Z**: F1 plan-compliance report write failed with `SyntaxError: unterminated triple-quoted string literal (detected at line 222)` because the report body contained an exact `PY` heredoc delimiter line inside a fenced code block. Retried with a unique heredoc delimiter.

## 2026-05-27 UTC — Workspace had unrelated uncommitted files during W1 plan patch verification
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Task 10 — Surgically Patch W1 Prompt-Surface Plan
- **Category**: other
- **Blocked current task**: no
- **What happened**: The required path-filtered diff stat for `.sisyphus/plans/wave1-prompt-surface-changes.md` passed, but a global changed-file probe showed unrelated pre-existing modified paths outside this task scope. This task did not edit those files.
- **Evidence**: `GIT_MASTER=1 git diff --name-only` returned `.sisyphus/plans/wave1-prompt-surface-changes.md`, `TRIAGE.md`, and `frontend/next-env.d.ts`.
- **Likely cause**: Workspace carried over modified files from earlier sessions or generated frontend type artifacts before this task began (confidence 80%).
- **Suggested action**: Review or stash unrelated workspace changes before requiring a globally clean changed-file check; for this task, use the required path-filtered diff stat to verify the W1 plan patch itself.

## 2026-05-27 UTC — Extraction Benchmark Determinism Test Imports Missing Symbol
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 7 — Triage and W1 Gate Handoff (extraction-benchmark-recovery-rebuild)
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `tests/memory/test_extraction_determinism.py` (line 13) imports `BENCHMARK_MODE` from `orchestrator.memory.extraction`, but the symbol does not exist in that module. The import fails with `ImportError: cannot import name 'BENCHMARK_MODE' from 'orchestrator.memory.extraction'`.
- **Evidence**:
  - `from orchestrator.memory.extraction import BENCHMARK_MODE` → `ImportError: cannot import name 'BENCHMARK_MODE'`
  - `grep -r "BENCHMARK_MODE" orchestrator/memory/extraction.py` → no matches
  - `grep -r "DEDUP_BENCHMARK_MODE" orchestrator/memory/dedup.py` → no matches
  - `tests/memory/test_extraction_determinism.py` also imports `DEDUP_BENCHMARK_MODE`, `BENCHMARK_SEED`, `DEDUP_BENCHMARK_SEED`, `EXTRACTION_TEMPERATURE`, `CONTRADICTION_TEMPERATURE` from the same modules
- **Likely cause**: The determinism test file expects symbols that were never exported from the production extraction/dedup modules, or were removed/refactored without updating the test imports (confidence 92%).
- **Suggested action**: Either add the missing symbol exports to `orchestrator.memory.extraction` and `orchestrator.memory.dedup`, or update the test imports to use the correct symbol paths. Do not run `tests/memory/test_extraction_determinism.py` until the import gap is resolved.

## 2026-05-27 UTC — Extraction Benchmark External Service Dependencies
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 7 — Triage and W1 Gate Handoff (extraction-benchmark-recovery-rebuild)
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: The canonical extraction benchmark (`tests/benchmark_extraction.py` v2.4) depends on external services with no local fallback: OpenRouter for extraction model calls and contradiction detection, and Voyage AI for embedding generation. A service outage or credential failure would block benchmark execution.
- **Evidence**:
  - Task 4 validation confirmed OpenRouter (GPT-4o-mini extraction + contradiction) and Voyage AI (embeddings) are required
  - No local fallback model or embedding service is configured for extraction
  - Task 4 ran successfully with no service failures, but the dependency risk remains
- **Likely cause**: The benchmark harness intentionally exercises the production extraction pipeline against live external services to detect real-world quality regressions (confidence 95%).
- **Suggested action**: Document the dependency risk in benchmark runbook. Consider adding a dry-run mode that validates connectivity before full execution. Do not attempt to mock these services in the benchmark harness — the purpose is real-world quality detection.
## 2026-05-27T11:31:53Z — GitHub large file warning on main push
- **Severity**: warning
- **Scope**: project
- **Encountered during**: auth-device-model Task 1 preflight cleanup
- **Category**: config
- **Blocked current task**: no
- **What happened**: `git push origin main` completed, but GitHub warned that a committed cleanup archive artifact exceeds the recommended 50 MB file size threshold.
- **Evidence**: `remote: warning: File .cleanup/2026-05-06/safety-net/untracked_archive/tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_filtered_dataset.json is 91.64 MB; this is larger than GitHub's recommended maximum file size of 50.00 MB`
- **Likely cause**: Large benchmark recovery artifact was included in the pre-existing cleanup archive committed to preserve local work (confidence 95%).
- **Suggested action**: Review whether large benchmark/archive artifacts should be moved to external artifact storage or Git LFS in a separate cleanup task.

## 2026-05-31T05:36:14Z — pip-audit Reports 29 Vulnerabilities In Current Python Lockset
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 4 (Backend Pyright gate + grandfather baseline and SCA tool)
- **Category**: security
- **Blocked current task**: no — Task 4 requires inventory without suppression; future CI SCA gate would fail until remediated
- **What happened**: `uv run pip-audit` exited 1 and reported 29 known vulnerabilities across 13 installed packages. No findings were suppressed or ignored.
- **Evidence**: Command output: `Found 29 known vulnerabilities in 13 packages`; affected packages and fixes: aiohttp 3.13.3 -> 3.13.4 (10 CVEs), cryptography 46.0.5 -> 46.0.6/46.0.7 (PYSEC-2026-35/36), idna 3.11 -> 3.15 (CVE-2026-45409), litellm 1.81.1 -> 1.83.0/1.83.7 (5 findings), lxml 6.0.2 -> 6.1.0, pygments 2.19.2 -> 2.20.0, pyjwt 2.11.0 -> 2.12.0, pytest 9.0.2 -> 9.0.3, python-dotenv 1.2.1 -> 1.2.2, python-multipart 0.0.22 -> 0.0.26/0.0.27, requests 2.32.5 -> 2.33.0, starlette 0.50.0 -> 1.0.1, urllib3 2.6.3 -> 2.7.0. Full output recorded in `.sisyphus/evidence/task-4-pip-audit.txt`.
- **Seen again**: 2026-05-31T06:31Z during Task 7 local CI parity; `.sisyphus/evidence/task-7-ci-local-parity.txt` shows `uv run pip-audit` exit code 1 with `Found 29 known vulnerabilities in 13 packages`.
- **Seen again**: 2026-06-04T10:28Z during local CI PR-submission wiring verification; `scripts/local_ci.sh backend` kept `pip-audit` non-blocking as inventory and reported `Found 36 known vulnerabilities in 13 packages`.
- **Likely cause**: Current locked dependency versions lag newly published vulnerability advisories; several are transitive dependencies of FastAPI/LiteLLM/aiohttp stack (confidence 90%).
- **Suggested action**: Plan a dedicated dependency-upgrade/remediation task using uv-managed upgrades, then rerun `uv run pip-audit` without suppressions.

## 2026-05-31T06:31Z — Whole-Suite Pytest Collection Has Additional Import Drift

- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 7 — CI workflow local parity
- **Category**: test-failure
- **Blocked current task**: no — Task 7 records blocker inventory and must not fix test/app drift
- **What happened**: `PYTHONPATH=. uv run pytest -q` exited 2 during collection. In addition to the known `tests/test_video_e2e.py:596` syntax error, collection reports multiple import drifts for benchmark/advisor/router symbols.
- **Evidence**: `.sisyphus/evidence/task-7-ci-local-parity.txt` shows import errors for `get_benchmark_tracking` from `orchestrator.memory.extraction`, `BenchmarkProviderError` from `tests.longmemeval.evaluate`, `BENCHMARK_CONFIG_PIN_PATH` from `orchestrator.eval.runner`, `BenchmarkSamplingError` from `orchestrator.memory.extraction`, `_detect_temporal_query_window` from `orchestrator.memory.retrieval`, `create_advisor_registry` from `orchestrator.tools.builtin`, and `classify_message` from `orchestrator.model_router`, followed by `tests/test_video_e2e.py:596` `SyntaxError: unmatched ')'`.
- **Likely cause**: Existing test modules reference benchmark/advisor/router APIs that have moved or been removed while the project-wide pytest gate was absent or already blocked by earlier collection failures (confidence 85%).
- **Suggested action**: Commission a focused test collection repair task after Task 7; keep CI pytest command intact so these failures remain visible.

## 2026-05-31T06:31Z — Next Build Regenerated Service Worker Artifact

- **Severity**: info
- **Scope**: project
- **Encountered during**: Task 7 — CI workflow local parity
- **Category**: config
- **Blocked current task**: no
- **What happened**: Running the frontend build as part of local CI parity modified generated `frontend/public/sw.js`, which is outside Task 7's allowed file set. The generated file was restored immediately.
- **Evidence**: `git status --short -- frontend/public/sw.js` showed `M frontend/public/sw.js` after `cd frontend && npm run build`; `git checkout -- frontend/public/sw.js` was run and subsequent `git diff -- frontend/public/sw.js` was empty.
- **Seen again**: 2026-06-23 during #108 frontend build verification. `npm run build` modified `frontend/public/sw.js` and deleted `frontend/public/workbox-00a24876.js`; both are generated PWA artifacts and were left unstaged.

## 2026-05-28T20:04:00Z — pycache permission denied during syntax verification
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Task 14 — legacy API-key removal final verification
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `python -m py_compile` on modified Python files failed with `Permission denied: __pycache__/*.pyc` due to container host-user vs container-process user mismatch (container root vs host user).
- **Evidence**: `PermissionError: [Errno 13] Permission denied: 'orchestrator/__pycache__/main.cpython-314.pyc.140168641807248'`
- **Likely cause**: Host user's permissions don't match container's pycache directory ownership (confidence 95%).
- **Suggested action**: Use `python -c "import ..."` syntax check instead of `py_compile` for verification in containerized environments.


## 2026-05-28T14:42:37Z — ast-grep pattern parse failure during auth review
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: PR #4 backend/admin auth slice review
- **Category**: other
- **Blocked current task**: no
- **What happened**: `ast_grep_search` failed on a Python query that was intended to enumerate route function definitions, so the search fell back to grep/LSP.
- **Evidence**: `Error: Cannot parse query as a valid pattern. Help: The pattern either fails to parse or contains error. Please refer to pattern syntax guide. See also: https://ast-grep.github.io/guide/pattern-syntax.html` and `Multiple AST nodes are detected. Please check the pattern source 'async def µNAME(µµµ) { µµµ }'.`
- **Likely cause**: The query was not a valid complete AST pattern for the Python parser (confidence 99%).
- **Suggested action**: Use a complete Python AST pattern or switch to grep/LSP for route enumeration.
## 2026-05-29 UTC — Mis-typed InlineArtifact path during search
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: PR #4 auth-device-model frontend artifact-consumption search
- **Category**: other
- **Blocked current task**: no
- **What happened**: A read attempt targeted `frontend/components/InlineArtifact.tsx`, but the file does not exist at that path; the actual component lives at `frontend/components/chat/InlineArtifact.tsx`.
- **Evidence**: `File not found: /home/sol/daemon/frontend/components/InlineArtifact.tsx`
- **Likely cause**: Search/inspection path typo during broader artifact-pattern review (confidence 99%).
- **Suggested action**: Use the `components/chat/InlineArtifact.tsx` path for any future inspection.


## 2026-05-31 04:04 UTC — Frontend typecheck fails on pre-existing advisor event type drift
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #4 Studio video SSE endpoint review fix verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: A full frontend TypeScript check failed on advisor event tests and `lib/advisorEvents.ts` type mismatches unrelated to the Studio video endpoint candidate change. The targeted Studio video test still passes.
- **Evidence**: `npx tsc --noEmit --pretty false` exited with code 2 and reported errors including `__tests__/advisor-events.test.ts(4,3): error TS2305: Module '"../lib/events"' has no exported member 'isAdvisorEndEvent'.`, multiple `Type '"advisor_start"' is not assignable to type ...` errors, and `lib/advisorEvents.ts(521,25): error TS2352: Conversion of type ... to type 'ChatEvent' may be a mistake`.
- **Likely cause**: Advisor event tests/helpers appear to expect event union members and type guards that are not currently exported by `lib/events.ts` (confidence 90%).
- **Suggested action**: Reconcile advisor event schema exports with `lib/advisorEvents.ts` and `__tests__/advisor-events.test.ts`, or exclude stale tests from the typecheck if intentionally obsolete.

## [2026-05-31T08:59:30Z] — CodeQL Actions Node 20 Deprecation Warnings
- **Severity**: warning
- **Scope**: upstream
- **Encountered during**: F3 Real Manual QA for ci-tooling-baseline PR #5
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Live CodeQL check annotations for PR #5 passed but emitted GitHub Actions deprecation warnings for Node.js 20 and CodeQL Action v3.
- **Evidence**: `gh api repos/sol-aeternum/Daemon/check-runs/78712719260/annotations --paginate` and `gh api repos/sol-aeternum/Daemon/check-runs/78712719252/annotations --paginate` returned warnings: "Node.js 20 actions are deprecated" and "CodeQL Action v3 will be deprecated in December 2026."
- **Likely cause**: GitHub runner/action lifecycle moving CodeQL JavaScript actions from Node 20 toward Node 24 and future CodeQL Action v4 (confidence 95%).
- **Suggested action**: Track GitHub's CodeQL Action v4 migration window separately; do not change this CI baseline PR unless commissioned.

## [2026-05-31T09:00:00Z] — Safe Gate Probes Run On Local Main Surface Non-PR Debt
- **Severity**: info
- **Scope**: project
- **Encountered during**: F3 Real Manual QA for ci-tooling-baseline PR #5
- **Category**: config
- **Blocked current task**: no
- **What happened**: Selected safe local gate probes were run from the required clean local `main` checkout, not the PR branch. `python scripts/lint_feature_matrix.py` passed, but `uv run ruff format --check .`, `uv run basedpyright`, and Renovate validation were not representative of PR-branch CI state because `main` lacks PR branch config/artifacts and still exposes known project debt.
- **Evidence**: `uv run ruff format --check .` failed parsing `tests/test_video_e2e.py:596` and reported 127 files would reformat; `uv run basedpyright` reported 328 errors / 9870 warnings; `npx --yes --package renovate renovate-config-validator --strict renovate.json` returned `ERROR: File does not exist "file": "renovate.json"`; `python scripts/lint_feature_matrix.py` returned `OK: 60 feature rows validated`.
- **Likely cause**: F3 guardrail requires local HEAD remain on clean `main`, while CI baseline tooling files are intentionally only on PR branch `ci-tooling-baseline-2026-05-28`; whole-repo local gates on main therefore measure pre-baseline project debt rather than PR branch behavior (confidence 95%).
- **Suggested action**: For future manual QA, prefer live PR checks and `gh api` check-run/log data for branch-specific CI state unless explicitly switching to the PR branch is allowed.

## [2026-05-31T10:01:00Z] — Follow-up Local Gate Verification Surfaced Remaining Baseline Red Gates
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #5 follow-up — resolve remaining red gates
- **Category**: build-error
- **Blocked current task**: yes
- **What happened**: Local verification confirmed several CI steps still exit non-zero on known inventory debt, so the follow-up keeps those steps visible but non-blocking in CI while preserving their output.
- **Evidence**: `uv run bandit -r orchestrator providers scripts tests` exited 1 with `Low: 3500`, `Medium: 30`, `High: 0`, and `tests/test_video_e2e.py (syntax error while parsing AST from file)`; `uv run pip-audit` exited 1 with `Found 29 known vulnerabilities in 13 packages`; `PYTHONPATH=. uv run pytest -q` exited 2 with 8 collection errors including `tests/test_video_e2e.py:596 SyntaxError: unmatched ')'`; frontend `npm run type-check`, `npm run lint`, `npm run format:check`, `npm run audit:ci`, `npm run test:run`, and `npm run build` exited non-zero on existing advisor-event, lint, format, audit, test, and build debt.
- **Seen again**: 2026-06-07 during PR hosted-identity follow-up for failing backend gates. `uv run bandit -r orchestrator providers scripts tests` exited 1 with `Low: 4535`, `Medium: 25`, `High: 0`, and `tests/test_video_e2e.py (syntax error while parsing AST from file)`; `uv run pip-audit` exited 1 with `Found 41 known vulnerabilities in 14 packages`; `PYTHONPATH=. uv run pytest -q` exited 2 with the same 8 collection errors/import drifts plus `tests/test_video_e2e.py:596 SyntaxError: unmatched ')'`.
- **Likely cause**: The CI baseline PR intentionally introduced first-run inventories before the existing project debt was remediated, but several inventory commands were still wired as required/failing steps (confidence 95%).
- **Suggested action**: Keep these inventory steps non-blocking until dedicated remediation tasks upgrade dependencies, repair pytest collection, fix frontend contracts/tests, and apply mechanical formatting.
- **Seen again**: 2026-06-08 during PR #17 review-comment follow-up. `scripts/local_ci.sh backend` reported `uv run bandit -r orchestrator providers scripts tests` exit 1 with pre-existing inventory (`Low: 4606`, `Medium: 24`, `High: 0`) and `tests/test_video_e2e.py (syntax error while parsing AST from file)`; `uv run pip-audit` exit 1 with `Found 41 known vulnerabilities in 14 packages`; `PYTHONPATH=. uv run pytest -q` exit 2 with the same 8 collection errors/import drifts plus `tests/test_video_e2e.py:596 SyntaxError: unmatched ')'`.
- **Seen again**: 2026-06-08 during PR #17 integer-precision review follow-up. `scripts/local_ci.sh backend` passed blocking gates, but inventory reported `uv run bandit -r orchestrator providers scripts tests` exit 1 with pre-existing inventory (`Low: 4609`, `Medium: 24`, `High: 0`) and `tests/test_video_e2e.py (syntax error while parsing AST from file)`; `uv run pip-audit` exit 1 with `Found 41 known vulnerabilities in 14 packages`; `PYTHONPATH=. uv run pytest -q` exit 2 with the same 8 collection errors/import drifts plus `tests/test_video_e2e.py:596 SyntaxError: unmatched ')'`.
- **Seen again**: 2026-06-08 during PR #17 unary-recursion review follow-up. `scripts/local_ci.sh backend` passed blocking gates, but inventory reported `uv run bandit -r orchestrator providers scripts tests` exit 1 with pre-existing inventory (`Low: 4611`, `Medium: 24`, `High: 0`) and `tests/test_video_e2e.py (syntax error while parsing AST from file)`; `uv run pip-audit` exit 1 with `Found 41 known vulnerabilities in 14 packages`; `PYTHONPATH=. uv run pytest -q` exit 2 with the same 8 collection errors/import drifts plus `tests/test_video_e2e.py:596 SyntaxError: unmatched ')'`.
- **Seen again**: 2026-06-08 during PR #17 complex-result review follow-up. `scripts/local_ci.sh backend` passed blocking gates, but inventory reported `uv run bandit -r orchestrator providers scripts tests` exit 1 with pre-existing inventory (`Low: 4613`, `Medium: 24`, `High: 0`) and `tests/test_video_e2e.py (syntax error while parsing AST from file)`; `uv run pip-audit` exit 1 with `Found 41 known vulnerabilities in 14 packages`; `PYTHONPATH=. uv run pytest -q` exit 2 with the same 8 collection errors/import drifts plus `tests/test_video_e2e.py:596 SyntaxError: unmatched ')'`.
- **Seen again**: 2026-06-07 during PR #8 review-fix verification when `uv run bandit -r orchestrator providers scripts tests` exited 1 with pre-existing inventory (`Low: 3480`, `Medium: 26`, `High: 0`) and `tests/test_video_e2e.py (syntax error while parsing AST from file)`. A scoped production-file Bandit check for `orchestrator/eval/fact_harness.py` and `orchestrator/eval/chunk_harness.py` passed with no issues.
- **Seen again**: 2026-06-08 during PR #20 benchmark replay follow-up verification when `scripts/local_ci.sh backend` passed all blocking backend gates but reported non-blocking inventory: `uv run bandit -r orchestrator providers scripts tests` exited 1 with `Low: 4585`, `Medium: 25`, `High: 0`, and `tests/test_video_e2e.py (syntax error while parsing AST from file)`; `uv run pip-audit` exited 1 with `Found 41 known vulnerabilities in 14 packages`; `PYTHONPATH=. uv run pytest -q` exited 2 with the same 8 collection errors/import drifts plus `tests/test_video_e2e.py:596 SyntaxError: unmatched )`.

## [2026-05-31T10:02:00Z] — BasedPyright Baseline Was Not Loaded By Default Command
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #5 follow-up — resolve remaining red gates
- **Category**: config
- **Blocked current task**: yes
- **What happened**: `uv run basedpyright` exited 1 despite reporting `0 errors, 2743 warnings, 0 notes`; the command still treats warning-level inventory as a non-zero result. Running `uv run basedpyright --level error` exited 0 while preserving an error-level type gate.
- **Evidence**: Initial command summary: `0 errors, 2743 warnings, 0 notes` with exit code 1; `uv run basedpyright --level error` reported `0 errors, 0 warnings, 0 notes` with `EXIT:0`.
- **Likely cause**: The baseline PR intended to ratchet existing type debt, but the documented/CI command did not constrain the gate to error-level diagnostics while warning-level debt remains grandfathered (confidence 90%).
- **Suggested action**: Keep `[tool.basedpyright] baselineFile = ".basedpyright/baseline.json"` and run `uv run basedpyright --level error` in CI until warning-level type debt is remediated or separately baselined.

## [2026-05-31T10:03:00Z] — Ruff Gate Found Two Unused Test Locals
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #5 follow-up — resolve remaining red gates
- **Category**: build-error
- **Blocked current task**: yes
- **What happened**: The local Ruff lint gate failed on two unused local variables in auth/enrollment tests. The variables were not used by subsequent assertions, so they were removed in this follow-up.
- **Evidence**: `uv run ruff check .` reported `F841 Local variable raw_refresh_a2 is assigned to but never used` at `tests/test_auth_smoke.py:502:21` and `F841 Local variable pepper is assigned to but never used` at `tests/test_enrollment_flow.py:931:17`.
- **Likely cause**: Prior test edits left behind dead assignments after assertions were simplified (confidence 90%).
- **Suggested action**: No further action for these two findings after this patch; keep Ruff required in CI.

## [2026-05-31T10:04:00Z] — Local Node 20 Emits Commitlint Engine Warnings
- **Severity**: warning
- **Scope**: host
- **Encountered during**: PR #5 follow-up — resolve remaining red gates
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: Local `npm ci` completed, but emitted repeated `EBADENGINE` warnings because the local shell uses Node v20.20.2 while `@commitlint/*@21` requires Node >=22.12.0. The GitHub workflow pins Node 24 for frontend and commitlint CI jobs.
- **Evidence**: `npm ci` warned `Unsupported engine { package: '@commitlint/cli@21.0.2', required: { node: '>=22.12.0' }, current: { node: 'v20.20.2', npm: '11.4.2' } }`.
- **Likely cause**: Host/container Node version is older than the workflow-pinned Node runtime (confidence 99%).
- **Suggested action**: Use Node 24 locally when validating commitlint/frontend gates, or rely on the workflow setup-node step for CI parity.
- **Seen again**: 2026-05-31T10:25:43Z during PR follow-up basedpyright verification; `npm ci` completed but emitted the same `EBADENGINE Unsupported engine` warnings for `@commitlint/*@21` under local Node `v20.20.2`.
- **Seen again**: 2026-06-08 during PR #21 Studio image API retirement follow-up; `npm ci` completed but emitted the same `EBADENGINE Unsupported engine` warnings for `@commitlint/*@21` under local Node `v20.20.2`.

## [2026-05-31T10:05:00Z] — PyYAML Missing In Backend Environment For Ad Hoc Workflow Validation
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: PR #5 follow-up — resolve remaining red gates
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: An ad hoc Python YAML-parse check could not run because the current backend environment does not include `yaml`/PyYAML. The workflow and pre-commit YAML files were validated immediately afterward with Ruby's standard YAML parser instead.
- **Evidence**: `python - <<'PY' ... import yaml ... PY` failed with `ModuleNotFoundError: No module named 'yaml'`; `ruby -e 'require "yaml"; ...' .github/workflows/ci.yml .pre-commit-config.yaml` printed both files as `ok`.
- **Likely cause**: PyYAML is not a project dependency, and this was an ad hoc validation helper rather than a project gate (confidence 99%).
- **Suggested action**: Use Ruby's built-in YAML parser or another existing tool for future workflow syntax smoke checks; do not add PyYAML solely for this.

## [2026-05-31T10:09:00Z] — Pre-commit Gitleaks Environment Bootstrap Blocked By Proxy
- **Severity**: warning
- **Scope**: host
- **Encountered during**: PR #5 follow-up — resolve remaining red gates
- **Category**: tooling
- **Blocked current task**: no
- **What happened**: `uv run pre-commit run --all-files` could not bootstrap the remote gitleaks hook environment because pre-commit's Go installer request was blocked by the host proxy. The local ruff hooks were verified with `SKIP=gitleaks`, and the commit-message hook was verified separately.
- **Evidence**: `uv run pre-commit run --all-files` failed with `An unexpected error has occurred: URLError: <urlopen error Tunnel connection failed: 403 Forbidden>`; `/root/.cache/pre-commit/pre-commit.log` shows the blocked request while opening `https://go.dev/dl/?mode=json`; `SKIP=gitleaks uv run pre-commit run --all-files` passed ruff hooks and skipped gitleaks.
- **Likely cause**: The local container's outbound proxy blocks Go toolchain discovery for pre-commit's golang language environment (confidence 90%).
- **Suggested action**: Validate gitleaks in CI or in a host with Go/pre-commit network access; do not weaken the hook config for this host limitation.
- **Seen again**: 2026-05-31T10:26:58Z during PR follow-up basedpyright verification; `uv run pre-commit run --all-files` again failed while installing the remote gitleaks environment with `URLError: <urlopen error Tunnel connection failed: 403 Forbidden>`. `SKIP=gitleaks uv run pre-commit run --all-files` and the explicit commitlint hook passed.
- **Seen again**: 2026-06-07 during PR follow-up for failing CI; `uv run pre-commit run --all-files` again failed while installing the remote gitleaks environment with `URLError: <urlopen error Tunnel connection failed: 403 Forbidden>`.
- **Seen again**: 2026-06-07 during PR hosted-identity follow-up for failing backend gates; `uv run pre-commit run --all-files` again failed while installing the remote gitleaks environment with `URLError: <urlopen error Tunnel connection failed: 403 Forbidden>`. `SKIP=gitleaks uv run pre-commit run --all-files` passed.
- **Seen again**: 2026-06-08 during PR #20 benchmark replay follow-up verification; `uv run pre-commit run --all-files` again failed while installing the remote gitleaks environment with `URLError: <urlopen error Tunnel connection failed: 403 Forbidden>`. `SKIP=gitleaks uv run pre-commit run --all-files` passed.

## 2026-05-31T10:24:30Z — BasedPyright config consolidation surfaced benchmark harness typing debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR follow-up — fix final remaining basedpyright error
- **Category**: build-error
- **Blocked current task**: yes
- **What happened**: Running the basedpyright gate while validating the requested final fix revealed the root `pyproject.toml` had separate `[tool.pyright]` and `[tool.basedpyright]` sections that basedpyright rejects when used as the explicit project config. After consolidating the configuration, the stricter standard-mode config surfaced unbaselined dictionary `update` typing errors in `tests/benchmark_harness/verify_recovery_logic.py`.
- **Evidence**: `uv run basedpyright --level error -p pyproject.toml` printed `Pyproject file cannot have both pyright and basedpyright sections. pick one` and exited `3`; after config consolidation, `uv run basedpyright --level error` reported `tests/benchmark_harness/verify_recovery_logic.py:156:5 - error: No overloads for "update" match the provided arguments` plus the same pattern at lines 157, 211, and 212.
- **Likely cause**: The default command had been loading `pyrightconfig.json`, hiding the invalid pyproject basedpyright config; once the config was unified, dict inference for recovery result rows was too narrow for later inserting list-valued `raw_session_ids` rows (confidence 95%).
- **Suggested action**: Keep basedpyright settings in one `[tool.basedpyright]` section and keep `pyrightconfig.json` synchronized for editor/default CLI discovery; continue ratcheting the baseline as real errors are fixed.

## 2026-06-04 UTC — Council lifespan test fails pre-existing on pepper validation
- **Severity**: warning
- **Scope**: project
- **Encountered during**: TODO 6 startup-wiring fix verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `tests/council/test_sse_integration.py` (3 tests) error during fixture setup with `PepperValidationError: daemon_auth_pepper is required in production`. The test fixture sets `DATABASE_URL`, `REDIS_URL`, and `MOCK_LLM` env vars but does not set `DAEMON_ENVIRONMENT=development` or a valid `DAEMON_AUTH_PEPPER`. The `Settings` default for `daemon_environment` is `"production"` (`orchestrator/config.py:63`), so lifespan startup fails on the existing pepper gate.
- **Evidence**: `pytest tests/council/test_sse_integration.py` → `ERROR ... PepperValidationError: daemon_auth_pepper is required in production`. Verified pre-existing by `git checkout 973c4b4d` (the prior TODO 6 commit) and re-running; the same 3 errors appear, confirming they are not introduced by the TODO 6 startup-wiring fix.
- **Likely cause**: The council test fixture predates any startup-time pepper validation enforcement and was never updated when the pepper check became mandatory in production. Confidence: high.
- **Suggested action**: Update `tests/council/test_sse_integration.py` fixture to also `monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")` (or set a dev-acceptable pepper). This is a test-infra fix, not a TODO 6 scope item.

## 2026-06-04 UTC — Auth user-scoping tests fail pre-existing on pepper env
- **Severity**: warning
- **Scope**: project
- **Encountered during**: TODO 7 rate-limiter verification (running the auth test surface)
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `tests/test_auth_user_scoping.py` (5 tests) error during fixture setup with `PepperValidationError: daemon_auth_pepper is required in production`. Same root cause as the council lifespan entry above: the fixture does not set `DAEMON_ENVIRONMENT=development` or a test pepper.
- **Evidence**: `pytest tests/test_auth_user_scoping.py` → 5 errors, all `PepperValidationError: daemon_auth_pepper is required in production`. Verified pre-existing by `git stash` of the TODO 7 changes and re-running on commit `1455a63b` — the same 5 errors appear.
- **Likely cause**: The user-scoping test fixture predates startup-time pepper validation. Confidence: high.
- **Suggested action**: Follow-up test-fixture update (set `DAEMON_ENVIRONMENT=development` or a test pepper), same fix pattern as the council lifespan entry.

## 2026-06-04 UTC — Auth smoke lifecycle test fails pre-existing on 401 vs 410
- **Severity**: warning
- **Scope**: project
- **Encountered during**: TODO 7 rate-limiter verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `tests/test_auth_smoke.py::TestAuthDeviceLifecycleSmoke::test_full_lifecycle_smoke` assertion failure: `assert 401 == 410`. The test expects a consumed-reuse path to return 410, but the route returns 401. The drift predates TODO 7.
- **Evidence**: `pytest tests/test_auth_smoke.py::TestAuthDeviceLifecycleSmoke::test_full_lifecycle_smoke` → `FAILED ... assert 401 == 410`. Verified pre-existing by `git stash` of the TODO 7 changes and re-running on commit `1455a63b` — the same failure appears.
- **Likely cause**: Status code drift between the test expectation and the actual route response (likely the route was changed to return 401 on a path the test expected to be 410). Confidence: high.
- **Suggested action**: Follow-up: decide whether the test or the route is correct and reconcile.

## 2026-06-05 UTC — Frontend build fails on missing qrcode.react dependency
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 15 — Auth landing component verification
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: `npm run build` in `frontend/` fails with `Module not found: Can't resolve 'qrcode.react'` from `components/settings/EnrollmentModal.tsx`. This is a pre-existing dependency issue unrelated to Task 15 changes.
- **Evidence**: `npm run build` output: `./components/settings/EnrollmentModal.tsx Module not found: Can't resolve 'qrcode.react'`
- **Likely cause**: `qrcode.react` is listed in `package.json` dependencies but may not be installed, or its types are missing (confidence 85%).
- **Suggested action**: Verify `qrcode.react` is installed in `frontend/node_modules/`; if missing, run `npm ci` or check for installation issues.

## 2026-06-05 UTC — Frontend type-check has pre-existing advisorEvents and tool_call_log errors
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 15 — Auth landing component verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `npm run type-check` reports 80+ errors in `lib/advisorEvents.ts`, `__tests__/advisor-events.test.ts`, `__tests__/tool-call-log.test.ts`, and `components/settings/EnrollmentModal.tsx`. Task 15 files (`lib/deployment.ts`, `components/AuthLanding.tsx`, `app/setup/page.tsx`, `__tests__/auth-landing.test.tsx`) have zero type errors.
- **Evidence**: `npm run type-check` output shows errors only in pre-existing files; no errors in newly created/modified Task 15 files.
- **Likely cause**: Advisor event types were refactored without updating all consumers; `tool_call_id` and `advisor_id` fields were removed from base event types but still used in tests and `lib/advisorEvents.ts` (confidence 90%).
- **Suggested action**: Update `lib/advisorEvents.ts` and related tests to match current event type definitions, or restore the missing type fields.
- **Seen again**: 2026-06-05 during PR #7 hosted-identity review-comment verification when `./node_modules/.bin/tsc --noEmit --pretty false -p tsconfig.json` failed only in the same pre-existing advisor/tool-call event files while the targeted auth/deployment Vitest suite, changed-file eslint, and changed-file prettier checks passed.
- **Seen again**: 2026-06-08 during PR #21 Studio image API retirement follow-up when `npm run type-check` failed in the same pre-existing `__tests__/advisor-events.test.ts` and `lib/advisorEvents.ts` advisor/tool-call event types after `next typegen` succeeded.

## 2026-06-05 UTC — Frontend lint has pre-existing errors across 29 files
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Task 15 — Auth landing component verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: `npm run lint` reports 29 errors and 13 warnings across existing files (e.g., `app/page.tsx`, `components/ToolCallBlock.tsx`, `hooks/useLocalStorage.ts`). Task 15 files have zero lint errors.
- **Evidence**: `npm run lint` output shows 42 total problems; none in `lib/deployment.ts`, `components/AuthLanding.tsx`, `app/setup/page.tsx`, or `__tests__/auth-landing.test.tsx`.
- **Likely cause**: These are pre-existing lint violations from earlier development, likely introduced before the current strict eslint config was applied (confidence 95%).
- **Suggested action**: Run a dedicated lint-fix pass across the frontend, or grandfather existing violations and enforce lint only on changed files.

## 2026-06-05 UTC — Temporary refresh sessions rotate into 90-day persistent sessions
- **Severity**: critical
- **Scope**: project
- **Encountered during**: Task 22 — Hosted Identity Oracle security/product review
- **Category**: security
- **Blocked current task**: no
- **What happened**: A temporary hosted-identity session is issued with session-cookie / short-TTL semantics, but `/v1/auth/refresh` does not preserve that temporary posture. Refresh rotation recreates the session with the global 90-day refresh lifetime and sets a persistent `Max-Age=7776000` cookie, silently upgrading a temporary/public-computer session into a long-lived private session.
- **Evidence**: `orchestrator/routes/auth_setup.py:1601-1649` hardcodes `refresh_expires = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)` and `build_refresh_cookie(... max_age=refresh_max_age)` on every successful web refresh; `orchestrator/services/identity/session_issuance.py:251-286` is where temporary sessions are initially distinguished; targeted ASGI proof command on 2026-06-05 returned `status 200`, `set_cookie __Host-daemon_refresh=...; Max-Age=7776000`, and `rotated_refresh_days 90.0` after seeding a 10-minute temporary web refresh token.
- **Likely cause**: The pre-existing refresh endpoint only keys on `client_kind` and has no persisted `device_persistence` / temporary-session flag to carry forward during rotation, so temporary hosted-identity sessions fall back to the legacy 90-day refresh path on first refresh (confidence 97%).
- **Suggested action**: In a follow-up auth fix, persist temporary/private refresh semantics (or an equivalent session-scope marker) and teach `/v1/auth/refresh` to rotate temporary sessions without lengthening TTL or minting persistent cookies/JSON refresh tokens.


## 2026-06-05 UTC — Plain text artifacts cannot satisfy changed-file LSP diagnostics
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Task 22 — Hosted Identity Oracle security/product review verification
- **Category**: config
- **Blocked current task**: no
- **What happened**: Required changed-file diagnostics could not run on the new evidence artifact `.sisyphus/evidence/hosted-identity-claim/task-22-oracle.txt` because this workspace has no LSP server configured for `.txt` files. The review/report content was still verified via direct reads and grep-based checks.
- **Evidence**: `lsp_diagnostics(filePath="/home/sol/daemon/.sisyphus/evidence/hosted-identity-claim/task-22-oracle.txt", severity="error")` returned `Error: No LSP server configured for extension: .txt` and listed only code-oriented servers (`typescript, deno, vue, eslint, oxlint, biome, gopls, ruby-lsp, basedpyright, pyright...`).
- **Likely cause**: OpenCode LSP configuration in this environment does not include a plain-text-capable language server, so `.txt` evidence artifacts cannot participate in changed-file diagnostics (confidence 99%).
- **Suggested action**: If plain-text evidence files are expected to satisfy changed-file diagnostics, add a `.txt`-capable LSP to the workspace or treat grep/read validation as the canonical check for text artifacts.

## 2026-06-07 UTC — npm emits unknown http-proxy config warning
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: PR #7 Codex review follow-up for hosted auth proxy IP header precedence
- **Category**: config
- **Blocked current task**: no
- **What happened**: Frontend npm script execution emitted a warning before running the targeted Vitest suite. The tests still passed, and the warning did not affect the review fix.
- **Evidence**: `npm run test:run -- __tests__/auth-proxy-route.test.ts` output included `npm warn Unknown env config "http-proxy". This will stop working in the next major version of npm.`
- **Seen again**: 2026-06-08 04:27-04:28 UTC during PR hosted-auth-fixes review comment follow-up for hiding hosted email until runtime config resolves; `npx prettier`, `npx eslint`, `npm run test:run -- auth-landing.test.tsx`, and `npm run type-check` emitted the same `npm warn Unknown env config "http-proxy"...` warning before continuing.
- **Likely cause**: The host or project npm environment includes a legacy `http-proxy` config key that current npm accepts with a warning but plans to reject in a future major version (confidence 80%).
- **Suggested action**: Inspect npm config sources (`npm config list`) and remove or rename the legacy `http-proxy` setting if it is not required by the container/network environment.
- **Seen again**: 2026-06-08 during PR #21 Studio image API retirement follow-up; `npm ci` and `npm run type-check` emitted `npm warn Unknown env config "http-proxy"`.

## 2026-06-07 UTC — Backend basedpyright failed on fake Redis test cast
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR hosted-identity follow-up for failing backend gates
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: The backend type gate failed because a test cast a `FakeRedis` instance to `ArqRedis` and then accessed fake-only `script` and `store` attributes through the casted variable. The production code was unaffected, but the strict `basedpyright --level error` gate rejected the test.
- **Evidence**: `uv run basedpyright --level error` reported `tests/test_identity_rate_limiter.py:615:29 - error: Cannot access attribute "script" for class "ArqRedis"` and `tests/test_identity_rate_limiter.py:621:43 - error: Cannot access attribute "store" for class "ArqRedis"`.
- **Likely cause**: The regression test needed an `ArqRedis`-typed value for `RateLimiter`, but reused that typed variable for fake-specific assertions instead of keeping the concrete fake object for inspection. Confidence: 98%.
- **Suggested action**: Keep future Redis fakes as concrete variables for fake-only assertions and pass a separately cast value only across the production API boundary.

## [2026-06-08 01:41 UTC] — Local edit script quoting error
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: PR review follow-up for multiline `extend-exclude` guard
- **Category**: other
- **Blocked current task**: no
- **What happened**: A one-off Python edit script failed before modifying files because nested triple-quoted strings produced invalid Python syntax.
- **Evidence**: `SyntaxError: unexpected character after line continuation character` from the inline `python - <<'PY'` command while constructing the multiline TOML regression test.
- **Likely cause**: Agent-authored shell helper used conflicting quote delimiters in generated Python source (95% confidence).
- **Suggested action**: No project action needed; corrected by rerunning the edit with distinct quote delimiters.

## [2026-06-08 01:41 UTC] — basedpyright baseline rewrote deleted-file diagnostics
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: PR review follow-up for multiline `extend-exclude` guard
- **Category**: config
- **Blocked current task**: no
- **What happened**: Running `uv run basedpyright --level error tests/test_test_files_parse.py` rewrote `.basedpyright/baseline.json`, removing 405 lines of diagnostics for the already-deleted `tests/test_video_e2e.py` even though this follow-up only changes the parse guard test.
- **Evidence**: Command output: `updated ./.basedpyright/baseline.json with 520 errors (went down by 51)`. `git diff --stat` showed `.basedpyright/baseline.json | 405 -----------------------------------------`.
- **Likely cause**: basedpyright refreshes the configured baseline opportunistically when invoked, and the original PR deleted a file that still had baseline entries (90% confidence).
- **Suggested action**: Decide separately whether baseline cleanup belongs in the original deletion PR; this follow-up reverted the unrelated baseline mutation.

## 2026-06-08 UTC — Backend Ruff format gate reports pre-existing formatting debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #107 review-comment follow-up — register auth config router
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: The backend lint command passed, but the full repository `uv run ruff format --check .` gate stopped the chained verification before basedpyright because two files outside this change need formatting. The touched router registration file was not reported.
- **Evidence**: `uv run ruff check . && uv run ruff format --check . && uv run basedpyright --level error` printed `All checks passed!` then `Would reformat: orchestrator/routes/video_credits.py` and `Would reformat: tests/test_orchestrator_legacy_image_gen.py`; command exit code 1.
- **Likely cause**: Pre-existing formatting drift in unrelated files that were not part of this review-comment fix (confidence 92%).
- **Suggested action**: Run a separate mechanical formatting remediation for the reported files, or establish a backend formatting baseline if those files are intentionally deferred.

## 2026-06-08 UTC — Backend basedpyright gate reports unrelated video credits test type debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: PR #107 review-comment follow-up — register auth config router
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: The full backend `basedpyright --level error` gate failed on an existing test type error outside the auth router registration change.
- **Evidence**: `uv run basedpyright --level error` reported `tests/test_video_credits_grant_bounds.py:89:42 - error: Argument of type "None" cannot be assigned to parameter "admin_key" of type "str" in function "_settings"` and exited 1.
- **Likely cause**: A pre-existing test helper type annotation does not accept `None` for a call path that intentionally passes `None` (confidence 95%).
- **Suggested action**: Fix the helper annotation or call site in a dedicated video credits typing cleanup so the backend type gate can pass again.

## 2026-06-10 05:22 UTC — uv cache path is not writable in sandbox
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Issue #109 backend pytest collection restoration
- **Category**: config
- **Blocked current task**: no
- **What happened**: The recommended backend sync command failed inside the sandbox because uv tried to use the default cache under `/home/sol/.cache/uv`, which is outside the writable workspace roots. Rerunning backend commands with `UV_CACHE_DIR=/tmp/uv-cache` avoided the host permission issue.
- **Evidence**: `UV_PROJECT_ENVIRONMENT=.uv-venv uv sync --locked` failed with `error: failed to create directory '/home/sol/.cache/uv': Permission denied (os error 13)`.
- **Likely cause**: The managed workspace sandbox allows writes to `/home/sol/daemon` and `/tmp`, but not to the host-level uv cache directory (confidence 98%).
- **Suggested action**: Use `UV_CACHE_DIR=/tmp/uv-cache` for sandboxed backend gate commands, or configure a project-local uv cache for agent runs.

## 2026-06-10 05:22 UTC — Full pytest run produced no terminal result after early failures
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: Issue #109 backend pytest collection restoration
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: A full `pytest -q` attempt printed early failures/errors and then produced no further output for several minutes. A narrower `pytest -x` pass was used afterward to surface actionable blockers one at a time.
- **Evidence**: `PYTHONPATH=. UV_CACHE_DIR=/tmp/uv-cache UV_PROJECT_ENVIRONMENT=.uv-venv uv run pytest -q` printed `F...EEE...` and then did not return useful failure details before the session became non-interactive.
- **Likely cause**: One of the early suite failures likely entered a slow wait or live-service path before pytest could summarize the run (confidence 70%).
- **Suggested action**: Continue using `pytest -x` to identify the first concrete blocker, then rerun the full suite after DB-bound smoke tests are environment-gated.

## 2026-06-10 05:22 UTC — Retrieval log smoke test required live database socket
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #109 backend pytest collection restoration
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: The full backend suite reached a LongMemEval retrieval smoke test that attempted to create a real asyncpg pool even though the audit says Postgres is not running by default. The test now skips when the database socket is unavailable and still runs when a configured database can be reached.
- **Evidence**: `tests/benchmark_longmemeval/test_retrieval_log_smoke.py::test_benchmark_retrieval_path_persists_one_retrieval_log_row` failed in `asyncpg.create_pool(...)` with `PermissionError: [Errno 1] Operation not permitted`.
- **Likely cause**: The test is an integration smoke test, not a hermetic unit test, and it lacked a skip path for socket-denied/no-Postgres local environments (confidence 95%).
- **Suggested action**: Keep DB-bound smoke tests explicitly environment-gated, and run them in CI lanes that provision Postgres.

## 2026-06-10 08:47 UTC — FastAPI TestClient route tests stalled under Python 3.14
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #109 backend pytest collection/runtime restoration
- **Category**: test-failure
- **Blocked current task**: yes
- **What happened**: Several route-level tests using `fastapi.testclient.TestClient` or sync dependency overrides stopped producing output and did not complete reliably under the current Python 3.14/pytest-asyncio strict environment. Converting those tests to direct `httpx.ASGITransport` calls with async dependency overrides made them deterministic.
- **Evidence**: Hangs occurred at `tests/test_skill_api_contracts.py`, `tests/test_skill_ui_metadata.py::TestSkillUIBadgeRendering::test_system_skill_badge_metadata`, and `tests/test_video_credits_grant_bounds.py::test_grant_rejects_zero_amount`; after conversion the focused files passed (`37 passed`, `23 passed`, and `8 passed` respectively).
- **Likely cause**: Starlette/FastAPI sync test portals and sync overrides interact poorly with this Python 3.14 async test environment, especially when the route dependency graph uses async auth/app-state dependencies (confidence 85%).
- **Suggested action**: Prefer `httpx.ASGITransport` plus async overrides for new backend route tests, and migrate remaining `TestClient` tests if they become flaky.

## 2026-06-10 08:47 UTC — basedpyright config pointed at broken `.venv`
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #109 backend gate restoration
- **Category**: config
- **Blocked current task**: yes
- **What happened**: `basedpyright --level error` reported missing imports for packages that were present in `.uv-venv` and importable at runtime. Verbose resolver output showed basedpyright reading `/home/sol/daemon/.venv/lib/python3.14/site-packages`, matching the known broken root-owned environment rather than the audit-required `.uv-venv`.
- **Evidence**: `basedpyright --verbose orchestrator/services/fetch/extract.py` printed search paths under `/home/sol/daemon/.venv/...` and `Import "trafilatura" could not be resolved`; `UV_CACHE_DIR=/tmp/uv-cache UV_PROJECT_ENVIRONMENT=.uv-venv uv run python -c "import trafilatura, youtube_transcript_api, docx, fal_client"` printed `imports ok`.
- **Likely cause**: `pyrightconfig.json` and `[tool.basedpyright]` still referenced `.venv` even though the repository audit mandates `.uv-venv` for backend work (confidence 99%).
- **Suggested action**: Keep type-checker environment config aligned with the locked uv environment, and avoid recreating or relying on the root-owned `.venv`.

## 2026-06-10 08:47 UTC — pip-audit reports dependency vulnerabilities when network is available
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #109 backend local CI
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: The backend local CI wrapper reached its non-blocking `pip-audit` inventory gate. In the sandboxed backend-only run it could not resolve PyPI, but in the escalated PR-wrapper run it reached PyPI and reported dependency vulnerabilities.
- **Evidence**: Sandboxed `scripts/local_ci.sh backend` printed `Failed to resolve 'pypi.org' ([Errno -2] Name or service not known)`. Escalated `scripts/pr_create.sh` printed `Found 43 known vulnerabilities in 14 packages`, including `aiohttp 3.13.3`, `cryptography 46.0.5`, `litellm 1.81.1`, `starlette 0.50.0`, and `urllib3 2.6.3`.
- **Likely cause**: The dependency lock contains packages with known advisories, and `pip-audit` is intentionally configured as inventory / continue-on-error for legacy debt (confidence 95%).
- **Suggested action**: File a dependency-upgrade/security follow-up that updates the affected packages through the locked dependency workflow; do not hand-edit the lockfile.
- **Seen again**: 2026-06-12 during #24 PR-wrapper creation. Escalated `scripts/pr_create.sh` reached PyPI and again reported `Found 43 known vulnerabilities in 14 packages`; this remained inventory/non-blocking.
- **Seen again**: 2026-06-12 during #113 backend local CI. `scripts/local_ci.sh backend` reached PyPI and again reported `Found 43 known vulnerabilities in 14 packages`; this remained inventory/non-blocking.
- **Seen again**: 2026-06-12 during #54 backend local CI. `scripts/local_ci.sh backend` reached PyPI and again reported `Found 43 known vulnerabilities in 14 packages`; this remained inventory/non-blocking.
- **Seen again**: 2026-06-14 during #45 PR-wrapper creation. Escalated `scripts/pr_create.sh` reached PyPI and again reported `Found 43 known vulnerabilities in 14 packages`; this remained inventory/non-blocking.
- **Seen again with changed count**: 2026-06-23 during #108 PR-wrapper backend inventory. `scripts/pr_create.sh` reached PyPI and reported `Found 69 known vulnerabilities in 16 packages`; this remained inventory/non-blocking.

## 2026-06-10 08:47 UTC — Backend inventory gates report existing security and warning debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #109 backend local CI
- **Category**: security
- **Blocked current task**: no
- **What happened**: `scripts/local_ci.sh backend` passed all blocking gates but reported the configured inventory gates as non-blocking. Bandit found existing low/medium issues across production and test code, and pytest completed with 95 warnings including third-party deprecations and several unawaited `AsyncMock` runtime warnings.
- **Evidence**: Local CI summary: `blocking failures: 0`, `Inventory reports (non-blocking): backend/bandit (exit=1), backend/pip-audit (exit=1)`, `PASS All blocking gates passed`. Full pytest output: `1838 passed, 4 skipped, 95 warnings`.
- **Likely cause**: The repository intentionally treats Bandit and pip-audit as inventory for legacy debt, and the test suite still has warning debt from third-party deprecations plus some mock shape mismatches (confidence 90%).
- **Suggested action**: Track Bandit findings and pytest warnings in dedicated cleanup issues; keep them non-blocking until the inventory baseline is actively ratcheted.
- **Seen again**: 2026-06-23 during #108 PR-wrapper backend inventory. Backend blocking gates passed, while inventory reported `bandit (exit=1)`, `pip-audit (exit=1)`, and full `pytest (exit=1)` with known auth-scoping setup errors plus the Google route duplicate-IP failure.

## 2026-06-10 08:47 UTC — PR wrapper blocked by unrelated frontend gates
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #109 PR creation
- **Category**: build-error
- **Blocked current task**: yes
- **What happened**: `scripts/pr_create.sh` runs full `scripts/local_ci.sh` without an affected-family selector, so the backend-only #109 PR could not be created through the wrapper while Wave 0 frontend debt remains. Backend and aggregate blocking gates passed; frontend `type-check`, `lint`, and `format-check` failed.
- **Evidence**: Wrapper summary: `blocking failures: 3` with `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`. Type-check evidence included missing advisor event exports from `frontend/lib/events`; lint reported 55 errors / 13 warnings; format-check reported style drift in 274 frontend files and generated `.next_broken` artifacts.
- **Likely cause**: The wrapper enforces all families even though the issue sequence calls for affected-family local CI and explicitly notes frontend Wave 0 breakage for #108 (confidence 98%).
- **Suggested action**: Either allow `scripts/pr_create.sh` to accept a local-CI family selector for backend-only PRs, or complete #108 before using the all-family wrapper path.
- **Seen again**: 2026-06-12 during #24 PR creation. Backend blocking gates and aggregate gates passed inside the wrapper, but `scripts/pr_create.sh` refused to call `gh pr create` because unrelated frontend blocking gates failed: `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`.
- **Seen again**: 2026-06-14 during #45 PR creation. Backend blocking gates and aggregate gates passed inside the wrapper, but `scripts/pr_create.sh` refused to call `gh pr create` because unrelated frontend blocking gates failed: `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`.
- **Seen again**: 2026-06-23 during #108 PR creation. Backend blocking gates, frontend `type-check`, frontend `test-run`, frontend `build`, and aggregate gates passed inside the wrapper, but `scripts/pr_create.sh` refused to call `gh pr create` because unrelated frontend blocking gates failed: `frontend/lint (exit=1)` and `frontend/format-check (exit=1)`.

## 2026-06-12 10:56 UTC — Frontend test dependencies unavailable in isolated worktree
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Issue #26 logout session revocation
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: The issue worktree did not have `frontend/node_modules`, so the focused Vitest command could not find the test binary until the worktree reused the already-installed root dependency tree.
- **Evidence**: `npm run test:run -- auth.test.ts auth-provider.test.tsx` initially failed with `sh: line 1: vitest: command not found`; after adding an untracked `frontend/node_modules -> /home/sol/daemon/frontend/node_modules` symlink, the same command passed with `53 passed`.
- **Likely cause**: Git worktrees do not share untracked dependency directories by default, and `npm ci` was not rerun in the temporary worktree (confidence 95%).
- **Suggested action**: Either install frontend dependencies per worktree with `npm ci` or document the local symlink approach for agent worktrees.

## 2026-06-12 10:56 UTC — Frontend type-check still reports advisor event contract debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #26 logout session revocation
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Full frontend type-check failed on existing advisor event and tool-call event typing errors outside the logout files. The new logout files no longer appear in the type-check output after focused test typing fixes.
- **Evidence**: `npm run type-check` exited 2 after `next typegen && tsc --noEmit`; representative errors include `__tests__/advisor-events.test.ts(4,3): error TS2305: Module '"../lib/events"' has no exported member 'isAdvisorEndEvent'`, `lib/advisorEvents.ts(3,21): error TS2305: Module '"./events"' has no exported member 'isAdvisorEvent'`, and `__tests__/tool-call-log.test.ts(15,9): error TS2353: Object literal may only specify known properties, and 'tool_call_id' does not exist`.
- **Likely cause**: Advisor/tool-call tests and helpers expect SSE event union members and metadata fields that are not present in `frontend/lib/events.ts` on this branch (confidence 90%).
- **Suggested action**: Resolve the advisor event contract in the dedicated frontend Wave 0/event-schema follow-up; do not broaden issue #26 beyond logout.

## 2026-06-12 10:56 UTC — Frontend lint and format gates have broad pre-existing debt
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #26 logout session revocation
- **Category**: build-error
- **Blocked current task**: no
- **What happened**: Full frontend lint and Prettier checks failed across unrelated pages/components. Changed-file ESLint and Prettier checks for the logout files passed.
- **Evidence**: `npm run lint` exited 1 with 41 problems, including `app/artifacts/page.tsx:108:5 react-hooks/set-state-in-effect`, `app/studio/components/ImageLightbox.tsx:19:42 react-hooks/rules-of-hooks`, and `components/TextToSpeechButton.tsx:39:25 react-hooks/rules-of-hooks`. `npm run format:check` reported `Code style issues found in 125 files`. `npm exec eslint -- __tests__/auth.test.ts __tests__/auth-provider.test.tsx __tests__/auth-page.test.tsx components/AuthProvider.tsx lib/auth.ts --max-warnings 0` passed, and `npm exec prettier -- --check ...` passed for those same files.
- **Likely cause**: Existing frontend React Compiler lint and formatting debt predates the logout change (confidence 95%).
- **Suggested action**: Fix frontend lint/format debt in dedicated PRs or establish an explicit baseline; keep issue #26 scoped to logout behavior.
- **Seen again**: 2026-06-23 during #108 frontend local CI. `scripts/local_ci.sh frontend` passed `type-check`, `test-run`, and `build`, while blocking `frontend/lint` failed with `38 problems (27 errors, 11 warnings)` and blocking `frontend/format-check` reported style drift in `121 files`; changed-file ESLint/Prettier checks for #108 files passed.

## 2026-06-12 10:56 UTC — Auth frontend tests emit existing act/navigation warnings
- **Severity**: info
- **Scope**: project
- **Encountered during**: Issue #26 logout session revocation
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Focused auth frontend tests pass, but stderr still contains existing React `act(...)` warnings and a jsdom navigation-not-implemented warning.
- **Evidence**: `npm run test:run -- auth.test.ts auth-provider.test.tsx` passed with `53 passed`; stderr included `An update to AuthProvider inside a test was not wrapped in act(...)` and `Error: Not implemented: navigation (except hash changes)` from `attemptPageLoadRefresh`.
- **Likely cause**: Existing tests assert redirect side effects around asynchronous provider updates and read-only jsdom `window.location` behavior (confidence 85%).
- **Suggested action**: Wrap provider-triggered updates in Testing Library `act` and isolate redirect assertions from jsdom's real navigation implementation in a frontend test cleanup.
- **Seen again**: 2026-06-23 during #108 frontend full Vitest verification. `npm run test:run` passed with `16 passed` test files and `212 passed` tests, but stderr again included repeated `AuthProvider` `act(...)` warnings plus the jsdom `Not implemented: navigation (except hash changes)` warning in `attemptPageLoadRefresh`.

## 2026-06-12 10:56 UTC — Temporary logout test insertion produced syntax error before correction
- **Severity**: info
- **Scope**: tooling
- **Encountered during**: Issue #26 logout session revocation
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: While adding the logout backend regression test, a new test class was inserted before an existing `finally` block, making the file temporarily unparseable. The structure was corrected before final verification.
- **Evidence**: The focused pytest run reported `SyntaxError: expected 'except' or 'finally' block` in `tests/test_refresh_flow.py`; after restoring the missing `finally: restore_init(original)`, `pytest -q tests/test_refresh_flow.py::TestLogoutCurrentSession::test_logout_revokes_current_session_clears_cookie_and_blocks_refresh` passed.
- **Likely cause**: Manual patch placement split an existing `try`/`finally` test block (confidence 99%).
- **Suggested action**: Prefer inspecting surrounding control-flow boundaries before inserting new test classes in large test modules.

## 2026-06-12 11:04 UTC — Issue #26 local CI wrappers hit existing environment and frontend debt
- **Severity**: warning
- **Scope**: project | host
- **Encountered during**: Issue #26 logout session revocation
- **Category**: build-error | dependency | test-failure
- **Blocked current task**: no
- **What happened**: Backend and aggregate local CI passed their blocking gates when rerun with `UV_PROJECT_ENVIRONMENT=/home/sol/daemon/.uv-venv UV_CACHE_DIR=/tmp/uv-cache`; backend then timed out during the non-blocking full pytest inventory phase. Frontend local CI could install dependencies outside the sandbox, but blocking type-check, lint, and format gates still failed on unrelated advisor event and repo-wide frontend debt.
- **Evidence**: Backend wrapper passed `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`; `timeout 420s scripts/local_ci.sh backend` exited 124 during inventory `PYTHONPATH=. uv run pytest -q` after progress reached `[ 91%]`. Aggregate wrapper passed `feature-matrix` and `pre-commit`. Escalated `scripts/local_ci.sh frontend` passed `npm-ci`, then failed blocking gates `frontend/type-check (exit=2)`, `frontend/lint (exit=1)`, and `frontend/format-check (exit=1)`.
- **Likely cause**: Same local backend full-suite stall and frontend baseline debt already seen in previous issue runs; issue #26 only touches logout behavior and focused logout/auth tests pass (confidence 90%).
- **Suggested action**: Keep #26 scoped to logout and rely on branch-protection CI for full hosted verification while tracking frontend advisor/lint/format cleanup separately.

## 2026-06-12 11:04 UTC — Frontend npm audit inventory increased to 27 vulnerabilities
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #26 logout session revocation
- **Category**: security
- **Blocked current task**: no
- **What happened**: The frontend local CI inventory audit reported 27 vulnerabilities, including one critical Vitest advisory. Earlier triage recorded 26 frontend npm audit vulnerabilities, so this count has changed and is logged again.
- **Evidence**: `npm --prefix frontend run audit:ci` reported `27 vulnerabilities (4 low, 8 moderate, 14 high, 1 critical)`. Representative advisories included `vitest <3.2.6` critical `GHSA-5xrq-8626-4rwp`, `next 9.3.4-canary.0 - 16.3.0-canary.5` high advisories, and vulnerable `@ai-sdk/provider-utils`.
- **Likely cause**: New upstream advisories now apply to the locked frontend dependency graph; some suggested fixes require breaking upgrades such as AI SDK, Next, or next-pwa (confidence 95%).
- **Suggested action**: Handle through the locked dependency remediation process in a dedicated security/dependency PR; do not hand-edit lockfiles in issue #26.
- **Seen again with changed count**: 2026-06-23 during #108 frontend dependency installation. `npm ci` reported `30 vulnerabilities (6 low, 8 moderate, 15 high, 1 critical)`; install succeeded and dependency remediation remains out of scope for #108.

## 2026-06-12 11:20 UTC — Issue #42 backend local CI timed out in inventory pytest
- **Severity**: warning
- **Scope**: host
- **Encountered during**: Issue #42 conversation ownership verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Backend local CI passed all blocking gates, then timed out in the non-blocking full pytest inventory phase after late-suite progress. Aggregate local CI passed.
- **Evidence**: `UV_PROJECT_ENVIRONMENT=/home/sol/daemon/.uv-venv UV_CACHE_DIR=/tmp/uv-cache timeout 420s scripts/local_ci.sh backend` passed `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`; `pip-audit` reported `Failed to resolve 'pypi.org' ([Errno -2] Name or service not known)` and inventory `PYTHONPATH=. uv run pytest -q` reached `[ 91%]` before the outer timeout exited 124. `timeout 180s scripts/local_ci.sh aggregate` passed `feature-matrix` and `pre-commit`.
- **Likely cause**: Same sandbox network restriction and recurring late-suite local pytest inventory stall seen in previous issue worktrees (confidence 90%).
- **Suggested action**: Treat hosted protected CI as the authoritative full-suite result while investigating the local late-suite inventory stall separately.
- **Seen again**: 2026-06-12 during #113 refresh-rotation grace verification. `timeout 420s scripts/local_ci.sh backend` passed blocking `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`; inventory full pytest reached late-suite progress after showing unrelated failures and then the outer timeout exited `124`.
- **Seen again**: 2026-06-12 during #54 session cleanup grace-days verification. `timeout 420s scripts/local_ci.sh backend` passed blocking `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`; inventory `bandit` and `pip-audit` reported existing non-blocking findings, inventory full pytest printed progress through `[ 91%]` with one `F` marker but no failure summary before the outer timeout exited `124`.
- **Seen again**: 2026-06-13 during #56 session cleanup / refresh serialization verification. `timeout 420s scripts/local_ci.sh backend` passed blocking `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`; inventory `pip-audit` failed DNS resolution for `pypi.org`, full pytest printed the known `tests/test_auth_user_scoping.py` setup errors plus one `F` marker, then reached late-suite progress before the outer timeout exited `124`.
- **Seen again**: 2026-06-14 during #45 council read-only tool registry verification. `timeout 420s scripts/local_ci.sh backend` passed blocking `ruff-check`, `ruff-format`, `basedpyright`, and `pytest-collect`; inventory `bandit` reported existing low/medium findings, `pip-audit` failed DNS resolution for `pypi.org`, and full pytest printed known auth-scoping setup errors plus a late-suite `F` marker before reaching `[ 90%]` and exiting `124`.

## 2026-06-12 11:20 UTC — Existing auth user scoping fixture fails development pepper setup
- **Severity**: warning
- **Scope**: project
- **Encountered during**: Issue #42 conversation ownership verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: A neighboring auth scoping test fixture failed during FastAPI lifespan setup because its mocked `db_pool.acquire()` does not implement the async context manager protocol required by development pepper initialization. The focused #42 chat-history suite passed.
- **Evidence**: `PYTHONPATH=. uv run pytest -q tests/test_chat_history.py tests/test_auth_user_scoping.py::test_chat_persistence_ignores_payload_user_id` ended with `TypeError: 'coroutine' object does not support the asynchronous context manager protocol (missed __aexit__ method)` at `orchestrator/auth_runtime_state.py:97` in `ensure_development_pepper_in_db`; the same command showed `8 passed` for `tests/test_chat_history.py` before the fixture error.
- **Likely cause**: `tests/test_auth_user_scoping.py` builds `app_state.db_pool` as a plain `AsyncMock`, but the post-setup-token runtime now expects `db_pool.acquire()` to be usable in `async with` (confidence 95%).
- **Suggested action**: Update that fixture to use the existing mock async context manager pattern from setup/auth runtime tests in a dedicated cleanup.
- **Seen again**: 2026-06-12 during #24 PR-wrapper backend inventory. Full inventory pytest completed and surfaced the same `TypeError: 'coroutine' object does not support the asynchronous context manager protocol` in `tests/test_auth_user_scoping.py` setup; #24 focused enrollment tests were unaffected.
- **Seen again**: 2026-06-13 during #56 backend local CI inventory. Full pytest again surfaced the same `TypeError: 'coroutine' object does not support the asynchronous context manager protocol (missed __aexit__ method)` at `orchestrator/auth_runtime_state.py:97` in `tests/test_auth_user_scoping.py` setup; #56 focused session cleanup / refresh tests were unaffected.
- **Seen again**: 2026-06-14 during #45 PR-wrapper backend inventory. Full pytest again surfaced the same `TypeError: 'coroutine' object does not support the asynchronous context manager protocol (missed __aexit__ method)` at `orchestrator/auth_runtime_state.py:97` in `tests/test_auth_user_scoping.py` setup; #45 focused council tool-registry tests were unaffected.
- **Seen again**: 2026-06-23 during #108 PR-wrapper backend inventory. Full pytest again surfaced 5 setup errors in `tests/test_auth_user_scoping.py`, all rooted in `TypeError: 'coroutine' object does not support the asynchronous context manager protocol` at `orchestrator/auth_runtime_state.py:97`; #108 frontend advisor tests were unaffected.

## 2026-06-12 11:20 UTC — Chat history tests still emit existing AsyncMock warning debt
- **Severity**: info
- **Scope**: project
- **Encountered during**: Issue #42 conversation ownership verification
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The focused chat-history suite passes but emits existing unawaited `AsyncMock` runtime warnings from preference formatting paths.
- **Evidence**: `PYTHONPATH=. uv run pytest -q tests/test_chat_history.py` passed with `8 passed, 33 warnings`; representative warnings include `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited` from `orchestrator/memory/injection.py:134`, `orchestrator/memory/injection.py:136`, and `orchestrator/main.py:1804`.
- **Likely cause**: The test file's generic `AsyncMock` store exposes async mock attributes for user-settings reads that are consumed as dict-like values by preference formatting (confidence 85%).
- **Suggested action**: Normalize chat-history mock stores with explicit `get_user_settings = AsyncMock(return_value={})` in a separate warning cleanup if warning-free focused runs become required.
