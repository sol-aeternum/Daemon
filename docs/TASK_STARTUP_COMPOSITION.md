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

`tests/fixtures/exact_worker_privileges.json` pins the proposed ordinary W map;
`tests/exact_worker_privileges.py` applies it only to owned synthetic databases.
The shared backend/worker W role includes ordinary auth, content and billing
capabilities. The five extra column UPDATE grants are real mutation authority,
including four row-lock requirements and the ownership interval trigger write;
they must not be described as read-only access. No live grant is supplied here.

The real-PostgreSQL tests cover the effective ACL map, ordinary lifespan/auth,
owner isolation, budget settlement, seeded-spend refunds, projections, audit,
identity retention and snapshot locking, plus operation-specific grant refusal.
Video debit's separate integer-cast repair and its regression tests belong to a
separate draft; seeded refund setup does not claim debit passed on this branch.
This is partial qualification: full retention/GC, attestation insertion/negative
sequence cases, device-revoke routes and operational restore remain outstanding.
The existing independent-dispatch/restore and #477 gates are not waived.
