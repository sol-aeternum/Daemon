# T11 — Harness Parity Smoke Trace

**Date**: 2026-05-06T10:45:49.027691
**Question**: `b86304ba` (single-session-user)
**Fresh Question ID**: `b86304ba_t11fresh`
**Synthetic User ID**: `c8bf04a7-ba3e-54e3-b622-fd77a7cb0c37`

## Rollback Gates

- memories_used_nonzero: ✅ PASS
- extraction_completed_nonzero: ✅ PASS
- retrieval_latency_ok: ✅ PASS
- encryption_ok: ✅ PASS
- same_user_retrieval: ✅ PASS
- current_run_provenance: ✅ PASS

**Overall**: ✅ PASS

## Haystack Ingestion

- Sessions: 3
- Messages ingested: 27
- Extraction outcomes: ['completed', 'empty', 'completed']

## Extraction

- Completed: 2
- Empty: 1
- Created memory IDs: ['e8e4e0f0-9da9-4989-bd9f-520e29362cda', 'a09dcc01-406f-4a61-a07a-30f54ceeea7d', '1545e495-1a14-40a8-aad9-966656d35a96', '857897c1-ec6f-4611-8848-40dc0927b9b6', '24ca7d5c-8b6f-4628-bfd1-93af9207a03b', 'c0536584-c9af-4770-afd1-66c6d42c1d2e', 'f2742c9d-31b0-4691-8b6a-c20969f18926', 'a997f913-1aa0-477d-8ebc-35052054d452', '7eaa7305-b17e-4d02-8061-fff7eff265e6']

## Provenance

- Extraction created: 9 memories
- Retrieved: 1 memories
- Provenance intersection: 1 (current-run created AND retrieved)
- Provenance IDs: ['857897c1-ec6f-4611-8848-40dc0927b9b6']

## Retrieval

- Memories used: 1
- Retrieved memory IDs: `['857897c1-ec6f-4611-8848-40dc0927b9b6']`
- Retrieval latency: 29.61ms
- Same-user verification: PASS

## Memory Context

Length: 102 chars

```
About this user:
- Fact: User's reusable water bottle has saved them money on overpriced airport water```

## System Prompt

Length: 9336 chars

Preview (first 1000 chars):

```
You are Daemon, a personal AI assistant.

When asked "who are you" or similar, respond: "I'm Daemon, a personal AI assistant."

If the user presses for specifics about your model or capabilities, be honest: explain you are currently running on a specific model (which may vary), that you can switch models automatically based on requests, and that you have tools and subagents at your disposal. The exact wording can vary naturally.

You respond directly most of the time. When necessary, you spawn s```

## Encryption Verification

- Messages checked: 10, decoded OK: 10
- Memories checked: 9, decoded OK: 9
- Extraction logs checked: 2, decoded OK: 2
- Failures: none

## Provider Route

- openai/gpt-4o-mini

---

_Note: answer/judge calls mocked after prompt capture. Extraction, embedding, and retrieval used real providers._
