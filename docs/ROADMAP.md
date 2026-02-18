# Implementation Roadmap

> Last updated: 2026-02-18

## Phase 1: Cloud Orchestration — COMPLETE ✅
**Timeline:** ~10 days (extended from 4 due to Open WebUI → Next.js pivot)

### Core API ✅
- [x] FastAPI scaffold with OpenAI-compatible endpoints + custom `/chat` SSE endpoint
- [x] LiteLLM integration with OpenRouter (88 models, tier-1 sorting, 5-min cache)
- [x] Tier-based model configuration (free/starter/pro/max/byok) with env-var model slots
- [x] Auto-routing: fast vs reasoning classification based on message complexity
- [x] SSE streaming with typed events (token, thinking, routing, tool_call, tool_result, final, error, done)

### Subagent Framework ✅
- [x] SubagentManager with sessions, parallel execution, error handling, timeouts
- [x] @research agent (parallel Brave searches + synthesis)
- [x] @image agent (Gemini Flash Image via OpenRouter)
- [x] @audio agent (ElevenLabs sound FX)
- [x] @code agent (sandboxed execution)
- [x] @reader agent (document analysis)
- [x] Tool registry: web_search, http_request, calculate, get_time, notifications, reminders

### Frontend ✅
- [x] Next.js 16 + Vercel AI SDK 4 + React 19
- [x] Streaming chat with SSE bridge to backend
- [x] Rich inline rendering: images (lightbox/download), audio player, tool call blocks
- [x] Voice I/O: ElevenLabs TTS streaming + STT push-to-talk
- [x] Model selector with curated catalog + full 88-model search
- [x] Sidebar: conversation list with CRUD, search, pinning, rename
- [x] PWA manifest + service worker + offline indicator
- [x] ThinkingIndicator, AgentStatusCard/List, ConnectionStatus

---

## Phase 2: Memory System — COMPLETE ✅
**Status:** Fully operational. Memory extraction pipeline writes `status="active"` directly.

### Storage Layer ✅
- [x] PostgreSQL + pgvector (pg16) with 13 migrations
- [x] Redis + arq worker queue
- [x] Fernet encryption at rest (messages, memories, extraction_log)
- [x] Conversation CRUD (create, get, list, update, delete)
- [x] Message persistence (insert on each turn, encrypted content)

### Memory Pipeline ✅
- [x] Embedding pipeline (text-embedding-3-small, 1536d, via OpenAI API with OpenRouter fallback)
- [x] Post-response fact extraction (GPT-4o-mini via LiteLLM)
- [x] Dedup engine: similarity thresholds (0.92 merge, 0.75 supersede)
- [x] Retrieval: composite scoring (similarity × recency × source_boost × confidence)
- [x] Injection: builds enhanced system prompt with memories + preferences + token budget
- [x] Memory tools: memory_read / memory_write integrated into Daemon
- [x] Extracted memories written as `status="active"` — pipeline fully operational

### Background Jobs ✅
- [x] extract_memories (extraction → dedup → insert with status="active")
- [x] generate_conversation_title (GPT-4o-mini, first exchange only, respects title_locked)
- [x] generate_summary (conversation summaries)
- [x] garbage_collect (cleanup deleted/rejected/inactive, 30-90 day retention)
- [x] Debounce + advisory locks + retry logic

### API Routes ✅
- [x] /conversations — list, get, create, update (PATCH), delete
- [x] /conversations/{id}/messages — message history
- [x] /memories — list, create, update, confirm, export, import, re-embed, delete-all
- [x] /users/settings — get, update
- [x] /system/health — health check

### Frontend Refactoring ✅ (Feb 2026)
- [x] Extracted useEventArchive hook for event management
- [x] Extracted AudioPlaybackProvider for audio state
- [x] Consolidated ConversationHistoryProvider
- [x] Added SettingsPanel component
- [x] Added useMediaQuery hook
- [x] Removed legacy hooks (useSpeechRecognition, useTextToSpeech)
- [x] Added lib/constants.ts and lib/format.ts utilities

### Remaining
- [ ] Memory management UI beyond "Clear All" (view, browse, edit individual memories)

---

## Phase 3: Local Pipeline — BLOCKED ON HARDWARE ⏸️
**Dependency:** RTX 5090 acquisition (~$5999 AUD for ASUS TUF)

### Local LLM
- [ ] Ollama/llama.cpp installation
- [ ] Qwen 72B Q5_K_M download (~55GB)
- [ ] `/local` pre-router integration (parsing implemented, inference not)
- [ ] Health check endpoint

### Local Image Generation
- [ ] ComfyUI or direct FLUX Dev setup
- [ ] Concurrent operation with Qwen (32GB allows coexistence)

### Local Search
- [ ] SearXNG container deployment
- [ ] @research agent local backend

---

## Phase 4: Hardening — ONGOING
- [ ] Fix known issues (see CURRENT_ISSUES.md)
- [ ] Test coverage (currently near-zero)
- [ ] Markdown rendering for chat messages
- [ ] Error boundary wrapping
- [ ] Logging / observability
- [ ] Rate limit handling
- [ ] Fallback chains (quota exhaustion → auto-downgrade)
- [ ] Cost tracking
- [ ] Backup/restore for memories
- [ ] Remove Open WebUI from docker-compose
- [ ] Remove legacy OpenCode Zen provider config

---

## Parallel: Hardware (Independent — DELAYED)
- [ ] 5090 ASUS TUF acquisition
- [ ] Full system build (9950X3D + 64GB DDR5 Kingston Fury Beast Black 6000 CL36)
- [ ] Be Quiet Light Base 500 non-LX case
- [ ] CachyOS setup
- [ ] Network configuration (static IP, Tailscale)

---

## Milestones

| Milestone | Description | Status |
|-----------|-------------|--------|
| M1 | Chat with orchestrator from mobile | ✅ Complete |
| M2 | Research + image generation | ✅ Complete |
| M2.5 | Voice I/O + audio gen + notifications | ✅ Complete |
| M3 | Memory persistence | ✅ Complete |
| M3.1 | Chat persistence across sessions | ✅ Complete |
| M4 | Local pipeline | ⏸️ Blocked on 5090 |
| M5 | Production-ready | Pending Phase 4 hardening |

---

## Risks

| Risk | Status | Mitigation |
|------|--------|------------|
| ~~Memory extraction invisible~~ | ✅ Fixed | Extraction now writes `status="active"` directly |
| Conversation switching state corruption | 🟡 UX bug | Key `useChat` on conversation ID |
| No markdown rendering | 🟡 UX gap | Add react-markdown + rehype |
| Near-zero test coverage | 🟡 Tech debt | Tests added: test_chat_history.py, test_store.py |
| 5090 acquisition delayed | ⚪ Mitigated | Cloud pipeline fully functional standalone |
