# Bitemporal memory — source-only #601

> **Separate-PR evidence boundary:** This file's existing acceptance counts and gate
> results describe the historical mixed `trust-real-postgres` checkout. They are
> not verification of the clean memory-only PR. Its final-state verification and
> independent transfer-diff review remain pending until recorded by the primary.

Status: local implementation and required blocking gates verified; **not deployment clearance**.
Migration: `migrations/066_memory_bitemporal.sql`. Upstream migrations 064 (artifact
restore) and 065 (terminal output) are preserved unchanged. This unapplied memory
migration was renumbered during source integration; no production migration ran.

## Two independent questions

- Event time: when an assertion applies in the world.
- Knowledge time: which committed assertions Daemon had recorded.

Source-message dates are provenance, not knowledge timestamps. Import records
new knowledge; it does not restore the original system's knowledge history.
Existing text becomes a migration baseline. Earlier versions are not invented.

Event intervals are half-open `[start,end)`. Unknown bounds are distinct from
unbounded intervals. Partial dates, approximate expressions and unresolved local
times retain their precision and original encrypted expression. They cannot be
promoted to exact timestamp matches. Resolved inputs require explicit offsets;
IANA zone names are validated, not silently guessed. A date-only value does not
mean midnight and an ambiguous daylight-saving time requires resolution.

## Committed knowledge, not transaction-start time

Exact knowledge uses an owner/account-generation-qualified committed sequence
watermark. The account clock and all semantic changes commit together. A reader
observes the watermark in a fresh statement after its serialization barrier.
Rollback produces no committed change. A transaction that started before a
cutoff but committed afterward cannot be assumed visible at that cutoff.

`NOW()` is transaction-start time. `clock_timestamp()` evaluated while writing
is still before commit. Neither is a commit timestamp. Transaction IDs and
allocated sequences alone do not reconstruct historical wall-clock visibility.

Wall-clock knowledge requests use conservative bounds:

1. Record a pre-commit lower bound with the mutation.
2. Another transaction observes that committed mutation and records an upper
   bound, excluding its own uncommitted mutation.
3. A cutoff inside the bracket, or with insufficient visibility evidence,
   returns `knowledge_cutoff_indeterminate` rather than a supposedly complete
   older answer. Sealing a bracket does **not** make a cutoff inside it exact.

This assumes a trustworthy database wall clock. Detected regression blocks
wall-clock resolution; exact logical watermarks remain separately meaningful.
Missing post-commit observations can leave a large uncertain interval after a
crash. No new PostgreSQL server configuration is required or authorized.

## Revisions and relationships

Stable memory IDs identify lineages. Immutable revision bundles contain one or
more assertion segments and their semantic state. Current projections retain
ordinary retrieval/index compatibility; historical ranking uses selected
revision content and its own lexical/vector representations.

An explicit correction revises knowledge over its declared event scope. An
explicit world change learned at K and effective at T retains the previous
world before T and the new world from T in the new bundle, while the earlier
knowledge bundle remains available before K. Expected revision IDs prevent ABA
and metadata-only concurrent changes from evading optimistic concurrency.

Different facts and unresolved conflicts are retained. Category, slot and
similarity do not establish a correction or world-change relationship. Exact
deduplication is qualified by temporal scope.

Status, category, tier, locality, confidence and assertion metadata are semantic
changes. Embedding maintenance and access statistics are not new assertions.
Historical embeddings can be absent; lexical search remains valid.

## Historical requests and context

Native requests use additive `temporal_scope` containing optional `event_at` and
either `known_at` or an observed `watermark`. Durable input retains the resolved
watermark, and request identity includes the requested scope. Omitted scope
preserves legacy request identity. Parent ceilings apply to memory reads and
reflection; tool arguments cannot widen them.

Owner, current permissions/locality, current tombstones, knowledge and exact
event bounds must qualify candidates before ranking and LIMIT. Strict empty is
empty, never fallback to current records. Permissions are not historical grants.

Historical prompts exclude unsupported current preferences, learned context,
summaries and recent conversation history with an explanation. The submitted
question is a present instruction, not proof of knowledge at the cutoff. Frozen
L0 memory is not exempt from qualification. Unsupported contextual-home/council
combinations are refused rather than silently injecting unversioned context.

Legacy NULL-source records remain available to ordinary reads with unknown
provenance. Strict reads exclude records whose provenance/current permission
cannot be certified; migration does not silently erase current memory.

## Memory deletion scope A

Deletion revokes the entire selected memory lineage and memory-derived context
at every knowledge cutoff. Mixed derived summaries may be conservatively
invalidated in full. Original user/assistant source-conversation text and
unrelated artifacts are not wholesale deleted or rewritten.

Soft erasure immediately removes governed searchable lexemes, vectors/model
identities and derived log query text/vectors, while retained lineage payloads
remain encrypted until routine cleanup after 30 days. That deadline is based on
erasure time, not subsequent touches. Hard erasure removes governed payloads
immediately. Superseded revision history has no arbitrary expiry.

Entity projections, derived UUID arrays and memory-tool-result payloads are
included. Stale derived publication is rejected using captured erasure state and
revision dependencies. Recorded erased source spans suppress automatic replay;
this is not a permanent content ban: a new user statement can establish the fact
again. Legacy dependency gaps require conservative context invalidation and
prevent claims of exhaustive retroactive copy identification.

Deletion cannot recall provider context already disclosed or client-held copies.
This work does not establish remote cessation or a completed global account fence.

## Deployment and rollback blockers

**Memory-only erasure is not backup-restore safe.** Existing account-deletion
journal contracts remain unchanged. Restoring an earlier backup can restore
erased memory. Deployment requires an explicit restore restriction/owner decision
that addresses this limitation; this implementation grants no waiver of backup
history retention or privacy requirements and authorizes no production rollout.

Every new table is account-owned with cascade removal and must be included in
explicit account-reset classification. This is not evidence that the separate
global account deletion/reset protocol is implemented.

Migration and matching binary must be qualified together. Unregistered semantic
writers fail closed through database capture enforcement. Rollback preserves
history/tombstones and must not resume an older unrestricted writer. Do not drop
history or weaken the trigger to make an old binary work.

Existing memory caps are unchanged. Revisions and duplicate historical search
representations grow without an arbitrary retention cutoff. Operators must budget
storage, index/build cost and backup retention accordingly; growth tests are not
a production sizing claim.

## Acceptance evidence

Core suites: `tests/test_memory_bitemporal_types.py` and
`tests/test_memory_bitemporal_postgres.py`. They are synthetic and require owned,
disposable PostgreSQL through the existing test infrastructure, never application
`DATABASE_URL`, inference or production data. Final evidence must additionally
cover every writer/caller, source detachment, derived erasure races, actual
chat/task/tool propagation, historical query EXPLAIN plans, migration/rollback,
storage growth and the repository gates. Unrun checks are not passed checks.

### Backend acceptance ledger — 9 November 2026

This is source/synthetic qualification for #601 (DEC08 context provenance and
DEC11's portable context), not real-model historical-answer quality. Retained
XML and logs live in the registered task's external evidence directory,
`~/.local/share/opencode-work/evidence/trust-real-postgres/`.

| Requirement | Implementation / executable evidence | Result / limit |
| --- | --- | --- |
| Independent event T and committed knowledge K; no caller-backdated knowledge | `temporal.py`, `revisions.py`, migration 066; types/core PostgreSQL suites | Covered by `memory-execution-qualified.xml`: 57 passed, zero skips across types, core, paths, execution and listing/import/metadata persistence |
| Unknown, partial, approximate and timezone-uncertain times stay uncertain | Temporal input validation; strict SQL excludes unknown event bounds; types/core suites | Offsets required for exact timestamps; no inferred midnight or historical fallback |
| Stable IDs, corrections, world-change segmentation, revision CAS/ABA | `store.py`, capture trigger and revision bundles; core/paths and fact-account guard suites | Outside event fragments and earlier knowledge preserved; stale revision CAS refused |
| Every semantic writer participates; maintenance is not an assertion | Registered store writers, tool/PATCH/extraction/import/derived publication, source detachment and cleanup; paths and existing boundary suites | Raw writes and inherited asynchronous writer contexts refused; current caps unchanged |
| Filter current owner/permissions/deletion and T/K before ranking/LIMIT | Historical search SQL and ordinary current-assertion view; poison-ciphertext, LIMIT and actual EXPLAIN paths | Small synthetic query plans/index-preservation only, not production latency or scale |
| Native chat and encrypted durable task retain explicit scope | Native HTTP and actual durable worker execution tests | Original cutoff retained despite later memory; request identity rejects changed scope under the same idempotency key |
| L0, tools/reflection and prompt context cannot widen the parent | Read-scope propagation, historical injection and tool ceiling checks | L0 qualified; unsupported current preferences/history/summary/learned context excluded with explanation; unsupported combinations refused |
| Scope A lineage/derived-copy deletion, not original conversation bodies | `lineage.py`, registered erasure and derived publication; core/paths and entity/summary boundaries | Tombstones apply at old K; derived traces/log queries/vectors scrubbed; originals and unrelated tool results retained |
| Soft 30-day encrypted payload retention; immediate hard purge | Tombstone deadline and garbage collection; core/paths suites | Search indexes scrubbed immediately; touches do not extend erasure deadline; superseded history has no arbitrary expiry |
| Suppress erased source replay without banning new statements | Source-span records, extraction filtering and source-suppression checks | New message IDs may reestablish a fact; legacy gaps use conservative over-invalidation |
| Migration baseline, matching-binary rollback guard, reset/export classification | Legacy migration execution, disabled-trigger startup refusal, account cascade and catalogue/history/export tests | Migration-time knowledge only; older unrestricted binary must not resume; no production migration performed |
| Historical storage growth and operational limits | Repeated immutable revisions and relation-size/index checks | Growth demonstrated, not capacity qualification; backup-restore limitation above remains a deployment blocker |
| Repository gates and independent review | Final backend/static evidence recorded below; primary owns remaining gates and review | Incomplete until exact final-state evidence and independent review are reconciled; no deployment clearance |

Final focused PostgreSQL batches (same implementation; split for the existing
192 MiB disposable fixture's storage budget):

- `memory-final-fact-entity.xml`: 161 passed, 19 skipped. Skips remain explicit;
  they are not transaction/fencing qualification.
- `memory-final-summary-title.xml`: 120 passed, zero skips.
- `memory-final-existing-persistence.xml`: 34 passed, zero skips, including all
  seven formerly skipped edit-persistence cases.
- `memory-final-core-execution.xml`: 30 passed, zero skips.
- Total: **345 passed, 19 skipped**. The oversized combined run is retained as
  failure evidence, not counted as another passing gate; the fixture confirmed
  its exact owned container stopped after a tmpfs-full server failure.

Business-logic compatibility evidence includes `memory-tool-units-final.xml`
(52 passed) and `memory-unit-compatibility-2.xml` (422 passed, 44 skipped). The
latter's test XML completed before managed-scope finalization returned EAGAIN;
the stopped scope was reconciled with unknown manager exit status. This is not
a successful lifecycle-manager command or a full backend-suite pass.

Ruff check and full basedpyright (`--level error`) passed after the backend
fixture changes. Feature-matrix validation passed (90 rows). The full offline
suite inventory and final formatter/catalogue reconciliation are separate gates;
their completion must be recorded, never inferred from these focused results.

### Finite compatibility repair — 9 November 2026

The earlier full offline inventory was interrupted by the primary, not restarted:
12,455 passed, 291 failed, 82 errors and 1,566 skipped. It predates several
fixture repairs and is not a current whole-suite verdict. Its XML/log remain
`memory-full-offline-inventory.{xml,log}` in the external evidence directory.

The streaming stall was reproduced with a 45-second SIGINT timeout and a
15-second faulthandler dump (`memory-finite-streaming-before.log`, lines 13,
69 and 311–330). The producer had already failed because its fake memory store
lacked `_database_pool`; the test's unbounded insert/token waiter never noticed
the failed task. The test now observes producer failure and bounds that wait,
without suppressing the cancellation or persistence assertions. Progressive
delivery doubles now provide the same original account/erasure observations.
This establishes that particular stall cause, not the cause of every delay in
the interrupted inventory.

Execution-path regressions found after repairing the doubles were also fixed:

- Temporal stream forwarding explicitly closes its inner generator before
  leaving capture/read scope. Early close immediately persists partial text as
  cancelled, never complete; both ordinary and historical branches are tested.
- Durable execution does not attempt `original_capture(None, ...)` when memory
  is absent. Explicit historical requests with no memory store still refuse
  before model dispatch or task mutation; no current-data fallback is added.
- The HTTP writer's forwarding dependency explicitly closes its inner compute
  scope. Its test checks that closing the dependency restores the absent outer
  compute scope, rather than leaking account-compute context.

Startup doubles return explicit boolean schema observations for migration 066;
they do not disable the runtime guard. Negative 063/066 cases remain enforced.
HTTP import doubles exercise the real post-commit observer and its actual
account/transaction guard protocol using an empty synthetic clock; no observer
or runtime guard is bypassed. Those doubles are not PostgreSQL qualification.

All pytest commands in this repair phase were finite per-file checks with
`timeout --signal=INT --kill-after=15s 120s` and
`-o faulthandler_timeout=30`; the initial 45-second reproduction is retained
separately. Final per-file XML/logs are `memory-finite-final-<test module>.*`:

| Final scoped qualification | Passed | Skipped |
| --- | ---: | ---: |
| Streaming persistence and progressive delivery | 105 | 0 |
| Context wrapper, startup and identity/session compatibility | 290 | 0 |
| Memory API, embedding, dreaming/hooks, blank inputs and summary units | 269 | 0 |
| Account-writer catalogue | 100 | 0 |
| Types/core/paths/native-durable real PostgreSQL (`memory-finite-postgres.xml`) | 32 | 0 |
| Total distinct cases in this finite scope | **796** | **0** |

The later `memory-finite-postclose-test_memories.*`,
`memory-finite-postclose-test_embedding_adapter.*` and
`memory-finite-postclose-test_account_writer_catalogue.*` supersede their
corresponding per-file runs after the final HTTP dependency closure change;
their repeated cases are not added to the total. Catalogue reconciliation
preserved all 512 unrelated writer entries and required no fixture edits.

Final static checks passed: full Ruff check and formatter check (879 files),
full `basedpyright --level error` (zero errors/warnings/notes), feature-matrix
validation (90 rows) and `git diff --check`. Their retained logs are
`memory-finite-{ruff,format,types,feature-matrix,diff-check}.log`.

Remaining inventory groups are intentionally not expanded into another full
run: `test_store.py` (22 old failures concerning its account/transaction doubles),
`test_memory_exposure_sweep.py` (7 old failures) and
`test_memory_retrieval_exactness.py` (13 old failures/5 errors) share a synthetic
SQL driver that still expects `memories` instead of the current-assertion view;
`test_entitlements_migration.py` has one old numbering-gap failure for reserved
migration 064. These are retained-inventory counts, not freshly rerun verdicts.
At that finite boundary, migration integration was primary-owned and renumbering
was not authorized. The subsequent approved upstream integration preserves actual
064/065 and moves the unapplied memory migration to 066; the contiguous-chain
gate remains unchanged. The counts above predate that integration.
Full project gates, independent final review and backup-restore erasure safety
remain outstanding; this finite green scope is not release clearance.

### Upstream integration and final review — 9 November 2026

Owner-authorized integration includes upstream
`2fd7a21b73eca7b347c4d0a41b34d4f65d5b3ef3`. Upstream migrations 064/065 are
preserved; the unapplied memory migration is now 066 with unchanged SQL. The
contiguous migration chain, reconciled writer catalogue, store/exposure/exactness,
native/durable PostgreSQL and startup checks passed: **767 distinct cases, zero
skips** in `integration-20261109/worker-checks.json`. The catalogue preserves the
upstream and feature classifications; two of the original 512 frozen entries
retain their metadata under upstream's `PacketOwner._handle` function rename.

Fresh read-only final review completed with a disclosed same-OpenAI-family
reviewer fallback and bounded caller coverage. Its one finding concerned local
source revisions in owner history. Primary adjudication preserves the existing
distinction: authenticated owner management/export can include local data, while
cloud retrieval cannot. History has no model/tool callers. A real-PostgreSQL
regression changes a source from cloud to local and verifies owner history/export
remain readable, cloud retrieval and foreign-owner history/export exclude it,
and deleting the source revokes history. It passed without a runtime policy
change (`final-gates-20261109/history-permission.xml`).

Full Ruff, formatting, types, blocking high-severity Bandit, dependency audit,
feature-matrix and pre-commit gates passed. Full Bandit inventory remains
non-blocking. All blocking frontend gates, including audit, unit tests and build,
passed; the non-blocking browser inventory had 31 passes and 25 failures, recorded
in [#490](https://github.com/sol-aeternum/Daemon/issues/490#issuecomment-6736357015).

The bounded final backend inventory reached its 900-second deadline with 6,413
passes, two cancellation-test failures and 1,254 skips, at roughly 46% progress.
Stack diagnostics show slow campaign persistence tests and continued progress,
not the earlier streaming waiter stall. Remaining cases and the two failures are
being qualified separately; this is **not a passing whole-suite gate**. No commit,
production migration, deployment or backup-restore clearance is claimed.

### Final local qualification

The subsequent uninterrupted bounded backend run completed successfully in
740.84 seconds: **13,979 passed, 2,395 skipped, two expected failures and 16
passing subtests**, with 34 warnings (`final-gates-20261109/pytest-final.xml`).
This supersedes the interrupted whole-suite gate above. Opt-in service/provider
skips and expected failures are not claimed as exercised paths. The intervening
deduplicated continuation is supporting evidence, not added to these totals.

The final three fixture repairs retain actual body/rate-limit checks, pin GC's
qualified candidate query and owner-specific hard-delete calls, and assert
nonzero original erasure epochs at summary publication. No runtime changes were
needed for those failures. After staging new files, secret scanning identified
hard-coded synthetic test peppers; these were replaced by generated per-test
values without scanner exemptions. All 13 affected real-PostgreSQL cases passed
again (`generated-pepper-postgres.xml`); the production implementation is
unchanged from the full-suite run and final source review.

Migration deployment remains separately gated: apply 066 only with the matching
binary, retain history on rollback and keep incompatible writers stopped. The
backup-restore erasure limitation, historical-answer/model quality and production
capacity remain unqualified. No commit, production migration or deployment was
performed. The non-blocking browser failures remain tracked in #490.
