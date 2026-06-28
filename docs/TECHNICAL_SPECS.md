# Technical Specifications

Verified-against-commit: 3155d69fa1eb1939cf5c737018242fc119480d6c
Last updated: 2026-05-31
Upstream Sources: orchestrator/config.py, migrations/, docker-compose.yml, MEMORY_LAYER.md, docs/SOURCES_OF_TRUTH.md, tests/benchmark_results/doc-alignment-regeneration/truth_set.md

## Daemon System Prompt (Actual)

Daemon is a personal AI assistant orchestration layer. The system prompt (v3) defines its identity, tool access, and subagent dispatch logic.

**Core Identity:**
- "I'm Daemon, a personal AI assistant."
- Honest about model specifics and capabilities.
- Concise, accurate, and pragmatic.

**Tools Available:**
- `get_time`, `calculate`, `web_search`, `web_fetch` (with transcript support), `http_request`, `notification_send`, `reminder_set`, `reminder_list`, `spawn_agent`, `spawn_multiple`, `generate_document`.

**Subagent Dispatch:**
- `@research`: News, fact-checking, market research.
- `@image`: Image and video generation (mode="video").
- `@audio`: Sound effects, audio clips.
- `@code`: Review, debugging (reserved).
- `@reader`: Document analysis (reserved).

**Memory:**
- Persistent across conversations.
- Injected context via "What you know about this user" section.
- Categories: `fact`, `preference`, `project`, `correction`, `summary`.
- Tools: `memory_read` (semantic/temporal), `memory_reflect` (synthesis), `memory_write`.

Full prompt in `orchestrator/prompts.py`.

---

## Tier Configuration

All model assignments are env-var overridable via `TIER_{NAME}_{SLOT}_MODEL`.

### Current Defaults (config.py)

| Tier | Orchestrator | Research/Code | Image | Reader | Embeddings | Video |
|------|--------------|---------------|-------|--------|------------|-------|
| **FREE** | Kimi K2.5 | _none_ | openrouter | _none_ | _none_ | Disabled |
| **STARTER** | Kimi K2.5 | Claude 3.5 Sonnet | Gemini 2.5 Flash | Gemini 2.0 Pro Exp | Voyage 4 Large | fal |
| **PRO** | Kimi K2.5 | Claude 3.5 Sonnet | Gemini 2.5 Flash | Gemini 2.0 Pro Exp | Voyage 4 Large | fal |
| **MAX** | Claude Opus 4.6 | Claude 3.5 Sonnet / Claude Opus 4.6 | Gemini 2.5 Flash | Gemini 2.0 Pro Exp | Voyage 4 Large | fal |
| **BYOK** | Kimi K2.5 | _none_ | openrouter | _none_ | _none_ | fal |

*Note: Image models are accessed via OpenRouter. Video provider `fal` uses Kling models.*

### Auto-Routing (within tiers)
- `auto_fast_model`: `openrouter/google/gemini-2.5-flash`
- `auto_reasoning_model`: `openrouter/moonshotai/kimi-k2.5`

---

## SSE Event Protocol

The `/chat` endpoint streams Server-Sent Events with typed frames:

| Event Type | Data Fields | Description |
|------------|-------------|-------------|
| `token` | `data.text` | Incremental text token (compat: `data.delta` accepted by bridge) |
| `thinking` | `data.content`, `id` | Model thinking/reasoning content |
| `routing` | `data.model`, `data.tier` | Model selection notification |
| `tool_call` | `data.name`, `data.arguments` | Tool invocation |
| `tool_result` | `data.name`, `data.result` | Tool response |
| `final` | `data.text`, `data.model`, `data.finish_reason`, `data.usage` (optional), `data.timing` (optional) | Completed response |
| `error` | `data.code`, `data.message` | Error |
| `done` | `data.ok` | Stream complete |

---

## Database Schema

PostgreSQL 16 with pgvector extension. 38 migrations in the `migrations/` directory.

### Core Tables
- **`users`**: Settings and profile data.
- **`conversations`**: Metadata, summary, and pinning status.
- **`messages`**: Content (Fernet-encrypted), reasoning, and token usage.
- **`memories`**: Encrypted content, 1024d Voyage embeddings, trust scores, and bitemporal validity.
- **`memory_extraction_log`**: History of fact extraction attempts.
- **`retrieval_log`**: History of memory retrieval for scoring analysis.
- **`entities`**: Extracted named entities for cross-referencing.
- **`dream_log`**: Logs for background consolidation and dreaming jobs.
- **`skill_projections`**: Mapping of skills to conversation context.

Latest migration: `037_worker_job_failures.sql`.
---

## Memory Pipeline

Daemon uses a multi-stage pipeline for durable fact management. See [MEMORY_LAYER.md](../MEMORY_LAYER.md) for full architecture.

### Extraction & Dedup
- **Extraction**: GPT-4o-mini extracts facts from conversation turns.
- **Embeddings**: `voyage-4-large` (1024d) for documents, `voyage-4-lite` (1024d) for queries.
- **Dedup Thresholds**:
  - Merge: ≥ 0.90
  - Supersede (generic): ≥ 0.82
  - Supersede (same slot): ≥ 0.65
  - Insert new: < 0.65

### Retrieval
Hybrid search combining:
- **Vector search**: pgvector cosine distance.
- **BM25 search**: Lexical rank on `content_tsv`.
- **Scoring**: `0.5 × vector_sim + 0.3 × bm25_normalized + 0.2 × recency × confidence × trust`.

---

## API Endpoints

| Category | Endpoints |
|----------|-----------|
| **Chat** | `/chat` (SSE), `/v1/chat/completions` (OpenAI), `/chat/completions` |
| **Models** | `/v1/models`, `/v1/catalog`, `/providers` |
| **Conversations** | `/conversations/{conversation_id}` (GET/PATCH/DELETE) |
| **Memories** | `/memories/{memory_id}` (GET/PATCH/DELETE), `/memories/export`, `/memories/import`, `/memories/reembed`, `/memories/consolidate`, `/memories/dream` |
| **Skills** | `/skills/{skill_id}` (GET/PUT/PATCH/DELETE), `/skills/upload`, `/skills/admin/sync` |
| **Audio** | `/tts`, `/stt`, `/audio/token`, `/audio/scribe-token`, `/sound-effects` |
| **Video** | `/video-credits/balance`, `/video-credits/estimate`, `/video-credits/transactions` |
| **Retired Image API** | `/api/images/models`, `/api/images/generate`, `/api/images/upload-reference`, `/api/images/{image_id}`, `/api/images/{image_id}/metadata` (authenticated 410) |
| **System** | `/status`, `/health`, `/generated-images/{filename}`, `/generated-audio/{filename}`, `/generated-files/{filename}` |

---

## Infrastructure

### Docker Compose (7 services)
- `backend`: FastAPI app (port 8000).
- `worker`: arq background job processor.
- `frontend`: Next.js 16 (port 3000).
- `postgres`: pgvector/pg16 (port 5432).
- `redis`: Redis 7 (port 6379).
- `crawl4ai`: Web scraping service.
- `migrate`: One-shot migration runner.

### Key Environment Variables
- `OPENROUTER_API_KEY`, `VOYAGE_API_KEY`, `XAI_API_KEY`, `FAL_KEY`, `BRAVE_API_KEY`, `ELEVENLABS_API_KEY`.
- `DATABASE_URL`, `REDIS_URL`, `DAEMON_ENCRYPTION_KEY`.
- `DAEMON_WORKER_FAILURE_ALERT_EMAIL` enables best-effort email alerts for critical worker failures.
- **EMBEDDING_DOCUMENT_MODEL**: voyage-4-large
- **EMBEDDING_QUERY_MODEL**: voyage-4-lite
- **EMBEDDING_DIMENSIONS**: 1024

---

## Local Pipeline (Phase 3)

**Status: Unimplemented.**
The `/local` flag is parsed by the pre-router, but all local inference code is pending hardware acquisition (RTX 5090). Current operations are 100% cloud-based.
