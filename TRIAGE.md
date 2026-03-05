# Triage Log

> Auto-generated diagnostic capture. Items here were encountered during task
> execution but fall outside the immediate task scope. Review and action as needed.

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
