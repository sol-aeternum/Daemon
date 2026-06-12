# Daemon — Agent Instructions

## What This Is
Personal multi-agent AI assistant. FastAPI backend orchestrates LLM calls via OpenRouter, spawning subagents (@research, @image, @audio, @code, @reader). Next.js 16 frontend with Vercel AI SDK. PostgreSQL + pgvector for memory. Redis + arq for background jobs.

## Before You Touch Anything
1. Read `docs/CURRENT_ISSUES.md` — know what's broken before changing things
2. Read `docs/FEATURE_MATRIX.md` (implemented/planned status) and `docs/PROJECT_CONTEXT.md` (regenerated context)
3. If the task touches memory: read `MEMORY_LAYER.md` and `docs/TECHNICAL_SPECS.md`
4. Read `docs/SOURCES_OF_TRUTH.md` — documentation authority map
5. Check recent commits and code comments for context on current state

## Rules of Engagement
- **Ask before making design decisions.** If a task has multiple valid approaches, present options with tradeoffs. Do not pick one autonomously.
- **Clarify ambiguity, don't assume.** If the spec is unclear, ask. Wrong assumptions cost more than a question.
- **No silent architecture changes.** Changing data models, API contracts, SSE event types, or tier config requires explicit approval.
- **Update docs with code.** If you fix a bug in CURRENT_ISSUES.md or complete a ROADMAP.md item, update the doc in the same change.
- **Don't add dependencies without asking.** Especially frontend — bundle size matters for PWA.
- **Done means the gates pass.** No task is complete until it satisfies the Definition of Done below.

## Quality Gates (Definition of Done)
No task is complete until it passes the project's automated gates. Run them before claiming done. Existing debt may be recorded as blocker inventory in `TRIAGE.md` or dedicated baseline files (e.g., `.basedpyright/baseline.json`); do not weaken or remove gates.

### Backend Gates
- **Lint**: `uv run ruff check .`
- **Format**: `uv run ruff format --check .`
- **Type Check**: `uv run basedpyright --level error`
- **Security (SAST)**: `uv run bandit -r orchestrator providers scripts tests`
- **Security (SCA)**: `uv run pip-audit`
- **Tests**: `PYTHONPATH=. uv run pytest -q`

### Frontend Gates (run from `frontend/`)
- **Install**: `npm ci`
- **Type Check**: `npm run type-check`
- **Lint**: `npm run lint`
- **Format**: `npm run format:check`
- **Security (SCA)**: `npm run audit:ci`
- **Tests**: `npm run test:run`
- **Build**: `npm run build`

### Aggregate & Infrastructure Gates
- **Feature Matrix**: `python scripts/lint_feature_matrix.py`
- **Pre-commit**: `uv run pre-commit run --all-files` (includes gitleaks; commitlint runs as a commit-msg hook)

## Tech Stack
- **Backend:** Python 3.11+, FastAPI, LiteLLM, asyncpg, arq, cryptography (Fernet)
- **Frontend:** Next.js 16, React 19, Vercel AI SDK 4, Tailwind CSS 3, lucide-react
- **Infra:** Docker Compose — backend, worker, frontend, postgres (pgvector), redis
- **External:** OpenRouter (LLMs), Voyage AI (embeddings), OpenAI (Sora), Brave Search, ElevenLabs, ntfy.sh

## Structure
```
orchestrator/           # FastAPI backend
  main.py               # Routes, SSE streaming, chat endpoint
  daemon.py             # Core orchestration loop (stream_sse_chat)
  config.py             # Tier system, env-var model slots
  prompts.py            # System prompt (v1)
  memory/               # Full memory pipeline
    store.py            # PostgreSQL CRUD (973 lines)
    extraction.py       # Fact extraction
    dedup.py            # Embedding similarity dedup
    retrieval.py        # Composite scoring retrieval
    injection.py        # System prompt assembly with memory context
    embedding.py        # Voyage AI asymmetric embeddings (doc/query)
    encryption.py       # Fernet encrypt/decrypt
    tools.py            # memory_read / memory_write tool implementations
  worker/               # arq background jobs
  routes/               # API route modules
  agents/               # Subagent implementations
frontend/
  app/page.tsx          # Main chat UI (ChatContent component)
  app/api/chat/route.ts # SSE bridge: backend SSE → Vercel AI SDK format
  components/           # UI components
  hooks/                # React hooks (useConversationHistory, useChat wrappers)
  lib/events.ts         # Typed SSE event definitions
docs/                   # Project documentation (keep in sync)
migrations/             # PostgreSQL migrations
```

## Conventions
- Backend uses `asyncpg` directly — no ORM. Raw SQL in store.py.
- All message/memory content encrypted at rest via Fernet. Embeddings are plaintext for pgvector.
- SSE events are typed: token, thinking, routing, tool_call, tool_result, final, error, done.
- Tier model assignments are env-var configurable. Don't hardcode model strings in logic.
- Frontend uses `useChat` from Vercel AI SDK — ErrorBoundary wraps ChatContent for crash recovery.
 - Backend tests use pytest + pytest-asyncio; frontend tests use Vitest and Playwright.
 - **Test suite is growing** (pytest + pytest-asyncio backend; Playwright planned for frontend). New backend code ships with tests; new frontend behaviour ships with at least a smoke test.
 - **Conventional Commits** for all commit messages (`feat:`, `fix:`, `chore:`, `refactor:`, ...). commitlint enforces this.
 - **Reproducible installs only.** Use the locked path (`uv sync --locked` backend / `npm ci` frontend). Never `pip install` ad hoc and never hand-edit a lockfile — let the package manager and Renovate own them.

 ## Definition of Done — Quality Gates
 No change is complete until it passes the project's automated quality gates. Run them locally before declaring a task done; once CI lands (see the `ci-tooling-baseline` plan) these run on every PR and are required to merge. Backend lives in `orchestrator/`; frontend in `frontend/`.

 **Backend (`orchestrator/`):**
 - `uv run ruff check .` — lint (autofix with `--fix`)
 - `uv run ruff format --check .` — formatting
 - `uv run basedpyright --level error` — strict error-level type check (new errors must be clean; existing diagnostics are grandfathered via the baseline — ratchet, not rewrite)
 - `uv run bandit -r .` — security static analysis
 - `PYTHONPATH=. uv run pytest -q` — tests

 **Frontend (`frontend/`):**
 - `npm run type-check` — type check (Next 16 `build` does NOT type-check; run this explicitly)
 - `npm run lint` — eslint (NOT `next lint`; removed in Next 16)
 - `npm run format:check` — formatting
 - `npm run audit:ci` — security SCA
 - `npm run test:run` — tests
 - `npm run build` — production build

 **Repo-wide:**
 - `python scripts/lint_feature_matrix.py` — feature-matrix validation (this is the CI integration the Feature Matrix section previously flagged as a follow-up)
 - `uv run pre-commit run --all-files` — pre-commit hooks (gitleaks; commitlint runs as a commit-msg hook)

 Run backend commands through the project's package manager — `uv run …` is the recommended runner. **Tool versions are pinned in the backend dependency manifest (`pyproject.toml` or equivalent), `frontend/package.json`, and the lockfiles — those files are the source of truth.** Do not restate versions anywhere else, including in this file.

 Gate config lives in: `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `.github/workflows/codeql.yml`, `renovate.json`.

## Local CI / PR Submission

Local gate runner and PR wrapper live in `scripts/`:

- `scripts/local_ci.sh [backend|frontend|aggregate] [--list]` — runs the gate families above. Blocking gates (ruff check, ruff format, basedpyright, frontend type/lint/format, feature matrix, pre-commit) fail the script; inventory gates (CI `continue-on-error`) are reported but do not block.
- `scripts/pr_create.sh --dry-run -- <gh pr create args>` — refuses to invoke `gh pr create` until `scripts/local_ci.sh` exits 0. `--dry-run` shows the plan without running gates or contacting GitHub. `scripts/pr_create.sh -- <args>` is the recommended replacement for `gh pr create`.
- `main` is protected by the GitHub `Main Protection` ruleset. Required checks are `Backend gates`, `Frontend gates`, `Feature matrix gate`, and `Pre-commit and secret scanning`.
- Do not merge around failed required checks. If a required check is stale, missing, or misconfigured, fix the workflow/ruleset or record the blocker before merging.

## Recent Fixes (as of Feb 2026)
- ✅ Memory extraction now writes `status="active"` — pipeline is fully operational
- ✅ Error boundary added to chat view for crash recovery
- ✅ Voyage embedding service added (voyage-4-large docs / voyage-4-lite queries)
- ✅ Retry detection consolidated into orchestrator/tools/retry.py with word-boundary matching
- ✅ Audio endpoint returns scoped token instead of raw API key (security fix)
- ✅ Completion streaming adds incremental content_delta for real-time output
- ✅ Frontend hooks extracted: useEventArchive, AudioPlaybackProvider, ConversationHistoryProvider
- ✅ SettingsPanel component added for user preferences
- ✅ Tests added: test_chat_history.py, test_store.py

## What NOT to Do
- Don't add Open WebUI references — it's being removed
- Don't reference OpenCode Zen provider — legacy, being removed
- Don't use `gpt-4o` as a default anywhere — backend uses tier-based auto-routing
- Don't put secrets in code or docs — everything goes through env vars; commit `.env.example` only, never `.env`. gitleaks runs in pre-commit and CI.
- Don't create new Docker services without discussing architecture impact
- **Don't weaken a gate to make CI pass.** If strictness surfaces debt that blocks you, surface it for a decision — do not loosen `ruff`/`mypy`/`tsconfig` config silently.
- **Don't hand-edit lockfiles or `pip install` ad hoc.** Dependency changes go through the package manager (and need approval, per Rules of Engagement).
- **Don't regenerate or reflow config/doc files.** Edits to `pyproject.toml`, `package.json`, `tsconfig.json`, `*.yml`, READMEs, and `AGENTS.md` are surgical — change the relevant lines only.

# Diagnostic Triage Protocol

## Mandatory: TRIAGE.md Maintenance

During ALL task execution, maintain a `TRIAGE.md` file in the project root. Log any error, warning, failure, unexpected behavior, or anomaly encountered — **especially** items outside your current task scope.

### What to log
- Build errors/warnings (even if you can work around them)
- Pre-existing bugs you stumble upon
- Test failures unrelated to your changes
- Deprecation warnings, version incompatibilities
- Configuration issues outside your task scope
- Anything you'd mentally dismiss as "not my problem"

### Entry format
```markdown
## [TIMESTAMP] — [SHORT TITLE]
- **Severity**: critical | warning | info
- **Scope**: project | host | upstream | tooling
- **Encountered during**: [current TODO/task]
- **Category**: build-error | runtime-error | deprecation | config | test-failure | dependency | security | other
- **Blocked current task**: yes | no
- **What happened**: [1-3 sentences]
- **Evidence**: [exact error output, file:line refs]
- **Likely cause**: [assessment with confidence %]
- **Suggested action**: [what to investigate]
```

### Scope definitions
- **project**: Fixable by changing project code/config/tests
- **host**: Environment/OS/container/permissions issue — not a code fix
- **upstream**: Third-party dependency issue — track, don't fix
- **tooling**: Agent/LSP/MCP/dev infrastructure — config fix, not code fix

### Rules
1. Log BEFORE marking any TODO complete
2. Include actual error messages — don't paraphrase
3. Do NOT fix triaged items unless they block your current task
4. "Pre-existing / not my fault / probably fine" = triage trigger, not dismissal
5. If it was worth noting in your thinking, it's worth logging in TRIAGE.md
6. Check TRIAGE_SUPPRESS below — skip suppressed patterns unless behavior has CHANGED
7. Check for existing entries before logging — append "seen again" to duplicates

### On task completion, always report
```
Triage: {N} issues ({critical} critical, {warning} warning, {info} info)
Scope: {project} project | {host} host | {upstream} upstream | {tooling} tooling
See TRIAGE.md — items requiring attention: [list critical/warning titles]
```
If zero issues: "Triage: clean — no anomalies encountered."

<!-- TRIAGE_SUPPRESS: Known issues — do not re-log unless behavior has CHANGED -->
<!-- Add patterns here after running triage review -->
<!-- Example: -->
<!-- - __pycache__ PermissionError in container (host ownership mismatch) -->
<!-- - LiteLLM asyncio.iscoroutinefunction DeprecationWarning on Python 3.14+ -->
<!-- END TRIAGE_SUPPRESS -->

## Feature Matrix

Daemon maintains a feature matrix at `docs/FEATURE_MATRIX.md` capturing every user-visible feature's state across each client surface. This is scope control, not documentation.

**When you must edit the matrix:**
- Adding a new user-visible feature → add a row
- Promoting a feature's state on any surface (e.g., `Not started` → `Mobile eligible`) → update the relevant cell
- Retiring or platform-restricting a feature → update cells or remove the row with justification in the PR

**Validation:** Run `python scripts/lint_feature_matrix.py` and `python scripts/check_doc_freshness.py --mode fail` before committing changes. CI integration is a separate follow-up; until then, discipline is human-enforced via PR review.

**Internal infrastructure is out of scope.** The matrix tracks user-visible capabilities only. Memory dedup thresholds, embedding model choice, retrieval scoring — none of these are matrix entries.
