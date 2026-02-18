# Project Context — Daemon

> Last updated: 2026-02-18
> Source of truth: codebase audit against `daemon-core` + `daemon-frontend-core` tarballs

## What Daemon Is

Mobile-first personal AI assistant with multi-model orchestration. FastAPI backend orchestrates LLM calls via OpenRouter (88 models), spawning specialized subagents for research, image generation, audio, code, and document reading tasks. Next.js 16 frontend with Vercel AI SDK provides the chat interface. PostgreSQL + pgvector stores conversations and memories. Redis + arq handle background job processing.

## Architecture

```
┌───────────────────────────────────────────────────┐
│           Next.js 16 Frontend (PWA)               │
│           Vercel AI SDK 4 + React 19              │
└────────────────────┬──────────────────────────────┘
                     │ /api/chat (SSE bridge)
                     ▼
┌───────────────────────────────────────────────────┐
│              FastAPI Backend                      │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │ Pre-Router: /local → local | else → cloud   │  │
│  └─────────────┬───────────────────────────────┘  │
│                │                                  │
│    ┌───────────┴───────────┐                      │
│    ▼                       ▼                      │
│  LOCAL (Phase 3)     CLOUD PIPELINE               │
│  Qwen 72B Q5         Orchestrator model           │
│  (pending 5090)      (tier-configured)            │
│                            │                      │
│                      ┌─────┴─────┐                │
│                      │ Subagents │                │
│                      │ @research │                │
│                      │ @image    │                │
│                      │ @audio    │                │
│                      │ @code     │                │
│                      │ @reader   │                │
│                      └───────────┘                │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │ Memory Layer (PostgreSQL + pgvector)        │  │
│  │ Background Jobs (Redis + arq)               │  │
│  │ Encryption at rest (Fernet)                 │  │
│  └─────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┘
```

## Tier System

The backend implements a tier-based model configuration system. Tiers are real architecture; specific model assignments are placeholders subject to change as the model landscape evolves. All model assignments are env-var configurable — no code changes required to swap models.

| Tier | Price | Current Orchestrator | Subagents | Notes |
|------|-------|---------------------|-----------|-------|
| Free | $0 | Kimi K2.5 | None | Orchestrator only |
| Starter | $9/mo | Kimi K2.5 | Claude 3.5 Sonnet (research, code), Gemini Flash (image), Gemini Pro (reader) | Basic subagent suite |
| Pro | $19/mo | Kimi K2.5 | Same as Starter | Full subagent suite (default tier) |
| Max | $29/mo | Claude 3 Opus | Opus (code), Sonnet (research), Gemini Flash (image), Gemini Pro (reader) | Premium models, large embeddings |
| BYOK | $9/mo | Kimi K2.5 | User-configured | User's own OpenRouter key |

Auto-routing within tiers: messages are classified as `fast` (→ Gemini Flash) or `reasoning` (→ tier orchestrator) based on complexity heuristics including turn count and code presence.

**Note on model identity:** The system prompt instructs Daemon to identify as "Kimi K2.5 via OpenRouter" regardless of actual model running. This should track the tier's orchestrator assignment or be made dynamic.

## What's Implemented

### Phase 1: Cloud Orchestration ✅
- FastAPI with OpenAI-compatible endpoints + custom `/chat` SSE endpoint
- LiteLLM multi-provider routing via OpenRouter (88 models, tier-1 sorting)
- SubagentManager: @research (Brave Search), @image (Gemini Flash), @audio (ElevenLabs), @code, @reader
- Tool registry: web_search, http_request, calculate, get_time, notifications (ntfy.sh), reminders, memory_read, memory_write
- SSE streaming with typed events (token, thinking, routing, tool_call, tool_result, final, error, done)

### Phase 2: Memory System ✅
- PostgreSQL + pgvector running (13 migrations applied)
- Redis + arq worker queue operational
- Encryption at rest via Fernet (messages, memories, extraction log)
- Conversation CRUD with message persistence (conversations, messages tables)
- Memory extraction pipeline: GPT-4o-mini extracts facts → embedding → dedup → store with `status="active"`
- Memory retrieval: composite scoring (similarity × recency × source_boost × confidence)
- Memory injection: builds enhanced system prompt with retrieved memories + user preferences
- Memory tools: memory_read / memory_write integrated into Daemon's tool system
- Background jobs: extract_memories, generate_title, generate_summary, garbage_collect
- API routes: /conversations, /memories, /users/settings, /system/health
- Retry detection: orchestrator/tools/retry.py with word-boundary matching

### Frontend (Work in Progress)
- Streaming chat via Vercel AI SDK `useChat` with SSE bridge to backend
- Conversation list with CRUD, search, pinning, rename
- Rich inline rendering: images (lightbox + download), audio player, tool call blocks
- Voice I/O: ElevenLabs TTS (streaming) + STT (Scribe) with push-to-talk
- Model selector with catalog + full 88-model search
- ThinkingIndicator, AgentStatusCard/List for orchestration visibility
- PWA manifest + offline indicator + service worker caching
- Settings panel: TTS voice/model/speed, STT language, memory management (clear all)

### Phase 3: Local Pipeline — Blocked on Hardware
- Pre-router `/local` flag parsing implemented
- All local inference code unimplemented (pending RTX 5090 acquisition)

## Infrastructure

### Docker Compose Services (6 containers)
- `backend` — FastAPI (port 8000)
- `worker` — arq background job processor
- `frontend` — Next.js 16 (port 3000)
- `postgres` — pgvector/pgvector:pg16
- `redis` — Redis 7 Alpine

### Cleanup Required
- Open WebUI service still in docker-compose.yml — **remove** (dead weight from pre-pivot era)
- Legacy OpenCode Zen provider config in Settings — **remove**

### Key Dependencies

**Backend:** FastAPI, LiteLLM, httpx, asyncpg, arq, cryptography, pydantic-settings

**Frontend:** Next.js 16, React 19, Vercel AI SDK 4, @ai-sdk/react, @ai-sdk/openai, lucide-react, next-pwa

**External APIs:** OpenRouter (LLMs), OpenAI (embeddings only — separate API key), Brave Search, ElevenLabs (TTS/STT/SFX), ntfy.sh (push notifications)

### Database Schema (13 migrations)
- `users` — single default user (multi-user scaffolded)
- `conversations` — id, user_id, title, pipeline, pinned, title_locked, status, metadata
- `messages` — conversation_id, role, content (encrypted), model, status, metadata
- `memories` — content (encrypted), embedding (1536d), category, source_type, confidence, status, source_conversation_id
- `extraction_log` — tracks extraction runs per conversation

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Frontend | Next.js 16 + Vercel AI SDK | Open WebUI lacked orchestration state concepts |
| Tier system | 5 tiers, env-var model slots | Model assignments as configuration, not code |
| Default tier | Pro (Kimi K2.5) | Placeholder — model assignments are fluid |
| Cloud search | Brave Search API | Fast, privacy-respecting |
| Cloud image gen | Gemini Flash Image via OpenRouter | In existing API flow |
| Voice I/O | ElevenLabs | TTS, STT (Scribe), sound FX |
| Notifications | ntfy.sh | Simple, self-hostable |
| Memory encryption | Fernet (at rest) | Content + messages encrypted, embeddings plaintext for pgvector |
| Embeddings | text-embedding-3-small (1536d) via OpenAI API | Separate from OpenRouter |
| Local LLM | Qwen 2.5 72B Q5_K_M | 32GB VRAM enables Q5, no CPU offload |
| Local image gen | FLUX Dev | Privacy, no guardrails |
| Local search | SearXNG | No Google/Bing dependency |
| GPU | ASUS TUF 5090 @ $5999 AUD | Delayed — cloud pipeline proceeds independently |

## Unresolved

- Frontend: conversation switching / useChat state management
- Frontend: no markdown rendering
- Local pipeline complexity: full orchestration or simple Qwen-only?
- Memory scope: shared vs separate stores for cloud/local
- Always-on vs Wake-on-LAN for home server
- Test coverage: minimal (test_chat_history.py, test_store.py added)

## Next Steps

1. Fix conversation switching state management
2. Add markdown rendering to chat messages
3. Remove Open WebUI and legacy OpenCode references from codebase
4. Acquire RTX 5090 TUF, then execute Phase 3
