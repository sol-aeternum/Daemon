# Separate durable task startup — approved source contract

The owner approved this separate-service design on 29 October 2026. Backend and
worker now select explicit qualification during normal startup. This is source
and isolated-test evidence, not deployed readiness or approval to provision roles,
run application migrations or change the deployed source.

## Selection and ownership

`orchestrator/tasks/startup.py` consumes three settings:

| Setting | Backend | Worker |
| --- | --- | --- |
| `DAEMON_TASK_ACCEPTOR_ROLE` | Expected A login name | A name for catalog checks only |
| `DAEMON_TASK_WORKER_ROLE` | Expected W login name | Expected actual W login |
| `DAEMON_TASK_ACCEPTOR_DATABASE_URL` | Backend-only A credential | Must be absent/empty; nonempty refuses |

`DATABASE_URL` selects W in both services. Names are distinct lowercase PostgreSQL
identifiers, at most 63 ASCII characters. Both services require configured
`DAEMON_DELETION_JOURNAL_DIR` and `DAEMON_DELETION_JOURNAL_ID`. Partial or malformed
configuration refuses before pool creation. `DURABLE_CHAT_ENABLED=true` without
this configuration refuses. Default factories perform no I/O.

With no task configuration and NEW disabled, startup queries only the catalog for
absence of `public.task_inference_recovery`. Only literal positive absence allows
the old pre-050 startup path, still subject to the existing mandatory-DB J1 gate.
Missing or uncertain evidence refuses. Removing configuration after 050 cannot
turn recovery into legacy execution. This catalog test does not detect a database
restored to an earlier history.

Backend owns W and A pools. It checks both actual authenticated connections,
expected distinct names, same observed database identity, protected schema/ACLs
and ordinary operation privileges before either journal barrier can write a
binding. A may initialize the configured J1 binding after file/marker checks; W
requires that matching binding. A failed or cancelled qualification publishes no
bundle and closes every created pool. Shutdown attempts both pool closes.

Worker owns only W. It receives no A pool, acceptor store, credential or role
membership. Its factory checks actual W identity and authority, current A login
and capability catalogs, protected schema/ACLs and required ordinary privileges,
then the existing matching J1 binding/files/markers. An unbound journal refuses
with `binding_required`; the worker cannot create the binding. Mismatch, pending
journal entries, changed unsafe privileges or missing evidence refuse before
pepper initialization or background jobs. Catalog observations do not authenticate
A and do not replace backend or per-operation current-connection checks.

## Turning new submissions off

`DURABLE_CHAT_ENABLED=false` prevents NEW durable acceptance. Qualification stays
selected. Existing owner-scoped task observation, Stop, worker recovery and original
hold settlement remain available after restart. Keep roles, DSNs and journal
configuration. No dispatch witness is cleared, no uncertain task is automatically
resent, and the flag is not rollback or evidence that remote work/billing stopped.
Existing immutable dispatch, invocation and SQL fences remain in force.

The source inventory admits exactly the backend paired factory call and worker
W-only call plus existing factory constructors. Tests reject alternate callers,
alias activation, wrong functions, additional A inputs and crossed pools. This is
a discriminating known-source inventory, not a Python execution sandbox.

## Qualification and remaining operational decisions

`tests/test_durable_startup_preflight.py` exercises failure ordering and pool
ownership without database I/O. `tests/test_task_startup_postgres.py` exercises
actual service startup against the owned network-none synthetic PostgreSQL fixture,
including W without first-binding privilege, both flag states, restart/Stop,
identity/pending-entry failures and current privilege drift. The paired composition
suite retains authenticated HTTP, immutable dispatch, partial-output and original
funded-hold recovery coverage, including a fresh W-only startup with NEW disabled.
Scripted remote continuation is not real-provider cessation evidence.

Before operational use, separately approve exact role/credential/grant and migration
actions, then qualify the resulting pinned deployment. Use separate limited A/W
logins; retain the existing database owner only for reviewed migrations. A gets
only the four coverage INSERT columns and audited ordinary requirements. W gets
the exact existing public entrypoints and audited ordinary requirements, with no
protected direct grants, helper/owner membership, A credential or first-binding
write privilege. A generic `ALL TABLES`/`ALL FUNCTIONS` grant is incompatible.

The current generic Compose path still exposes its repository through a bind
mount and carries legacy database-owner environment fields. This change documents
and injects the task settings; it does not certify credential isolation. Merely
omitting an A variable from `worker.environment` is insufficient if a shared
`.env` or source mount remains readable. A pinned runtime image and reviewed
service-specific credential sources/mounts are still required. Do not run the dirty
legacy checkout as this code or silently switch its revision.

J1 requires separately provisioned private durable storage and a stable identity.
It is the deletion journal, not independent inference dispatch history. Restore
must block dispatch until independent journal/dispatch evidence is reconciled;
matching J1 alone does not establish that. Missing #477 account/cache/lifecycle,
backup/restore, privilege and deployment qualifications are neither implemented
nor waived here. Conditional owner authorization to enable durable chat applies
only once readiness is established and actual state is read back.

## Exact ordinary-role fixture qualification

`config/task_privileges/worker.json` pins the ordinary W map and
`config/task_privileges/acceptor.json` the A map. `tests/exact_worker_privileges.py`
applies them to owned synthetic databases with the same statement builder as the
live provisioning script (below).
The shared backend/worker W role includes ordinary auth, content and billing
capabilities. The five extra column UPDATE grants are real mutation authority,
including four row-lock requirements and the ownership interval trigger write;
they must not be described as read-only access. Live grants come only from
`scripts/provision_task_roles.py`.
The map covers the memory revision objects from migration 066 and the summary
provenance tables from 072: the memory triggers run with the caller's rights, so
W writes revision, mutation and clock rows directly. Its UPDATE on those
tables is limited to the columns the code changes: clock `clock_regressed`,
`erasure_epoch`, `last_clock_at` and `sequence` (the clock is also row-locked
`FOR UPDATE`), mutation `observed_committed_by`, segment `content_tsv`,
`embedding` and `embedding_model`, tombstone `hard`, and summary version
`revoked_at` and `sealed`. It also covers reminders (067) and the account-owned
skills tables (068, 070): skills and skill revisions get SELECT, INSERT and DELETE
plus UPDATE on the columns the skills service writes; the store key and shipped
catalogue get SELECT, INSERT and UPDATE on their two changing columns.
Memory erasure (`orchestrator/memory/lineage.py`) runs as W on every memory
delete, so the map also grants DELETE on entities and UPDATE on exactly the
columns erasure scrubs: extraction log `dedup_results`, `extracted_facts` and
`input_snippet`, and retrieval log `candidate_memory_ids`, `candidate_scores`,
`query_embedding`, `query_text`, `retrieval_context` and `selected_memory_ids`
(#740).
`proposal_sha256` is the SHA-256 of the map without that field, serialized as
sorted, compact JSON.

## Live role provisioning

`scripts/provision_task_roles.py` creates both LOGIN roles (no superuser, create
or replication attributes, no memberships) and applies the two maps in one
transaction. Before COMMIT it checks every public table, view and sequence for
each role: effective table and column privileges must equal the map exactly,
mapped functions must be executable, and the protected task functions must not be
executable except where W's map lists them. `verify` repeats that check read-only.
Both commands first require the migration runner's complete history:
`_migrations` must equal the sorted `migrations/*.sql` set exactly (a hole, an
unknown row or a missing table refuses with `migration_history_incomplete`; the
runner records filenames only, no checksums). They hold the runner's advisory
lock for the transaction, so a concurrent migration refuses
(`migration_in_progress`) and none can land before COMMIT. It also refuses an
existing role, a tampered map digest, and secret files that are not private
regular files. Passwords are sent as client-computed SCRAM-SHA-256 verifiers.

The A map holds what startup's ordinary checks require and what NEW acceptance
runs: SELECT on the account fence tables, task attempts and journal markers;
SELECT/INSERT/UPDATE on tasks, conversations and messages; SELECT/UPDATE on
entitlement accounts; SELECT/INSERT on task events and the journal binding (the
backend creates the first binding); and the four-column INSERT on
`task_inference_recovery`. It grants no DELETE and no function EXECUTE.

`tests/test_task_role_provisioning_postgres.py` applies both maps to a database
with every migration except the restore-history ones (056, 057, 064, 065; see
#731) and reruns the real startup, acceptance, worker and exact-W suites against
it. That history is deliberately incomplete, so `apply`/`verify` refuse it and
have no test-only bypass. `tests/test_task_role_provisioning_chain_postgres.py`
runs them, as a superuser and through the CLI, on a database migrated by the
real runner through the latest file (H included), including the hole, unknown
row and concurrent-migration refusals. A database that holds the
restore-history schema refuses unselected startup, so neither is evidence that a
fully migrated host can run.

The real-PostgreSQL tests cover the effective ACL map, ordinary lifespan/auth,
owner isolation, budget settlement, seeded-spend refunds, projections, audit,
identity retention and snapshot locking, plus operation-specific grant refusal.
Video debit's separate integer-cast repair and its regression tests belong to a
separate draft; seeded refund setup does not claim debit passed on this branch.
This is partial qualification: full retention/GC, attestation insertion/negative
sequence cases, device-revoke routes and operational restore remain outstanding.
The existing independent-dispatch/restore and #477 gates are not waived.

## Selected H observation-only host

[H startup](RESTORE_HISTORY_RUNTIME.md) now wraps paired qualification with
independent H/W identity and parity checks before native recovery writes, and
retains actual pool reset completion. Selected workers deliberately refuse
startup after qualification: the observation-only backend is not execution
readiness. Unselected services must prove H schema absence, or an exactly
shaped unbound H schema (#731), before recovery. They first open the native
fence presence connection and guard every pool acquire with it, so migration 073
refuses a binding for as long as any unselected process runs; losing the
presence connection exits the process.

The [H bootstrap qualification](RESTORE_HISTORY_RUNTIME.md#application-startup-and-bootstrap-boundary)
uses the full application lifespan and a fresh supported-launcher process, with
synthetic H file ownership/material and restricted SQL logins. Its H fixture adds
separate history-function grants beyond the ordinary W map; the broader A fixture
is not a deployment grant proposal. Expected bootstrap refusals remain intact.
These tests neither activate execution nor establish complete application or
physical-phone acceptance.
