# H1 acceptance composition — isolated source slice

This is an owner-approved uncalled source/test join, not production activation.
`HistoryAcceptance` in `orchestrator/services/restore_history/composition.py`
borrows an explicitly opened HistoryCoordinator, independent Identity/K1 binding,
AcceptanceProjection and actual native TaskStore sharing one actual asyncpg.Pool.
Borrowed connection shims and alternate pool types refuse. Its constructor starts no I/O,
worker, task, transaction or installation. No startup, HTTP/client, provider,
send/Stop/output, configuration, SQL payload, privilege or provisioning changes
are installed. Rollout tracking remains #477, which stays open.

## Native path and ordering

The ordinary TaskStore.accept and ResourceInputs.accept signatures and behavior
remain unchanged. They call private `_accept_with_stage` implementations with
`stage=None`. Only the uncalled H1 join supplies the narrow staging callback;
there is no new mutable collaborator field, bind method or global activation.
The source inventory restricts those exact calls and keeps ADMITTED unchanged.
External H1 imports/construction/activation remain prohibited.

H shared lifetime and exclusive operation ownership precede any native pool,
transaction, account guard, resource-input preparation or content lock. The join
validates the independent key/identity, finite canonical server fingerprint and
its message/conversation binding, and exact stable ASCII client key. No-key H1
acceptance refuses rather than inventing a new identity; ordinary native no-key
behavior remains unchanged. Received attachments/structured content refuse unless
the actual C1 input collaborator is bound; there is no unpinned-input fallback.
The caller still supplies the original captured
account generation and trusted server arguments. This is no authentication API:
future hosts must acquire their actual authentication/device/session authority
in the approved H-before-native order. No existing HTTP authorization is moved
or bypassed by this uncalled source.

Before NEW work, complete H history is compared with bounded qualified SQL
projection pages. Missing/extra/mismatched facts or restore intervals refuse.
SEND_POSSIBLE, STOP_INTENT and restore records are not filtered to manufacture
parity; this acceptance-only slice cannot qualify their missing projections.
An empty page is not an identity initializer or an independent readiness permit.

The existing native acceptance implementation retains its original guards,
request/origin sealing, conversation ownership, task/message insertion and C1
batch reservation/preparation. C1 publishes pinned versions/references and its
accepted pointer in the original final task transaction. The staging callback
then appends one durable ACCEPT_PREPARE using the actual pending native IDs and
stages the exact protected acceptance row on that same connection/transaction.
No J1 append lock overlaps H APPEND_LOCK: C1's bounded journal scans/appends finish
before the H append; H OP remains held throughout. A trusted host must preserve
this order across other writers; arbitrary externally held locks are not visible
to the adapter.

Native definite busy-conflict handling before H prepare remains unchanged. Once
the callback has started, neither native nor C1 refusal/unique handlers may fall
back to readback, another insertion or another operation identity. A failed
projection, unexpected historical insert or unknown COMMIT exits unsuccessfully.
No repair, projection backfill, transaction retry or restore reconciliation is
supplied.

## COMMIT, cancellation and response boundary

The supervised child owns the complete H/native operation. Caller cancellation
or logical timeout is forwarded to it once, then its physical work, native
transaction/connection cleanup and H release are drained. Repeated caller
cancellation cannot detach that owner. Unlike shield-only gate observation, a
queued child receives cancellation and settles its native asyncio.Lock waiter.
The repaired #628 physical-work and queued-gate distinctions remain intact.

Pool-provided channels already inside a transaction refuse before staged work;
a nested SAVEPOINT release is never treated as top-level COMMIT. The native
connection check is staging-only, preserving ordinary native behavior.

A fresh AcceptedTask is delivered only after the original native transaction
reports known COMMIT, its connection release completes, H scope cleanup succeeds,
and the original absolute monotonic deadline still holds. Once supervision starts, error, cancellation, late result or release failure
monotonically blocks the composition/coordinator. Stateless request rejection
before child creation starts no H/native work.
The first caller exception is retained; cleanup failures are secondary notes.
Logical deadlines do not bound kernel cleanup or an indefinitely stalled native
resource. Forced process/event-loop loss is not successful cleanup.

H1 currently has ACCEPT_PREPARE, not an independently acknowledged-COMMIT record.
Therefore **every historical matching H key refuses as historical evidence**;
a different request conflicts. Even matching committed SQL cannot distinguish an
acknowledged prior response from a lost response. A new owner cannot use readback
to promote that ambiguity, recreate work after rollback, refresh generation or
replace an operation ID. This source supplies no historical-observation/client
response contract. The ordinary, uncomposed native replay API is unchanged and
is not protected merely because this source module exists.

## Synthetic evidence and limits

Tests use owned network-none cached-image PostgreSQL, synthetic independent A/W
roles, finite test grants and temporary POSIX H1/J1 directories. The restricted
C1 fixture grants explicit ordinary table/column permissions, including mutable
identity columns needed by row locks; those are mutation authority, not read-only
privileges. They are not a production grant proposal. It reuses an existing
qualified C1 namespace; production namespace/principal/OS/key provisioning is
not established.

Real SQL/POSIX cases cover fresh commit/release; rollback after durable H prepare;
committed and rolled-back lost acknowledgement; append/stage/COMMIT/release
cancellation; repeated cancellation and late delivery; release failure; competing
identical/conflicting keys; missing binding/key mismatch; account generation and
ownership guards; native cascades; restored SQL/H mismatch; and C1 task/input/
projection atomicity. In-process transaction faults establish known server
branches under a lost caller result. C1 relay faults also exercise loss on the
synthetic PostgreSQL protocol. Neither is a real network partition or live
provider side-effect test. Exact executed cases/results and all failed receipts
remain outside Git; this document is not a claim that all broader future fault
cases ran.

The pre-existing uncomposed native diagnostic still demonstrates its gap. The
new source does not intercept callers that bypass this isolated join. Runtime
composition, authenticated client/key compatibility, historical observation,
production retention/J1 appliers, independent restore controls, financial/auth/
cache continuity, send/Stop/output, deployment and enablement remain outstanding.
No result or history/projection receipt is dispatch permission; no exactly-once
external effect, remote cessation or live daily-driver readiness is claimed.
