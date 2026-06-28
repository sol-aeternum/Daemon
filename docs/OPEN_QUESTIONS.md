# Open Questions & Decisions Needed

- **Verified-against-commit**: 3155d69fa1eb1939cf5c737018242fc119480d6c
- **Last updated**: 2026-05-31
- **Upstream Sources**: `orchestrator/config.py`, `MEMORY_LAYER.md`, `docs/FEATURE_MATRIX.md`, `migrations/`, `docs/SOURCES_OF_TRUTH.md`, `tests/benchmark_results/doc-alignment-regeneration/truth_set.md`

## High Priority

### 1. Local Pipeline Complexity
**Question**: Should `/local` route to full orchestration (Qwen + subagents + SearXNG + FLUX) or just "chat with Qwen, no frills"?

**Status**: **OPEN / BLOCKED**
**Context**: Blocked on hardware (RTX 5090 acquisition). The `/local` flag is parsed but all local inference code is unimplemented.

**Options**:
- **Simple**: `/local` → Qwen 72B direct, no subagents. Truly isolated.
- **Full**: `/local` → Local Daemon with local subagents. Full capability while private.

**Recommendation**: Start simple, add local subagents as v2.

---

## Medium Priority

### 2. Always-On vs Wake-on-LAN
**Question**: Is home server always running, or wake on demand?

**Status**: **OPEN**
**Tradeoffs**:
- Always-on: ~80-120W idle, instant response
- WoL: Zero idle power, 15-20s cold start

**Recommendation**: Start always-on. Consider WoL if local usage stays <1%.

### 3. Fallback Chains
**Question**: What happens when tier model quota exhausted or provider down?

**Status**: **OPEN**
**Context**: No concrete source evidence of general quota/provider fallback behavior.

**Options**:
- Auto-downgrade to next tier's model
- Notify user, let them choose
- Route to local Qwen (if available)

**Recommendation**: Auto-downgrade with subtle indicator. OpenRouter catalog provides multiple fallback options via tier config.

---

## Low Priority

### 4. Cost Tracking
**Status**: **OPEN**
- Per-conversation cost display?
- Budget alerts?
- Usage dashboard?
- **Note**: `/video-credits` exists for video generation, but general LLM cost tracking is not implemented.

### 5. Frontend Polish
**Status**: **OPEN**
- Markdown rendering
- Theme unification
- Memory management UI beyond "Clear All"
- File attachment support (button exists, no backend)

### 6. Multi-User
**Question**: Single default user currently. Schema supports multi-user.

**Status**: **OPEN / ARCHITECTED**
**Context**: Schema support for user scoping exists in `migrations/`, but user-facing multi-user implementation and workflow decisions remain open.

---

## Resolved

| Question | Resolution | Source |
|----------|------------|--------|
| Memory Promotion Strategy | **RESOLVED**: Extraction pipeline now writes `status="active"`. | `truth_set.md` (Task 2 conflict note) |
| Model Identity | **RESOLVED**: Abstracted as "Daemon". Underlying models are implementation details managed by the tier system. | `orchestrator/prompts.py`, `orchestrator/config.py` |
| Project name | Daemon | Phase 1 |
| Frontend choice | Next.js 16 + Vercel AI SDK | Phase 1 |
| Cloud search | Brave Search API | Phase 1 |
| Cloud image gen | Gemini Flash Image via OpenRouter | Phase 1 |
| Voice I/O | ElevenLabs (TTS, STT Scribe, SFX) | Phase 1 |
| Notifications | ntfy.sh | Phase 1 |
| LLM provider | OpenRouter (88 models, tier-sorted) | Phase 1 |
| Subagent approval | Auto-spawn with AgentStatusCard visibility | Phase 1 |
| VRAM management | Eliminated — 32GB allows concurrent Qwen + FLUX | Hardware decision |
| Quantization | Q5_K_M (32GB enables, no offload) | Hardware decision |
| GPU choice | ASUS TUF 5090 @ $5999 AUD | Hardware decision |
| RAM | Kingston Fury Beast Black 64GB 6000 CL36 @ $1299 | Hardware decision |
| Case | Be Quiet Light Base 500 non-LX | Hardware decision |
| Memory encryption | Fernet at rest | Phase 2 |
| Embeddings | voyage-4-large (documents) + voyage-4-lite (queries), 1024d vectors | Phase 2 |
| Chat persistence | Backend PostgreSQL + frontend API integration | Phase 2 |
| Tier architecture | 5 tiers (free/starter/pro/max/byok), env-var model slots | Phase 2 |
