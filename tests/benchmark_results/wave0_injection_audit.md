# Wave 0 Injection Audit

**Artifact type:** Architecture audit
**Evidence basis:** Code path analysis of `tests/longmemeval/evaluate.py` vs `orchestrator/memory/injection.py`
**Date:** 2026-04-29

---

## 1. Purpose

This document audits the two divergent code paths that handle memory injection into LLM prompts:

1. The **production chat path** — used by live user conversations
2. The **benchmark evaluation path** — used by `tests/longmemeval/evaluate.py`

Understanding the separation is critical for correctly attributing failure causes in the Wave 0 benchmark collapse.

---

## 2. Production Chat Path

**Entry point:** `orchestrator/main.py`

**Relevant functions:**
- `build_memory_context()`
- `assemble_system_prompt()`

**Key characteristics:**
- Uses the full production injection module: `orchestrator/memory/injection.py`
- Constructs a structured system prompt with categorized memory blocks
- Applies `DEFAULT_MAX_TOKENS = 2500` budget
- Includes `MAX_SINGLE_MEMORY_CHARS = 400` per-memory truncation
- Has L0 support with `L0_TOKEN_BUDGET = 200` and `MAX_L0_CHARS = 600`
- Appends `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` to the assembled system prompt
- Supports `include_local` parameter
- Imports `retrieve_memories_for_text` (as of current state)

This path is architecturally significant and handles all live user conversations.

---

## 3. Benchmark Evaluation Path

**Entry point:** `tests/longmemeval/evaluate.py`

**Relevant functions:**
- `retrieve_user_memories()` → `retrieve_memories_for_text()`
- `build_answer_prompt()`
- `answer_with_llm()`

**Key characteristics:**
- **Bypasses the production injection module entirely**
- Uses a separate, simple prompt builder (`build_answer_prompt()`)
- Constructs a **single user message** (not a system prompt) containing a bullet list of retrieved memories
- Provides **no system-level grounding**, no category structure, no guardrails
- No token budget enforcement from the injection module
- No `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`
- The benchmark harness is completely decoupled from `orchestrator/memory/injection.py`

---

## 4. Architecture Diagram

```
Production chat path:
  orchestrator/main.py
    → build_memory_context()
    → assemble_system_prompt()
    → orchestrator/memory/injection.py (FULL module)
    → SYSTEM prompt with categories, guardrails, budget enforcement

Benchmark evaluation path:
  tests/longmemeval/evaluate.py
    → retrieve_user_memories() → retrieve_memories_for_text()
    → build_answer_prompt()
    → answer_with_llm()
    → SINGLE USER MESSAGE (no system prompt, no production injection)
```

---

## 5. Critical Implication

**The benchmark path bypasses the production injection module.**

This means:

1. **Production injection bugs cannot directly cause benchmark failures**, because the benchmark does not use the production injection module.
2. **Changes to `orchestrator/memory/injection.py`** (including token budget settings, truncation logic, L0 support, guardrails, or any other injection feature) **have no effect on benchmark scores** unless the benchmark harness is also updated to use those features.
3. **The benchmark evaluates a simplified version of memory injection**, not the production version.

This architectural separation must be accounted for in any diagnosis of the Wave 0 benchmark collapse.

---

## 6. Production Path Still Matters Architecturally

While the production injection path is not the direct path for measured benchmark failures, it remains important for:

1. **Live user conversations** — this is what users actually experience
2. **Future benchmark alignment** — if benchmark scores are to reflect production behavior, the harness should be updated to use production injection
3. **Diagnostic inference** — production injection behavior can inform hypotheses about what might help, but cannot be treated as the direct cause of observed benchmark failures

---

## 7. Summary

| Attribute | Production Path | Benchmark Path |
|-----------|-----------------|----------------|
| Entry point | `orchestrator/main.py` | `tests/longmemeval/evaluate.py` |
| Injection module | `orchestrator/memory/injection.py` | **Bypassed** |
| Prompt type | System prompt + user message | Single user message only |
| Memory formatting | Categorized, truncated, budgeted | Bullet list, no structure |
| Guardrails | Yes (`MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`) | No |
| Token budget | `DEFAULT_MAX_TOKENS = 2500` | Not enforced |
| L0 support | Yes | No |

The benchmark harness uses a stripped-down prompt builder that does not exercise the production injection pipeline. Failures observed in benchmark scores cannot be directly attributed to production injection module behavior.
