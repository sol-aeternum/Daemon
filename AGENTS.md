# Daemon — Agent Instructions

## What This Is
Daemon is one personal AI for everything: the best qualified model per role, with user-owned, portable context. Read `docs/DAEMON.md` first and use `docs/GLOSSARY.md` terms. Product identity and ratified decisions govern direction; code and gated documentation govern implementation status.

## Before You Touch Anything
1. `docs/FEATURE_MATRIX.md` (implemented/planned status) and `docs/PROJECT_CONTEXT.md` (regenerated context).
2. `docs/SOURCES_OF_TRUTH.md` — which document is authoritative for what.
3. Memory work: also `MEMORY_LAYER.md` and `docs/TECHNICAL_SPECS.md`.
4. Product changes: also `docs/DAEMON_VISION.md` and `docs/DAEMON_VISION_DECISIONS.md`.
5. Recent commits and code comments for current state. When this file disagrees with code, code wins — fix this file.

## Product Direction
- Preserve the Vision / Approved product decision / Proposed / Open distinctions, and cite V/D/AC/DEC IDs.
- DEC11–DEC12 are ratified. "Spine before surface" and build order are still Proposed.
- Inference stays zero-data-retention only (Z routes). DEC12 permits opt-in provider-retained (R) routes in principle, but none may execute until their implementation design is approved.
- `docs/DAEMON_RECONCILIATION.md` is dated evidence, not current release status.
- Product approval does not authorise unrelated rewrites, deployments, expanded permissions, or unapproved schema/API changes.

## Rules of Engagement
- **Ask before making design decisions.** If a task has multiple valid approaches, present options with tradeoffs. Do not pick one autonomously. In an unattended run where you cannot ask, take the most reversible option and flag the decision in the PR description.
- **Clarify ambiguity, don't assume.** Wrong assumptions cost more than a question.
- **No silent architecture changes.** Data models, API contracts, SSE event types, and plan/entitlement config change only with explicit approval.
- **Update docs with code.** Close (or comment the resolution on) the GitHub issue a change fixes, in the same change. Completing a `docs/ROADMAP.md` item means updating it.
- **Don't add dependencies without asking.** Especially frontend — bundle size matters for the PWA.
- **Done means the gates pass** — see below.

## Definition of Done — Quality Gates
No change is complete until these pass. Run them locally; CI runs the same commands (`.github/workflows/ci.yml`). Existing debt is grandfathered only through dedicated baselines (e.g. `.basedpyright/baseline.json`) or a pinned tracking issue — ratchet, never loosen.

**Backend** (repo root):
- `uv run ruff check .` — lint
- `uv run ruff format --check .` — formatting
- `uv run basedpyright --level error` — type check; new errors must be clean
- `uv run bandit -r orchestrator providers scripts tests -lll` — blocking high-severity SAST
- `uv run bandit -r orchestrator providers scripts tests` — full SAST inventory (non-blocking)
- `uv run pip-audit` — blocking dependency audit
- `PYTHONPATH=. uv run pytest -q` — tests

**Frontend** (`frontend/`):
- `npm ci`
- `npm run security:braces` — vendored `braces` integrity and regressions (`docs/BRACES_SECURITY_MAINTENANCE.md`)
- `npm run type-check` — Next 16 `build` does not type-check
- `npm run lint` — eslint (`next lint` was removed in Next 16)
- `npm run format:check`
- `npm run audit:ci` — blocking dependency audit
- `npm run test:run` — Vitest
- `npm run test:browser` — Playwright regressions (non-blocking inventory)
- `npm run build`

**Repo-wide:**
- `python scripts/lint_feature_matrix.py` — feature-matrix validation
- `uv run pre-commit run --all-files` — doc freshness (`scripts/check_doc_freshness.py --mode fail`), ruff, gitleaks; commitlint runs as a commit-msg hook

Tool versions live in `pyproject.toml`, `frontend/package.json`, and the lockfiles. Do not restate them elsewhere, including here. Gate config: `.pre-commit-config.yaml`, `.github/workflows/`, `.github/dependabot.yml`, `renovate.json`.

## Local CI, PRs and Merging
- `scripts/local_ci.sh [backend|frontend|aggregate] [--list]` runs the gate families above. Blocking gates fail the script; inventory gates are reported only.
- `scripts/pr_create.sh -- <gh pr create args>` replaces `gh pr create`: it refuses to open a PR until `local_ci.sh` exits 0. `--dry-run` shows the plan without running anything.
- `main` is protected by the `Main Protection` ruleset. Required checks: `Backend gates`, `Frontend gates`, `Feature matrix gate`, `Pre-commit and secret scanning`.
- **Merge only after review, not just green CI.** Wait for the Codex review on the current head SHA (and any `[agent]` source review), and resolve its findings first. A requested-but-missing review means the PR is waiting, not mergeable.
- Never merge around a failed required check. If a check is stale, missing, or misconfigured, fix the workflow/ruleset or record the blocker.

## Conventions
- Entry points: `orchestrator/main.py` (FastAPI app), `orchestrator/daemon.py` (orchestration loop), `orchestrator/memory/` (memory pipeline), `orchestrator/worker/` (arq jobs), `frontend/app/page.tsx` (chat UI), `frontend/app/api/chat/route.ts` (backend SSE → AI SDK bridge). Explore the tree for the rest.
- Backend uses `asyncpg` directly — no ORM, raw SQL.
- Message and memory content is Fernet-encrypted at rest; embeddings are plaintext for pgvector.
- SSE event types are a typed API contract defined in `frontend/lib/events.ts`. Adding or changing one needs approval.
- Commercial plans resolve centrally into entitlements and compute budgets; provider qualification is independent of plans (`docs/SUBSCRIPTION_ARCHITECTURE.md`). Don't hardcode model strings or plan checks in execution logic.
- Tests: pytest + pytest-asyncio (backend), Vitest + Playwright (frontend). New backend code ships with tests; new frontend behaviour ships with at least a smoke test.
- **Conventional Commits** (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, …), enforced by commitlint.
- **Reproducible installs only:** `uv sync --locked` / `npm ci`. Never `pip install` ad hoc or hand-edit a lockfile.

## What NOT to Do
- Don't add Open WebUI or OpenCode Zen integrations or references — both are legacy and being removed. Existing Open WebUI compatibility endpoints in `orchestrator/main.py` stay untouched unless the task is removing them.
- Don't use `gpt-4o` (or any fixed model) as a default route — routing is privacy-qualified and capability-aware.
- Don't put secrets in code or docs. Everything goes through env vars; commit `.env.example`, never `.env`. gitleaks runs in pre-commit and CI.
- **Keep the env surface in sync in the same commit.** Adding, renaming, removing, or re-scoping an environment variable updates every surface it touches in one commit. `tests/test_env_surface_parity.py` enforces this and owns the allowlists (`SERVICE_ROLE_EXCEPTIONS`, `LEGACY_DOTENV_ONLY`, consumer attributions). The rules it can't check for you:
  - A shared backend/worker setting is documented once in `.env.example` and injected into **both** `backend` and `worker` in `docker-compose.yml`. A per-service exclusion needs a written reason in the PR.
  - A frontend-only, Compose-only, or script-only variable is documented once and wired to its own surface only.
  - Every declaration has an identified consumer, and a commented line still counts as a declaration. Exemptions go through the test's enumerated allowlist, never a prefix wildcard.
  - **Renames and removals** also need a `MIGRATION:` line in the PR description and a section in `docs/ENV_SURFACE_MIGRATION.md` in the same PR. It must list the exact production keys to add, change, or drop, the fallback behaviour, and any case where absence differs from an empty value. Never recommend deleting a variable that still has a consumer.
- Don't create new Docker services without discussing architecture impact.
- **Don't weaken a gate to make CI pass.** Surface blocking debt for a decision; don't silently loosen `ruff`, `basedpyright`, or `tsconfig` config.
- **Don't regenerate or reflow config/doc files.** Edits to `pyproject.toml`, `package.json`, `tsconfig.json`, `*.yml`, READMEs, and `AGENTS.md` are surgical — change the relevant lines only.

## Feature Matrix
`docs/FEATURE_MATRIX.md` records every user-visible feature's state per client surface. It is scope control, not documentation, and CI validates it.
- New user-visible feature → add a row.
- Promoting a feature on any surface (e.g. `Not started` → `Mobile eligible`) → update the cell.
- Retiring or platform-restricting a feature → update or remove the row, with justification in the PR.

Internal infrastructure (dedup thresholds, embedding model choice, retrieval scoring) is out of scope.

## Anomaly Reporting Protocol
Note errors, warnings, failures, and unexpected behaviour during a task — especially outside its scope. Don't keep an in-repo log. Route by scope and severity:
- scope `project`/`upstream` and severity `critical`/`warning` → GitHub issue
- scope `host`/`tooling` → append to `.triage.local.md` (gitignored, never committed)
- severity `info` → don't record. When in doubt, it is info.

**Filing:** batch at task completion.
1. Search: `gh issue list --label triage --search "<keywords>" --state open`
2. Match → `gh issue comment <n> --body "[agent] <new evidence>"`
3. No match → `gh issue create --title "[triage][<category>] <title>" --label triage --label agent-filed --label severity:<critical|warning> --label scope:<project|upstream> --body "<template>"`

File only: don't assign, prioritise, close, or fix triaged items unless they block the current task. If `gh` is unavailable, record the finding in `.triage.local.md`, surface it in your completion report, and file it once `gh` works.

**Issue body template:**
- Severity: critical | warning
- Scope: project | upstream
- Category: build-error | runtime-error | deprecation | config | test-failure | dependency | security | other
- Encountered during: <task / issue #>
- Blocked current task: yes | no
- What happened: <1–3 sentences>
- Evidence: <exact output, file:line>
- Likely cause: <assessment + confidence %>
- Suggested action: <what to investigate>

**Completion report:** `Anomalies: {N} filed/updated ({critical} crit, {warning} warn) — issues [#…]; {M} host/tooling → local. "Clean" if none.`

## Review guidelines

### Mandatory review completion signal

When reviewing a pull request, Codex must always leave a top-level GitHub PR review comment, even if no issues are found.

If findings are found:

* Leave inline comments where appropriate.
* Also leave a top-level summary comment with:

  * Review status: `Findings`
  * Number of findings
  * Highest severity
  * Areas reviewed
  * Any tests or checks inspected

If no findings are found:

* Do not invent issues.
* Still leave a top-level comment using this exact structure:

```markdown
## Codex PR Review

Review status: No findings

I reviewed this pull request and found no blocking or high-priority issues.

Scope reviewed:
- Correctness/regression risk
- Security/auth/data-handling risk
- Test coverage impact
- Documentation/config impact
- Obvious maintainability risks

Notes:
- No merge action taken.
- Human final review is still required.
```
Codex must not treat "no findings" as permission to remain silent. A visible review comment is required so downstream reviewers and agents can confirm that the PR was actually pre-reviewed.
