# Synthetic artifact restore boundary qualification

Source baseline: main `a57b5773b27d42d238acbeea4364e03c46ab1d38`. This
test/documentation slice is independent of draft #685. It changes no production
code, SQL interface, permission, protocol, runtime selection or recovery authority.
The H-configured runtime remains observation-only; #477 stays open.

## What the rehearsal actually exercises

`tests/test_artifact_restore_boundary_postgres.py` uses the existing native
preparation, H-owned R0 reservation, P file/catalog, P presentation and W terminal
joins. Each consistent custom-format `pg_dump` contains synthetic fixture data
only. `pg_restore` targets a new, verified-owned database in the same temporary,
pinned network-none PostgreSQL container. It never targets the source database,
uses `--create`, exports globals, or restores private application data.

Before reopening, the healthy original H owner and its actual A/W/P/R0/registry
pools close completely. A fresh H coordinator uses the original independently
held identity/key and ownership, alongside distinct restored W/P/registry pools.
It does not replace a failed owner or clear a blocked state. Expected refusals
remain blocked until teardown. Original newer SQL and files are preserved.

The fixture retains H and J1 independently. It either retains the newer artifact
root or selects a separate copy captured at the earlier checkpoint. No original
file is overwritten or removed to manufacture the rollback. All keys, identities,
accounts, requests, rows and outputs are synthetic. No provider call follows the
possible-Send control.

## Observed boundaries

The four SQL checkpoints all include the original acceptance, running attempt,
namespace birth and output reservation. Later phases commit normally before the
older archive is restored. The same accepted task ID is retained throughout.

| Restored SQL checkpoint | Artifact files | Actual observation |
| --- | --- | --- |
| After reservation | Newer committed files retained | H acceptance parity still matches, but catalog/card/final text are absent and the task appears running. Original resource lookup is not found. The native catalog call refuses existing staged-file evidence with `needs_reconciliation`; catalog/task/message hashes and file bytes do not change. |
| After catalog | Newer files retained | Original CSV bytes reopen, but later card and terminal text are absent; the task appears running. H does not independently detect those missing later outputs. |
| After presentation | Newer files retained | CSV/catalog/card remain, but later terminal text is absent and the task appears running. H still matches the restored acceptance projection. |
| After terminal | Matching files retained | Original completed task and final plaintext reopen, with the original CSV. This is the consistent control, not live restore acceptance. |
| After reservation | Files also rolled back to the reservation checkpoint | H/J1 and restored SQL look consistent. A deliberate privileged probe of the existing uninstalled catalog join can recreate the same resource/version with a new reference ID. The original newer source database/files remain unchanged. **This asserts an unresolved restore gap, not permission to replay.** |

Outputs currently add no independent H artifact/terminal event in this fixture.
Consequently, matching H acceptance facts and a matching namespace birth cannot
prove that the latest catalog, card or terminal result survived a rollback.
Refusing an old RAM handle is a separate boundary: this test does not invoke
#685, issue a fresh assignment, implement a listener, or authorize historical
packet reconstruction. A future trusted issuer must not wrap old work as fresh.

Three positive safety controls preserve the existing independent evidence:

- A later H Stop survives the SQL rollback and the next native publication
  refuses with `effects_reconciliation_required`.
- A later H possible-Send fact survives and causes the same refusal. The test
  performs no provider/network effect.
- A later J1 deletion request causes `recovery_required`, even though H acceptance
  parity itself matches. No file/catalog/task/message change follows refusal.

These controls do not qualify deletion application, account reset, retention/GC,
real revocation, client authentication, original global privileges or key recovery.

## Schema and privilege evidence

The archive preserves object ownership and grants within the existing synthetic
role cluster; no `--no-owner` or `--no-privileges` shortcut is used. Before backup,
the test independently captures the existing bounded runner's full catalog-query
sections. It compares all captured logical definitions, owners, constraints,
indexes, function bodies/configuration/ACLs, triggers, extensions, sequence
parameters and default privileges after restoration.

A raw catalog digest is **not** assumed identical after `pg_dump`/`pg_restore`.
The consistent control established two representational differences: explicit
owner-only relation ACLs become NULL/default ACLs, and `memories` column numbers
compact across the column dropped in migration 016. Both raw catalogs/digests are
retained. This test's separate comparison resolves relation/sequence grants using
PostgreSQL `acldefault` and `aclexplode`, preserving grantor, grantee, privilege and
grant option; it compares visible columns in their original logical order.
It removes no column or permission from the comparison.

PostgreSQL documents [NULL ACL defaults and decomposed grants](https://www.postgresql.org/docs/16/functions-info.html#FUNCTIONS-ACLITEM-FN-TABLE)
and [physical dropped-column catalog entries](https://www.postgresql.org/docs/16/catalog-pg-attribute.html).
Negative restored-target controls prove that the comparison still rejects an
extra grant, an extra visible column and an added unvalidated constraint.
The live migration runner and its strict raw target expectations are unchanged;
these test comparisons cannot bless a failed live digest or substitute for new
independent target qualification after an actual restore.

## Isolation, failure handling and limits

The existing owned fixture uses the cached pinned pgvector image, network none,
no mounts/ports, bounded memory/PIDs and tmpfs data. Archives are private 0600
files in the owned pytest directory, limited to 16 MiB; CLI work has a finite
deadline and no automatic retry. Archive TOCs must contain no database-creation
entry. Source/target identity, catalog digests and command outcomes are recorded
without application-row samples or archive contents in the handback.

Every successful target closes all fresh owners/pools, verifies zero clients and
drops only its exact generated name. Uncertain setup/cleanup quarantines the
target; it is not retried or blindly dropped. The outer verified-owned temporary
container is stopped on failure as well. This is synthetic fixture cleanup, not
a production restore or destructive-recovery procedure.

Eleven opt-in SQL/POSIX cases cover the matrix and metadata fidelity controls.
The cases require `--compute-metadata-postgres` and never take an application DSN.
Results and full-gate receipts are reported separately. Passing these
characterization tests means the observations are reproducible; it does not mean
artifact restore safety is complete.

## Next decision remains separate

Before execution/runtime installation, define how an approved restore assessment
will independently establish surviving output facts or keep affected work in
needs-attention without replay. Independently retained artifact/terminal facts,
their protected SQL counterpart, bounded retention and controlled reconciliation
would require an explicit contract; this slice implements none of them. Matching
schema/H/J1 alone and a closed source issue are not a release waiver. Real
identities, grants, keys, storage, migrations, deployment and enablement remain
separate operational approvals.
