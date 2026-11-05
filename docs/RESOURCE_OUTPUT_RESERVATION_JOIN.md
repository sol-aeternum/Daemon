# H-owned first-output reservation

This source-only join follows the approved backend-owned publication contract.
`HistoryEffects.reserve_output` accepts an exact retained `IntentCommand`, its
original `AttemptIdentity`, the existing `ReservedOutputPublisher`, an exact
`OutputReservations` collaborator, and a distinct backend-held R0 pool. It
allocates no job/intent/slot/ciphertext and returns no reusable permission.
No startup, HTTP, socket, worker or producer installs it. H-configured startup
remains observation-only; #477 stays open.

## Authority and order

P, R0 and W pools must be distinct. The actual R0 session must also be a direct
login distinct from A/W and the independently observed P login, without membership
in those roles; a second pool using the same login is insufficient. This check
precedes reservation writes and changes no native R0 or application grants.
The backend content cipher authenticates the
closed command before SQL; task/account/generation/lifecycle/epoch must match the
original identity. No worker receives A/P/R0 credentials, K1 or artifact storage.
The actual R0 connection is qualified by its unchanged native restricted-role
preflight, rather than trusted because it came from a supplied pool.

The existing two-second absolute H effect cap applies. H lifetime/operation
ownership precedes all SQL. Full independent H identity/key/protected projection
parity, original acceptance/generation and historical Stop checks run first.
The existing 058 P snapshot then supplies the locked original sealed input and
source IDs. Its encrypted native origin and exact conversation/result message
must match the retained acceptance. Existing explicit snapshot and presentation
record budgets apply; no production quota or new setting is selected.

P owns a top-level READ COMMITTED transaction and completes actual release before
R0 acquisition. There are never simultaneous P/R0 account transactions. R0 owns
one top-level READ COMMITTED transaction containing only the existing
`register_output_job` and `reserve_output_intent` operations. Their native current
account/operation/generation/lifecycle/task/attempt/lease/Stop fences and immutable
packet conflicts remain unchanged. No filesystem, transport, provider call,
catalog publication or card operation occurs in that transaction.

## Commit, uncertainty and cleanup

Only known top-level R0 COMMIT and completed actual native/H cleanup permit a
successful `None` result. That result is not file, publication, completion or send
authority. Every later phase reacquires its own original native and H checks.
Expired historical reservations do not become new write permission; historical
observation remains in the existing separately bounded observation paths.

Failure/cancellation/deadline drains the pinned asyncpg query cancellation
transport before terminating an uncertain channel, and retains actual native
pool reset/release through repeated cancellation before H ownership can exit.
Original exceptions survive termination/cleanup failures, with secondary notes.
The mutating owner blocks monotonically. It issues no application rollback or
readback command to resolve uncertain COMMIT. Driver reset/release remains physical
cleanup, not COMMIT reconciliation. There is no automatic retry, replacement packet/
identity, readback promotion or renewable-writer exception. Logical deadlines do
not bound kernel cleanup.

The original command remains the caller's packet if either COMMIT outcome is
uncertain. A committed reservation may still lack files, catalog rows or a card.
This join neither completes another phase nor interprets that state as permission
to repair/adopt it. Shutdown waits for actual owned work; no late success is
published after cancellation or cleanup failure.

## Narrow source admission and release limits

The reservation import inventory admits only exact top-level static, unaliased
`OutputReservations` and `IntentCommand` imports in the existing H effects module.
The source seam guard permits only the exact P snapshot and R0 calls in the new
join, while retaining the original C1 caller unchanged. ADMITTED and runtime
module allowlists are unchanged. Aliases, extra imports/arguments, callbacks,
other callers, consumer construction/injection and activation remain refused.

`tests/test_history_output_reservation_postgres.py` uses owned SQL/POSIX fixtures
with distinct P/R0/W pools for known commits, immutable competing packets, native
fences across the transaction handoff, actual lost COMMIT acknowledgements,
held reset/cancellation transport, repeated cancellation, shutdown and cleanup
failure. Unit guard negatives and existing import/constructor no-I/O probes qualify
the source boundary separately. These are synthetic tests, not authenticated
transport, distinct deployed service-UID or live acceptance evidence.

No SQL function/schema, table permission, environment setting, credential, grant,
key, mount, socket or deployment is introduced. Preparing a command from an
authenticated worker request, append, complete runtime composition and supported
artifact restore/reconciliation remain outstanding. H does not independently
record every artifact reservation/publication/card. Lifecycle/deletion/reset/
retention/GC/privacy requirements are not waived by this reservation join.

## Combined phase qualification after #673

`tests/test_history_output_phases_postgres.py` reconciles the previously separate
post-#669 qualification with merged preparation source. It first calls the actual
H-owned `prepare_output`, retains the exact returned command and CSV bytes in the
explicit synthetic fixture, and then selects reservation, catalog/file,
presentation and terminal-text operations. No reservation bypasses H or prepares
replacement identifiers. Preparation writes no reservation/catalog/card/result.
The known-COMMIT path reopens the original task, decrypts its final text,
authenticates original card pins and reads the verified CSV bytes.

Stop and protected-projection loss at every boundary between the five operations
preserve earlier committed effects while the next operation refuses. Unknown
COMMIT acknowledgement before/after each actual phase COMMIT and cancelled/failed
native holder resets block all five entrypoints without returning a late packet,
regenerating identifiers, advancing another phase or promoting diagnostic rows.
Synthetic owner readback establishes which rows survived; it is never application
reconciliation or retry authority. Actual reset workers and H operation/lifetime
ownership must settle before the caller observes failure. Production behavior,
assertions and deadlines are unchanged.

This is synthetic SQL/POSIX interoperability proof, not an installed runtime,
authenticated user/device/socket path or durable prepared-packet store. The
fixture's explicit memory retention does not define crash/restart packet recovery.
A future host must define retained packet ownership, bounded authenticated local
transport and the response/recovery contract before invoking these methods.
No automatic regeneration, replacement, history adoption or uncertain-COMMIT
promotion is inferred. Independent artifact restore evidence and lifecycle/privacy
release gates remain open. Qualification results are reported separately; this
source description does not itself establish passing tests or live acceptance.
