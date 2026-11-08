# Completed-text restore evidence

This is an explicit, uninstalled `terminal-v3` source profile. It independently
records that a normal completed-text publication was prepared, so a later
whole-SQL rollback cannot silently turn the original task into fresh work.
It does not retain, reconstruct, replay or adopt lost text. Runtime startup
remains observation-only; #477 stays open.

## Closed history and SQL contract

The profile accepts unchanged v1/v2 frames and one new v3 kind,
`TERMINAL_OUTPUT_PREPARE`, with worker actor and digest domain
`daemon.restore-history.frame.v3\n`. Its seven data fields are `account_id`,
`account_generation`, `task_id`, `result_message_id`, `acceptance_record_id`,
`epoch` and the fixed `terminal_schema` value `native-complete-body-v1`.
Original acceptance, account generation and task/result identity must match.
There is no text, artifact data, content digest, credential or authority token.
Existing UUID/int64 validation and 8 KiB payload, 16 MiB log and 16384-record
bounds remain unchanged, without pruning.

Only one terminal prepare may exist per task, including across epochs or fresh
operation IDs. It cannot follow Stop or an open restore interval. Subsequent
positive Send, preparation, reservation, catalog, presentation, partial and
completion operations refuse a historical terminal task. Negative Stop retains
its existing protected path. Matching evidence is never dispatch authority or
a completed-result receipt that can be adopted by another operation.

Source migration `065_terminal_output_prepare.sql` adds the closed kind to the
existing immutable, non-cascading projection and three fixed functions:

| Function | Authority and behavior |
| --- | --- |
| `task_history_decode_terminal(bytea)` | Private owner-only closed decoder |
| `task_history_terminal_project(bytea)` | Exact direct W principal; validates and stages a new fact in the caller's native transaction |
| `task_history_terminal_page(uuid,bigint,text)` | Existing exact A/W principals; expected identity/key check and complete bounded mixed-history read |

The stager validates current account eligibility, original acceptance and
task/result/epoch binding, completed native task/message/attempt/event state,
absence of prior Stop/terminal facts, and the exact history predecessor. All
four native publication rows must still belong to the current top-level
transaction or an active child transaction. Already committed output cannot be
backfilled. The stager neither commits nor returns historical success. Public,
inherited, extra-principal, P and direct-table mutation authority remain refused.
The source migration creates no login, service grant, key or binding data.

The explicit qualifier requires the complete 16-function schema and exact ACLs.
Older strict H1/artifact-v2 qualifiers refuse that expanded schema; there is no
fallback. Deployment must coordinate schema/profile compatibility separately.
The new reader returns every history kind, rather than hiding terminal facts to
manufacture parity. The existing acceptance/observation/runtime installers do
not select this new profile.

## Fixed normal-completion transaction

`HistoryEffects` takes H operation ownership before native guards/transaction,
validates independent history/SQL parity and the supplied original Claim, then
uses `TaskStore._worker` with its unchanged lifecycle and inference guards. The
existing quarantine prelude runs before any completed-text prepare. Only the
normal path appends the durable negative fact, runs the extracted fixed native
`_stage_completed_body`, and stages the protected projection in that same SQL
transaction. There is no generic callback, H import in TaskStore, autonomous
COMMIT or post-stage readback.

Success requires known top-level COMMIT, actual SQL/file cleanup, H release and
the original absolute deadline. Lost append/COMMIT/release acknowledgement,
late completion, cancellation or cleanup failure blocks the original owner;
physical work drains before ownership is released. Native transaction errors
retain their commit-attempt/acknowledgement flags and underlying history cause.
No retry, replacement ID, readback promotion or writer reset is supplied.
Directly cancellable admission waiters and physical cleanup supervision remain
unchanged. No provider/network call occurs under H ownership.

Public `TaskStore.complete` and unselected H1/v2 behavior remain compatible.
Native control refusal may settle through a separate `finish_refusal`
transaction; interrupted-invocation quarantine is also not normal completed
text. Both retain their existing outcomes and are explicitly outside this
terminal fact. A known control result is not reclassified as successful text.

## Synthetic evidence and remaining gates

`test_terminal_history_model.py` and `test_terminal_history_guards.py` exercise
closed framing, mixed replay, strict old profiles, task-level deduplication,
negative controls, fixed-seam restrictions and import/constructor no-I/O.
`test_terminal_history_postgres.py` uses owned synthetic PostgreSQL/POSIX
fixtures for atomic text/projection commit with and without a catalog, actual
whole-SQL restore, unknown COMMIT outcomes, physical append/reset/release
failures and cancellation, historical refusal, native control compatibility,
capacity and principal/ACL/binding/backfill refusals. Default CI skips opt-in
database cases without `--compute-metadata-postgres`; exact executed commands
and counts belong to the accompanying local receipts, not this coverage list.

This proves neither arbitrary privileged selective-tamper detection nor text
integrity after a restore. Cards, durable partial checkpoints, all control-only
terminal outcomes, controlled reconciliation, authenticated runtime composition,
original ACL/key/account/file continuity and live restore qualification remain
open. No real grant, migration, provisioning, deployment, recovery action or
activation follows from source tests or matching metadata.
