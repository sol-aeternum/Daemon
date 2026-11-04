# H-owned presentation of a committed CSV

Source-only continuation after #664. `HistoryEffects.present_reserved_output`
joins an existing first-output CSV publication to its pinned C1 presentation. It
does not reserve or publish a file, append a version, complete a task, install a
transport or activate execution. H-configured startup remains observation-only;
#477 stays open. Catalog and presentation commits remain distinct phases.

## Restricted observation capability

Migration `061_resource_output_presentation_references.sql` adds
`resource_output_presentation_references(jsonb,jsonb)`. Existing 058-060 signatures,
snapshots, ciphertext formats and table permissions are unchanged. No login,
application grant, table, key, mount or initializer is created. A future backend P
login must directly hold this seventh EXECUTE capability as well as the six prior
entrypoints. Helpers stay owner-only. All existing principal, membership,
grant-option, temporary-namespace, SET ROLE, database/schema/table/column/sequence
and A/W/R0/H exclusions remain enforced on the current connection.

The function accepts at most 100 distinct canonical resource/version pairs and
returns a closed version-1 object with `output_references`, in input order. Empty
pins retain native observation compatibility. Each pin must identify exactly one
output reference and matching version for the original account/generation/task/
conversation/result-message scope. Missing, duplicate, foreign or contradictory
matches refuse the whole call. There is no cursor, partial prefix, inverse intent
adoption, repair or scan of unrelated references. Returned provenance is encrypted;
SQL does not authenticate that ciphertext. This is source-row-locking observation
inside the caller's READ COMMITTED transaction, not a read-only SQL transaction.

This closes an information gap: 058 returns presentation envelopes, while 059 can
read a reference only from its original reservation intent. Existing pins retain
resource/version IDs, not every earlier intent. Native reference authentication
must not be replaced with plaintext SQL scope checks or skipped for older cards.

## Original-operation composition

The existing backend-only `ReservedOutputPublisher` has optional explicit
`presentation_item_limit` and `presentation_record_limit` construction budgets,
both 1-100 or both unset. Unset preserves the existing catalog-only collaborator;
it cannot perform the presentation phase. No runtime default/quota or environment
key is selected. The existing explicit snapshot byte budget caps encoded 058/061
responses before client decoding. Overflow refuses rather than truncating; this
does not bound SQL construction, parsing or kernel cleanup time.

H lifetime and operation ownership precede native SQL ownership. The join checks
complete H/SQL parity, original acceptance/generation/no Stop, sealed native origin
and exact source targets. The owned P transaction independently observes the
original reservation. The catalog version must already exist and authenticate,
and its bytes must pass the actual J1/complete namespace and private-file checks.
Missing or corrupt state refuses without preparing files or completing that phase.
Short journal/file locks are closed before later SQL calls. No H ownership spans
provider calls or a publication socket exchange.

Every bounded existing presentation is decoded and its pinned reference provenance
authenticated before matching or insertion. Shared pure decoders preserve the
native C1 validation/format; they add no pool or filesystem authority. An exact
committed match returns its original ID, including after lease expiry, as
observation only. Contradictory multiple matches refuse. A fresh record retains
the native current lifecycle/attempt/lease/Stop and source/account fences. The
envelope stays `file_results` v1 with original pinned versions and source IDs.
Presentation timestamps are normalized to UTC for native envelope validation and
encoding, regardless of the SQL session timezone; no stored format is changed.

Known top-level COMMIT and completed actual P reset/release, filesystem work and H
cleanup are required before success. Cancellation/repeated cancellation, deadline,
lost acknowledgement or cleanup failure preserves ownership until real work
settles, retains the original exception and blocks mutating reuse. The pinned
asyncpg cancellation transport is drained before terminating an uncertain SQL
connection; termination must not cancel and detach that cleanup. Cleanup-state
errors are secondary notes, never replacements for the original failure. There is no
automatic retry, fresh identity, owner replacement or readback promotion. A
committed catalog without a presentation remains incomplete. A returned
presentation ID is neither task completion nor dispatch authority.

## Noncomposition and release limits

The exact existing `ReservedOutputPublisher`/`Publication` import is unchanged.
Only the existing H owner's typed presentation seams/cleanup and bounded publisher
internal calls are admitted by the guard. ADMITTED, module allowlists, runtime
construction/injection/activation and callback restrictions remain unchanged.
No HTTP/client contract, worker credential/key/file access or default behavior is
installed. Live migration, seventh-function grants, keys/UID/storage/socket actions
and deployment require separate operational approval.

This does not add independently retained artifact history. Matching H, SQL and J1
cannot establish that a restored database retained all artifact/presentation facts.
Artifact restore/reconciliation and lifecycle/retention/GC/privacy qualification,
reservation/append/split collaborators, authenticated transport and complete runtime
composition remain launch gates. Synthetic PostgreSQL/POSIX checks do not prove
distinct live service-UID isolation or deployed acceptance. Standard hosted pytest
does not enable the explicit PostgreSQL fixture flag; its skips must be reported
separately from the local opt-in qualification.

## Cross-phase qualification

`tests/test_artifact_phase_boundary_postgres.py` runs six owned synthetic SQL/POSIX
cases against merged #668. Known catalog and card commits followed by H-protected
native terminal completion retain the original task, decryptable final text,
authenticated pins and verified file bytes. Stop after the card preserves that
committed file/card while refusing subsequent terminal output. Internal task
readback here is not authenticated HTTP/device or deployed cross-device evidence.

The remaining cases deliberately document the uncomposed reservation boundary.
Native restricted R0 can commit after synthetic protected SQL projection loss,
while the later H-owned catalog phase refuses before files. Native Stop still
refuses a new reservation. Lost reservation COMMIT acknowledgements can leave
either absent or committed R0 rows with the exact original packet retained, but
do not block the separate H owner because it did not own that transaction. No
retry, replacement or file effect follows those uncertain diagnostic outcomes.
Projection loss is an owner-only synthetic simulation, not a full restore drill.

The approved [H-owned R0 reservation join](RESOURCE_OUTPUT_RESERVATION_JOIN.md)
adds exact typed guard seams and actual cleanup retention for selected callers.
The native diagnostics remain deliberately uncomposed and do not change the
existing SQL/R0 promises. The observation-only
barrier, independent artifact restore evidence and lifecycle release gates remain.
