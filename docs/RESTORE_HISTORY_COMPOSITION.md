# Acceptance composition — isolated source slice

## Default isolated authenticated acceptance host

`IsolatedAcceptanceHost` / `create_isolated_acceptance_app` in
`orchestrator/services/restore_history/acceptance_host.py` are explicitly
constructed qualification adapters, never selected by startup. Constructors do
no I/O. This section describes the default, **acceptance-only** construction with
no execution collaborator. The optional original-runner text composition is
documented separately in [ISOLATED_TEXT_EXECUTION.md](ISOLATED_TEXT_EXECUTION.md);
its qualification does not select production execution. The host borrows
distinct actual A/W stores/pools with the same encryption
and independent H identity/K1. It owns separate writer/read coordinators: open
before requests, drain aclose before closing borrowed pools, reconstruct all
owners/pools for in-process reconstruction. This does not prove process restart.
No blocked writer/read owner is renewed automatically.

By default, only isolated `POST /chat` and existing `GET /tasks/by-key/{key}` are installed.
POST uses ChatRequest, a mandatory existing-format stable key, the task-ID header
and existing task/conversation SSE acceptance frames. It emits no tokens, progress
or completion. The stream stays open while qualified by-key reads still show
queued work; another state/refusal ends the stream, not the task. Reads poll every
two seconds until disconnect or shutdown, without a stream-lifetime limit; they
share H operation serialization with acceptance. This is isolated qualification,
not a production admission or capacity policy. Attachments,
structured content, suggestions, supplied user identity and temporal-memory scope
refuse without fallback. All responses, including size/validation errors, are
no-store. GET reuses HistoryObservation and the existing TaskOut/refusal mapping.
This is neither a new public deployment/API/SSE format nor an answer/output stream.

The supervised child takes H OP and retained cleanup before SQL. Its distinct W
READ COMMITTED auth transaction takes session-cleanup then auth-runtime advisory
locks, then device/session row locks **before** actual bearer verification and
last-seen mutation. A separate post-wait query rechecks both owners, revocation
and clock_timestamp expiry. Row locks also cover logout, which does not take the
auth advisory lock. W captures generation once before deriving accepted input,
without an account row lock. It retains auth authority across the original A
acceptance transaction, known COMMIT and actual A reset. Native A still applies
the original-generation account guard before content locks. Auth is not acquired
late in the staging callback, and no supplied account/context is authentication.

Private `_accept_owned` requires the same active task-affine operation and cleanup
scope. It preserves the original staging closure, single-use prepare, top-level
COMMIT, parity and historical refusal. Its result remains pending until W
transaction/reset, scope cleanup and H release settle within the original absolute
host budget. W has explicit local SQL limits; A limits do not bound W/logout.
Auth controls serialize while those rows/advisories are held. Physical cleanup
can exceed logical deadlines. Cancellation/shutdown drain actual children and
resets before H closure; errors never become acknowledgements. Historical matching
submissions block the writer. Other exceptions inside the H operation, including
conversation-busy/not-found and a concurrent account fence, also conservatively
block subsequent submissions, even with a different key, until controlled restart.
Observation never renews the writer or retries the failed submission.
SQL-only keys already visible in the qualified A preflight refuse before native
replay can refresh a stored request digest. This negative check is not a fence
against an out-of-contract non-H writer racing that preflight; the isolated host
must not be mixed with legacy producers. It creates no readback or acceptance
authority; ordinary uncomposed native replay is unchanged.

The host accepts exact combined 057 `HistoryProjection`/`h1`, or exact
`TerminalHistoryProjection`/`terminal-v3` with the complete schema through 065.
Both sides must use the same concrete projection type, profile, identity,
acceptor/worker principals and (for terminal-v3) publisher. Constructor and
request admission recheck these bindings, including each coordinator's profile.
Standalone artifact-v2 and subclasses remain refused. The underlying acceptance
join also retains exact 056 `AcceptanceProjection`/`h1`; that acceptance-only
projection cannot form this host. Old H1 qualifiers still reject expanded SQL;
there is no schema/profile fallback. All acceptance, observation and startup
qualification comparisons use the selected profile and full mixed-history pages.
Inherited acceptance staging retains unchanged v1 frames in terminal-v3 chains.
No queue wakeup, attempt, provider/search/tool call, output or memory extraction
is installed by this default construction. Accepted queued work is not dispatch authority. Answer/CSV delivery
still requires authenticated Send/Stop/output composition, original attempt
authority and restart fencing. Production observation-only startup, worker refusal,
grants and deployment gates are unchanged. #477 stays open.

Synthetic terminal-host tests use actual unchanged migration ancestry and
synthetic restricted grants, original native test-only claims/HistoryEffects,
and second-device reads of persisted partial/completed text before and after
in-process host/pool reconstruction. The host itself installs no execution graph.
This qualifies neither durable partial restore evidence nor cards, control-only
terminal outcomes, output reconstruction, process restart or deployed phone use.
Missing/extra facts remain negative evidence, never permission to repair or
acknowledge an uncertain acceptance. Exact executed coverage and any unmet mixed
page/fault cases belong to local receipts, not this source contract.

## Underlying source join

This is an owner-approved source/test join, not production activation.
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
replaced and no production runtime host enters the scope. Outside it, the helper delegates
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
replace an operation ID. The separate [historical-observation boundary](RESTORE_HISTORY_OBSERVATION.md)
now supplies the approved read/client contract; this acceptance path still
refuses historical work. The ordinary, uncomposed native replay API is unchanged and
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
composition, deployed client/key and historical-observation qualification,
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

The uncalled terminal `complete` fence serializes with Stop and reuses
native claim/epoch/current-authority checks. If Stop wins, terminal success cannot
publish. If completion wins and commits first, later Stop observes native terminal
state and cannot retroactively cancel it. The durable text extension below adds
partial output; live streaming, artifact and independently retained output
continuity remain uncomposed.

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

## Durable text publication boundary

The same uncalled `HistoryEffects` join now covers native `write_partial` and
carries existing native `message_fields` through terminal `complete`. This is
SQL text/field publication, not live token delivery or artifact publication.
There is no new H record kind, protected table, output backfill, content store,
API response or runtime collaborator installation. The observation-only ingress
and worker barrier remain unchanged.

H operation ownership and the retained cleanup scope precede complete H/SQL
parity, exact original acceptance/account/generation matching and negative Stop
checks. The original TaskStore claim/epoch/account/current-generation/lease and
invocation checks still own the actual output transaction. Only known native
COMMIT, actual pool reset and H release within the existing absolute two-second
logical operation budget can return success. Native TransactionUnavailable phase
information is retained; committed-result-unavailable cannot become a second
write or readback success. Cancellation, deadlines and cleanup failure block the
same owner monotonically and drain underlying SQL/filesystem work before H exits.

A Stop that wins H prevents later partial or terminal publication. Partial text
committed before Stop remains available; it is not retroactively erased. H carries
no output contents or independently acknowledged terminal receipt: a restored
older SQL result is not repaired or reconstructed. Existing possible-send
history still cannot authorize re-execution. No Redis/network/provider work is
performed under this output scope. Live delta/progress transport, full runner
composition, financial/account/file restore qualification and activation remain
separate gates. Terminal message fields preserve the native contract; they do
not create a pinned artifact publication or reference by themselves.

The closed source inventory adds only the exact `_write_partial` method in the
existing effects module to retained-cleanup scope admission. `ADMITTED`, imports,
consumer construction, injection and runtime activation restrictions are unchanged.
Discriminating negative tests reject foreign methods/modules, aliases and extra
cleanup arguments. Synthetic PostgreSQL/POSIX tests cover Stop ordering, missing
acceptance/Send/Stop projections, original native authority, both server branches
under lost COMMIT acknowledgement, actual held reset through repeated cancellation
and deadline, cleanup failure and H release loss. Exact executed counts/receipts
are reported separately; no live acceptance is claimed.
