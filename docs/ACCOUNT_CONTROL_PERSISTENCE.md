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
  lease epoch). No task/auth/client/write-guard integration consumes it here.

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
