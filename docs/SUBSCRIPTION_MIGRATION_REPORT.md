# Subscription migration validation report

Validation date: 26 September 2026. No deployment or billing-provider changes
were performed.

## Current-main isolation

The migration is isolated on `feat/account-entitlements` from upstream
`81b0cb6d` in a separate worktree. Only the migration delta above the original
checkout's index was transferred; unrelated staged removals and generated files
were excluded. Upstream already retired `backend/image_gen`, so that package is
not resurrected. Current-main auth, artifact ownership, SSRF, summary cursor,
memory identity, and frontend SDK changes must be preserved during conflict
resolution.

The current locked backend environment passes **183** foundation, migration,
real-PostgreSQL, and documentation-checker tests. All **40** migration files,
including both historical `036_*` files and the new `039_*` file, applied to a
fresh isolated PostgreSQL database. Scoped foundation type checking passes.
The current locked backend dependency audit also reports no known vulnerabilities.
Runtime/frontend conflicts are resolved. The complete backend suite passes
**2,882 tests** with eight optional integration skips; type checking reports zero
new errors, Ruff lint/format pass, and the high-severity security scan passes.
The type-check baseline only shrank: 32 resolved diagnostic entries were removed,
with no new suppressions. Frontend type/lint/format/audit, **316 tests**, and the
production build pass. Feature-matrix and pre-commit gates pass. Independent
runtime, memory, frontend, and final integration reviews found no blocking issues.
Draft publication uses the gated PR wrapper; provider qualification and commercial
approval remain rollout prerequisites.

The older snapshot's failures below are historical evidence, not an assertion
that those defects remain on current main.

## Migration result

- Commercial authority moves from deployment/client tier guesses to authenticated
  account-owned Free, Pro, and Power plans.
- Free recurring routine funding and the lifetime premium trial are independent.
  Upgrades/downgrades preserve unused trial allocation and existing video credits.
- Additive migration `039_entitlements_commercial.sql` introduces durable account
  entitlements, reservations, usage, subscription-event deduplication, and reporting
  views. The full prior migration chain followed by 039 was exercised in isolated
  PostgreSQL; existing user, memory, and credit totals were preserved.
- No local subscriber inventory or billing integration existed. Paid status must
  come from verified external account-ID imports; `DEFAULT_TIER=pro` is not evidence
  of a paid subscription. Trusted legacy imports map Starter to Pro, Max to Power,
  and BYOK to Pro without granting a spending bypass.
- The shipped provider policy approves no routes. Live account privacy settings,
  endpoint terms, capabilities, limits, prices, and availability require operator
  qualification before inference is available.
- Unbounded image/video/audio execution and direct vendor audio tokens are
  disabled. Their feature-matrix entries are retired pending qualified bounded
  replacements; preserved UI, settings, and credit balances do not imply working
  generation. Memory reads/writes retain a lexical-only path without embeddings.

See [the architecture and rollout contract](SUBSCRIPTION_ARCHITECTURE.md) for
source locations, trusted import interfaces, privacy requirements, and deployment
ordering. Policy approval does not create a missing bounded external adapter.

## Pre-port validation evidence (original dirty checkout)

| Check | Observed result |
| --- | --- |
| Combined policy/service/migration, real PostgreSQL, documentation-checker and image-catalog tests | 189 passed |
| Focused runtime chat/history/advisor/model/council tests | 121 passed in implementation pass; primary independently verified 105 runtime/history/model/council/benchmark tests |
| Council real-guard and invalid-parameter regression tests | 45 passed |
| Final reflection, skill evaluation, store provenance and benchmark transport tests | 54 passed |
| Feature matrix | 72 rows validated |
| Documentation freshness | No drift detected |
| Full pre-commit, including secret scanning | Passed |
| Full backend Ruff lint | Passed (unreadable host directory warning) |
| Full backend format check | All readable files formatted; command exits 2 on unreadable `.daemon/` |
| Full backend type check | Five pre-existing worker import/store-method errors |
| Full backend pytest | Collection blocked by pre-existing removed worker encryption symbols |
| Broad backend diagnostic (`--continue-on-collection-errors`) | 2,136 passed, 21 skipped, 12 failed, two collection errors; baseline/host blockers detailed below |
| Full frontend tests | 248 passed, 19 pre-existing failures |
| Frontend type/lint/format | Baseline unchanged: 130 type errors; 55 lint errors and 13 warnings; 274 format failures |
| Frontend production build | Blocked by pre-existing removed `isAdvisorEvent` export |
| Backend dependency audit | 157 reported advisories across 20 packages; dependencies unchanged |
| Frontend dependency audit | 37 reported vulnerabilities; dependencies unchanged |
| Bandit | 5,335 low, 33 medium, no high scanner findings; adjudication inventory tracked |

Final review's council reasoning-parameter and capability-denial corrections pass
45 focused tests and independent review. Residual embedding-call and benchmark
transport corrections pass their final 54-test suite; their bounded final review
found no blocking findings. Test groups overlap and should not be added into a
unique-test total. Independent review covered the entitlement foundation, frontend,
runtime enforcement and residual memory/benchmark corrections in bounded passes.
These results do **not** constitute a clean release gate. Bandit's new entitlement
SQL findings concern source-defined column lists/static locking suffixes with
bound request values, reviewed as false positives; no scanner rule was disabled.

## Original-snapshot findings and tracking

- [#310](https://github.com/sol-aeternum/Daemon/issues/310): pre-existing worker and
  advisor removals leave consumers/tests unresolved and block validation.
- [#309](https://github.com/sol-aeternum/Daemon/issues/309) and
  [#311](https://github.com/sol-aeternum/Daemon/issues/311): backend/frontend
  dependency-audit inventory.
- [#312](https://github.com/sol-aeternum/Daemon/issues/312): pre-existing generated
  artifact ownership gap in the old snapshot; current main has account-scoped
  artifact namespaces that the port must retain.
- [#313](https://github.com/sol-aeternum/Daemon/issues/313): SAST inventory requiring
  adjudication without gate weakening.
- [#314](https://github.com/sol-aeternum/Daemon/issues/314): pre-existing direct web
  fetch SSRF gap in the old snapshot; current main validates and pins addresses
  for every redirect hop, which the port must retain.
- [#306](https://github.com/sol-aeternum/Daemon/issues/306): fabricated paid-plan
  display addressed by server-owned entitlements; profile display-name wiring is
  not claimed resolved.
- [#323](https://github.com/sol-aeternum/Daemon/issues/323): non-blocking,
  pre-existing whitespace-only memory input validation edge found during the
  current-main memory review; no production request was sent.

The repository's existing staged changes were compared byte-for-byte with the
pre-task snapshot and remain intact. The broken/unwritable local virtualenv was
avoided with an isolated locked environment. Unreadable `.daemon/` remains a host
tooling warning, recorded in the ignored local triage log.

The task-owned isolated PostgreSQL test container was stopped after verification.
No migration was applied to an existing deployment, and no billing product or
subscription was changed; schema application was tested only in the isolated
container.
