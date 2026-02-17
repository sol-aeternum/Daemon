# Technical Specifications

> Last updated: 2026-02-17

## Daemon System Prompt (Actual)

```
You are Daemon, a personal AI assistant orchestration layer running on
the Kimi K2.5 model via OpenRouter.

[Note: This hardcoded identity should be made dynamic or abstracted.
See OPEN_QUESTIONS.md #3.]

Tools available: get_time, calculate, web_search, http_request,
notification_send, reminder_set, reminder_list, spawn_agent,
spawn_multiple, memory_read, memory_write.

Subagent dispatch: @research (news, fact-checking, comparison),
@image (generation), @audio (sound FX), @code (review, debug),
@reader (document analysis).

Memory: persistent across conversations. Injected context via
"What you know about this user" section. Categories: fact, preference,
project, correction, summary. Uses memory_read for deeper recall,
memory_write for explicit storage. Routine facts captured automatically
via background extraction.
```

Full prompt in `orchestrator/prompts.py` (v1).

---

## Tier Configuration

All model assignments are env-var overridable. Format: `TIER_{NAME}_{SLOT}_MODEL`.

### Current Defaults (config.py)

```
FREE:
  orchestrator: openrouter/moonshotai/kimi-k2.5
  subagents: none

STARTER ($9/mo):
  orchestrator: openrouter/moonshotai/kimi-k2.5
  research: openrouter/anthropic/claude-sonnet-4.5
  code: openrouter/anthropic/claude-sonnet-4.5
  image: google/gemini-2.5-flash-image
  reader: openrouter/google/gemini-2.0-pro-exp
  embeddings: openrouter/openai/text-embedding-3-small

PRO ($19/mo) — default tier:
  [same as STARTER]

MAX ($29/mo):
  orchestrator: openrouter/anthropic/claude-opus-4.6
  research: openrouter/anthropic/claude-sonnet-4.5
  code: openrouter/anthropic/claude-opus-4.6
  image: google/gemini-3-pro-image-preview
  reader: openrouter/google/gemini-2.0-pro-exp
  embeddings: openrouter/openai/text-embedding-3-large

BYOK ($9/mo):
  orchestrator: openrouter/moonshotai/kimi-k2.5
  subagents: none (user configures)
```

### Auto-Routing (within tiers)

```python
auto_fast_model = "openrouter/google/gemini-2.5-flash"
auto_reasoning_model = "openrouter/moonshotai/kimi-k2.5"
```

Classification based on: message complexity, turn count, code block presence. Explicit user model selection overrides auto-routing.

---

## SSE Event Protocol

The `/chat` endpoint streams Server-Sent Events with typed frames:

| Event Type | Data Fields | Description |
|------------|-------------|-------------|
| `token` | `data.delta` (string) | Incremental text token |
| `thinking` | `data.content`, `id`, `request_id` | Model thinking/reasoning content |
| `routing` | `data.model`, `data.tier`, `data.reason` | Model selection notification |
| `tool_call` | `data.name`, `data.arguments`, `id` | Tool invocation |
| `tool_result` | `data.name`, `data.result`, `id` | Tool response |
| `final` | `data.message`, `data.usage`, `data.model` | Completed response |
| `error` | `data.code`, `data.message`, `data.retryable` | Error |
| `done` | `data.ok` | Stream complete |

Frontend SSE bridge (`/api/chat/route.ts`) translates these into Vercel AI SDK's `createDataStreamResponse` format, mapping `token` → text parts and everything else → data parts.

---

## Database Schema

PostgreSQL 16 with pgvector extension. 13 migrations in `/migrations/`.

### Core Tables

```sql
-- Users (single default user, multi-user ready)
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT UNIQUE NOT NULL,
    display_name TEXT,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Default user seeded: 00000000-0000-0000-0000-000000000001

-- Conversations
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    title TEXT DEFAULT 'New conversation',
    pipeline TEXT DEFAULT 'cloud',  -- 'cloud' | 'local'
    pinned BOOLEAN DEFAULT FALSE,
    title_locked BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'active',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Messages (content encrypted via Fernet)
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    role TEXT NOT NULL,  -- 'user' | 'assistant' | 'system'
    content TEXT NOT NULL,  -- encrypted
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    status TEXT DEFAULT 'active',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Memories (content encrypted, embedding plaintext for pgvector)
CREATE TABLE memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    content TEXT NOT NULL,  -- encrypted
    category TEXT DEFAULT 'fact',  -- fact | preference | project | correction | summary
    source_type TEXT DEFAULT 'extracted',  -- extracted | manual | tool
    embedding VECTOR(1536),
    embedding_model TEXT DEFAULT 'text-embedding-3-small',
    source_conversation_id UUID REFERENCES conversations(id),
    confidence FLOAT DEFAULT 1.0,
    status TEXT DEFAULT 'active',  -- active | pending | rejected | superseded
    local_only BOOLEAN DEFAULT FALSE,
    superseded_by UUID REFERENCES memories(id),
    last_accessed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Extraction Log
CREATE TABLE extraction_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id),
    input_snippet TEXT,  -- encrypted
    extracted_count INTEGER DEFAULT 0,
    model TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX ON memories USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON messages (conversation_id, created_at);
```

---

## Memory Pipeline

### Extraction (Background Job)

```
User sends message
    → Backend persists user + assistant messages
    → Enqueues `extract_memories` job (debounced)
    → Worker picks up job
    → GPT-4o-mini extracts facts from conversation text
    → Each fact gets embedded (text-embedding-3-small, 1536d)
    → Dedup engine compares against existing memories:
        - similarity ≥ 0.92 → merge (touch existing)
        - similarity ≥ 0.75 → supersede (create new, mark old)
        - similarity < 0.75 → insert new
    → Memories stored with status="pending"  ⚠️ BUG: should be "active"
```

### Retrieval (Pre-Response)

```
User sends message
    → Query embedded
    → pgvector similarity search (top 10 candidates, status="active")
    → Composite scoring:
        score = similarity × recency_boost × source_boost × confidence
        recency_boost = 1 / sqrt(days_since_accessed)
        source_boost = 1.1 for project/important categories
    → Top 5 returned
    → Formatted into "What you know about this user" context block
    → Injected into system prompt with token budget (1500 tokens default)
```

### Dedup Thresholds

| Similarity | Action |
|-----------|--------|
| ≥ 0.92 | Merge — touch existing memory (update last_accessed_at) |
| ≥ 0.75 | Supersede — create new, mark old as superseded |
| < 0.75 | New — insert as new memory |

---

## API Endpoints (Implemented)

### Chat
```
POST /chat                    → SSE streaming chat (primary endpoint)
POST /v1/chat/completions     → OpenAI-compatible chat completions
POST /chat/completions        → Redirect to /v1/chat/completions
```

### Models
```
GET  /v1/models               → List available models (88 via OpenRouter)
GET  /v1/catalog               → Curated model catalog with badges
```

### Conversations
```
GET    /conversations                    → List conversations (limit, offset)
POST   /conversations                    → Create conversation
GET    /conversations/{id}               → Get conversation with messages
PATCH  /conversations/{id}               → Update (title, pinned, title_locked, metadata)
DELETE /conversations/{id}               → Delete conversation + messages
GET    /conversations/{id}/messages      → Get messages (limit, offset)
```

### Memories
```
GET    /memories                         → List memories (status filter, limit)
POST   /memories                         → Create memory manually
PATCH  /memories/{id}                    → Update memory
POST   /memories/{id}/confirm            → Confirm/reject pending memory
DELETE /memories                         → Delete all (requires confirm=true)
POST   /memories/export                  → Export memories as JSON
POST   /memories/import                  → Import memories from JSON
POST   /memories/re-embed                → Re-embed all memories
```

### Users
```
GET    /users/settings                   → Get user settings
PATCH  /users/settings                   → Update user settings
```

### Audio
```
POST /tts                               → Text-to-speech (ElevenLabs)
POST /stt                               → Speech-to-text (ElevenLabs Scribe)
GET  /scribe-token                       → ElevenLabs Scribe auth token
```

### System
```
GET  /health                             → Health check
GET  /providers                          → List configured providers
```

---

## Frontend Architecture

### Route: `/api/chat/route.ts` (SSE Bridge)

Next.js API route that proxies between Vercel AI SDK's `createDataStreamResponse` and the backend's custom SSE format. Translates backend events into AI SDK data stream parts:

- `token` → `formatDataStreamPart("text", delta)`
- `thinking` → `formatDataStreamPart("data", [{ type: "thinking", ... }])`
- `routing` → `formatDataStreamPart("data", [{ type: "routing", ... }])`
- `tool_call` → `formatDataStreamPart("data", [{ type: "tool_call", ... }])`
- `tool_result` → `formatDataStreamPart("data", [{ type: "tool_result", ... }])`
- `final` (fallback) → `formatDataStreamPart("text", content)` only if no tokens were streamed

### State Management

- `useChat` (Vercel AI SDK) — message state, streaming, submission
- `useConversationHistory` — conversation CRUD via backend API, URL-based routing (`?id=`)
- `useLocalStorage` — TTS/STT settings persisted in browser
- `useAgentStatus` — derives agent status from SSE event stream
- Chat events parsed from `useChat`'s `data` array via `isChatEvent()` type guard

### Key Components

| Component | Purpose |
|-----------|---------|
| `ChatContent` | Main chat view (messages, input, events) |
| `ChatInputBar` | Textarea + model selector + mic + send |
| `ConversationList` | Sidebar with search, pin, rename, delete, settings |
| `ModelSelector` | Curated catalog + full model search |
| `ToolCallBlock` | Inline tool call/result rendering (images, audio, generic) |
| `ThinkingIndicator` | Collapsible thinking/reasoning display |
| `AgentStatusCard/List` | Subagent spawn/progress/completion indicators |
| `StreamingTtsMessage` | Auto-TTS on streaming assistant messages |
| `MicButton` | Push-to-talk STT via ElevenLabs Scribe |

---

## Infrastructure

### Docker Compose (Target: 5 services)

```yaml
services:
  backend:     # FastAPI (port 8000)
  worker:      # arq background jobs
  frontend:    # Next.js 16 (port 3000)
  postgres:    # pgvector/pgvector:pg16
  redis:       # Redis 7 Alpine
```

**Remove:** Open WebUI service (port 8080) — dead since Next.js pivot.

### Required Environment Variables

```
# LLM Provider
OPENROUTER_API_KEY=

# Embeddings (separate from OpenRouter — see CURRENT_ISSUES.md #8)
OPENAI_API_KEY=

# External Services
BRAVE_API_KEY=
ELEVENLABS_API_KEY=

# Infrastructure
DATABASE_URL=postgresql://user:pass@postgres:5432/daemon
REDIS_URL=redis://redis:6379

# Security
DAEMON_API_KEY=
DAEMON_ENCRYPTION_KEY=

# Optional
NEXT_PUBLIC_API_URL=http://backend:8000
DAEMON_INTERNAL_API_URL=http://backend:8000
```

---

## Local Pipeline (Phase 3 — Unimplemented)

### Pre-Router (Implemented)

```python
def route_message(message: str) -> tuple[str, str]:
    local_triggers = ["/local", "~local", "/private"]
    for trigger in local_triggers:
        if trigger in message.lower():
            cleaned = message.replace(trigger, "").strip()
            return ("local", cleaned)
    return ("cloud", message)
```

### VRAM Budget (32GB 5090)

| Component | VRAM | Disk |
|-----------|------|------|
| Qwen 72B Q5_K_M | ~22GB | ~55GB |
| FLUX Dev | ~12GB | ~25GB |
| Concurrent headroom | Yes | — |
| SearXNG | — | ~1GB |
| **Total** | ~34GB (slight over-sub OK) | ~90GB |

### Performance Targets

| Metric | Target | Status |
|--------|--------|--------|
| Cloud response start | < 2s | ✅ Met |
| Local first load | < 15s | Phase 3 |
| Local warm response | < 3s | Phase 3 |
| Memory retrieval | < 100ms | Phase 2 (operational) |
| Image gen (cloud) | < 10s | ✅ Met |
| Image gen (local warm) | < 6s | Phase 3 |
