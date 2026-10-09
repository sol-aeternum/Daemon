# Durable chat reliability closeout — #477

## Scope and release boundary

This implements the approved slice-1 recovery contract (DEC04–DEC05,
AC03/AC09) against main `988940c5`. It does not complete all Stage 1 slices or
authorize deployment. `DURABLE_CHAT_ENABLED` remains default-off; the restart
proof enables it only in a disposable, synthetic local Compose project.

The owner approved atomic snapshot/task-marker publication and minimal additive
recovery evidence on existing event types on 9 October 2026; see
[DURABLE_REQUEST_DESIGN.md](DURABLE_REQUEST_DESIGN.md), §18. PostgreSQL remains
task authority. Redis is delivery/dispatch only. Leases, cancellation, account
budgets and conservative material-effect retry restrictions remain in force.

## Acceptance reconciliation

These are the #477 checklist and follow-up findings, not a claim that closing
their original issues cleared the later regressions.

| Item | Baseline / closeout status | Proof required |
| --- | --- | --- |
| #472 persisted progress | Original fix merged; Redis-gap, lifecycle and outcome follow-ups here | Ordered replay, pagination, duplicates, missed delivery, terminal flush |
| #475 snapshot identity | Original refresh guard merged; split-commit crash window here | Shared commit then crash; same pinned snapshot, one fetch and row |
| #476 attachments, key lifetime, content verifier | Already resolved by #479/#485; preserved | Held-file completeness, identity and migration order |
| #474 authenticated PWA reads | Already resolved by #482/#484; no cache changes here | Existing privacy/cache regressions; #486 remains separate |
| Pre-preparation cancellation | Already resolved; preserved | No preparation after cancel |
| Busy-conflict acceptance race | Already resolved; preserved | Retry acceptance when prior task ends during conflict handling |
| HTTP/tool outcomes and ambiguous effects | In this closeout | HTTP response shapes, timeout, `performed:false`, unknown legacy replay |
| Interruption disclosure excluding admission deferrals | In this closeout | Only executed lost/retryable attempts count |
| Revisit pending entries younger than 60 seconds | In this closeout | Scheduled lookup and auth/navigation cleanup |
| Correct failed-turn reconnect key | In this closeout | Older failed turn versus newer held submission |
| Accepted new chat discovered on Home | In this closeout | Open accepted conversation only while still Home |
| Held restoration when composer frees | In this closeout | Occupied load-time composer, later empty, newer input preserved |
| Remove rejected exchange before resend | In this closeout | Refused prompt/reply removed, unrelated messages retained |
| Permanent lookup failures | In this closeout | 403/404 stops retries with actionable state; transient retries |
| Live regeneration and stale generations | In this closeout | Real regeneration versus same-generation correction; late frames ignored |
| Held attachment capacity after release | In this closeout | Full held-file budget, skipped new file, release then durable save |
| #483 restart proof | Strengthened here | Persisted partial before kill, second authenticated client, settlements, stale fence, Redis gap, material no-repeat |
| #469 account fence-and-drain | **Still blocked / separate** | Approved design is not completed implementation; #491 is media-submission safety only |
| #486 legacy HTTP-cache privacy | **Still blocked / separate** | Real-browser pre-existing cache and account-change acceptance |
| Deployment / enablement | **Not authorized** | Separate owner decisions after remaining gate dependencies |

## Deterministic restart proof

Run `scripts/durable_restart_drill.sh` from a checkout without `.env`.
Project-name reservation, Docker-resource preflight, sanitized environment,
project-volume isolation and verified teardown remain mandatory. No provider
keys, real accounts or paid inference are used.

The explicit `scripts/durable_restart_fixture.py worker` launcher is test-only;
normal workers never import it. It refuses absent project permits, non-mock
settings, external database URLs and provider credentials. It replaces provider
transport and a synthetic notification effect, retaining real tool orchestration,
PostgreSQL task persistence and compute reservation/settlement. No production
pacing setting or new environment surface is added.

- **A:** acceptance with no worker survives backend restart, finishes exactly
  once, and same-key replay returns the original task.
- **B:** the first provider emits a prefix then waits at an explicit gate.
  The driver observes the durable prefix before SIGKILL, expires the stopped
  worker's lease using SQL, and restarts it. A distinct authenticated device
  sees the exact saved answer. Assertions check `lost,completed`, preserved
  partial text, interruption metadata, two settled reservations, conservative
  lost-hold settlement and refusal of stale writes/operations.
- **C:** a second client reattaches after backend restart while Redis
  publications are dropped. PostgreSQL replay recovers lifecycle/tool evidence
  once, the truthful outcome and the exact saved result.
- **D:** one synthetic material action is performed before the worker kill.
  Recovery must end at `needs_attention`, preserving its outcome, with one
  attempt and one effect. No automatic repeat is permitted.

Pacing is not the kill condition: committed state plus explicit gates is.
Disposable PostgreSQL regressions cover additional cursor/race interleavings.

## Verification record

Committed regression coverage:

- `tests/test_task_reliability_snapshots.py`: crash after the shared commit,
  pre-commit rollback, exact pinned identity, expired/missing/legacy pins,
  lock-wait lease/cancellation/takeover/deletion checks, and old-lock negative
  controls for the cancellation/publication deadlock.
- `tests/test_task_reliability_outcomes.py` and
  `tests/test_task_reliability_observe.py`: HTTP/timeout/no-effect evidence,
  stale-worker operation completion, paginated ordered replay, Redis gaps and
  duplicates, catch-up under continuous traffic, terminal-watermark flushing,
  owner/revocation checks, generation replacement and admission deferrals.
- Existing task runner/store/API regressions preserve pre-preparation
  cancellation, busy-conflict acceptance retry and idempotent acceptance.
- `frontend/__tests__/durable-page-integration.test.tsx`: failed-turn key,
  Home discovery including terminal answers and late navigation, age revisits
  and cleanup, occupied-composer restoration, rejected exchange removal/resend,
  and actionable permanent lookup failure. Conversation-history regressions
  exercise permanent 403/404 and capped transient lookup retries.
- Durable stream/task and tool activity/log regressions exercise explicit
  interruption counts, stale generations, same-generation corrections and
  unknown/failed outcomes without success indicators.
- Held-submission regressions prove that releasing a full 100 MiB held-file
  budget persists the previously skipped new file, with payload readback after
  reload. Existing #485 completeness, key-identity and migration tests remain.
- Existing authenticated PWA/cache regressions remain unchanged; they do not
  establish the separate real-browser legacy-cache acceptance in #486.

Local verification on 9 October 2026: backend/aggregate blocking gates passed
with 5,433 tests passed and 123 skipped (CI's task-database scope, plus the
snapshot database). All frontend blocking gates passed, including 1,058 Vitest
tests and the build. Three deterministic A–D Compose runs passed with verified
disposal, including hostile inherited settings and the final Settings-boundary
fixture checks. Fresh read-only backend and mixed-author frontend/drill reviews
found no confirmed blocking defects.

Non-blocking browser inventory reported 25 passed and 16 failed; existing #490
tracks the inventory debt, including separately reproduced baseline navigation
and memory-paging failures and inconsistent tool-log failures. The additional
optional memory-import database tests reproduce the existing #465 failures on
unchanged main and are not absorbed here. Full Bandit inventory remains tracked
by #313; the high-severity gate passed. No baseline or gate was weakened.

Exact-head CI, detailed commands and independent-review adjudication are recorded
in the draft PR. Local verification is not deployment or enablement clearance.
No merge or deployment is part of this task; #477 must remain open.

#488 provider retries, #487 OpenUI and #489 artifact catalog remain separate.
