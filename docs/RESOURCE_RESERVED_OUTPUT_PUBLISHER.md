# H-owned reserved CSV publisher

Source-only continuation of [restricted catalog P](RESOURCE_OUTPUT_CATALOG.md)
and [complete namespace inventory](RESOURCE_OUTPUT_NAMESPACE_INVENTORY.md).
No startup, worker, HTTP, socket or producer installs this collaborator. H-selected
runtime remains observation-only; #477 stays open.

## Narrow operation and ownership

`HistoryEffects.publish_reserved_output` accepts an original `AttemptIdentity`,
existing first-output intent UUID, and at most 8 MiB of CSV bytes. It does not
reserve an intent, append a version, present an artifact card, acknowledge task
completion, execute a callback or perform network/provider work. A returned
`Publication` establishes only the catalog phase under the original identity.
Reservation, files, catalog and presentation remain separate incomplete outcomes.

The H lifetime/operation owner precedes all native transactions. It compares
complete independent H/SQL facts, checks original acceptance/generation and
historical Stop, then uses the existing TaskStore origin decoder on the locked
original sealed task input. The locked conversation and result-message IDs must
also match that retained acceptance, including when different same-account targets
would satisfy native foreign keys. The separate backend-only `ReservedOutputPublisher`
uses only P's two 058, three 059 and one 060 entrypoints. P receives no A/W/R0/H
capabilities or direct table rights. W receives neither P credentials nor file/key
access. Constructors perform no I/O; a native cleanup owner and exact active
transaction task/connection are required before private effect seams.

The registry import guard permits only the exact unaliased `ReservedOutputPublisher`
and `Publication` type import in the existing H effects module. Its module allowlist
and inference `ADMITTED` remain unchanged. Extra/module/dynamic/nested imports,
runtime construction, consumer injection and activation remain refused; this
exception installs no host. Import/constructor service-I/O probes run separately.

Each call owns its top-level READ COMMITTED transaction. Borrowed SAVEPOINTs,
autocommit and nested/overlapping collaborator use refuse. Fresh effects require
native current lifecycle/attempt/lease/Stop admission before filesystem work and
final native staging checks afterward. The original captured generation is never
replaced by a current generation. Existing exact committed lookup is observation
only, including after lease expiry: it authenticates the unchanged metadata,
binding, provenance and bytes, without staging, repair or phase completion.

## Files, history and bounds

The publisher performs a real full J1 scan, closes short journal locks before SQL,
then compares the bounded complete 060 marker/namespace inventory. Its source
namespace must exactly match that inventory and be ready. A single supplied row,
snapshot, receipt or caller-created view cannot authorize file work. Existing
same-EUID 0700/0600 topology and exact inode/device/link/envelope checks remain in
force. It creates no namespace, repairs no orphan and deletes no evidence. Shared
codec methods preserve the existing encrypted C1 record formats.

`inventory_limit` (1–10000 records per array) and `snapshot_byte_limit` (1–2147483647
encoded bytes) are required trusted construction parameters, with no production
default/configuration selected here. SQL caps the returned encoded 059 snapshot
before client decoding; overflow refuses rather than truncating. The synthetic
fixture uses 10 records and 400000 bytes. These are qualification budgets, not a
production quota or a bound on SQL construction/kernel work. Existing descriptor,
ciphertext and content bounds also apply. No new migration or runtime grant is
introduced by this Python slice.

## Failure is not completion

Known top-level COMMIT, actual native pool reset/release, physical filesystem work
and H ownership cleanup must all settle before success. Cancellation/repeated
cancellation/deadline retains those owners until real work finishes, preserves the
original exception and blocks mutating reuse. No readback converts an unknown
commit into success; no replacement operation, automatic retry or new identity is
created. The existing two-second H logical effect budget is unchanged and does not
bound kernel cleanup. SQL failure after file preparation can leave verified files
without catalog rows; uncertain COMMIT can leave either committed or absent rows.
Both outcomes retain files/evidence for separately authorized reconciliation.
Existing uncommitted stage/private pairs refuse new catalog staging, even when
bytes verify: a fresh object cannot silently finish that missing phase. The legacy
storage helper's duplicate-observation default is unchanged; this publisher
requires a newly prepared stage.

This does not add independently retained artifact publication history. Matching H,
SQL and J1 alone cannot prove that a restored database retained all catalog/file
facts. Artifact restore/lifecycle/GC policy, append and split input/output
collaborators, presentation composition, authenticated bounded local transport,
distinct-UID deployment qualification and complete execution installation remain
launch gates. No same-user synthetic test establishes live worker exclusion or
live acceptance. All live keys, identities, credentials, grants, migrations,
mount/socket permissions, deployment and activation remain separate operations.
