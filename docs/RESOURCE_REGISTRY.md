# C1 — source-only resource registry and publication

Within the owner-approved combined #489/#471 C1 package (DEC05/DEC07,
AC03/AC09/AC11), not production activation or the complete DEC10 milestone.
The source-only implementation covers deterministic CSV output publication,
append-version intents, received-input preparation/atomic task acceptance, source
detachment, catalog pages, pinned presentations and internal full-object reads/repair.
Authenticated isolated read routes and native client surfaces are implemented;
complete integrated qualification remains required. Default startup stays
uncomposed; only the explicitly injected isolated host may bind these collaborators.

## Authority and atomic publication

Migration `053_resource_registry.sql` adds the unified account-owned catalog.
`ResourceStore` is an explicitly injected **trusted** registry service, not a
database privilege sandbox. R0 reservation creation stays on its existing restricted
app principal and unchanged `OutputReservations` wrapper. That transaction must
commit and release before publication; an R0 receipt does not grant storage access.
No new roles or grants are created, and no production principal is selected here.

Publication reads the actual committed job/intent and authenticates its retained
descriptor. In its own READ COMMITTED transaction it takes account advisory and
entitlement UPDATE locks, guards the original account generation, scans the actual
complete journal and compares namespace inventory. Unsupported lifecycle history
blocks before effects. New effects require the actual registered attempt and
current lease via the existing SQL lifecycle assertion on this **same connection**.
Calling this SQL function from the explicitly trusted owner is not permission to
bypass the restricted Python wrappers or grant their helpers to an app role.

Job/intent/resource/catalog and live source locks precede the private filesystem
lock. Every source task/conversation/message is checked against the account and
actual task output; single-column source FKs are not ownership checks. Filesystem
work finishes and releases all descriptors before another DB wait. A final lifecycle
check after filesystem work and all INSERT/FK/unique waits rejects expired producers.
The lease is checked immediately before prompt commit, not promised valid at the
instant a network acknowledgement arrives.

Resource, immutable version, encrypted publication binding, pinned output reference
and catalog insertion counter commit atomically. Rolled-back publication leaves no
acknowledged version or advanced counter; tracked private stages remain. Appending
uses a separate stable-operation intent, an existing owned resource and a fresh
version ID; R0's first-version unique-resource rule is unchanged. Cross-kind slot
collisions are denied, not interpreted as a second operation. Old versions and
references never become implicit "latest" aliases. #487 IDs alias these identities.

An uncertain COMMIT discards the exact owned connection before pool release. No
automatic new-identity staging or execution retry occurs. A fresh serialized call
resolves the same actual intent/publication and verifies original bytes. A committed
publication can be read after its producer expires; historical replay is not new
execution authority and does not perform repair. Every call keeps the original
account-generation guard; it never refreshes an old request to a new generation.

## Encrypted bytes, reads and explicit repair

`resource_storage.py` uses the existing bounded Fernet envelope and store-owned
nofollow POSIX descriptors. Preparation verifies and re-syncs even a duplicate
uncommitted stage/private pair; the old staging observation is not durability proof.
The complete object, measured size and encrypted expected checksum are verified
before a read. Private copies can serve committed bytes before namespace publication.
Plaintext temporary files and credentials in URLs are not introduced.

Explicit repair performs Linux `renameat2(RENAME_NOREPLACE)` through the existing
libc. Missing/unsupported support fails closed; there is no overwriting rename
fallback. Existing destinations must match the exact inode and authenticated
binding. A verified private-only copy may restore a missing public link exclusively.
Directory edges are synced and topology pins rechecked. Ordinary reads perform no
repair. Committed repair after producer expiry is storage maintenance of the exact
immutable mapping, never new content generation or a new version identity.

Conflicting paths, extra links, wrong ownership/mode/device, symlinks, wrong keys,
corruption, partial stages and storage errors remain preserved and uncertain. No
expiry or permanent-missing cause is invented. Foreign or mismatched version IDs
are rejected before any artifact filesystem access. No retention/cleanup scheduler,
unlink, purge, namespace retirement or full lifecycle handler is supplied.

## Received inputs and source deletion

Migration `054_resource_input_batches.sql` and `ResourceInputs` record a batch and
ordered immutable intent/resource/version identities **before** staging. Only actual
account-qualified DB rows, authenticated descriptors and verified private bytes
admit acceptance. Metadata-only records have no file, measured size or checksum;
their internal content read returns `original_not_received`. Submitted text is the
exact received UTF-8 representation, including existing trimming/truncation markers,
not an original upload. No extraction, prompt or fingerprint rewriting occurs.

`resource_input_codec.py` has required finite injected request/item/aggregate,
concurrency and decoded-raster budgets. These are qualification budgets, not selected
production quotas. The passive raster qualification profile admits only bounded
noninterlaced 8-bit grayscale/RGB with optional alpha PNG containing IHDR/IDAT/IEND.
CRC, legal dimensions/format, exact bounded zlib output and scanline filters are
checked before acceptance. Unsupported received images fail before effects; they
are not silently relabelled metadata-only. Other media remains separately gated.

Owner-approved stable lookup derives a domain-separated key with the existing
HKDF-SHA256 implementation from explicitly injected Fernet key material, then
HMAC-SHA256 binds account UUID and length-framed native idempotency key. No new
secret/environment variable or access to cipher internals. Auth-pepper rotation
preserves batch identity. An account-bound opaque key verifier is persisted even
on no-key batches and deleted-source markers; startup and per-account admission
inspect all retained verifier values **before** treating a new digest as absent.
Encryption/lookup-key rotation is unsupported and fails closed, not a rekey process.

`ResourceInputs.bind()` is explicit isolated composition, never default startup.
It joins native TaskStore insertion and ordered input publication on the same
connection, with account-first locks and the original server generation. Native
origin sealing, actual inference-role preflight and bounded busy-task retry remain;
no privileged owner workaround is supplied. A private insertion result is pending
until outer COMMIT acknowledgement. Unknown COMMIT discards the exact connection;
fresh original-batch readback verifies task ownership/origin/messages and every
pinned input, without an automatic new submission or new identity. A no-key
submission needs its original known batch ID for explicit recovery; a new no-key
request is a distinct operation. Preexisting tasks are returned without backfill.
The native pre-admission replay lookup uses the same account-serialized batch
checks; it cannot bypass a deleted-source marker through the older task-only path.

Composed TaskStore and MemoryStore deletion independently qualify the authenticated
owner, take account locks before source locks, preserve running cancellation/409
without detachment, and atomically detach all live source links with encrypted
`source_deleted` provenance and a filter revision change. The canonical batch chat
request and accepted-task pointer are erased in that transaction. Only opaque
original provenance and received resources remain. A retained deleted submission
key always conflicts (409), regardless of payload; it cannot recreate a deleted
task or acknowledge a dangling task. This does not erase historical backups or
implement account purge. Direct uncomposed deletion remains unchanged; deferred
FKs deny deletion of composed sources unless detachment is performed.

Single-column task/source FKs are **not** account authorization. Actual trusted
transition and recovery paths qualify the preallocated task ID AND its account,
origin/canonical request, conversation and both message owners before an accepted
pointer can acknowledge work. No untrusted direct table write is admitted. This is
not protection from a database owner changing data or bypassing the service.

## Account/reset content inventory

`ResourceCatalog` observes bounded owned versions through actual account/journal
authority. Its encrypted purpose/account/generation/query cursor captures committed
insertion watermark, sort tuple and source/filter revision plus actually verified
availability digest. Later inserts are excluded; changed mutable observations require
`restart_required`. Injected scan budgets refuse overflow, never return a truncated
inventory as complete. Catalog/version `reference:null` differs from pinned ordered
task inputs. Availability is a per-request observation, not an atomic disk snapshot.

`ResourcePresentations` validates actual owned task/result message and pinned output
references before encrypting a versioned `file_results` envelope in migration055.
Committed matching pins resolve before new identity allocation. Current actual
attempt authority is required for a new record; immutable replay can survive expiry.
Source deletion detaches presentations in the same transaction, including zero-ref
cases, retaining minimal encrypted opaque provenance rather than canonical chat text.
The record limit applies per result message; matching immutable replay remains
valid at capacity, while new records are refused. Conversation detachment uses
bounded keyset batches in two passes under the existing source/task locks: all
presentations and references validate before mutation, then presentations are
revalidated and detached. A later failure rolls back the entire deletion; long
conversations are not subject to the per-message presentation cap.

`install_resource_routes` is an explicit isolated installer only. Existing real
bearer-session auth and same-pool ownership precede metadata/content. Shared injected
slots remain held through ASGI send/cancellation, with no DB transaction across
network send. Actual auth identity and original generation are checked again before
headers; revocation cannot retract already-sent bytes. Full verification precedes
HEAD, conditionals and ranges. All statuses are private/no-store/nosniff.

`ResourceCSV` uses bounded in-memory encoding, unchanged restricted R0 and actual
trusted publication, then records pinned presentation. No legacy plaintext writer,
model call, tool rerun or default tool-registry change. Isolated deterministic local
CSV qualification does not activate separated production inference.

Native gallery filters and Load more/Retry/restart controls, plus task input/output
cards, use authenticated reads only. Auth-generation changes abort reads and discard
content/blob URLs. Proxies use one existing backend origin, no redirects/fan-out.
Service-worker private handling bypasses HTTP cache/preload and runtime caches.
Legacy media is explicitly labelled as the limited last-20-conversation view.

Owner-approved history discovery adds optional `MessageOut.task_id: UUID | null`.
Only explicit resource composition derives it at read time, under the actual
account/journal guard and same connection used for that bounded message page.
The corresponding task/user-or-result-message link, all account/conversation owners
and authenticated original task generation must agree; ambiguous or inconsistent
links refuse. Missing legacy origin or an uncomposed store returns null. No stored
column, metadata mutation or backfill is required. Both cursor and ordinary history
paths retain ordering/pagination. The client preserves this as typed message data,
not invented SSE/status; conflicting live/historical identities yield no selection.
The UUID discovers pinned resources, never authorizes them or substitutes latest.

These tables are all **account/reset content**, not retained financial/control
history. All must participate in the eventual deletion/reset manifest and erasure:

| Table | Disposition and invariant |
| --- | --- |
| `resource_catalog_heads` | Generation-bound committed version watermark and mutable-filter revision; never rewind to admit old content. |
| `resources` | Account-owned immutable identity; chat/task deletion must retain it. |
| `resource_versions` | Immutable received/generated representation and bounded encrypted metadata/provenance/checksum. Metadata-only has NULL measured size and cannot have a byte publication. |
| `resource_publications` | Immutable committed account/version/generation/namespace/intent binding; not an availability boolean or transferable grant. |
| `resource_references` | Pinned version and encrypted original provenance; live source links must be explicitly detached with encrypted `source_deleted` and filter revision in the deleting transaction. |
| `resource_version_intents` | Stable append operation/slot, original generation, producer identity and encrypted descriptor; no task/chat deletion cascade. |
| `resource_input_batches` | Stable keyed submission identity, original generation and task identity; encrypted canonical request erased on source deletion, content-free closed-key marker retained within the account. |
| `resource_input_intents` | Ordered immutable received representation descriptors and tracked staging identities; no artifact I/O grant. |
| `resource_presentations` | Encrypted pinned envelope and minimal opaque source provenance; source deletion detaches its live message link transactionally. |

R0 `artifact_output_jobs` and `artifact_output_intents` remain content as documented
in [R0](ARTIFACT_OUTPUT_RESERVATIONS.md). The non-cascading `artifact_namespaces`
inventory is content-free journal control, not a retained copy of content metadata.
SQL account cascade removes DB rows, **not private/staged/public files**; it is not
a completed account deletion implementation. Deferred NO ACTION source FKs refuse
source deletion until explicit detachment; default runtime remains uncomposed.

## Qualification limits

Owned synthetic PostgreSQL, actual journal and POSIX tests cover output publication,
two pinned versions, concurrency, before/after COMMIT faults, rollback, cancellation,
lease expiry, foreign IDs, source ownership and corruption. POSIX tests cover
duplicate re-sync, no-overwrite repair, sync/rename faults and owned-process death.
History qualification exercises actual native acceptance and both paged/ordinary
history, beyond twenty turns, original task lookup, ambiguous/foreign/stale origin
refusals and retained version-one replay after version two is published. The native
component browser fixture in `tests/test_resource_history_postgres.py` composes the
production React cards/gallery, auth coordination/cache barrier and resource proxy
against real PostgreSQL/POSIX reads. Two real synthetic device sessions reopen the
same pinned CSV; an A-to-B switch aborts/discards an in-flight preview. Tokens enter
through the public manual-token interface in a test-only harness, not a production
identity-provider login or auth override. Existing independent HTTP/browser tests
also verify original input bytes, foreign denial, restart and response backpressure.
These are isolated component/composition checks, not a full Next application,
production HTTPS, other-engine or production-capacity certification. Tiny fixture
memory measurements do not select production quota values. Test concurrency obeys
the same injected limits; temporary capacity refusal is not permission to raise them.
These do not establish power-loss durability, production capacity or a deployment
principal. Exact current source/test evidence and final reviews govern qualification;
full combined C1 repository gates remain required before either integrated PR.

Retention periods, quota values and abandoned-stage deletion are unselected. Complete
account lifecycle, backup/restore, production migration, activation and deployment
remain separate gates. No app/benchmark database, paid inference, extraction,
retrieval, prompts, routing, model or evaluation changes are part of this work.
