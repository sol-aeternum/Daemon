# N0 — uncalled namespace journal candidate

Owner approved option A on 29 October 2026: a pure candidate codec and mixed
structural validator only. Governing contracts remain
[account deletion §§4.2/6.6](ACCOUNT_DELETION_DESIGN.md) and
[artifact continuity](ARTIFACT_CONTINUITY.md). This prerequisite supports
DEC05/DEC07 and AC03/AC09/AC11; it does not complete #489 or #471.

## Source boundary

The following records N0's original boundary. The subsequently owner-approved
C1 package adds an explicitly uncalled `mixed_store` consumer and shares bounded
POSIX framing/HEAD mechanics with `store.py`; the default reader and startup
remain v1-only. See [C1 work in progress](DELETION_JOURNAL.md#c1-work-in-progress--offline-container-promotion).
This codec remains pure; no namespace effect or runtime activation is supplied.

`orchestrator/services/deletion_journal/namespace_candidate.py` is a separate,
uncalled candidate. Production `model.py`, `store.py`, `barrier.py`, journal
metadata and DB schemas are unchanged. There is no filesystem I/O, database,
clock, random-name allocation, settings lookup, dependency or runtime integration.
No namespace is created, admitted, retired, restored or deleted.

The existing production decoder **still rejects v2**, including correctly hashed
candidate frames. No candidate bytes may be written into an actual journal by
this increment. Durable integration requires a separately approved container
compatibility and coordinated reader/writer upgrade/downgrade contract. No v1
log rewrite, digest replacement, binding change, pruning or new journal identity.

## Closed candidate format

Framing retains the four-byte big-endian payload length and 32-byte SHA-256 footer
over the length prefix plus canonical JSON payload; total frame size is at most
64 KiB. This detects corruption, not malicious rewriting by the trusted owner.
Existing v1 canonical payloads retain their byte representation and semantics.

V2 keeps the v1 envelope keys and adds exactly one command shape:

| Field | Value |
| --- | --- |
| `version` | Exact integer `2` |
| `event_type` | `namespace_create_intended` |
| `operation_id`, `operation_prev` | Both null; no invented deletion/reset operation |
| `fields` | Exactly `kind`, `generation`, `namespace` |
| `kind` | `namespace_create` |
| `generation` | Exact integer from zero through signed 64-bit maximum |
| `namespace` | 32 lowercase hex characters; a later trusted producer must allocate random, never-reused 128-bit names |

UUIDs, timestamps and digests use the existing canonical representations. Unknown
fields, versions and events fail closed. There are no paths, display names, content,
secrets, unrestricted payloads or free-text errors in the format.

The namespace command digest binds domain `daemon-namespace-command-v2`, account,
null operation, event type and all three fields. It excludes decision time,
sequence, predecessors and global digest. Original generation and namespace are
immutable inputs, not retry allocations. The same event identity must never be
used to change either. Shape validation cannot prove entropy, ownership, current
generation or permission.

## Structural replay is not live authority

Mixed replay checks journal identity, contiguous sequence, digest chain, event
uniqueness and account predecessor. A birth advances only the account head, never
an operation head. Namespace names are globally unique; each account/generation
gets at most one birth. Duplicate frames cannot become another accepted event.
Any historical retry lookup is identity evidence only.

All v1 operation ownership, predecessor, cause, deadline, generation and terminal
rules remain mandatory. Birth is refused during an open operation or after final
purge. With no prior generation-bearing frame, initial zero or a positive current
generation may be represented; the future DB guard must establish its validity.
Otherwise birth must use the current recorded resulting generation. It never
allocates a generation; later v1 fences must exceed every recorded generation.
Restore, reset cancellation and reset purge retain their existing generation rules.

Rebuild during an operation, replacement after failed creation, abandonment,
creation-ready and retirement events are unsupported. A failed intended birth is
not automatically replaced. These availability limits need later approved
protocols, not a permissive extension registry.

## Qualification and remaining gates

`tests/test_namespace_journal_candidate.py` provides synthetic codec, framing,
mixed lifecycle and rejection regressions plus a no-runtime-import guard. These
tests do not qualify durable append, DB projection, filesystems or deployment.
Repository checks and independent final-source review govern source qualification.

The later coordinator must preserve journal-first intent, exact retry identity,
original account generation, the stable private preparation lock and DB/file-lock
ordering. Physical reconciliation must consider the full lifecycle chain so that
replaying an old birth never resurrects a retired namespace. An applied DB marker
is not fresh physical proof. Unknown commits and filesystem outcomes close
admission; they do not authorize another name or a blind retry.

Immutable pre-fence manifests/private copies, handle drain, scoped verification
of every storage location, the publication registry and accepted-input integration
remain unimplemented here. Full deletion/reset, retention, quotas, cleanup,
mount/process qualification and runtime activation remain separately gated.
Catalog and account-deletion/reset feature states remain **Not started**.
