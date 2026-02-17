# Daemon — Agent Instructions

## What This Is
Personal multi-agent AI assistant. FastAPI backend orchestrates LLM calls via OpenRouter, spawning subagents (@research, @image, @audio, @code, @reader). Next.js 16 frontend with Vercel AI SDK. PostgreSQL + pgvector for memory. Redis + arq for background jobs.

## Before You Touch Anything
1. Read `docs/CURRENT_ISSUES.md` — know what's broken before changing things
2. Read `docs/PROJECT_CONTEXT.md` — understand what's implemented vs planned
3. If the task touches memory: read `orchestrator/memory/` and `docs/TECHNICAL_SPECS.md`
4. If the task touches frontend: read `docs/CURRENT_ISSUES.md` #2-#5 for known state bugs

## Rules of Engagement
- **Ask before making design decisions.** If a task has multiple valid approaches, present options with tradeoffs. Do not pick one autonomously.
- **Clarify ambiguity, don't assume.** If the spec is unclear, ask. Wrong assumptions cost more than a question.
- **No silent architecture changes.** Changing data models, API contracts, SSE event types, or tier config requires explicit approval.
- **Update docs with code.** If you fix a bug in CURRENT_ISSUES.md or complete a ROADMAP.md item, update the doc in the same change.
- **Don't add dependencies without asking.** Especially frontend — bundle size matters for PWA.

## Tech Stack
- **Backend:** Python 3.11+, FastAPI, LiteLLM, asyncpg, arq, cryptography (Fernet)
- **Frontend:** Next.js 16, React 19, Vercel AI SDK 4, Tailwind CSS 3, lucide-react
- **Infra:** Docker Compose — backend, worker, frontend, postgres (pgvector), redis
- **External:** OpenRouter (LLMs), OpenAI (embeddings), Brave Search, ElevenLabs, ntfy.sh

## Structure
```
orchestrator/           # FastAPI backend
  main.py               # Routes, SSE streaming, chat endpoint
  daemon.py             # Core orchestration loop (stream_sse_chat)
  config.py             # Tier system, env-var model slots
  prompts.py            # System prompt (v1)
  memory/               # Full memory pipeline
    store.py            # PostgreSQL CRUD (973 lines)
    extraction.py       # GPT-4o-mini fact extraction
    dedup.py            # Embedding similarity dedup
    retrieval.py        # Composite scoring retrieval
    injection.py        # System prompt assembly with memory context
    embedding.py        # text-embedding-3-small via OpenAI
    encryption.py       # Fernet encrypt/decrypt
    tools.py            # memory_read / memory_write tool implementations
  worker/               # arq background jobs
  routes/               # API route modules
  agents/               # Subagent implementations
frontend/
  app/page.tsx          # Main chat UI (ChatContent component)
  app/api/chat/route.ts # SSE bridge: backend SSE → Vercel AI SDK format
  components/           # UI components
  hooks/                # React hooks (useConversationHistory, useChat wrappers)
  lib/events.ts         # Typed SSE event definitions
docs/                   # Project documentation (keep in sync)
migrations/             # PostgreSQL migrations (13 applied)
```

## Conventions
- Backend uses `asyncpg` directly — no ORM. Raw SQL in store.py.
- All message/memory content encrypted at rest via Fernet. Embeddings are plaintext for pgvector.
- SSE events are typed: token, thinking, routing, tool_call, tool_result, final, error, done.
- Tier model assignments are env-var configurable. Don't hardcode model strings in logic.
- Frontend uses `useChat` from Vercel AI SDK — be aware of its state management quirks (see CURRENT_ISSUES.md #2).
- No test suite yet. If you add tests, use pytest + pytest-asyncio for backend, Playwright for frontend.

## Known Critical Bug
Extracted memories write `status="pending"` but retrieval filters `status="active"`. The entire automatic memory pipeline runs but output is invisible. See `docs/CURRENT_ISSUES.md` #1.

## What NOT to Do
- Don't add Open WebUI references — it's being removed
- Don't reference OpenCode Zen provider — legacy, being removed
- Don't use `gpt-4o` as a default anywhere — backend uses tier-based auto-routing
- Don't put secrets in code or docs — everything goes through env vars
- Don't create new Docker services without discussing architecture impact

# Diagnostic Triage Protocol

## Mandatory: TRIAGE.md Maintenance

During ALL task execution, maintain a `TRIAGE.md` file in the project root. Log any error, warning, failure, unexpected behavior, or anomaly encountered — **especially** items outside your current task scope.

### What to log
- Build errors/warnings (even if you can work around them)
- Pre-existing bugs you stumble upon
- Test failures unrelated to your changes
- Deprecation warnings, version incompatibilities
- Configuration issues outside your task scope
- Anything you'd mentally dismiss as "not my problem"

### Format per entry
```markdown
## [TIMESTAMP] — [SHORT TITLE]
- **Severity**: critical | warning | info
- **Encountered during**: [current TODO/task]
- **Category**: build-error | runtime-error | deprecation | config | test-failure | dependency | security | other
- **Blocked current task**: yes | no
- **What happened**: [1-3 sentences]
- **Evidence**: [exact error output, file:line refs]
- **Likely cause**: [assessment with confidence %]
- **Suggested action**: [what to investigate]
```

### Rules
1. Log BEFORE marking any TODO complete
2. Include actual error messages — don't paraphrase
3. Do NOT fix triaged items unless they block your current task
4. If you think "this is pre-existing / not my fault / probably fine" — that's a triage trigger, not a dismissal
5. If it was worth noting in your thinking, it's worth logging in TRIAGE.md

### On task completion, always report
```
Triage: {N} issues ({critical} critical, {warning} warning, {info} info)
See TRIAGE.md — items requiring attention: [list critical/warning titles]
```
If zero issues: "Triage: clean — no anomalies encountered."
