# Vision integration and continuity baseline

Date: 26 September 2026. Historical integration evidence from the original working tree; PR-port evidence is recorded separately below. **No deployment or task-architecture approval**.

## Original working-tree scope

- Integrated vision v0.2, approved product decisions and a dated Stage 0 reconciliation.
- Linked product direction from README, roadmap, source-of-truth mapping and agent instructions.
- Corrected active media/voice narrative overclaims without enabling retired execution (#315).
- Prepared [durable-request architecture options](DURABLE_REQUEST_DESIGN.md); schemas, APIs and resource-lifecycle decisions remain pending.
- Fixed false completion on explicit chat disconnect (#316): incomplete rows are not promoted to complete, no fallback/final success is fabricated, and success-only follow-up jobs are skipped. This preserves the current incomplete-row convention, not durable background continuation.
- Removed obsolete frontend advisor-event consumers/tests while preserving generic tool activity coverage and council roles. Corrected an auth subscription mock signature and added required Suspense boundaries to `/auth` and `/setup` after production prerender exposed them.
- Backend baseline repair removes obsolete counter wiring and unreachable advisor tool/budget/prompt modules, preserves legacy stored traces and the council-scoped registry, restores eligible-user selection for periodic consolidation, and routes skill projection deletion through its existing service after the pending audit record.
- Separately confirmed with the product owner: restore the prior status-based memory garbage-collection implementation (inactive after 90 days; pending/rejected/deleted after 30 days; never active). Existing scheduled callers remain functional without inventing a new retention contract. No cleanup was run against user data.

Validation confirms substantial baseline improvement, but the full gate set remains blocked by the inventory below. Broad assistant continuity is still proposed architecture, not implemented capability.

## Original working-tree validation

| Check | Result |
| --- | --- |
| Feature matrix | 72 rows validated; advisor retirement recorded; no continuity capability marked implemented |
| Documentation freshness | No drift detected |
| Working-tree whitespace | `git diff --check` passed at documentation integration |
| Stable IDs and links | Local links resolved; exactly one definition for each V01–07, D01–10, AC01–12, O01–11 and DEC01–10 |
| Frontend locked install | `npm ci --no-audit --no-fund` passed |
| Frontend type check | `npm run type-check` passed after integration |
| Frontend tests | 254 passed; focused auth tests also passed after the auth boundary change |
| Frontend production build | Passed after `/auth` and `/setup` Suspense fixes; task-generated service-worker changes restored to pre-test bytes |
| Frontend scoped lint/format | Changed tests and auth/setup wrappers passed |
| Full frontend lint | Blocked: 55 errors, 13 warnings, including traversal of old local build directories |
| Full frontend format | Blocked: 270 files, including old build artifacts and existing source-format debt |
| Backend dependency audit | 157 advisories across 20 packages; dependencies unchanged (#309) |
| Frontend dependency audit | 37 vulnerabilities (7 low, 7 moderate, 21 high, 2 critical); dependencies unchanged (#311) |
| Full backend lint | Passed; unreadable host-directory warning remains |
| Full backend format | Exit 2 on unreadable host directory; all 14,935 readable files reported already formatted (includes nested checkouts) |
| Full backend type check | 0 errors/warnings/notes; tool-generated baseline pruning archived and original baseline bytes restored |
| Full backend collection | Passed; removed counter/advisor imports no longer block collection |
| Full backend tests | 2,195 passed, 21 skipped, 5 failed: four nested-worktree source-scan false positives and one stale local database schema failure |
| Stream regression | 11 focused tests passed; final full suite also passes the previously failing worker-registration case |
| Bandit | 5,368 low / 33 medium / 0 high findings; inventory remains tracked in #313 |
| Full pre-commit | Passed, including gitleaks; explicit run over newly added untracked documents/tests also passed |
| Independent review | Frontend retirement, auth-boundary changes and interrupted-completion repair (#316): no blocking findings. The #316 review was static; runtime checks are reported separately above. Documentation and worker-repair review completion remain pending. |

## Approval boundary

Product decisions DEC01–DEC10 are approved. The user additionally approved preserving the existing staged counter/advisor removals rather than restoring removed features to satisfy old tests. Durable task schemas, acceptance protocol, public API/SSE contracts, artifact ownership storage and legacy-file handling remain unapproved architecture choices.

The user chose a **separate cleanup pass** for remaining full frontend lint/format and host-artifact debt. Dependency upgrades require separate approval. This bounded integration must not be represented as a fully gate-clean release or as completion of Stage 1.

## Existing work and verification limits

HEAD is `f5a72150` with substantial pre-existing staged/unstaged/untracked work. Task-start tracked diffs were saved outside the repository for comparison; the staged index was compared byte-for-byte after integration and is unchanged. No staging or commits are part of this integration. Passing focused tests or documentation checks will not certify a deployment, provider qualification or cross-client crash recovery.

Full gate logs are retained outside the repository under `/tmp/opencode/vision-integration-*`. Full frontend lint/format remain blockers; no rule was weakened or blanket reformat applied. Existing dependencies were installed from the lockfile, not upgraded to hide audit findings. The caller remains responsible for deployment-specific provider qualification.

Remaining backend test failures are `tests/test_admin_api_key_timing.py::test_no_eq_comparison_against_admin_key` (four cases whose recursive scan matches test fixtures inside existing nested worktrees) and `tests/benchmark_longmemeval/test_teardown_audit.py::test_teardown_audit_writes_report` (`memories.metadata` missing in the local test database). These are host/test-environment blockers, not permission to weaken security assertions or migrate an existing database without approval. They are recorded in the gitignored local triage notes.

## Next steps from the original assessment

1. Separate, reviewable cleanup of remaining frontend source lint/format debt and host build/worktree/test-database artifacts, as the user requested. Do not delete unfamiliar artifacts or alter the existing database implicitly.
2. Approve durable-request design choices A/B/C and the necessary budget/notification/retention contracts before implementation. Artifact ownership (#312) joins that resource design; legacy-file access cannot be inferred from filenames.
3. Implement and fault-test broad continuity across the declared supported operation set. None of the current baseline repairs substitutes for durable acceptance, worker recovery or cross-client status.

## Issue reconciliation

Updated existing issues with this run's evidence: [#310](https://github.com/sol-aeternum/Daemon/issues/310) (remaining gate debt), [#315](https://github.com/sol-aeternum/Daemon/issues/315) (media/voice prose corrected; separately reported environment-guide drift remains), [#316](https://github.com/sol-aeternum/Daemon/issues/316) (disconnect fix), [#312](https://github.com/sol-aeternum/Daemon/issues/312) (artifact design still pending), and audit inventories [#309](https://github.com/sol-aeternum/Daemon/issues/309), [#311](https://github.com/sol-aeternum/Daemon/issues/311), [#313](https://github.com/sol-aeternum/Daemon/issues/313). No issue was closed or reprioritised and no new duplicate issue was created.

Anomalies: **7 updated (0 critical, 7 warning); 6 host/tooling records added or updated locally**. Host notes cover unreadable/scratch storage, corrected snapshot/CLI invocation errors, stale frontend build artifacts, nested-worktree scan matches and the stale local test database. Source dependencies and security gates remain unchanged.

## PR preparation against current main

The requested PR contains the vision/README integration and applicable baseline repairs, not the original checkout's unrelated staged or unstaged work. It is prepared in an isolated worktree against `2bf65150` (current `main` at preparation time), which already includes account entitlements (#324), hosted login (#307) and subsequent security/gate improvements. The old checkout and its index remain untouched by PR preparation.

The validation counts and pending reviews above describe the **older dirty checkout**, not the PR branch. Repairs already present or superseded on current main must not be replayed as regressions. Current-main port validation and review will be recorded here before submission.

Current main already enforces authenticated owner namespaces for generated artifacts (`orchestrator/artifacts.py` and the download handlers). The original #312 evidence does not describe this newer implementation. The durable-resource draft now preserves that enforcement and proposes extending it with task/version/lifecycle metadata rather than claiming download owner checks are still missing. The older worker-repair review subsequently completed with no blocking findings in the inspected delta; the PR port needs its own current-baseline review.

Current-branch dependency verification: `uv sync --locked` and `npm ci --no-audit --no-fund --prefer-offline` succeeded; `uv run pip-audit` reports no known vulnerabilities and `npm run audit:ci` reports zero vulnerabilities. No dependency or lockfile changes were made. These current-baseline results supersede the old checkout's dependency counts for this PR only.

The product owner explicitly chose to preserve newer main's working encryption metrics and advisor-event compatibility. No old-checkout runtime/module removals are carried into this PR. Main already has status-based memory GC, consolidation-user selection, projection deletion wiring, auth/setup Suspense boundaries and a stronger interrupted-message lifecycle that persists terminal `cancelled` rows. Seven regression tests are ported onto that lifecycle without replacing runtime code or weakening its terminal-state contract. The existing tests remain intact.

### Current-branch verification

| Check | PR-branch result |
| --- | --- |
| Locked dependency installs | Backend `uv sync --locked` and frontend `npm ci` passed; lockfiles unchanged |
| Backend lint / format / types | All passed; basedpyright reports 0 errors, warnings or notes |
| Backend high-severity Bandit | Passed; no high-severity findings |
| Full Bandit inventory | Seven medium findings remain in the pre-existing inventory (#313); no runtime code changed |
| Backend dependency audit | No known vulnerabilities |
| Full backend tests | **2,910 passed, 32 skipped**; optional integration skips do not certify a live deployment |
| Streaming regression file | **15 passed**, including seven added cases; rerun after strengthening tool-count and partial-output assertions |
| Frontend type / lint / format / build | All passed against the current-main toolchain |
| Frontend dependency audit | Zero vulnerabilities |
| Frontend tests | **317 passed** after a test-only synchronization repair described below |
| Aggregate gates | Feature matrix: **73 rows**; full pre-commit including gitleaks passed |
| Documentation links / IDs | New-document links resolve; V/D/AC/O IDs preserved |

The initial frontend run exposed an existing race in the CSP-nonce test: it queried the Google script immediately after a separate loading-state wait. The focused file passed in isolation. The test now waits for the actual script element before asserting its nonce and triggering the error/recovery path; assertions remain intact. Full frontend tests then passed. This is the only additional baseline repair needed on the current branch, alongside the streaming regression coverage; no auth runtime changed.

Fresh read-only review of the current PR files found no runtime/test or product-decision blocker. Its one submission finding was the missing current-branch validation record, resolved by this section. The older checkout reviews are not substituted for this current-main inspection. The PR wrapper will rerun the required gate set at submission; no merge or deployment is part of this task.
