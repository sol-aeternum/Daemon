# Current Issues

> Last updated: 2026-02-17
> Source: codebase audit against `daemon-core` + `daemon-frontend-core` tarballs

---

## #1 — Memory Extraction Pipeline Output Is Invisible
**Severity:** CRITICAL
**Component:** Backend — `orchestrator/memory/extraction.py`, `orchestrator/memory/dedup.py`
**Impact:** The entire automatic memory system is running but producing zero visible output. Every conversation is still lost context despite the infrastructure being operational.

### What's Happening
The extraction pipeline runs correctly: conversations trigger background jobs → GPT-4o-mini extracts facts → embeddings are generated → dedup runs → memories are inserted into PostgreSQL. However:

- `extraction.py` line 162 calls `deduplicate_facts()` with `status="pending"`
- `dedup.py` line 30 defaults to `status="pending"` and passes it through to all insert/supersede operations
- `store.py` line 330: `search_memories()` filters by `status="active"` by default
- `retrieval.py` uses `search_memories()` for context injection

Result: memories are written as "pending" and retrieval never sees them. The `confirm_memory` API endpoint exists (`/memories/{id}/confirm`) but nothing calls it automatically.

Contrast: when Daemon uses the `memory_write` tool (user explicitly says "remember this"), `tools.py` line 78 writes with `status="active"` — those memories work correctly.

### Recommended Fix
Two options depending on desired workflow:

**Option A — Auto-promote (simplest, recommended for personal use):**
Change `extraction.py` line 162 from `status="pending"` to `status="active"`. One-line fix. All extracted memories immediately visible to retrieval. No review queue.

**Option B — Review queue:**
Keep "pending" default. Build frontend UI to browse/approve/reject pending memories. More control, more friction. Only worth it if productizing with untrusted users.

### Files to Change
- `orchestrator/memory/extraction.py:162` — change status parameter
- Or: `orchestrator/memory/dedup.py:30` — change default

---

## #2 — Conversation Switching Corrupts Chat State
**Severity:** HIGH
**Component:** Frontend — `app/page.tsx`, `hooks/useConversationHistory.ts`
**Impact:** Switching between conversations may show stale messages, duplicate content, or fail to load history properly.

### What's Happening
`useChat` from Vercel AI SDK manages its own internal message state. The hook is instantiated once in `ChatContent` with `id: currentId || undefined` and `initialMessages: currentConversation?.messages || []`. When `currentId` changes (conversation switch):

- `initialMessages` only applies on first render — subsequent changes don't reset the hook's internal state
- The same `useChat` instance persists across conversation switches
- Stale messages from the previous conversation may remain in the hook's buffer
- `archivedEvents` state accumulates across conversations

### Recommended Fix
Key the component that owns `useChat` on the conversation ID. This forces React to unmount/remount, giving a fresh hook instance per conversation:

```tsx
// In ChatPage, wrap ChatContent with a key:
<ChatContent key={currentId || "new"} />
```

This is the canonical fix for reusable `useChat` instances across different chat contexts. The tradeoff is a brief re-render flash on switch, which is acceptable.

### Files to Change
- `app/page.tsx` — add `key` prop to `ChatContent`
- Consider moving `archivedEvents` state into a ref or context that resets with the key

---

## #3 — No Markdown Rendering
**Severity:** HIGH
**Component:** Frontend — `app/page.tsx` line 365
**Impact:** Code blocks, headers, lists, links, bold/italic all render as raw text. For an orchestrator that frequently emits structured responses, this significantly degrades readability.

### What's Happening
Assistant messages render via:
```tsx
<div className="whitespace-pre-wrap">{formatMessageContent(message.content)}</div>
```
No markdown parsing at all. `formatMessageContent` only strips image/audio path artifacts.

### Recommended Fix
Add `react-markdown` with syntax highlighting:

```bash
npm install react-markdown remark-gfm rehype-highlight
```

Replace the raw content div with a markdown renderer. Apply appropriate Tailwind prose classes for styling.

### Files to Change
- `package.json` — add react-markdown, remark-gfm, rehype-highlight
- `app/page.tsx` — replace `formatMessageContent` output with `<ReactMarkdown>` component
- `app/globals.css` — add prose/code styling if needed

---

## #4 — Dual Input State Race Condition
**Severity:** MEDIUM
**Component:** Frontend — `app/page.tsx`, `components/ChatInputBar.tsx`
**Impact:** Potential for lost messages or double-sends under fast input conditions.

### What's Happening
Two separate `input` states exist simultaneously:

1. `ChatInputBar` maintains its own `useState("")` for the textarea
2. `page.tsx` has `input`/`setInput` from `useChat`

The send flow is:
```tsx
onSendMessage={(msg) => {
  setInput(msg);              // Sets useChat's input
  setTimeout(() => {
    formRef.current?.requestSubmit();  // Submits the form
  }, 0);
}}
```

This relies on React batching the state update before the `setTimeout` fires. It works by accident but is fragile — especially under React 19's concurrent features.

### Recommended Fix
Eliminate the dual state. Either:
- Let `ChatInputBar` directly call `handleSubmit` from `useChat` (pass it as a prop)
- Or: make `ChatInputBar` a controlled component using `useChat`'s `input`/`handleInputChange`

### Files to Change
- `components/ChatInputBar.tsx` — remove internal input state
- `app/page.tsx` — pass `useChat`'s input/handleInputChange/handleSubmit directly

---

## #5 — Visual Theme Split
**Severity:** MEDIUM
**Component:** Frontend — `tailwind.config.ts`, multiple components
**Impact:** The UI is neither dark nor light — it's both simultaneously, creating a disjointed visual identity.

### What's Happening
- Footer/input bar: `bg-gpt-main` (#343541), `bg-gpt-input` (#444654), `text-gpt-text-primary` — dark theme
- Chat area: `bg-gray-50`, `bg-white` message bubbles — light theme
- Sidebar: white background
- Custom `gpt-*` colors defined in tailwind.config.ts suggest an early ChatGPT-clone aesthetic that was partially abandoned

### Recommended Fix
Pick one direction and commit. Given the stated goal of "Claude app experience with orchestration superpowers," a clean light theme with subtle accent colors would align better. Remove `gpt-*` color definitions from tailwind config and unify the palette.

### Files to Change
- `tailwind.config.ts` — remove or rename gpt-* colors
- `app/page.tsx` — unify footer/header/chat area colors
- `components/ChatInputBar.tsx` — update input area styling
- `app/globals.css` — update any gpt-* references

---

## #6 — Model Selector Default Mismatch
**Severity:** LOW
**Component:** Frontend — `app/page.tsx` line 92
**Impact:** Displayed model doesn't match actual model used. Cosmetic but confusing.

### What's Happening
```tsx
const [activeModel, setActiveModel] = useState<string>("gpt-4o");
```
The default is hardcoded to "gpt-4o" but the backend defaults to the tier's orchestrator (currently Kimi K2.5 for Pro tier) with auto-routing. The selector shows "gpt-4o" until the user explicitly changes it, even though the backend is running something else entirely.

### Recommended Fix
Default to `"auto"` to match the backend's auto-routing behavior:
```tsx
const [activeModel, setActiveModel] = useState<string>("auto");
```

### Files to Change
- `app/page.tsx:92` — change default to "auto"

---

## #7 — Dead Weight in Codebase
**Severity:** LOW
**Component:** Backend — `docker-compose.yml`, `orchestrator/config.py`
**Impact:** Confusion for contributors, unnecessary container resource usage.

### Items to Remove
1. **Open WebUI service** in `docker-compose.yml` — still defined with port 8080 mapping, volume mount, environment variables. Dead since Next.js pivot.
2. **Legacy OpenCode Zen provider** in `config.py` — `opencode_api_key`, `opencode_base_url`, `opencode_model` fields. Not used by any active code path.
3. **Open WebUI volume** in docker-compose — `open-webui` volume definition.

### Files to Change
- `docker-compose.yml` — remove open-webui service + volume
- `orchestrator/config.py` — remove opencode_* fields and opencode_zen provider branch in `get_provider_config()`

---

## #8 — Embedding Vendor Lock-in
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

## #9 — No Error Boundary
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
| 1 | Memory extraction invisible | CRITICAL | 1 line | Entire memory pipeline non-functional |
| 2 | Conversation switching state | HIGH | Small | Messages corrupt on switch |
| 3 | No markdown rendering | HIGH | Medium | All structured content unreadable |
| 4 | Dual input state | MEDIUM | Small | Fragile message sending |
| 5 | Theme split | MEDIUM | Medium | Disjointed visual identity |
| 6 | Model selector default | LOW | 1 line | Cosmetic mismatch |
| 7 | Dead weight (Open WebUI, OpenCode) | LOW | Small | Codebase cleanliness |
| 8 | Embedding vendor lock-in | LOW | Medium | Architectural concern |
| 9 | No error boundary | LOW | Small | Crash recovery |

Issues #1, #2, #6, and #7 are quick wins — fixable in under an hour combined. Issue #3 is the highest-effort item with the biggest UX payoff.
