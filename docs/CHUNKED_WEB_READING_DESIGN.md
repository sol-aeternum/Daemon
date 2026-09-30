# Chunked web reading — approved implementation contract

Status: **Implemented; local blocking gates pass; deployed to the local Compose runtime on 2026-09-30.** On 2026-09-30 the user
selected designing chunked reading rather than applying a fixed truncated-page
hotfix. This document does not enable a new tool contract or certify a fix.
The user subsequently requested a comparison of other implementations before
choosing snapshot lifetime, then approved the recommendation ("as recommended")
for bounded conversation-scoped snapshots. The turn-only implementation below
is retained as an unselected alternative. The user then explicitly approved the
detailed PostgreSQL storage, retention, tool/API, encryption and configurable
resource-limit contract below. Approval does not establish deployment status.

## Verification and rollout

On 2026-09-30 the clean-source backend gate run passed all blocking checks:
3,852 tests passed, 8 skipped; lint, format, type checks, high-severity Bandit and
dependency audit passed. Full Bandit remains a non-blocking inventory. Targeted
PostgreSQL tests cover ownership, encrypted payloads, concurrent quota admission,
deletion races, expiry, export, and reuse across separate tool instances/pools.
The actual main application exposes all three snapshot API routes. Independent
rate/trace, reader, fetch and persistence reviews are complete; material findings
were resolved, with no blocking findings remaining. The final code was compared
byte-for-byte with the passing source snapshot.

After explicit deployment approval, migration `041_web_snapshots.sql` was applied
and the backend and worker were recreated. Live health reports PostgreSQL and
Redis healthy; all three snapshot API routes and the approved configuration are
present. No paid-provider end-to-end test was performed. Legacy plaintext tool
traces are still readable, not backfilled; old cache entries are bypassed, not purged.

## Problem and evidence

Before this change, `orchestrator/tools/web_fetch.py` serializes the full `FetchResult.content`.
`orchestrator/tools/completion.py` appends each result to the continuation
messages. `orchestrator/compute_runtime.py` enforces context limits but does not
make oversized page results fit. The direct fetch strategy also reads a response
without a body-size ceiling. Search results already have separate output bounds.

This establishes a context-growth failure path, not the exact cause of the
reported historical context error. The confirmed rate-limit incident is separate
and tracked in #356. Context growth is tracked in #357. A no-network diagnostic
with a synthetic 1,100,000-character page returned all 1,100,000 characters
(1,100,125 serialized bytes), already above the compute runtime's input-byte
ceiling before adding history or tool schemas.

Direction: DEC06 (ask on material choices), DEC10 (ordinary research continuity),
and the context-integrity priority in `DAEMON.md`. Existing account budgets,
qualified-route selection, URL validation and tool-loop limits remain governing
constraints.

## Approved tool contract

Extend `web_fetch`; do not add a second reading tool:

- Existing `url`, `extract`, and `force_refresh` inputs remain.
- Add optional `snapshot_id`, `start_char` (default 0), and `max_chars` (default
  6,000; maximum 12,000). Positions count Unicode code points, not bytes or tokens.
- A first read fetches/extracts the page once and returns its first bounded chunk.
- Later reads supply the returned snapshot ID and next offset. They read the
  same extracted text, even if the source page changes; an optional URL must
  match the saved source identity.
- Return existing provenance fields plus `snapshot_id`, `start_char`, `end_char`
  (exclusive), `total_chars`, `has_more`, and `next_start_char` (null at EOF).
   Preserve `content_length == len(content)` for the returned section; use
   `total_chars` for the complete snapshot. This preserves the existing length
   field's meaning, while `content` now explicitly represents a partial section.
- A partial result explicitly says it is a section, not a complete page. The
  assistant should cite the source and disclose incomplete coverage when relevant.
- Reject negative/non-integer offsets, excessive limits, mismatched URL/extract
  mode, or conflicting refresh-plus-snapshot inputs. Offset equal to length is
  a valid empty EOF; offset beyond length is an error.
- Missing/expired snapshots return a bounded `snapshot_expired` tool result.
  Never refetch and silently apply an old offset to changed content.
- Add bounded literal find-in-snapshot support, returning source offsets and
  short surrounding excerpts rather than loading the entire page. Exact JSON
  fields follow the detailed tool contract below.

### Snapshot text and source identity

Direct fetching currently returns raw `response.text`, and `extract` is logged
but does not control direct extraction. Snapshot creation must correct this:
HTML article reads use the existing `html_to_markdown`/trafilatura helper to
produce readable Markdown with source links and tables. Extraction failure is a
bounded tool error, not a fallback that silently injects raw HTML. Plain-text
responses retain their text; transcript/metadata modes need explicit supported
outputs rather than silently behaving like article mode. Record an extraction
version so offsets always refer to one immutable textual representation.

Decode once, bound snapshot text by its UTF-8 byte size, and slice by Unicode
code-point offsets. Separately bound the final serialized tool result, including
JSON escaping, metadata and the untrusted-content wrapper.

The existing fetch cache lowercases the whole URL (`#358`), so distinct
case-sensitive paths/query values can collide. It also rewrites `FetchResult.url`.
Preserve source identity from the original validated URL and final redirect URL,
not that rewritten field. Cache keys must preserve resource-identity semantics
and bind extraction mode/version; fix and test them before snapshot creation.

These are tool JSON changes; the turn-only alternative proposes no new HTTP
endpoint or SSE event type. The conversation-scoped option's persistence and
user-facing access contracts remain to be designed. Existing consumers and tests
must tolerate the additional result fields.

## Evidence from other implementations (checked 2026-09-30)

| Source | Verified behavior | Implication for Daemon |
| --- | --- | --- |
| [MCP reference fetch server](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/src/mcp_server_fetch/server.py) | `max_length` defaults to 5,000 characters; `start_index` reads subsequent sections; truncated results explicitly supply the next offset. The implementation fetches again before slicing each call. | Offset reading has a concrete precedent, but repeated fetching does not guarantee the same source version. Use a snapshot identity for stable offsets. |
| [Anthropic web fetch](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool) | Approximate `max_content_tokens` truncation for text, dynamic filtering before context, `retrieved_at`, optional source-passage citations, and cache bypass. Newer versions can exclude completed nested fetch results from response output. | Separate retrieval from context inclusion; preserve provenance; expose freshness deliberately. A timestamp/cache hit is not an immutable version ID. |
| [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search) | Agentic `open_page` and `find_in_page`; configurable search context size; separate returned-token budget; all consulted sources are distinguishable from inline citations. | Support targeted reading and a shared context allowance. Answer citation spans identify positions in the answer, not necessarily source-page offsets. |
| [Jina Reader](https://github.com/jina-ai/reader#using-request-headers) | Semantic `x-markdown-chunking`, selectors, truncating `x-max-tokens`, rejecting `x-token-budget`, and explicit cache tolerance/bypass. The open-source service can be stateless or use bucket caching across requests. | Prefer meaningful sections, bounded output and clear cache controls. Neither a URL cache nor chunk output alone establishes a durable conversation-owned snapshot. |

These sources do not establish a universal cross-message snapshot retention
period. Nor does pagination guarantee exhaustive reading under a finite context
and tool budget. Returning all semantic chunks at once still requires a separate
context allowance. Hosted APIs are design references, not proposed new providers.

### Research-informed recommendation: two lifetimes

Keep **source lifetime** separate from **prompt lifetime**:

1. Prefer immutable, account-owned, conversation-scoped snapshots that can be
   referenced across later messages, subject to explicit storage quotas and
   retention expiry. Store retrieval time, source URL, extraction version and
   content hash; expose an opaque ID rather than treating the hash as authority.
2. Insert only requested/relevant sections into a turn's context. A compact
   source reference can survive without repeatedly inserting the entire page.
3. A read by snapshot ID means "that saved version" and performs no automatic
   refetch. A freshness request fetches a new version with a new ID, preserving
   the old reference while it remains retained. Conditional HTTP validators may
   optimize refresh; a content hash alone cannot check a remote page for changes.
4. Expired/deleted content must fail explicitly. Retained citations should still
   distinguish the original retrieval from a later refetch. Do not promise
   source availability after expiry or deletion.

The approved direction requires the persistence contract below: encrypted content
at rest, owner/conversation authorization on every read, cross-worker lookup,
per-account and per-conversation byte limits, expiry/deletion/export semantics,
and treatment of restricted-project derivatives. Exact TTLs and quotas are
product choices, not numbers justified by these external examples. No snapshot
storage or paging implementation has shipped through this document.

## Detailed conversation-scoped contract — approved

### Storage choice and lifecycle

Recommend a dedicated PostgreSQL table, using existing asyncpg and
`ContentEncryption`. This gives shared lookup across backend processes and
transactional quota enforcement, and follows the existing conversation-deletion
lifecycle. A Redis-only cache would avoid a migration but would make deletion,
quota reconciliation and snapshot durability depend on a separate store; it is
not the recommended option.

Approved `web_snapshots` record:

- Random UUID `id`, authenticated `user_id`, owning `conversation_id`.
- Fernet-encrypted source metadata (original/final URL, title, extraction mode
  and version), and separately encrypted extracted text. Listing sources must
  not require decrypting every page body.
- Account-scoped keyed identity/content fingerprints for lookup/version
  comparison; neither hashes nor snapshot IDs grant authorization.
- Plain numeric `content_chars`, `content_bytes`, `stored_bytes`; UTC
  `retrieved_at` and immutable `expires_at`.
- Foreign keys to owner/conversation, with conversation deletion cascading to
  snapshots. Index owner, conversation and expiry for bounded queries/cleanup.

Every store operation binds the authenticated user and conversation together;
model arguments cannot supply either. Reserve/insert under a per-account lock
and lock/check the owned conversation in the same transaction. Foreign keys
remain the last integrity boundary if deletion races a fetch. Fetch network
content before acquiring database locks, then revalidate ownership and quota
before saving. Reject a save after conversation deletion rather than recreating
the conversation or making an orphan snapshot.

Approved deployment-wide initial limits (not plan upgrades):

| Limit | Default value | Meaning |
| --- | --- | --- |
| Snapshot retention | 30 days from retrieval | Access does not silently extend retention |
| Decoded HTTP response | 2 MiB | Stream-enforced, including compressed responses |
| Extracted page text | 1 MiB UTF-8 | Reject oversize instead of claiming a full snapshot |
| Conversation storage | 16 MiB / 64 snapshots | Encrypted payload bytes and count; first reached binds |
| Account storage | 128 MiB / 512 snapshots | Encrypted payload bytes and count across conversations |
| New snapshots per turn | 8 | Additional admission bound, separate from tool-loop limits |
| Returned section | 6,000 characters default; 12,000 maximum | Can shrink further to fit the active context allowance |

These are approved operational defaults, not values derived from vendor docs.
Expose them through the existing Settings/env convention, with `.env.example`,
backend/worker Compose wiring and parity tests updated together. Quota accounting
counts retained encrypted payloads; row/index overhead is additionally bounded
by the count limits. Purge expired rows before quota admission, with a daily
worker sweep as housekeeping. Reads reject expired content even if cleanup is
delayed. Reject full storage without silently evicting still-retained sources.
Expiry removes the full saved snapshot; already-quoted excerpts and answers in
chat history retain the conversation's own lifecycle.

For this path, the conversation snapshot store is the cache. Bypass both reads
and writes of the existing shared plaintext Redis page cache; otherwise a new
encrypted snapshot would still leave an unencrypted full-page copy and an
ambiguous original retrieval time. Retain the fetch service's URL/redirect
validation and direct transport, with an explicit internal cache-bypass seam.
Do not claim that pre-existing legacy cache entries have been purged.

### Tool and authenticated access contracts

Extend existing `web_fetch` with a default read operation and an explicit find
operation, plus bounded listing of this conversation's sources. Approved
`action` values are `read` (default), `find`, and `list`. Read accepts a URL or
snapshot ID plus start/length; find accepts snapshot ID plus a bounded literal
query and returns bounded source-offset/excerpt matches. List takes a bounded
page offset/limit and returns compact metadata, allowing the model to discover
older sources without an unbounded automatic manifest.
Fresh URL reads create snapshots; explicit refresh creates a new immutable
version. Keep the old version until its expiry/deletion. No automatic refetch on
snapshot lookup. Errors remain ordinary bounded tool results except existing
account-compute refusals, which continue to terminate the operation.

Provide owner-checked list, export and delete routes under
`/conversations/{conversation_id}/web-snapshots`:

- `GET` collection: bounded metadata listing with opaque snapshot references.
- `GET /{snapshot_id}/export`: one retained snapshot as a bounded downloadable
  JSON document containing source metadata and extracted text.
- `DELETE /{snapshot_id}`: explicit deletion; wrong owner/conversation is 404.

These new API contracts have explicit user approval. Existing conversation DELETE
also removes snapshots by cascade. No automatic conversion to facts/memories or
cross-conversation/project search is included; snapshots remain source evidence.

### Encryption of traces and prompt inclusion

`messages.tool_calls` and `messages.tool_results` currently persist plaintext
JSON, including fetched URLs/content (`#359`). Encrypt new tool-trace writes in
a versioned JSON envelope using `ContentEncryption`, decrypt transparently on
authorized history reads, and retain backward-compatible reads of legacy JSON.
This preserves the existing external history shape and prevents plaintext
shadows of newly encrypted snapshots. Historical trace backfill needs a separate
explicit migration plan; do not claim old plaintext has been removed.

Construct the fetch tool with server-owned user/conversation/store context via
the per-turn registry factory. A loop-owned allowance object, never model kwargs,
supplies the current output budget. The registry currently creates tools per
request; no global mutable snapshot or allowance state is needed.

Across messages, inject a bounded manifest of retained source references, not
old page bodies or every old chunk. Use at most 20 recent entries initially,
bounded by the same prompt budget; title, ID, retrieval/expiry times suffice.
Older references remain usable by known ID and discoverable through the bounded
list action/route. Within one turn, retain already-read chunks and stop with an honest
partial synthesis when the remaining allowance is exhausted. This is not
automatic history compaction. A genuinely oversized base history still fails
under the existing guard.

## Unselected alternative: turn-only snapshots and resource bounds

For this alternative, use a server-owned, account-and-chat-turn-scoped snapshot
store in server-owned request execution state. Opaque random
IDs resolve only within that owner and turn. Follow-up turns must fetch again;
this is explicitly not durable document storage or resumable task execution.

Proposed initial bounds, subject to approval:

- 2 MiB maximum decoded HTTP body, enforced while streaming, including compressed
  responses; Content-Length alone is insufficient.
- 1 MiB extracted UTF-8 text per snapshot and 4 MiB total snapshot text per turn.
- At most eight snapshots per turn. Refuse additional snapshots with a bounded
  capacity result rather than silently evicting one the model may still read.
- Release all snapshots on completion, failure or cancellation. A process restart
  loses them and must produce an honest expiry/failure, not fabricated continuity.

Oversized pages receive an explicit `page_too_large` result, never a claim that
the entire page was read. Cache hits must pass the same extracted-size bound.
Retain DNS/redirect/private-network protections and untrusted-content wrapping.
Do not introduce persistent plaintext page storage as part of this change.

## Context budgeting: chunks alone are insufficient

Appending every chunk forever still overflows context. Before each tool batch
and model continuation, derive an available tool-result allowance from the
effective account/qualified-route context bound, reserving system instructions,
user input, tool schemas/framing, and the planned answer tokens. Enforce an
additional serialized-byte bound and account for JSON escaping and non-ASCII
text. Reuse the compute runtime's sizing rules instead of creating a competing
route selector; the final guarded dispatch remains authoritative.

For the initial implementation, keep all already-returned chunks in the prompt
and shrink new chunks to the remaining allowance. Distribute allowance across
multiple tool results in a batch, including bounded error/provenance wrappers.
When allowance runs out, stop reading and perform a tools-disabled synthesis
using the reserved answer space. Explain incomplete coverage. Do not silently
remove earlier user messages or evidence, and do not raise plan limits.

If the base prompt itself cannot fit, return the existing truthful context-limit
failure; paging cannot solve an already oversized history. If a provider rejects
an otherwise locally admissible prompt, preserve the failure rather than retrying
without bound. Keep synthesis within the existing tool-loop and cost constraints.

This bounded-first design supports targeted page sections, but does not promise
exhaustive reading of arbitrary documents. Multi-stage summarization and
long-history compaction remain outside this contract because they change cost,
evidence retention and continuity semantics.

## Acceptance checks before shipping

1. A large admissible page is read in consecutive chunks with no gaps or overlap;
   Unicode offsets, EOF and partial final chunks are correct.
2. Later reads remain stable across turns and upstream changes; expired, forged,
   cross-conversation and cross-account IDs fail without exposing content.
3. Huge/chunked/compressed HTTP responses stop at the body ceiling. Cache hits,
   parallel reads, cancellation and snapshot-capacity exhaustion are bounded.
4. Multiple pages/chunks fit the remaining prompt allowance, including schemas,
   wrappers and final synthesis. Near-full histories and non-ASCII text do not
   bypass the final guard; already oversized input still fails honestly.
5. Streaming and non-streaming tool loops preserve valid assistant/tool pairing,
   source metadata and untrusted-result boundaries. Ordinary small pages work.
6. Per-turn rate accounting, per-call costs, tool-loop limits, routing policy and
   SSRF protections continue to hold. No live paid-provider tests without budget
   approval. Run backend and repository gates plus affected frontend contracts.

The conversation-scoped storage, schema, tool/API fields, limits and
prompt-inclusion policy have explicit user approval. Release additionally
requires expiry, deletion, authorization, encrypted-trace compatibility and
cross-worker quota tests. Legacy plaintext traces/cache entries are not removed
by this implementation; their migration needs a separately approved operation.
