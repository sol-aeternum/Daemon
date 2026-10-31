# H1 acceptance projections — uncalled source candidate

Owner-approved source and synthetic-test boundary only. The required reader
key-check argument was separately approved. No runtime imports/callers, startup
composition, HTTP/client change, live binding/role/grant/key, application migration,
retention activation or dispatch authority is installed. J1 is unchanged.

## Payload and migration allocation

`migrations/056_restore_history_acceptance.sql` follows C1's merged 052–055
allocation, verified on main `2ad9257ade1ddf6dd9f57f2eedacd82574e95222` before
selecting the next free number. The ordinary runner discovers 056. The closed
044–050 runner and migration 051 are unchanged; this is no live-upgrade approval.
Synthetic upgrade tests use the existing migration advisory lock and per-file
transactions, checking exact source inventory, owner-only publication and atomic
056 failure after committed C1 migrations. All acceptance-role tests install
051–055 before 056. No application database is used.

The branch includes merged C1 source. Its TaskStore now shares a private
transaction join with ResourceInputs; native H1 runtime integration must preserve
C1's atomic pinned received-input registration and known-COMMIT boundary. This
payload has no dependency on C1 tables. Schema coexistence and restricted-role
qualification do not prove a composed C1/H1 runtime coordinator.

The caller supplies the migration transaction. There are no application grants,
role/membership creations, binding data, CLI initializer, backfill, pruning or
rollback/repair command in this payload. Reject unsafe inherited/default grants
before publishing objects, including grants other than PostgreSQL's intrinsic
PUBLIC function EXECUTE; revoke that intrinsic access in the same transaction.
Tests explicitly provision synthetic binding and narrowly scoped fixture grants.
Actual deployment/privileges and production metadata retention remain separate.

## Immutable relational evidence

The singleton `task_history_binding` fixes history UUID, H version 1, key ID 1,
key-check commitment and request schema `chat-canonical-3715-v1`. It stores no K1.
Missing or different expected identity refuses, including an empty page. Binding
is never initialized by first access or inferred from an incoming frame.

`task_history_acceptance` stores the exact bounded H1 frame/digest/sequence/record
ID and closed ACCEPT_PREPARE fields. It has no foreign key to account, task,
conversation, message or generation rows. Only the immutable H binding is
referenced with DELETE RESTRICT. Task/conversation/account cascades therefore do
not remove this evidence. UPDATE/DELETE/TRUNCATE triggers reject mutation; a
trusted schema/host owner can still change DDL, outside this cooperating-principal
model. This is not protection against a hostile operator or coordinated rollback.

Uniqueness covers history/sequence, history/record ID, original task ID and
account/key-ID/key-commitment. Generation does not create a new key namespace.
Frames obey H1's closed field types and canonical byte encoding: UUIDs, integer
versions/generations, fixed lowercase hashes, exact actor/kind/schema literals,
duplicate-key/escape/whitespace rejection, payload length and SHA-256 framing.
SQL reconstructs sorted-key canonical bytes; JSONB equality alone is insufficient.
Prototype sequence capacity is 16384 and frame size is at most 8192+36 bytes.
No raw client key, canonical request, content or provider object is stored.
These linked identities/commitments remain personal metadata, not anonymous data.

The SQL decoder validates one frame, not full H chain ancestry, actual K1,
canonical request provenance or fresh filesystem durability. Complete H validation
and independent identity/key checks remain necessary before runtime integration.

## Principal and operation boundaries

All protected tables and internal validators are migration/schema-owner-only.
Qualified acceptor A alone may execute
`task_history_acceptance_project(bytea)`. Qualified A and worker W may execute
`task_history_acceptance_page(uuid,bigint,text)`. Neither gets direct table/column
access or mutation. No PUBLIC access, owner fallback, A/W membership, grant option
or W acceptance capability is accepted. SQL entrypoints also reject another
login inheriting the intended service role's EXECUTE and reject global projection
ACL drift. This payload creates/selects no actual production principal. Actual role names are supplied existing
qualification facts, never environment discovery or newly created production roles.

The SECURITY DEFINER writer has a fixed pg_catalog search path. Catalog
relations, execution-time builtin types and signature lookups are also explicitly
qualified, so caller temporary relations/types cannot redirect privileged checks. It validates the
bound history and exact native task/coverage/conversation/message ownership and
identity. Account SHARE comes before content checks, with a fresh subordinate
control snapshot after any wait. Active generation, open deletion/reset and
suspension causes are rechecked. Native equality is relational evidence: it
cannot prove decrypted origin, fresh task creation or a fresh H append. There is
no xmin-based creation claim. The original native caller must preserve its own
guard and lock order; the adapter never acquires H locks inside a DB transaction.

An identical operation returns only historical existing metadata. Different
record/sequence/task/key reuse conflicts. Concurrent identical insertions cannot
both report a new row. Even an inserted row is not a fresh H receipt or permit.

The reader takes **required expected key_check** as its third argument. It compares
that commitment and H identity before returning zero or more rows; there is no
two-argument fallback. It returns at most 32 exact ProjectionFacts in increasing
sequence order. Rows are decoded and compared against every stored semantic
column. No underlying content/account rows are required for historical reads.

## Uncalled adapter and caller ownership

`orchestrator/services/restore_history/acceptance.py` borrows a current native
connection; it retains no pool/connection/transaction/filesystem resource.
`qualify` checks actual current principal and native split capabilities plus
protected schema, ACL, function, uniqueness and immutable-trigger structure.
There is no cached qualification. Unexpected grants, role switching, direct
column access, owner membership or catalog drift refuse.

The noncomposition inventory permits only this adapter's exact static, no-alias
import of ROLE_PRECONDITION, acceptor_preflight and check_principal from the
existing inference module. ADMITTED is unchanged. Consumer construction,
collaborator injection, installation and factory activation remain restricted;
external runtime imports/construction of H1 remain prohibited. Import/constructor
probes initiate no database, history-lock, network, task or subprocess I/O.

`stage` and `page` require the caller's explicit READ COMMITTED transaction and
absolute monotonic deadline. They never acquire, commit, rollback, terminate or
release that transaction/connection. They cap caller-local lock/statement limits
at 250ms/1000ms while preserving stricter values; SQL entrypoints also preserve a
stricter lock limit. No process-wide setting changes or budget resets occur.

`stage` validates the supplied typed entry and complete expected binding, stages
the projection, and checks exact returned metadata. Its StagedAcceptance result
is **not COMMIT acknowledgement, an accepted-task ACK or execution permission**.
The caller alone owns native COMMIT/release and final acknowledgement. Errors,
cancellation or late/corrupt results must propagate through caller rollback and
ownership reconciliation. An attempted projection failure blocks an existing
invocation monotonically; no retry, readback promotion or new operation ID occurs.
Cancellation preserves the original BaseException. Logical deadlines do not
promise bounded kernel/native cleanup or prove server rollback.

`page` returns historical facts only. A full read/empty page does not prove parity,
authority, restored account/finance/auth continuity or readiness. Feed the complete
H state and supplied facts to the existing comparator without filtering away
SEND_POSSIBLE, STOP_INTENT or restore records. Their missing projections remain
unresolved in this acceptance-only slice.

## Qualification limits

Opt-in PostgreSQL tests use cached-image network-none fixtures, synthetic roles
and temporary POSIX H1 directories. Test-only native transaction hooks demonstrate
SQL staging/rollback, not native runtime H OP_LOCK ordering. The separate uncalled
ownership boundary in `RESTORE_HISTORY_COORDINATION.md` has synthetic async
filesystem supervision; this adapter is not wired to it.
Faults around real transaction results/COMMIT distinguish server commit/rollback
from the caller's lost acknowledgement; they do not simulate a real network split.
Mocked/corrupted response cases test typed-result validation, not SQL privilege
proof. Real-role cases separately exercise privilege boundaries and drift.

An owned older database snapshot can lose its task/projection while current H
retains the acceptance; missing projections are reported, never repaired. This is
synthetic PostgreSQL/POSIX evidence, not a private backup rehearsal or live
qualification. Exact commands/results and failures are retained outside Git.

Remaining: deployed upgrade/ACL qualification;
native composition of the uncalled H operation/lifetime ownership layer; acceptance/send/Stop/output
integration and fresh dispatch receipts; stable clients; deletion/retention/J1
appliers; financial/auth/account/cache continuity; independent restore controls;
production identity/key/storage provisioning and capacity/retention policy.
This source does not establish #477 readiness or authorize activation/deployment.
