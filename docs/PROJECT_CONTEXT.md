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
│           Vercel AI SDK + React 19                    │
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
- **Frontend**: Next.js 16 with Vercel AI SDK and React 19.
- **Memory**: PostgreSQL with `pgvector` for semantic search and Fernet encryption for content at rest.
- **Worker**: Redis + `arq` for background jobs (memory extraction, consolidation, dreaming).
- **Fetch**: Public HTTP(S) `direct` strategy retains upstream URL/redirect protections. Vendor-assisted fallbacks are disabled pending qualified bounded adapters; the `crawl4ai` docker service remains unused by `web_fetch`.

## Account Plans and Compute

The commercial model is Free / Pro / Power with a separate finite, usage-based
premium trial. Plans resolve into capabilities and bounded compute budgets;
provider qualification and workload model configuration are independent of plans.
Privacy and persistent memory belong to every plan. BYOK is a future funding
mode, not a subscription plan or an accounting bypass.

See [Account entitlements and compute policy](SUBSCRIPTION_ARCHITECTURE.md) for
the audited legacy inventory, migration behavior, and implementation status.
Commercial defaults live in `config/commercial.json`; provider qualification
lives in `config/inference_policy.json`. Video-credit balances remain separate.

## Implementation Status

### Phase 1: Cloud Orchestration ✅
- **SSE Streaming**: Typed events (`token`, `thinking`, `routing`, `tool_call`, `tool_result`, `final`, `error`, `done`).
- **Subagents**: Execution is subject to account capabilities, provider qualification and bounded adapters. Unbounded image/video/audio vendor paths are disabled.
- **Tools**: `generate_document` (deterministic CSV/DOCX generation).
- **Tools**: `web_search`, `http_request`, `calculate`, `get_time`, `notifications`, `reminders`, `memory_read`, `memory_write`.

### Phase 2: Memory System ✅
- **Storage**: PostgreSQL + pgvector; migration inventory is derived from `migrations/`. Account entitlements require the new commercial migration before deploying the replacement runtime.
- **Pipeline**: Account-funded extraction → deduplication → retrieval; denied embeddings use nullable vectors and lexical retrieval.
- **Encryption**: Fernet for messages and memories.
- **Background Jobs**: Extraction, summary, consolidation, dreaming.

### Video Credits and Pending Bounded Generation
- **Generation**: Disabled pending qualified, cost-reserving vendor adapters.
- **Credits**: Prepaid system with atomic debit/refund. Balance and transactions via `/video-credits`.
- **Studio**: Dedicated UI for video generation. Legacy image mode is retired; `/api/images/models`, `/api/images/generate`, and `/api/images/upload-reference` remain authenticated 410 routes until the hosted-identity image replacement lands.

### Frontend ✅
- **Chat**: Streaming via Vercel AI SDK `useChat`.
- **Voice**: Settings and controls remain; vendor execution and direct vendor tokens are disabled pending bounded adapters.
- **Settings**: Voice preferences, model selector, memory management.

### Phase 3: Local Pipeline (Blocked)
- Pre-router `/local` flag parsed but not wired to local inference routing.
- Inference code pending hardware (RTX 5090).

## Infrastructure

### Docker Compose Services (7 services)
1. `migrate`: One-shot migration runner.
2. `backend`: FastAPI app (port 8000).
3. `worker`: arq background job processor with durable `job_failures` audit rows.
4. `frontend`: Next.js 16 (port 3000).
5. `postgres`: pgvector/pg16 (port 5432).
6. `redis`: Redis 7 Alpine (port 6379).
7. `crawl4ai`: Web scraping service.

## Memory Layer

For detailed architecture, see [MEMORY_LAYER.md](../MEMORY_LAYER.md).

- **Embeddings**: Provider execution is denied until a qualified bounded adapter exists. Existing provider/model storage identities remain isolated; configured Voyage/OpenRouter/OpenAI fallback settings do not bypass qualification. Lexical retrieval and memory persistence remain available.
- **Dedup Thresholds**:
  - Merge: ≥ 0.90
  - Supersede (generic): ≥ 0.82
  - Supersede (same slot): ≥ 0.65
- **Retrieval**: Hybrid score (0.5 × vector + 0.3 × BM25 + 0.2 × recency/confidence/trust).

## Subagent Status

| Subagent | Status | Implementation |
|----------|--------|----------------|
| `@research` | Implemented | Brave Search + synthesis |
| `@image` | Unavailable | Unbounded vendor execution retired pending qualified bounded adapters |
| `@audio` | Unavailable | Vendor execution disabled pending qualified bounded adapters |
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
- **Model Assignments**: Workload configuration and approved inference routes are separate from account plans; see `SUBSCRIPTION_ARCHITECTURE.md`.
- **Migrations**: Repository files do not prove production application; run the migration gate before deployment.
