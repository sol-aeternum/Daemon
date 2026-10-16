# Attempt lifecycle — source-only prerequisite

Owner-approved four-operation primitive for #477. Source: migration
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
Ordinary metadata/reservation/deletion functions do not consult this gate. The
uncalled financial seam below cooperates; closure does not fence existing runtime
writers or stop admitted/outbound work.

## Uncalled attempt-qualified financial admission

`EntitlementService.reserve_for_attempt(identity: AttemptIdentity, amount_microusd,
*, operation, ...) -> Reservation` is a source-only cooperating consumer. It derives
account and financial scope from the exact existing identity; no optional identity,
client token, supplied connection, outer transaction or savepoint API is supported.
Ordinary `reserve`/`recover_reservation`, cloud-only callers, policy/arithmetic,
account provisioning rules and settlement behavior remain unchanged. No runtime
caller, registration/closure, metadata attachment or provider dispatch is added.

The service acquires one connection and starts its own top-level READ COMMITTED
transaction. It is `assert_open`'s caller: gate before financial locks, same-connection
revalidation before each cooperating write and before COMMIT. An expired lease
after financial waits refuses and rolls back the admission. Checks do not promise
a lease stays live through COMMIT acknowledgement or authorize later network work.
Only a complete financial body with its original `ReservationReceipt` reaches the
explicit COMMIT path. A caller catching an exception cannot commit a partial prefix.
Provisional values never escape before acknowledged COMMIT and successful release.

Both new methods use one cooperative 2s absolute deadline including release and
failure cleanup, acquisition/release/rollback allowances capped at 0.5s and the
remaining overall budget, SQL lock timeout 250ms and statement timeout 1000ms. No
cleanup grace, automatic retry or deferred application cleanup task is added.
Failed/interrupted cleanup terminates only the exact still-owned connection proxy.
Ownership is relinquished before entering pool release: neither cancellation nor
release failure permits application termination of that proxy while the driver's
shielded reset may still reference its reusable holder. Release failure (including
failure before reset starts) leaves cleanup unresolved; it does not certify a
returned lease. Driver-owned reset work and server backends still require qualification.
Timeouts and termination do not prove remote query cessation or rollback.

Before COMMIT, errors retain financial denials or raise safe
`AttemptReservationUnavailable`; no COMMIT path follows a failed body. If the body
already produced a receipt before final gate refusal, that provisional receipt is
preserved for accounting only, never as successful admission. During
unacknowledged COMMIT, `ReservationCommitUncertain` preserves the original financial
receipt. After acknowledgement, `ReservationCommittedResultUnavailable` explicitly
reports the committed outcome even if release fails. External cancellation preserves
the first external `CancelledError`, including during cleanup after a refusal,
with `reservation_identity`, `reservation_receipt`,
`reservation_outcome`, `reservation_commit_acknowledged`,
`reservation_rollback_acknowledged` and `reservation_termination_failed`. A
`not_committed` protocol outcome does not certify a stopped uncommitted query.

`recover_attempt_reservation(receipt: ReservationReceipt, *, commit_acknowledged:
bool) -> Reservation | None` explicitly observes accounting on a fresh bounded
READ COMMITTED connection, behind the financial account lock, with existing exact
receipt/row matching. It has no lifecycle gate or mutation/settlement/provisioning.
Missing account, mismatch, unavailable readback or known-committed missing row is
`ReservationRecoveryUnresolved`, never absence. Unknown-commit absence is only a
current-primary observation under the existing no-restore qualification assumptions.
Neither a matching row nor absence authorizes replay, readmission or dispatch.
Lifecycle receipts/readback cannot resolve a financial reservation COMMIT.

The NEW seam deliberately omits ordinary reserve's separate best-effort denial
analytics transaction: no extra unguarded financial/analytics writes on refusal.
Existing callers retain their analytics unchanged. Frozen IDs and test-only grants
do not authenticate ownership or qualify deployed privilege separation.

`tests/test_attempt_reservations.py` supplies fake driver/store unit checks, not SQL
or financial-race qualification. `tests/test_attempt_reservations_postgres.py`
contains the real synthetic PostgreSQL/wire-fault acceptance scenarios; skips do
not qualify them. Source implementation alone does not establish these checks passed.
The approved isolated fixture run on 16 October 2026 passed the admission and
lifecycle suites, including account-lock expiry, both close/cancel orderings,
partial-write rollback, COMMIT wire faults and receipt readback. Additional real
driver tests cancel acquisition, open transaction, partial write, COMMIT wait,
shielded release and refusal rollback, and bound recovery behind an account lock
without restarting its deadline. Cancellation/reset pauses and the post-release
exception are synthetic fault controls around real SQL/driver operations, not
proof of arbitrary wire timing, deployed principals or remote producer cessation.
In-flight driver cancellation can forward a separate server CancelRequest through
the bridge; cancelled COMMIT readback therefore permits either matching committed
truth or current absence, never presumed rollback or permission to retry. A live
original backend can keep the account barrier locked and make bounded recovery
unresolved. Passing fixture runs and their zero-backend teardown observations do
not guarantee future session teardown within the recovery lock timeout.
Existing ordinary financial tests also passed against the same approved cluster
using an evidence-only fixture-source adapter; their test bodies were unchanged.
That adapter is retained in task evidence outside the repository: this extra
ordinary-test qualification is not reproducible from tracked files alone.
These runs do not substitute for full project gates or production qualification.

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
`env UV_PROJECT_ENVIRONMENT=.uv-venv PYTHONPATH=. uv run pytest -q tests/test_attempt_lifecycle_postgres.py tests/test_attempt_reservations_postgres.py --compute-metadata-postgres`.
It requires a cached `pgvector/pgvector:pg16` image, launches a uniquely labelled
network-none disposable cluster and Unix bridge under owned SSD resources, replays
the prerequisite migrations plus the original 049 in public, uses synthetic
identities only, and reconciles exact owned backends/processes. No application DSN,
production credentials, live account data or inference. Evidence distinguishes
real wire/driver faults, actual existing writers and synthetic negative controls.
Passing tests are source qualification only, never deployment/merge clearance.
