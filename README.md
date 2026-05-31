# Daemon

**Multi-provider LLM orchestration platform with intelligent routing, persistent memory, and subagent architecture.**

Daemon is an orchestration layer that sits between multiple LLM providers and a custom Next.js frontend, adding capabilities that no single provider offers: tiered cross-provider routing with failover, persistent conversational memory via pgvector, specialised subagents for task decomposition, and a typed SSE event surface for real-time streaming.

## Why This Exists

Commercial LLM products lock you into a single provider, a single model, and their memory implementation. Daemon inverts that: you own the orchestration, the memory, and the routing logic. Switch providers without losing conversation history. Route different query types to different models. Spawn specialised subagents for complex tasks. Run it locally, keep your data.

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
│  │                 │  │ @document @code @reader   │  │
│  └─────────────────┘  └──────────────────────────┘  │
└──────────────────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│               LLM Providers                          │
│  OpenRouter · Local Models (Phase 3, pending GPU)   │
└─────────────────────────────────────────────────────┘
```

### Key Design Decisions

**Tiered multi-provider routing.** LiteLLM abstracts provider differences. The tier system assigns models per pricing level via environment variables — no code changes to swap models. Default tier is Pro; Free tier is rate-limited with no subagents.

**Persistent memory via pgvector.** Conversations are embedded and stored in PostgreSQL with vector similarity search. The memory pipeline uses Voyage AI asymmetric embeddings (`voyage-4-large` for documents, `voyage-4-lite` for queries). See [MEMORY_LAYER.md](MEMORY_LAYER.md).

**Subagent spawning for task decomposition.** Complex requests are routed to specialised agents: `@research` (Brave Search), `@image` (image/video generation), `@audio` (ElevenLabs TTS/STT), `@code`, and `@reader`. Each may use a different model optimised for its task.

**Typed SSE streaming.** Real-time token streaming uses typed events (`token`, `thinking`, `routing`, `tool_call`, `tool_result`, `final`, `error`, `done`) rather than raw model output, enabling structured frontend rendering.

**Local pipeline blocked on hardware.** The `/local` pre-router flag is implemented, but all local inference code is pending RTX 5090 acquisition. The cloud pipeline runs independently.

## Project Structure

```
Daemon/
├── orchestrator/       # FastAPI backend (main app, routing, subagents,
│                       #   memory, tools, routes, worker)
│   ├── main.py         # FastAPI app + OpenAI-compatible + SSE endpoints
│   ├── daemon.py       # Core orchestration loop (stream_sse_chat)
│   ├── config.py       # Tier system + env-var model configuration
│   ├── prompts.py      # System prompt (v1)
│   ├── memory/         # Full memory pipeline (store, extraction, dedup,
│                       #   retrieval, injection, embedding, encryption)
│   ├── routes/         # API route modules
│   ├── agents/         # Subagent implementations
│   └── worker/         # arq background job processor
├── frontend/           # Next.js 16 web frontend (PWA)
│   ├── app/            # App router pages (/page.tsx, /studio/page.tsx)
│   ├── components/     # UI components
│   ├── hooks/          # React hooks (useChat wrappers, audio, events)
│   └── lib/events.ts   # Typed SSE event definitions
├── providers/          # Provider client implementations
├── migrations/         # PostgreSQL migrations
├── tests/              # Test suite (pytest + playwright)
├── scripts/             # Utility scripts
├── data/                # Runtime data (generated files, etc.)
├── .sisyphus/           # Agent workflow configuration
├── MEMORY_LAYER.md      # Memory system design document
├── QUICKSTART.md        # Quick setup guide
├── docker-compose.yml   # Full-stack deployment (6 long-running services)
└── Dockerfile           # Single-image backend build
```

Note: The top-level `backend/` directory contains only a Dockerfile for Docker builds. All backend source code lives under `orchestrator/`.

## Quick Start

**Prerequisites:** [uv](https://github.com/astral-sh/uv) installed.

```bash
# Local development (backend only, postgres/redis must be running)
uv run uvicorn orchestrator.main:app --reload --host 0.0.0.0 --port 8000

# Docker (full stack)
cp .env.example .env    # Configure providers
docker compose up --build
```

Verify: `curl http://localhost:8000/health`

Benchmarking runs from the host shell against localhost-exposed container services. See [QUICKSTART.md](QUICKSTART.md) for detailed setup.

## Capabilities

### Chat & Routing
- Native `/chat` endpoint with SSE streaming (typed events)
- OpenAI-compatible `/v1/chat/completions` and `/v1/models`
- Tier-based model routing with auto-classification (`fast` vs `reasoning`)
- Per-request model override

### Memory
- Encrypted conversation storage with pgvector similarity search
- Background fact extraction (GPT-4o-mini → embedding → dedup → store)
- Composite scoring retrieval (similarity × recency × confidence)
- Memory injection into system prompt per conversation
- `memory_read` / `memory_write` tools available to the orchestrator

### Subagents
- `@research` — Brave Search web search
- `@image` — Image generation (xAI) and video generation (xAI, fal.ai/Kling)
- `@audio` — ElevenLabs TTS/STT/sound effects
- `@document` — Document file generation
- `@code` — Code generation (experimental, not fully implemented)
- `@reader` — Document reading (experimental, not fully implemented)

### Frontend
- Next.js 16 PWA with streaming chat (Vercel AI SDK `useChat`)
- Conversation list with CRUD, search, pinning, rename
- Studio page (`/studio`) for image and video generation
- Voice I/O: ElevenLabs streaming TTS + STT with push-to-talk
- Settings panel: TTS voice/model/speed, STT language, memory management
- Rich inline rendering: images (lightbox + download), audio player, tool call blocks
- Error boundary for crash recovery

### API Routes

| Endpoint | Method | Description |
|---|---|---|
| `/v1/models` | GET | List available models (OpenAI-compatible) |
| `/v1/chat/completions` | POST | Chat completion, streaming and non-streaming |
| `/chat` | POST | Native Daemon chat with SSE streaming |
| `/conversations` | GET/POST | Conversation CRUD |
| `/conversations/{id}/messages` | GET/POST | Message history |
| `/memories` | GET/POST/DELETE | Memory management |
| `/skills` | GET/POST | Skills management (list, create, upload, update, delete) |
| `/users/settings` | GET/PUT | User preferences |
| `/video-credits` | GET/POST | Video credit balance and transactions |
| `/health` | GET | Health check |

## Status

Cloud pipeline fully operational: FastAPI backend, Next.js frontend, PostgreSQL+pgvector memory, Redis+arq worker queue, typed SSE, subagent system, Studio/video, Council deliberation system, Skills loader, Fetch service, tiered model routing, and voice I/O.

**Phase 3 (local pipeline)** is blocked on hardware (RTX 5090 acquisition). The `/local` flag is parsed but all local inference code is unimplemented.

Open WebUI integration is **legacy/deprecated** — the custom Next.js frontend is the primary interface.

## License

This repository does not currently declare a license.
