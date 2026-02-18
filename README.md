# Daemon — Personal Multi-Agent Assistant

A mobile-first AI assistant with multi-model orchestration, persistent memory, and specialized subagents. FastAPI backend + Next.js 16 frontend. Self-hosted with Docker Compose.

**Live:** `https://dmn.solaeternum.xyz` (Pro tier: Kimi K2.5 orchestrator)

---

## What It Is

Daemon is a personal AI assistant that:

- **Responds directly** most of the time (Kimi K2.5 via OpenRouter)
- **Spawns subagents** when specialized capability is needed (@research, @image, @audio, @code, @reader)
- **Remembers context** across conversations via PostgreSQL + pgvector memory system
- **Runs entirely self-hosted** with Docker Compose (cloud LLMs via OpenRouter, data stays local)

### Core Philosophy

> Daemon is the assistant — not a router that delegates everything. It responds directly and only escalates to subagents when the task demands specialized capability.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Next.js 16 Frontend (PWA)                              │
│  React 19 + Vercel AI SDK + Tailwind CSS               │
│  Voice I/O • Markdown • Offline Support                │
└────────────────────┬────────────────────────────────────┘
                     │ /api/chat (SSE stream)
                     ▼
┌─────────────────────────────────────────────────────────┐
│  FastAPI Backend                                        │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Orchestrator (Kimi K2.5)                       │   │
│  │  Streaming SSE • Tool Use • Memory Injection   │   │
│  └────────────┬────────────────────────────────────┘   │
│               │                                         │
│      ┌────────┴────────┐                                │
│      ▼                 ▼                                │
│  Subagents         Tools                                │
│  @research         • web_search (Brave)                │
│  @image            • http_request                      │
│  @audio            • calculate                         │
│  @code             • get_time                          │
│  @reader           • notifications (ntfy.sh)           │
│                    • memory_read / memory_write        │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Memory Layer                                   │   │
│  │  PostgreSQL + pgvector • Fernet Encryption     │   │
│  │  Redis + arq (background jobs)                 │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Tech Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Python 3.11, FastAPI, LiteLLM, asyncpg, arq |
| **Frontend** | Next.js 16, React 19, Vercel AI SDK 4, Tailwind CSS |
| **Database** | PostgreSQL 16 + pgvector extension |
| **Queue** | Redis 7 + arq (async job processor) |
| **LLMs** | OpenRouter (88 models), tier-based routing |
| **Embeddings** | OpenAI text-embedding-3-small |
| **Voice** | ElevenLabs (TTS, STT Scribe, sound FX) |
| **Search** | Brave Search API |
| **Notifications** | ntfy.sh |

---

## Quick Start

### Prerequisites

- Docker + Docker Compose
- OpenRouter API key
- OpenAI API key (for embeddings)
- (Optional) Brave Search API key, ElevenLabs API key, ntfy.sh topic

### 1. Clone & Configure

```bash
git clone https://github.com/sol-aeternum/Daemon.git
cd Daemon

# Copy and edit environment variables
cp .env.example .env
# Edit .env with your API keys
```

### 2. Start Services

```bash
docker compose up --build
```

This starts 5 containers:
- **frontend**: `http://localhost:3000` (Next.js dev server)
- **backend**: `http://localhost:8000` (FastAPI)
- **worker**: Background job processor (arq)
- **postgres**: PostgreSQL + pgvector
- **redis**: Redis for job queue

### 3. Access the App

Open `http://localhost:3000` in your browser.

Install as PWA on mobile: Chrome menu → "Add to Home Screen"

---

## Features

### 🤖 Multi-Model Orchestration

Tier-based configuration with auto-routing:

| Tier | Price | Orchestrator | Subagents | Use Case |
|------|-------|--------------|-----------|----------|
| Free | $0 | Kimi K2.5 | None | Basic chat |
| Starter | $9/mo | Kimi K2.5 | Sonnet, Gemini | Research + code |
| **Pro** | $19/mo | Kimi K2.5 | Full suite | **Default tier** |
| Max | $29/mo | Claude 3 Opus | Premium models | Heavy reasoning |
| BYOK | $9/mo | Kimi K2.5 | User-configured | Custom OpenRouter key |

All model assignments are env-var configurable — swap models without code changes.

### 🧠 Persistent Memory

- **Automatic extraction**: GPT-4o-mini extracts facts from conversations
- **Semantic search**: pgvector + composite scoring (similarity × recency × confidence)
- **Encryption at rest**: Fernet-encrypted content, plaintext embeddings for search
- **Memory tools**: `memory_read` and `memory_write` for explicit recall

### 🎯 Subagent Framework

Spawn specialized agents with `@mention`:

| Subagent | Trigger | Capability |
|----------|---------|------------|
| @research | `@research quantum computing` | Brave Search + synthesis |
| @image | `@image a futuristic city` | Gemini Flash image generation |
| @audio | `@audio generate rain sounds` | ElevenLabs sound FX |
| @code | `@code review this function` | Code analysis + suggestions |
| @reader | `@reader summarize https://...` | Web scraping + summarization |

### 🎙️ Voice I/O

- **TTS**: Streaming ElevenLabs with voice/model selection
- **STT**: Push-to-talk with Scribe v1
- **Sound FX**: Generate audio effects via @audio

### 📱 PWA Features

- Offline indicator + service worker caching
- Mobile-optimized UI (ChatGPT-style interface)
- Safe area insets for notched devices
- Installable to home screen

### 💬 Chat Features

- **Streaming responses**: Real-time SSE with typing indicators
- **Markdown rendering**: Code blocks, tables, links, formatting
- **Rich content**: Image lightbox, audio player, tool call logs
- **Conversation management**: Search, pin, rename, delete
- **Model selector**: Full 88-model catalog with search

---

## API Endpoints

### Chat (SSE Streaming)

```bash
curl -N -X POST http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_KEY' \
  -d '{
    "message": "What is quantum computing?",
    "conversation_id": "optional-existing-id"
  }'
```

**Events:** `token`, `thinking`, `routing`, `tool_call`, `tool_result`, `final`, `error`, `done`

### OpenAI-Compatible

- `GET /v1/models` — List 88 available models
- `POST /v1/chat/completions` — Standard chat (streaming/non-streaming)

### Memory & Conversations

- `GET /conversations` — List user's conversations
- `GET /conversations/{id}` — Get conversation with messages
- `POST /conversations` — Create new conversation
- `GET /memories` — List extracted memories
- `POST /memories/{id}/confirm` — Promote pending memory to active

### System

- `GET /health` — Health check
- `GET /system/health` — Detailed system status
- `GET /providers` — List configured LLM providers

---

## Project Structure

```
Daemon/
├── orchestrator/           # FastAPI backend
│   ├── main.py            # API routes, SSE streaming
│   ├── daemon.py          # Core orchestration loop
│   ├── config.py          # Tier system, provider config
│   ├── prompts.py         # System prompts
│   ├── memory/            # Memory pipeline
│   │   ├── store.py       # PostgreSQL CRUD
│   │   ├── extraction.py  # Fact extraction (GPT-4o-mini)
│   │   ├── retrieval.py   # Semantic search + scoring
│   │   ├── embedding.py   # text-embedding-3-small
│   │   └── tools.py       # memory_read/write
│   ├── agents/            # Subagent implementations
│   ├── worker/            # arq background jobs
│   └── routes/            # API route modules
├── frontend/              # Next.js 16 frontend
│   ├── app/               # App router (Next.js 13+)
│   │   ├── page.tsx       # Main chat interface
│   │   └── api/chat/      # SSE bridge to backend
│   ├── components/        # React components
│   │   ├── ChatInputBar.tsx
│   │   ├── ConversationList.tsx
│   │   ├── MarkdownMessage.tsx
│   │   └── ToolCallBlock.tsx
│   ├── hooks/             # Custom React hooks
│   └── lib/               # Utilities, types
├── docs/                  # Documentation
│   ├── CURRENT_ISSUES.md  # Known bugs (2 remaining)
│   ├── PROJECT_CONTEXT.md # Detailed architecture
│   ├── ROADMAP.md         # Phase planning
│   └── TECHNICAL_SPECS.md # API specs, schemas
├── migrations/            # PostgreSQL migrations
├── docker-compose.yml     # 5-service stack
└── .env.example           # Configuration template
```

---

## Configuration

Key environment variables (see `.env.example` for full list):

```env
# Required
OPENROUTER_API_KEY=sk-or-v1-...
OPENAI_API_KEY=sk-...              # For embeddings only

# Optional (for full features)
BRAVE_API_KEY=...                  # @research subagent
ELEVENLABS_API_KEY=...             # Voice I/O
NTFY_TOPIC=...                     # Push notifications

# Tier Configuration (all optional, have defaults)
TIER_FREE_ORCHESTRATOR=openrouter/deepseek/deepseek-chat
TIER_STARTER_ORCHESTRATOR=openrouter/kimi/k2.5
TIER_PRO_ORCHESTRATOR=openrouter/kimi/k2.5
TIER_MAX_ORCHESTRATOR=openrouter/anthropic/claude-3-opus
```

---

## Development

### Backend Only

```bash
cd daemon
uv run uvicorn orchestrator.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Only

```bash
cd frontend
npm install
npm run dev
```

### Database Migrations

```bash
cd daemon
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head
```

### Background Jobs

The worker container runs arq for async tasks:
- `extract_memories` — Extract facts from completed conversations
- `generate_title` — Auto-generate conversation titles
- `generate_summary` — Create conversation summaries
- `garbage_collect` — Clean up old data

---

## Documentation

| Document | Contents |
|----------|----------|
| [PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) | Detailed architecture, current state, decisions |
| [PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) | High-level overview, tier system, hardware plans |
| [CURRENT_ISSUES.md](docs/CURRENT_ISSUES.md) | Known bugs (2 low-priority issues remaining) |
| [ROADMAP.md](docs/ROADMAP.md) | Phase planning: Phase 1 ✅, Phase 2 ~90%, Phase 3 pending |
| [TECHNICAL_SPECS.md](docs/TECHNICAL_SPECS.md) | System prompts, schemas, API specifications |
| [OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) | Unresolved design decisions |

---

## Status

- **Phase 1 (Cloud Orchestration)**: ✅ Complete
- **Phase 2 (Memory System)**: ✅ Complete — extraction pipeline fully operational
- **Phase 3 (Local Pipeline)**: ⏸️ Blocked on RTX 5090 acquisition

See [CURRENT_ISSUES.md](docs/CURRENT_ISSUES.md) for remaining work.

---

## License

MIT — Personal use and modification allowed. Attribution appreciated.

---

Built with [Sisyphus](https://github.com/code-yeongyu/oh-my-opencode) | Self-hosted on CachyOS + Docker
