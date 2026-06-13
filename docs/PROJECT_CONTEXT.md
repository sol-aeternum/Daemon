# Project Context — Daemon

> **Verified-against-commit**: `3155d69fa1eb1939cf5c737018242fc119480d6c`
> **Last updated**: 2026-05-31
> **Upstream Sources**: `tests/benchmark_results/doc-alignment-regeneration/truth_set.md`, `docs/SOURCES_OF_TRUTH.md`, `docs/FEATURE_MATRIX.md`, `MEMORY_LAYER.md`, `orchestrator/config.py`, `docker-compose.yml`, `migrations/`

## What Daemon Is

Daemon is a multi-provider LLM orchestration platform with intelligent routing, persistent memory, and a subagent architecture. It provides a unified interface for multiple LLM providers (via OpenRouter), adding capabilities like tiered routing, persistent conversational memory via pgvector, specialized subagents, and a typed SSE event protocol for real-time streaming.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│           Next.js 16 Frontend (PWA)                  │
│           Vercel AI SDK 4 + React 19                  │
└──────────────────────┬──────────────────────────────┘
                       │ /api/chat (SSE bridge)
┌──────────────────────▼──────────────────────────────┐
│              FastAPI Backend                          │
│   orchestrator/  (routing, streaming, subagents,     │
│                   memory, tools, routes, worker)     │
│                                                      │
│  ┌─────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │ Router  │→ │ Provider │→ │ LiteLLM Streaming  │  │
│  │         │  │ Registry │  │ (SSE)              │  │
│  └─────────┘  └──────────┘  └────────────────────┘  │
│       │                                              │
│  ┌────▼────────────┐  ┌──────────────────────────┐  │
│  │ Memory Layer    │  │ Subagent Orchestrator    │  │
│  │ (pgvector)      │  │ @research @image @audio  │  │

│  └─────────────────┘  └──────────────────────────┘  │
└──────────────────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│               LLM Providers                          │
│  OpenRouter · xAI · fal.ai · ElevenLabs · Brave      │
└─────────────────────────────────────────────────────┘
```

- **Backend**: FastAPI (Python 3.11+) orchestrates LLM calls, memory, and subagents.
- **Frontend**: Next.js 16 with Vercel AI SDK 4 and React 19.
- **Memory**: PostgreSQL with `pgvector` for semantic search and Fernet encryption for content at rest.
- **Worker**: Redis + `arq` for background jobs (memory extraction, consolidation, dreaming).
- **Fetch**: `crawl4ai` service for robust web scraping.

## Tier System

Daemon uses a tier-based model configuration system. Specific model assignments are env-var configurable in `orchestrator/config.py`.

| Tier | Price | Orchestrator Model (Default) | Subagents | Video |
|------|-------|------------------------------|-----------|-------|
| **Free** | $0/mo | `kimi-k2.5` | None | Disabled |
| **Starter** | $9/mo | `kimi-k2.5` | Sonnet 3.5, Gemini Flash | Enabled (fal) |
| **Pro** | $19/mo | `kimi-k2.5` | Sonnet 3.5, Gemini Flash | Enabled (fal) |
| **Max** | $29/mo | `claude-opus-4.6` | Sonnet 3.5, Gemini Flash | Enabled (fal) |
| **BYOK** | $9/mo | `kimi-k2.5` | User-configured | Enabled (fal) |

*Note: Default video provider is `fal` (Kling) via config.py; runtime selection depends on `video_provider` propagation. BYOK users bypass credits using their own keys.*

## Implementation Status

### Phase 1: Cloud Orchestration ✅
- **SSE Streaming**: Typed events (`token`, `thinking`, `routing`, `tool_call`, `tool_result`, `final`, `error`, `done`).
- **Subagents**: `@research` (Brave), `@image` (OpenRouter/Gemini for images; xAI, fal for video), `@audio` (ElevenLabs).
- **Tools**: `generate_document` (deterministic CSV/DOCX generation).
- **Tools**: `web_search`, `http_request`, `calculate`, `get_time`, `notifications`, `reminders`, `memory_read`, `memory_write`.

### Phase 2: Memory System ✅
<<<<<<< HEAD
- **Storage**: PostgreSQL + pgvector with 36 migrations applied (latest: `036_memory_content_hash.sql`).
=======
- **Storage**: PostgreSQL + pgvector with 36 migrations applied (latest: `036_skill_consolidation_pending_status.sql`).
>>>>>>> f893638a (fix(worker): audit destructive skill deletes before applying)
- **Pipeline**: Extraction (GPT-4o-mini) → Embedding (Voyage 4) → Dedup → Retrieval (Hybrid).
- **Encryption**: Fernet for messages and memories.
- **Background Jobs**: Extraction, summary, consolidation, dreaming.

### Video Generation + Credits ✅
- **Providers**: `fal` (Kling) as default; `xai` (Imagine) also supported.
- **Credits**: Prepaid system with atomic debit/refund. Balance and transactions via `/video-credits`.
- **Studio**: Dedicated UI for video generation. Legacy image mode is retired; `/api/images/models`, `/api/images/generate`, and `/api/images/upload-reference` remain authenticated 410 routes until the hosted-identity image replacement lands.

### Frontend ✅
- **Chat**: Streaming via Vercel AI SDK `useChat`.
- **Voice**: ElevenLabs TTS/STT with push-to-talk.
- **Settings**: Voice preferences, model selector, memory management.

### Phase 3: Local Pipeline (Blocked)
- Pre-router `/local` flag parsed but not wired to local inference routing.
- Inference code pending hardware (RTX 5090).

## Infrastructure

### Docker Compose Services (7 services)
1. `migrate`: One-shot migration runner.
2. `backend`: FastAPI app (port 8000).
3. `worker`: arq background job processor.
4. `frontend`: Next.js 16 (port 3000).
5. `postgres`: pgvector/pg16 (port 5432).
6. `redis`: Redis 7 Alpine (port 6379).
7. `crawl4ai`: Web scraping service.

## Memory Layer

For detailed architecture, see [MEMORY_LAYER.md](../MEMORY_LAYER.md).

- **Embeddings**: `voyage-4-large` (1024d) for documents, `voyage-4-lite` (1024d) for queries.
- **Dedup Thresholds**:
  - Merge: ≥ 0.90
  - Supersede (generic): ≥ 0.82
  - Supersede (same slot): ≥ 0.65
- **Retrieval**: Hybrid score (0.5 × vector + 0.3 × BM25 + 0.2 × recency/confidence/trust).

## Subagent Status

| Subagent | Status | Implementation |
|----------|--------|----------------|
| `@research` | Implemented | Brave Search + synthesis |
| `@image` | Implemented | OpenRouter/Gemini image subagent remains; Studio image API is retired; xAI/fal video remains |
| `@audio` | Implemented | ElevenLabs SFX |
| `generate_document` | Implemented | Deterministic CSV/DOCX generation via `generate_document` tool |
| `@code` | **Reserved** | Not implemented |
| `@reader` | **Reserved** | Not implemented |

## Key API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Simple health check |
| `/status` | GET | Detailed system status (DB, Redis, Memory) |
| `/providers` | GET | List available LLM providers |
| `/skills` | GET/POST | Skill management CRUD |
| `/chat` | POST | Native SSE chat endpoint |
| `/v1/chat/completions` | POST | OpenAI-compatible completions |

## Caveats & Cleanup
- **Local Pipeline**: Blocked on hardware (RTX 5090); cloud pipeline runs independently.
- **Linter Scope**: `check_doc_freshness.py` gates high-confidence structured facts only.
- **Model Assignments**: Tier-to-model mappings are env-var configurable in `config.py`.
- **Migrations**: 36 migrations applied.
