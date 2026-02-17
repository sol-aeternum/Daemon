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

## #2 — No Error Boundary
**Severity:** LOW
**Component:** Frontend — `app/page.tsx`
**Impact:** Any rendering error in the chat view crashes the entire page with no recovery path.

### What's Happening
`ChatContent` renders inside `<ErrorProvider>` (which handles toast notifications) and `<Suspense>` (which handles loading), but there's no React Error Boundary. A malformed message, unexpected tool result shape, or rendering exception in any child component takes down the whole UI.

### Recommended Fix
Wrap `ChatContent` in an Error Boundary component that catches rendering errors and shows a "Something went wrong — reload" fallback instead of a white screen.

### Files to Change
- Create `components/ErrorBoundary.tsx` (class component, React Error Boundaries require class components)
- `app/page.tsx` — wrap `ChatContent` in `<ErrorBoundary>`

---

## Summary by Priority

| # | Issue | Severity | Effort | Impact |
|---|-------|----------|--------|--------|
| 1 | Embedding vendor lock-in | LOW | Medium | Architectural concern |
| 2 | No error boundary | LOW | Small | Crash recovery |

Both issues are low priority but improve system resilience. Issue #1 is the higher-impact item.

