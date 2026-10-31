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

The scope also retains actual native pool release tasks. asyncpg shields its
holder reset internally; joining a cancelled acceptance task alone would leave
that reset detached. A task-affine cleanup scope is entered after H ownership
and exits before H release. Projection scans, native replay/readback/acceptance,
the optional A/W preflight, and every participating C1 namespace/account lease
use the same actual pools with retained release inside that scope. No pool is
replaced and no runtime host enters the scope. Outside it, the helper delegates
to original pool acquisition/convenience behavior. Acquisitions stay directly
cancellable; release tasks are shielded and drained through repeated cancellation
or deadline expiry. A late reset or failed reset cannot become an acknowledgement
or readback authority. Cleanup failure poisons that scope even if caught by a
native fallback; the first body/caller exception remains primary. Scope inheritance
by another task and nested activation refuse. This does not bound native cleanup
or introduce an automatic retry, a new release deadline or a force-close policy.

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
cache continuity, runtime Send/Stop/output, deployment and enablement remain outstanding.
No result or history/projection receipt is dispatch permission; no exactly-once
external effect, remote cessation or live daily-driver readiness is claimed.

## Uncalled Send/Stop extension (057)

`HistoryEffects` is an explicit synthetic collaborator, never installed by HTTP,
worker startup or a client. The original `InferenceRecovery.mark`,
`TaskStore.request_cancel` and terminal completion defaults remain uncomposed.
The closed 044–050 migration runner does not include 057. Applying this source
to any application database remains a separate operational decision.

057 adds one immutable, non-cascading common projection index for the existing
H1 `ACCEPT_PREPARE`, `SEND_POSSIBLE` and `STOP_INTENT` frames. It retains the
original acceptance account/key uniqueness table. Its owner-only upgrade copies
existing acceptance frames after validating every semantic column; it neither
prunes nor repairs missing history. No roles, service grants, binding data or
credentials are created. A alone writes acceptance; W alone writes Send/Stop;
A/W read the same bounded combined page. Neither gets direct table mutation or
private decoder/helper execution. These records contain personal identity and
possible-effect metadata; live retention/deletion policy is still unapproved.
The old 056 `AcceptanceProjection` profile stays strict. The explicit
`HistoryProjection` profile qualifies all ten functions, immutable table shape,
current principal and closed ACLs on the acquired connection. Acceptance using
that profile compares all three kinds, never drops Send/Stop to obtain parity.

Each joined operation holds H OP before acquiring native account/lifecycle/task
locks or a transaction. Append locking remains short. Full H/SQL parity and the
independent identity/key-check metadata must match first; unresolved restores,
missing/extra facts and historical matching keys still refuse, with no repair,
readback promotion or automatic retry. A cancelled logical gate waiter settles
directly. Actual filesystem work and native pool-holder reset are drained before
OP release even after cancellation/deadline. The 2-second total Send/Stop budget
includes admission, SQL, H fsync and cleanup; stricter native limits remain.
Logical expiry never promises bounded kernel cleanup.
Public entry checks process/loop ownership and blocked state before invoking the
original invocation check or checking K1. The same total absolute deadline is
frozen before that validation and never extended by supervision. The trusted
invocation check remains synchronous local validation, not a transport/I/O hook.
Send rechecks the same deadline after its final invocation check before returning.

For Send, the original native mark still generates a new runtime identity and
nonce and validates its own `created:true` result. Its private callback durably
appends H and stages the exact Send projection on that SAME native transaction.
Only a confirmed top-level COMMIT, actual connection release and H release can
return None. Scoped control transactions refuse an inherited transaction rather
than treating SAVEPOINT release as COMMIT. Outside the H cleanup scope, the
original connection ownership behavior is unchanged. A Send records only that an
effect may happen: no provider/network call runs under H. The trusted caller
labels the closed effect kind; this slice does not invent a mapping from billing
operation names to transport kinds.

An earlier Send from a reconstructed invocation refuses. Locally retained record
IDs are only a negative historical filter for the same original live invocation;
each subsequent distinct call must still pass a fresh native mark, reservation,
nonce, H append, projection and COMMIT. No record, returned value or local set is
reusable dispatch permission. Any uncertain outcome blocks the object and original
invocation; a fresh wrapper/state cannot adopt historical Send as permission.

For Stop, the backend supplies K1 for that call; worker Send never receives it
or A credentials. Native cancellation retains its control/W connection and
original account/task/origin guards. Only an accepted cancellation appends Stop
and stages its projection inside that same transaction. The projection preserves native negative control eligibility after suspension or
generation advancement rather than applying fresh content/send admission rules.
Its original authenticated task/coverage generation stays unchanged. This grants
no positive content, dispatch, financial settlement or restored-account authority.
A historical Stop refuses; there is no new historical observation/acknowledgement
API. A terminal native
`accepted:false` result adds no Stop record and is not a durable Stop confirmation.
Stop before mark refuses new sends. Send before Stop preserves possible-effect
uncertainty; it cannot revoke an already admitted call atomically across a provider
boundary or guarantee remote cessation/billing cancellation.

The narrow uncalled terminal `complete` fence serializes with Stop and reuses
native claim/epoch/current-authority checks. If Stop wins, terminal success cannot
publish. If completion wins and commits first, later Stop observes native terminal
state and cannot retroactively cancel it. This is no streaming, partial-output,
artifact or independently retained output-continuity implementation.

The static noncomposition inventory admits only the exact `InferenceRecovery`
type import in `effects.py`, exact private native staging calls and cleanup scopes
inside its three joined methods. `ADMITTED` is unchanged; consumer construction,
installation, alternate hosts and runtime H imports remain prohibited. Positive,
negative and no-I/O tests discriminate that boundary. This is a bounded source
inventory, not an arbitrary Python execution sandbox.

Synthetic tests exercise the joined boundary and real PostgreSQL/POSIX ownership;
exact executed counts and failed receipts are reported separately. They do not
qualify production trust, provisioning, restore dispositions, financial/auth/cache
continuity, clients or runtime rollout. #477 remains open.
