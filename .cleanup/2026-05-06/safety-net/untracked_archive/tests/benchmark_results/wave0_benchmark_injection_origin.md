# Wave 0 Benchmark Injection Origin

**Artifact type:** BH3 — Diagnosis of benchmark's independent prompt path
**Evidence basis:** IL1 injection trace, IL2 injection audit, IL5 historical diff, direct code inspection of `evaluate.py` and `injection.py`
**Date:** 2026-04-29

---

## 1. Purpose

This document diagnoses *why* the benchmark harness (`tests/longmemeval/evaluate.py`) ended up with its own prompt construction path (`build_answer_prompt()`) instead of calling the production injection module (`orchestrator/memory/injection.py`). It is a historical causation document — not a fix recommendation.

---

## 2. Timeline of Events

| Date | Event | Evidence |
|------|-------|----------|
| Pre-2026-04-10 | Production injection pipeline (`build_memory_context()`, `assemble_system_prompt()`) exists in `orchestrator/memory/injection.py` | `injection.py` — full module with token budgets, L0 support, guardrails, truncation, category formatting |
| ~2026-04-10 | Benchmark harness added (`evaluate.py`, commit `c5a2a75787e5f0d9701def2ae1bb78e11af9e5d4`) | IL5 historical diff; `evaluate.py` docstring at line 1–31 |
| ~2026-04-10 | Benchmark harness uses `build_answer_prompt()` (simple bullet-list, single user message) — does NOT call production injection | IL2 injection audit; `evaluate.py:391–401` |
| Post-2026-04-10 | Production injection continues to evolve independently (L0, guardrails, budgets, per-memory truncation) | IL5 historical diff lists 6 diff themes between c5a2a757 and current |
| Wave 0 runs | Benchmark measures evaluate-path behavior; production injection changes have zero effect on benchmark scores | IL2 + IL5 — architectural independence confirmed |

---

## 3. Confirmed Historical Facts

### 3.1 Production injection predates the benchmark harness

The production memory injection module (`orchestrator/memory/injection.py`) existed before the benchmark harness was introduced. The module contains:

- `build_memory_context()` — retrieves and formats memory with `DEFAULT_MAX_TOKENS = 2500`, per-memory truncation (`MAX_SINGLE_MEMORY_CHARS = 400`), L0 support, and category labeling
- `assemble_system_prompt()` — prepends `DAEMON_SYSTEM_PROMPT`, inserts memory context, appends `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`

### 3.2 The benchmark harness was added as a separate, independent artifact

The benchmark harness (`evaluate.py`) was introduced around 2026-04-10 as a standalone evaluation script. Its core answering path is:

```
evaluate_single()
  → embed_query(question_text)
  → retrieve_user_memories()     # thin wrapper calling retrieve_memories_for_text() directly
  → answer_with_llm()
      → build_answer_prompt()   # simple bullet-list prompt builder
      → single user message via litellm
  → judge_answer()
```

This path bypasses `orchestrator/memory/injection.py` entirely. It does not call `build_memory_context()` or `assemble_system_prompt()`.

### 3.3 The benchmark prompt builder is architecturally independent

`build_answer_prompt()` (evaluate.py:391–401) is a 12-line function that:
1. Joins retrieved memories as a plain bullet list with no category labels
2. Interpolates into a hardcoded instruction template: `"You are a helpful assistant. Use the provided memories to answer the question concisely."`
3. Sends everything as a single user message — no system prompt, no guardrails, no budget enforcement, no L0 formatting, no preference injection

### 3.4 No evidence of a hard technical blocker

There is no code evidence of a technical constraint that prevented the benchmark harness from calling production injection. The retrieval function (`retrieve_memories_for_text()`) is importable and callable from `evaluate.py`, and in fact `retrieve_user_memories()` in `evaluate.py` calls it directly (evaluate.py:503–514). The *only* thing the benchmark bypasses is the downstream `build_memory_context()` → `assemble_system_prompt()` pipeline in `injection.py`.

---

## 4. Best-Supported Inference: Why the Independent Path Was Chosen

### 4.1 Evaluation isolation (primary inferred motive)

The benchmark harness was designed to evaluate the *retrieval + answering pipeline in isolation* — without the full production orchestration context. A standalone test script with a simple prompt template:

- Does not require spinning up the full FastAPI app or SSE streaming infrastructure
- Produces reproducible, controlled inputs for the answering LLM
- Avoids coupling the evaluation to any production routing logic, tier config, or SSE event surface
- Allows the benchmark to be run as a simple Python script against a populated memory store

This is a standard evaluation design pattern: isolate the subsystem under test from the rest of the system.

### 4.2 No dependency on production orchestration complexity

The production injection path (`assemble_system_prompt()`) depends on:
- `DAEMON_SYSTEM_PROMPT` from `orchestrator/prompts.py`
- `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` from `orchestrator/prompts.py`
- `format_preferences_block()` for user preference injection
- Trust signal recording via `orchestrator.memory.trust_signals`

Introducing all of these dependencies into a standalone benchmark script would couple it tightly to production code paths that are outside the scope of the retrieval + answering evaluation. The simpler `build_answer_prompt()` path keeps the benchmark self-contained.

### 4.3 Summary of inference

The benchmark's independent prompt path is most consistent with a **design-for-isolation** choice: the harness was intentionally structured to evaluate retrieval quality and answering fidelity in a controlled, decoupled setting. There is no evidence it was a fallback due to a broken production path, a migration artifact, or an oversight.

---

## 5. What This Means for Score Interpretation

Because the benchmark path and production path are architecturally independent:

- **Benchmark scores reflect the evaluate-path prompt design only.** They do not measure production injection behavior.
- **Production injection changes (L0, guardrails, budgets, truncation) have zero effect on benchmark scores** unless the harness is updated to use the production path.
- **The 28% Wave 0 score is a measurement of the evaluate-path prompt**, not of how production memory injection performs in live chat.

---

## 6. Relationship to BH4 Recommendations

The architectural independence documented here means that **if BH4 recommendations involve production injection improvements (token budgets, guardrails, L0 formatting, category labels), those improvements will not improve benchmark scores unless the benchmark harness is also updated** to call `build_memory_context()` / `assemble_system_prompt()` instead of `build_answer_prompt()`. This architectural constraint should inform the priority and scope of any BH4 fix recommendations.

---

## 7. Summary

| Item | Finding |
|------|---------|
| Production injection existed first | Confirmed — `injection.py` predates `evaluate.py` |
| Benchmark harness introduced independently | Confirmed — added ~2026-04-10 as standalone evaluation script |
| Benchmark uses `build_answer_prompt()` | Confirmed — evaluate.py:391–401, 12-line bullet-list builder |
| Benchmark bypasses production injection | Confirmed — no call to `build_memory_context()` or `assemble_system_prompt()` |
| Hard technical blocker exists | **Not confirmed** — no evidence of any blocker; `retrieve_memories_for_text()` is directly accessible |
| Primary inferred motive | Evaluation isolation / decoupling from production orchestration complexity |
| Production injection changes affect benchmark | **No** — architecturally independent paths |

---

*This document is diagnosis-only. It does not recommend a fix. For fix recommendations, see the BH4 artifact.*
