# Daemon Memory Layer — Implementation Plan

## TL;DR

> **Quick Summary**: Implement persistent memory for Daemon — PostgreSQL+pgvector storage, ARQ+Redis background processing, automatic fact extraction, semantic retrieval with passive injection, memory tools, conversation persistence, user preferences, and API endpoints.
>
> **Deliverables**:
> - PostgreSQL+pgvector database with full schema (users, conversations, messages, memories, extraction log)
> - asyncpg connection pool with AppState lifecycle management
> - Message + conversation persistence wired into `/chat` SSE endpoint
> - Automatic fact extraction pipeline (GPT-4o-mini) with deduplication
> - ARQ background worker (extraction, titles, summaries, garbage collection)
> - Semantic + temporal memory retrieval with passive system prompt injection
> - `memory_read` + `memory_write` Daemon tools
> - REST API endpoints for conversations, memories, user settings, system status
> - User personality presets + characteristics + custom instructions
> - Fernet content encryption at rest
> - Bootstrap memories + migration system + seed scripts
> - Automated pytest test suite
>
> **Estimated Effort**: Large (4 days, 16 tasks)
> **Parallel Execution**: YES — 9 waves, ~40% faster than sequential
> **Critical Path**: Task 1 → Task 2 → Task 5 → Task 6 → Task 8 → Task 9 → Task 11

---

## Context

### Original Request

Complete implementation of Phase 2 (Memory Layer) for the Daemon multi-agent AI assistant, following the 24-section design brief provided by the project owner. The brief specifies every architectural decision, schema, prompt, algorithm, and configuration value.

### Codebase State (from exploration)

**CRITICAL — actual structure differs from design brief:**
- Source code lives in `orchestrator/` (NOT `backend/` as brief states). `backend/` only contains a Dockerfile.
- Package manager is `uv` with `pyproject.toml` (NOT pip/requirements.txt).
- Root `Dockerfile` copies `orchestrator/`, runs `uvicorn orchestrator.main:app`.
- `docker-compose.yml` currently has 3 services: backend (port 8000), frontend (port 3000), open-webui (port 8080).
- `config.py` uses `@lru_cache` on `get_settings()` — DB/Redis pools CANNOT live in Settings class.
- `main.py` endpoints: `/health`, `/v1/chat/completions`, `/chat` (SSE), `/v1/models`, `/tts`, `/stt`, etc.
- `daemon.py`: `stream_sse_chat()` is core SSE generator. Uses litellm. Tool calling via `completion_with_tools()`. No persistence.
- `router.py`: `route_message()` detects `/local` prefix → `RoutingDecision` with `local_requested=True`.
- `prompts.py`: Generic "You are Daemon" prompt (no hardcoded user name). Lists all tools.
- `models.py`: `ChatRequest`, `OpenAIChatRequest`, etc. `conversation_id` exists but only for SSE session tracking.
- `tools/`: `registry.py`, `executor.py`, `builtin.py` handle tool registration/execution.
- `pyproject.toml` deps: fastapi, httpx, litellm, pydantic-settings, uvicorn. Dev: pytest, pytest-asyncio.

### Metis Review Findings (incorporated)

1. **AppState class required** — `@lru_cache` on `get_settings()` means database pools and Redis connections must live in a separate `AppState` class initialized via FastAPI's lifespan handler. Settings remains config-only.
2. **Skip memory for `/v1/chat/completions`** — Open WebUI manages its own state. Memory injection + extraction applies ONLY to the `/chat` SSE endpoint.
3. **Frontend keeps sending history (no change)** — No modifications to frontend contract. Backend stores messages for extraction but doesn't reconstruct history from DB.
4. **Worker as separate container** — Same Docker image, different CMD: `arq orchestrator.worker.WorkerSettings`.
5. **Graceful degradation** — If Postgres/Redis unavailable at startup or during operation, fall back to stateless mode. Chat must still work without memory.
6. **Backward compatibility** — Existing tests and `/v1/chat/completions` endpoint MUST pass unchanged at every task boundary.
7. **All file paths use `orchestrator/`** — Brief's `backend/` paths must be translated throughout.

---

## Work Objectives

### Core Objective

Transform Daemon from a stateless chatbot into a persistent assistant that remembers user context across conversations through automatic fact extraction, semantic retrieval, and active memory tools.

### Concrete Deliverables

- `orchestrator/memory/` — 11 Python modules (store, embedding, encryption, extraction, dedup, retrieval, injection, summarization, titles, tools, garbage)
- `orchestrator/db.py` — asyncpg connection pool + AppState
- `orchestrator/worker.py` — ARQ background worker
- `orchestrator/routes/` — 4 API route modules (conversations, memories, users, system)
- `migrations/` — 7 SQL migration files
- `scripts/` — 3 utility scripts (migrate, seed, generate_key)
- Updated: `docker-compose.yml`, `config.py`, `main.py`, `daemon.py`, `prompts.py`, `pyproject.toml`
- `tests/` — pytest test suite for memory layer

### Definition of Done

- [ ] `docker compose up` starts all 6 services (backend, worker, frontend, open-webui, postgres, redis) with health checks passing
- [ ] Messages sent via `/chat` are persisted to PostgreSQL (encrypted)
- [ ] Facts are automatically extracted within 2 minutes of conversation idle
- [ ] New conversations show injected memory context from previous conversations
- [ ] Daemon can call `memory_read` and `memory_write` tools
- [ ] All API endpoints return correct responses
- [ ] User preferences modify Daemon's system prompt
- [ ] Existing tests pass: `uv run pytest`
- [ ] Memory pipeline test suite passes

### Must Have

- PostgreSQL 16 + pgvector with HNSW index
- asyncpg (raw SQL, no ORM)
- ARQ + Redis for background processing
- Fernet encryption on content columns
- text-embedding-3-small (1536 dims) via direct OpenAI API
- GPT-4o-mini via OpenRouter for extraction/summarization
- Passive injection (top-5 memories + 3 summaries, 1500 token budget)
- Active tools (memory_read semantic + temporal, memory_write create/update/delete)
- Conversation persistence (messages, titles, summaries)
- Deduplication with configurable similarity thresholds
- User preferences (8 personality presets, 4 characteristic axes, custom instructions)
- Graceful degradation when DB/Redis unavailable

### Must NOT Have (Guardrails)

- **No ORM** — raw asyncpg only. No SQLAlchemy, no Tortoise, no Prisma.
- **No frontend changes** — Phase 2 is backend only. Frontend sends history as before.
- **No memory on `/v1/chat/completions`** — Open WebUI endpoint stays stateless.
- **No graph memory** — pure vector similarity + category filtering. No entity-relationship modeling.
- **No multi-user auth** — single user mode with `DEFAULT_USER_ID`. Schema supports multi-user but no auth logic.
- **No conversation history reconstruction** — backend stores messages but doesn't rebuild history for the LLM. Frontend continues sending full history.
- **No hardcoded user names** — all prompts use "the user" / "the current user". No "Julian" or similar.
- **No breaking changes to existing endpoints** — `/v1/chat/completions`, `/health`, `/v1/models` must work unchanged.
- **No over-extraction** — most conversations yield 0-3 facts. Empty arrays are fine.
- **No premature abstraction** — no base classes, no factory patterns, no dependency injection frameworks. Simple functions and classes.

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.
> Every criterion is verified by running commands or using tools.

### Test Decision

- **Infrastructure exists**: YES (pytest + pytest-asyncio in dev deps)
- **Automated tests**: YES (tests after implementation)
- **Framework**: pytest + pytest-asyncio (already configured)
- **Test approach**: Each module gets a test file. Tests run after implementation is complete (Task 16).

### Agent-Executed QA (MANDATORY — ALL tasks)

Every task includes Bash-based QA scenarios:
- **Database operations**: `psql` or Python scripts to verify schema/data
- **API endpoints**: `curl` to verify request/response contracts
- **Background jobs**: Python scripts to verify job execution
- **System integration**: `docker compose` commands to verify service health

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
└── Task 1: Docker Compose + Infrastructure

Wave 2 (After Wave 1):
├── Task 2: Database Layer + Config + AppState
├── Task 3: Schema Migrations + Seed
└── Task 4: Core Utilities (encryption, embedding)

Wave 3 (After Wave 2):
└── Task 5: Memory Store (store.py)

Wave 4 (After Wave 3):
├── Task 6: Message Persistence (/chat wiring)
└── Task 7: Extraction Pipeline + Dedup

Wave 5 (After Wave 4):
├── Task 8: Background Worker (ARQ)
└── Task 9: Retrieval & Injection

Wave 6 (After Wave 5):
├── Task 10: Conversation Summaries
├── Task 11: System Prompt Integration
└── Task 12: Memory Tools

Wave 7 (After Wave 6):
├── Task 13: API Routes
└── Task 14: User Preferences

Wave 8 (After Wave 7):
└── Task 15: Garbage Collection + Bootstrap + Prompts Cleanup

Wave 9 (After Wave 8):
└── Task 16: Automated Tests

Critical Path: 1 → 2 → 5 → 6 → 8 → 9 → 11
Parallel Speedup: ~35% faster than sequential
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|-----------|--------|---------------------|
| 1 | None | 2, 3, 4 | None |
| 2 | 1 | 5 | 3, 4 |
| 3 | 1 | 5 | 2, 4 |
| 4 | None (pure utility) | 5, 7 | 2, 3 |
| 5 | 2, 3, 4 | 6, 7, 8, 9, 10, 12, 13 | None |
| 6 | 5 | 8 | 7 |
| 7 | 4, 5 | 8 | 6 |
| 8 | 6, 7 | 10 | 9 |
| 9 | 5 | 11 | 8 |
| 10 | 8 | 11 | 11 |
| 11 | 9, 10 | 12 | None |
| 12 | 5, 9 | None | 13, 14 |
| 13 | 5 | None | 12, 14 |
| 14 | 9 | None | 12, 13 |
| 15 | 8 | 16 | None |
| 16 | All | None | None |

---

## Specifications Reference

### Spec A: Database Schema (SQL)

All migration files implement this schema. Extensions: `pgcrypto` (gen_random_uuid), `vector` (pgvector).

```sql
-- 001_create_extensions.sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- 002_create_users.sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    encryption_key_id TEXT,
    settings JSONB DEFAULT '{}'::jsonb
);

-- 003_create_conversations.sql
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    title TEXT,
    summary TEXT,
    pipeline TEXT NOT NULL DEFAULT 'cloud'
        CHECK (pipeline IN ('cloud', 'local')),
    message_count INTEGER DEFAULT 0,
    total_tokens_in INTEGER DEFAULT 0,
    total_tokens_out INTEGER DEFAULT 0
);
CREATE INDEX idx_conversations_user_updated ON conversations (user_id, updated_at DESC);
CREATE INDEX idx_conversations_user_pipeline ON conversations (user_id, pipeline);

-- 004_create_messages.sql
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    tool_calls JSONB,
    tool_results JSONB
);
CREATE INDEX idx_messages_conversation_created ON messages (conversation_id, created_at);
CREATE INDEX idx_messages_user_created ON messages (user_id, created_at DESC);

-- 005_create_memories.sql
CREATE TABLE memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    content TEXT NOT NULL,
    category TEXT NOT NULL
        CHECK (category IN ('preference', 'fact', 'project', 'correction')),
    embedding VECTOR(1536),
    embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    last_accessed_at TIMESTAMPTZ,
    access_count INTEGER DEFAULT 0,
    local_only BOOLEAN DEFAULT FALSE,
    confidence FLOAT DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'pending', 'inactive', 'rejected')),
    source_type TEXT NOT NULL DEFAULT 'extracted'
        CHECK (source_type IN (
            'extracted', 'user_confirmed', 'user_corrected',
            'user_created', 'bootstrapped'
        )),
    superseded_by UUID REFERENCES memories(id) ON DELETE SET NULL
);
CREATE INDEX idx_memories_embedding_hnsw
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_memories_user_category_status
    ON memories (user_id, category, status);
CREATE INDEX idx_memories_user_accessed
    ON memories (user_id, last_accessed_at DESC)
    WHERE status = 'active';
CREATE INDEX idx_memories_embedding_model
    ON memories (embedding_model)
    WHERE status = 'active';

-- 006_create_extraction_log.sql
CREATE TABLE memory_extraction_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    input_snippet TEXT,
    extracted_facts JSONB NOT NULL,
    dedup_results JSONB,
    model_used TEXT NOT NULL
);
CREATE INDEX idx_extraction_log_user_created
    ON memory_extraction_log (user_id, created_at DESC);
```

### Spec B: Extraction Prompt

```
You are a memory extraction system. Given a conversation between a user and an AI assistant, extract facts worth remembering about the user.

Rules:
- Extract ONLY facts about the user, not about general topics discussed.
- Each fact must be a single, atomic statement under 280 characters.
- Assign exactly one category: preference, fact, project, or correction.
- Assign confidence 0.0-1.0 based on how explicitly the user stated the fact.
- If a fact updates or contradicts something previously known, include a "supersedes" description.
- Capture unresolved tasks/questions as open_threads.
- Most conversations yield 0-3 facts. Return empty arrays if nothing worth remembering.
- IGNORE tool outputs (search results, image URLs, etc.) — focus on what the user said and decided.

Categories:
- preference: Communication style, tool choices, likes/dislikes, workflow preferences
- fact: Biographical info, location, relationships, credentials
- project: Current work, technical decisions, goals, deadlines
- correction: User explicitly correcting a previous assumption ("actually, I meant X not Y")

Respond with valid JSON only. No markdown, no explanation.

{
  "facts": [
    {
      "content": "...",
      "category": "preference|fact|project|correction",
      "confidence": 0.0-1.0,
      "supersedes": "description of old fact this replaces, or null"
    }
  ],
  "open_threads": ["description of unresolved task/question, or empty array"]
}

Examples:

CONVERSATION:
User: Can you help me pick between asyncpg and SQLAlchemy for my project?
Assistant: For a single developer building a FastAPI app, asyncpg gives you...
User: Yeah, let's go with raw asyncpg. I want to understand every query.

EXTRACTION:
{
  "facts": [
    {
      "content": "Prefers raw asyncpg over SQLAlchemy — wants to understand every query",
      "category": "preference",
      "confidence": 0.95,
      "supersedes": null
    }
  ],
  "open_threads": []
}

CONVERSATION:
User: Actually I changed my mind about the GPU. Going with the 5090 TUF, not the Windforce.
Assistant: Makes sense — better thermals and RMA process for $100 more.
User: Yeah. Also need to figure out the cooling situation but that can wait.

EXTRACTION:
{
  "facts": [
    {
      "content": "Chose ASUS TUF 5090 over Windforce OC — better thermals and RMA, $100 delta acceptable",
      "category": "project",
      "confidence": 1.0,
      "supersedes": "Was considering Windforce OC 5090"
    }
  ],
  "open_threads": ["Cooling solution for 5090 build undecided"]
}

CONVERSATION:
User: What's the weather in Tokyo?
Assistant: Currently 12°C and cloudy in Tokyo...
User: Thanks

EXTRACTION:
{
  "facts": [],
  "open_threads": []
}

Now extract from this conversation:

{conversation_summary}

Recent messages:
{messages}
```

### Spec C: Summarization Prompt

```
Summarize this conversation in 2-5 sentences. Focus on:
- What was discussed (topics, decisions)
- What was decided or built
- Key outcomes

End with "Open: [list unresolved questions/tasks]" or "Open: none" if everything was resolved.

Be concise. This summary is used to provide context in future conversations, not as a transcript.

{previous_summary_if_exists}

Messages:
{messages}
```

### Spec D: Title Generation Prompt

```
Generate a 3-6 word title for this conversation. No quotes, no punctuation, no emoji. Just a concise descriptive title.

First message: {first_user_message}
First response: {first_assistant_message_truncated_500_chars}

Title:
```

### Spec E: Dedup Algorithm

For each extracted fact, compare against existing active memories:

| Similarity | Action |
|-----------|--------|
| > 0.92 | **Skip** — near-duplicate. Touch existing memory timestamp. |
| 0.75 – 0.92 with `supersedes` field | **Supersede** — insert new, mark old as inactive with `superseded_by` pointer. |
| 0.75 – 0.92, same category, no `supersedes` | **Update** — replace content + re-embed existing memory. |
| 0.75 – 0.92, different category | **Insert** — separate fact. |
| < 0.75 | **Insert** — new distinct fact. |

Thresholds are config-driven: `DEDUP_THRESHOLD_EXACT=0.92`, `DEDUP_THRESHOLD_RELATED=0.75`.

### Spec F: Retrieval Scoring

Final score = `similarity × recency_boost × source_boost × confidence`

- `recency_boost = 1.0 / (1.0 + days_since_access × 0.01)` — gentle decay
- `source_boost = 1.3` if `source_type` in (`user_corrected`, `user_created`), else `1.0`
- Retrieve top-10 by vector similarity, re-rank with boosts, return top-5

### Spec G: Injection Format

```markdown
## What you know about this user

### Key context
- [2026-02-10] Prefers direct communication, no fluff — match their density (preference)
- [2026-02-08] Building Daemon, a multi-agent AI assistant with memory persistence (project)

### Recent sessions
- [2026-02-10] Discussed memory extraction model options. Decided GPT-4o-mini. Open: implementation plan needed.
```

Token budget: 1500 total. ~800 for memories, ~600 for summaries, ~100 formatting.

### Spec H: Docker Compose (target state)

```yaml
version: '3.8'
services:
  backend:
    build: .
    ports:
      - "8000:8000"
    environment:
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - BRAVE_API_KEY=${BRAVE_API_KEY}
      - ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DATABASE_URL=postgresql://daemon:${POSTGRES_PASSWORD}@postgres:5432/daemon
      - REDIS_URL=redis://redis:6379
      - DAEMON_ENCRYPTION_KEY=${DAEMON_ENCRYPTION_KEY}
      - EXTRACTION_MODEL=${EXTRACTION_MODEL:-openai/gpt-4o-mini}
      - SUMMARIZATION_MODEL=${SUMMARIZATION_MODEL:-openai/gpt-4o-mini}
      - DEFAULT_USER_ID=${DEFAULT_USER_ID:-00000000-0000-0000-0000-000000000001}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build: .
    command: arq orchestrator.worker.WorkerSettings
    environment:
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DATABASE_URL=postgresql://daemon:${POSTGRES_PASSWORD}@postgres:5432/daemon
      - REDIS_URL=redis://redis:6379
      - DAEMON_ENCRYPTION_KEY=${DAEMON_ENCRYPTION_KEY}
      - EXTRACTION_MODEL=${EXTRACTION_MODEL:-openai/gpt-4o-mini}
      - SUMMARIZATION_MODEL=${SUMMARIZATION_MODEL:-openai/gpt-4o-mini}
      - DEFAULT_USER_ID=${DEFAULT_USER_ID:-00000000-0000-0000-0000-000000000001}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend

  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    ports:
      - "8080:8080"
    environment:
      - OPENAI_API_BASE_URL=http://backend:8000/v1
      - OPENAI_API_KEY=dummy
    depends_on:
      - backend

  postgres:
    image: pgvector/pgvector:pg16
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=daemon
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=daemon
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U daemon"]
      interval: 5s
      timeout: 5s
      retries: 5
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    ports:
      - "6379:6379"

volumes:
  pgdata:
  redisdata:
```

### Spec I: MemorySettings Config Class

```python
class MemorySettings(BaseSettings):
    database_url: str = ""
    redis_url: str = "redis://redis:6379"
    openai_api_key: str = ""
    extraction_model: str = "openai/gpt-4o-mini"
    summarization_model: str = "openai/gpt-4o-mini"
    daemon_encryption_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    memory_injection_max_tokens: int = 1500
    memory_injection_max_memories: int = 5
    memory_injection_max_summaries: int = 3
    dedup_threshold_exact: float = 0.92
    dedup_threshold_related: float = 0.75
    extraction_debounce_seconds: int = 120
    extraction_token_threshold: int = 10000
    summary_idle_minutes: int = 30
    summary_token_threshold: int = 15000
    gc_inactive_retention_days: int = 90
    gc_rejected_retention_days: int = 30
    gc_pending_retention_days: int = 30
    default_user_id: str = "00000000-0000-0000-0000-000000000001"
    default_username: str = "default"
```

### Spec J: Personality Presets

| Preset | Instruction text |
|--------|-----------------|
| `default` | _(empty — no modifier)_ |
| `professional` | "Respond in a formal, structured manner. Prioritize clarity and precision. Avoid casual language, humor, and filler." |
| `efficient` | "Be extremely concise. Lead with the answer. No preamble, no restating the question, no filler. Omit pleasantries." |
| `friendly` | "Be warm and conversational. Use a relaxed tone. It's fine to be casual and personable while still being helpful." |
| `candid` | "Be direct and honest. Don't hedge or soften. If something is a bad idea, say so. Prioritize truth over comfort." |
| `technical` | "Assume high technical literacy. Use precise terminology. Skip introductory explanations. Provide implementation details." |
| `mentor` | "Be patient and explanatory. Break down complex topics. Ask clarifying questions. Guide reasoning rather than just giving answers." |
| `minimal` | "Respond with the minimum words necessary. No explanations unless asked. No formatting unless requested. Raw answers only." |

### Spec K: Characteristic Modifiers

| Characteristic | `more` | `less` |
|---------------|--------|--------|
| `warmth` | "Be warm, empathetic, and personable in your responses." | "Be matter-of-fact and neutral. Skip warmth and pleasantries." |
| `enthusiasm` | "Be enthusiastic and encouraging about the user's ideas and work." | "Be measured and reserved. Don't express excitement or encouragement." |
| `emoji` | "Use emoji where they add clarity or tone." | "Never use emoji." |
| `formatting` | "Use headers, bullet points, and structured formatting liberally." | "Minimize formatting. Prefer prose paragraphs. Avoid bullet points and headers unless essential." |

`default` = omit modifier entirely.

### Spec L: Memory Tool Definitions

**memory_read:**
- Parameters: `query` (string), `mode` (semantic|temporal, default semantic), `after` (datetime, temporal only), `before` (datetime, temporal only), `category` (preference|fact|project|correction|any, default any), `limit` (1-20, default 5)

**memory_write:**
- Parameters: `action` (create|update|delete), `content` (string, max 280 chars), `category` (preference|fact|project|correction), `memory_id` (uuid, update/delete only), `supersedes_content` (string, create only — triggers dedup to find and deactivate old memory)

### Spec M: API Endpoint Contracts

```
GET    /conversations                → List (paginated: limit, offset, sort, order)
GET    /conversations/{id}           → Full conversation with messages
DELETE /conversations/{id}           → Delete + cascade
PATCH  /conversations/{id}           → Update title

GET    /memories                     → List (status, category, search, limit, offset, sort, order)
GET    /memories/{id}                → Single memory with metadata
POST   /memories                     → Create memory manually
PATCH  /memories/{id}                → Update content/status
DELETE /memories/{id}                → Soft delete (status=inactive)
DELETE /memories/{id}?hard=true      → Hard delete
POST   /memories/{id}/confirm        → Confirm pending memory
POST   /memories/{id}/reject         → Reject pending memory
GET    /memories/export              → Export all as JSON (no embeddings)
POST   /memories/import              → Import from JSON (mode: merge|replace)
POST   /memories/reembed             → Trigger re-embedding job

GET    /users/me/settings            → Current user settings
PATCH  /users/me/settings            → Partial merge update
GET    /users/me/settings/presets    → List personality presets

GET    /status                       → DB health, queue depth, memory count
```

### Spec N: System Prompt Assembly Order

```
1. BASE DAEMON PROMPT          (~800 tokens)  — identity, capabilities, tools
2. USER PREFERENCES BLOCK      (~200 tokens)  — personality + characteristics + custom instructions
3. MEMORY CONTEXT BLOCK        (~1500 tokens) — top-5 memories + 3 summaries
4. CONVERSATION HISTORY        (variable)     — recent messages
```

### Spec O: What Gets Encrypted

| Data | Encrypted |
|------|-----------|
| `messages.content` (user + assistant) | YES |
| `messages.tool_calls` / `tool_results` | YES |
| `memories.content` | YES |
| `memories.embedding` | NO (search requires plaintext) |
| `conversations.title` | YES |
| `conversations.summary` | YES |
| `memory_extraction_log.input_snippet` | YES |
| `memory_extraction_log.extracted_facts` | YES |

---

## TODOs

- [x] 1. Docker Compose + Infrastructure Setup

  **What to do**:
  - Update `docker-compose.yml` to add: `postgres` (pgvector/pgvector:pg16), `redis` (redis:7-alpine), `worker` (same build as backend, cmd: `arq orchestrator.worker.WorkerSettings`)
  - Add health checks for postgres (`pg_isready`) and redis (`redis-cli ping`)
  - Add `depends_on` with `condition: service_healthy` for backend + worker
  - Add volumes: `pgdata`, `redisdata`
  - Keep existing frontend and open-webui services unchanged
  - Add new env vars to backend + worker services (see Spec H)
  - Update `.env.example` with all new variables (DATABASE_URL, REDIS_URL, OPENAI_API_KEY, DAEMON_ENCRYPTION_KEY, EXTRACTION_MODEL, SUMMARIZATION_MODEL, POSTGRES_PASSWORD, DEFAULT_USER_ID)
  - Verify the root `Dockerfile` builds correctly with new dependencies (it copies `orchestrator/` and runs uvicorn)
  - Add `arq` as a dependency in `pyproject.toml` — the worker CMD needs it available

  **Must NOT do**:
  - Do NOT modify the frontend service configuration
  - Do NOT change existing backend environment variables
  - Do NOT remove the open-webui service
  - Do NOT add Kubernetes or production deployment configs

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward config file updates, no complex logic
  - **Skills**: []
    - No specialized skills needed for YAML/config editing

  **Parallelization**:
  - **Can Run In Parallel**: NO (first task)
  - **Parallel Group**: Wave 1 (alone)
  - **Blocks**: Tasks 2, 3, 4
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `docker-compose.yml` — current 3-service configuration to extend (preserve existing services exactly)
  - `Dockerfile` (root) — understand build context for worker service (same image, different CMD)
  - `backend/Dockerfile` — check if this is the actual build target vs root Dockerfile

  **API/Type References**:
  - Spec H in this plan — exact target docker-compose.yml content

  **Documentation References**:
  - pgvector Docker: `pgvector/pgvector:pg16` image includes pgvector extension pre-installed
  - ARQ worker: entrypoint is `arq orchestrator.worker.WorkerSettings`

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: All services start with health checks
    Tool: Bash
    Preconditions: Docker daemon running, .env file exists with POSTGRES_PASSWORD set
    Steps:
      1. docker compose config --quiet (validate YAML syntax)
      2. docker compose up -d postgres redis
      3. Wait 15s for health checks
      4. docker compose ps --format json
      5. Assert: postgres status is "healthy"
      6. Assert: redis status is "healthy"
      7. docker compose down
    Expected Result: Both infrastructure services start and pass health checks
    Evidence: docker compose ps output captured

  Scenario: Backend builds and starts with new env vars
    Tool: Bash
    Preconditions: postgres and redis healthy
    Steps:
      1. docker compose build backend
      2. Assert: build succeeds (exit code 0)
      3. docker compose up -d backend
      4. Wait 10s
      5. curl -s http://localhost:8000/health
      6. Assert: returns 200
      7. docker compose down
    Expected Result: Backend starts successfully with new dependencies
    Evidence: Health check response captured

  Scenario: Existing endpoints unaffected
    Tool: Bash
    Preconditions: All services running
    Steps:
      1. curl -s http://localhost:8000/health → Assert 200
      2. curl -s http://localhost:8000/v1/models → Assert 200 with models array
    Expected Result: No regression on existing endpoints
    Evidence: Response bodies captured
  ```

  **Commit**: YES
  - Message: `feat(infra): add PostgreSQL, Redis, and worker services for memory layer`
  - Files: `docker-compose.yml`, `.env.example`, `pyproject.toml`

---

- [ ] 2. Database Layer + Config + AppState

  **What to do**:
  - Create `orchestrator/db.py`:
    - `AppState` class holding: `db_pool` (asyncpg.Pool), `redis_pool` (arq Redis pool), `encryption` (ContentEncryption instance)
    - `init_app_state(settings)` async function: creates asyncpg pool (`min_size=5, max_size=20`), creates ARQ Redis pool, initializes encryption
    - `close_app_state(state)` async function: closes pools gracefully
    - Pool should be `None`-able for graceful degradation
  - Create FastAPI lifespan handler in `main.py`:
    - On startup: call `init_app_state()`, store on `app.state`
    - On shutdown: call `close_app_state()`
    - If `DATABASE_URL` is empty, skip DB init (graceful degradation)
    - If `REDIS_URL` connection fails, log warning, continue without Redis
  - Extend `orchestrator/config.py`:
    - Add `MemorySettings` class (see Spec I) — either as a separate class or merged into existing `Settings`
    - If merged: add all memory-related fields with defaults to existing `Settings` class
    - If separate: compose into `Settings` or load independently
    - Ensure `@lru_cache` on `get_settings()` still works (no mutable objects in Settings)
  - Add helper `get_app_state(request: Request) -> AppState` dependency for FastAPI routes
  - **CRITICAL**: `AppState` is NOT cached. It's created once in lifespan, stored on `app.state`, accessed via dependency injection. `Settings` remains `@lru_cache`-cached and contains only config values.

  **Must NOT do**:
  - Do NOT put connection pools in the `Settings` class
  - Do NOT use `@lru_cache` on anything with mutable state
  - Do NOT use SQLAlchemy or any ORM
  - Do NOT modify existing endpoint signatures

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Requires understanding FastAPI lifespan, asyncpg pool management, and DI patterns
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 4)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `orchestrator/config.py` — existing `Settings` class with `@lru_cache` pattern. Understand what's cached and why.
  - `orchestrator/main.py` — existing app initialization, endpoint registration. Lifespan handler goes here.
  - `orchestrator/models.py` — Pydantic model patterns used in project

  **API/Type References**:
  - Spec I in this plan — exact MemorySettings fields

  **External References**:
  - asyncpg pool: `asyncpg.create_pool(dsn, min_size=5, max_size=20)`
  - FastAPI lifespan: `@asynccontextmanager async def lifespan(app): ...`
  - ARQ Redis: `arq.create_pool(RedisSettings(host=...))`

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: AppState initializes with database pool
    Tool: Bash
    Preconditions: postgres and redis running via docker compose
    Steps:
      1. Create a test script that imports AppState, init_app_state, close_app_state
      2. Run: uv run python -c "
         import asyncio
         from orchestrator.db import init_app_state, close_app_state
         from orchestrator.config import get_settings
         async def test():
             state = await init_app_state(get_settings())
             assert state.db_pool is not None
             row = await state.db_pool.fetchval('SELECT 1')
             assert row == 1
             await close_app_state(state)
             print('PASS')
         asyncio.run(test())
         "
      3. Assert: output contains "PASS"
    Expected Result: Pool connects and executes query
    Evidence: Script output captured

  Scenario: Graceful degradation without DATABASE_URL
    Tool: Bash
    Preconditions: No DATABASE_URL set
    Steps:
      1. Run: DATABASE_URL="" uv run python -c "
         import asyncio
         from orchestrator.db import init_app_state
         from orchestrator.config import get_settings
         async def test():
             s = get_settings()
             s.database_url = ''  # Override
             state = await init_app_state(s)
             assert state.db_pool is None
             print('PASS')
         asyncio.run(test())
         "
      2. Assert: output contains "PASS"
    Expected Result: AppState created with None pool, no crash
    Evidence: Script output captured

  Scenario: Settings loads with memory config defaults
    Tool: Bash
    Steps:
      1. uv run python -c "from orchestrator.config import get_settings; s = get_settings(); print(s.embedding_model if hasattr(s, 'embedding_model') else 'MISSING')"
      2. Assert: output contains "text-embedding-3-small" (or equivalent default field)
    Expected Result: Memory settings accessible with defaults
    Evidence: Output captured
  ```

  **Commit**: YES
  - Message: `feat(db): add asyncpg AppState, FastAPI lifespan, and MemorySettings config`
  - Files: `orchestrator/db.py`, `orchestrator/config.py`, `orchestrator/main.py`

---

- [ ] 3. Schema Migrations + Seed

  **What to do**:
  - Create `migrations/` directory at project root
  - Create 7 SQL files implementing Spec A schema:
    - `001_create_extensions.sql` — pgcrypto + vector extensions
    - `002_create_users.sql`
    - `003_create_conversations.sql` — table + indexes
    - `004_create_messages.sql` — table + indexes
    - `005_create_memories.sql` — table + HNSW index + all indexes
    - `006_create_extraction_log.sql` — table + index
    - `007_seed_default_user.sql` — insert default user with UUID `00000000-0000-0000-0000-000000000001`
  - Create `scripts/migrate.py`:
    - Connects to DB via `DATABASE_URL` env var using asyncpg directly
    - Creates `_migrations` tracking table if not exists
    - Runs each `.sql` file in sorted order, skipping already-applied
    - Prints progress: "Applying 001_create_extensions.sql... ✓"
  - Create `scripts/seed.py`:
    - Creates default user (idempotent — ON CONFLICT DO NOTHING)
    - Optionally loads bootstrap memories from a JSON file: `python scripts/seed.py --memories bootstrap.json`
  - Create `scripts/generate_key.py`:
    - One-liner: generates a Fernet key and prints it
    - `from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())`

  **Must NOT do**:
  - Do NOT use Alembic or any migration framework
  - Do NOT add RLS enforcement (keep policies commented as future-proofing)
  - Do NOT make migration runner depend on `orchestrator/db.py` — it uses asyncpg directly

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Mostly writing SQL files and a simple Python script
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 2, 4)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - Spec A in this plan — complete SQL schema to implement verbatim

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Migrations run successfully on clean database
    Tool: Bash
    Preconditions: postgres running, DATABASE_URL set
    Steps:
      1. Drop and recreate database: docker compose exec postgres psql -U daemon -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
      2. uv run python scripts/migrate.py
      3. Assert: output shows all 7 migrations applied with ✓
      4. docker compose exec postgres psql -U daemon -d daemon -c "\dt"
      5. Assert: tables listed include users, conversations, messages, memories, memory_extraction_log, _migrations
      6. docker compose exec postgres psql -U daemon -d daemon -c "SELECT count(*) FROM users WHERE id = '00000000-0000-0000-0000-000000000001';"
      7. Assert: count = 1 (default user seeded)
    Expected Result: All tables created, default user exists
    Evidence: psql output captured

  Scenario: Migrations are idempotent
    Tool: Bash
    Preconditions: Migrations already applied
    Steps:
      1. uv run python scripts/migrate.py
      2. Assert: output shows no new migrations (all skipped)
      3. Assert: exit code 0
    Expected Result: Re-running migrations is safe
    Evidence: Script output captured

  Scenario: Vector extension and HNSW index exist
    Tool: Bash
    Steps:
      1. docker compose exec postgres psql -U daemon -d daemon -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"
      2. Assert: returns "vector"
      3. docker compose exec postgres psql -U daemon -d daemon -c "SELECT indexname FROM pg_indexes WHERE tablename = 'memories' AND indexname = 'idx_memories_embedding_hnsw';"
      4. Assert: returns the HNSW index name
    Expected Result: pgvector extension and HNSW index present
    Evidence: psql output captured
  ```

  **Commit**: YES
  - Message: `feat(db): add schema migrations, seed script, and key generation`
  - Files: `migrations/*.sql`, `scripts/migrate.py`, `scripts/seed.py`, `scripts/generate_key.py`

---

- [ ] 4. Core Utilities (Encryption + Embedding)

  **What to do**:
  - Create `orchestrator/memory/__init__.py` (empty or minimal exports)
  - Create `orchestrator/memory/encryption.py`:
    - `ContentEncryption` class using `cryptography.fernet.Fernet`
    - `__init__(self, key: str)` — takes key from env var `DAEMON_ENCRYPTION_KEY`
    - `encrypt(self, plaintext: str) -> str`
    - `decrypt(self, ciphertext: str) -> str`
    - Handle the case where key is empty/missing — return plaintext (graceful degradation, log warning)
  - Create `orchestrator/memory/embedding.py`:
    - `embed_text(text: str, model: str = "text-embedding-3-small") -> list[float]` — single text embedding
    - `embed_batch(texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]` — batch embedding
    - Uses `openai` library directly (NOT via OpenRouter/litellm) — the `OPENAI_API_KEY` env var
    - Returns 1536-dim vectors
    - Handles API errors with retry (3 attempts, exponential backoff)
    - Handles empty/whitespace text gracefully (return zero vector or raise)
  - Add new dependencies to `pyproject.toml`:
    - `asyncpg>=0.29.0`
    - `pgvector>=0.3.0`
    - `arq>=0.26.0`
    - `redis>=5.0.0`
    - `cryptography>=42.0.0`
    - `openai>=1.10.0`

  **Must NOT do**:
  - Do NOT use litellm for embeddings — direct OpenAI only
  - Do NOT use OpenRouter for embeddings — direct OpenAI API for latency
  - Do NOT add complex retry frameworks — simple exponential backoff

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Two small utility modules with clear specs
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 2, 3)
  - **Blocks**: Tasks 5, 7
  - **Blocked By**: None (pure utility, no infra dependency)

  **References**:

  **Pattern References**:
  - `pyproject.toml` — existing dependency format to follow
  - `orchestrator/tools/web_search.py` — example of external API client pattern in codebase

  **API/Type References**:
  - Spec O in this plan — what gets encrypted vs. not

  **External References**:
  - OpenAI embeddings API: `client.embeddings.create(model=..., input=[text])`
  - Fernet: `from cryptography.fernet import Fernet`

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Encryption round-trip
    Tool: Bash
    Steps:
      1. uv run python -c "
         from orchestrator.memory.encryption import ContentEncryption
         key = 'dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXQ0ND0='  # test key
         # Generate a proper Fernet key for testing
         from cryptography.fernet import Fernet
         key = Fernet.generate_key().decode()
         enc = ContentEncryption(key)
         ct = enc.encrypt('Hello World')
         assert ct != 'Hello World'
         pt = enc.decrypt(ct)
         assert pt == 'Hello World'
         print('PASS')
         "
      2. Assert: output is "PASS"
    Expected Result: Encrypt then decrypt returns original text
    Evidence: Script output

  Scenario: Encryption graceful degradation without key
    Tool: Bash
    Steps:
      1. uv run python -c "
         from orchestrator.memory.encryption import ContentEncryption
         enc = ContentEncryption('')
         result = enc.encrypt('Hello')
         assert result == 'Hello'  # passthrough
         print('PASS')
         "
      2. Assert: output is "PASS"
    Expected Result: Empty key means no encryption (passthrough)
    Evidence: Script output

  Scenario: Embedding produces 1536-dim vector
    Tool: Bash
    Preconditions: OPENAI_API_KEY set in environment
    Steps:
      1. uv run python -c "
         import asyncio
         from orchestrator.memory.embedding import embed_text
         async def test():
             vec = await embed_text('test sentence')
             assert len(vec) == 1536
             assert all(isinstance(v, float) for v in vec)
             print('PASS')
         asyncio.run(test())
         "
      2. Assert: output is "PASS"
    Expected Result: Returns float vector of correct dimensionality
    Evidence: Script output

  Scenario: Dependencies install correctly
    Tool: Bash
    Steps:
      1. uv sync
      2. Assert: exit code 0
      3. uv run python -c "import asyncpg, arq, cryptography, openai; print('PASS')"
      4. Assert: output is "PASS"
    Expected Result: All new deps importable
    Evidence: Import output
  ```

  **Commit**: YES
  - Message: `feat(memory): add encryption and embedding utility modules`
  - Files: `orchestrator/memory/__init__.py`, `orchestrator/memory/encryption.py`, `orchestrator/memory/embedding.py`, `pyproject.toml`

---

- [ ] 5. Memory Store (store.py)

  **What to do**:
  - Create `orchestrator/memory/store.py` — the central data access layer
  - `MemoryStore` class, initialized with `db_pool` (asyncpg.Pool) and `encryption` (ContentEncryption)
  - All methods are `async`. All use raw SQL via asyncpg.
  - **Conversation operations**:
    - `create_conversation(user_id, pipeline='cloud') -> UUID`
    - `get_conversation(conversation_id) -> dict | None`
    - `list_conversations(user_id, limit=20, offset=0) -> list[dict]`
    - `update_conversation(conversation_id, **fields)` — title, summary, message_count, tokens
    - `delete_conversation(conversation_id, pipeline=None)` — if pipeline='local', also delete local_only memories
  - **Message operations**:
    - `insert_message(conversation_id, user_id, role, content, model=None, tokens_in=None, tokens_out=None, tool_calls=None, tool_results=None) -> UUID`
    - `get_messages(conversation_id, limit=None) -> list[dict]`
    - `get_recent_messages(conversation_id, limit=10, exclude_roles=['system']) -> list[dict]` — for extraction input
  - **Memory operations**:
    - `insert_memory(user_id, content, category, embedding, confidence=1.0, source_type='extracted', source_conversation_id=None, local_only=False) -> UUID`
    - `get_memory(memory_id) -> dict | None`
    - `list_memories(user_id, status='active', category=None, limit=20, offset=0, sort='updated_at', order='desc') -> tuple[list[dict], int]` — returns (memories, total_count)
    - `update_memory_content(memory_id, content, embedding, confidence=None)`
    - `update_memory_status(memory_id, status)`
    - `supersede_memory(old_id, new_id)` — sets old to inactive, superseded_by=new_id
    - `touch_memory(memory_id)` — update last_accessed_at, increment access_count
    - `bulk_touch_memories(memory_ids)` — batch update access tracking
    - `delete_memory(memory_id, hard=False)` — soft: status=inactive; hard: DELETE row
    - `search_memories(user_id, embedding, limit=10, min_similarity=0.0, status_filter=['active'], category=None, pipeline=None) -> list[dict]` — cosine similarity via pgvector, respects local_only flag based on pipeline
    - `delete_memories_by_source(conversation_id, local_only_only=True)` — cascade for local conversation deletion
  - **Summary operations**:
    - `get_recent_summaries(user_id, limit=3, pipeline=None) -> list[dict]`
  - **Extraction log**:
    - `log_extraction(conversation_id, user_id, input_snippet, extracted_facts, dedup_results, model_used)`
  - **Bulk operations**:
    - `export_memories(user_id) -> list[dict]` — all active memories, no embeddings
    - `import_memories(user_id, memories, mode='merge')` — merge: dedup against existing; replace: clear and reimport
    - `count_memories(user_id, status='active') -> int`
  - **All content fields encrypted/decrypted** at the store layer per Spec O
  - Use `pgvector` for vector operations: `embedding::vector` casting, `<=>` operator for cosine distance
  - Vector similarity query: `1 - (embedding <=> $1::vector)` returns cosine similarity (0 to 1)

  **Must NOT do**:
  - Do NOT use any ORM patterns — raw SQL only
  - Do NOT encrypt embeddings — vectors must be plaintext for search
  - Do NOT add caching — premature at this stage
  - Do NOT use transactions unless needed for multi-step operations (supersession needs transaction)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Large module with many methods, SQL queries, encryption integration, and vector search
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (critical path)
  - **Parallel Group**: Wave 3 (alone)
  - **Blocks**: Tasks 6, 7, 8, 9, 10, 12, 13
  - **Blocked By**: Tasks 2, 3, 4

  **References**:

  **Pattern References**:
  - Spec A in this plan — exact schema to query against
  - Spec O in this plan — which fields to encrypt/decrypt
  - `orchestrator/memory/encryption.py` (Task 4) — ContentEncryption interface

  **API/Type References**:
  - asyncpg API: `pool.fetch()`, `pool.fetchrow()`, `pool.fetchval()`, `pool.execute()`
  - pgvector: `embedding <=> $1::vector` for cosine distance, `1 - (... <=>)` for similarity

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: CRUD lifecycle for conversation + messages
    Tool: Bash
    Preconditions: Migrations applied, AppState initialized
    Steps:
      1. Run Python script that:
         a. Creates conversation → assert returns UUID
         b. Inserts 3 messages (user, assistant, user) → assert returns UUIDs
         c. Gets messages → assert 3 returned in order
         d. Updates conversation title → assert no error
         e. Gets conversation → assert title matches
         f. Deletes conversation → assert no error
         g. Gets conversation → assert None
      2. Assert: script prints "PASS"
    Expected Result: Full CRUD lifecycle works
    Evidence: Script output

  Scenario: Memory insert + vector search
    Tool: Bash
    Preconditions: Migrations applied, OPENAI_API_KEY set
    Steps:
      1. Run Python script that:
         a. Embeds "User prefers dark mode"
         b. Inserts memory with embedding
         c. Embeds "What theme does the user like?"
         d. Searches with query embedding, limit=3
         e. Assert: inserted memory is in results
         f. Assert: similarity > 0.5
      2. Assert: script prints "PASS"
    Expected Result: Vector similarity search returns relevant memory
    Evidence: Script output

  Scenario: Content encryption at store layer
    Tool: Bash
    Steps:
      1. Run Python script that:
         a. Inserts message with content "Secret message"
         b. Reads raw DB row via direct SQL (bypassing store decrypt)
         c. Assert: raw content != "Secret message" (encrypted)
         d. Reads via store method
         e. Assert: returned content == "Secret message" (decrypted)
      2. Assert: script prints "PASS"
    Expected Result: Content encrypted in DB, decrypted on read
    Evidence: Script output
  ```

  **Commit**: YES
  - Message: `feat(memory): add MemoryStore with CRUD, vector search, and encryption`
  - Files: `orchestrator/memory/store.py`

---

- [ ] 6. Message Persistence (Wire into /chat)

  **What to do**:
  - Modify `orchestrator/main.py` `/chat` endpoint (or wherever the SSE chat handler is invoked):
    - Before processing: check if `AppState.db_pool` is available. If not, proceed without persistence (graceful degradation).
    - Accept optional `conversation_id` in request. If provided, use it. If not, create a new conversation.
    - After Daemon generates complete response: store user message and assistant response as messages in DB.
    - Increment `conversations.message_count` and token counters.
    - Track `tokens_in` and `tokens_out` from litellm response usage.
    - Store `tool_calls` and `tool_results` as JSONB if present in the response.
  - Modify SSE stream to yield `conversation_id` in the initial event so frontend knows which conversation this is.
  - The `/chat` endpoint currently receives the full message history from the frontend. Continue this pattern — do NOT reconstruct history from DB.
  - **Message storage timing**: Store AFTER the full response is generated (not during streaming). Buffer the complete response, then persist.
  - Do NOT modify `/v1/chat/completions` — it stays stateless for Open WebUI.

  **Must NOT do**:
  - Do NOT reconstruct message history from DB for the LLM context
  - Do NOT modify the frontend contract (keep accepting full history in request)
  - Do NOT touch `/v1/chat/completions`
  - Do NOT block the SSE stream on database writes — persistence can be fire-and-forget async

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Modifying core chat flow, needs careful understanding of existing SSE streaming
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 7)
  - **Blocks**: Task 8
  - **Blocked By**: Task 5

  **References**:

  **Pattern References**:
  - `orchestrator/main.py` — current `/chat` endpoint handler, SSE streaming setup
  - `orchestrator/daemon.py:stream_sse_chat()` — core SSE generator, understand yield flow and response assembly
  - `orchestrator/models.py:ChatRequest` — current request model (has `conversation_id` field for tracking)
  - `orchestrator/db.py` (Task 2) — AppState access pattern via `request.app.state`

  **API/Type References**:
  - `orchestrator/memory/store.py` (Task 5) — `create_conversation()`, `insert_message()`, `update_conversation()`

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Messages persisted after chat
    Tool: Bash
    Preconditions: All services running, DB migrated
    Steps:
      1. Send a chat request via curl to /chat endpoint with a simple message
      2. Wait for SSE stream to complete
      3. Query DB: SELECT count(*) FROM messages WHERE role = 'user';
      4. Assert: count >= 1
      5. Query DB: SELECT count(*) FROM messages WHERE role = 'assistant';
      6. Assert: count >= 1
      7. Query DB: SELECT count(*) FROM conversations;
      8. Assert: count >= 1
    Expected Result: Both user and assistant messages stored, conversation created
    Evidence: psql output captured

  Scenario: Chat works without database (graceful degradation)
    Tool: Bash
    Preconditions: Backend running but postgres stopped
    Steps:
      1. docker compose stop postgres
      2. curl -s -X POST http://localhost:8000/chat with a simple message
      3. Assert: response streams (HTTP 200), not 500
      4. docker compose start postgres
    Expected Result: Chat still works, just without persistence
    Evidence: curl output captured

  Scenario: /v1/chat/completions unchanged
    Tool: Bash
    Steps:
      1. curl -s -X POST http://localhost:8000/v1/chat/completions with standard OpenAI format
      2. Assert: returns valid response
      3. Query DB: verify no new messages stored from this endpoint
    Expected Result: OpenAI-compatible endpoint stays stateless
    Evidence: Response + DB query output
  ```

  **Commit**: YES
  - Message: `feat(chat): persist messages and conversations from /chat endpoint`
  - Files: `orchestrator/main.py`, `orchestrator/daemon.py` (if changes needed)

---

- [ ] 7. Extraction Pipeline + Dedup

  **What to do**:
  - Create `orchestrator/memory/extraction.py`:
    - `extract_facts(messages: list[dict], conversation_summary: str | None, settings) -> ExtractionResult`
    - Formats the extraction prompt (Spec B) with the last 10 user+assistant messages
    - Excludes system messages, tool results, thinking blocks, injected memory context
    - Calls extraction model (GPT-4o-mini via OpenRouter/litellm) with JSON response format
    - Parses response into structured `ExtractionResult` with `facts` and `open_threads`
    - Handles invalid JSON: retry once with simplified prompt, then skip
    - `input_snippet`: first 1000 chars of input for logging
    - Open threads become memories with `category='project'` and `[OPEN]` content prefix
  - Create `orchestrator/memory/dedup.py`:
    - `dedup_and_store(fact, user_id, store, settings) -> DedupResult`
    - Implements Spec E algorithm:
      1. Embed the new fact
      2. Search top-3 similar active/pending memories
      3. Apply threshold-based decision (skip/supersede/update/insert)
    - Returns `DedupResult` with action taken and matched memory IDs
    - All thresholds from config (not hardcoded)
  - `process_extraction(conversation_id, user_id, store, settings)`:
    - Orchestrates full pipeline: get recent messages → extract → dedup each fact → log result
    - This is what the ARQ job calls

  **Must NOT do**:
  - Do NOT use an LLM for dedup comparison — embedding similarity + category matching is sufficient
  - Do NOT extract from system messages or tool results
  - Do NOT extract from injected memory context (avoid circular extraction)
  - Do NOT hardcode similarity thresholds — use config values

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Complex pipeline with LLM integration, JSON parsing, embedding, and multi-step dedup logic
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 6)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 4, 5

  **References**:

  **Pattern References**:
  - Spec B in this plan — exact extraction prompt (include verbatim in code)
  - Spec E in this plan — exact dedup algorithm
  - `orchestrator/daemon.py` — how litellm calls are made (follow same pattern for extraction model call)
  - `orchestrator/memory/embedding.py` (Task 4) — `embed_text()` for embedding facts
  - `orchestrator/memory/store.py` (Task 5) — `search_memories()`, `insert_memory()`, `supersede_memory()`, `touch_memory()`, `update_memory_content()`, `log_extraction()`

  **API/Type References**:
  - Extraction model output: `{"facts": [{"content": str, "category": str, "confidence": float, "supersedes": str|null}], "open_threads": [str]}`
  - DedupResult: action (`inserted`|`updated`|`superseded`|`skipped`), matched memory ID, reason

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Extraction produces facts from conversation
    Tool: Bash
    Preconditions: OPENROUTER_API_KEY set, DB running
    Steps:
      1. Run Python script that calls extract_facts() with test messages:
         [{"role": "user", "content": "I live in Adelaide and prefer Python"}, {"role": "assistant", "content": "..."}]
      2. Assert: result.facts has >= 1 fact
      3. Assert: each fact has content, category, confidence fields
      4. Assert: at least one fact mentions Adelaide or Python
      5. Print facts for review
    Expected Result: Meaningful facts extracted
    Evidence: Extracted facts JSON

  Scenario: Dedup skips near-duplicate
    Tool: Bash
    Steps:
      1. Insert memory "Lives in Adelaide, Australia"
      2. Run dedup with fact "Based in Adelaide, Australia" (near-duplicate)
      3. Assert: DedupResult action is "skipped"
    Expected Result: Near-duplicate detected and skipped
    Evidence: DedupResult output

  Scenario: Dedup supersedes contradicting fact
    Tool: Bash
    Steps:
      1. Insert memory "Considering PostgreSQL vs MongoDB"
      2. Run dedup with fact "Chose PostgreSQL for database" with supersedes="Considering PostgreSQL vs MongoDB"
      3. Assert: old memory status is "inactive"
      4. Assert: old memory superseded_by points to new memory
      5. Assert: new memory status is "active"
    Expected Result: Old fact superseded, new fact active
    Evidence: DB state captured

  Scenario: Empty extraction for trivial conversation
    Tool: Bash
    Steps:
      1. Run extract_facts() with: [{"role": "user", "content": "What's 2+2?"}, {"role": "assistant", "content": "4"}]
      2. Assert: result.facts is empty array
    Expected Result: No facts extracted from trivial exchange
    Evidence: Extraction result
  ```

  **Commit**: YES
  - Message: `feat(memory): add fact extraction pipeline with deduplication`
  - Files: `orchestrator/memory/extraction.py`, `orchestrator/memory/dedup.py`

---

- [ ] 8. Background Worker (ARQ)

  **What to do**:
  - Create `orchestrator/worker.py`:
    - ARQ worker with `WorkerSettings` class
    - **Jobs**:
      - `extract_memories(ctx, conversation_id, user_id)` — calls `process_extraction()` from extraction.py
      - `generate_title(ctx, conversation_id, user_id)` — generates title from first user+assistant message pair using Spec D prompt, stores via `store.update_conversation()`
      - `generate_summary(ctx, conversation_id, user_id)` — placeholder, wired in Task 10
      - `garbage_collect(ctx)` — placeholder, wired in Task 15
    - **Worker startup**: creates its own asyncpg pool and encryption instance (separate from API server)
    - `on_startup(ctx)` — init DB pool, Redis, encryption
    - `on_shutdown(ctx)` — close pools
    - `redis_settings` from config
    - `max_jobs = 10`, `job_timeout = 120`
  - Create `orchestrator/memory/titles.py`:
    - `generate_conversation_title(messages, settings) -> str`
    - Uses Spec D prompt
    - Calls summarization model (GPT-4o-mini via litellm/OpenRouter)
    - Returns 3-6 word title, strips quotes/punctuation/emoji
  - Wire extraction scheduling into `/chat` endpoint (Task 6 code):
    - After storing messages: `await arq_pool.enqueue_job('extract_memories', conversation_id, user_id, _job_id=f"extract:{conversation_id}", _defer_by=timedelta(minutes=2))`
    - After first assistant response: `await arq_pool.enqueue_job('generate_title', conversation_id, user_id, _job_id=f"title:{conversation_id}")`
    - The `_job_id` with `_defer_by` handles debounce natively — re-enqueueing replaces existing deferred job

  **Must NOT do**:
  - Do NOT use Celery — ARQ only
  - Do NOT run extraction synchronously in the request path
  - Do NOT block SSE stream on job enqueueing — fire-and-forget

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Worker lifecycle management, async job scheduling, debounce pattern
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with Task 9)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 6, 7

  **References**:

  **Pattern References**:
  - Spec D in this plan — title generation prompt
  - `orchestrator/memory/extraction.py` (Task 7) — `process_extraction()` function to call from job
  - `orchestrator/memory/store.py` (Task 5) — `update_conversation()` for title storage
  - `orchestrator/db.py` (Task 2) — AppState pattern (worker creates its own instance)
  - `orchestrator/daemon.py` — litellm call pattern for title/summary LLM calls

  **External References**:
  - ARQ worker: `class WorkerSettings:`, `functions = [...]`, `cron_jobs = [cron(...)]`
  - ARQ debounce: `_job_id` + `_defer_by=timedelta(minutes=2)`
  - ARQ startup/shutdown: `on_startup`, `on_shutdown` coroutines in ctx

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Worker starts and connects
    Tool: Bash
    Preconditions: postgres, redis running
    Steps:
      1. docker compose up -d worker
      2. Wait 10s
      3. docker compose logs worker --tail=20
      4. Assert: logs show "Starting worker" or similar ARQ startup message
      5. Assert: no connection errors in logs
    Expected Result: Worker process starts cleanly
    Evidence: Worker logs captured

  Scenario: Title generation produces valid title
    Tool: Bash
    Steps:
      1. Run Python script:
         - Call generate_conversation_title() with test messages
         - Assert: result is string, 3-6 words, no quotes/emoji
      2. Print title
    Expected Result: Concise descriptive title generated
    Evidence: Title output

  Scenario: Extraction job fires after debounce
    Tool: Bash
    Steps:
      1. Enqueue extract_memories job with _defer_by=5s (shortened for testing)
      2. Check job status immediately — should be deferred
      3. Wait 10s
      4. Check memories table — should have new entries if conversation had extractable content
      5. Check extraction_log — should have new entry
    Expected Result: Job executes after debounce period
    Evidence: DB state and job status
  ```

  **Commit**: YES
  - Message: `feat(worker): add ARQ background worker with extraction and title jobs`
  - Files: `orchestrator/worker.py`, `orchestrator/memory/titles.py`, `orchestrator/main.py` (job scheduling)

---

- [ ] 9. Retrieval & Injection

  **What to do**:
  - Create `orchestrator/memory/retrieval.py`:
    - `retrieve_memories(user_id, query_text, store, settings, pipeline='cloud') -> list[dict]`:
      1. Embed query text
      2. Call `store.search_memories()` with limit=10
      3. Apply recency boost + source boost + confidence (Spec F)
      4. Re-rank and return top-N (default 5)
      5. Fire-and-forget `store.bulk_touch_memories()` for access tracking
    - `retrieve_temporal(user_id, after, before, store) -> list[dict]`:
      - Query conversation summaries within date range
      - Return summaries with dates
  - Create `orchestrator/memory/injection.py`:
    - `build_memory_context(user_id, user_message, store, settings, pipeline='cloud') -> str`:
      1. Call `retrieve_memories()` for top-5 memories
      2. Call `store.get_recent_summaries()` for last 3 summaries
      3. Format per Spec G
      4. Enforce token budget (1500 max):
         - If memories exceed ~800 tokens: drop lowest-scored
         - If summaries exceed ~600 tokens: truncate/drop oldest
         - If single memory > 200 tokens: log warning, truncate
      5. Return formatted string (empty string if no memories available)
    - `format_preferences_block(settings: dict) -> str`:
      - Reads user settings from DB (passed in as dict)
      - Applies personality preset (Spec J)
      - Applies characteristic modifiers (Spec K)
      - Appends custom instructions verbatim
      - Returns formatted block or empty string
    - `assemble_system_prompt(base_prompt, preferences_block, memory_block) -> str`:
      - Concatenates in order per Spec N:
        1. Base Daemon prompt
        2. Preferences block (if any)
        3. Memory context block (if any)
    - Token counting: use a simple heuristic (chars / 4) or tiktoken if already available

  **Must NOT do**:
  - Do NOT use tiktoken unless already in deps — chars/4 heuristic is fine
  - Do NOT make injection blocking if embedding API is slow — have a timeout
  - Do NOT inject memory for `/v1/chat/completions` endpoint
  - Do NOT inject memory if no memories exist — return empty string

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multi-step retrieval, scoring algorithm, prompt assembly, token budget management
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with Task 8)
  - **Blocks**: Tasks 11, 14
  - **Blocked By**: Task 5

  **References**:

  **Pattern References**:
  - Spec F in this plan — retrieval scoring formula
  - Spec G in this plan — injection format template
  - Spec J in this plan — personality preset texts
  - Spec K in this plan — characteristic modifier texts
  - Spec N in this plan — system prompt assembly order
  - `orchestrator/memory/store.py` (Task 5) — `search_memories()`, `get_recent_summaries()`, `bulk_touch_memories()`
  - `orchestrator/memory/embedding.py` (Task 4) — `embed_text()` for query embedding
  - `orchestrator/prompts.py` — existing `DAEMON_SYSTEM_PROMPT` that becomes the "base prompt"

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Memory retrieval returns ranked results
    Tool: Bash
    Preconditions: DB has 5+ active memories with embeddings
    Steps:
      1. Run Python script that:
         a. Calls retrieve_memories() with query "What programming language?"
         b. Assert: returns list of dicts with content, similarity, category
         c. Assert: results are sorted by final_score descending
         d. Assert: len(results) <= 5
      2. Print results
    Expected Result: Relevant memories returned in score order
    Evidence: Retrieval results

  Scenario: Injection respects token budget
    Tool: Bash
    Steps:
      1. Insert 20 memories with long content (100+ chars each)
      2. Call build_memory_context()
      3. Assert: result string length < 6000 chars (~1500 tokens)
      4. Assert: no more than 5 memories in output
    Expected Result: Output stays within budget
    Evidence: Context string length

  Scenario: Preferences block formats correctly
    Tool: Bash
    Steps:
      1. Call format_preferences_block({"preferences": {"personality": "efficient", "characteristics": {"emoji": "less"}, "custom_instructions": "Respond in Australian English."}})
      2. Assert: output contains "Be extremely concise"
      3. Assert: output contains "Never use emoji"
      4. Assert: output contains "Respond in Australian English"
    Expected Result: Preset + modifiers + custom instructions all present
    Evidence: Formatted block output

  Scenario: System prompt assembly order
    Tool: Bash
    Steps:
      1. Call assemble_system_prompt() with all three blocks
      2. Assert: preferences block appears AFTER base prompt
      3. Assert: memory block appears AFTER preferences block
    Expected Result: Correct injection order
    Evidence: Assembled prompt inspection
  ```

  **Commit**: YES
  - Message: `feat(memory): add retrieval, injection, and system prompt assembly`
  - Files: `orchestrator/memory/retrieval.py`, `orchestrator/memory/injection.py`

---

- [ ] 10. Conversation Summaries

  **What to do**:
  - Create `orchestrator/memory/summarization.py`:
    - `generate_summary(messages, previous_summary=None, settings) -> str`:
      - Formats Spec C prompt with messages and optional previous summary
      - Calls summarization model (GPT-4o-mini) via litellm
      - Returns 2-5 sentence summary ending with "Open: [items]" or "Open: none"
    - `should_summarize(conversation, last_summary_time, settings) -> bool`:
      - Returns True if:
        - Conversation idle > `summary_idle_minutes` (default 30)
        - Token count since last summary exceeds `summary_token_threshold` (default 15K)
        - New conversation started (previous conversation unsummarized)
  - Wire into worker (`orchestrator/worker.py`):
    - `generate_summary` job: calls `generate_summary()`, stores via `store.update_conversation(summary=...)`
    - Schedule summary generation:
      - On extraction completion (same debounce trigger, slightly longer delay)
      - Or as a separate deferred job alongside extraction
    - The summary job checks `should_summarize()` before doing work

  **Must NOT do**:
  - Do NOT summarize every message — use idle/threshold triggers
  - Do NOT store summary outside the conversations table
  - Do NOT make summary generation blocking

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Single-purpose module, straightforward LLM call + DB update
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 6 (with Tasks 11, 12)
  - **Blocks**: Task 11
  - **Blocked By**: Task 8

  **References**:

  **Pattern References**:
  - Spec C in this plan — summarization prompt
  - `orchestrator/memory/titles.py` (Task 8) — similar LLM call pattern for summary generation
  - `orchestrator/worker.py` (Task 8) — add summary job and scheduling
  - `orchestrator/memory/store.py` (Task 5) — `update_conversation()`, `get_recent_messages()`

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Summary generated from conversation
    Tool: Bash
    Steps:
      1. Call generate_summary() with 10 test messages about a project discussion
      2. Assert: result is string, 2-5 sentences
      3. Assert: result ends with "Open: " followed by items or "none"
    Expected Result: Coherent summary with open items
    Evidence: Summary text

  Scenario: Summary stored on conversation
    Tool: Bash
    Steps:
      1. Create conversation with messages
      2. Trigger summary job
      3. Wait for completion
      4. Query conversation from DB
      5. Assert: summary field is not NULL
    Expected Result: Summary persisted
    Evidence: DB query output
  ```

  **Commit**: YES
  - Message: `feat(memory): add conversation summary generation`
  - Files: `orchestrator/memory/summarization.py`, `orchestrator/worker.py` (add summary job)

---

- [ ] 11. System Prompt Integration

  **What to do**:
  - Modify `orchestrator/daemon.py` (or wherever the system prompt is assembled for `/chat`):
    - Before calling the LLM: invoke `build_memory_context()` with the current user message
    - Get user settings from DB: `store.get_user_settings(user_id) -> dict`
    - Build preferences block: `format_preferences_block(user_settings)`
    - Assemble final system prompt: `assemble_system_prompt(DAEMON_SYSTEM_PROMPT, preferences_block, memory_context)`
    - Use the assembled prompt instead of raw `DAEMON_SYSTEM_PROMPT`
    - Add timeout on memory retrieval (3s max) — if exceeded, proceed without memory
    - If `AppState.db_pool` is None (graceful degradation), use raw `DAEMON_SYSTEM_PROMPT`
  - Add `get_user_settings(user_id)` to `store.py` if not already present:
    - `SELECT settings FROM users WHERE id = $1`
    - Returns empty dict if no settings configured
  - Modify `orchestrator/prompts.py`:
    - Add memory tool descriptions to `DAEMON_SYSTEM_PROMPT` (the section about memory tools from Spec L context):
      ```
      ## Memory

      You have persistent memory about the current user. Relevant memories are injected into
      your context automatically — check the "What you know about this user" section above.

      For deeper recall, use memory_read:
      - Temporal queries → mode: temporal, with after/before dates
      - Specific facts → mode: semantic, with targeted query
      - Don't search for things already in your injected context

      Use memory_write when the user explicitly asks you to remember or forget something,
      or when they correct a previous fact. Routine facts are captured automatically —
      you don't need to store everything manually.

      If current conversation contradicts an injected memory, follow the conversation
      and use memory_write to update the memory.
      ```
    - Ensure no hardcoded user names remain — all references use "the user" / "the current user"

  **Must NOT do**:
  - Do NOT modify the system prompt for `/v1/chat/completions`
  - Do NOT make memory injection blocking beyond the 3s timeout
  - Do NOT hardcode user names in any prompt text

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Modifying core prompt assembly, wiring multiple systems together, careful about existing behavior
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 6 (with Tasks 10, 12, but has dependency on 9+10)
  - **Blocks**: Task 12
  - **Blocked By**: Tasks 9, 10

  **References**:

  **Pattern References**:
  - `orchestrator/daemon.py` — current system prompt usage, `stream_sse_chat()` function
  - `orchestrator/prompts.py` — `DAEMON_SYSTEM_PROMPT` constant, check for hardcoded names
  - `orchestrator/memory/injection.py` (Task 9) — `build_memory_context()`, `format_preferences_block()`, `assemble_system_prompt()`
  - `orchestrator/memory/store.py` (Task 5) — need `get_user_settings()` method

  **API/Type References**:
  - Spec N in this plan — prompt assembly order

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Memory context appears in system prompt
    Tool: Bash
    Preconditions: DB has active memories for default user
    Steps:
      1. Add debug logging to daemon.py that prints assembled system prompt length
      2. Send chat message via /chat endpoint
      3. Check logs for system prompt content
      4. Assert: prompt contains "What you know about this user" section
      5. Assert: prompt contains memory entries
      6. Remove debug logging
    Expected Result: Memory injected into system prompt
    Evidence: Log output

  Scenario: No hardcoded names in prompts
    Tool: Bash
    Steps:
      1. grep -ri "julian" orchestrator/prompts.py orchestrator/daemon.py
      2. Assert: no matches (or only in comments/non-prompt context)
    Expected Result: All prompts use neutral "the user" references
    Evidence: grep output (empty)

  Scenario: System prompt includes memory tool descriptions
    Tool: Bash
    Steps:
      1. python -c "from orchestrator.prompts import DAEMON_SYSTEM_PROMPT; print('memory_read' in DAEMON_SYSTEM_PROMPT)"
      2. Assert: prints "True"
    Expected Result: Memory tools documented in system prompt
    Evidence: Check output
  ```

  **Commit**: YES
  - Message: `feat(memory): integrate memory injection and preferences into system prompt`
  - Files: `orchestrator/daemon.py`, `orchestrator/prompts.py`, `orchestrator/memory/store.py` (if adding get_user_settings)

---

- [ ] 12. Memory Tools (memory_read + memory_write)

  **What to do**:
  - Create `orchestrator/memory/tools.py`:
    - `handle_memory_read(params, user_id, store, settings) -> str`:
      - `mode=semantic`: embed query, call `retrieve_memories()`, format results as readable text
      - `mode=temporal`: call `retrieve_temporal()` with after/before dates, format summaries
      - Apply category filter if provided
      - Return formatted string for Daemon to present
    - `handle_memory_write(params, user_id, store, settings) -> str`:
      - `action=create`: embed content, run through dedup pipeline, insert/skip accordingly
      - `action=update`: find memory by `memory_id`, update content, re-embed
      - `action=delete`: find memory by `memory_id`, set status=inactive (soft delete)
      - For create with `supersedes_content`: embed the supersedes text, find matching memory, supersede it
      - Return confirmation message
  - Register tools in the tool system:
    - Add `memory_read` and `memory_write` tool definitions to `orchestrator/tools/registry.py` (or wherever tools are registered)
    - Follow existing tool registration pattern (check `builtin.py` and `registry.py` for the pattern)
    - Tool definitions per Spec L (parameter names, types, descriptions)
  - Wire tool execution:
    - In the tool executor (likely `orchestrator/tools/executor.py`), add handlers that route `memory_read` → `handle_memory_read()` and `memory_write` → `handle_memory_write()`
    - Ensure `user_id` and `store` are available in the tool execution context

  **Must NOT do**:
  - Do NOT create a separate tool calling mechanism — use existing tool infrastructure
  - Do NOT bypass dedup for memory_write create action
  - Do NOT allow hard delete via memory_write tool — only soft delete

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Integrating with existing tool system, understanding current patterns, multiple operations
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 7 (with Tasks 13, 14)
  - **Blocks**: None
  - **Blocked By**: Tasks 5, 9

  **References**:

  **Pattern References**:
  - `orchestrator/tools/registry.py` — how tools are defined and registered (follow this exact pattern)
  - `orchestrator/tools/executor.py` — how tool calls are dispatched to handlers
  - `orchestrator/tools/builtin.py` — example tool implementations
  - `orchestrator/memory/retrieval.py` (Task 9) — `retrieve_memories()`, `retrieve_temporal()`
  - `orchestrator/memory/dedup.py` (Task 7) — `dedup_and_store()` for write-create
  - `orchestrator/memory/store.py` (Task 5) — all memory CRUD operations

  **API/Type References**:
  - Spec L in this plan — exact tool parameter definitions

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: memory_read semantic search returns results
    Tool: Bash
    Preconditions: Active memories in DB
    Steps:
      1. Simulate tool call: handle_memory_read({"query": "programming preferences", "mode": "semantic"}, ...)
      2. Assert: returns non-empty formatted string
      3. Assert: contains memory content text
    Expected Result: Relevant memories returned as readable text
    Evidence: Tool output

  Scenario: memory_write creates new memory
    Tool: Bash
    Steps:
      1. Simulate: handle_memory_write({"action": "create", "content": "User prefers dark mode", "category": "preference"}, ...)
      2. Assert: returns confirmation message
      3. Query DB: SELECT * FROM memories WHERE content LIKE '%dark mode%'
      4. Assert: memory exists with status='active', source_type='user_created'
    Expected Result: Memory created with correct metadata
    Evidence: DB query output

  Scenario: Tools registered in tool system
    Tool: Bash
    Steps:
      1. Check tool registry for memory_read and memory_write
      2. Assert: both tools are registered with correct parameter schemas
    Expected Result: Tools discoverable by Daemon
    Evidence: Registry inspection output
  ```

  **Commit**: YES
  - Message: `feat(memory): add memory_read and memory_write Daemon tools`
  - Files: `orchestrator/memory/tools.py`, `orchestrator/tools/registry.py`, `orchestrator/tools/executor.py`

---

- [ ] 13. API Routes (Conversations + Memories + System)

  **What to do**:
  - Create `orchestrator/routes/__init__.py`
  - Create `orchestrator/routes/conversations.py`:
    - `GET /conversations` — list with pagination (limit, offset, sort, order)
    - `GET /conversations/{id}` — full conversation with messages
    - `DELETE /conversations/{id}` — delete + cascade (local_only memories if pipeline='local')
    - `PATCH /conversations/{id}` — update title
  - Create `orchestrator/routes/memories.py`:
    - `GET /memories` — list with filters (status, category, search, limit, offset, sort, order)
    - `GET /memories/{id}` — single memory with metadata
    - `POST /memories` — manually create (embed, run dedup)
    - `PATCH /memories/{id}` — update content/status
    - `DELETE /memories/{id}` — soft delete; `?hard=true` for hard delete
    - `POST /memories/{id}/confirm` — confirm pending → active
    - `POST /memories/{id}/reject` — reject pending
    - `GET /memories/export` — export all as JSON (no embeddings)
    - `POST /memories/import` — import from JSON, mode=merge|replace
    - `POST /memories/reembed` — trigger re-embedding job
  - Create `orchestrator/routes/system.py`:
    - `GET /status` — DB health (pool size, available), Redis health (ping), memory count, queue depth
  - Register all routes in `orchestrator/main.py` using FastAPI APIRouter with appropriate prefixes
  - All endpoints use `get_app_state(request)` dependency for DB access
  - All endpoints use `DEFAULT_USER_ID` (single-user mode) — no auth
  - Response models: use Pydantic models for request/response validation
  - Error handling: 404 for not found, 400 for invalid params, 500 with logging for unexpected errors

  **Must NOT do**:
  - Do NOT add authentication middleware — single-user mode
  - Do NOT expose embedding vectors in API responses (except for debugging)
  - Do NOT add WebSocket endpoints
  - Do NOT break existing routes in main.py

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multiple endpoints with validation, pagination, error handling, import/export logic
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 7 (with Tasks 12, 14)
  - **Blocks**: None
  - **Blocked By**: Task 5

  **References**:

  **Pattern References**:
  - `orchestrator/main.py` — existing route registration pattern
  - `orchestrator/models.py` — existing Pydantic model patterns
  - `orchestrator/memory/store.py` (Task 5) — all CRUD methods to call from routes
  - `orchestrator/memory/dedup.py` (Task 7) — for POST /memories create flow
  - `orchestrator/memory/embedding.py` (Task 4) — for re-embed and create flows
  - `orchestrator/db.py` (Task 2) — `get_app_state()` dependency

  **API/Type References**:
  - Spec M in this plan — complete endpoint contract definitions

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: CRUD lifecycle via API
    Tool: Bash
    Preconditions: Services running, DB migrated
    Steps:
      1. POST /memories {"content": "Test memory", "category": "fact"} → Assert 201, returns id
      2. GET /memories/{id} → Assert 200, content matches
      3. PATCH /memories/{id} {"content": "Updated memory"} → Assert 200
      4. GET /memories/{id} → Assert content is "Updated memory"
      5. DELETE /memories/{id} → Assert 200
      6. GET /memories/{id} → Assert status is "inactive" (soft delete)
    Expected Result: Full lifecycle works via HTTP
    Evidence: curl responses

  Scenario: Export and import round-trip
    Tool: Bash
    Steps:
      1. Create 3 memories via API
      2. GET /memories/export → save response as export.json
      3. DELETE all memories (hard delete)
      4. POST /memories/import with export.json, mode=replace
      5. GET /memories → Assert 3 memories present
    Expected Result: Round-trip preserves all memories
    Evidence: Export/import responses

  Scenario: Conversations list with pagination
    Tool: Bash
    Steps:
      1. Ensure 5+ conversations exist (from previous tasks)
      2. GET /conversations?limit=2&offset=0 → Assert 2 results
      3. GET /conversations?limit=2&offset=2 → Assert 2 results (different ones)
      4. Assert total count is accurate
    Expected Result: Pagination works correctly
    Evidence: curl responses

  Scenario: Status endpoint reports health
    Tool: Bash
    Steps:
      1. GET /status → Assert 200
      2. Assert response contains db_healthy, redis_healthy, memory_count fields
    Expected Result: System status reported
    Evidence: Status response
  ```

  **Commit**: YES
  - Message: `feat(api): add conversation, memory, and system status endpoints`
  - Files: `orchestrator/routes/__init__.py`, `orchestrator/routes/conversations.py`, `orchestrator/routes/memories.py`, `orchestrator/routes/system.py`, `orchestrator/main.py` (route registration)

---

- [ ] 14. User Preferences & Settings

  **What to do**:
  - Create `orchestrator/routes/users.py`:
    - `GET /users/me/settings` — returns current user settings JSONB
    - `PATCH /users/me/settings` — partial merge update (only provided keys updated)
    - `GET /users/me/settings/presets` — returns available personality presets with labels + descriptions
  - Add constants to `orchestrator/memory/injection.py` (or a new `orchestrator/memory/preferences.py`):
    - `PERSONALITY_PRESETS` dict — maps preset ID to instruction text (Spec J)
    - `CHARACTERISTIC_MODIFIERS` dict — maps axis → value → modifier text (Spec K)
  - The `format_preferences_block()` function (from Task 9) should already exist — verify it uses these constants
  - Wire user settings route into `main.py`
  - Settings structure in JSONB:
    ```json
    {
      "preferences": {
        "personality": "default",
        "custom_instructions": "",
        "characteristics": {
          "warmth": "default",
          "enthusiasm": "default",
          "emoji": "default",
          "formatting": "default"
        }
      }
    }
    ```
  - PATCH endpoint does deep merge: only provided keys are updated, unset keys retain current value
  - Custom instructions max 2000 characters — validate at API level

  **Must NOT do**:
  - Do NOT add per-model prompt translation
  - Do NOT add "creative" personality presets (no "quirky", "cynical", etc.)
  - Do NOT store presets in database — they're code constants

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Straightforward CRUD endpoints + constant definitions
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 7 (with Tasks 12, 13)
  - **Blocks**: None
  - **Blocked By**: Task 9

  **References**:

  **Pattern References**:
  - Spec J in this plan — personality preset texts (verbatim)
  - Spec K in this plan — characteristic modifier texts (verbatim)
  - `orchestrator/memory/injection.py` (Task 9) — `format_preferences_block()` function
  - `orchestrator/routes/memories.py` (Task 13) — similar CRUD route patterns

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Set and retrieve personality preset
    Tool: Bash
    Steps:
      1. PATCH /users/me/settings {"preferences": {"personality": "efficient"}}
      2. Assert: 200
      3. GET /users/me/settings
      4. Assert: personality is "efficient"
    Expected Result: Preference persisted and retrievable
    Evidence: API responses

  Scenario: Partial merge preserves unset fields
    Tool: Bash
    Steps:
      1. PATCH /users/me/settings {"preferences": {"personality": "technical"}}
      2. PATCH /users/me/settings {"preferences": {"characteristics": {"emoji": "less"}}}
      3. GET /users/me/settings
      4. Assert: personality is still "technical" (not reset to default)
      5. Assert: emoji is "less"
    Expected Result: Only provided keys updated
    Evidence: Settings response

  Scenario: List presets returns all options
    Tool: Bash
    Steps:
      1. GET /users/me/settings/presets
      2. Assert: returns 8 presets
      3. Assert: each has id, label, description
      4. Assert: includes "default", "efficient", "technical"
    Expected Result: All presets listed with descriptions
    Evidence: Presets response

  Scenario: Preferences affect system prompt
    Tool: Bash
    Steps:
      1. Set personality to "efficient", emoji to "less"
      2. Send chat message, inspect system prompt (via logging)
      3. Assert: prompt contains "Be extremely concise"
      4. Assert: prompt contains "Never use emoji"
    Expected Result: Preferences injected into system prompt
    Evidence: System prompt log
  ```

  **Commit**: YES
  - Message: `feat(preferences): add personality presets, characteristics, and settings endpoints`
  - Files: `orchestrator/routes/users.py`, `orchestrator/memory/injection.py` (or `preferences.py`), `orchestrator/main.py`

---

- [ ] 15. Garbage Collection + Bootstrap + Prompts Cleanup

  **What to do**:
  - Create `orchestrator/memory/garbage.py`:
    - `run_garbage_collection(store, settings)`:
      - Delete `inactive` memories older than `gc_inactive_retention_days` (default 90)
      - Delete `rejected` memories older than `gc_rejected_retention_days` (default 30)
      - Delete `pending` memories older than `gc_pending_retention_days` (default 30)
      - Log counts of deleted memories
    - Uses hard delete (actual row removal for GC'd memories)
  - Wire GC into worker:
    - Add `garbage_collect` as a cron job in `WorkerSettings`: `cron(garbage_collect, weekday='sun', hour=3, minute=0)`
  - Create bootstrap memories file `scripts/bootstrap_memories.json`:
    - 10-15 known facts about the project from the codebase:
      - "Building Daemon, a multi-agent AI assistant with memory persistence"
      - "Uses Python 3.11+ with FastAPI, litellm, and asyncpg"
      - "Package manager is uv with pyproject.toml"
      - "Frontend is Next.js"
      - "Database is PostgreSQL 16 with pgvector extension"
      - etc.
    - Each with `category`, `confidence: 1.0`, `source_type: "bootstrapped"`
  - Run seed with bootstrap: `uv run python scripts/seed.py --memories scripts/bootstrap_memories.json`
  - After seeding, trigger embedding generation for bootstrapped memories (call embed + store)
  - Update `orchestrator/prompts.py`:
    - Ensure `DAEMON_SYSTEM_PROMPT` uses "the current user" / "the user" (no hardcoded names)
    - Audit all prompt strings for hardcoded name references
    - Check subagent prompts in `orchestrator/subagents/` for same issue

  **Must NOT do**:
  - Do NOT soft-delete during GC — use hard delete (these are already soft-deleted/rejected)
  - Do NOT bootstrap memories that are speculative — only facts from documentation/code
  - Do NOT add hardcoded user names anywhere

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Simple GC logic, JSON file creation, grep + fix for naming
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 8 (alone)
  - **Blocks**: Task 16
  - **Blocked By**: Task 8

  **References**:

  **Pattern References**:
  - `orchestrator/worker.py` (Task 8) — add GC cron job to WorkerSettings
  - `orchestrator/memory/store.py` (Task 5) — delete operations for GC
  - `orchestrator/prompts.py` — audit for hardcoded names
  - `orchestrator/subagents/*.py` — audit for hardcoded names
  - `scripts/seed.py` (Task 3) — extend to handle bootstrap memories with embeddings

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: GC deletes old inactive memories
    Tool: Bash
    Steps:
      1. Insert inactive memory with created_at = 100 days ago
      2. Insert inactive memory with created_at = 10 days ago
      3. Run garbage_collection()
      4. Assert: 100-day-old memory is gone (hard deleted)
      5. Assert: 10-day-old memory still exists
    Expected Result: Only memories past retention threshold deleted
    Evidence: DB query before/after

  Scenario: Bootstrap memories seeded with embeddings
    Tool: Bash
    Steps:
      1. Run: uv run python scripts/seed.py --memories scripts/bootstrap_memories.json
      2. Query: SELECT count(*) FROM memories WHERE source_type = 'bootstrapped'
      3. Assert: count >= 10
      4. Query: SELECT count(*) FROM memories WHERE source_type = 'bootstrapped' AND embedding IS NOT NULL
      5. Assert: count matches total (all have embeddings)
    Expected Result: Bootstrap memories exist with embeddings
    Evidence: DB counts

  Scenario: No hardcoded names in prompts
    Tool: Bash
    Steps:
      1. grep -ri "julian" orchestrator/
      2. Assert: no matches in .py files (or only in non-prompt context like comments)
    Expected Result: All user references are neutral
    Evidence: grep output
  ```

  **Commit**: YES
  - Message: `feat(memory): add garbage collection, bootstrap memories, and fix prompt naming`
  - Files: `orchestrator/memory/garbage.py`, `scripts/bootstrap_memories.json`, `orchestrator/worker.py`, `orchestrator/prompts.py`

---

- [ ] 16. Automated Tests

  **What to do**:
  - Create test files in `tests/` directory (or `tests/memory/` subdirectory):
    - `tests/memory/__init__.py`
    - `tests/memory/test_encryption.py` — encrypt/decrypt round-trip, empty key handling
    - `tests/memory/test_embedding.py` — mock OpenAI API, verify vector dimensions, batch handling
    - `tests/memory/test_store.py` — full CRUD lifecycle (requires test DB):
      - Conversation create/get/list/delete
      - Message insert/get
      - Memory insert/search/update/delete/supersede
      - Encryption at store layer
      - Vector similarity search accuracy
    - `tests/memory/test_extraction.py` — mock LLM response, verify parsing, handle invalid JSON
    - `tests/memory/test_dedup.py` — threshold logic with mock embeddings:
      - Near-duplicate (>0.92) → skip
      - Related same category (0.75-0.92) → update
      - Related with supersedes → supersede
      - Unrelated (<0.75) → insert
    - `tests/memory/test_injection.py` — format verification, token budget, preferences formatting
    - `tests/memory/test_integration.py` — end-to-end pipeline:
      - Messages → extraction → dedup → storage → retrieval → injection
      - Requires running DB + mocked LLM
  - Use `pytest-asyncio` for async test functions
  - Use fixtures for DB setup/teardown:
    - Create test database or use transactions that rollback
    - Fixture provides `store` instance with test encryption
  - Mock external APIs:
    - Mock OpenAI embedding API (return deterministic vectors)
    - Mock OpenRouter/litellm extraction calls (return known JSON)
  - Run with: `uv run pytest tests/memory/ -v`
  - Verify existing tests still pass: `uv run pytest` (full suite)

  **Must NOT do**:
  - Do NOT test against production database
  - Do NOT make tests depend on real API keys (mock all external calls)
  - Do NOT break existing tests
  - Do NOT add test infrastructure (already exists — pytest + pytest-asyncio)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multiple test files, fixtures, mocking, async testing, integration test
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (final task)
  - **Parallel Group**: Wave 9 (alone)
  - **Blocks**: None
  - **Blocked By**: All previous tasks

  **References**:

  **Pattern References**:
  - `tests/` — existing test structure and patterns
  - All `orchestrator/memory/*.py` modules — the code being tested
  - `pyproject.toml` — pytest configuration

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: All memory tests pass
    Tool: Bash
    Steps:
      1. uv run pytest tests/memory/ -v
      2. Assert: exit code 0
      3. Assert: all tests pass (no failures, no errors)
    Expected Result: Full test suite green
    Evidence: pytest output

  Scenario: Existing tests still pass
    Tool: Bash
    Steps:
      1. uv run pytest -v
      2. Assert: exit code 0
      3. Assert: no regressions in existing tests
    Expected Result: No regressions
    Evidence: pytest output

  Scenario: Tests don't require real API keys
    Tool: Bash
    Steps:
      1. Unset OPENAI_API_KEY and OPENROUTER_API_KEY
      2. uv run pytest tests/memory/ -v -k "not integration"
      3. Assert: all unit tests pass without API keys
    Expected Result: Unit tests are self-contained
    Evidence: pytest output
  ```

  **Commit**: YES
  - Message: `test(memory): add comprehensive test suite for memory layer`
  - Files: `tests/memory/*.py`

---

## Commit Strategy

| After Task | Message | Key Files | Verification |
|------------|---------|-----------|--------------|
| 1 | `feat(infra): add PostgreSQL, Redis, and worker services` | docker-compose.yml, .env.example, pyproject.toml | docker compose up -d → all healthy |
| 2 | `feat(db): add asyncpg AppState, lifespan, MemorySettings` | orchestrator/db.py, config.py, main.py | Python import test |
| 3 | `feat(db): add schema migrations, seed, key gen scripts` | migrations/*.sql, scripts/*.py | migrate.py → tables exist |
| 4 | `feat(memory): add encryption and embedding modules` | orchestrator/memory/{encryption,embedding}.py | Round-trip tests |
| 5 | `feat(memory): add MemoryStore CRUD and vector search` | orchestrator/memory/store.py | CRUD + search test |
| 6 | `feat(chat): persist messages and conversations` | orchestrator/main.py, daemon.py | Messages in DB after chat |
| 7 | `feat(memory): add extraction pipeline with dedup` | orchestrator/memory/{extraction,dedup}.py | Extraction test |
| 8 | `feat(worker): add ARQ worker with extraction and titles` | orchestrator/worker.py, memory/titles.py | Worker starts, title generated |
| 9 | `feat(memory): add retrieval, injection, prompt assembly` | orchestrator/memory/{retrieval,injection}.py | Ranked retrieval test |
| 10 | `feat(memory): add conversation summary generation` | orchestrator/memory/summarization.py | Summary with "Open:" |
| 11 | `feat(memory): integrate injection into system prompt` | orchestrator/daemon.py, prompts.py | Memory in prompt |
| 12 | `feat(memory): add memory_read and memory_write tools` | orchestrator/memory/tools.py, tools/*.py | Tool execution test |
| 13 | `feat(api): add conversation, memory, system endpoints` | orchestrator/routes/*.py, main.py | curl CRUD test |
| 14 | `feat(preferences): add presets, characteristics, settings` | orchestrator/routes/users.py, injection.py | Preferences in prompt |
| 15 | `feat(memory): add GC, bootstrap, fix prompt naming` | memory/garbage.py, scripts/*, prompts.py | GC + bootstrap test |
| 16 | `test(memory): add comprehensive test suite` | tests/memory/*.py | uv run pytest → green |

---

## Success Criteria

### Verification Commands

```bash
# All services healthy
docker compose up -d && docker compose ps
# Expected: 6 services (backend, worker, frontend, open-webui, postgres, redis) all healthy/running

# Migrations applied
uv run python scripts/migrate.py
# Expected: All migrations ✓

# Memory count after bootstrap
docker compose exec postgres psql -U daemon -d daemon -c "SELECT count(*) FROM memories WHERE status = 'active';"
# Expected: >= 10

# API health
curl -s http://localhost:8000/status | python -m json.tool
# Expected: {"db_healthy": true, "redis_healthy": true, "memory_count": N, ...}

# Memory CRUD via API
curl -s -X POST http://localhost:8000/memories -H "Content-Type: application/json" -d '{"content": "Test", "category": "fact"}'
# Expected: 201 with id

# Full test suite
uv run pytest -v
# Expected: All tests pass, 0 failures
```

### Final Checklist

- [ ] All 6 Docker services start with health checks passing
- [ ] Messages persisted to PostgreSQL after `/chat` conversations (encrypted)
- [ ] Facts extracted automatically after 2-minute idle
- [ ] Conversation titles generated after first exchange
- [ ] Memory injection visible in system prompt for new conversations
- [ ] `memory_read` tool returns relevant memories
- [ ] `memory_write` tool creates/updates/deletes memories
- [ ] Conversation summaries generated with "Open:" suffix
- [ ] Deduplication prevents same fact from being stored multiple times
- [ ] All API endpoints respond correctly (conversations, memories, settings, status)
- [ ] User preferences modify system prompt behavior
- [ ] Garbage collection deletes old inactive/rejected/pending memories
- [ ] Bootstrap memories seeded with embeddings
- [ ] No hardcoded user names in any prompt
- [ ] `/v1/chat/completions` endpoint unchanged and working
- [ ] `/chat` works without database (graceful degradation)
- [ ] All tests pass: `uv run pytest`
- [ ] Content encrypted at rest, embeddings in the clear
