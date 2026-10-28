# R0 — output identity reservations

Owner approved 28 October 2026 for #489/#471, after the bounded design review.
This is an **uncalled source-only prerequisite**, not a working resource catalog,
accepted-input path, namespace admission, publication, or completed deletion fence.
It implements the smaller reservation slice of increment 3 in
[artifact continuity](ARTIFACT_CONTINUITY.md). Feature status stays **Not started**.

## Schema and ownership

Migration `051_artifact_output_reservations.sql` adds two account-content tables:

- `artifact_output_jobs`: random `job_id`, `account_id` (user cascade), original
  `account_generation`, historical `task_id`, stable app-owned `operation_id` and
  DB creation timestamp. `(account_id, task_id, operation_id)` is unique across
  attempts and generations. This operation is not an arq delivery or tool fence.
- `artifact_output_intents`: random intent/resource/version IDs, stable output
  slot, account/generation/task/job composite FK, historical lifecycle/attempt ID
  and epoch, closed `generated_file`/`text/csv`, measured bytes (0–8 MiB), encrypted
  descriptor (maximum 128 KiB token), DB timestamp. Each account/job/slot has one
  command; resource/version IDs are unique. Only a new resource's first version
  can be reserved here; later versions need a separate operation.

No task/attempt/conversation FK cascades: deleting chat preserves these account
reservations, and readback returns historical identity only. It does not establish
a live source link; a future owner-facing read must distinguish `source_deleted`.
Account cascade removes DB content, **not files**. Reset inventory explicitly
includes both tables. There is no cleanup, retention or reset implementation.
The logical IDs eventually become unified resource/version IDs and #487 aliases.

## Descriptor and immutable command

`orchestrator/artifact_reservations.py` accepts an explicitly injected Fernet cipher.
The schema-1 descriptor is bounded to 64 KiB plaintext, with exact fields:
`schema_version`, `account_id`, `account_generation`, `job_id`, `operation_id`,
`task_id`, `intent_id`, `output_slot`, `resource_id`, `version_id`, `lifecycle_id`,
`attempt_id`, `attempt_epoch`, `representation`, `mime`, `measured_bytes`, `sha256`,
`filename`, `provenance`. Provenance has only `original_task_id`,
`original_attempt_id`, `kind` (`deterministic_csv`); IDs match the outer descriptor.
All identity/representation/size fields match the immutable command. Filename
(at most 1,024 UTF-8 bytes) and checksum stay encrypted, never lookup indexes.
No text, provider URLs or extensible presentation metadata are accepted.

Canonical ASCII JSON uses sorted keys, compact separators, ASCII escapes and no
NaN/Infinity. UUIDs are lowercase hyphenated strings; numeric fields are exact
integers, never booleans. Reject duplicate/unknown keys, unpaired surrogates and
noncanonical re-encoding. Unicode codepoints are preserved without normalization;
filenames are metadata, never paths. Whole bounded Fernet authentication precedes
schema checking. Size/checksum metadata does not itself verify stored CSV bytes.

Allocate IDs and encrypt once before submission. Retain the **exact command,
including ciphertext**, for retry/readback. Changed ciphertext, even encryption
of equivalent plaintext, conflicts. Separate operations with identical bytes
retain different IDs; no content-hash deduplication. This internal delivery
contract is not the future canonical-input acceptance API.

## APIs, locks and uncertain commits

`OutputReservations` exposes:

- `register_output_job(conn, identity, job: JobBinding)`;
- `reserve_output_intent(conn, identity, command: IntentCommand)`;
- `readback_output_reservation(conn, job, command=None)` (job-only reconciliation
  when no intent was submitted).

These are trusted-principal methods, not authentication or account-facing APIs.
They take a caller-owned READ COMMITTED transaction and never commit, rollback,
retry, create a pool, fetch settings or perform external effects. Every write,
including duplicate delivery, checks the exact registered lifecycle and actual
current task/attempt/lease under database locks. Caller generation is the original
account generation, **not `tasks.content_generation`**, which is output lease epoch.
Never recapture it or transfer an old slot to a new attempt to cure a refusal.

Write order reuses lifecycle 049: account advisory lock, account UPDATE, open
operations, lifecycle, task, attempt, compute metadata, job, intent. Account locking
serializes R0 deliberately; no SHARE-to-UPDATE upgrade. Separate post-wait state
checks and wall-clock lease checks follow job/intent waits and INSERT uniqueness
waits on other accounts' invisible uncommitted global IDs; expired INSERTs roll
back atomically before returning. Task lock holds
epoch stable; lease expiry is checked before the write, not promised at the instant
COMMIT returns. Commit promptly; no external work inside this transaction. A new
method invocation reacquires/revalidates all authority after savepoint rollback.
Refusal/cancellation leaves rollback to the transaction owner.

Write returns `pending_commit` and `inserted`, plus exact immutable row observations.
Readback returns `present`, `absent`, or `job_present_intent_absent` after account
serialization. History after task deletion/lease expiry supplies no current
authority. Missing account is unavailable; ID/slot/argument mismatch is conflict;
DB/connection/timeout failure is unknown, never inferred absence. Closed errors
do not include private arguments, ciphertext or database error text.

Unknown COMMIT: close mutation admission, retain the exact packet, safely dispose
of the original connection, reconcile on a fresh transaction. A still-running
original transaction blocks readback on the same account lock; timeout remains
unknown. Established absence permits only an explicit same-packet submission
through fresh authority checks. Readback cannot resolve database backup rollback
or unprojected external journal entries; runtime remains gated on their authority.

## Privileges and qualification limits

Narrow SECURITY DEFINER functions use `pg_catalog` search path and qualified
objects. Migration rejects unsafe default ACLs and revokes PUBLIC function/table
privileges atomically; it creates no roles/grants for production. The trusted
Python wrapper rejects owner/superuser/CREATEROLE/BYPASSRLS, server-file/program
roles, nested ownership membership and direct reservation/lifecycle-table access.
Only test roles receive explicit public-operation grants. Helpers are not callable.
The SQL principal trusts opaque ciphertext; authenticated descriptor validation is
the Python boundary, not SQL cryptographic proof. No API permits mutation/deletion
of existing reservations. DB owners remain trusted, not sandboxed by this module.

The writer catalogue explicitly inventories both tables as content and the
conservative Python candidate as uncovered. Its scanner does not follow SQL
functions: `artifact_output_register` and `artifact_output_reserve` are the two
actual insert paths and are separately inspected/tested. Classification is not
proof that runtime writers or deletion/reset are fully fenced.

Pure descriptor tests and opt-in `--compute-metadata-postgres` tests exercise
real synthetic task acceptance, lifecycle authority, narrow roles, duplicate and
conflict behavior, account fencing, lease waits, chat/account deletion, savepoint
rollback and lost COMMIT. The fixture uses only a fresh owned network-none cached
PostgreSQL container and wire bridge, never an application DSN. Full repository
gates and final source review remain separate completion evidence.

The isolated-schema fixture adapter explicitly relocates migration 051 alongside
048–050; regression checks exercise clean full-chain template creation and clone
with private reservation tables/functions and unchanged PUBLIC privilege grantees.
This test-only relocation does not alter the production migration or replace its
separate original-`public` schema qualification.

No reservation includes a namespace, availability, ready flag or publication
receipt, and no module imports it at runtime. Journaled namespace creation and
retirement, pending-entry reconciliation, storage durability, publication/repair,
accepted-input atomicity, retention/quotas/cleanup and cross-device qualification
are later increments. Nothing here enables staging or generation.
