# Memory Layer Architecture

## Overview

Daemon's memory system captures, stores, and retrieves durable facts about users and projects across conversations. The pipeline uses Voyage AI asymmetric embeddings for semantic search, PostgreSQL with pgvector for storage, Fernet encryption for content at rest, and a multi-stage extraction → calibration → deduplication → retrieval workflow.

Provider embedding execution remains disabled by default. The dedicated
Azure/OpenRouter token-budgeted adapter is qualified in the deployment-specific
policy; the portable policy remains denied and selection defaults off. Core persistence accepts nullable vectors
and retrieval uses account-scoped lexical search when admission is denied.
Existing model/provider vector spaces remain isolated; fallback configuration
cannot bypass privacy or account compute policy.

**Key components:** `orchestrator/memory/{extraction,dedup,retrieval,store,injection,embedding,encryption,consolidation,trust,trust_signals,summary}.py`

---

## Storage

### Technology

- **PostgreSQL 16** with `pgvector` extension — direct asyncpg (no ORM)
- **Fernet** encryption applied at the application layer before write — all `content` fields encrypted transparently
- **Embeddings stored as plaintext** vectors so pgvector can index and search them

### Encryption

`ContentEncryption` (`orchestrator/memory/encryption.py`) encrypts content before it reaches PostgreSQL and decrypts on read:

```
messages.content          → encrypted
messages.tool_calls       → versioned encrypted JSON envelope (new writes)
messages.tool_results     → versioned encrypted JSON envelope (new writes)
web_snapshots metadata/text → separately encrypted, conversation-scoped
memories.content          → encrypted
extraction_log.input_snippet → encrypted
```

If `DAEMON_ENCRYPTION_KEY` is missing or invalid when memory storage is initialized, startup fails closed instead of writing plaintext. Encryption init, encrypt, and decrypt failures increment the `encryption_operations_failed_total` status metric and raise to the caller.

Tool-trace reads accept legacy plaintext JSON and decrypt new envelopes before
returning the existing history API shape. This change does not backfill legacy
traces. Web snapshots are temporary source evidence, not extracted memories;
their approved retention, quota, deletion and export contract is documented in
`docs/CHUNKED_WEB_READING_DESIGN.md`.

### Tables

#### `conversations`
```sql
id UUID PRIMARY KEY
user_id UUID REFERENCES users(id)
title TEXT
pipeline TEXT DEFAULT 'cloud'  -- 'cloud' or 'local'
summary TEXT
summary_updated_at TIMESTAMPTZ
message_count INTEGER DEFAULT 0
tokens_total INTEGER DEFAULT 0
pinned BOOLEAN DEFAULT FALSE
title_locked BOOLEAN DEFAULT FALSE
last_retrieved_memory_ids JSONB  -- tracking for trust signals
created_at TIMESTAMPTZ DEFAULT NOW()
updated_at TIMESTAMPTZ DEFAULT NOW()
```

Conversation list/detail reads derive `message_count` from saved messages belonging
to the conversation owner. Effective activity takes the latest message timestamp
and stored conversation activity/update timestamps; list sorting applies this
before pagination. Stored counters are not backfilled by these reads.

An unlocked, untitled pre-created draft queues automatic title generation after
its first message is saved. Existing nonempty conversations are not automatically
backfilled. Both title workers save through an atomic comparison against the
title read before generation and require the conversation to remain unlocked,
so a rename or lock during generation prevents the generated title from saving.

#### `messages`
```sql
id UUID PRIMARY KEY
conversation_id UUID REFERENCES conversations(id)
user_id UUID REFERENCES users(id)
role TEXT CHECK (role IN ('user', 'assistant', 'system'))
content TEXT NOT NULL  -- Fernet-encrypted
model TEXT
tokens_in INTEGER DEFAULT 0
tokens_out INTEGER DEFAULT 0
tool_calls JSONB
tool_results JSONB
status TEXT DEFAULT 'streaming'
metadata JSONB
reasoning_text TEXT  -- Fernet-encrypted
reasoning_duration_secs INTEGER
reasoning_model TEXT
created_at TIMESTAMPTZ DEFAULT NOW()
```

Assistant message status is an explicit lifecycle contract: `streaming` is
mutable, `complete` is eligible for extraction, and terminal `error` or
`cancelled` rows are retained for audit/history but skipped by extraction.
They are also excluded from future model prompt context. Extraction never
infers abandonment from message age.

**Note:** Messages do not have an embedding column. Extraction operates on the text content directly.

#### `memories`
```sql
id UUID PRIMARY KEY
user_id UUID REFERENCES users(id)
content TEXT NOT NULL  -- Fernet-encrypted
content_hash TEXT  -- HMAC-SHA256(normalized plaintext, DAEMON_AUTH_PEPPER)
embedding VECTOR(1024)  -- plaintext; Voyage 4-large document vectors
category TEXT CHECK (category IN ('fact', 'preference', 'project', 'summary', 'correction'))
source_type TEXT CHECK (source_type IN ('conversation', 'manual', 'import', 'extracted', 'user_created'))
source_conversation_id UUID REFERENCES conversations(id)
local_only BOOLEAN DEFAULT FALSE
confidence REAL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0)
status TEXT DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'deleted'))
superseded_by UUID REFERENCES memories(id)
memory_slot TEXT  -- hierarchical slot, e.g. 'language.python', 'vehicle.current'
tier TEXT DEFAULT 'l1' CHECK (tier IN ('l0', 'l1', 'l2'))
trust_score REAL DEFAULT 0.5
last_accessed_at TIMESTAMPTZ
access_count INTEGER DEFAULT 0
valid_from TIMESTAMPTZ DEFAULT NOW()
valid_to TIMESTAMPTZ  -- bitemporal: soft-delete timestamp
content_tsv tsvector  -- BM25; populated in application code after decryption
embedding_model TEXT  -- e.g. 'voyage-4-large'
created_at TIMESTAMPTZ DEFAULT NOW()
updated_at TIMESTAMPTZ DEFAULT NOW()
```

#### `memory_extraction_log`
```sql
id UUID PRIMARY KEY
conversation_id UUID REFERENCES conversations(id)
user_id UUID REFERENCES users(id)
input_snippet TEXT  -- Fernet-encrypted
extracted_facts JSONB
dedup_results JSONB  -- {merged, superseded, new, raw_count, ...}
model_used TEXT
created_at TIMESTAMPTZ DEFAULT NOW()
```

---

## Pipeline

### 1. Extraction

Triggered after each conversation turn via the worker queue. `process_extraction()` in `extraction.py`:

1. **Role-label** recent messages as `[User]: ...` / `[Assistant]: ...` and prefix with conversation summary
2. **Call gpt-4o-mini** (OpenRouter) with the extraction prompt — outputs structured JSON `{facts: [{content, category, confidence, slot}]}`
3. **Calibrate confidence** using linguistic hedge/strength signals:
   - Hedge words ("might", "probably") → cap at 0.65
   - Strong words ("definitely", "allergic") → boost to 0.92
   - Corrections always ≥ 0.90
4. **Validate** each fact:
   - Must start with "User" (or match `user`/`user's` pattern)
   - Reject assistant-prefixed facts, general-knowledge facts, filler patterns, ephemeral actions, and meta-descriptions
5. **Retry** on poor output (empty or >50% rejection rate) with an exhaustive coverage hint
6. **Log** the full extraction outcome to `memory_extraction_log`

**Assistant extraction guardrails:** The extraction prompt instructs the model to extract from `[Assistant]` messages only where the assistant explicitly references "you/your" (i.e., facts about the user). General knowledge, technical explanations, recommendations, and instructional content are skipped. Validation reinforces this by rejecting anything starting with "The [Capitalized..." without a user reference.

**Selective extraction** (you/your filter) is the current mode — verified by `tests/benchmark_results/assistant_extraction_results.json` showing median precision 0.9677, median recall 0.9667, adversarial false positives 0.

### 2. Deduplication

Production `deduplicate_facts()` uses a bounded PLAN → revalidate/COMMIT path
(`orchestrator/memory/equivalence.py`, `dedup.py`, and `store.py`). Owner-scoped,
active/open L1 lexical, slot-family and qualified-vector candidates are discovery
only. Complete facts are compared by the configured, account-budgeted background
role; only a complete `equivalent` verdict may merge a paraphrase. A missing
normal `stop` terminal reason, refusal or tool/function-call output also prevents
semantic merging, even when the visible text contains valid verdict JSON. Exactly
one nonempty text choice is required at this decision boundary. A missing
scope, unavailable judge, malformed/truncated verdict, correction, distinct fact
or uncertainty preserves the incoming fact without threshold supersession or
slot-family closure. Embeddings are not required for this path.

Provider work happens before write transactions. The selected canonical row is
owner-scoped and locked `FOR UPDATE`, and its plaintext and decision-state
snapshot are revalidated on the same connection before touching it. An explicit
tool replacement also locks/revalidates its target before closing it. Stale
decisions never overwrite a concurrent edit. Extraction commits each fact before
planning the next; concurrent writers with no shared candidate may still retain
paraphrases (a conservative false negative, not global exactly-once dedup).

Frozen L0, dreams and local-only records are excluded from cloud equivalence.
The cloud `memory_write` update tool refuses local-only or unknown-locality
targets before embedding, judging or writing, even with explicit replacement
text. It does not provide a concurrent privacy-revocation protocol for requests
already authorized from an earlier nonlocal snapshot.
Extracted facts may reuse an explicit canonical record without changing its
content/source/slot. An explicit incoming paraphrase is not silently suppressed
behind an extracted origin. Existing whitespace-normalized database hash
uniqueness remains a separate deterministic exception, including cross-source
reuse; conflict reuse is reported as merged, not newly inserted. No existing
records are reconciled by this source change.

The following thresholds and behaviors are retained **only for explicitly
historical offline dedup benchmarks**, not production merge authority:

| Scenario | Threshold | Action |
|---|---|---|
| Merge | `0.90` (config: `dedup_merge_threshold`) | Touch existing; don't insert |
| Supersede (generic) | `0.82` (config: `dedup_supersede_threshold`) | Replace existing; apply trust penalty |
| Supersede (same slot) | `0.65` (config: `dedup_supersede_same_slot_threshold`) | Replace within slot family |
| Below thresholds | < 0.65 | Insert as new memory |

Thresholds are calibrated from `tests/results/voyage_similarity_analysis.json`:
- Within-scenario max: 0.8374 / p95: 0.6621
- Cross-scenario max: 0.8046 / p95: 0.6080

**Historical slot families:** Memories with slots like `language.python` share the family `language`. The legacy benchmark's `.current` cleanup closes family members; production uses families for bounded discovery only and never infers supersession from the family label.

**Historical sibling blocking:** The threshold benchmark separates different explicit slots. Production may merge equivalent facts despite spelling/slot-label differences, but preserves different entities, values, negation, temporal scope, conditions and certainty.

**Historical contradiction check:** The retained benchmark's advisory contradiction helper is not used as proof of production equivalence. Workload routing remains configuration-owned; no new model is enabled by deduplication.

**Explicit provenance:** Production does not blindly touch a recent explicit match. It requires equivalence plus commit-time revalidation; corrections and uncertain facts are preserved separately.

### Embedding capability status

Authenticated `GET /status` adds an `embeddings` object while retaining legacy
counters. Static `configuration` (`eligible`, `unavailable`, `unknown`) is not a
provider test or proof of account-budgeted dispatch. Safe reason codes distinguish
missing credentials, unapproved routes, invalid configuration and missing adapters.
Legacy adapters report `budget_adapter_unavailable`; the dedicated Azure adapter
has an account-budget integration but still requires explicit selection and a
qualified policy entry. Adding a key alone does not make semantic memory ready.
Process-local outcomes, timestamps and configuration
denial counts have `observation_scope=backend_process`: they do not represent the
worker or survive restarts. The status read performs no provider request.

The Memory Browser shows embedding capability separately from browser loading
and errors. Missing/old-server status remains unknown, never green Ready. See
`docs/MEMORY_EMBEDDING_SCREEN.md` for the separately authorized fictional screen;
its results do not qualify or activate a private-memory provider.

### 3. Storage

`MemoryStore.insert_memory()` / `supersede_memory()` in `store.py`:

- Content encrypted via `ContentEncryption` before the SQL write
- `content_tsv` populated via `to_tsvector('english', decrypted_content)` in application code after decryption
- Embedding stored as a 1024-dimensional vector (Voyage `voyage-4-large` output dimension)
- Supersession creates a new row and soft-deletes the old (`valid_to = NOW()`)

### 4. Retrieval

`retrieve_memories()` in `retrieval.py` — **hybrid search**:

```
final_score = 0.5 × vector_sim + 0.3 × bm25_normalized + 0.2 × recency × confidence × trust
```

- **Vector search** via pgvector cosine distance (`embedding <=>`) — `voyage-4-lite` query model
- **BM25 search** via PostgreSQL `ts_rank(content_tsv, plainto_tsquery)` — supports lexical/exact-match queries
- **Scoring factors:** recency (7d/30d/90d decay), source boost (`project`/`important` +10%), access count boost (up to +15%), confidence, trust score
- **Minimum threshold:** final_score ≥ 0.15
- **Max returned:** 5 memories
- **Touch:** Retrieved memory IDs are updated asynchronously (`last_accessed_at`, `access_count += 1`)

For **local pipeline** conversations (pipeline = 'local'), vector search is skipped and only BM25 is used.

### 5. Trust Signals

`trust_signals.py` and `trust.py`:

- **Implicit positive:** On the next user turn after retrieval, if no `memory_write` correction was made, all retrieved memories receive +0.05 trust (capped at 1.0)
- **Explicit negative:** When a memory is superseded via dedup, if it was retrieved within the last 3 user turns or 30 minutes, it receives -0.10 trust (floored at 0.1)
- Trust score influences retrieval ranking (trust × confidence × recency term)

### 6. Tiering (L0 / L1 / L2)

Three-tier memory model (`memories.tier` column):

| Tier | Description | Injection |
|---|---|---|
| **L0** | Frozen/important memories | Always injected — no budget check; token-capped at 200 tokens |
| **L1** | Standard active memories | Retrieved via hybrid search; normal injection |
| **L2** | Consolidated/historical | Background consolidation only; not retrieved in normal flow |

L0 memories bypass embedding-based retrieval entirely. They are always prepended to memory context with a 200-token budget (`MAX_L0_CHARS = 600`).

### 7. Consolidation

`consolidation.py` runs as a background job (triggered post-extraction, also on schedule):

- Groups L1 memories by **slot family** (first two segments, e.g., `language.python` → `language`)
- Clusters within-family memories by embedding similarity ≥ 0.65 (CLUSTER_SIMILARITY_THRESHOLD)
- For clusters of ≥ 3 memories, calls gpt-4o-mini to synthesize a summary fact
- Summary is stored as a new `category='summary'` memory with `tier='l1'`
- Source memories are demoted to `tier='l2'`

### 8. Summary Generation

`summary.py` — `generate_or_update_summary()`:

- Triggered after each successful extraction (best-effort) and periodically by `generate_summary_job` (arq worker)
- **Cursor model:** persists `last_summarized_msg_count` in `conversations.metadata` JSONB and a per-iteration `snapshot_at` timestamp. The batch is read at `offset = persisted_baseline` and bounded by the contiguous-finalized prefix at the snapshot (the rank of the first non-finalized row, in `created_at ASC, id ASC` order, minus 1). The persisted baseline advances only by the rows actually incorporated in this iteration, capped at `contiguous_baseline` (matches the inline path on `summary.py:225`).
- **Atomic write:** `update_conversation_summary` advances `summary`, `summary_updated_at`, `summarized_message_count`, `last_summarized_msg_count`, and `summary_continuation_pending` in a single SQL row, with optimistic concurrency; on conflict the inline path surfaces continuation to the extraction caller which re-enqueues `generate_summary_job(force=True)`.
- **Continuation:** a full batch (100 rows in the worker, 20 in the inline path) sets `summary_continuation_pending` and the worker enqueues a forced summary continuation. The flag survives extraction retries via `consume_summary_continuation_pending` at the top of `extract_memories`.
- Uses the configured routine workload model, subject to account compute and provider privacy policy

### 9. Injection

`injection.py` — `build_memory_context()` assembles the memory block injected into the system prompt:

1. Fetch L0 memories → prepend as `[FROZEN MEMORIES]` (200-token budget)
2. Embed the latest user message with `voyage-4-lite`
3. Retrieve top 5 L1 memories via hybrid search
4. Fetch recent session summaries (up to 3)
5. Token-aware truncation to `max_tokens` budget
6. Format: `About this user:` / `Recent context:` / `[FROZEN MEMORIES]`

`assemble_system_prompt()` then prepends DAEMON_SYSTEM_PROMPT, adds personality/preferences, appends the memory block, and ensures the memory tools reminder is present.

---

## Embeddings

### Dedicated selected route (default off; qualification pending)

`EMBEDDING_ROUTE_ID` selects an entry in the separate `embedding_routes` policy
mapping. The implemented candidate is Azure-hosted OpenAI small through OpenRouter
at the existing storage dimensions; the exact model, input/batch bounds and price
ceiling live in `config/inference_policy.json`, with the adapter's implementation
identity in `orchestrator/memory/embedding_adapter.py`. It is not a completion
candidate or a fixed-price tool service. The portable entry remains unapproved;
the deployment entry is qualified under monitored admission. See [the approved adapter contract](docs/MEMORY_EMBEDDING_ADAPTER.md)
for privacy evidence, prospective criteria and actual qualification status.

When selected, this is the primary document/query route and never traverses the
legacy fallback chain. Its stable storage identity is
`openrouter:azure:openai/text-embedding-3-small:1024`; old identities remain
lexically discoverable, but their vectors are never compared against new query
vectors. New writes do not reembed old rows. Embeddings do not authorize merging.

Each outbound batch reserves bounded input-token compute on the existing account
scope before sending. Unknown sends and malformed receipts retain conservative
charges; validated usage settles under the existing ledger. Settlement failures
propagate rather than silently producing lexical success. No retries, redirects,
proxies or provider fallback are enabled on this adapter. Document/query inputs
are bounded without silent truncation, and receipts require finite nonzero vectors.

Native-chat context preparation runs inside the turn's existing producer account
scope, not a second admission. Authenticated memory writers use an outer scope;
background calls retain their existing background allocation. Local-only/unknown
sources cannot enter cloud embeddings or dream synthesis. Cloud L0 and summary
selection also requires explicit nonlocal metadata; known-local conversations
retain lexical/frozen context. Concurrent source revocation after an authorized
snapshot remains an acknowledged separate limitation.

`memory_read` derives locality from its server-bound, owner-checked conversation.
Cloud/unknown context excludes local-only or unclassified rows; known-local context
retains local lexical results without query-provider dispatch, including retrieval
fallback. Model-supplied privacy arguments cannot widen access. Compatibility
clients without a stored conversation receive cloud-safe lexical reads, not an
assumed permission for private memory. Explicit owner-created/imported nonlocal
facts without a conversation remain cloud-eligible writes.

### Retained legacy configuration (not executable admission)

| Purpose | Model | Input type | Dimensions |
|---|---|---|---|
| Document (memory writes) | `voyage-4-large` | `input_type="document"` | 1024 |
| Query (retrieval) | `voyage-4-lite` | `input_type="query"` | 1024 |
| Optional fallback | `voyageai/voyage-4-large` / `voyageai/voyage-4-lite` via OpenRouter | matching `document` / `query` input type | configured `EMBEDDING_DIMENSIONS` (default 1024) |
| Optional fallback | `text-embedding-3-small` via OpenAI | OpenAI embeddings API | configured `EMBEDDING_DIMENSIONS` (default 1024) |

Retained legacy retry logic: 3 attempts with exponential backoff (1s → 2s → 4s). These unqualified legacy adapters remain denied; this logic is not the selected Azure adapter's behavior. Counters `_retry_count` and `_last_retry_at` are exposed via `/status` as `embedding_retry_activations` and `embedding_last_retry_at`.

Fallback logic: Voyage remains primary. `EMBEDDING_FALLBACK_PROVIDERS` is empty by default; operators can explicitly configure an ordered `openrouter,openai` chain (or either provider alone). After 5 Voyage failures within 60 seconds, a circuit breaker skips Voyage and tries that chain until the failure window clears. OpenRouter reuses `OPENROUTER_API_KEY` and sends native `input_type="document"` / `input_type="query"` requests, but routed-vector parity with direct Voyage has not been demonstrated, so those vectors use a distinct `openrouter:<model>` storage identity. OpenAI vectors likewise use `openai:<model>`. Vector and BM25 search filter by enabled storage identity; retrieval embeds fallback queries only when that user has rows in the corresponding active or inferred historical space. Dedup reconciles enabled spaces lexically and by slot without cross-provider vector comparison, while excluding L0 and dream observations. OpenAI inputs are truncated to its smaller per-input limit. `/status` exposes `embedding_failures_total` and per-provider `embedding_provider_used` counters.

---

## Background Jobs

Handled by the arq worker (`orchestrator/worker/`):

1. **Extraction** — `process_extraction()` after each conversation turn
2. **Summary update** — `generate_or_update_summary()` post-extraction (best-effort)
3. **Consolidation** — `run_consolidation()` on configurable interval (default 7 days, enabled by `consolidation_enabled`)

Worker failures are persisted to `job_failures` before arq result TTL cleanup. Critical memory
jobs can send a best-effort alert through the existing mail sender when
`DAEMON_WORKER_FAILURE_ALERT_EMAIL` is configured.

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql://daemon:daemon@postgres:5432/daemon

# Encryption (Fernet key — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
DAEMON_ENCRYPTION_KEY=<fernet-key>

# Embeddings
EMBEDDING_ROUTE_ID=  # OFF until qualified policy and explicit selection
VOYAGE_API_KEY=<voyage-api-key>
EMBEDDING_DOCUMENT_MODEL=voyage-4-large
EMBEDDING_QUERY_MODEL=voyage-4-lite
EMBEDDING_DIMENSIONS=1024

# Dedup thresholds (calibrated from voyage_similarity_analysis.json)
DEDUP_MERGE_THRESHOLD=0.90
DEDUP_SUPERSEDE_THRESHOLD=0.82
DEDUP_SUPERSEDE_SAME_SLOT_THRESHOLD=0.65

# Consolidation
CONSOLIDATION_ENABLED=true
CONSOLIDATION_INTERVAL_DAYS=7
```

---

## Current Benchmarks & Verification Caveats

### Selective Assistant Extraction (you/your filter)
- **Artifact:** `tests/benchmark_results/assistant_extraction_results.json`
- **Median precision:** 0.9677 | **Median recall:** 0.9667 | **Adversarial false positives:** 0
- All 3 benchmark runs passed aggregate gates (precision ≥ 0.90 floor, no run below total precision 0.90)
- Verified that controlled assistant spot-check produces only user-specific memories — no assistant general knowledge, recommendations, or instructional content extracted
- **Caveat:** Scenario-level variance exists — individual runs showed occasional Scenario 1 regression or Scenario 6 precision/recall noise. Median behavior is stable.

### LongMemEval IE-assistant
- **Status:** Pending — blocked by host DB resolution (`socket.gaierror: [Errno -2] Name or service not known` when resolving the configured Postgres host)
- The benchmark script `tests/longmemeval/evaluate.py --limit 10` cannot connect from the host environment; requires containerized execution with proper DNS resolution to the postgres service
- IE-assistant (assistant→user implicit preference extraction) not yet independently verified against LongMemEval corpus

### LongMemEval Phase 4d Closeout
- **Status:** `no_shippable_composition`
- Final variance review found zero eligible composition candidates, so no composition run or full-corpus triple run was executed.

### General System Caveats
- **Assumption:** Voyage asymmetric embeddings provide sufficient separation between document and query spaces. The 0.90/0.82/0.65 thresholds were calibrated against within/cross-scenario similarity distributions but there is known overlap in the generic supersede band (cross-scenario p95=0.6080 vs threshold 0.82 — no overlap; within-scenario max=0.8374 vs 0.90 — no overlap; the diagnostic false-positive pair at 0.8046 is below the 0.82 generic supersede threshold).
- **Trust signals are best-effort:** Failures in trust signal application are logged but do not block extraction or retrieval.
- **BM25 requires `content_tsv`:** If decryption fails or `content_tsv` is unpopulated for a memory, BM25 search will miss it. Vector search still works.
- **Consolidation is not yet independently verified:** The clustering logic and LLM summary synthesis have not been benchmarked against a ground-truth dataset.
