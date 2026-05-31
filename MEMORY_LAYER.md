# Memory Layer Architecture

## Overview

Daemon's memory system captures, stores, and retrieves durable facts about users and projects across conversations. The pipeline uses Voyage AI asymmetric embeddings for semantic search, PostgreSQL with pgvector for storage, Fernet encryption for content at rest, and a multi-stage extraction → calibration → deduplication → retrieval workflow.

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
memories.content          → encrypted
extraction_log.input_snippet → encrypted
```

If `DAEMON_ENCRYPTION_KEY` is not set, content falls back to plaintext (logged as a warning on startup).

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

**Note:** Messages do not have an embedding column. Extraction operates on the text content directly.

#### `memories`
```sql
id UUID PRIMARY KEY
user_id UUID REFERENCES users(id)
content TEXT NOT NULL  -- Fernet-encrypted
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

`deduplicate_facts()` in `dedup.py` compares each extracted fact against existing active memories using embedding similarity:

| Scenario | Threshold | Action |
|---|---|---|
| Merge | ≥ 0.90 (config: `dedup_merge_threshold`) | Touch existing; don't insert |
| Supersede (generic) | ≥ 0.82 (config: `dedup_supersede_threshold`) | Replace existing; apply trust penalty |
| Supersede (same slot) | ≥ 0.65 (config: `dedup_supersede_same_slot_threshold`) | Replace within slot family |
| Below thresholds | < 0.65 | Insert as new memory |

Thresholds are calibrated from `tests/results/voyage_similarity_analysis.json`:
- Within-scenario max: 0.8374 / p95: 0.6621
- Cross-scenario max: 0.8046 / p95: 0.6080

**Slot families:** Memories with slots like `language.python` share the family `language`. Within a family, `.current`-suffixed slots (`vehicle.current`) trigger post-insert cleanup: other family members are closed (soft-deleted via `valid_to`).

**Sibling blocking:** Facts with different explicit slots at the merge/supersede threshold are inserted as parallel siblings rather than merged (e.g., `language.python` vs `language.typescript`).

**LLM contradiction check:** Before superseding, a `kimi-k2.5` call checks if the new fact contradicts the existing one. Contradictions are stored in metadata but do not block supersession (advisory).

**Protected explicit matches:** `user_created` memories within a 5-minute window from the same conversation are protected from extraction-driven supersession — they are touched instead.

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
- Clusters within-family memories by embedding similarity ≥ 0.65 (`CLUSTER_SIMILARITY_THRESHOLD`)
- For clusters of ≥ 3 memories, calls gpt-4o-mini to synthesize a summary fact
- Summary is stored as a new `category='summary'` memory with `tier='l1'`
- Source memories are demoted to `tier='l2'`

### 8. Summary Generation

`summary.py` — `generate_or_update_summary()`:

- Triggered after each successful extraction (best-effort)
- **Incremental:** fetches only messages since `summary_updated_at`
- Uses `auto_fast_model` from tier config
- Updates `conversations.summary` and `summary_updated_at`

### 9. Injection

`injection.py` — `build_memory_context()` assembles the memory block injected into the system prompt:

1. Fetch L0 memories → prepend as `[FROZEN MEMORIES]` (200-token budget)
2. Embed the latest user message with `voyage-4-lite`
3. Retrieve top 5 L1 memories via hybrid search
4. Fetch recent session summaries (up to 3)
5. Token-aware truncation to `max_tokens` budget
6. Format: `About this user:` / `Recent context:` / `[FROZEN MEMORIES]`

`assemble_system_prompt()` then prepends `DAEMON_SYSTEM_PROMPT`, adds personality/preferences, appends the memory block, and ensures the memory tools reminder is present.

---

## Embeddings

| Purpose | Model | Input type | Dimensions |
|---|---|---|---|
| Document (memory writes) | `voyage-4-large` | `input_type="document"` | 1024 |
| Query (retrieval) | `voyage-4-lite` | `input_type="query"` | 1024 |

Retry logic: 3 attempts with exponential backoff (1s → 2s → 4s). Module-level counters `_retry_count` and `_last_retry_at` are internal.

---

## Background Jobs

Handled by the arq worker (`orchestrator/worker/`):

1. **Extraction** — `process_extraction()` after each conversation turn
2. **Summary update** — `generate_or_update_summary()` post-extraction (best-effort)
3. **Consolidation** — `run_consolidation()` on configurable interval (default 7 days, enabled by `consolidation_enabled`)

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql://daemon:daemon@postgres:5432/daemon

# Encryption (Fernet key — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
DAEMON_ENCRYPTION_KEY=<fernet-key>

# Embeddings
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

### General System Caveats
- **Assumption:** Voyage asymmetric embeddings provide sufficient separation between document and query spaces. The 0.90/0.82/0.65 thresholds were calibrated against within/cross-scenario similarity distributions but there is known overlap in the generic supersede band (cross-scenario p95=0.6080 vs threshold 0.82 — no overlap; within-scenario max=0.8374 vs 0.90 — no overlap; the diagnostic false-positive pair at 0.8046 is below the 0.82 generic supersede threshold).
- **Trust signals are best-effort:** Failures in trust signal application are logged but do not block extraction or retrieval.
- **BM25 requires `content_tsv`:** If decryption fails or `content_tsv` is unpopulated for a memory, BM25 search will miss it. Vector search still works.
- **Consolidation is not yet independently verified:** The clustering logic and LLM summary synthesis have not been benchmarked against a ground-truth dataset.
