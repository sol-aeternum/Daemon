# J1b — account-control request facts

Owner-approved source-only slice, 12 October 2026, after merged #513. Governing
contracts remain [account deletion §§4/6.6/10](ACCOUNT_DELETION_DESIGN.md) and
[J1 ordering/recovery §§3–5](DELETION_JOURNAL_J1_PROPOSAL.md). This is not deletion,
reset, deployment or enablement qualification; #477 remains the enablement gate.
Settled D8/R1/R2/R3/S1/J1 decisions and completed P0 prerequisites are unchanged.

## Source and schema boundary

[`047_account_control_persistence.sql`](../migrations/047_account_control_persistence.sql)
contains exactly the approved two tables and account generation column:

- `account_deletions`: stable request operation/event identity, deployment journal
  id/sequence/digests, kind, original request time, prior status/reason code,
  recorded fence generation, drain/revocation/billing deadlines, zero-grace reason
  and reserved `closed_at`. A partial unique index permits one open operation per
  account. No user/entitlement FK, so records do not cascade; no applied-marker FK.
  There is **no closure or phase API** in J1b.
- `account_suspension_causes`: account-qualified opaque cause UUID/code pairs.
  Exact-pair removal cannot clear unrelated billing, abuse or operator causes.
  These rows cascade only with the entitlement account; the original immutable
  cause snapshot remains in the request's authoritative journal frame.
- `entitlement_accounts.content_generation`: recorded account content generation,
  default zero. It is distinct from `tasks.content_generation` (attempt-output
  lease epoch). J1b itself adds no task/auth/client integration; J1c-1b below
  consumes it for generated titles; J1c-1c below extends it to conversation summaries.

Only opaque identifiers, closed codes, hashes, generations and original times
are stored. No content, destination, secret, report or external-resource registry.
No live migration, legacy backfill, provisioning or configuration change occurred.

## Internal contracts

`orchestrator/services/account_control/store.py` has no runtime callers.

1. `lock_account_control(conn, account_id, operation_id=None)` requires a caller
   transaction: domain-separated SHA-256-to-int64 account advisory lock, entitlement
   `FOR UPDATE`, then operation `FOR UPDATE`. Full UUID identity is checked;
   advisory collisions only serialize extra accounts. Missing entitlement fails,
   never creates an account. Scope/connection/transaction identity is checked and
   locks reacquired after possible savepoint rollback. The helper never commits
   its caller's transaction.
2. The yielded scope's `verify_request(journal, event_id)` performs an actual
   durable full journal scan under those locks, checks deployment binding and
   exact marker identities/digests, and resolves the original request frame.
   Bare entries or caller-supplied views are not verification. The resulting
   concrete input expires with its scope/transaction and is persistence provenance
   **only**, not a completed-fence certificate. Filesystem I/O finishes cleanup
   before cancellation returns; no live file lock/descriptor is abandoned.
3. `record_request_facts(conn, verified_entry)` atomically records only
   `requested`/`reset_requested` facts, adds missing recorded cause pairs without
   deleting existing ones, and installs the recorded generation. A savepoint
   prevents partial helper writes even if its caller catches an SQL failure.
   Contradictory identities/facts, extra/conflicting causes and equal/newer DB
   generation without the corresponding operation record fail closed. An exact
   existing request record returns unchanged; retry never rewinds later causes,
   generation or closure metadata. Private cause primitives never set status.

Append is still journal-first. A future producer must hold the account/operation
locks, reconcile **all** earlier account entries, validate, durably append, then
perform its DB/effect transition and exact marker transaction before acknowledging.
The POSIX lock is never held while waiting for a DB lock or commit. J1b has no
append producer or effect handler; tests use explicit test-only drivers.

## Retry, older backups and unresolved effects

Stable event/operation UUIDs and the existing command digest resolve the original
frame **before** mutable prior-state facts are recomputed. Lost commit response
does not allocate a second operation, generation or deadline. Existing status-only
suspensions do not establish complete cause provenance: do not invent a cause id
or infer a set from a prior reason. Trusted recorded cause pairs can be restored
  from the request frame into an older snapshot; unrelated existing pairs are never
  silently erased. Production suspension writers remain unchanged and unconverted.
An older snapshot may still have status `active` when its authoritative request
recorded a prior billing/abuse/operator suspension: J1b restores those cause facts
but leaves status unchanged and the request unapplied, with startup still blocked.

First projection uses the exact recorded generation; a contradictory newer DB
state is refused rather than rewound. New allocation must eventually exceed both
DB and every journaled account generation, including terminal rebuild generations;
J1b adds no allocation producer. Original deadlines are never extended on retry
or restore. Grace/report-ready windows remain in their existing journal events;
J1b neither fabricates them at request time nor creates a phase/report API.

Earlier account entries lacking exact applied markers block a later request.
J1b can record/re-record the same pending request, **not apply its effects**.
Request rows, timestamps, current generation or latest terminal state are not
application evidence. Full multi-operation replay requires later concrete
terminal/cancellation/cutoff handlers and must retain every original entry.

**All J1b rows committed still does not constitute a completed request fence.**
There is no applied-marker writer or startup-barrier change. Before a later handler
may commit a request marker, required task cancellation, suspension, session
revocation/restriction, transactional guards on every writer and S1 namespace
retirement with durable manifest/mapping reconciliation must be implemented and
verified. No partial-success marker, readiness release or runtime producer bypass.
Unknown events (including unsupported suspension changes) and incompletely applied
entries still block startup. Mandatory PostgreSQL and J1a timeout cleanup remain.
All three #513 request-cause/zero-grace/purge-cause validation repairs are preserved.

## Acceptance boundaries

`tests/test_account_control_persistence.py` uses real disposable PostgreSQL schemas,
POSIX journals and process boundaries: concurrent requests, retry identity,
append/commit crashes and lost replies, consistent COPY snapshots before requests
and between append/commit, immutable request deadlines/generation, exact-pair cause
preservation, provenance/transaction expiry, savepoint atomicity, advisory collision
isolation and real startup refusal with request facts committed but no markers.
Shutdown-cancellation coverage uses an owned process and verifies filesystem work
finishes before descriptors can close, without a cancellable child-task spin.
Existing journal repair/ready-window suites retain their independent assertions.

These prove persistence mechanics, **not** cancellation, restricted sessions,
stale-worker refusal, reset content cutoff, terminal races, artifact retirement,
purge, billing/provider outcomes or complete restore readiness. No public endpoint,
UI, live migration/provisioning, artifact implementation, pruning, deployment,
flag enablement or dependency change. Repository gates and independent final-source
review govern qualification; source existence alone is not release clearance.

## J1c-1a — uncalled content-writer prerequisite

Owner-approved bounded source slice, 13 October 2026:
[`guard.py`](../orchestrator/services/account_control/guard.py) adds immutable
account-generation capture and a caller-owned transactional `FOR SHARE` scope.
At the #521 prerequisite merge, **no runtime writer called it**; the bounded
generated-title integration below is the first caller. This is not an account fence, request applier,
applied-marker writer or readiness change; #477 remains open. Migration 047,
schema/API/env/dependencies and the settled S1/J1 decisions are unchanged.

Capture before reading/deriving inputs or provider work; never refresh an old
generation to cure refusal. The captured UUID/generation is an internal value,
not authentication, row-ownership proof or write authority. Acquire the guard on
the same caller connection **before** content/task/identity locks and retain the
transaction through the content commit. Only READ COMMITTED is supported: acquire
the account row, check active status/exact generation, then check open operations
and causes in a **separate post-lock statement**. This avoids a pre-wait snapshot
missing subordinate-only commits. Stronger snapshot isolation refuses rather
than assuming an entitlement-row serialization failure detects those changes.

Missing accounts, schema/read failures, suspension (including unknown legacy
cause provenance), active-with-causes, open operations and stale generations deny
permission. The helper never provisions, activates, allocates generations,
commits/rolls back caller data, upgrades to UPDATE or offers a privileged bypass.
Control/settlement/late-evidence writers need separately approved narrow APIs.
State-changing control writers must first lock the same account `FOR UPDATE`
(existing J1b account-control ordering); a subordinate FK lock alone is not enough
to serialize cause/operation changes against this cooperating-writer guard.

The scope checks connection, captured account/generation, outer transaction and
live lifetime. Savepoint rollback can release its locks without ending the outer
transaction: callers **must revalidate before subsequent writes**, reacquiring
SHARE and checking state. A Python context cannot intercept arbitrary raw SQL;
this is a cooperating-caller contract, not a sandbox. Scope exit invalidates the
API authority but does not release the transaction lock or commit caller writes.
On refusal/cancellation the caller owns transaction rollback.

[`test_account_write_guard.py`](../tests/test_account_write_guard.py) exercises
real disposable PostgreSQL lock ordering, post-wait cause visibility, stale
generation, snapshot-isolation refusal, scope/savepoint lifetime, cancellation,
owned writer death and real POSIX append/failed-commit/retry composition. Startup
without applied markers still refuses in the real backend/worker composition
tests above. These tests require an owned `TASKS_TEST_DATABASE_URL`; skipped
PostgreSQL cases do not qualify the prerequisite.

The explicit writer/table catalogue in
[`account_writer_catalogue.py`](../tests/account_writer_catalogue.py) and its
fixture/tests is bounded source-change detection, **not complete runtime
enforcement**. Content/control/shared-unowned/deferred classifications record
historical FK dependencies, semantic ownership notes and uncovered paths, not
authorization from FK reachability alone. Unsupported table renames and
quoted/schema-qualified declarations fail closed and require explicit scanner/
manifest review. Classification itself fences nothing. Later
generation propagation for other writers, account-first lock conversions, ownership fixes,
restricted sessions, non-DB/Redis/SSE publication and durable S1 retirement are
still dependencies. No guard test or classified deferred writer permits an
applied marker or releases the fail-closed startup barrier.

An append committed to the journal but not yet projected into PostgreSQL is not
observable to this DB-only kernel. The startup barrier still refuses that case;
runtime production/applier integration cannot be inferred from this prerequisite.

## J1c-1b — generated conversation titles only

Owner-approved internal payload and terminal legacy-refusal contract, 13 October
2026. The two existing title workers now converge on the guarded SQL in
`MemoryStore.save_generated_conversation_title`. This is source integration, not
deployment, a complete account fence or permission to enable durable chat.
**#477 stays open.** No schema, migration, HTTP/SSE, environment, dependency,
session, retirement or request-application change.

Native chat captures the authenticated account UUID and
`entitlement_accounts.content_generation` once, before deriving source inputs.
Both request-bound and durable-acceptance title producers serialize
`origin_account_id` and `origin_account_generation` alongside conversation/message
identifiers. Capture failure suppresses optional title scheduling without changing
chat acceptance. Enqueue delay and ARQ retries retain that original generation;
neither worker rediscovers an owner or recaptures to cure a stale job. Missing,
malformed, boolean, negative and out-of-range captures are terminally refused
before input reads, compute or inference. No legacy-job upgrade or title backfill.
The task-attempt counter `tasks.content_generation` is never used for this family.

Title preflight uses the full account guard, not the weaker capture read: one
short READ COMMITTED transaction acquires account SHARE first, validates current
state/generation and subordinate facts, then reads only owner-qualified title
metadata and messages on that same connection. The exact-message path requires a
user message belonging to both the captured account and conversation; the history
path retains its last-50 ordering and user/assistant filter, excluding foreign
message rows even in historically inconsistent conversations. Message content is
decrypted only after SQL ownership qualification. Locked titles skip inference.

Preflight commits before compute admission. A second short guarded check after
admission rechecks account/ownership/title state before the model; no account
lock is held across compute admission or provider work. The final save opens its
own connection and READ COMMITTED transaction, acquires account SHARE **before**
the conversation update, and retains it through commit. SQL requires the captured
owner, unchanged expected title and an unlocked title. Guard/database failure or
cancellation never authorizes a write or reports an uncommitted title as saved;
transaction contexts own rollback and connection release. Model failures may
retry with the same original payload; stale/legacy refusals do not retry.

`tests/test_title_account_guard_postgres.py` exercises the real input/writer SQL
and both worker entry points against owned disposable PostgreSQL, stubbing only
compute/model boundaries. It includes serialized retry currency, nonzero account
generations, both HTTP producers, foreign conversations/messages, restricted and
contradictory account state, compute/model interleavings, observed PostgreSQL
wait-for edges in both fence orderings, cancellation/connection reuse, failed real
commits and manual rename/title-lock protections. Existing listing/title suites
retain their nullable-title and lock-wait regressions. PostgreSQL skips are not
qualification. The catalogue records only this explicit SQL site's bounded
integration; scanner metadata is not a runtime proof.

Limits remain deliberate: a provider call already in flight is not recalled, and
the last pre-model check is not atomic authority over later network dispatch.
Initial conversation titles, manual renames, general task outputs,
memory, sessions, Redis/publication paths, workspace retirement and every other
writer remain uncovered. Control writers must still use account UPDATE-first
ordering. The DB-only guard still cannot observe an unprojected durable journal
append; no request applier, applied marker or startup-readiness release is added.

## J1c-1c — conversation summary writers only

Owner-requested bounded extension of merged #522. Native chat's original
authenticated capture now accompanies extraction base/follow-up enqueues and the
existing encrypted durable-task input. Resumed attempts, extraction chunk
continuations, summary continuations and ARQ retries retain that exact account
UUID/generation. `tasks.content_generation` remains the task-attempt epoch, not
the account origin. No capture occurs in a resumed task, stream finalizer or job.
At the #523 merge, historical/malformed/foreign-origin extraction payloads skipped
**only summary work**. The fact/checkpoint extension below now refuses the entire
queued extraction persistence family for invalid original captures.
Historical task input and direct stream callers without a capture similarly
suppress optional summaries; summary-only legacy jobs terminate before inputs.

Both the 20-row inline and 100-row queued paths use
`MemoryStore.read_conversation_summary_inputs`: a short READ COMMITTED transaction
locks account SHARE first, verifies full account state/generation and qualifies
the conversation owner before decrypting owner-qualified messages. Conversation
SHARE stabilizes the summary version during preflight; message ranks, contiguous
prefix counts and bounded batch share one SQL statement snapshot. Foreign rows
cannot shift cursor ranks or block the owner prefix. Terminal error/cancelled rows
consume no summary position; streaming/unknown rows still stop the prefix. The
cursor advances only by incorporated rows, capped at that contiguous prefix.

No lock spans compute admission, model calls or Redis. Queued work rechecks
account/owner/summary version after admission; inline work inherits extraction's
existing admitted scope and rechecks immediately before inference. Final
`update_conversation_summary` requires the original capture and guards account
SHARE before the owner-qualified optimistic UPDATE. Summary text, timestamps,
cursor, snapshot and continuation flag commit together; success is reported only
after commit. Account refusal is not a CAS conflict and does not authorize a new
continuation. In-flight provider requests are not recalled; stale results cannot
commit.

Recovery now **observes rather than consumes** `summary_continuation_pending`.
Enqueue failure, uncertain reply or cancellation leaves the flag set. Full queued
batches persist it with their summary before enqueue; retry can drain the remaining
tail. Guarded summary progress recomputes it, while a streaming hole retains it.
The no-op clearing writer requires the observed summary timestamp/cursor and SQL
proof that the owner has no mutable hole or unsummarized finalized messages. A
stale retry cannot clear a new generation's flag; competing progress cannot be
erased by an old no-op. Duplicate recovery enqueues are safe under summary CAS.

`tests/test_summary_account_guard_postgres.py` exercises actual guarded input and
writer SQL on disposable PostgreSQL: original-origin propagation through HTTP
acceptance and resumed tasks, stale retries, legacy summary-only suppression,
owner/message qualification, admission/provider races, streaming holes/terminal
states, competing writes/no-op clearing, both observed lock-wait orderings,
cancellation/connection reuse, repeated enqueue failures and failed real commits.
Only synthetic fixtures and scripted compute/model boundaries are used; skipped
PostgreSQL tests are not qualification. Existing summary/title regressions and
project gates remain required. The catalogue identifies only the two bounded
summary SQL sites, not complete runtime account-writer coverage.

This adds no memory-fact fence, task-output fence, request/deletion applier,
schema/API/SSE/env/dependency change, marker, namespace retirement, deployment or
enablement claim. Generic conversation updates and other writers remain uncovered.
The DB-only kernel cannot observe an unprojected journal append; control writers
must still use account UPDATE-first ordering and startup stays fail closed.
**#477 remains open.**

## J1c-1d — queued entity projection only

Owner-approved bounded extension after merged #523, 13 October 2026. The owner
approved terminal legacy/invalid/foreign-origin refusal, one atomic planned-batch
transaction, terminal stale-plan refusal without automatic replan, the existing
background compute scope (including otherwise-admitted ambiguous confirmations),
and processing only eligible owner-qualified source rows. This is source-only
integration, not deletion/reset, deployment or enablement qualification.

Extraction's original account UUID/generation accompanies both successful and
failed-suffix entity enqueues. Extraction continuations and serialized ARQ retries
retain it; a later summary refusal does not replace this independent use of the
same origin. No entity job recaptures an account or uses `tasks.content_generation`.
Missing/malformed/foreign captures and malformed identifier lists skip optional
projection before reads/admission/providers. Fact writes/checkpoints were outside
this slice at #525; their bounded extension is below. Input/reference bounds are internal constants in
[`entity_projection.py`](../orchestrator/memory/entity_projection.py), not new config.

Preflight uses account-first READ COMMITTED and SQL owner qualification before
decryption. Sources must be active/open and explicitly nonlocal; referenced
conversations must be owner-qualified cloud sources. Explicit nonlocal manual/import
records without a conversation remain eligible; missing, foreign, local or unknown
required provenance is excluded. Canonical names additionally require an eligible
primary source and eligible ownership/locality for **every** recorded linked memory.
One cloud link cannot authorize mixed lineage. Excluded canonical/lineage content is
never decrypted for this job. Recorded references are conservative eligibility
evidence, not proof that historical lineage is complete or globally repaired.

Immutable in-memory snapshots retain encrypted source/canonical state and provenance
dependencies. Preflight ends before background compute admission; a second guarded
check after admission refuses changed inputs before embeddings/model work. Planning
uses those qualified canonical names without hidden generic-store reads. Existing
thresholds, rejection/completeness rules and first-source merge grouping remain;
the selected dedicated embedding route's existing legacy-consumer refusal remains.
No route, embedding identity or provider is activated. No locks span compute,
embedding/model calls or Redis. In-flight provider requests cannot be recalled.

Final persistence requires original capture and snapshots, acquires account SHARE
on the **actual writer connection** before deterministic conversation/source/entity
row locks, and retains them through the batch commit. It revalidates all consumed
canonical rows, including nonwinning candidates, and their source dependencies.
Insertions, aliases and source-memory links commit together; stale plans, failed SQL,
failed commits and cancellation cannot return successful counts. An unexpected
same-lookup conflict refuses the whole batch rather than overwriting aliases or
refreshing/replanning. Repeated keys created within the same transaction can share
that new entity's qualified links. Empty aliases use the encrypted collection
representation, not SQL NULL. Concurrent stale alias plans cannot erase newer data.

[`test_entity_account_guard_postgres.py`](../tests/test_entity_account_guard_postgres.py)
and [`test_entity_producer_postgres.py`](../tests/test_entity_producer_postgres.py)
exercise actual disposable PostgreSQL input/writer SQL, native enqueue adapters and
worker entry points with synthetic data/scripted providers. Qualification requires
non-skipped owner/state/lineage refusals, original origin across HTTP/task/extraction
carriers, admission/planning edits, duplicate/concurrent writers, observed wait-for
edges in both account/control orderings, cancellation/failed commit/pool reuse and
whole-batch atomicity. Existing title/summary/extraction regressions and full project
gates remain mandatory; mocks or catalogue metadata do not qualify runtime fencing.

Only the three SQL sites in the queued `save_plan` writer are recorded as integrated.
Generic `MemoryStore` entity methods, retrieval, memory-fact writers, task outputs,
sessions, request/deletion appliers, markers, readiness and retirement remain
uncovered/out of scope. Generic insertion's pre-existing NULL-alias issue is tracked
separately in #524; this queued writer avoids it without claiming all APIs repaired.
No schema/API/SSE/env/dependency change. Control writers still require account
UPDATE-first ordering; unprojected journal intent remains outside this DB-only
guard's visibility. Startup remains fail closed and **#477 remains open**.

## J1c-1e — queued production facts and extraction checkpoints

Source-only extension after #525 under the owner's conservative policies. The
existing original account UUID/generation carrier now governs queued extraction
facts and every extraction log/checkpoint, not only its optional summaries and
entities. Invalid, legacy, malformed, foreign and stale original captures refuse
the persistence family; retries never capture a replacement generation. The
task-attempt epoch remains separate. No schema/API/SSE/environment/dependency,
retention, account-control producer, marker, readiness or deployment change.

`orchestrator/memory/extraction_fencing.py` supplies bounded account-first READ
COMMITTED input and writer transactions. Conversation/message ownership and
explicit cloud locality are SQL-qualified before decryption, independent of
embedding selection. Arbitrary raw/encrypted legacy job snapshots are refused;
new continuations use source-version references. Reference ranges cannot jump
mutable holes or omit uncheckpointed finalized rows. Same-generation changed
sources retain the approved at-least-once fragment restart; account generations
are never refreshed. Already-checkpointed reference prefixes are not re-extracted.

Consumed encrypted message state, provenance and summary context are rechecked
after admission/provider work and before persistence. Candidate discovery qualifies
canonical ownership/nonlocality and conversation provenance before decrypting.
Explicit nonlocal manual/import/user-created facts may have no conversation;
conversation-derived missing, foreign, local or unknown provenance is excluded.
Each fact plans outside locks, then uses its own guarded transaction through
commit for canonical touch or conservative insert/exact-conflict reuse. Changed
semantic matches retain safe conservative insertion; unsafe changed provenance
refuses. All consumed candidates' privacy evidence is checked, not only winners.
Late exact-conflict winners are qualified/locked before decrypting; NOWAIT on late
locks prevents reverse-order waiting and preserves the established suffix retry.
Later same-batch plans see earlier committed facts; this is not global exactly-once
semantic deduplication or the entity writer's whole-batch transaction contract.

Every fact, no-fact, fragment and filtered-page log is independently guarded on
its actual writer connection. Checkpoints advance only after successful commits;
partial failures retain the committed prefix without crossing the failed suffix.
Prefix evidence is recorded after commit, and eligible exact reuse can recover
projection scheduling on retries without counting reused rows as new. A changed
later source can schedule only its independently requalified committed prefix,
not the refused suffix; stale/restricted account currency still prevents that
enqueue. Every entity enqueue requalifies committed fact IDs; successful prefixes
are projected before optional tail checkpoints or later source fences can refuse.
Continuation references cover the complete uncheckpointed cursor range, including
leading filtered rows; their job identity follows the remaining selected fragment.
Malformed reference shapes, duplicates, versions and fragment indices refuse
terminally rather than exhausting retries; qualified source failures remain distinct.
Refused work reports skipped rather than success and does not schedule
derived work as though its suffix completed. Existing already-committed summary-flag recovery
remains independent of current extraction success and unrelated message snapshots,
with original account currency and current owner/cloud conversation qualification.
No database lock spans
compute admission, model/embedding calls or Redis; downstream jobs keep the same
original capture. In-flight network calls cannot be recalled, and pre-dispatch
checks do not provide atomic authority over a later network send.

Qualification is in `tests/test_fact_account_guard_postgres.py` and the actual
HTTP/task/queue paths in `tests/test_entity_producer_postgres.py`, alongside the
existing title/summary/entity suites. Required evidence is real disposable
PostgreSQL and owned Redis with scripted providers, including original capture,
owner/locality refusals, source/provider races, no-fact/filter checkpoints, prefix
retry, concurrent exact writers, both observed account-first lock-wait orderings,
cancellation, failed real commits and pool reuse. Skipped tests and mocked source
adapters in algorithm-only unit tests do not qualify runtime fencing. Current
final-state project gates and independent review remain required for publication.

The generic memory insertion/touch/log APIs, import/tool writers, retrieval and
other writer families remain uncovered. Shared SQL catalogue entries remain
uncovered where generic callers exist; assembled read queries retain explicit
scanner uncertainty. The historical threshold supersede/family-closure algorithm
is unreachable from production without an offline-only flag mutation: the runtime
default is `DEDUP_BENCHMARK_MODE=False`, no production/script assignment enables
it, and its direct calls/toggles are in tests/benchmarks only. It is not silently
converted into a production writer. This slice neither repairs #524 nor completes
deletion/reset, unprojected journal handling, S1 retirement or rollout acceptance.
Control writers still require account UPDATE-first ordering. **#477 stays open.**
