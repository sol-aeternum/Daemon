# H1 operation and asynchronous ownership — uncalled source slice

Owner-approved source and synthetic-test boundary only. No runtime import,
construction, configuration, startup hook, native acceptance/send/Stop/output
change, database transaction, migration, provisioning or activation is installed.
The rollout tracker remains #477; this slice does not establish its readiness.

## Ordering and resource ownership

Store opening holds its local mutex while acquiring shared RUNTIME_LOCK for the
lifetime. Each operation then takes its process-local invocation gate and store
mutex before exclusive OP_LOCK. The cross-store order is shared RUNTIME_LOCK;
exclusive OP_LOCK; native transaction and its existing auth/account/device/session/
task guards; short APPEND_LOCK for history scan/append/HEAD work. The local mutex
precedes each flock attempt. Standalone synchronous calls omit the invocation
gate and native-transaction step. APPEND_LOCK never outlives its filesystem
call; OP stays held across the whole operation scope and its release checks.
No J1 append lock may overlap H APPEND_LOCK. No provider/transport call belongs
inside this scope. Operator exclusive RUNTIME ownership is not a shared-lock
upgrade and has no implementation here.

The module cannot inspect an arbitrary caller's existing SQL/J1 locks. Native
call sites must prove the full lock-order matrix before composition. Existing
guards, C1's atomic pinned received-input registration and known-COMMIT boundary
are unchanged. No inference or TaskStore API accepts an H permit here.

`HistoryStore` holds shared RUNTIME_LOCK from explicit construction until its
owned descriptors close. Its synchronous `operation(deadline=...)` scope owns
the mutex and OP. Same-thread `verify`/`append` borrow that OP and take only
APPEND_LOCK, avoiding a reentrant flock unlock of the outer operation. Other
threads still wait on the mutex. Nested operation entry and same-thread close
inside a scope refuse. Scope failure poisons the store; uncertain cleanup never
returns success. Existing fork and descriptor-close rules remain in force.

`HistoryCoordinator(path, expected, ownership)` performs no I/O or thread/task
startup in its constructor. Explicit `open(deadline=...)` creates one serial
filesystem worker and opens the store there. `operation(deadline=...)` yields a
task-affine `HistoryOperation` with asynchronous `verify()` and `append(command)`.
All work, scope entry and scope release execute on that same worker. The scope
borrows no caller-owned descriptor, pool, connection or transaction. It owns no
commitment key and generates no record/request/operation identity.

## Failure, cancellation and shutdown

Every method keeps the caller's absolute monotonic deadline. Operation methods
inherit their scope's deadline; there is no fresh per-call budget. Lock wait,
filesystem work, result delivery and scope release must all fit for a successful
invocation. Storage syscall checks and prototype capacity bounds remain intact.

Worker failure, cancellation, late completion or uncertain release blocks the
coordinator monotonically. Catching a method error inside the scope does not make
scope exit successful. No automatic retry, reopening, replacement worker/store,
fresh record ID, HEAD repair, readback promotion or unblock method is supplied.
A successful append result is still history, never acceptance/COMMIT ACK or
dispatch authority. Identical replay remains `created=False` historical evidence.
There is no claim of exactly-once external effects or remote cancellation.

The supervisor shields the physical future and drains it before propagating its
original exception, including the original CancelledError across repeated
cancellation. An expired deadline never cancels a thread or releases its locks
or descriptors while its filesystem work continues. A completed late success is
discarded as a success result. Cleanup failure is recorded on a protected-body
exception; without a body exception cleanup failure propagates directly.

Queued process-local gate acquisition is different: it submits no filesystem
work and is directly cancellable under the same absolute deadline. Native
`asyncio.Lock` settles a cancellation while queued or racing a wakeup; a
successful acquisition is tracked before the next suspension and released once.
It must not use physical-owner draining: an operation owner may cancel and join
a child waiting for its gate, including during TaskGroup cleanup. Such a failed
wait still blocks the coordinator monotonically, but settles without waiting
for the current owner to exit. Submitted physical work, operation-lock cleanup
and shutdown retain the shield-and-drain guarantees above.

`aclose(deadline=...)` immediately refuses new scopes, waits for the current scope
and its physical work, closes on the same serial worker, and joins the worker
off-loop. Its close check is queued behind any unfinished initializer, so an
open/close race cannot overlook descriptors published later. Concurrent close
callers share one shutdown task. Cancellation cannot detach that task. A failed
close remains a failed close on subsequent observation, without descriptor retry.
Close from the operation's own task refuses instead of waiting on itself. An
inherited coordinator refuses before touching inherited executor/mutex state;
it must not be used after fork. Existing HistoryStore child-discard semantics
remain available only on the synchronous store itself.

This implementation waits for cleanup; it does not return early while silently
detaching an owner. Logical deadline expiry **does not bound kernel cleanup**.
A blocked syscall or caller scope that never exits can delay cancellation and
shutdown indefinitely. Callers must keep the event loop and structured scope
alive until cleanup finishes. Forced loop/interpreter/process loss is not a
successful cleanup; restart/reconciliation and operational stalled-owner
reporting remain separate. No process-kill policy or production supervisor is
chosen by this slice.

## Evidence and limits

Synthetic tests fence owned threads inside LOG, HEAD.pending and directory
fsync, late-result delivery, initializer publication and OP unlock. They check
locks/descriptors remain owned, repeated cancellation preserves the original
error, shutdown cannot overtake initialization or an active scope, failures do
not become success, contention cannot bypass OP, and close attempts descriptors
once. Interrupted acquisitions are tracked before the native attempt, cleaned
and poisoned even if the kernel acquired a lock before raising. Existing
real-fork/store tests and runtime noncomposition/no-I/O probes
remain applicable. No guard exception is added. Exact results and any failed
receipts are retained outside Git.

The scope's native-transaction sentinel is ordering evidence only, not a wired
SQL transaction or reconciliation proof. This slice does not supervise COMMIT,
account for an uncertain native dispatch, implement acceptance acknowledgement,
or certify C1/H1 atomic runtime composition. Native acceptance/COMMIT/ACK wiring,
then send/Stop/output coordination, independent restore controls, retention/J1
appliers and production lifecycle/provisioning qualification remain outstanding.
The original 24-case future integration plan is not represented as executed.
