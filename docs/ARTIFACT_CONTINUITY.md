# Artifact and accepted-attachment continuity — first contract

Owner-approved design, 28 October 2026, for [#489](https://github.com/sol-aeternum/Daemon/issues/489)
and [#471](https://github.com/sol-aeternum/Daemon/issues/471). The owner approved
the recommended design and then authorised beginning the first increment:
contract documentation, synthetic fixtures and contract tests only. This is not
a registry, storage implementation, API registration, migration or rollout.

Governing contracts: [durable requests §§3–13](DURABLE_REQUEST_DESIGN.md),
[account deletion §§4/6/10](ACCOUNT_DELETION_DESIGN.md), and
[account-control qualification boundaries](ACCOUNT_CONTROL_PERSISTENCE.md).
This advances DEC05/DEC07 and AC03/AC09/AC11, not the complete DEC10 milestone.

## 1. Baseline and decision boundary

Source inspected at `df8085b40f2da796664b70b0e40d1f1d1823f7e0`:

- `orchestrator/artifacts.py` already scopes paths to authenticated owners and
  rejects unsafe paths/symlinks. Generated downloads in `orchestrator/main.py`
  already authenticate and use private/no-store responses. Preserve this.
- `frontend/app/artifacts/page.tsx` scans image/audio paths from only the latest
  20 conversations. This is not an authoritative all-thread inventory.
- `frontend/app/page.tsx::serializeAttachments` sends image bytes, trimmed and
  possibly truncated text, or binary metadata only. The backend prepares these
  representations and retains them in encrypted durable task input; it does
  not retain all original uploads. Task snapshots must not expose that input.
- Generation dispatch in `orchestrator/tools/spawn.py` stays fail-closed.
- S1 shared POSIX storage was already selected by the owner in
  [#469 comment 6072269138](https://github.com/sol-aeternum/Daemon/issues/469#issuecomment-6072269138).
  This does not prove shared mounts, durable storage or deletion qualification.

Approved defaults are the unified model below, honest received representations,
8 MiB bounded encrypted objects, structured metadata search, and chat deletion
detaching retained resources. Exact DDL and implementation API payloads remain
subject to review/approval in their increments. **Retention durations, quotas and
abandoned-upload cleanup policy remain open; activation is blocked until settled.**
No new storage provider, dependency, backfill, paid inference or deployment is
approved here. Existing account-deletion grace and backup periods are not
workspace-artifact retention periods. #250 does not approve a durable-audio TTL.

## 2. Identity and provenance

One account-owned resource model covers accepted inputs and generated outputs:

| Concept | Contract |
| --- | --- |
| Resource | Server-generated opaque `resource_id`, account owner and immutable creation timestamp. |
| Version | Server-generated `version_id`, immutable received/generated representation and content-bearing metadata, validated MIME/size, encrypted checksum and storage binding. Availability is separate mutable state. |
| Reference | Account-qualified resource/version plus task/thread/message, role and ordinal. Every reference pins a version, never an implicit latest version. |
| Output job | App-owned `job_id` for a logical output operation within a task; not an arq delivery, lease or attempt. One job can produce several resources. |
| Publication/import intent | Stable operation/output-slot identity, original account generation and execution authority, with reconciliable outcome. |

Owner comes from authentication or a trusted claimed task, not renderer props,
filenames, client attachment IDs, provider URLs or model-supplied identifiers.
Every resource/version/task association is account-qualified. The #487 adapter's
`artifact_id` **is** `resource_id`; `artifact_version_id` **is** `version_id`.
Do not build two catalogs. Separate operations with identical bytes retain
distinct identities; repeated delivery of one operation resolves its original
version. No cross-account content-hash deduplication.

Input representations:

- `submitted_image`: validated decoded submitted image bytes.
- `submitted_text`: the exact submitted text representation, not the original
  file. Preserve the accepted processing representation without changing
  extraction, prompts or routing. Do not guess how much original text was lost.
- `metadata_only`: input identity/order and encrypted supplied metadata, with
  `original_not_received`; no byte size/checksum/download claim for absent bytes.
- `generated_file`: validated output bytes, initially deterministic CSV.

Client-reported original size is untrusted metadata, distinct from measured
representation size. Original-file upload is a later API increment.

## 3. Acceptance, publication and encryption

Inputs are encrypted and synced into tracked staging and the store-private copy
before acceptance commits. One transaction commits task/messages, immutable
version/publication records, ordered input references and encrypted canonical
input under the original account-generation guard. Only then acknowledge.
Same account idempotency key and canonical input returns the same task and
versions; conflicting input returns 409. Failed acceptance leaves no acknowledged
input and leaves tracked stages for safe reconciliation, not invisible garbage.

Use the approved S1 protocol in deletion §4.2, not the existing mutable filename
helper as a durable store: encrypted stage, sync/close, private immutable hardlink
and directory sync, fenced publication transaction, then namespace rename/repair.
The publication commit is authoritative; an available read must resolve verified
bytes even if the public namespace entry still requires repair. Unknown COMMIT
closes mutation admission and requires reconciliation, never a blind second write.
Output publication also checks current attempt, lease, owned references and the
original account generation in the actual transaction. A queued message or final
token cannot establish completion. Producers cannot create namespaces.

Initial objects are at most **8 MiB of decoded representation bytes**. Use the
existing Fernet primitive with a versioned authenticated envelope binding account,
resource, version and representation. Verify the whole bounded object before
preview/range access; no plaintext temporary files. Bound encrypted-envelope size,
request sizes, decoded allocation and account/global concurrency too. Exact
concurrency capacity needs qualification. Larger media needs a separately reviewed
established encrypted-streaming format, not custom chunk cryptography.

Encrypt filenames, titles, descriptions, detailed provenance, checksums, necessary
provider reconciliation details and presentation JSON. Plaintext indexes are
limited to operational opaque IDs, generation, timestamps, closed kind/MIME/state
values and measured byte size. These fields still disclose operational metadata;
encryption is not provider-invisible or end-to-end encryption.
No private text, URL credentials or arbitrary errors in logs/indexes. Telemetry
contains bounded counts, timings and closed codes only.

## 4. Read and pagination contract

Intended additive paths (not registered by this increment):

```text
GET  /tasks/{task_id}/inputs?limit=20&cursor=...
GET  /resources?role=output&kind=image&limit=20&cursor=...
GET  /resources/{resource_id}/versions/{version_id}
GET  /resources/{resource_id}/versions/{version_id}/preview
GET  /resources/{resource_id}/versions/{version_id}/content
HEAD /resources/{resource_id}/versions/{version_id}/content
```

Inputs and catalog pages have `schema_version`, bounded `items` and nullable
`next_cursor`. Input entries include a pinned reference, role/ordinal,
representation, availability and authorised safe display metadata. No raw task
input, provider URL, storage path or bearer credential is returned.

Authenticate and resolve owner/resource/version on every path, before conditional
or range processing. Foreign/unknown/mismatched identities all return 404 without
lengths or validators. Missing/invalid authentication follows existing auth.
Owned metadata may return 200 with truthful availability:

| State | Content response / meaning |
| --- | --- |
| available | 200, or authorised single range 206; verified bytes exist. |
| importing | 409; not yet published. |
| original_not_received | 410; metadata-only input cannot supply an original. |
| missing / expired / deleted / rejected | 410 when absence is confirmed permanent; label the actual cause, not invented expiry. |
| uncertain | 503; storage/publication outcome cannot currently be established. |

Temporary storage errors return 503, not a permanent-missing conclusion. Single
ranges use 206/416; unsupported multiple ranges return 400. An If-Range mismatch
returns the full authorised representation. Apply private/no-store to successes
and errors, safe Content-Disposition and nosniff. Only passive allowlisted
previews; HTML/SVG and unsupported formats get a file/text fallback.
Use existing authenticated same-origin access or authenticated fetch with
ephemeral object URLs, never credential-bearing URLs. Auth/account-generation
changes cancel requests and clear account UI/object URLs. Preserve #484/#486
service-worker and HTTP-cache isolation, including old cached entries.

Catalog default 20/max 100; immutable server creation timestamp plus unique ID
descending. Capture a committed insertion watermark under account serialization.
Authenticated opaque cursors bind account, content generation, normalised query,
watermark and last sort pair. New inserts after that watermark appear on refresh,
not in later pages. Deletion/revocation applies immediately and can shrink the
enumeration. Mutable-state filters use revision-bound invalidation with explicit
`restart_required` rather than silently skipping changed members. Never keep a
SQL transaction open between pages. Index account/sort and account/filter/sort;
do not promise exact totals in this slice.

Search filters the complete authorised catalog by structured kind, MIME, date,
availability, input/output and task/job. No filename/content full-text search or
plaintext private-token index. Unsupported free text is explicitly rejected,
not silently searched only within the first page.

## 5. Lifecycle and imports

Chat deletion detaches references while retaining account resources under the
eventual retention policy. Preserve current queued-task cancel/delete and running
409 cancellation semantics. Nullable live links can disappear; minimal encrypted
opaque original provenance remains marked `source_deleted`, not copied chat text.
No resource/version cascade on task/conversation deletion.

Account deletion/reset removes originals, versions, private links, stages,
previews, indexes and presentations under the stronger approved contract. No
content-bearing tombstones remain after account purge. Within a retained account,
a minimal unavailable record may explain a pinned reference, subject to policy.
Use original account-generation fencing, journaled random-namespace retirement,
immutable pre-fence manifests/private copies, handle drain and all-location
verification. Restore/export never trusts a mutable frozen-directory scan.
Generation and attempt-output epoch are different fences. Journal/account-control
source prerequisites do not establish a completed lifecycle implementation.

Internal imports record intent before effects, deduplicate by operation/output
slot, never advertise partial copies, and resume only evidence-backed safe reads.
Provider acquisition requires approved HTTPS destinations, DNS and every-redirect
SSRF controls, credential isolation, time/byte/type limits and atomic publication.
Provider URLs are transient, never renderer identities. No expired URL triggers
paid regeneration. Preserve #488's prohibition on blind paid resubmission.
Ambiguous legacy ownership stays preserved and denied. Separately approved
dry-run/backfill requires trustworthy unique account evidence, stable source
identities, bounded resumable checkpoints and counts by outcome. No backfill here.

## 6. Presentation and executable evidence boundary

#487 consumes a renderer-independent pinned-reference envelope with explicit
schema/component-library versions and snapshot timestamp. Persist through an
explicitly encrypted field/table, not arbitrary message metadata. Allowlisted
local UI state cannot select endpoints, actions, provider URLs or tools. Unknown
versions retain useful prose/file fallback. Native components work without
OpenUI. Reopen, local controls and reconnect submit no generation work.

`tests/fixtures/artifact_continuity/contract.json` contains fictional accounts,
an image input, submitted-text and metadata-only cases, two CSV output versions,
and a pinned file-results envelope. `tests/test_artifact_contract_vectors.py`
checks fixture bytes, identity mapping, representation honesty and negative
reference vectors with a **test-only oracle**. It does not test runtime auth,
durability, encryption, range handling or cross-device execution. No mock registry
is substituted for those later acceptance gates.

## 7. Independently reviewable increments and acceptance gates

| Increment | Deliverable / acceptance |
| --- | --- |
| 1 — this change | Approved contract, synthetic vectors and consistency tests; no production code or runtime wiring. |
| 2 | Uncalled bounded encrypted S1 store; crash tests before/after stage/private-link/commit/rename and unknown commits. Required security supervision/review before material changes. |
| 3 | Separately approved registry migration/transaction primitives; ownership constraints, operation dedup and deletion/reset inventory classification. |
| 4 | Read APIs; >20 older-thread/equal-time catalog fixtures, query/cursor isolation, concurrent insertion/deletion, range/HEAD and corruption/key failures. |
| 5 | Accepted-input references and deterministic document-output registration; lost acknowledgement, duplicate submits/completions and interrupted publication. |
| 6 | Native two-client UI plus encrypted replay adapter; account-switch/cache, unsupported-version and local-control tests asserting zero generation calls. |

### Increment 2a — uncalled envelope codec

The owner approved splitting increment 2 on 28 October 2026: first the bounded
in-memory codec, then staging/private links, then publication qualification with
the separately approved registry transaction authority. No mock callback or
caller-created receipt substitutes for a committed publication record.

`orchestrator/artifact_envelope.py` provides `ArtifactBinding` and
`ArtifactEnvelopeCodec`, with an explicitly injected initialized Fernet cipher.
The v1 authenticated plaintext is canonical ASCII JSON (sorted keys, compact
separators), with exactly `format`, `account_id`, `resource_id`, `version_id`,
`generation`, `representation` and `content`. The format is
`daemon-artifact-envelope-v1`; content uses canonical standard base64. IDs are
1–128 ASCII alphanumeric/underscore/hyphen characters, generation is an exact
integer from zero through signed 64-bit maximum, and representation is one of
`submitted_image`, `submitted_text`, `generated_file`. Metadata-only resources
have no content envelope. These internal validation limits do not establish a
registry schema or external API.

Decoded content is capped at 8 MiB; plaintext envelope size is capped at the
maximum base64 content length plus 1,024 bytes. A derived Fernet token bound is
checked before decryption. Full-object authentication precedes JSON/content
parsing and return; duplicate fields, unknown fields and noncanonical encodings
are rejected. Errors use `invalid_artifact_envelope` without parser details.
Whole-object copies remain in process memory; there is no secure-erasure claim.
Concurrent-call limits and deployment memory capacity still need qualification.

The codec has no runtime callers, filesystem, settings lookup, logs, publication
state or implicit key management. Expected bindings must come from a separately
authorized registry lookup: matching caller-supplied IDs is not authorization,
generation freshness, uniqueness, media validation or proof of publication.
Synthetic tests in `tests/test_artifact_envelope.py` cover binding substitution,
corruption/wrong-key/format errors and allocation boundaries. They do not qualify
S1 staging, crash recovery, commit uncertainty, namespace retirement or reads.
Catalog status remains **Not started**; the rest of increment 2 is outstanding.

Smallest eventual end-to-end: A submits one PNG; fixture worker publishes CSV;
A closes; another authenticated client for A reopens the same inputs and pinned
CSV bytes; B is denied on every read variant. CSV version two never changes the
earlier message's version one. This is a test case, not DEC10 milestone reduction.

Before activation, also prove reset/import and stale-worker races, retained-handle
replacement/addition restoration, all-host storage/backup/journal qualification,
provider/legacy failure disclosures, and approved retention/quota/abandoned-stage
policy. Future media/provider ingestion/backfill are separate increments. No
storage-provider, migration execution, flags, deployment or paid inference is
authorised by passing fixture tests. Required repository gates remain unchanged.
