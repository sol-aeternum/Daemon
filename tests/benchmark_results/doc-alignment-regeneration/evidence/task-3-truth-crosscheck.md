# Task 3 — Truth Cross-Check Evidence

Programmatic extraction and verification of key config values from source.

---

## Cross-Check 1: Migration Count

**Command**:
```bash
ls /home/sol/daemon/migrations/*.sql | wc -l
ls /home/sol/daemon/migrations/*.sql | sort -V | tail -5
```

**Output**:
```
30
/home/sol/daemon/migrations/026_create_entities.sql
/home/sol/daemon/migrations/027_create_dream_log.sql
/home/sol/daemon/migrations/028_skill_projection.sql
/home/sol/daemon/migrations/029_skill_consolidation_nudge.sql
/home/sol/daemon/migrations/030_add_advisor_traces.sql
```

**truth_set.md entry**: Migration count = 30, latest = `030_add_advisor_traces.sql`
**Result**: ✅ MATCH

---

## Cross-Check 2: Dedup Thresholds via Python Extraction

**Command**:
```python
import sys
sys.path.insert(0, '/home/sol/daemon')
from orchestrator.config import get_settings
s = get_settings()
print(f"merge={s.dedup_merge_threshold}")
print(f"supersede={s.dedup_supersede_threshold}")
print(f"same_slot={s.dedup_supersede_same_slot_threshold}")
```

**Output** (actual from `python3 -c`):
```
merge=0.9
supersede=0.82
same_slot=0.65
```

**truth_set.md entry**: merge=0.90, supersede=0.82, same_slot=0.65
**Result**: ✅ MATCH

---

## Cross-Check 3: Embedding Models via Python Extraction

**Command**:
```python
import sys
sys.path.insert(0, '/home/sol/daemon')
from orchestrator.config import get_settings
s = get_settings()
print(f"document_model={s.embedding_document_model}")
print(f"query_model={s.embedding_query_model}")
print(f"dimensions={s.embedding_dimensions}")
```

**Output** (actual from `python3 -c`):
```
document_model=voyage-4-large
query_model=voyage-4-lite
dimensions=1024
```

**truth_set.md entry**: document_model=voyage-4-large, query_model=voyage-4-lite, dimensions=1024
**Result**: ✅ MATCH

---

## Cross-Check 4: Tier Model Assignments via Grep

**Command**:
```bash
grep -n "tier_pro_orchestrator_model\|tier_max_orchestrator_model\|tier_free_orchestrator_model\|tier_byok_orchestrator_model\|tier_starter_orchestrator_model" /home/sol/daemon/orchestrator/config.py
```

**Output**:
```
82:    tier_free_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
97:    tier_starter_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
113:    tier_pro_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
129:    tier_max_orchestrator_model: str = "openrouter/anthropic/claude-opus-4.6"
148:    tier_byok_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
```

**truth_set.md entry**: FREE=openrouter/moonshotai/kimi-k2.5, STARTER=openrouter/moonshotai/kimi-k2.5, PRO=openrouter/moonshotai/kimi-k2.5, MAX=openrouter/anthropic/claude-opus-4.6, BYOK=openrouter/moonshotai/kimi-k2.5
**Result**: ✅ MATCH

---

## Cross-Check 5: Video Provider Defaults via Grep

**Command**:
```bash
grep -n "tier_.*_video_provider" /home/sol/daemon/orchestrator/config.py
```

**Output**:
```
88:    tier_free_video_provider: str = "fal"
106:    tier_starter_video_provider: str = "fal"
122:    tier_pro_video_provider: str = "fal"
141:    tier_max_video_provider: str = "fal"
154:    tier_byok_video_provider: str = "fal"
```

**truth_set.md entry**: All tiers default to `fal` (fal.ai/Kling)
**Result**: ✅ MATCH

---

## Cross-Check 6: Docker Compose Service Count

**Command**:
```bash
grep -n "^  [a-z]" /home/sol/daemon/docker-compose.yml | head -20
```

**Output**:
```
  migrate:
  backend:
  worker:
  frontend:
  postgres:
  redis:
  crawl4ai:
```

**truth_set.md entry**: 7 services (migrate, backend, worker, frontend, postgres, redis, crawl4ai)
**Result**: ✅ MATCH

---

## Cross-Check 7: API Router Prefixes

**Command**:
```bash
grep -n "router = APIRouter(prefix=" /home/sol/daemon/orchestrator/routes/*.py
```

**Output**:
```
system.py:8:router = APIRouter(prefix="/status", tags=["system"])
users.py:11:router = APIRouter(prefix="/users", tags=["users"])
conversations.py:11:router = APIRouter(prefix="/conversations", tags=["conversations"])
memories.py:12:router = APIRouter(prefix="/memories", tags=["memories"])
video_credits.py:13:router = APIRouter(prefix="/video-credits", tags=["video_credits"])
skills.py:29:router = APIRouter(prefix="/skills", tags=["skills"])
```

**truth_set.md entry**: /status, /users, /conversations, /memories, /video-credits, /skills
**Result**: ✅ MATCH

---

## Cross-Check 8: Provider Registration in main.py

**Command**:
```bash
grep -n "include_router" /home/sol/daemon/orchestrator/main.py
```

**Output**:
```
1961:app.include_router(conversations.router)
1962:app.include_router(memories.router)
1963:app.include_router(skills.router)
1964:app.include_router(system.router)
1965:app.include_router(users.router)
1966:app.include_router(video_credits.router)
1967:app.include_router(getattr(image_api_router, "router"))
```

**truth_set.md entry**: All 6 routers plus image_api_router registered
**Result**: ✅ MATCH

---

## Cross-Check 9: Tier Pricing

**Command**:
```bash
grep -n "price" /home/sol/daemon/orchestrator/config.py | head -10
```

**Output** (from `list_available_tiers()` at lines 420-454):
```
427:                "price": 0,
433:                "price": 9,
438:                "price": 19,
444:                "price": 29,
450:                "price": 9,
```

**truth_set.md entry**: free=$0, starter=$9, pro=$19, max=$29, byok=$9
**Result**: ✅ MATCH

---

## Cross-Check 10: Video Credit Valid Providers

**Command**:
```bash
grep -n "VALID_VIDEO_PROVIDERS" /home/sol/daemon/orchestrator/routes/video_credits.py
```

**Output**:
```
158:VALID_VIDEO_PROVIDERS = {"xai", "fal"}
```

**truth_set.md entry**: VALID_VIDEO_PROVIDERS = {"xai", "fal"}
**Result**: ✅ MATCH

---

## Cross-Check 11: VALID_TIERS

**Command**:
```bash
grep -n "VALID_TIERS" /home/sol/daemon/orchestrator/routes/video_credits.py
```

**Output**:
```
157:VALID_TIERS = {"free", "starter", "pro", "max", "byok"}
```

**truth_set.md entry**: VALID_TIERS = {free, starter, pro, max, byok}
**Result**: ✅ MATCH

---

## Cross-Check 12: /providers Endpoint (CONFLICT-1 Resolution)

**Command**:
```bash
grep -n "def list_providers\|/providers" /home/sol/daemon/orchestrator/main.py
```

**Output**:
```
702:@app.get("/providers")
703:async def list_providers(
```

**truth_set.md CONFLICT-1 note**: `/providers` IS implemented at main.py:702 (Task 2 audit was wrong — CONFLICT-1 resolution)
**Result**: ✅ CONFLICT-1 is a correction to Task 2 audit

---

## Cross-Check 13: Video Pricing (fal/Kling estimate_cost)

**Command**:
```python
from config.video_pricing import estimate_cost
for dur in [5, 10, 15]:
    no_audio = estimate_cost(dur, tier='pro', provider='fal', kling_model='o3-pro', audio_enabled=False)
    with_audio = estimate_cost(dur, tier='pro', provider='fal', kling_model='o3-pro', audio_enabled=True)
    print(f"o3-pro audio=False {dur}s={no_audio}, audio=True {dur}s={with_audio}")
for dur in [5, 10, 15]:
    no_audio = estimate_cost(dur, tier='pro', provider='fal', kling_model='v3-pro', audio_enabled=False)
    with_audio = estimate_cost(dur, tier='pro', provider='fal', kling_model='v3-pro', audio_enabled=True)
    print(f"v3-pro audio=False {dur}s={no_audio}, audio=True {dur}s={with_audio}")
```

**Output** (actual from `python3 -c`):
```
o3-pro audio=False 5s=10, audio=True 5s=10
o3-pro audio=False 10s=20, audio=True 10s=20
o3-pro audio=False 15s=30, audio=True 15s=30
v3-pro audio=False 5s=10, audio=True 5s=15
v3-pro audio=False 10s=20, audio=True 10s=30
v3-pro audio=False 15s=30, audio=True 15s=45
```

**truth_set.md entry**: o3-pro: 2 credits/sec (5s=10, 10s=20, 15s=30) regardless of audio; v3-pro: audio=False → 2 credits/sec, audio=True → 3 credits/sec
**Result**: ✅ MATCH

---

## Summary

| Check | Fact | Expected | Actual | Status |
|-------|------|----------|--------|--------|
| 1 | Migration count | 30 | 30 | ✅ |
| 2 | Latest migration | 030_add_advisor_traces.sql | 030_add_advisor_traces.sql | ✅ |
| 3 | Dedup merge threshold | 0.90 | 0.90 | ✅ |
| 4 | Dedup supersede threshold | 0.82 | 0.82 | ✅ |
| 5 | Dedup same_slot threshold | 0.65 | 0.65 | ✅ |
| 6 | Embedding document model | voyage-4-large | voyage-4-large | ✅ |
| 7 | Embedding query model | voyage-4-lite | voyage-4-lite | ✅ |
| 8 | Embedding dimensions | 1024 | 1024 | ✅ |
| 9 | Pro orchestrator | kimi-k2.5 | kimi-k2.5 | ✅ |
| 10 | Max orchestrator | claude-opus-4.6 | claude-opus-4.6 | ✅ |
| 11 | Default video provider | fal | fal | ✅ |
| 12 | Docker service count | 7 | 7 | ✅ |
| 13 | Router prefixes | 6 routers | 6 routers | ✅ |
| 14 | Tier pricing (free/starter/pro/max/byok) | 0/9/19/29/9 | 0/9/19/29/9 | ✅ |
| 15 | VALID_VIDEO_PROVIDERS | {xai, fal} | {xai, fal} | ✅ |
| 16 | /providers endpoint | EXISTS | EXISTS | ✅ (corrected from T2) |
