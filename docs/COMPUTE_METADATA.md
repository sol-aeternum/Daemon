# Compute metadata — uncalled storage slice (#477)

This is internal source infrastructure, not a runtime feature or deployment
approval. The implementation is `orchestrator/compute_metadata.py` and
`migrations/048_compute_metadata.sql`. The source-only
[inference quarantine](INFERENCE_QUARANTINE.md) composes its SQL operations in
explicit fixtures; production startup does not construct that consumer. #477 remains
open; `DURABLE_CHAT_ENABLED` activation is not authorized.

## Storage and trust boundary

Exactly two additive tables extend the existing ledger: a metadata scope
(`id`, `account_id`, `created_at`) and a one-to-one reservation binding
(`reservation_id`, `metadata_scope_id`, `runtime_identity`, `dispatch_nonce`,
`created_at`). `metadata_scope_id` is distinct from legacy reservation `scope_id`.
There are no financial copies, parallel holds/debits, status, lease or lifecycle
fields. Cascades point **toward metadata**; metadata never deletes financial
parents or vetoes their deletion. No account provisioning, backfill or reset.

Frozen typed identities and UUID4 factories run before storage awaits. Account
UUIDs and reservation acquisition results must come from trusted backend context,
not client JSON. The application principal can name accounts: these records do
not authenticate end users or establish first-writer execution authority.

New scopes and attachments require an existing active account; new attachments
also require an owned scope and open owned reservation. Exact historical
duplicates remain readable after settlement/suspension and never reopen work.
Locks follow account → scope → reservation → binding; reads take the same account
barrier. There is **no cross-clock ordering or prospective-origin guarantee**.
An old open owned reservation may be explicitly attached without proving origin;
automatic legacy adoption/recapture is still blocked.

## API and outcomes

`ComputeMetadataStore(existing_asyncpg_pool)` performs no import/construction I/O,
does not create pools, consult configuration, start jobs or retry. The immutable
APIs are `create_scope(identity)`, `attach_reservation(identity)`,
`read_scope(account_id=..., metadata_scope_id=...)` and
`read_binding(account_id=..., reservation_id=...)`.

Every method owns a fresh READ COMMITTED transaction. The embedded V7
`ROLE_PRECONDITION` runs on the **caller connection outside definer functions**;
it is not constructor-injectable. Missing schema, unsafe owner/MEMBER privileges,
direct table/column privileges, private-helper EXECUTE or missing operation
EXECUTE grants refuse. Membership checks are conservative even with NOINHERIT,
including implicit/transitive `pg_database_owner`. No role repair or fallback.
The five definer functions fix `search_path=pg_catalog` and qualify their objects.
UP rejects unsafe inherited table/named function default ACLs, then revokes PUBLIC.
PostgreSQL's intrinsic ordinary PUBLIC function EXECUTE is revoked atomically;
the server itself rejects granting EXECUTE WITH GRANT OPTION to PUBLIC.

Each transaction sets lock timeout 250ms and statement timeout 1000ms. Acquisition
has a 0.5s allowance; the cooperative whole-operation deadline is 2s **including
release**, which also receives asyncpg's own 0.5s allowance. No extra cleanup
grace is added. These are responsive-event-loop/driver bounds, not hard real-time
or remote-stop guarantees.

Success requires COMMIT acknowledgement, valid exact FROM-function decoding and
successful release. Only read absence (missing row/NULL primary key) becomes
`None`. Missing/all-NULL/malformed/mismatched write results after acknowledgement
raise `ComputeMetadataCommittedResultUnavailable`, never success.

Every `MetadataError` has `identity`, `commit_acknowledged` (default false),
`code`, `sqlstate` and `termination_failed`. Original write identities are retained
by object identity. Public messages contain no SQL key/detail/query text.

| Evidence/phase | Outcome |
|---|---|
| Invalid UUID before acquire, statement identity conflict/22P02 | `ComputeMetadataIdentityConflict` |
| Statement account unavailable | `ComputeMetadataAccountUnavailable`; no provisioning |
| Acquire/begin/preflight refusal; statement 40P01/40001/55P03/57014/42501/isolation refusal | `ComputeMetadataUnavailable` |
| Unclassified statement failure, client/transport timeout, 40003, any COMMIT-await failure | `ComputeMetadataWriteUncertain` for writes |
| Acknowledged COMMIT, then decode/release failure | `ComputeMetadataCommittedResultUnavailable`, acknowledgement true |
| Non-write failure | Unavailable, apart from precise identity/account errors |

Before release, failures/cancellation synchronously terminate the still-owned
connection. Ownership transfers **before invoking** native pool release; afterwards
the application never terminates, reclaims or retries that proxy. This prevents
premature holder reuse while the driver's shielded reset still runs. Even failure
before native shield creation leaves cleanup unresolved, not application-owned.
Driver cleanup may outlive the application deadline; no extra grace/task is added.
Termination is **not a rollback oracle** or proof of remote query cessation. Termination
failure is exposed on the error/cancellation and in `cleanup_events`; resource
reconciliation is still required. Original `CancelledError` propagates with
`metadata_identity`, `metadata_outcome` (`not_issued`, `uncertain`, `committed`)
and `metadata_termination_failed`. Never regenerate identities or automatically
retry/replay a write/provider operation. Fresh authoritative same-identity readback
is required to resolve uncertainty; missing account/timeout is not absence proof.

## Migration and supported rollback entry

UP runs only under the existing migration runner's transaction/advisory lock
(`scripts/migrate.py`). It creates no production roles, memberships or application
grants. Its 1s lock/5s statement limits do not make FK parent locking load-free;
an operator must qualify an appropriate migration window. Application rollback
leaves metadata and the migration marker intact.

The **only supported schema rollback entry is fresh `psql`**, as migration owner,
outside any existing transaction, executing the **entire file** once:

```sh
psql -X -v ON_ERROR_STOP=1 -f migrations/rollback/048_compute_metadata.down.sql
```

Use operator-managed connection settings for the intended database. Never invoke
this against a live database as a qualification test. This self-contained guarded
transaction takes the runner's advisory key `0x4441454D4F4E`, requires exactly the
048 marker, locks both metadata tables, refuses any metadata rows, drops only
metadata objects **without CASCADE**, and deletes only that marker atomically.
On error, exit/close the connection; never resume remaining statements. Never
delete rows to pass the guard. Populated, uncertain or unreconciled metadata means
application-only rollback. Emptiness alone does not reconcile external clients.
Whole-file asyncpg rollback is not an advertised operator entry.

## Executable checks and explicit limits

Unit tests: `tests/test_compute_metadata.py`. Actual integration tests:

```sh
uv run --locked pytest -q tests/test_compute_metadata_postgres.py --compute-metadata-postgres
```

Use a managed job with private `OPENCODE_WORK_TEST_DIR` and
`OPENCODE_WORK_EVIDENCE_DIR` destinations. The explicit switch launches only a
fresh labelled network-none container from an already cached image; there is no
application DSN or shared database fallback. The finite private Unix bridge reaps
its exact children and verifies stopped containers/backend reconciliation.
Without the switch integration tests skip and are **not qualification evidence**.

Existing durable-task, isolated benchmark and conversation-listing fixtures replay
048 in their own generated private schemas
using the test-only `migration_sql_for_schema` namespace adapter. It preserves
fixture isolation and does not qualify the production public namespace. The
explicit metadata suite above still executes unchanged production SQL in public;
production prerequisites and grants are not weakened by the test adapter.

Tests cover the full source migration chain, restricted principals and exact ACL
restoration, natural lock/deadlock waits, deliberate server trigger faults,
real adapter COMMIT-response loss (including unsent-COMMIT absence), a killed
adapter client with authoritative same-identity readback, and real asyncpg pool
reset timeout. Trigger faults, transport injection and fake phase tests are
labelled separately; a killed process is not an acknowledged commit result.
Actual reset timeout can leave a remote query running after local termination;
the synthetic fixture explicitly reconciles its exact backend separately.

These checks do not prove deployment principal separation, deployed versions or
ledger ownership (all remain **unverified and block deployment**), full plan/trial
requalification, dispatch/claim/admission/launch/stop/transfer/seal/recovery/usage
or settlement authority, hosted-account purge, retention, or 825s outage
completion. No entitlements consumers/arithmetic, environment surface, routes,
providers, settings or deployment permissions are changed. Independent source
review, required local gates, exact-head CI/code review and parent direct review
remain necessary before any merge; no merge or activation is authorized here.
