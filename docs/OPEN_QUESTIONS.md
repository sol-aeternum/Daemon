# Open Questions & Decisions Needed

> Last updated: 2026-02-17

## High Priority

### 1. Memory Promotion Strategy
**Question:** Should extracted memories auto-promote to active, or go through a review queue?

**Context:** Extraction pipeline writes `status="pending"`, retrieval filters by `status="active"`. This is the critical bug in CURRENT_ISSUES.md #1.

**Options:**
- **Auto-promote:** Change default status to "active". Simplest. All extracted facts immediately available. Risk: low-quality extractions surface without review.
- **Review queue:** Keep "pending". Build UI to browse/approve/reject. More control, more friction.
- **Confidence threshold:** Auto-promote above 0.8 confidence, queue below. Middle ground.

**Recommendation:** Auto-promote for personal use. Add confidence threshold if productizing.

---

### 2. Local Pipeline Complexity
**Question:** Should `/local` route to full orchestration (Qwen + subagents + SearXNG + FLUX) or just "chat with Qwen, no frills"?

**Options:**
- **Simple:** `/local` → Qwen 72B direct, no subagents. Truly isolated.
- **Full:** `/local` → Local Daemon with local subagents. Full capability while private.

**Recommendation:** Start simple, add local subagents as v2.

---

### 3. Model Identity
**Question:** System prompt hardcodes "Kimi K2.5 via OpenRouter" but the tier system means different tiers run different models. Should identity be dynamic?

**Options:**
- **Static:** Keep hardcoded. Users on Max tier get told they're talking to Kimi when it's actually Opus.
- **Dynamic:** Inject model identity from tier config into system prompt at runtime.
- **Abstract:** "I'm Daemon" — don't expose underlying model at all.

**Recommendation:** Abstract. Daemon is the product identity. Underlying model is an implementation detail.

---

## Medium Priority

### 4. Always-On vs Wake-on-LAN
**Question:** Is home server always running, or wake on demand?

**Tradeoffs:**
- Always-on: ~80-120W idle, instant response
- WoL: Zero idle power, 15-20s cold start

**Recommendation:** Start always-on. Consider WoL if local usage stays <1%.

---

### 5. Fallback Chains
**Question:** What happens when tier model quota exhausted or provider down?

**Options:**
- Auto-downgrade to next tier's model
- Notify user, let them choose
- Route to local Qwen (if available)

**Recommendation:** Auto-downgrade with subtle indicator. 88-model OpenRouter catalog provides multiple fallback options via tier config.

---

## Low Priority

### 6. Cost Tracking
- Per-conversation cost display?
- Budget alerts?
- Usage dashboard?

### 7. Multi-User
- Single default user currently. Schema supports multi-user.
- Separate memory stores per user already architected.

### 8. Frontend Polish
- Markdown rendering (CURRENT_ISSUES.md #3)
- Theme unification (CURRENT_ISSUES.md #5)
- Memory management UI beyond "Clear All"
- File attachment support (button exists, no backend)

---

## Resolved

| Question | Resolution | When |
|----------|------------|------|
| Project name | Daemon | Phase 1 |
| Frontend choice | Next.js 16 + Vercel AI SDK (pivoted from Open WebUI) | Phase 1 |
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
| Embeddings | text-embedding-3-small (1536d) | Phase 2 |
| Chat persistence | Backend PostgreSQL + frontend API integration | Phase 2 |
| Tier architecture | 5 tiers (free/starter/pro/max/byok), env-var model slots | Phase 2 |
