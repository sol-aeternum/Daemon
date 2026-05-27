# Wave 0 Dual Injection Test

**Artifact type:** BH2 — Dual-injection path comparison diagnosis
**Evidence basis:** IL1 (actual benchmark-path prompt traces), IL2 (architectural audit), IL3 (budget check), code-level comparison of `evaluate.py` and `injection.py`
**Date:** 2026-04-29
**Traces examined:** `e47becba`, `58bf7951`

---

## 1. Purpose and Scope

This document (BH2) tightens the answer to the question posed at Wave 0: **Can current benchmark scores be used as a production-memory baseline?**

BH1 established that the benchmark harness and production injection are architecturally independent paths. BH2 directly compares the actual prompt text from the benchmark path against the production path framing for the same trace evidence, to determine whether the prompt difference is large enough to invalidate benchmark-to-production generalization.

**Diagnosis-only scope.** This document does not recommend fixes. It establishes what was directly verified versus what was inferred from architectural comparison.

---

## 2. What Was Directly Verified (IL1 Evidence)

### 2.1 Trace `e47becba` — Benchmark-Path Prompt

**Question:** "What degree did I graduate with?"
**Reference answer:** Business Administration

The actual benchmark prompt reconstructed from `build_answer_prompt()` in `evaluate.py` (lines 391–401):

```
You are a helpful assistant. Use the provided memories to answer the question concisely.

Memories:
- User graduated with a Bachelor's degree in Computer Science on May 15th, 2022
- User graduated with a Bachelor's degree in Business Administration five years ago
- [3 additional degree-related memories]

Question: What degree did I graduate with?

Answer:
```

**IL1 finding:** Both the correct memory (Business Administration, rank 2) and the competing wrong memory (Computer Science, rank 1) are present in the prompt. The model answered: "I'm sorry, the provided memories do not contain information about your degree." — an abstention despite correct memory presence.

**Verification method:** Actual prompt trace recovered from current store state. Direct evidence.

### 2.2 Trace `58bf7951` — Benchmark-Path Prompt

**Question:** "What play did I attend at the local community theater?"
**Reference answer:** The Glass Menagerie

The actual benchmark prompt reconstructed from `build_answer_prompt()` in `evaluate.py` (lines 391–401):

```
You are a helpful assistant. Use the provided memories to answer the question concisely.

Memories:
- User attended a local production of The Glass Menagerie at their community theater in late March
- [Rent (movie) memory]
- [The Crucible ushering memory]
- [2 additional theater-related memories]

Question: What play did I attend at the local community theater?

Answer:
```

**IL1 finding:** The correct memory (The Glass Menagerie, score 0.3978) is present in the prompt. The model answered: "Based on the provided memories, you recently auditioned for a role in 'The Crucible,' but there is no specific memory indicating you attended a play at the local community theater." — an override of correct retrieval by a competing wrong memory.

**Verification method:** Actual prompt trace recovered from current store state. Direct evidence.

---

## 3. Production-Path Prompt Framing (Architectural Comparison Only)

### 3.1 What This Section Is

This section compares the production-path prompt framing to the benchmark path. **This comparison is based on current production injection code analysis (`injection.py`, `prompts.py`), NOT on a live production replay.** No production-path model output was directly captured for `e47becba` or `58bf7951`.

### 3.2 Production-Path Prompt Structure

If `e47becba` or `58bf7951` were processed through the production chat path, the memory context would be assembled by `build_memory_context()` and `assemble_system_prompt()` in `injection.py` and `prompts.py`. The resulting system prompt would contain:

**System prompt header** (`DAEMON_SYSTEM_PROMPT`):
```
You are Daemon, a personal AI assistant.
[Full role definition, tool descriptions, memory categories, slot guidance]
```

**Memory block** (from `build_memory_context()`):
```
About this user:
- Fact: User graduated with a Bachelor's degree in Computer Science on May 15th, 2022
- Fact: User graduated with a Bachelor's degree in Business Administration five years ago
- [additional formatted memories]

Recent context:
- Session: [conversation summary if available]
```

**Abstention guardrail** (from `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`):
```
When a question depends on retrieved memory or recent context, treat that memory as evidence rather than permission to guess.
If the available memory does not directly answer the question, say that you do not know or that the available memory is insufficient.
Do not fill gaps with nearby but non-answering details, inferred timelines, or best guesses.
Only answer confidently when the memory evidence directly supports the answer.
```

### 3.3 Critical Differences Summary

| Attribute | Benchmark Path | Production Path |
|-----------|---------------|-----------------|
| Prompt type | Single user message | System prompt + user message |
| Memory formatting | Flat bullet list | Categorized (`Fact:`, `Project:`, etc.) |
| Memory ordering | Flat composite ranking | L0 frozen block first, then retrieved, then summaries |
| System grounding | None | Full `DAEMON_SYSTEM_PROMPT` with role definition |
| Abstention guardrail | None | `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` |
| Per-memory truncation | None | `MAX_SINGLE_MEMORY_CHARS = 400` |
| Token budget enforcement | None | `DEFAULT_MAX_TOKENS = 2500` |
| L0 frozen memories | Formatted by retrieval, not prompt | Separate `[FROZEN MEMORIES]` block |
| Session summaries | None | Appended under `Recent context:` |

### 3.4 What This Comparison Does NOT Claim

- **Does NOT claim** that a live production replay was executed for `e47becba` or `58bf7951`
- **Does NOT claim** that the production path would produce a correct answer for either trace
- **Does NOT claim** that adding guardrails/categories would fix the benchmark failures
- **Claims only** that the production prompt structure is substantially different from the benchmark prompt structure

---

## 4. Central BH2 Conclusion

### 4.1 What the Evidence Shows

**Directly verified (IL1):**
- The correct memories for `e47becba` and `58bf7951` are present in the actual benchmark-path prompts
- The model fails to use the correct memories in both cases — this is an answer-generation/context-use failure
- The benchmark-path failures are NOT due to injection omission (memory was present)

**Architectural comparison (IL2 + code analysis):**
- The production path uses a fundamentally different prompt structure than the benchmark path
- Production adds system grounding, category labels, abstention guardrails, and token budget enforcement
- These additions are absent from the benchmark prompt

**Budget check (IL3):**
- No token budget truncation occurs in the sampled queries
- Even if production path were used, no truncation would occur for these traces

### 4.2 Is the Prompt Difference Large Enough to Invalidate the Benchmark as a Production-Memory Baseline?

**Yes.** The prompt differences are large enough to invalidate using the benchmark score as a direct proxy for production-memory quality. The reasons are:

1. **No shared prompt infrastructure.** The benchmark harness (`evaluate.py`) bypasses `orchestrator/memory/injection.py` entirely. The two paths have no code sharing below the retrieval function.

2. **Benchmark measures a stripped-down prompt.** The benchmark evaluates the model's ability to answer questions given only a thin bullet-list prompt with no system grounding, no category labels, no guardrails, and no budget enforcement. This is a fundamentally different task than production memory retrieval.

3. **Production uses additional grounding signals.** The production path includes the `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` which explicitly instructs the model how to handle insufficient memory evidence. The benchmark prompt has no equivalent instruction.

4. **No live production replay evidence exists.** For `e47becba` and `58bf7951`, we have not executed a production-path replay to determine whether the production prompt framing would produce correct, incorrect, or abstaining answers. The architectural comparison shows the prompts are different, but we have not verified that the production path would yield different (or better) answers for these specific traces.

### 4.3 What This Means for Baseline Validity

| Question | Answer |
|----------|--------|
| Do benchmark scores reflect production memory quality? | **No.** The benchmark path bypasses production injection. |
| Could production improvements raise benchmark scores? | **Only if the benchmark harness is updated to use production injection.** |
| Is the benchmark score meaningless? | **The benchmark measures the thin prompt's answer-generation quality — useful for evaluating that specific prompt design, not for production memory quality.** |
| Can we extrapolate from benchmark failures to production problems? | **No direct extrapolation.** The failure modes are benchmark-path-specific. |

---

## 5. Relationship to BH3 Recommendations

The diagnosis in this document (BH2) does not extend to fix recommendations. If the architectural separation between benchmark and production paths is considered problematic, potential directions include:

- Aligning the benchmark harness with production injection (BH3 territory — not pursued here)
- Investigating the answer-generation failure mode separately from injection

These directions require separate evaluation and are outside the diagnosis-only scope of BH2.

---

## 6. Evidence Lineage

| Source | Key Finding |
|--------|-------------|
| IL1 (`wave0_injection_trace.md`) | Correct memories confirmed present in actual benchmark prompts for `e47becba` and `58bf7951` |
| IL2 (`wave0_injection_audit.md`) | Benchmark harness bypasses `orchestrator/memory/injection.py` entirely |
| IL3 (`wave0_injection_budget_check.md`) | Zero truncations in 50-query sample; no budget pressure |
| BH1 (`wave0_benchmark_vs_production_injection.md`) | Full architectural comparison of both paths |
| `evaluate.py:391–401` | `build_answer_prompt()` — thin bullet-list user message template |
| `injection.py:171–341` | `build_memory_context()` + `assemble_system_prompt()` — full production pipeline |
| `prompts.py:3–6` | `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` — abstention instruction text |
| `prompts.py:8–156` | `DAEMON_SYSTEM_PROMPT` — full Daemon role and memory guidance |

---

## 7. Summary

**BH2 confirms and tightens the BH1 conclusion:**

The Wave 0 benchmark collapse is attributable to benchmark-path answer-generation failure (IL1), not production injection omission. The architectural separation between benchmark and production paths (IL2) means the benchmark cannot serve as a production-memory quality baseline. The prompt difference — thin bullet-list user message versus structured system prompt with guardrails — is large enough that benchmark scores measure fundamentally different behavior than production memory retrieval.

A live production-path replay for `e47becba` and `58bf7951` was **not** directly executed; the production-path framing comparison is based on current code analysis only.

**Central BH2 conclusion:** The benchmark-path prompt difference is large enough to invalidate using Wave 0 benchmark scores as a production-memory quality baseline.
