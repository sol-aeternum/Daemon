# Technical Specifications

Verified-against-commit: 3155d69fa1eb1939cf5c737018242fc119480d6c
Last updated: 2026-05-31
Upstream Sources: orchestrator/config.py, migrations/, docker-compose.yml, MEMORY_LAYER.md, docs/SOURCES_OF_TRUTH.md, tests/benchmark_results/doc-alignment-regeneration/truth_set.md

## Daemon System Prompt (Actual)

Daemon is a personal AI assistant orchestration layer. The system prompt (v4) defines its identity, tool access, and subagent dispatch logic.

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

**Runtime date/time:** Native and OpenAI-compatible chat prompts use the authenticated user's
saved `timezone` (or `time_zone`), including either key under `preferences`, before
`Settings.daemon_default_timezone` (`DAEMON_DEFAULT_TIMEZONE`, default `UTC`). Invalid or
unavailable user settings fall back to the deployment default; an invalid default falls
back to UTC. IANA zones apply daylight-saving rules. Set `DAEMON_DEFAULT_TIMEZONE=Australia/Adelaide`
to retain the former deployment-wide timezone. Sources: `orchestrator/timezones.py`,
`orchestrator/daemon.py`, and the chat entry points in `orchestrator/main.py`.

---

## Web Fetch Identity

Direct HTTP fetches use a stable Daemon User-Agent defined by
`DEFAULT_FETCH_USER_AGENT` in `orchestrator/config.py`. This replaces the former
random browser identities, which identified automated requests as browsers.
The same identifier is sent on each address retry, redirect hop, and subsequent fetch.

Operators can set `DAEMON_FETCH_USER_AGENT` for an explicitly configured integration,
for example `ExampleBot (+https://example.org/contact)`. Values must be 1–512 printable
ASCII characters with no leading whitespace or control characters. Restart the service
after changing the setting. A non-default value emits a warning when the direct strategy
is initialized; the header value itself is not copied into that warning. Docker Compose
passes the setting to both the backend and worker.

Sites may return different content or reject the new default identifier. Vendor-assisted
fallbacks are disabled pending qualified bounded adapters. This identifies direct fetch traffic; it does
not add robots.txt enforcement or change SSRF protections, cookie scoping, or timeouts.
Sources: `orchestrator/services/fetch/strategies/direct.py` and
`orchestrator/services/fetch/service.py`.

---

## Account Entitlements and Routing

Commercial plans are Free, Pro, and Power. A lifetime premium trial is a separate
usage-based entitlement on Free. Central policy resolves capabilities and limits;
execution must enforce privacy-qualified routes and atomic account cost ceilings.
Commercial numbers and provider approvals are separately configurable in
`config/commercial.json` and `config/inference_policy.json`.

See [SUBSCRIPTION_ARCHITECTURE.md](SUBSCRIPTION_ARCHITECTURE.md) for the account,
trial, usage, migration, and routing contracts. Model availability alone is not
privacy approval. Unknown qualification fails closed on every plan.

---

## SSE Event Protocol

The `/chat` endpoint streams Server-Sent Events with typed frames:

| Event Type | Data Fields | Description |
|------------|-------------|-------------|
| `token` | `data.text` | Incremental text token (compat: `data.delta` accepted by bridge) |
| `thinking` | `data.content`, `id` | Model thinking/reasoning content |
| `routing` | `data.model`, `data.route_class` | Model selection notification |
| `tool_call` | `data.name`, `data.arguments` | Tool invocation |
| `tool_result` | `data.name`, `data.result` | Tool response |
| `final` | `data.text`, `data.model`, `data.finish_reason`, `data.usage` (optional), `data.timing` (optional) | Completed response |
| `error` | `data.code`, `data.message` | Error |
| `done` | `data.ok` | Stream complete |

---

## Database Schema

PostgreSQL 16 with pgvector extension. Migration inventory is maintained in `migrations/`.

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

Account entitlement schema and usage ledgers are introduced by the commercial migration in `migrations/`; see `SUBSCRIPTION_ARCHITECTURE.md` for rollout requirements.
---

## Memory Pipeline

Daemon uses a multi-stage pipeline for durable fact management. See [MEMORY_LAYER.md](../MEMORY_LAYER.md) for full architecture.

### Extraction & Dedup
Provider embedding execution is currently denied pending a qualified, budget-bounded
adapter. The configured identities below remain distinct for existing stored vectors;
fallback settings cannot grant approval. Core writes and retrieval degrade to nullable
vectors and account-scoped lexical search.

- **Extraction**: GPT-4o-mini extracts facts from conversation turns.
- **Embeddings**: Direct Voyage `voyage-4-large` (1024d) for documents, and `voyage-4-lite` (1024d) for queries, with an explicit ordered fallback chain configured by `EMBEDDING_FALLBACK_PROVIDERS`. Supported fallbacks are the corresponding Voyage models through OpenRouter (reusing `OPENROUTER_API_KEY`) and OpenAI `text-embedding-3-small`. Routed Voyage parity is unproven, so fallback vectors retain distinct `openrouter:<model>` or `openai:<model>` storage identities. Vector/BM25 retrieval only searches enabled identities with stored rows for that user, including inferred historical windows; dedup reconciles spaces lexically/by slot without cross-provider vector comparisons and excludes L0/dream rows.
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

Subagent-generated image and audio payloads are persisted only after strict
base64 decoding, a 50 MiB decoded-size ceiling, an allowlisted format check,
and matching file-signature validation. Generated-media responses use the
validated extension's media type and carry the global `nosniff` header.
All generated image, audio, TTS/sound-effect cache, and document files live in
opaque per-user filesystem namespaces derived by the server from the
authenticated UUID. Download routes keep filename-only URLs and resolve them
only within that authenticated namespace; wrong-owner and legacy flat files
return 404, and legacy files are not migrated.

### Chat rate limits

All three chat routes use atomic Redis windows before any LLM-backed work:
30 requests/minute per session, 60/minute per user, and a coarse 120/minute
per client IP. The IP ceiling intentionally applies to every chat attempt,
including malformed or unauthenticated traffic. This differs from the original
5/minute unauthenticated proposal: 120/minute avoids penalizing legitimate
households and CGNAT users while the narrower authenticated scopes protect the
LLM budget. Thresholds are configurable through the corresponding
`DAEMON_RATE_LIMIT_CHAT_*` variables.

Rate-limit rejections are structured log events. The authenticated `/status`
response exposes process-local request/rejection totals and per-endpoint ratios;
the backend emits a rate-bounded warning once at least 10 requests have a
rejection ratio above 10%.

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
- **EMBEDDING_FALLBACK_PROVIDERS**: unset by default; ordered comma-separated opt-in (`openrouter`, `openai`, or `openrouter,openai`)
- **EMBEDDING_OPENROUTER_DOCUMENT_MODEL**: voyageai/voyage-4-large
- **EMBEDDING_OPENROUTER_QUERY_MODEL**: voyageai/voyage-4-lite
- **EMBEDDING_OPENAI_FALLBACK_MODEL**: text-embedding-3-small

---

## Local Pipeline (Phase 3)

**Status: Unimplemented.**
The `/local` flag is parsed by the pre-router, but all local inference code is pending hardware acquisition (RTX 5090). Current operations are 100% cloud-based.
