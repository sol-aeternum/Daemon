# Restricted output catalog authority

Source-only continuation of the approved backend-owned publication design and
[058 presentation capability](RESOURCE_OUTPUT_AUTHORITY.md). Migration
`059_resource_output_catalog.sql` installs no login, application grant, storage,
pool, socket or runtime caller. H-selected startup remains observation-only;
[#477](https://github.com/sol-aeternum/Daemon/issues/477) stays open.

## Exact P interface

The same distinct backend-held P login must eventually have direct, nongrantable
EXECUTE on the two 058 presentation entrypoints and these three entrypoints:

* `resource_output_catalog_snapshot(jsonb,uuid)` locks and returns exactly the
  original reserved output/append intent, job, current source task and sealed
  input, account-generation namespace row, and any committed publication/version
  plus exactly one original output reference in `output_references`. It is bounded original-operation
  observation, including after lease expiry; absent intent, conflicting cross-kind
  slot, foreign identity, missing source or uncertain namespace refuses. It does
  not allocate a reservation, refresh a producer, repair rows or scan files.
* `resource_output_catalog_admit(jsonb,uuid)` returns that same snapshot after
  the existing native current-lifecycle/attempt/lease/Stop assertion. Its locks
  belong solely to the caller's current READ COMMITTED transaction. A returned
  snapshot is not a reusable filesystem, dispatch or COMMIT permit.
* `resource_output_catalog_stage(jsonb,uuid,jsonb)` stages the version, publication,
  original source reference, new resource for first output, and catalog counter in
  the caller's transaction. Existing append intents retain their original resource.
  There is no append-reservation capability in this migration. Final native checks
  include time spent waiting on insertion/uniqueness/FKs. SQL exceptions must abort
  the caller's transaction; no automatic retry or replacement identity is supplied.

The closed stage packet has exactly `reference_id` (canonical UUID string),
`metadata_ciphertext`, `binding_ciphertext` and `provenance_ciphertext`. Ciphertext
fields are strings of 1–131072 encoded bytes each; the whole JSONB text is at most
400000 bytes. All account/generation/job/intent/resource/version/source/ordinal
fields come from locked authoritative records, not this packet. The snapshot includes `next_output_ordinal`; encrypted
provenance must be computed/verified by the future trusted collaborator
under that retained account lock; SQL cannot decrypt it. The caller must inspect
existing committed records before allocating a new reference/token. An exact
original packet can be observed/staged as immutable replay after lease expiry;
changed reference or any ciphertext conflicts. No new effects follow that replay.
Every stage/replay result is `pending_commit` until the caller independently knows
top-level COMMIT and all physical cleanup/ownership release succeeded.

058's clean direct-login checks remain unchanged: no A/W, owner, privileged
membership, SET ROLE, temporary namespace, table/column/sequence authority or native
lifecycle/helper grant on P. 059 also refuses R0 reservation or protected H
function access on P. 059 additionally requires all three catalog entrypoints
to name only that same P directly, without grant option; catalog helpers remain
owner-only. Migration default grants to other roles refuse atomically. Production
SQL and isolated-schema fixture replay are tested separately. No application ACL,
existing A/W predicate or production grant script is changed.

## Composition obligations and incomplete outcomes

This SQL boundary is necessary but insufficient for a publisher. The later backend
owner must authenticate a bounded local worker request **before** H; retain H
lifetime/operation ownership **before** native transactions; independently validate
H identity/key/full protected parity/original acceptance/no Stop; qualify the full
J1 namespace inventory; decrypt/authenticate native origin, descriptors, version,
binding and reference provenance; and use the original captured generation.
The single returned namespace row is **not** full J1 inventory or physical readiness.

Within the retained native transaction, admission must precede verified private
filesystem work, which must settle before catalog staging and final native checks.
Keep same-EUID 0700/0600 storage, exact inode/device/link checks, short filesystem
locks and no DB waits while filesystem locks are held. P does not authenticate
ciphertext or prove bytes exist: it is a trusted internal capability. SQL tests
that intentionally stage synthetic tokens do not claim filesystem qualification.
The positive native-read case separately uses the actual encrypted POSIX path.

The future owner must refuse autocommit/borrowed SAVEPOINT ownership, retain actual
pool-release/reset and physical filesystem completion across cancellation/deadline,
and drain cleanup before releasing H. No success after uncertain COMMIT/reset/H
release, no replacement writer, no readback promotion, no implicit retry. A logical
deadline does not bound kernel cleanup. This migration adds no Python owner and
cannot enforce those Python obligations by itself.

R0 reservation, catalog and presentation remain separate commits. Reservation-only,
files-without-catalog and catalog-without-presentation are distinct incomplete
outcomes, retained under the original identity. Do not delete evidence or complete
a later phase after Stop/expiry merely because an earlier phase committed. Exact
committed lookup is observation only. Source deletion/account reset, independent
artifact restore evidence and retained-file GC remain separate lifecycle gates;
H/J1 matching does not establish retained artifact catalog history. No new retention,
pruning, recovery/adoption or deletion policy is selected here.

## Remaining launch gates

Complete P namespace inventory is supplied separately by
[060](RESOURCE_OUTPUT_NAMESPACE_INVENTORY.md); it is observation only and adds no
file authority. Restricted append reservation/read collaborators,
trusted split input/output composition, backend-owned storage/transport admission,
current-generation authentication and full Send/Stop/partial/terminal/artifact
integration still need implementation/qualification. Then require supported
restore/reconciliation and lifecycle/privacy evidence, exact live migration/ACL/key/
UID/socket/storage approval, pinned deployment and real cross-device/browser/restart
acceptance before durable enablement. No source test here establishes live readiness.
