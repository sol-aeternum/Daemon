# Adversarial audit — 2026-09-22

Local audit record; observations apply to the revision below, not to a deployed instance.

## Baseline and isolation

- Starting revision: `149b314e5835d7afc8f78644e88be9424cb34387` (freshly fetched `origin/main`).
- Branch: `audit/adversarial-20260922`.
- Worktree: `/tmp/opencode/daemon-audit-20260922`.
- Original checkout: `/home/sol/daemon`, `main` at `f5a72150070f8e79446e46d7ecf75c6162d59a2d`; substantial staged/untracked user work was preserved.
- Read repository instructions, feature matrix, project context, documentation authority map, memory architecture, technical specifications, deployment configuration, gate tooling, and recent history.
- GitHub read access worked. The only open PRs at audit start were #281/#282 (separate CodeQL action bumps) and #283 (setup-uv bump). Their workflow files are excluded from repairs.
- Completion recheck: #281 and #282 were closed without merging during the sprint; coordinated CodeQL PR #284 is now open, alongside #283. #284 touches only `.github/dependabot.yml` and the CodeQL workflow; there is no file overlap with these local changes.
- #263 is merged; CodeQL `init` and `analyze` both use `5595ccaf912efad79be6eef63a5619ff05969be3`. Google state/nonce and media CSP history are regression hotspots, not presumptive findings.
- Three parallel read-only investigations cover authentication/authorization, input/secrets/deployment, and persistence/failure recovery. Integration and verification stay in this worktree.

## Validation evidence

Logs are local under `/tmp/opencode/daemon-audit-*.log`; no service credentials or production data are used in reproducers.

- `scripts/local_ci.sh frontend` baseline: installation, type check, lint, format, Vitest, and production build pass; `audit:ci` fails on existing dependencies (10 reported vulnerabilities: 1 low, 3 moderate, 5 high, 1 critical). These scanner counts are not ten demonstrated application exploits. No dependency upgrades were applied.
- `python scripts/check_doc_freshness.py --mode fail`: passes at baseline.
- `UV_PROJECT_ENVIRONMENT=.uv-venv uv sync --locked`: passes; isolated Python 3.14 environment.
- `scripts/local_ci.sh backend aggregate` baseline: Ruff lint/format, Basedpyright, high-severity Bandit, feature matrix, and pre-commit (including gitleaks) pass. Pytest: **2616 passed, 5 skipped**. Blocking `pip-audit` fails for existing `anyio==4.12.1` (CVE-2026-63374 / CVE-2026-64847; reported fix 4.14.2). Full Bandit inventory reports 6157 low, 1 medium, 0 high; the medium benchmark assertion issue is already tracked as #272.
- Frontend baseline Vitest: **254 passed / 25 files**. `npm run test:browser`: **3 passed**, including all existing CSP browser regressions. Host Node 26 emits the known Playwright warning tracked in #274; CI uses Node 24.
- Existing backend deprecation warnings correspond to #177, #192, and #228; npm's deprecated `whatwg-encoding` is tracked as #195.

## Findings and disposition

### R1 — Partial chat persistence continues using stale history (Medium)

- Original location: `orchestrator/main.py:2141-2214`, native `/chat` persistence exception handler and subsequent history loading.
- Preconditions: authenticated chat with a configured memory store; conversation lookup/creation succeeds but insertion of the current user message fails (e.g. transient database failure).
- Demonstrated defect: the handler says it continues without persistence but retains the store and conversation UUID. Existing-conversation history can then replace the previous user question with the current question; downstream streaming can persist an assistant reply without its corresponding user turn.
- Expected contract: preserve the existing graceful-degradation policy while actually disabling persistence for that turn. This repair does not introduce a new error response or claim durability for degraded chat.
- Repair: clear the local persistence store, conversation UUID, and existing-conversation flag. Preserve authenticated identity and use submitted history. For refreshed clients without history, best-effort read prior finalized turns and append the unsaved question. No global store state is changed.
- Regression: `tests/test_chat_history.py::test_failed_user_insert_disables_turn_persistence`, parametrized for new/existing conversations and present/empty client history. The real mock-mode SSE stream verifies completion without an error or new-conversation event, preserved identity/context, and no orphan assistant insert. Initial two cases failed before the repair (`daemon-audit-chat-red.log`); the expanded original-code replay is in `daemon-audit-original-regressions.log`.
- Remaining limitation: graceful degradation still permits a non-durable reply; final assistant persistence failure and user-facing durability notification were not redesigned.

### R2 — Cancelled video generation leaks credits (Medium)

- Original location: `orchestrator/subagents/image.py:567-649` (`_generate_video`), after the credit debit and before/during provider execution.
- Preconditions: an authenticated paid-tier generation has debited credits, then its task is cancelled, or constructing the selected provider fails. The old `except Exception` missed `asyncio.CancelledError`, and construction occurred outside compensation.
- Evidence: original cancellation and construction tests failed with a reduced balance; see `daemon-audit-video-red.log`. This is a financial-correctness defect, not an authorization bypass.
- Repair: include provider construction in the compensation boundary; retain and shield the refund task through repeated caller cancellation, observe its result/exception, and propagate cancellation after cleanup. Refund DB errors are logged without masking cancellation. Successful generation, BYOK, and unsupported-provider behavior remain covered.
- Regressions in `tests/test_kling_e2e.py`: cancellation during generation; construction failure; one/repeated cancellation during blocked refund after either provider error or cancellation; refund outage preserving cancellation. Assertions check balance restoration and one refund where applicable, rather than merely checking a refund mock was called.
- Limitations: the tests use the existing in-memory DAL double, not live PostgreSQL transaction/uniqueness enforcement. Process death, cancellation of the refund task itself during loop shutdown, and refund DB outage still require durable reconciliation outside this patch. Cleanup can delay cancellation until the DB operation finishes.

### R3 — Deployment build ignores lockfile; standalone image is incomplete (Medium)

- Original locations: `backend/Dockerfile:5-18` dependency installation and application COPY instructions; this is the image used by Compose backend, worker, and migration services.
- Expected contract: project instructions require reproducible installs, and the image's default command must import the application.
- Original-build evidence: an isolated original Dockerfile build **succeeded with malformed `uv.lock`**, exit 0 (`daemon-audit-container-red.log`). No secrets or original checkout were mounted. Separately, the locked-install intermediate image reproduced the pre-existing standalone failure: `ModuleNotFoundError: No module named 'db'` (`daemon-audit-container-import-red.log`). Compose's source mount had hidden omitted runtime packages.
- Repair: reuse the root Dockerfile's pinned uv image; require `uv sync --locked --no-dev --no-install-project`; keep the environment in `/opt/venv` with Python/uvicorn on PATH so `/app` mounts cannot hide it. Include runtime `db`, `config`, `providers`, `scripts`, and `migrations` alongside `orchestrator`.
- Regressions: `tests/test_backend_container.py` performs actual isolated Docker builds. Malformed and syntactically valid stale locks must fail; valid image imports the real app/worker offline, includes migrations, and retains locked FastAPI plus Python/uvicorn under a synthetic source mount. Three tests pass (`daemon-audit-container-green.log`). They are explicitly opt-in, not a default unit-suite Docker requirement.
- No dependency/lockfile versions, Compose architecture, CI permissions, CodeQL pins, or service-image tags changed. Base-image immutability and bit-for-bit builds are not claimed.

### Authentication track — no confirmed new authorization defect

Reviewed hosted email/Google flows, nonce/state consumption, origin/CSRF protection, device sessions, refresh/logout, and protected resource boundaries. Existing Google replay/origin/provider-token tests remain in `tests/test_identity_google_routes.py` and `tests/test_hosted_identity_smoke.py` and passed in the full baseline suite.

The initial cross-user skills finding was withdrawn after adversarial challenge: canonical files and projections are instance-global, and `tests/test_skill_protection.py` deliberately permits authenticated manual edits to shared protected-looking skills. Conversely, “My skills” UI copy and personal hosted tenants imply private ownership. This is **decision-required scope ambiguity**, not a proven bypass of a declared ownership boundary. Personal skills would require explicit storage/projection ownership and migration decisions; no guessed tenancy model was introduced.

### Input/deployment track — rejected and bounded findings

Reviewed rendering/preview isolation, redirects, outbound request validation, filesystem artifacts, secret handling, deployment builds, and CI. No new reachable rendering/SSRF/artifact-authorization or media-CSP defect was demonstrated.

The proposed checkout-token exploit was rejected as unsupported: the exact checkout v7 revision inherits v6's credential-file storage under `$RUNNER_TEMP`, so the suggested `.git/config` bearer reproducer describes an older implementation. Normal fork `pull_request` jobs receive read-only tokens by default; no write-token escalation path was demonstrated here. Explicit permissions and credential-persistence changes remain optional hardening, outside the three pending workflow dependency PRs. Sources: [pinned checkout README](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/README.md), [GitHub pull-request workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request).

The deployment defects are repaired as R3 above. Mutable service/base-image tags were not promoted to separate demonstrated exploits.

### Dependency baseline blockers

Official upstream advisories corroborate installed `next@16.3.0`, `sharp@0.35.3`, and `anyio==4.12.1` affected ranges:

- [Next Windows RCE](https://github.com/advisories/GHSA-p293-qw3h-jr36): patched in 16.3.3; requires Windows hosting, unlike the documented Linux containers.
- [Next AVIF optimization](https://github.com/advisories/GHSA-2xp9-vwfh-vxw4) and [sharp/libheif](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c): patched in Next 16.3.3 / sharp 0.35.4; requires processing malicious AVIF/HEIF. No malicious native-code payload was exercised. Absence of an application PoC does not clear the dependency gate.
- [AnyIO TLS hostname validation](https://github.com/advisories/GHSA-82r6-8w77-94w6): fixed in 4.14.2; requires an internationalized hostname plus attacker-controlled routing/certificate conditions.
- [AnyIO process-worker stderr deadlock](https://github.com/advisories/GHSA-5p39-cfhj-2xmp): fixed in 4.14.2; requires process-pool use and enough stderr output. No direct AnyIO process-pool use was found in application code.

These are existing SCA blockers, not regressions caused by this sprint. Lockfiles and dependency versions remain unchanged; a separately approved dependency update is needed for clean gates.

## Coverage limits

- Synthetic credentials/data, mocked LLM/video providers, and in-memory database doubles are used for failure injection. No production API, real email delivery, paid generation, or deployed database was exercised.
- The five baseline skipped tests remain coverage gaps; the full passing unit suite does not establish live database concurrency or production OAuth-provider behavior.
- Durable video-refund reconciliation after database outage/process death and persistent-message failure notification are outside the focused repair boundary. They require lifecycle/product decisions rather than more speculative exception handlers.
- Skill tenancy remains an explicit decision item. Concurrent memory trust-signal updates were only a lower-impact hypothesis, not reproduced or changed.
- No pushes, PR comments, issue creation, merges, or deployment. Qualifying dependency blockers and existing warnings are recorded here instead of publishing; host/tooling observations are in ignored `.triage.local.md`.

## Independent review

- Fresh `review-go` reviewed the Astra-authored chat repair. Its identity/SSE coverage and refreshed-client context concerns were addressed; re-review found no new defects.
- Fresh `explore-luna` reviewed GLM/mixed-authored video and container changes. Repeated-refund cancellation was repaired and re-reviewed closed. The standalone image-import finding was reproduced and repaired; final re-review reports **No findings**, with both review defects closed.
- The parent also reproduced and fixed refund exceptions masking cancellation, and replaced initial Dockerfile-text assertions with real build/runtime regressions. Reviewers did not execute tests; all execution evidence was independently run by the primary agent.

## Changed files

- `orchestrator/main.py`
- `orchestrator/subagents/image.py`
- `backend/Dockerfile`
- `tests/test_chat_history.py`
- `tests/test_kling_e2e.py`
- `tests/test_backend_container.py`
- `docs/ADVERSARIAL_AUDIT_2026-09-22.md`

## Initial local-sprint validation

- Replayed all 11 new chat/video failure cases against a clean archive of the starting revision with only the tests copied in: **11 failed, 15 deselected** (19.39s). Final-code focused chat/video run: **26 passed**. Original-source event waits were bounded so absent refund execution fails rather than hanging.
- `UV_PROJECT_ENVIRONMENT=.uv-venv PYTHONPATH=. TMPDIR=/tmp/opencode DAEMON_DOCKER_TESTS=1 uv run pytest -q tests/test_backend_container.py`: **3 passed** (19.27s). Runtime probes use `--network=none`; no production Compose stack was started.
- Original Dockerfile accepts malformed lock (build exit 0); repaired Dockerfile rejects both malformed and stale locks. The standalone import failure is independently reproduced before its source-packaging repair.
- Final `scripts/local_ci.sh backend aggregate`: **2627 passed, 8 skipped** (5 pre-existing skips plus 3 opt-in Docker cases, separately executed and passed). Ruff lint/format, Basedpyright (0 errors), high-severity Bandit, feature matrix, and all pre-commit hooks including gitleaks pass. The sole blocking failure is the **same baseline AnyIO dependency audit**; full Bandit remains non-blocking inventory with no high-severity findings.
- Frontend source/lockfiles are unchanged: baseline **254 tests**, **3 browser tests**, type check, lint, format, and build pass. Its **existing dependency audit fails** as documented above. Thus the complete project gate is **not green**; neither gate was weakened or waived.
- Changes are staged, uncommitted, and local on the branch/worktree listed above. No remote mutation or deployment took place. Host/tooling notes: 5 local entries; dependency blockers and known inventory/deprecations retained in this report, with no issues published.

## PR preparation follow-up

The user subsequently authorized resolving the gate blockers and publishing the repairs. During the remote recheck, existing PR [#286](https://github.com/sol-aeternum/Daemon/pull/286) already supplied the dependency remediation. Rather than duplicate that work, the repair commits were rebased onto its head, `1b86d1cd9c1cc41fbbf089a46916e61dd38e010c` (`fix/dependency-audit-2026-09`). `main` remained at the original audit revision. `git range-diff` confirmed all four audit commits were unchanged by the rebase.

The repair PR is therefore a dependent PR: merge #286 first, retarget the repairs to `main`, and require passing checks. The repair diff remains limited to the seven files above. The original dependency blockers and local-only status recorded above describe the initial sprint, not this authorized publication follow-up.

- A fresh read-only integration review of the dependency-base changes and the repaired cancellation/container boundaries reports **No findings**.
- The repair commits contain no dependency-file or workflow changes relative to #286; all dependency remediation remains owned by that existing PR.
- Opt-in Docker verification against the updated dependency lock: **3 passed** (46.01s), including offline imports and bind-mount preservation.

- `UV_PROJECT_ENVIRONMENT=.uv-venv uv sync --locked` followed by `scripts/local_ci.sh`: **exit 0, all blocking gates pass** (213s). Backend: **2627 passed, 8 skipped**; frontend: **254 passed / 25 files**. Lint, format, type checks, high-severity Bandit, production build, feature matrix, documentation freshness, and pre-commit/gitleaks pass.
- `pip-audit`: **no known vulnerabilities**. `npm run audit:ci`: **passes**, retaining two moderate Vitest-related advisories already documented in #286; no high/critical findings remain. Full Bandit remains the existing non-blocking inventory. Gates were not weakened.
- `npm run test:browser`: **3 passed** (12.3s) on the updated Next.js dependency base. The known host Node/Playwright deprecation remains tracked as #274.
- `npx --no -- commitlint --from origin/fix/dependency-audit-2026-09 --to HEAD`: passes for the repair commits.
- Current verification logs: `/tmp/opencode/daemon-pr-{sync,gates,container,browser}.log`.

The follow-up publishes one dependent repair PR with three separately reviewable implementation commits and this evidence record. No merge or deployment is performed.
