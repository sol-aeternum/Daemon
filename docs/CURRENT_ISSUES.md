# Current Issues

> Last updated: 2026-02-17
> Source: codebase audit against `daemon-core` + `daemon-frontend-core` tarballs

---

## #1 — Embedding Vendor Lock-in
**Severity:** LOW (architectural note)
**Component:** Backend — `orchestrator/memory/embedding.py`
**Impact:** Memory system requires a separate OpenAI API key despite all LLM calls going through OpenRouter.

### What's Happening
`embedding.py` instantiates `AsyncOpenAI(api_key=...)` directly, bypassing OpenRouter and LiteLLM entirely. This means:
- A separate `OPENAI_API_KEY` env var is required
- Embeddings can't use OpenRouter's embedding endpoints
- If OpenAI access is lost, the entire memory pipeline breaks

### Recommended Fix
Route embeddings through LiteLLM/OpenRouter like everything else. OpenRouter supports `openai/text-embedding-3-small`. This aligns with the "capability endpoints" philosophy — OpenRouter as the single LLM/embedding gateway.

### Files to Change
- `orchestrator/memory/embedding.py` — switch from direct OpenAI client to LiteLLM call
- `orchestrator/config.py` — embeddings model already in tier config, just needs wiring

---

## Summary by Priority

| # | Issue | Severity | Effort | Impact |
|---|-------|----------|--------|--------|
| 1 | Embedding vendor lock-in | LOW | Medium | Architectural concern |

Both issues are low priority but improve system resilience. Issue #1 is the higher-impact item.

