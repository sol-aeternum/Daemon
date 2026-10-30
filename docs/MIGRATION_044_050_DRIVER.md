# Closed 043→050 migration driver — source only

`python -m scripts.migrate_044_050 plan` validates the fixed release locally and
prints its source digest and filenames without connecting or reading credentials.
It admits the exact 44 recorded filenames through 043 (including both 036 files)
and executes only the seven SQL payloads pinned in
`config/migration_044_050.json`. Migration 051 is never read or applied. An
existing 051 bookkeeping row or either of its tables/six function names refuses.
There is no arbitrary manifest/path/range option, baseline repair or resume mode.
The generic `scripts/migrate.py` and Compose behavior are unchanged: they still
glob pending SQL and must not be used as substitutes for this closed upgrade.

This source and its synthetic tests do not authorize application execution,
credentials/grants, journal provisioning, source deployment or durable activation.
The independent acceptance/dispatch restore-history safeguard is not implemented.
Existing #477 readiness requirements remain. The completed private schema-only
backup rehearsal does not establish original ACLs, key continuity or safe dispatch.

## Explicit inputs for a separately authorized execution

Invoke the module's `execute` subcommand with four required arguments:
`--expect-source DIGEST`, `--target-file ABSOLUTE_PATH`,
`--database-url-file ABSOLUTE_PATH`, and `--receipt ABSOLUTE_PATH`.
No new environment variable is introduced. There is no DATABASE_URL, POSTGRES_*,
application settings, dotenv or password-file fallback. Never place a credential
value on the command line. Both input files must be user-owned single-link
regular files, mode 0600, with no symlink path components and at most 16 KiB.
The connection file contains one explicit PostgreSQL URI with user, password
(explicitly empty only for a synthetic/local trust endpoint), host, port, database
and sslmode. Supported query keys are host, port and sslmode, without duplicates.
Network TLS policy is part of the separately reviewed connection configuration;
the driver does not provision certificates or silently choose a mode.
TLS contexts use system trust and never implicitly load a user's PostgreSQL
client certificate/key. Private CA/client-certificate configuration is not an
added input in this slice and needs separate review if a target requires it.

The target file is a closed JSON object with these exact fields:

```json
{
  "database": "approved_database_name",
  "oid": 12345,
  "principal": "approved_direct_migrator",
  "cluster_id": "1234567890123456789",
  "schema_before": "<reviewed 64-character lowercase SHA-256>",
  "schema_after": "<reviewed 64-character lowercase SHA-256>"
}
```

The values above are illustrative, not deployment facts. Database/role names are
canonical lowercase identifiers. Expected source bytes, endpoint, primary cluster
system identifier, database name/OID and direct CURRENT_USER=SESSION_USER must be
independently reviewed. Reading `pg_control_system()` requires the migrator's
existing capability; absence refuses, never grants it. These identity checks do
not establish cross-cluster uniqueness or independently detect a database restore.

Schema expectations are target-specific reviewed definition fingerprints from
separate baseline/rehearsal qualification, not values to copy from a failing live
run to make it pass. `catalog_digest` observes public relations/owners/ACLs,
columns/defaults, constraints/validation, indexes, non-extension function
definitions/ACLs, triggers, sequence parameters (not current values), extensions
and default ACLs in a bounded read-only repeatable-read snapshot. It normalizes
the deparsing search path. Only the digest is retained; application rows, function
bodies, sequence current values and credential catalogs are never emitted.
Extension internals, role memberships, every PostgreSQL object kind and arbitrary
DBA alterations are not certified. Role/deployment qualification remains separate.
There is no expectation-authoring or live auto-attestation mode in this driver.

The source digest binds the fixed manifest, SQL, driver and shared helper bytes
using length-framed relative paths. Review the release/commit/image provenance
separately; generating a digest is not approving it. All payload bytes are read
and checked before connection, then those same immutable strings execute.

## Execution, bounds and receipts

The caller must separately prove quiescence of application and maintenance
writers. The shared try-advisory migration lock excludes cooperating generic and
dedicated runners; it is not an arbitrary-writer or restore lock. Existing
bookkeeping is read, never created/reset. The baseline schema digest must match
before DDL. Unqualified CREATEs target public while PostgreSQL's implicit
pg_catalog-first lookup preserves the reviewed builtin function bindings.

Each file's SQL and filename insertion commit in one existing helper transaction.
There is no whole-batch transaction. Session lock/statement bounds are 5s/60s;
048–050 preserve their stricter 1s/5s SET LOCAL limits. Connection admission is
bounded at 5s, catalog snapshots at 10s, and each complete file call at 63s with a
65s driver command cap. Commit/release/cancellation consumes these bounds; no
automatic timeout extension or retry is supplied. Cooperative cancellation and
connection termination are not guaranteed remote-query cessation or rollback.
These budgets initiate cooperative cancellation; blocking filesystem work or
driver transaction cleanup may outlast them. They are not hard process-exit caps.

The receipt is a fresh O_EXCL mode-0600 JSONL file in an existing user-owned
mode-0700 directory, with no symlink ancestors. Existing receipts are not reused,
truncated or overwritten. Creation and each append are synced. Events include
source/expected target facts, preflight PID, file start, a pre-COMMIT record,
acknowledged commits and either completed postflight or a closed stopped result.
No SQL text, connection URI, credentials or application values enter receipts or
error output. A completed result requires the exact final 51 filenames (watermark
050, not migration 051), expected post-schema fingerprint, owned lock release
and successful connection close. CLI output is JSON with closed error categories
and SQLSTATE, never a raw driver exception.

## Failure and reconciliation limits

Any error, timeout, cancellation, unknown COMMIT, receipt failure, postcondition
mismatch or uncertain unlock/close stops the batch. Later files never execute.
Earlier acknowledged files remain committed. A COMMIT-pending failure is uncertain
even when the server actually committed; readback never becomes a new permission
to execute. Acknowledged commit followed by receipt failure is not rollback.
Cleanup is requested once on the owned connection; cleanup uncertainty is retained
without replacing the primary failure. Cancellation propagates its original
exception. Incomplete/unreadable receipts and missing completion are not success.

Keep all receipts and obtain independent read-only schema/bookkeeping/connection
evidence before a separately approved continuation. Never remove a bookkeeping
row, manufacture a completion receipt, discard evidence, patch live SQL or invoke
the generic runner to continue. The baseline precondition rejects partial and
already-completed batches. A fresh receipt filename does not authorize another
attempt after uncertainty. Receipts are evidence, not an independent global run
registry: this driver cannot detect lost operator receipts or a restored database
that looks like the original baseline. It supplies no destructive rollback,
restore attestation, journal provisioning or durable-chat readiness claim.

## Qualification

`tests/test_migration_044_050.py` exercises offline source/private-input boundaries;
the existing runner lock tests retain default helper compatibility.
`tests/test_migration_044_050_postgres.py --compute-metadata-postgres` uses only a
fresh positively owned network-none synthetic PostgreSQL fixture, without an
application DSN. It builds a separate reference schema, clones a new 043 database
per test, and exercises actual DDL/bookkeeping, owner/target/schema refusal,
generic/dedicated lock collisions, file rollback, timeout, cancellation and lost
COMMIT/unlock acknowledgements. The existing protocol relay drops actual COMMIT
frames or their replies; external fresh reads distinguish committed/uncommitted
outcomes without retry. Fault tests may lower a test client's deadline or inject
a test-only SQL stall/failure; production limits and approved SQL remain pinned.
