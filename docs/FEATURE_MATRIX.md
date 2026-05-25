# Feature Matrix

## Purpose

This file is the versioned, PR-gated source of truth for Daemon's user-visible feature scope across client surfaces (Web, Android PWA, Android native, iOS future). Changes to user-visible capabilities must be reflected here before shipping.

## Legend

- `—` = not applicable on this surface by design;
- `Not started` = no implementation work has happened;
- `Web experimental` = present on web only as a non-promoted experiment;
- `Backend stable` = backend support shipped, no client surface yet;
- `Mobile eligible` = client surfaces designed/architected, not yet implemented;
- `Cross-client stable` = live and stable on every surface where it should exist;
- `Platform-specific permanent` = deliberately scoped to this surface only (e.g., keyboard shortcuts on web, share intent on mobile).

## Update protocol

- Any PR that adds, removes, or changes a user-visible feature must edit this file.
- New features add a new row; changed features modify cell values.
- Removing a row requires explicit justification in the PR description.

---

## Feature Matrix

| Feature | Web | Android PWA | Android native | iOS future | Backend dependency | Wedge required? |
|---|---|---|---|---|---|---|
| **Chat & Streaming** | — | — | — | — | — | — |
| Chat Streaming + Reconnect | Cross-client stable | Cross-client stable | Not started | Not started | POST /chat → stream_sse_chat() | Yes |
| OpenAI Chat Completions API | Backend stable | Backend stable | Not started | Not started | POST /v1/chat/completions | No |
| Model Discovery | Cross-client stable | Cross-client stable | Not started | Not started | GET /v1/models, GET /v1/catalog | No |
| Typed SSE Event Protocol | Cross-client stable | Cross-client stable | Not started | Not started | SSE events (orchestrator/daemon.py, council/sse.py) | No |
| **Conversations** | — | — | — | — | — | — |
| Recent Conversations List (search, pin, rename, delete) | Cross-client stable | Cross-client stable | Not started | Not started | GET /conversations, POST /conversations, DELETE /conversations/{id}, PATCH /conversations/{id} | Yes |
| Conversation Switching | Cross-client stable | Cross-client stable | Not started | Not started | GET /conversations/{id} | No |
| **Memory (user-visible)** | — | — | — | — | — | — |
| Memory Read (semantic retrieval) | Cross-client stable | Cross-client stable | Not started | Not started | GET /memories | No |
| Memory Write (explicit storage) | Backend stable | Backend stable | Not started | Not started | POST /memories | No |
| Memory Correction | Cross-client stable | Cross-client stable | Not started | Not started | POST /memories/{id}/confirm | No |
| Memory Export/Import | Backend stable | Backend stable | Not started | Not started | POST /memories/export, POST /memories/import | No |
| Memory Reflect (non-persistent synthesis) | Backend stable | Backend stable | Not started | Not started | memory_reflect tool (LLM-to-LLM) | No |
| Memory Clear All | Cross-client stable | Cross-client stable | Not started | Not started | DELETE /memories?confirm=true | No |
| Background Memory Extraction (automatic) | Backend stable | Backend stable | Not started | Not started | extract_memories job (Redis/arq) | No |
| **Subagents** | — | — | — | — | — | — |
| @research (web search + synthesis) | Cross-client stable | Cross-client stable | Not started | Not started | spawn_agent tool, mode=research → ResearchSubagent | No |
| @image (image generation) | Cross-client stable | Cross-client stable | Not started | Not started | spawn_agent tool, mode=image → ImageSubagent | No |
| @image Video Generation | Cross-client stable | Cross-client stable | Not started | Not started | spawn_agent tool, mode=video → ImageSubagent + VideoCreditsDAL | No |
| @audio (ElevenLabs sound effects) | Cross-client stable | Cross-client stable | Not started | Not started | POST /sound-effects | No |
| @document (document file generation) | Cross-client stable | Cross-client stable | Not started | Not started | spawn_agent tool, mode=document → GET /generated-files/{filename} | No |
| @code (code generation) — NOT IMPLEMENTED | Web experimental | Web experimental | Not started | Not started | SubagentType.CODE enum only (no implementation) | No |
| @reader (document analysis) — NOT IMPLEMENTED | Web experimental | Web experimental | Not started | Not started | SubagentType.READER enum only (no implementation) | No |
| **Tools** | — | — | — | — | — | — |
| Web Search (Brave Search API) | Backend stable | Backend stable | Not started | Not started | web_search tool (not REST) | No |
| Web Fetch (multi-strategy URL fetcher) | Backend stable | Backend stable | Not started | Not started | web_fetch tool, FetchService | No |
| HTTP Request (generic) | Backend stable | Backend stable | Not started | Not started | http_request tool | No |
| Reminders (local JSON) | Backend stable | Backend stable | Not started | Not started | reminder_set + reminder_list tools, data/reminders.json | No |
| Time & Math (get_time, calculate) | Backend stable | Backend stable | Not started | Not started | get_time + calculate tools | No |
| Consult Advisor (domain expert escalation) | Cross-client stable | Cross-client stable | Not started | Not started | consult_advisor tool (5 domains) | No |
| Spawn Agent / Spawn Multiple | Backend stable | Backend stable | Not started | Not started | spawn_agent + spawn_multiple tools | No |
| Memory Tier Management (L0/L1 promotion/demotion) | Backend stable | Backend stable | Not started | Not started | memory_promote + memory_demote tools | No |
| Skill Management (CRUD) | Cross-client stable | Cross-client stable | Not started | Not started | GET/POST /skills, GET/PUT/DELETE /skills/{id}, PATCH endpoints, POST /skills/upload | No |
| Interactive HTML Artifacts | Cross-client stable | Cross-client stable | Not started | Not started | Orchestrator generates html:interactive code blocks | No |
| **Voice I/O** | — | — | — | — | — | — |
| ElevenLabs TTS (streaming) | Cross-client stable | Cross-client stable | Not started | Not started | POST /tts, GET /audio/token | No |
| ElevenLabs STT (streaming) | Cross-client stable | Cross-client stable | Not started | Not started | POST /stt, GET /audio/scribe-token | No |
| Sound Effects Generation (ElevenLabs) | Backend stable | Backend stable | Not started | Not started | POST /sound-effects | No |
| Voice Settings (TTS voice/model/speed/format, STT language) | Cross-client stable | Cross-client stable | Not started | Not started | localStorage (TTS), PATCH /users/me/settings (STT) | No |
| **Models & Routing** | — | — | — | — | — | — |
| Tier-based Model Routing (5 tiers) | Cross-client stable | Cross-client stable | Not started | Not started | TIER_*_MODEL env vars, model_router.py | No |
| Auto-classification (trivial/standard/complex) | Cross-client stable | Cross-client stable | Not started | Not started | classify_message() in model_router.py | No |
| Vision Fallback | Cross-client stable | Cross-client stable | Not started | Not started | gemini-2.5-flash-image for non-vision models | No |
| Model Selector UI (catalog + full search) | Cross-client stable | Cross-client stable | Not started | Not started | GET /v1/catalog, GET /v1/models | No |
| **Settings** | — | — | — | — | — | — |
| Appearance Settings (Dark/Light/System theme) | Cross-client stable | Cross-client stable | Not started | Not started | CSS variable switching (no backend) | No |
| Enrollment & Profile Settings (display name, custom instructions) | Cross-client stable | Cross-client stable | Not started | Not started | GET /users/me/settings, PATCH /users/me/settings | Yes |
| Memory Management Settings | Cross-client stable | Cross-client stable | Not started | Not started | GET /memories, DELETE /memories/{id}, POST /memories/{id}/confirm, DELETE /memories?confirm=true | No |
| **Notifications** | — | — | — | — | — | — |
| Push Completion Notifications (ntfy.sh) | Backend stable | Backend stable | Not started | Not started | notification_send tool → ntfy.sh | Yes |
| **Artifacts** | — | — | — | — | — | — |
| Inline Image Rendering (lightbox + download) | Cross-client stable | Cross-client stable | Not started | Not started | GET /generated-images/{filename} | No |
| Inline Audio Playback | Cross-client stable | Cross-client stable | Not started | Not started | GET /generated-audio/{filename} | No |
| Inline Video Playback | Cross-client stable | Cross-client stable | Not started | Not started | Video URLs (xAI/fal.ai hosted or /generated-files/) | No |
| Artifacts Gallery (image + audio collection) | Cross-client stable | Cross-client stable | Not started | Not started | GET /conversations/{id} + SSE event parsing | No |
| Document File Generation (.docx, .csv download) | Cross-client stable | Cross-client stable | Not started | Not started | spawn_agent mode=document → GET /generated-files/{filename} | No |
| **Council/Studio** | — | — | — | — | — | — |
| Council Deliberation (multi-perspective LLM debate) | Cross-client stable | Cross-client stable | Not started | Not started | stream_council() SSE (no REST) | No |
| Council Interview Flow (roster, rounds, audit config) | Cross-client stable | Cross-client stable | Not started | Not started | /council command → interview flow | No |
| Studio Image Generation (web UI) | Cross-client stable | Cross-client stable | Not started | Not started | POST /api/images/generate → ImageSubagent | No |
| Studio Video Generation (web UI with credit check) | Cross-client stable | Cross-client stable | Not started | Not started | POST /video-credits/estimate → POST /api/images/generate mode=video | No |
| Video Credit Balance & Transactions | Cross-client stable | Cross-client stable | Not started | Not started | GET /video-credits/balance, GET /video-credits/transactions, GET /video-credits/estimate | No |
| **BYOK** | — | — | — | — | — | — |
| BYOK (bring your own OpenRouter key) | Cross-client stable | Cross-client stable | Not started | Not started | User's XAI_API_KEY direct (no credit system) | No |
| **Projects** | — | — | — | — | — | — |
| Projects Page (placeholder — not yet implemented) | Web experimental | Web experimental | Not started | Not started | No backend API yet | No |
| **Mobile wedge targets** | — | — | — | — | — | — |
| Share Intent Ingestion | — | Not started | Not started | Not started | No backend (OS/app-intent entry point not implemented) | Yes |
| Biometric Unlock | — | — | Not started | Not started | No backend (client OS biometric gate not implemented) | Yes |
| **Local Pipeline** | — | — | — | — | — | — |
| Local Pipeline Pre-router (/local flag) | Backend stable | Backend stable | Not started | Not started | route_message() in router.py | No |
| **PWA / Offline** | — | — | — | — | — | — |
| PWA Service Worker + Offline Indicator | Platform-specific permanent | Platform-specific permanent | Not started | Not started | Browser service worker (no backend) | No |
| Mobile Navigation (sidebar + header) | Platform-specific permanent | Platform-specific permanent | Not started | Not started | No backend (purely frontend) | No |
