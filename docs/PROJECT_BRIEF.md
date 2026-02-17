# Project Daemon: Personal Multi-Agent Assistant

## Overview

Mobile-first personal assistant with multi-model orchestration via OpenRouter. Tier-based model configuration allows flexible model assignment without code changes. FastAPI backend orchestrates LLM calls and spawns specialized subagents. Next.js 16 frontend provides streaming chat with voice I/O. PostgreSQL + pgvector provides persistent memory across conversations.

**Core principle:** Daemon is the assistant — it responds directly most of the time, spawning subagents only when specialized capability is needed. Not a router that delegates everything.

---

## Current State

**Phase 1 (Cloud Orchestration):** Complete. Streaming chat, subagent framework, voice I/O, 88-model selection, tool registry.

**Phase 2 (Memory System):** ~80% complete. Infrastructure operational (PostgreSQL, pgvector, Redis, arq, encryption). Critical bug: extracted memories write as "pending" but retrieval filters by "active" — pipeline runs but output is invisible. See CURRENT_ISSUES.md.

**Phase 3 (Local Pipeline):** Blocked on RTX 5090 acquisition. Pre-router parsing implemented, inference code not.

**Frontend:** Work in progress. Core chat functional, steady feature additions. Needs markdown rendering, theme unification, conversation switching fix.

---

## Architecture

Tier-based model configuration with 5 tiers. Model assignments are env-var configurable placeholders — the tier structure is real architecture, specific models are fluid.

| Tier | Orchestrator (current) | Subagents |
|------|----------------------|-----------|
| Free | Kimi K2.5 | None |
| Starter | Kimi K2.5 | Sonnet, Gemini |
| Pro (default) | Kimi K2.5 | Full suite |
| Max | Claude 4.6 Opus | Premium |
| BYOK | Kimi K2.5 | User-configured |

**Backend:** FastAPI + LiteLLM + asyncpg + arq + Fernet encryption
**Frontend:** Next.js 16 + Vercel AI SDK 4 + React 19 + PWA
**Infrastructure:** Docker Compose (5 services: backend, worker, frontend, postgres, redis)

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Model assignment | Config, not code | Swap models via env vars as landscape evolves |
| Orchestrator style | Direct response + tool use | Daemon is the assistant, not a classifier |
| Local routing | Manual `/local` flag | Zero false negatives, user decides privacy boundary |
| Frontend | Next.js 16 + Vercel AI SDK | Open WebUI lacked orchestration state concepts |
| Memory | PostgreSQL + pgvector + encryption | Persistent, searchable, encrypted at rest |

---

## Hardware (Pending)

RTX 5090 32GB build for local inference. Enables Qwen 72B Q5_K_M (~22GB VRAM) + FLUX Dev (~12GB) concurrent operation. Cloud pipeline is fully functional standalone.

- GPU: ASUS TUF 5090 @ $5999 AUD
- CPU: AMD 9950X3D
- RAM: Kingston Fury Beast Black 64GB 6000 CL36
- Case: Be Quiet Light Base 500 non-LX

---

## Key Documents

| Document | Contents |
|----------|----------|
| PROJECT_CONTEXT.md | Detailed architecture, decisions, current state |
| ROADMAP.md | Phased implementation plan with status |
| CURRENT_ISSUES.md | Known bugs and issues ranked by severity |
| OPEN_QUESTIONS.md | Unresolved decisions |
| TECHNICAL_SPECS.md | System prompts, schemas, API specs |
| HARDWARE_CONTEXT.md | GPU/build decisions |
