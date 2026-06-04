# Authoritative Truth Set

**Task**: 3 — Extract authoritative truth set from source
**Branch**: `doc-alignment-regeneration-2026-05-29`
**Source**: `main` tip `3155d69fa1eb1939cf5c737018242fc119480d6c`
**Date**: 2026-05-31
**Sources used**: T0 (code/config/migrations/manifests) and T1 (gated curated docs) only. No T2 narrative docs used as authoritative inputs.

---

## Source Hierarchy Precedence

| Tier | Label | Examples |
|------|-------|---------|
| **T0** | Code/config/migrations/manifests | `orchestrator/config.py`, `migrations/*.sql`, `docker-compose.yml`, `orchestrator/routes/*.py` |
| **T1** | Gated curated specs | `MEMORY_LAYER.md`, `docs/FEATURE_MATRIX.md` |
| **T2** | Narrative/status docs | `ROADMAP.md`, `PROJECT_CONTEXT.md`, `TECHNICAL_SPECS.md` — used only as contrast notes |
| **T3** | Operational rollups | `CURRENT_ISSUES.md`, `TRIAGE.md` — raw log and rollup, not authoritative for facts |

---

## 1. Model Slots per Tier

Source: `orchestrator/config.py:80-156`

### FREE ($0/mo)
- **Orchestrator**: `openrouter/moonshotai/kimi-k2.5` (`config.py:82`)
- **Research**: _none_ (`config.py:84`)
- **Code**: _none_ (`config.py:85`)
- **Image**: _none_ (`config.py:86`)
- **Video**: disabled (`config.py:88`, `get_tier_config` hardcodes `tier_video_enabled=False` for free)
- **Reader**: _none_ (`config.py:89`)
- **Embeddings**: _none_ (`config.py:90`)

### STARTER ($9/mo)
- **Orchestrator**: `openrouter/moonshotai/kimi-k2.5` (`config.py:97`)
- **Orchestrator temp**: `0.7` (`config.py:98`)
- **Research**: `openrouter/anthropic/claude-3.5-sonnet` (`config.py:99`)
- **Research temp**: `0.5` (`config.py:100`)
- **Code**: `openrouter/anthropic/claude-3.5-sonnet` (`config.py:101`)
- **Code temp**: `0.3` (`config.py:102`)
- **Image**: `google/gemini-2.5-flash-image` via **OpenRouter** (`config.py:103`, `config.py:105`)
- **Image temp**: `0.8` (`config.py:104`)
- **Video provider**: `fal` (`config.py:106`)
- **Reader**: `openrouter/google/gemini-2.0-pro-exp` (`config.py:107`)
- **Reader temp**: `0.3` (`config.py:108`)
- **Embeddings**: `voyage-4-large` (`config.py:109`)

### PRO ($19/mo)
- **Orchestrator**: `openrouter/moonshotai/kimi-k2.5` (`config.py:113`)
- **Orchestrator temp**: `0.7` (`config.py:114`)
- **Research**: `openrouter/anthropic/claude-3.5-sonnet` (`config.py:115`)
- **Research temp**: `0.5` (`config.py:116`)
- **Code**: `openrouter/anthropic/claude-3.5-sonnet` (`config.py:117`)
- **Code temp**: `0.3` (`config.py:118`)
- **Image**: `google/gemini-2.5-flash-image` via **OpenRouter** (`config.py:119`, `config.py:121`)
- **Image temp**: `0.8` (`config.py:120`)
- **Video provider**: `fal` (`config.py:122`)
- **Reader**: `openrouter/google/gemini-2.0-pro-exp` (`config.py:123`)
- **Reader temp**: `0.3` (`config.py:124`)
- **Embeddings**: `voyage-4-large` (`config.py:125`)

### MAX ($29/mo)
- **Orchestrator**: `openrouter/anthropic/claude-opus-4.6` (`config.py:129`)
- **Orchestrator temp**: `0.7` (`config.py:130`)
- **Grok alternative**: `x-ai/grok-4` (`config.py:132`)
- **Grok temp**: `0.7` (`config.py:133`)
- **Research**: `openrouter/anthropic/claude-3.5-sonnet` (`config.py:134`)
- **Research temp**: `0.5` (`config.py:135`)
- **Code**: `openrouter/anthropic/claude-opus-4.6` (`config.py:136`)
- **Code temp**: `0.3` (`config.py:137`)
- **Image**: `google/gemini-2.5-flash-image` via **OpenRouter** (`config.py:138`, `config.py:140`)
- **Image temp**: `0.8` (`config.py:139`)
- **Video provider**: `fal` (`config.py:141`)
- **Reader**: `openrouter/google/gemini-2.0-pro-exp` (`config.py:142`)
- **Reader temp**: `0.3` (`config.py:143`)
- **Embeddings**: `voyage-4-large` (`config.py:144`)

### BYOK ($9/mo)
- **Orchestrator**: `openrouter/moonshotai/kimi-k2.5` (`config.py:148`)
- **Orchestrator temp**: `0.7` (`config.py:149`)
- **Research**: _none_ (`config.py:150`)
- **Code**: _none_ (`config.py:151`)
- **Image**: _none_ (`config.py:152`)
- **Video provider**: `fal` (`config.py:154`)
- **Reader**: _none_ (`config.py:155`)
- **Embeddings**: _none_ (`config.py:156`)

---

## 2. Embedding Models and Dimensions

Source: `orchestrator/config.py:225-227`

| Purpose | Model | Dimensions |
|---------|-------|------------|
| Document (memory writes) | `voyage-4-large` | 1024 |
| Query (retrieval) | `voyage-4-lite` | 1024 |

Confirmed in `MEMORY_LAYER.md:239-242` (T1 source):
```
| Document (memory writes) | `voyage-4-large` | `input_type="document"` | 1024 |
| Query (retrieval) | `voyage-4-lite` | `input_type="query"` | 1024 |
```

---

## 3. Dedup Thresholds

Source: `orchestrator/config.py:235-246`

| Scenario | Threshold | Config field |
|----------|-----------|--------------|
| Merge | **0.90** | `dedup_merge_threshold` (`config.py:235`) |
| Supersede (generic) | **0.82** | `dedup_supersede_threshold` (`config.py:239`) |
| Supersede (same slot) | **0.65** | `dedup_supersede_same_slot_threshold` (`config.py:243`) |
| Below all thresholds | < 0.65 → insert new | |

Calibration comment at `config.py:230-234`:
```
# Calibrated from `tests/results/voyage_similarity_analysis.json` for Voyage
# embeddings: within-scenario max=0.8374/p95=0.6621, cross-scenario
# max=0.8046/p95=0.6080, all-pairs p95=0.6263. Bands: merge >= 0.90,
# generic supersede >= 0.82, slot-constrained supersede >= 0.65,
# otherwise insert as new memory.
```

Confirmed in `MEMORY_LAYER.md:138-143` (T1 source):
```
| Scenario | Threshold | Action |
| Merge | ≥ 0.90 | Touch existing |
| Supersede (generic) | ≥ 0.82 | Replace existing |
| Supersede (same slot) | ≥ 0.65 | Replace within slot |
| Below | < 0.65 | Insert new |
```

---

## 4. Migration Inventory

Source: `migrations/` directory (filesystem count)

```
$ ls migrations/*.sql | wc -l
30
```

**Migration files (001–030)**:
`001_create_extensions.sql`, `002_create_users.sql`, `003_create_conversations.sql`, `004_create_messages.sql`, `005_create_memories.sql`, `006_create_extraction_log.sql`, `007_seed_default_user.sql`, `008_update_memories_source_type.sql`, `009_update_memories_constraints.sql`, `010_update_users_schema.sql`, `011_add_memories_embedding_model.sql`, `012_add_summary_updated_at.sql`, `013_extend_conversations_messages.sql`, `014_add_reasoning_columns.sql`, `015_add_reasoning_model.sql`, `016_bitemporal_memories.sql`, `017_video_credits.sql`, `018_video_credit_refund_idempotency.sql`, `019_voyage_embedding_migration.sql`, `020_create_council_sessions.sql`, `021_l0_tier_and_bm25.sql`, `022_summary_and_trust.sql`, `023_add_consolidation_source_type.sql`, `024_extend_memories_for_reasoning.sql`, `025_create_retrieval_log.sql`, `026_create_entities.sql`, `027_create_dream_log.sql`, `028_skill_projection.sql`, `029_skill_consolidation_nudge.sql`, `030_add_advisor_traces.sql`

**Count**: 30
**Latest migration**: `030_add_advisor_traces.sql`

---

## 5. Providers

### Image Generation
- **Provider**: `xai` — `XAIImagineClient` from `providers/xai_imagine.py`
- Source: `orchestrator/subagents/image.py:17`
- xAI handles image generation via `XAIImageProvider.generate_image()` at `image.py:240-280`

### Video Generation — xAI and fal (Kling)
- **xAI**: `providers/xai_imagine.py:138` has `generate_video()` method; `image.py:282-298` exposes it via `XAIImageProvider.generate_video()`. xAI supports video generation (text-to-video, max 15s per `xai_imagine.py:161`).
- **fal (Kling)**: `providers/fal_kling.py`; `image.py:301-350` exposes it via `FalKlingProvider.generate_video()`. Default for all tiers.
- Video provider selection: `image.py:619-631` dynamically creates either `XAIImageProvider` or `FalKlingProvider` based on `video_provider_name` context.

### Valid Video Providers
Source: `orchestrator/routes/video_credits.py:158`:
```python
VALID_VIDEO_PROVIDERS = {"xai", "fal"}
```
Both providers are active — xAI for video, fal for video.

### Sora
- **Status**: Deleted/shut down. No Sora provider implementation exists.
- `.env.example:79-85` has stale comments about Sora API key configuration
- `config/video_pricing.py` docstring references Sora but only Kling/fal is implemented

### Provider List
Source: `config.py:456-467` (`list_available_providers()` method):
- Primary: `openrouter` (`config.py:458`)
- Custom providers: discovered dynamically via `PROVIDER_{name}_BASE_URL` env vars

---

## 6. Routes and Endpoints

### Router-registered routes (via `app.include_router` at `main.py:1961-1967`)

| Prefix | File | Registered at |
|--------|------|-------------|
| `/conversations` | `routes/conversations.py` | `main.py:1961` |
| `/memories` | `routes/memories.py` | `main.py:1962` |
| `/skills` | `routes/skills.py` | `main.py:1963` |
| `/status` | `routes/system.py` | `main.py:1964` |
| `/users` | `routes/users.py` | `main.py:1965` |
| `/video-credits` | `routes/video_credits.py` | `main.py:1966` |
| image API router | (from subagent) | `main.py:1967` |

### Direct `@app` routes in `main.py`

| Method | Path | Line | Description |
|--------|------|------|-------------|
| GET | `/health` | 643 | Simple health check |
| POST | `/v1/tools/test` | 656 | Tool-calling test endpoint |
| GET | `/providers` | 702 | List available LLM providers (requires API key) |
| GET | `/api/models` | 719 | Redirect to `/v1/models` (Open WebUI compat) |
| GET | `/models` | 727 | Redirect to `/v1/models` |
| GET | `/v1/models` | 735 | OpenAI-compatible model list |
| GET | `/v1/catalog` | 817 | Extended model catalog |
| POST | `/chat/completions` | 850 | OpenAI-compatible chat completions |
| POST | `/v1/chat/completions` | 861 | OpenAI-compatible chat completions (v1) |
| GET | `/generated-images/{filename}` | 1100 | Serve generated images |
| GET | `/generated-audio/{filename}` | 1116 | Serve generated audio |
| GET | `/generated-files/{filename}` | 1134 | Serve generated files |
| POST | `/tts` | 1158 | ElevenLabs TTS |
| GET | `/audio/token` | 1240 | Scoped TTS token |
| GET | `/audio/scribe-token` | 1283 | Scoped STT token |
| POST | `/stt` | 1316 | ElevenLabs STT |
| POST | `/sound-effects` | 1362 | ElevenLabs sound effects |
| POST | `/chat` | 1407 | Native Daemon SSE chat endpoint |

### Skills sub-endpoints (from `routes/skills.py`)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| GET | `/skills` | 122 | List all skills |
| GET | `/skills/{skill_id}` | 149 | Get skill detail |
| POST | `/skills` | 164 | Create skill |
| POST | `/skills/upload` | 185 | Upload skill from file |
| PUT | `/skills/{skill_id}` | 219 | Update skill |
| PATCH | `/skills/{skill_id}/enabled` | 241 | Enable/disable skill |
| DELETE | `/skills/{skill_id}` | 263 | Delete skill |
| PATCH | `/skills/{skill_id}/autonomous-edit` | 277 | Toggle autonomous edit |
| POST | `/skills/{skill_id}/pending-update` | 303 | Apply/dismiss pending update |
| GET | `/skills/{skill_id}/download` | 350 | Download skill as markdown |
| POST | `/skills/admin/sync` | 381 | Admin sync from repo (requires admin API key) |

### Memories sub-endpoints (from `routes/memories.py`)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| GET | `/memories` | 63 | List memories |
| POST | `/memories/export` | 90 | Export memories |
| POST | `/memories/import` | 103 | Import memories |
| POST | `/memories/reembed` | 116 | Re-embed memories |
| DELETE | `/memories` | 189 | Delete all memories |
| GET | `/memories/{memory_id}` | 208 | Get single memory |
| POST | `/memories` | 225 | Create memory |
| PATCH | `/memories/{memory_id}` | 248 | Update memory |
| DELETE | `/memories/{memory_id}` | 262 | Delete memory |
| POST | `/memories/{memory_id}/confirm` | 276 | Confirm/reject memory |
| POST | `/memories/consolidate` | 299 | Trigger consolidation |
| POST | `/memories/dream` | 351 | Trigger dreaming (admin-only) |

### Conversations sub-endpoints (from `routes/conversations.py`)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| POST | `/conversations` | 100 | Create conversation |
| GET | `/conversations` | 127 | List conversations |
| GET | `/conversations/{conversation_id}` | 148 | Get conversation with messages |
| PATCH | `/conversations/{conversation_id}` | 169 | Update conversation |
| DELETE | `/conversations/{conversation_id}` | 190 | Delete conversation |

### Users sub-endpoints (from `routes/users.py`)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| GET | `/users/me/settings` | 20 | Get user settings |
| PATCH | `/users/me/settings` | 42 | Update user settings |
| GET | `/users/me/settings/presets` | 71 | List personality presets |

### Video credits sub-endpoints (from `routes/video_credits.py`)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| GET | `/video-credits/balance` | 75 | Get credit balance |
| GET | `/video-credits/transactions` | 92 | List transactions |
| POST | `/video-credits/grant` | 127 | Admin grant credits |
| GET | `/video-credits/estimate` | 161 | Estimate video cost |

### System sub-endpoints (from `routes/system.py`)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| GET | `/status` | 11 | Get system status (db, redis, memory, embedding retry state) |

---

## 7. Environment Variables

Source: `config.py`, `docker-compose.yml`, `.env.example`

### Core
| Variable | Default | Source |
|----------|---------|--------|
| `ENV` | `dev` | `config.py:58` |
| `LOG_LEVEL` | `INFO` | `config.py:59` |
| `DAEMON_API_KEY` | `None` | `config.py:62` |
| `DAEMON_ADMIN_API_KEY` | `None` | `config.py:63` |
| `DEFAULT_PROVIDER` | `openrouter` | `config.py:66` |
| `DEFAULT_TIER` | `pro` | `config.py:78` |
| `REQUEST_TIMEOUT_S` | `90.0` | `config.py:69` |
| `STREAM_PING_INTERVAL_S` | `15.0` | `config.py:70` |
| `MOCK_LLM` | `False` | `config.py:74` |

### Providers
| Variable | Source |
|----------|--------|
| `OPENROUTER_API_KEY` | `config.py:171` |
| `OPENROUTER_BASE_URL` | `config.py:172` (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_API_KEY` | `config.py:248` |
| `XAI_API_KEY` | `config.py:222` |
| `VOYAGE_API_KEY` | `config.py:224` |
| `BRAVE_API_KEY` | `config.py:207` |
| `ELEVENLABS_API_KEY` | Not in config.py directly; used by audio subagent |

### Memory Layer
| Variable | Default | Source |
|----------|---------|--------|
| `DATABASE_URL` | `None` | `config.py:251` |
| `REDIS_URL` | `None` | `config.py:252` |
| `DAEMON_ENCRYPTION_KEY` | `None` | `config.py:253` |
| `EMBEDDING_DOCUMENT_MODEL` | `voyage-4-large` | `config.py:225` |
| `EMBEDDING_QUERY_MODEL` | `voyage-4-lite` | `config.py:226` |
| `EMBEDDING_DIMENSIONS` | `1024` | `config.py:227` |
| `DEDUP_MERGE_THRESHOLD` | `0.90` | `config.py:235` |
| `DEDUP_SUPERSEDE_THRESHOLD` | `0.82` | `config.py:239` |
| `DEDUP_SUPERSEDE_SAME_SLOT_THRESHOLD` | `0.65` | `config.py:243` |
| `CONSOLIDATION_ENABLED` | `True` | `config.py:275` |
| `CONSOLIDATION_INTERVAL_DAYS` | `7` | `config.py:276` |

### Fetch Service
| Variable | Default | Source |
|----------|---------|--------|
| `JINA_API_KEY` | `None` | `config.py:211` |
| `FETCH_CACHE_TTL_SECONDS` | `86400` | `config.py:213` |
| `FETCH_MIN_CONTENT_LENGTH` | `200` | `config.py:215` |
| `CRAWL4AI_URL` | `http://crawl4ai:11235` | `config.py:217` |
| `FETCH_BLOCKED_DOMAINS` | `""` | `config.py:219` |

### Video
| Variable | Source |
|----------|--------|
| `FAL_KEY` | `docker-compose.yml:32`, not directly in config.py |
| `TIER_PRO_VIDEO_COST_PER_SEC` | Not in config.py; uses `config/video_pricing.py` |
| `TIER_MAX_VIDEO_COST_PER_SEC` | Not in config.py; uses `config/video_pricing.py` |

### Background/Reasoning
| Variable | Default | Source |
|----------|---------|--------|
| `TITLE_MODEL` | `openrouter/openai/gpt-4o-mini` | `config.py:256` |
| `BACKGROUND_REASONING_MODEL` | `openrouter/deepseek/deepseek-chat` | `config.py:260` |
| `DREAMING_ENABLED` | `True` | `config.py:263` |
| `DREAM_SCHEDULE_HOUR` | `3` | `config.py:264` |
| `DREAM_MIN_CLUSTER_SIZE` | `5` | `config.py:266` |
| `RETRIEVAL_LOGGING_ENABLED` | `False` | `config.py:270` |

### Custom Provider Pattern
Providers can be added via env vars following pattern `PROVIDER_{NAME}_BASE_URL`, `PROVIDER_{NAME}_API_KEY`, `PROVIDER_{NAME}_MODEL`, `PROVIDER_{NAME}_REQUIRES_AUTH` — source `config.py:393-405`.

---

## 8. Tier Pricing

Source: `config.py:420-454` (`list_available_tiers()` method)

| Tier | Price | Orchestrator model |
|------|-------|--------------------|
| `free` | $0/mo | `openrouter/moonshotai/kimi-k2.5` |
| `starter` | $9/mo | `openrouter/moonshotai/kimi-k2.5` |
| `pro` | $19/mo | `openrouter/moonshotai/kimi-k2.5` |
| `max` | $29/mo | `openrouter/anthropic/claude-opus-4.6` |
| `byok` | $9/mo | `openrouter/moonshotai/kimi-k2.5` |

---

## 9. Docker Compose Services

Source: `docker-compose.yml:1-150`

| Service | Lines | Description |
|---------|-------|-------------|
| `migrate` | 2-16 | One-shot migration runner (`python scripts/migrate.py`) |
| `backend` | 18-46 | FastAPI backend (uvicorn on port 8000) |
| `worker` | 48-76 | arq background job processor |
| `frontend` | 78-95 | Next.js 16 dev server (port 3000) |
| `postgres` | 97-113 | pgvector/pg16 (port 5432) |
| `redis` | 115-127 | Redis 7 Alpine (port 6379) |
| `crawl4ai` | 129-142 | crawl4ai web scraping service |

**Total**: 7 services (6 long-running + 1 one-shot migrate)

---

## 10. Feature Matrix — Key Feature States

Source: `docs/FEATURE_MATRIX.md` (T1 — PR-gated source of truth)

| Feature | State |
|---------|-------|
| Chat Streaming + Reconnect | Cross-client stable |
| File Upload | Cross-client stable |
| Model Discovery | Cross-client stable |
| Typed SSE Event Protocol | Cross-client stable |
| OpenAI Chat Completions API | Backend stable |
| Conversations (CRUD) | Cross-client stable |
| Memory Read/Write/Correction/Export/Clear | Cross-client stable or Backend stable |
| @research | Cross-client stable |
| @image (images) | Cross-client stable |
| @image Video Generation | Cross-client stable |
| @audio (ElevenLabs) | Cross-client stable |
| Document file generation (`generate_document` tool) | Cross-client stable |
| @code | **Web experimental** (NOT IMPLEMENTED) |
| @reader | **Web experimental** (NOT IMPLEMENTED) |
| Skill Management CRUD | Cross-client stable |
| ElevenLabs TTS/STT | Cross-client stable |
| Council Deliberation | Cross-client stable |
| Studio Image Generation | Cross-client stable |
| Studio Video Generation | Cross-client stable |
| Video Credit Balance | Cross-client stable |
| BYOK | Cross-client stable |
| Local Pipeline Routing | **Not started** |
| Projects Page | **Web experimental** |
| PWA Service Worker | Platform-specific permanent |

---

## 11. Video Pricing

Source: `config/video_pricing.py`

**Note**: The `estimate_cost()` function uses `int()` truncation on the per-second × 20 calculation, producing integer credit values.

### fal (Kling) — per-second credit cost (after integer truncation)
- **O3 Pro without audio**: `int(0.112 * 20) = 2` credits/sec → 5s=10, 10s=20, 15s=30 (`video_pricing.py:94`)
- **O3 Pro with audio**: `int(0.14 * 20) = 2` credits/sec → 5s=10, 10s=20, 15s=30 (`video_pricing.py:92`) — same rate as no-audio
- **V3 Pro without audio**: `int(0.112 * 20) = 2` credits/sec → 5s=10, 10s=20, 15s=30 (`video_pricing.py:89`)
- **V3 Pro with audio**: `int(0.196 * 20) = 3` credits/sec → 5s=15, 10s=30, 15s=45 (`video_pricing.py:87`)

**Note**: There is no separate "voice control" pricing tier — `audio_enabled: bool` is the only flag. The "Voice control" comment at `video_pricing.py:87` is a descriptive label for the v3-pro + audio_enabled rate.

### xAI — fixed duration-based pricing (credits)
- 5s: 5 credits, 10s: 10 credits, 15s: 15 credits, 20s: 20 credits, 30s: 30 credits (`video_pricing.py:49-55`)

### Tier discounts
- **Pro**: full price (discount = 1.0) (`video_pricing.py:34`)
- **Max**: 0.8× (`video_pricing.py:35`)
- **BYOK**: 0.0× (no discount) (`video_pricing.py:36`)

---

## 12. Current Memory Documentation Authority

Source: `MEMORY_LAYER.md` (T1 — gated curated spec)

**Designated as authoritative T1 source** for all memory-related facts including:
- Pipeline stages and order
- Embedding models and dimensions
- Dedup thresholds (verified above)
- Encryption approach (Fernet/AES-256-GCM)
- Table schemas (conversations, messages, memories, memory_extraction_log)
- Retrieval scoring formula: `0.5 × vector_sim + 0.3 × bm25_normalized + 0.2 × recency × confidence × trust`
- BM25 + vector hybrid search
- Trust signal system
- L0/L1/L2 tiering model
- Consolidation clustering threshold (0.65)
- Background job types (extraction, summary, consolidation, dreaming)

**Known caveats** (from `MEMORY_LAYER.md:285-302`):
1. Selective assistant extraction (you/your filter) — median precision 0.9677, recall 0.9667, but scenario-level variance exists
2. LongMemEval IE-assistant not independently verified (blocked by host DB resolution)
3. Consolidation not independently benchmarked against ground-truth dataset
4. BM25 requires `content_tsv` — if decryption fails or `content_tsv` is unpopulated, BM25 misses the memory

---

## 13. Subagent Implementations

Source: `orchestrator/subagents/` directory

| Subagent | File | Status |
|----------|------|--------|
| `@research` | `research.py` | Implemented |
| `@image` | `image.py` | Implemented (images via xAI; video via xAI and fal/Kling) |
| `@audio` | `audio.py` | Implemented |
| `generate_document` | `tools/document.py` | Implemented (deterministic tool; replaced LLM-codegen subagent) |
| `@code` | — | **NOT IMPLEMENTED** (reserved; `config.py:42` slot exists) |
| `@reader` | — | **NOT IMPLEMENTED** (reserved; `config.py:44` slot exists) |

---

## Source Conflicts / Oracle Review Queue

### CONFLICT-1: `/providers` endpoint exists (corrected from Task 2 audit)
- **Task 2 audit said**: `/providers` is "not implemented" (drift)
- **Actual source**: `/providers` IS implemented at `main.py:702-713`
- **Requires**: Task 2 audit should be amended; this is NOT a drift item — it is zero-drift
- **Verdict**: Source confirms the doc claim; CONFLICT resolved

### CONFLICT-2: `/health` endpoint exists in addition to `/status`
- **Task 2 audit said**: `/status` is the endpoint
- **Actual source**: Both exist — `/health` at `main.py:643` (simple ok/degraded) AND `/status` at `routes/system.py:11` (detailed db/redis/memory/embedding state)
- **Requires**: Two distinct health endpoints exist with different detail levels; both are correct

### CONFLICT-3: VALID_VIDEO_PROVIDERS = {"xai", "fal"} — Both providers have video (CORRECTED)
- **Source**: `video_credits.py:158` — `VALID_VIDEO_PROVIDERS = {"xai", "fal"}`
- **Source**: xAI video IS implemented — `providers/xai_imagine.py:138` has `generate_video()`, exposed via `image.py:282-298`
- **Source**: fal video IS implemented — `image.py:301-350` (`FalKlingProvider`)
- **Previous claim** (false): xAI had no video implementation
- **Verdict**: CONFLICT-3 is NOT a conflict — `VALID_VIDEO_PROVIDERS={"xai","fal"}` is accurate. Both providers support video generation.

### CONFLICT-4: `.env.example` has stale Sora comments
- **Source**: `.env.example:79-85` — comments about Sora API key
- **Source**: Sora API is shut down; no Sora provider exists in `image.py`
- **Verdict**: Stale env var comments — should be cleaned up (out of scope for Task 3 but logged for Oracle)

### CONFLICT-5: Tier model naming inconsistency
- `.env.example:23` has `TIER1_MODELS=claude-opus-45,kimi-k2-5,kimi-k2-thinking`
- `config.py` does not define `TIER1_MODELS` or `AUTO_ROUTING_MODELS`
- These are OpenRouter recommendation labels, not Daemon config — this is not a conflict, just `.env.example` documenting OpenRouter's model recommendation system
