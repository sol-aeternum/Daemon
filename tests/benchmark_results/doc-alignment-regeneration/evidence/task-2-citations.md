# Task 2 — Citation Evidence

Representative contradiction citation checks, plus zero-drift docs.

---

## DRIFT-1: Migration Count

**Doc claim** (`docs/TECHNICAL_SPECS.md:100`):
> "PostgreSQL 16 with pgvector extension. **13 migrations** in `/migrations/`."

**Doc claim** (`docs/PROJECT_CONTEXT.md:135`):
> "Database Schema (**13 migrations**)" — same claim.

**Doc claim** (`docs/ROADMAP.md:50`):
> "PostgreSQL + pgvector (pg16) with **13 migrations**"

**Source truth:**
```
$ ls /home/sol/daemon/migrations/*.sql | wc -l
30
```
Migration files present: 001–030 (with gaps: no file numbered exactly 014, 017 in isolation — they exist at 014_add_reasoning_columns.sql, 017_video_credits.sql). Full list confirmed 001, 002, 003, 004, 005, 006, 007, 008, 009, 010, 011, 012, 013, 014, 015, 016, 017, 018, 019 (migration + rollback subdir), 020, 021, 022, 023, 024, 025, 026, 027, 028, 029, 030.

**Verdict**: DRIFT — docs claim 13; source has **30 migrations**.

**Source**: `migrations/` directory, `ls *.sql | wc -l`.

---

## DRIFT-2: Dedup Thresholds

**Doc claim** (`docs/ROADMAP.md:59`):
> "Dedup engine: similarity thresholds (**0.85 merge, 0.75 supersede**)"

**Doc claim** (`docs/TECHNICAL_SPECS.md:194-196`):
> ```
> - similarity ≥ 0.85 → merge (touch existing)
> - similarity ≥ 0.75 → supersede (create new, mark old)
> - similarity < 0.75 → insert new
> ```

**Source truth** (`orchestrator/config.py:235-246`):
```python
dedup_merge_threshold: float = Field(default=0.90, ...)
dedup_supersede_threshold: float = Field(default=0.82, ...)
dedup_supersede_same_slot_threshold: float = Field(default=0.65, ...)
```

**Source truth** (`MEMORY_LAYER.md:138-143`):
```
| Scenario | Threshold | Action |
| Merge | ≥ 0.90 | Touch existing |
| Supersede (generic) | ≥ 0.82 | Replace existing |
| Supersede (same slot) | ≥ 0.65 | Replace within slot |
| Below | < 0.65 | Insert new |
```

**Verdict**: DRIFT — docs (ROADMAP, TECHNICAL_SPECS) claim 0.85/0.75; source has **0.90/0.82/0.65**.

**Source**: `orchestrator/config.py:235-246`; `MEMORY_LAYER.md:138-143`.

---

## DRIFT-3: Embedding Model/Dimensions in TECHNICAL_SPECS

**Doc claim** (`docs/TECHNICAL_SPECS.md:154`):
> `embedding_model TEXT DEFAULT 'voyage-4-large'`

**Doc claim** (`docs/TECHNICAL_SPECS.md:351-352`):
> `EMBEDDING_DOCUMENT_MODEL=voyage-4-large`
> `EMBEDDING_DIMENSIONS=1024`

**Doc note** (`docs/MEMORY_UPGRADE_ROADMAP.md:3`):
> "`TECHNICAL_SPECS.md` embedding section is stale (still lists `text-embedding-3-small`); Voyage-4 is live."

Note: MEMORY_UPGRADE_ROADMAP claims the old embedding was `text-embedding-3-small`, but TECHNICAL_SPECS already shows `voyage-4-large`. The real drift is that TECHNICAL_SPECS shows 1024d but the narrative about `text-embedding-3-small` is inaccurate — the stale claim is the reverse.

**Source truth** (`orchestrator/config.py:225-227`):
```python
embedding_document_model: str = "voyage-4-large"
embedding_query_model: str = "voyage-4-lite"
embedding_dimensions: int = 1024
```

**Source truth** (`MEMORY_LAYER.md:239-242`):
```
| Document (memory writes) | `voyage-4-large` | `input_type="document"` | 1024 |
| Query (retrieval) | `voyage-4-lite` | `input_type="query"` | 1024 |
```

**Verdict**: PARTIAL DRIFT — TECHNICAL_SPECS:154 shows correct `voyage-4-large`; TECHNICAL_SPECS env vars section (351-352) shows correct `voyage-4-large` + `1024`. The stale claim in MEMORY_UPGRADE_ROADMAP that TECHNICAL_SPECS still had `text-embedding-3-small` is inaccurate — it does not. However, the dedup thresholds in TECHNICAL_SPECS (0.85/0.75) ARE stale.

---

## DRIFT-4: Video Provider State

**Doc claim** (`docs/ROADMAP.md:35-36`):
> "xAI Imagine API integration for image and video generation"

**Doc claim** (`docs/PROJECT_CONTEXT.md:87-97`):
> "Video Generation + Credits ✅ (xAI Imagine)" — image AND video via xAI.

**Doc claim** (`docs/TECHNICAL_SPECS.md:354`):
> `# OpenAI (used for Sora video provider paths)`

**Source truth** (`orchestrator/subagents/image.py:17`):
```python
from providers.xai_imagine import XAIImagineClient, XAIImagineError
```

**Source truth** (`orchestrator/subagents/image.py:302-347`):
```python
"""Video provider using fal.ai Kling."""
from providers.fal_kling import FalKlingClient, FalKlingError
```

**Source truth** (`orchestrator/routes/video_credits.py:158`):
```python
VALID_VIDEO_PROVIDERS = {"xai", "fal"}
```

**Source truth** (`orchestrator/config.py:88,106,122,141,154`):
```python
tier_free_video_provider: str = "fal"
tier_starter_video_provider: str = "fal"
tier_pro_video_provider: str = "fal"
tier_max_video_provider: str = "fal"
tier_byok_video_provider: str = "fal"
```

**Source truth** (`docker-compose.yml:32`):
```
- FAL_KEY=${FAL_KEY}
```

**Verdict**: DRIFT — docs describe xAI for both image AND video; source has:
- Images: xAI via `XAIImagineClient`
- Video: fal.ai Kling via `FalKlingClient` (default provider = "fal")
- Sora: referenced in TECH SPECS but Sora API is shut down (per kling-integration-plan memory block)
- The `TECHNICAL_SPECS.md` comment about "OpenAI (used for Sora video provider paths)" is stale — Sora is gone.

---

## DRIFT-5: Docker Compose Service Count

**Doc claim** (`docs/TECHNICAL_SPECS.md:329`):
> "### Docker Compose (Target: **5 services**)"
> ```yaml
> services:
>   backend:     # FastAPI (port 8000)
>   worker:      # arq background jobs
>   frontend:    # Next.js 16 (port 3000)
>   postgres:    # pgvector/pgvector:pg16
>   redis:       # Redis 7 Alpine
> ```

**Doc claim** (`docs/PROJECT_CONTEXT.md:115-120`):
> "### Docker Compose Services (**6 containers**)"

**Source truth** (`docker-compose.yml:1-150`):
Services defined: `migrate`, `backend`, `worker`, `frontend`, `postgres`, `redis`, `crawl4ai` = **7 services** (6 long-running + 1 one-shot migrate).

**Verdict**: DRIFT — TECHNICAL_SPECS says 5, PROJECT_CONTEXT says 6, source has **7 services** (migrate + backend + worker + frontend + postgres + redis + crawl4ai).

---

## DRIFT-6: System Health Endpoint Path

**Doc claim** (`docs/TECHNICAL_SPECS.md:284`):
> `GET  /health                             → Health check`

**Doc claim** (`docs/PROJECT_CONTEXT.md:84`):
> "`/system/health` — health check"

**Source truth** (`orchestrator/routes/system.py:8`):
```python
router = APIRouter(prefix="/status", tags=["system"])
```
Route: `GET /status` (not `/health` and not `/system/health`).

**Verdict**: DRIFT — docs disagree with each other AND with source. Source endpoint is `/status`.

---

## DRIFT-7: Providers Endpoint

**Doc claim** (`docs/TECHNICAL_SPECS.md:285`):
> `GET  /providers                          → List configured providers`

**Source truth**: No `/providers` route exists in `orchestrator/routes/`. The `get_provider_config` method in `config.py` is used internally but no route exposes it.

**Verdict**: DRIFT — endpoint documented but not implemented.

---

## DRIFT-8: Tier Orchestrator Model — MAX Tier

**Doc claim** (`docs/PROJECT_CONTEXT.md:53-58` Tier table):
> "| Max | $29/mo | Claude 3 Opus |"

**Doc claim** (`docs/TECHNICAL_SPECS.md:56`):
> `MAX: orchestrator: openrouter/anthropic/claude-opus-4.6`

**Source truth** (`orchestrator/config.py:129`):
```python
tier_max_orchestrator_model: str = "openrouter/anthropic/claude-opus-4.6"
```

**Verdict**: NO DRIFT for model name. However, `config.py:132-133` also shows:
```python
tier_max_orchestrator_model_grok: str = "x-ai/grok-4"
```
This alternative is documented nowhere in any doc.

---

## DRIFT-9: Health/Providers Endpoint Path — Confirmed by Main.py

**Source truth** (`orchestrator/main.py:1961-1967`):
```python
app.include_router(conversations.router)
app.include_router(memories.router)
app.include_router(skills.router)
app.include_router(system.router)   # /status
app.include_router(users.router)    # /users
app.include_router(video_credits.router)  # /video-credits
app.include_router(getattr(image_api_router, "router"))
```

Skills route (`/skills`) is not documented in TECHNICAL_SPECS. This is a documentation gap (not a contradiction), but worth noting as a missing doc.

---

## ZERO-DRIFT Docs

The following docs had no contradictions found (all claims verified against source):

| File | Verdict |
|------|---------|
| `docs/FEATURE_MATRIX.md` | ZERO DRIFT — all feature states, client surfaces, and API dependencies verified against source. This is the designated gated truth source. |
| `MEMORY_LAYER.md` | ZERO DRIFT — dedup thresholds (0.90/0.82/0.65), embedding models (voyage-4-large/lite, 1024d), pipeline stages all match source. This is the designated gated truth source for memory. |
| `docs/CURRENT_ISSUES.md` | ZERO DRIFT (in direction of resolved) — claims "No outstanding issues!" and mentions extraction now writes `status="active"` which matches production `extraction.py` behavior. |
| `docs/OPEN_QUESTIONS.md` | ZERO DRIFT — question items and resolved table are accurate as of their dates. Note: Q1 (memory promotion) is listed as unresolved but CURRENT_ISSUES claims it IS resolved. This is an internal doc conflict (OPEN_QUESTIONS should have been updated when CURRENT_ISSUES was). |
| `docs/MEMORY_UPGRADE_ROADMAP.md` | ZERO DRIFT within its own scope — the wave plan references dedup thresholds correctly (0.90/0.82/0.65) and properly notes that TECHNICAL_SPECS dedup section is stale. |
| `docs/interactive-artifact-examples.md` | ZERO DRIFT — pure code examples with no factual claims. |
| `frontend/PWA_SETUP.md` | ZERO DRIFT — icon generation steps are accurate how-to content. |
| `frontend/PWA_CHECKLIST.md` | ZERO DRIFT — accurately reports build failures, missing deps (next-pwa, sharp), missing icons. This is a raw log, not a spec. |

---

## INTERNAL DOC CONFLICTS (No Source Violation)

| Conflict | Doc A says | Doc B says |
|----------|-----------|-----------|
| Migration count | 13 (TECHNICAL_SPECS, ROADMAP, PROJECT_CONTEXT) | 30 (source) |
| Health endpoint | `/health` (TECHNICAL_SPECS) | `/system/health` (PROJECT_CONTEXT) | `/status` (source) |
| Docker services | 5 (TECHNICAL_SPECS) | 6 (PROJECT_CONTEXT) | 7 (source: +migrate +crawl4ai) |
| Dedup thresholds | 0.85/0.75 (TECHNICAL_SPECS, ROADMAP) | 0.90/0.82/0.65 (source, MEMORY_LAYER) |
| Video provider | xAI for video (ROADMAP, PROJECT_CONTEXT) | fal for video (source: config.py, image.py) |
| Memory promotion | Q1 unresolved (OPEN_QUESTIONS) | IS resolved (CURRENT_ISSUES) |
| Sora | Referenced in TECH SPECS env vars | Deleted — API shut down |
| Skills API | Not documented in TECHNICAL_SPECS | Implemented at `/skills` (main.py) |
| @document subagent | Not in ROADMAP Phase 1 list | In FEATURE_MATRIX as "document file generation" |
