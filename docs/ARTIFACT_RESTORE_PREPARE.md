# Uninstalled artifact catalog restore evidence

Owner-approved source/synthetic continuation of the
[restore characterization](ARTIFACT_RESTORE_BOUNDARY_QUALIFICATION.md). It adds
negative evidence before first-output CSV catalog/file mutation. It installs no
runtime host, listener, HTTP/client contract, settings, credentials, grant,
initializer, recovery command or activation. H-configured execution remains
observation-only. #477 remains open.

## Closed, explicitly selected mixed-version profile

Default `h1` encoding, store/coordinator selection and projection types remain
strict. The explicit `artifact-v2` profile accepts unchanged v1 entries and one
new v2 kind, `ARTIFACT_PREPARE`, actor `backend`. Its digest domain is
`daemon.restore-history.frame.v2\n`; the predecessor digest still binds the
complete mixed chain. Other v2 kinds, a v1 artifact kind, wrong domains, unknown
fields/versions, aliases, noncanonical JSON and duplicate keys refuse. No v1
prefix is rewritten or silently upgraded. The caller supplies the profile and
expected identity independently; neither is inferred from SQL or stored bytes.

The new record contains only these closed fields:

- `account_id`, `account_generation`, `task_id`, `acceptance_record_id`;
- original `epoch`, `lifecycle_id`, `financial_scope_id`, `metadata_scope_id`,
  `producer_instance_id`;
- retained `intent_id`, `resource_id`, `version_id`, `job_id`,
  `source_operation_id`, `output_slot`, `original_attempt_id`;
- `operation_schema=first-output-csv-catalog-v1`.

IDs must be canonical non-nil UUIDs; integers exclude booleans. Existing signed
int64, generation/epoch and output-slot bounds apply. There is no prompt,
filename, path, CSV/content, ciphertext or plaintext digest in H. This is not an
independent content-integrity commitment or a reconstruction store. Original
native encrypted descriptors/file authentication remain required.

One intent has at most one catalog prepare in a history, regardless of a new
epoch, request ID or record ID. The original acceptance and generation must
match; a prior Stop or open restore forbids preparation. Exact low-level store
duplicates remain historical `created=False`, never fresh creation permission.
The joined catalog path refuses matching historical intent or existing catalog
instead of adopting it. A trusted issuer still cannot wrap old work as fresh.

Existing 8 KiB payload, 16 MiB log and 16,384-record prototype caps stay fixed.
There is no pruning, rotation, eviction, capacity increase or SQL-only fallback.
Capacity fails before artifact file mutation. Control headroom, retention and
full-scan latency remain deployment qualification decisions.

## Source SQL and exact authority

`064_artifact_restore_prepare.sql` follows occupied migration 063. It extends the
existing immutable non-cascading common projection's closed kind constraint;
it adds no table, ordinary content permission, service login or binding row.
New private/entrypoint functions are:

| Function | Intended direct service execution |
| --- | --- |
| `task_history_decode_artifact(bytea)` | None; private typed decoder |
| `task_history_artifact_project(bytea)` | Exact backend P only |
| `task_history_output_page(uuid,bigint,text)` | Exact A/W only |

The migration revokes default PUBLIC execution and rejects unsafe owner defaults;
it creates no application grants. Stage checks reuse current native P scope and
original acceptance/attempt/job/intent/version binding. The native intent lock
serializes direct competing P calls. Historical intent, Stop, missing predecessor,
wrong native binding, or a catalog from an earlier transaction refuses. The
protected row is staged on the same caller-owned P transaction as catalog rows.
No autonomous commit or result backfill is provided.

The same-transaction check accepts visible native rows whose insertion transaction
is still in progress, including the native stager's subtransaction. It does not
equate a tuple's subxid with only the top-level XID. PostgreSQL documents that
`pg_current_xact_id` returns the top-level ID even inside a subtransaction, and
describes [transaction-status functions](https://www.postgresql.org/docs/16/functions-info.html#FUNCTIONS-TXID-SNAPSHOT).
Owned PostgreSQL tests verify the subtransaction behavior and reject retroactive
projection of an already committed catalog. This status inspection happens before
COMMIT solely to reject backfill; it is never lost-COMMIT readback or permission
to promote an uncertain caller outcome.

The bounded complete reader requires independent history ID/key-check, returns
ordered pages of at most 32 and preserves original acceptance-index checks. It
does not filter v2 entries to manufacture parity. `ArtifactHistoryProjection`
qualifies all 13 history functions, current connections, closed A/W/P grants and
existing immutable table shape. Publisher is an independently supplied, required,
distinct principal; it cannot be learned as configuration from SQL grants.

The native P principal checks preserve migration 062's NOINHERIT/membership,
table/column/sequence, owner, temp-schema and lifecycle/compute exclusions. Their
only new exception is the exact direct P artifact-stage grant; private decoder,
A/W/R0, inherited alternative grants and other history capabilities remain
forbidden. P receives no A/W authority or K1; W receives no P credential or
backend private storage/key access. Actor labels/checksums are not signatures;
the cooperating trusted-single-host threat model is unchanged.

## Joined operation and failures

`HistoryEffects` admits only its two exact projection types, not arbitrary
subclasses. History/coordinator and projection profiles must match. Default H1
behavior is unchanged; the v2 profile stays unselected. Runtime
construction/injection admission is unchanged.
The lifecycle source guard permits its name prefix only inside this module's
fixed `PUBLISHER_CATALOG` metadata predicate, using the existing catalog-only
checks. Negative tests deny imports, aliases, lifecycle calls and moving that
prefix into another constant. This is not permission to invoke lifecycle APIs.
The v2 catalog operation:

1. Acquires H OP before native transaction/locks, verifies complete H/SQL parity,
   independent identity, captured origin/generation and negative Stop fences.
2. Borrows the actual P transaction, validates its current principal and original
   sealed intent/source, and refuses an existing publication/historical intent.
3. Durably appends the negative prepare under short APPEND ownership, retaining
   H OP and the original absolute deadline.
4. Runs existing authenticated namespace/J1, bounded encrypted file and native
   catalog checks, then stages the exact protected frame on that same connection.
5. Requires known top-level COMMIT, actual driver/file cleanup, H release and an
   unexpired logical deadline before returning a candidate. An installed packet
   owner would still own interested-caller settlement; none is installed here.

Lost append/COMMIT/release acknowledgements, cancellation, late completion or
cleanup failure block the original owners. Physical work remains supervised until
it actually settles; logical expiry does not promise bounded kernel cleanup.
No lock is held across a provider/network call. Durable H plus missing SQL is
needs-attention evidence, not permission to retry. Matching H/SQL after an unknown
committed outcome likewise cannot become success through readback or a fresh
owner. Files without catalog remain evidence; nothing repairs, adopts or deletes
them automatically. Retained established RAM replies are separate from a new
catalog creation call and cannot be reconstructed after restart.

## What restoration does and does not establish

Actual synthetic whole-SQL dump/restore tests show that retaining the new H fact
while rolling SQL back before catalog refuses before publication, with either
newer or older artifact files. SQL/catalog/projection rows commit atomically;
the same task/intent/version cannot become fresh through replacement IDs.

A catalog checkpoint can still lose a later card, durable partial text or terminal
result while its catalog fact matches. The tests deliberately preserve that
counterexample. No independent terminal/presentation/partial fact, new historical
observation policy or reconciliation is implemented. Selective table restoration,
arbitrary owner data edits, coordinated H/SQL/file rollback, financial/auth/account/
key continuity, live lifecycle/retention and offline downloaded data erasure are
not qualified by parity. Existing structural `RESTORE_ASSESSED` is not recovery
or dispatch permission. Stop prevents subsequent local output, not guaranteed
remote cessation or billing cancellation.

Migration 064 is not a drop-in live upgrade: legacy strict SQL profiles reject the
expanded history function set. This slice installs no startup profile fallback.
Future live migration, explicit compatible startup/read/write composition,
credentials/grants, storage/key provisioning, controlled restore assessment,
retention and activation require their separately approved qualification.

## Verification scope

Tests cover strict version/profile selection and v1 compatibility, negative
identity, no-I/O construction, runtime noncomposition, torn/fsync/rename append,
actual PostgreSQL catalog/projection atomicity, dump/restore, historical refusal,
lost acknowledgements, physical cancellation/reset/release, concurrent calls,
capacity and privilege/control fences. Exact executed counts and failed receipts
are reported separately. The default suite skips opt-in database cases; passing
default CI is not their execution. No live restore, distinct-UID deployment,
provider inference or daily-driver acceptance is claimed.

Integration includes the exact pending native-profile head `221533b3` (#685)
and restore-qualification head `9c0b4e9c` (#687), plus main `db3eee0d` (#686).
The native profile's phase/fault assertions also run with an explicitly selected
v2 test fixture; the legacy H1 cases remain. This does not install the profile,
authenticate a real worker, resume an old assignment or qualify live recovery.
Executed results and publication dependency status are reported separately.
