# Feature Matrix

## Purpose

This file is the versioned, PR-gated source of truth for Daemon's user-visible feature scope across client surfaces (Web, Android PWA, Android native, iOS future). Changes to user-visible capabilities must be reflected here before shipping.

Client Surface denotes user-invokable affordances only — direct interaction points the user can consciously trigger (buttons, slash commands, explicit menu actions). LLM-initiated tool executions rendered in-chat are system responses, not user affordances, and are excluded from client-surface status.

## Legend

- `—` = not applicable on this surface by design;
- `Not started` = no implementation work has happened;
- `Web experimental` = present on web only as a non-promoted experiment;
- `Backend stable` = backend support shipped, no client surface yet;
- `Mobile eligible` = client surfaces designed/architected, not yet implemented;
- `Cross-client stable` = live and stable on every surface where it should exist;
- `Platform-specific permanent` = deliberately scoped to this surface only (e.g., keyboard shortcuts on web, share intent on mobile);
- `Retired` = intentionally removed from the active product surface while a replacement is tracked separately.

## Update protocol

- Any PR that adds, removes, or changes a user-visible feature must edit this file.
- New features add a new row; changed features modify cell values.
- Removing a row requires explicit justification in the PR description.

---

## Feature Matrix

| Feature | Web | Android PWA | Android native | iOS future | Backend dependency | Wedge required? |
|---|---|---|---|---|---|---|
| **Chat & Streaming** | — | — | — | — | — | — |
| Chat Streaming + Reconnect | Cross-client stable | Cross-client stable | Not started | Not started | POST /chat SSE streaming service; prompt datetime uses saved user timezone, then configurable deployment default (UTC) | Yes |
| File Upload | Cross-client stable | Cross-client stable | Not started | Not started | Client-side file attachments and chat serialization | No |
| Stop/Cancel Streaming | Cross-client stable | Cross-client stable | Not started | Not started | Web/PWA composer exposes Stop and `Esc`, preserving partial output with a `(stopped)` label | No |
| Copy Message | Not started | Not started | Not started | Not started | Pure client-side clipboard action not implemented | No |
| Regenerate Response | Not started | Not started | Not started | Not started | Existing chat submission path; retry UI not wired | No |
| Edit and Resubmit Message | Not started | Not started | Not started | Not started | Existing chat submission path; message edit UI not implemented | No |
| Model Discovery | Cross-client stable | Cross-client stable | Not started | Not started | GET /v1/models, GET /v1/catalog | No |
| Typed SSE Event Protocol | Cross-client stable | Cross-client stable | Not started | Not started | Chat and council streaming services | No |
| OpenAI Chat Completions API | Backend stable | Backend stable | Not started | Not started | POST /v1/chat/completions compatibility endpoint; same user/deployment prompt timezone precedence as native chat | No |
| **Conversations** | — | — | — | — | — | — |
| Recent Conversations List (search, pin, rename, delete) | Cross-client stable | Cross-client stable | Not started | Not started | GET /conversations, POST /conversations, DELETE /conversations/{id}, PATCH /conversations/{id} | Yes |
| Conversation Switching | Cross-client stable | Cross-client stable | Not started | Not started | GET /conversations/{id} | No |
| Long Conversation Controls | Mobile eligible | Mobile eligible | Not started | Not started | Client-side collapse of messages over 1,200 characters outside the latest five, kept findable by browser search via `hidden="until-found"` (never collapsed where unsupported), persisted tool-log visibility, recoverable spawn history, and jump-to-latest during streaming; virtual scrolling at 50+ loaded messages is deferred to [#298](https://github.com/sol-aeternum/Daemon/issues/298) | No |
| **Memory (user-visible)** | — | — | — | — | — | — |
| Memory Read (account-scoped retrieval) | Cross-client stable | Cross-client stable | Not started | Not started | GET /memories; lexical fallback while embeddings are unqualified | No |
| Memory Write (explicit storage) | Backend stable | Backend stable | Not started | Not started | POST /memories | No |
| Memory Correction | Cross-client stable | Cross-client stable | Not started | Not started | POST /memories/{id}/confirm | No |
| Memory Export/Import | Backend stable | Backend stable | Not started | Not started | POST /memories/export, POST /memories/import | No |
| Memory Reflect (non-persistent synthesis) | Backend stable | Backend stable | Not started | Not started | Memory reflection service | No |
| Memory Clear All | Cross-client stable | Cross-client stable | Not started | Not started | DELETE /memories?confirm=true | No |
| **Subagents** | — | — | — | — | — | — |
| @research (web search + synthesis) | Retired | Retired | Not started | Not started | Retained client/framework integration; registered spawn dispatch currently returns capacity_unavailable pending qualified bounded adapters | No |
| @image (image generation) | Retired | Retired | Not started | Not started | Unbounded vendor execution disabled; replacement requires a qualified, cost-reserving adapter | No |
| @image Video Generation | Retired | Retired | Not started | Not started | Unbounded vendor execution disabled; retained video credits do not enable execution | No |
| @audio (ElevenLabs sound effects) | Retired | Retired | Not started | Not started | POST /sound-effects denied pending a qualified, bounded adapter | No |
| Document file generation (`generate_document`) | Cross-client stable | Cross-client stable | Not started | Not started | `generate_document` tool + /generated-files/{filename} | No |
| @code (code generation) — NOT IMPLEMENTED | Web experimental | Web experimental | Not started | Not started | Reserved subagent orchestration mode | No |
| @reader (document analysis) — NOT IMPLEMENTED | Web experimental | Web experimental | Not started | Not started | Reserved subagent orchestration mode | No |
| **Tools** | — | — | — | — | — | — |
| Web Search (Brave Search API) | Backend stable | Backend stable | Not started | Not started | Web search service | No |
| Web Fetch (multi-strategy URL fetcher) | Backend stable | Backend stable | Not started | Not started | URL fetch service; direct requests use a stable Daemon identity with an operator-configurable User-Agent | No |
| HTTP Request (generic) | Backend stable | Backend stable | Not started | Not started | HTTP request service | No |
| Reminders (local JSON) | Backend stable | Backend stable | Not started | Not started | Reminder scheduling service | No |
| Time & Math (get_time, calculate) | Backend stable | Backend stable | Not started | Not started | Utility tools service | No |
| Consult Advisor (domain expert escalation) | Not started | Not started | Not started | Not started | No advisor tool is registered in the shared tool registry, so there is no user-invokable client affordance; advisor SSE event and stored-trace compatibility is retained alongside generic tool activity, and council roles are separate | No |
| Spawn Agent / Spawn Multiple | Retired | Retired | Not started | Not started | Registered tool interfaces retained; execution currently returns capacity_unavailable before dispatch pending qualified bounded adapters | No |
| Memory Organization Controls | Backend stable | Backend stable | Not started | Not started | Memory management API | No |
| Skill Management (CRUD) | Cross-client stable | Cross-client stable | Not started | Not started | GET/POST /skills, GET/PUT/DELETE /skills/{id}, PATCH endpoints, POST /skills/upload | No |
| Interactive HTML Artifacts | Cross-client stable | Cross-client stable | Not started | Not started | Interactive artifact rendering service | No |
| **Voice I/O** | — | — | — | — | — | — |
| ElevenLabs TTS (streaming) | Retired | Retired | Not started | Not started | POST /tts and GET /audio/token denied pending bounded server-side execution | No |
| ElevenLabs STT (streaming) | Retired | Retired | Not started | Not started | POST /stt and GET /audio/scribe-token denied; vendor tokens cannot bypass account budgets | No |
| Sound Effects Generation (ElevenLabs) | Retired | Retired | Not started | Not started | POST /sound-effects denied pending a qualified, bounded adapter | No |
| Voice Settings (TTS voice/model/speed/format, STT language) | Cross-client stable | Cross-client stable | Not started | Not started | Client settings storage + PATCH /users/me/settings | No |
| **Models & Routing** | — | — | — | — | — | — |
| Model Selector UI (catalog + full search) | Cross-client stable | Cross-client stable | Not started | Not started | GET /v1/catalog, GET /v1/models; desktop chat header shows the running model and active subagent count from the current response | No |
| **Settings** | — | — | — | — | — | — |
| Account Plan & Premium Trial Capacity | Web experimental | Web experimental | Not started | Not started | Authenticated /users/me/entitlements; centralized Free/Pro/Power policy and usage-based trial (deployment qualification required) | No |
| Appearance Settings (Dark/Light/System theme) | Cross-client stable | Cross-client stable | Not started | Not started | Client theme settings (no backend) | No |
| Enrollment & Profile Settings (display name, custom instructions) | Cross-client stable | Cross-client stable | Not started | Not started | GET /users/me/settings, PATCH /users/me/settings; desktop chat header shortcut and settings navigation preserve the return conversation | Yes |
| Memory Management Settings | Cross-client stable | Cross-client stable | Not started | Not started | GET /memories, DELETE /memories/{id}, POST /memories/{id}/confirm, DELETE /memories?confirm=true | No |
| **Auth & Sessions** | — | — | — | — | — | — |
| First-boot Setup | Backend stable | Backend stable | Backend stable | Backend stable | Setup token: Postgres-shared one-time verifier, local 0600 operator token file, advisory lock, zero-active-device condition | Yes |
| Hosted Auth Landing | Cross-client stable | Cross-client stable | Not started | Not started | Hosted login shows configured identity providers and a single "Keep me signed in" opt-in (default off, for shared computers) without setup or enrollment fields; `/landing` login and signup CTAs enter through `/auth`, which redirects confirmed self-hosted deployments to `/setup` | No |
| Hosted `/auth` route | Cross-client stable | Cross-client stable | Not started | Not started | `/auth` shows hosted sign-in or a retryable runtime-config error; only confirmed self-hosted mode redirects to `/setup`; runtime mode sourced from `GET /v1/auth/config` | No |
| Runtime auth config endpoint (`GET /v1/auth/config`) | Cross-client stable | Cross-client stable | Backend stable | Backend stable | Public no-store endpoint exposes `{mode, email, google}`; frontend responses expire and refresh within 60 seconds so `/auth` vs `/setup` follows runtime mode without rebuilding | No |
| Email Sign-In | Cross-client stable | Cross-client stable | Backend stable | Backend stable | Email code identity proof exchanges for Daemon-issued device/session tokens | Yes |
| Google Sign-In | Cross-client stable | Cross-client stable | Not started | Not started | Standard GIS-rendered button with server nonce challenge and manual callback; Google-only profile needs no email delivery, creates temporary sessions unless "Keep me signed in" is checked, and supports explicitly configured open signup | Yes |
| Identity-Created Device Sessions | Cross-client stable | Cross-client stable | Backend stable | Backend stable | Hosted identity completion creates web or native devices and Daemon sessions; provider tokens are not API auth | Yes |
| Device Management | Backend stable | Backend stable | Backend stable | Backend stable | GET /devices, DELETE /devices/{id} | No |
| Hosted Identity Device Management | Cross-client stable | Cross-client stable | Not started | Not started | Identity-aware devices UI distinguishes web, native, enrollment-created, and identity-created devices | No |
| Device Enrollment | Backend stable | Backend stable | Backend stable | Backend stable | POST /enroll/initiate (pending-id), POST /enroll/complete (pending-id lookup) | Yes |
| Refresh Token Rotation | Backend stable | Backend stable | Backend stable | Backend stable | POST /refresh (cookie-backed web refresh, native JSON-body refresh, rotate on use) | Yes |
| **Notifications** | — | — | — | — | — | — |
| Push Completion Notifications (ntfy.sh) | Backend stable | Backend stable | Not started | Not started | Notification delivery service | Yes |
| **Artifacts** | — | — | — | — | — | — |
| Inline Image Rendering (lightbox + download) | Cross-client stable | Cross-client stable | Not started | Not started | GET /generated-images/{filename} | No |
| Inline Audio Playback | Cross-client stable | Cross-client stable | Not started | Not started | GET /generated-audio/{filename} | No |
| Inline Video Playback | Cross-client stable | Cross-client stable | Not started | Not started | Video URLs (xAI/fal.ai hosted or /generated-files/) | No |
| Artifacts Gallery (image + audio collection) | Cross-client stable | Cross-client stable | Not started | Not started | GET /conversations/{id} + SSE event parsing | No |
| Document File Generation (.docx, .csv download) | Cross-client stable | Cross-client stable | Not started | Not started | Subagent orchestration service + /generated-files/{filename} | No |
| **Council/Studio** | — | — | — | — | — | — |
| Council Deliberation (multi-perspective LLM debate) | Cross-client stable | Cross-client stable | Not started | Not started | Council streaming service | No |
| Council Interview Flow (roster, rounds, audit config) | Cross-client stable | Cross-client stable | Not started | Not started | Welcome-screen Deliberate shortcut or /council command → interview flow | No |
| Studio Image Generation (web UI) | Retired | Retired | Not started | Not started | Authenticated retired Studio image API surface returns 410; hosted-identity replacement tracked separately | No |
| Studio Video Generation (web UI with credit check) | Retired | Retired | Not started | Not started | UI retained; generation denied pending bounded provider integration; credit balances/history preserved | No |
| Video Credit Balance & Transactions | Cross-client stable | Cross-client stable | Not started | Not started | GET /video-credits/balance, GET /video-credits/transactions, GET /video-credits/estimate | No |
| **BYOK** | — | — | — | — | — | — |
| BYOK (bring your own inference credentials) | Not started | Not started | Not started | Not started | Future credential funding adapter; policy architecture reserves capability, no client plan or credit bypass | No |
| **Projects** | — | — | — | — | — | — |
| Projects Page (placeholder — not yet implemented) | Web experimental | Web experimental | Not started | Not started | No backend API yet | No |
| **Mobile wedge targets** | — | — | — | — | — | — |
| Share Intent Ingestion | — | Not started | Not started | Not started | No backend (OS/app-intent entry point not implemented) | Yes |
| Biometric Unlock | — | — | Not started | Not started | No backend (client OS biometric gate not implemented) | Yes |
| **Local Pipeline** | — | — | — | — | — | — |
| Local Pipeline Routing (/local flag) | Not started | Not started | Not started | Not started | Pre-router intent parsing and disabled Cloud/Local UI; local inference pending hardware | No |
| **PWA / Offline** | — | — | — | — | — | — |
| PWA Service Worker + Offline Indicator | Platform-specific permanent | Platform-specific permanent | Not started | Not started | Browser service worker (no backend) | No |
| Mobile-Responsive Navigation (hamburger + sidebar) | Cross-client stable | Cross-client stable | Not started | Not started | Purely frontend responsive navigation; settings sections form a vertical list on mobile with 44px minimum touch targets | No |
