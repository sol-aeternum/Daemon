# Daemon

**Your persistent agent workspace — cloud-first, device-extended, task-centred, permission-bound.**

Daemon is being built as one persistent assistant that carries your work forward wherever you access it. The hosted workspace is its default working environment; connected devices and services will extend what it can reach and do, within the permissions you grant.

**The work belongs to you—not to a device, chat thread, model, or execution environment.**

This is the product direction. Today's repository provides the hosted assistant foundation: a web/PWA client, account identity, saved conversations, memory, document generation, bounded tools (research dispatch currently disabled) and bounded provider routing. General durable task execution, reusable owned workspace resources and resource-scoped device companions remain in development planning. See [current status](#status) before treating the vision as shipped capability.

## Why This Exists

Work should not have to restart when you close a chat, change devices, or use a different model. Daemon's goal is to keep discussion, execution, files and follow-up connected, so you can ask for an outcome and return to the same work later.

Phone-only use is a complete intended endpoint: no paired computer or server administration should be required. Connecting a computer expands capability through explicit permission; signing in alone does not grant access to its files or applications.

## Product Model

The [vision](docs/DAEMON_VISION.md) and [approved product decisions](docs/DAEMON_VISION_DECISIONS.md) establish these behaviours to build toward:

- **One personal workspace, optional projects.** Start immediately and organise when useful. Relevant context shares by default; restricted projects can use permitted workspace context while keeping their own information inside unless explicitly shared (DEC01–DEC03).
- **Continuity for every accepted request.** Ordinary questions, research and supported tools continue after client closure within their permissions and budget. Acceptance means durably saved and tracked, not guaranteed success. Material ambiguity leads to useful independent progress, then a saved question or waiting state (DEC04–DEC06).
- **Reusable resources and attributable memory.** Uploaded documents become retained workspace resources under the applicable policy. Useful document-derived memory carries provenance and source restrictions, distinguishes document claims from user facts, and supports inspection, correction and removal (DEC07–DEC08).
- **Visible, bounded autonomy.** Capacity interruptions preserve progress and notify the user. Automatic resumption rechecks permissions, inputs and task currency, with a control to prevent it for the halted task. Extra spending still requires authority (DEC09).
- **Optional device extensions.** Supported companions will expose only authorised resources and operations. Device availability and platform constraints remain real boundaries (V04–V05).

These are product commitments, not a claim that the corresponding lifecycle, project controls or companion APIs already exist. Proposed implementation choices remain labelled separately in the vision and design documents.

## Architecture

Product direction: [Daemon vision](docs/DAEMON_VISION.md) · [Approved product decisions](docs/DAEMON_VISION_DECISIONS.md). The architecture below is the current implementation, which is separate from that direction; see [Feature Matrix](docs/FEATURE_MATRIX.md) for per-surface status.

```
Next.js 16 PWA · React 19 · Vercel AI SDK
                    │ /api/chat (SSE bridge)
                    ▼
FastAPI / orchestrator
  ├── Router → Provider Registry → LiteLLM streaming
  │                              ↕ typed tool events
  ├── Shared Tool Registry → Subagent framework / Council
  ├── Memory retrieval → Prompt context
  └── Policy + Entitlements → Qualified, funded execution
                    │
       ┌────────────┼─────────────┬──────────────────┐
       ▼            ▼             ▼                  ▼
  Qualified     PostgreSQL    Redis + arq       Owned artifact
  providers     + pgvector    maintenance      filesystem

Registered subagent names do not imply enabled dispatch;
see the execution boundaries below.
```

### Request Path

1. **Authenticate and rate limit.** Device/session auth, then Redis windows per session, per user and per client IP before any LLM-backed work.
2. **Classify the turn.** `orchestrator/model_router.py` classifies the message (`trivial` / `standard` / `complex` from length, turn count, code blocks and complexity signals) into a `fast` or `reasoning` tier, unless the request carries an explicit model override.
3. **Assemble context.** The system prompt, runtime date/time, user preferences and retrieved memory are assembled per request; the per-request tool registry is built with authenticated ownership context.
4. **Qualify and fund the route.** `orchestrator/compute_runtime.py` resolves the account's entitlements, selects only approved, unexpired, operator-reviewed routes whose pinned transport and price ceiling satisfy the request, reserves worst-case cost atomically, and dispatches with that route's pinned endpoint and transport flags.
5. **Stream typed events.** Tokens, reasoning, routing, tool calls and results are emitted as typed SSE frames; tool calls are executed in-loop against the shared registry.
6. **Persist and follow up.** Messages and tool activity are stored in PostgreSQL; generated artifacts use per-user filesystem namespaces. Eligible turns and scheduled maintenance drive background extraction, titling, summary and consolidation jobs.

### Key Design Decisions

**Capability-aware routing with task classification.** The router picks a `fast` or `reasoning` workload from the turn's complexity signals, honours a per-request model override, and reports the decision as a `routing` event. Delegated and background work (extraction, titling, summarisation, council roles) runs on its own configured models, all subject to the same qualification and account limits.

**Account entitlements and bounded compute.** Free, Pro, and Power resolve into capabilities and compute limits, with a separate usage-based premium trial. Privacy qualification is independent of plan and price. Every LLM call reserves worst-case cost against an atomic account ceiling and settles on reported usage; explicit model choices are never silently downgraded, and a request that fits no funded qualified route fails closed. See [the migration and architecture guide](docs/SUBSCRIPTION_ARCHITECTURE.md) for provider approval, configuration, and deployment requirements.

**Policy-qualified provider registry.** The provider registry holds configured providers (OpenRouter inference today), but a model being listed is not approval. `config/inference_policy.json` is the gate: pinned endpoint, pinned transport flags, price ceilings, dated approval and dated operator review. Unqualified, expired or unreviewed routes are unusable, and no default route is set as shipped.

**Shared tool registry.** One registry per request exposes `get_time`, `calculate`, `web_fetch`, `http_request`, notifications, reminders, `spawn_agent`/`spawn_multiple`, skill management and `generate_document`, plus the memory tools when memory is enabled. Unqualified or unbounded paths are omitted from registration or denied at execution; registration alone is not availability. Council members receive a separate read-only registry (time, math, fetch), so deliberation can gather context without acting. Advisor escalation is not registered as a tool and has no client affordance; advisor events and stored-trace shapes remain supported.

**Subagent spawning and delegation for task decomposition.** The framework defines `spawn_agent` for specialised work and `spawn_multiple` for parallel delegation, with session IDs for continuation. The retained `@research` implementation combines parallel Brave searches with report synthesis; results integrate with typed tool activity. **Current registered spawn tools return `capacity_unavailable` before dispatch**, including research, until independently priced side effects have qualified bounded adapters. Media/voice routes are also disabled pending bounded integration. `@code` and `@reader` remain reserved rather than implemented. These execution boundaries preserve the architecture without promising that every registered agent can currently run.

**Council deliberation.** `/council` and the welcome-screen shortcut start a multi-perspective deliberation: roles, per-role models and timeouts from a roster, round one positions, round two rebuttals and an audit round. Progress and results stream as typed `council_progress`, `council_output` and `council_done` SSE events, with per-round confidence. Council members use the read-only tool registry, so they cannot notify, schedule reminders, spawn agents, write memory or generate files.

**Persistent memory via pgvector.** Conversations and extracted memory are stored in PostgreSQL with encrypted content. Memory vectors support semantic retrieval, with distinct document/query embedding identities; messages themselves have no embedding column. Provider embedding execution is currently disabled pending a qualified bounded adapter, so persistence accepts nullable vectors and retrieval uses account-scoped lexical search. See [MEMORY_LAYER.md](MEMORY_LAYER.md) for the extraction, deduplication, retrieval and maintenance pipeline.

**Typed SSE streaming.** Real-time token streaming uses typed events (`token`, `thinking`, `routing`, `tool_call`, `tool_result`, `final`, `error`, `done`) rather than raw model output, enabling structured frontend rendering.

**Owned artifacts and durable boundaries.** Generated media and generated documents are written into opaque per-user filesystem namespaces derived server-side from the authenticated identity; download routes stay filename-only and resolve only within the authenticated owner's namespace.

**Hosted-first execution.** Local inference is an optional future extension, separate from companion resource access. Neither local hardware nor a paired device is a prerequisite for the intended hosted product.

## Project Structure

```
Daemon/
├── orchestrator/       # FastAPI backend (main app, routing, subagents,
│                       #   memory, tools, routes, worker)
│   ├── main.py         # FastAPI app + OpenAI-compatible + SSE endpoints
│   ├── daemon.py       # Core orchestration loop (stream_sse_chat)
│   ├── config.py       # Deployment + workload model configuration
│   ├── prompts.py      # System prompt assembly
│   ├── memory/         # Full memory pipeline (store, extraction, dedup,
│                       #   retrieval, injection, embedding, encryption)
│   ├── routes/         # API route modules
│   ├── subagents/      # Subagent implementations
│   ├── council/        # Deliberation engine, roster and SSE
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

Note: The main backend application lives under `orchestrator/`; `backend/` holds Docker build support only. Retained modules, routes and UI do not imply enabled execution.

## Developer Quick Start

These commands run a development/deployment instance. They are not the intended hosted-user onboarding flow. Configure identity, storage and qualified provider routes according to the linked setup and architecture guides; a running server alone does not establish inference readiness.

**Prerequisites:** [uv](https://github.com/astral-sh/uv) installed.

```bash
# Local development (backend only, postgres/redis must be running)
uv run uvicorn orchestrator.main:app --reload --host 0.0.0.0 --port 8000

# Docker (full stack, production server commands)
cp .env.example .env    # Configure providers
# Edit .env and set a unique POSTGRES_PASSWORD before first startup.
# The copied file starts in production mode (DAEMON_AUTH_PEPPER and
# DAEMON_ALLOWED_HOSTS required). For a local stack over plain HTTP, uncomment
# DAEMON_ENVIRONMENT=development and DAEMON_COOKIE_SECURE=false instead.
docker compose up --build
```

Docker compose binds PostgreSQL and Redis to `127.0.0.1` only. Containers use
the private compose network; external database access should be an explicit
operator override, not the committed default.

The Docker compose stack starts uvicorn without `--reload` and serves the
frontend with `next start`. Use host-local commands such as `npm run dev` only
for development sessions.

Verify: `curl http://localhost:8000/health`

Benchmarking runs from the host shell against localhost-exposed container services. See [QUICKSTART.md](QUICKSTART.md) for detailed setup.

## Existing Capabilities and Boundaries

Execution remains subject to account capacity and provider/service qualification. The [feature matrix](docs/FEATURE_MATRIX.md) records client-specific implementation status; saved history and internal background jobs do not yet provide general accepted-request continuity.

### Chat & Routing
- Native `/chat` endpoint with SSE streaming (typed events)
- OpenAI-compatible `/v1/chat/completions` and `/v1/models`
- Capability-aware model routing with task classification (`fast` vs `reasoning`)
- Per-request model override

### Memory
- Encrypted conversation and memory storage; pgvector schema retained with nullable vectors while provider embedding is disabled
- Background fact extraction through configured, policy-qualified models, followed by deduplication and storage
- Hybrid retrieval (vector similarity + BM25 + recency × confidence × trust); account-scoped lexical retrieval while no embedding route is approved
- Memory injection into system prompt per conversation
- `memory_read` / `memory_write` tools available to the orchestrator

### Subagents
- `@research` — Retained Brave search + synthesis implementation; registered spawn dispatch currently returns `capacity_unavailable`
- `@image` — Image/video execution retired pending qualified bounded adapters
- `@audio` — Audio execution retired pending qualified bounded adapters
- `@code` / `@reader` — Reserved; not implemented execution capabilities
- `generate_document` — Deterministic CSV/DOCX generation; durable owned artifact lifecycle remains planned

### Frontend
- Next.js 16 PWA with streaming chat (Vercel AI SDK `useChat`)
- Conversation list with CRUD, search, pinning, rename
- Retained Studio UI and video-credit history; generation is currently denied
- Retained voice settings; TTS/STT execution and direct vendor tokens are currently denied
- Settings panel: TTS voice/model/speed, STT language, memory management
- Rich inline rendering: images (lightbox + download), audio player, tool call blocks
- Error boundary for crash recovery

### API Routes

| Endpoint | Method | Description |
|---|---|---|
| `/v1/models` | GET | List available models (OpenAI-compatible) |
| `/v1/chat/completions` | POST | Chat completion, streaming and non-streaming |
| `/chat` | POST | Native Daemon chat with SSE streaming |
| `/conversations` | GET/POST | Conversation CRUD (uses `/conversations/{conversation_id}`) |
| `/memories` | GET/POST/DELETE | Memory management (uses `/memories/{memory_id}`) |
| `/skills` | GET/POST | Skills management (uses `/skills/{skill_id}`) |
| `/users/me/settings` | GET/PATCH | User preferences |
| `/video-credits/balance` | GET | Video credit balance |
| `/video-credits/transactions` | GET | Video credit transactions |
| `/health` | GET | Health check |

## Status

The repository contains a hosted assistant foundation — FastAPI backend, PWA client, pgvector memory, Redis+arq worker, typed SSE, subagent framework, council deliberation, skills, fetch and entitlements — subject to deployment qualification. Shipped provider policy approves no inference routes; registered spawn dispatch returns `capacity_unavailable`, and unbounded media/voice execution is retired. Durable user-task continuity and companion resource access are product direction, not implemented capability. See [Feature Matrix](docs/FEATURE_MATRIX.md), [subscription rollout prerequisites](docs/SUBSCRIPTION_ARCHITECTURE.md#rollout-prerequisites), and [vision integration evidence](docs/VISION_INTEGRATION_REPORT.md).

### Delivery Direction

1. **Stabilise the foundation.** Complete the remaining gate cleanup and approve the durable-request/resource contracts.
2. **Prove broad hosted continuity.** Ordinary questions, research and existing supported tools survive client closure with truthful state, saved results and safe recovery (DEC10). A single document-generation workflow is useful coverage, not the whole milestone.
3. **Add optional device extensions.** Prove scoped access on one desktop platform and the cross-device reference workflow, including its presentation-generation and validation dependencies.
4. **Expand supported actions and platforms.** Add conflict-safe writeback and other operations only with tested permission and recovery boundaries.

The vision's [delivery gates](docs/DAEMON_VISION.md#11-delivery-sequence-and-acceptance-criteria) define the intended evidence. Detailed architecture remains proposed in [DURABLE_REQUEST_DESIGN.md](docs/DURABLE_REQUEST_DESIGN.md); local inference and general computer control are not prerequisites for the first milestone.

## Documentation

- [Product vision](docs/DAEMON_VISION.md) and [approved decisions](docs/DAEMON_VISION_DECISIONS.md)
- [Stage 0 reconciliation](docs/DAEMON_RECONCILIATION.md) and [integration evidence](docs/VISION_INTEGRATION_REPORT.md)
- [Durable-request design — proposed](docs/DURABLE_REQUEST_DESIGN.md)
- [Documentation authority map](docs/SOURCES_OF_TRUTH.md), [feature matrix](docs/FEATURE_MATRIX.md), and [roadmap](docs/ROADMAP.md)

## License

This repository does not currently declare a license.
