# Attempt lifecycle — uncalled source-only prerequisite

Owner-approved four-operation scope for #477. Source: migration
`049_attempt_lifecycle.sql`, `orchestrator/attempt_lifecycle.py`, and the disposable
PostgreSQL suite `tests/test_attempt_lifecycle_postgres.py`. This is an internal
prerequisite for Daemon's general persistent assistant continuity (DEC04–DEC05,
DEC11), not a coding-specific runtime, a completed request fence, or activation.

## Scope and identities

`attempt_lifecycle_control` holds an immutable identity mapping and OPEN/CLOSED
control (`closed_at`, revision 1/2). `attempt_lifecycle_commands` is immutable
register/close history. Only the latter references the former; neither has an
outbound cascading FK to task/account/metadata. Existing evidence may disappear
through existing deletion paths; retained control cannot veto that deletion.

The full identity is lifecycle UUID, account UUID, **original** account generation,
task UUID, attempt epoch, financial scope UUID, separate metadata-scope UUID and
producer-instance UUID. The task/epoch and each scope map to at most one lifecycle.
The attempt start is read from its existing database row; it is NOT a new deadline.
No identity is rebound, recaptured or backfilled. Cleanup/financial/producer-stop
state is not copied. Metadata runtime_identity is per-reservation and is NOT this
producer-instance identity. UUIDs are not authentication or process-stop evidence.

The original origin is encrypted input. These SQL functions cannot authenticate
that origin: the future trusted caller must establish its provenance before
registration, after claim/record_compute_scope and before reservation or outbound
work. An existing live row is not proof of prospective origin. This primitive
neither adopts legacy attempts nor enables any caller to do so; no runtime caller
exists. Historical/singleton missing-entitlement disposition remains blocked.

## Exactly four operations

- **register(identity, command UUID):** live reference/account checks and atomic
  identity/command creation. Exact same-command replay returns historical receipt
  plus current state/reference observations, even after closure or removal. A new
  register command never reopens CLOSED state. Same command with changed operation
  or identity refuses. Task/epoch/scope collisions refuse, never repair.
- **assert_open(connection, identity):** caller-owned READ COMMITTED transaction
  only; rechecks OPEN and all live references under locks. No token, commit or
  remembered permission. The cooperating effect must occur on the same connection
  before actual transaction end. Reinvoke after rollback to any savepoint that
  released locks and immediately before subsequent cooperating effects. Raw SQL
  autocommit invocation holds locks only for that statement and permits nothing
  later. Caller controls bounded transaction lifetime; never span network calls.
- **close(identity, command UUID, reason):** irreversibly closes once; reasons are
  `requested`, `cancelled`, `stale`, not free-form content. Later close commands
  can add immutable receipts but cannot alter the first closure timestamp. Missing
  references do not prevent exact-existing-identity closure. Rollback is NOT close.
- **readback(identity, command UUID):** non-authoritative receipt + current control,
  account presence and live-reference observation. No executable authority, wildcard
  lookup, listing or partial-identity lookup. Missing account AND missing binding
  refuses; exceptions/timeouts never mean absence. Foreign/mismatched bindings
  refuse without returning their records. Account absence is not provisioning.

## Current predicates and ordering

SQL enforces all state/identity predicates independently of Python. Registration
and gate require an existing active entitlement, matching original generation,
no open account operation or suspension cause, owned running task/current epoch,
no cancellation, live **post-lock** database-clock lease, matching running attempt
and worker, matching recorded financial scope and owned metadata scope. Every gate
rechecks references; an orphaned OPEN control row grants nothing.

Lock order is account advisory (same SHA256 domain/signed bigint as account-control)
→ entitlement UPDATE when present → open account-operation set → lifecycle →
task/attempt → metadata scope → command receipt. Subordinate reads and lease
checks run in separate statements after their potentially waiting locks.
Missing-account close/readback keeps advisory serialization, exact binding and no
new identity. Existing `_relock` and metadata missing-account exceptions are NOT
absence oracles. No SHARE-to-UPDATE upgrade; no conversation/user/financial locks.

Existing claim/cancel are task-first; TaskStore deletion is conversation-first;
MemoryStore deletion directly cascades. These writers remain unchanged. Do NOT
call the new operations from a transaction already holding those content locks.
Cross-order lock timeout/deadlock is refusal, never retry or success. Function-local
250ms lock_timeout restores caller settings. Standalone adapters add statement,
acquire/release and cooperative operation bounds; these are database operation
bounds, NOT whole-call, producer termination or billing/drain deadlines.

Deletion first means registration/gate refuses; gate first serializes existing
task changes through caller commit. TaskStore keeps a running conversation and
requests cancellation; MemoryStore may delete it. Subsequent gate refuses for
either cancellation or missing task. Account/task removal may orphan control
identifiers, not manufacture authority or certify erasure. Dead producers may
leave OPEN records: lease/liveness checks refuse them; bounded abandoned-record
closure/reconciliation is a later prerequisite before runtime use.

## Principal and privilege boundary

This is a **trusted internal database-principal API**, not authenticated user
ownership. Neither UUID possession nor SQL EXECUTE proves authenticated ownership.
All future account-facing access needs separate authenticated ownership checks.
Public/private-helper EXECUTE and table access are revoked in the migration; unsafe
inherited defaults refuse migration atomically. No production roles/memberships or
application grants are created. Test grants apply only in a new disposable cluster.

Python preflight rejects owner/schema-owner membership (including transitive
pg_database_owner), privileged roles, table/column access, helper EXECUTE, and
missing entrypoint EXECUTE. It runs on the caller connection, not in a definer.
Raw EXECUTE can bypass this Python preflight but cannot bypass SQL state/identity
checks. Superusers, owners and directly privileged DML roles can bypass application
invariants; production separation is unverified and REQUIRED before runtime wiring.
Existing metadata/reservation/deletion functions do not consult this gate. Closure
therefore does not currently fence those writers or stop admitted/outbound work.

## Commit, cancellation and restore

Caller generates/preserves the exact command UUID and identity before waits.
Mutation and receipt commit atomically. No automatic write retry. Lost COMMIT
reply is uncertain even if the server committed; read back original identity and
command on a fresh authoritative connection. An acknowledged commit followed by
decode/release failure is `committed_result_unavailable`, not rollback. Cancellation
preserves the original exception and attaches identity/command/outcome. Connection
termination is not remote-query cessation or rollback proof.

Negative readback serializes behind the original account advisory lock; lock/read/
schema/primary failures are unresolved, never absence. A receipt reports history;
assert_open must still revalidate current state on the effect's transaction.
Restore may roll both state and receipt backward. No receipt or database epoch
detects that independently. Existing offline termination/journal/startup-barrier
qualification must cover future lifecycle consumers; this slice provides no applier
or readiness release.

## Unresolved release gates / exclusions

Account-linked retained identifiers are NOT anonymized. Retention/erasure and
restore qualification remain unresolved release gates; no live registration,
indefinite retention/pinning promise or premature evidence erasure is approved.
No deadline formula, cleanup lease/ownership/transfer/escalation, producer-stop
certification, settlement/billing/deletion API, runtime integration, deployed grants,
flag activation or legacy adoption. Existing conservative full-hold-before-retry
policy remains unchanged. Stream-read timeout excludes consumer suspension and
does not prove whole-call cessation. Later integration must qualify that premise,
bounded abandoned-record closure, financial/metadata same-transaction consumers,
deletion/evidence coordination and actual production privileges separately.

## Qualification entrypoint

Run through the registered task's managed runner:
`env PYTHONPATH=. uv run pytest -q tests/test_attempt_lifecycle_postgres.py --compute-metadata-postgres`.
It requires a cached `pgvector/pgvector:pg16` image, launches a uniquely labelled
network-none disposable cluster and Unix bridge under owned SSD resources, replays
the prerequisite migrations plus the original 049 in public, uses synthetic
identities only, and reconciles exact owned backends/processes. No application DSN,
production credentials, live account data or inference. Evidence distinguishes
real wire/driver faults, actual existing writers and synthetic negative controls.
Passing tests are source qualification only, never deployment/merge clearance.
