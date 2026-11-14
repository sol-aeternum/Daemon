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

### PR #693 review repairs — bounded qualification

The review repairs preserve migration 066 and public contracts: extraction logs
use the existing NOT NULL-compatible empty-string scrub; nested tools reuse their
owner-qualified originating capture; direct/combined/L0/component retrieval logs
carry a capture from before candidate reads and publish inside the existing
erasure-serialized mutation transaction. Logs do not create assertion revisions.
Historical BM25 retains any-term matching when requested, consolidation discovery
carries exact parent revision IDs, and ordinary summaries select current assertion
segments rather than future projection content. Scope A still preserves original
conversation bodies; backup/restore and deployment restrictions above remain.

Synthetic PostgreSQL regressions in `tests/test_memory_pr693_postgres.py` first
reproduced every reported defect (13 failures, three passing controls;
`pr693-before.xml`). The expanded final core/execution batch passed 36 cases with
zero skips (`pr693-final-core-execution.xml`), including pre-read captures,
event-controlled detached/buffered erasure races, an actual PostgreSQL lock wait
through log commit, nonzero stale epochs, owner/generation refusals, historical
qualification before LIMIT, consolidation dependencies and ordinary prompt-summary
selection. Evidence is retained outside the repository under the registered
`memory-temporal-pr` task. This is not a full-project gate or final-review claim.

CI's 26 fixture incompatibilities are addressed using initialized synthetic
accounts and registered mutation connections, not disabled triggers. The lexical
and rollback modules passed 13 cases, trust passed five, and entity producers
passed 14 recording variants. Subsequent owned-Redis qualification passed all
14 previously skipped entity variants plus the native acceptance lost-wakeup
case (`pr693-owned-redis-entity-live.xml`: 15 passed, zero skips). The existing
digest-qualified cached Redis fixture remained network-none; a one-shot private
loopback relay supplied the unchanged legacy opt-in flags, and exact container
removal was recorded in `pr693-entity-redis-receipt.json`. The initial benchmark
fixture-only workaround is superseded by the actual-helper qualification below;
no excluded benchmark checkout or real benchmark run is changed or executed.
The owner's later explicit fail-closed choice resolves the two missing-account
request-bound compatibility cases: tests retain the actual stream/capture guard
and require truthful terminal failure, no provider dispatch, assistant/derived
publication, task enqueue or account provisioning. See the scoped superseding
clarification in `docs/ACCOUNT_CONTROL_PERSISTENCE.md`; this is not an all-task
fence or deployment claim. Both revised request-bound cases and ten adjacent
durable acceptance refusals passed on real PostgreSQL
(`pr693-missing-account-owner-choice.xml`: 12 passed, zero skips). The subsequent
complete acceptance module, including owned Redis, passed all 57 cases without
skips (`pr693-acceptance-final-owned.xml`).

### Approved synthetic benchmark reset compatibility

The owner approved the narrow actual-helper repair, not account reset or a
temporal rewind. `bench_db.py` now checks the marked database and exact synthetic
owner, retains the originating capture, and owns the actual READ COMMITTED
account-guarded/temporal-barrier transaction. Orphan rollback erases lineages and
derived copies before deleting synthetic source conversations. Full content reset
also purges uncascaded dependency edges; revision segments and sources cascade.
No account generation is advanced and no spend/ledger records are removed.

Knowledge clocks, mutation history, tombstones and erased source spans remain
monotonic. Even an empty-payload reset advances the existing erasure epoch to
reject pre-reset captures. Owner-qualified fingerprints now include immutable
bundles, joined segments, dependencies, sources and retained protection, using
their deterministic keys. An interrupted session whose complete earlier state
cannot be proven restored causes a conservative question rebuild, including the
self-contained case; no clock rewind or falsely exact baseline is permitted.
Payload-zero checks and `reset_verify_helper.py` explicitly distinguish retained
deletion protection from leftover content.

Canonical reset commits per owner, reports completed versus unconfirmed owners,
and unlinks its checkpoint only after acknowledged database completion. SQL
failure, cancellation or uncertain COMMIT leaves checkpoint evidence intact;
checkpoint failure after an acknowledged purge does not imply rollback or replay.
There is no new global transaction, production-erasure policy or deployment claim.

The masking test transaction wrapper was removed. Actual raw-pool helper tests
and runner reconciliation passed **29 real-PostgreSQL cases**, plus two
no-replay confirmation units, **31 cases with zero skips**
(`pr693-benchmark-reset-qualified.xml`), covering lineage/derived cleanup,
uncascaded edges, preserved account/spend/foreign owners, stale captures and old
watermarks, temporal-only fingerprints, partial-owner failure, deferred failed
COMMIT, lost COMMIT receipt, cancellation and checkpoint failure. Source and
guarded SQL are real; the lost-receipt wrapper raises only after an actual commit.
These are synthetic qualification cases, not benchmark inference or restored
backup safety. The verifier refuses automatic confirmation after an uncertain
reset or unexplained payload leftovers. A further 329 affected unit/catalogue
checks passed (`pr693-benchmark-compat-final.xml`); full Ruff/format/types and
focused high-severity Bandit also passed. Expanded fingerprints intentionally
do not certify pre-066 pinned baselines; no frozen evidence was rewritten.
Primary final review and full project gates remain separate.

### PR #693 follow-up — diagnostic bookkeeping and checkpoint publication

The default benchmark fingerprint compares protected evaluation state, not a
byte-for-byte recovery image. Erasure-fenced retrieval diagnostics can advance
the clock sequence/time, append assertion-free mutations and seal post-commit
observations without changing retrieval inputs. Those bookkeeping changes are
excluded. The fingerprint retains current account generation/restrictions,
erasure epoch, clock-regression status, revision-bearing mutation identity and
write lower bounds, complete revisions/segments, dependencies, sources,
tombstones, erased spans, and existing ranking/locality/source inputs. An absent
clock and its first zero-epoch diagnostic clock normalize alike. A missing or
rewound clock below existing assertion revisions remains invalid protected state.
This does not certify unchanged historical wall-clock bounds; diagnostic exclusion neither
rewinds nor deletes any clock/mutation/protection records. Orphan erasure still
advances the epoch and forces conservative rebuild when restoration is inexact.
No state-mismatch override or frozen benchmark evidence is changed.

Migration 066 deduplicates legacy owner/generation pairs before evaluating the
volatile baseline timestamp. Multiple memories per owner therefore share one
baseline mutation without inventing earlier knowledge or suppressing conflicts.

Queued extraction checkpoints now use the same account-first, ordered temporal
mutation boundary as fact commits, with the original input erasure epoch. The
barrier precedes source locks, owner/conversation arguments must match the input
capture, and the log/cursor is returned only after acknowledged commit. Erasure
either scrubs an earlier checkpoint or rejects stale later publication, including
when the last fact has committed but an unchanged NULL summary and trace-free
source messages would otherwise pass source validation. No generation recapture,
schema/API expansion, global account fence or deployment is implied.

The final focused owned-PostgreSQL batch passed **129 cases, with 19 Redis-opt-in
skips** (`pr693-followup-protected-clock-pg.xml`), including missing/rewound
assertion-clock controls. The twelve named follow-up evaluation,
checkpoint-race/scope, migration and bookkeeping cases passed without skips.
The 19 Redis-opt-in cases subsequently passed against the owned disposable Redis
fixture (`pr693-followup-fact-live.xml`), with zero skips.
Actual runner/retrieval/logger tests script inference only, compare the original
ingest fingerprint on resume without an override, and cover logging before the
first memory clock exists. The runner's fixture-pool startup and compute scope
are injected; this is not provider-attestation or startup qualification. Both
final-fact race orders use the actual extraction path; the checkpoint-first case
observes PostgreSQL's wait-for edge before release. Deferred COMMIT failures also
verify rollback of clock/mutation state. Orphan/rebuild controls remain covered.
Offline runner/parity/catalogue compatibility passed **198 cases without skips**
(`pr693-followup-unit-final.xml`). Evidence is retained outside the repository
under the registered `memory-temporal-pr` task. Full-suite verification on the
new head and fresh final review remain primary-owned, separate gates; no
deployment or real benchmark inference is claimed.

### #601 prospective correction admission — partial foundation

Source-only migration 071 (`071_message_correction_admission.sql`) adds a managed
`messages.source_revision`, immutable nullable `correction_origin_generation`,
and the first complete `correction_source_revision`. Existing messages receive
only a baseline source version, never eligibility. Generic/import/replayed rows,
assistant/system/tool rows and contextual-home copied prompts remain ineligible.
Native live-user insertion and the shared durable acceptance insertion use the
explicit `MemoryStore.insert_live_user_message` entry point with the original
server-captured account generation. The helper takes the account guard before
insertion; durable acceptance retains its existing account-first outer transaction
and revalidates on that same connection without opening a nested transaction.
An admission refusal does not authorize native no-memory execution through its
generic persistence-degradation path; the existing sanitized SSE error path is used.

The ordinary-invoker database trigger validates admitted user/conversation owner
matching and rejects origin/version/frozen-version changes. Identity, role, owner
and conversation are immutable only for admitted rows. Every changed field is
semantic by default except the closed token-counter allowlist; ciphertext,
status, metadata, traces and future columns therefore advance the source version.
No-op SQL and token maintenance do not. Only exact `complete` freezes the first
version, including at insertion; NULL/`completed` legacy statuses do not. Later
edits or status cycling cannot refresh the frozen version, including content ABA.
An unequal frozen/current revision cannot qualify a future correction claim.
Future claim consumers must enforce that equality; none is implemented here.

This is the approved **trusted-backend** boundary, not authenticated SQL-callsite
provenance: a same-database privileged writer can forge birth admission. No GUC,
new credential, role, grant or privilege escalation purports to prevent that.
Account checks stay in the account-first helper, not a late message trigger that
could invert lock ordering. Matching schema/trigger startup qualification includes
071. Rollback is `migrations/rollback/071_message_correction_admission.down.sql`;
it drops the three columns and their data. No correction operation/receipt table, segment dependency, reader or erasure
integration is implemented by this foundation. Focused synthetic qualification
passed 148 real-PostgreSQL cases and 246 offline cases; one separately opt-in Redis
wake-up case remains skipped. Final static checks passed (Ruff/format, full types,
high-severity Bandit, feature matrix and diff whitespace). Evidence is retained in
`pr601-natural-fix/admission-foundation-results.md` under the registered task's
external evidence directory. Independent final review and full project gates remain
required; this is not correction completion or deployment clearance.

### #601 versioned summary direct-source foundation — 11 November 2026

Owner-approved option 2 adds source-only migration
`072_summary_provenance.sql` and `memory/summary_provenance.py`. Rollback is
`migrations/rollback/072_summary_provenance.down.sql`; it drops the versions,
closures and payloads, and conversations whose summary had been published are
left without one until it is regenerated. Its Scope A
boundary is directly stored source-message bodies, not a complete
assistant prompt manifest or certification of transitive memory copies inside
assistant prose. It does not implement natural correction claims or change M2.
Source selection retains the existing finalized-message role policy, including
stored system/tool rows; it does not certify their upstream derivations. The
primary adjudicated this as preserved behavior, not a new user/assistant-only
filter. Ownership, locality, exact versions and suppression qualify every role.

Legacy `conversations.summary` text is never a parent or fallback: without a
qualified head, rebuilding starts from the original source prefix with baseline
zero. Each immutable sealed version records the account generation, parent,
checkpoint and rendered-message identities/revisions. Incremental publication
flattens the parent's direct-source closure together with the newly rendered
messages, rather than depending on retained parent plaintext. A wholly suppressed
prefix can advance a checkpoint-only version without a payload or model call.

`conversation_summary_versions` and `conversation_summary_sources` retain
provenance metadata. New text lives only in the Fernet-encrypted
`conversation_summary_payloads` table; publishing clears the legacy plaintext
column and removes replaced payloads. This is a replaceable cache, not a summary
history API or an indefinite ciphertext-retention promise. All three tables are
account-owned, explicitly classified in the writer catalogue and included in
synthetic reset/fingerprint accounting. These classifications do not complete the
separate account-reset protocol.

Qualification checks the current owner, account restrictions/generation, source
existence/revision/status, current cloud locality where required, sealing,
revocation and source suppression before decrypting a payload. Owner management
reads can include local conversations; cloud readers cannot. Extraction retains
and rechecks qualified references, holding every summary-source message lock
through its publication transaction, including sources outside the current chunk.
Owner-approved skill generation omits conversation summaries until skill files
and projections have guarded dependency publication and erasure integration; it
does not fall back to legacy text. Inline and
queued producers release input transactions before scripted/provider work and
recheck their frozen inputs before dispatch and publication. Head/state-revision
CAS, cursor/prefix membership, source revisions and the original erasure epoch
reject stale publication, including ABA and source-row lock waits; failed COMMIT
rolls back the version, closure, payload and head together.

Scope A erasure conservatively revokes governed summary versions, purges their
payloads and clears summary heads/cursors without rewriting original conversation
bodies. Conversation deletion also invalidates closures; account cascade removes
the retained metadata. Rebuilds respect erased-source suppression. This does not
solve the backup-restore erasure restriction above or recall already disclosed
provider/client context.

Residual synthetic evidence is retained under the registered task's external
`pr601-natural-fix/summary-provenance-results.md`: **555 distinct passed cases,
20 explicit disposable-Redis opt-in skips**, with no unresolved test failures in
this bounded set. This includes 138 summary/reset PostgreSQL cases, 142 passing
extraction/account cases reconciled against the targeted repair, 40 resource and
startup PostgreSQL cases, and 235 offline cases. Failed initial compatibility
runs are preserved, not counted as passing gates; duplicate repair cases are not
added twice. Narrow resource/startup fixtures execute their pinned prerequisite
schemas plus the actual memory/admission/summary migrations, not a claim of a
full production migration replay. Full Ruff/format, basedpyright, high-severity
Bandit and feature-matrix checks passed after the final code edits. Full project
gates, review-repair verification and release/restore
restrictions remain primary-owned; no inference, deployment or production DB ran.
