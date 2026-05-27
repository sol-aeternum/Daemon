# Wave 0 Injection Historical Diff

**Artifact type:** Historical change analysis
**Evidence basis:** Code diff between current `orchestrator/memory/injection.py` and commit `c5a2a75787e5f0d9701def2ae1bb78e11af9e5d4` (2026-04-10)
**Date:** 2026-04-29

---

## 1. Purpose

This document summarizes the concrete diff themes between the current state of `orchestrator/memory/injection.py` and the baseline-window commit from 2026-04-10 (`c5a2a75787e5f0d9701def2ae1bb78e11af9e5d4`). It then states the crucial caveat: the historical benchmark harness also used the evaluate-path prompt builder, so these production injection diffs cannot by themselves explain the benchmark score collapse.

---

## 2. Diff Themes: Current vs 2026-04-10 Baseline

### 2.1 Import Change

| Aspect | Historical (c5a2a757) | Current |
|--------|----------------------|---------|
| Retrieval import | `retrieve_memories` | `retrieve_memories_for_text` |

The function name changed, reflecting an internal refactor in the retrieval module.

### 2.2 New `include_local` Parameter

| Aspect | Historical (c5a2a757) | Current |
|--------|----------------------|---------|
| `include_local` | Not present | Supported in retrieval call |

The current version passes `include_local` to the retrieval function.

### 2.3 Token Budget Configuration

| Aspect | Historical (c5a2a757) | Current |
|--------|----------------------|---------|
| `DEFAULT_MAX_TOKENS` | Not observed in diff | `2500` |

The current version has an explicit `DEFAULT_MAX_TOKENS = 2500` setting.

### 2.4 Per-Memory Truncation

| Aspect | Historical (c5a2a757) | Current |
|--------|----------------------|---------|
| `MAX_SINGLE_MEMORY_CHARS` | Not present | `400` |

The current version enforces `MAX_SINGLE_MEMORY_CHARS = 400` per-memory truncation.

### 2.5 L0 Support

| Aspect | Historical (c5a2a757) | Current |
|--------|----------------------|---------|
| L0 token budget | Not present | `L0_TOKEN_BUDGET = 200` |
| L0 char limit | Not present | `MAX_L0_CHARS = 600` |
| L0 formatting | Not present | `_format_l0_block()` function present |

The current version adds L0 (lower-priority) memory handling with its own budget and formatting.

### 2.6 Guardrail Addition

| Aspect | Historical (c5a2a757) | Current |
|--------|----------------------|---------|
| `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` | Not present | Appended in `assemble_system_prompt()` |

The current version appends `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` to the assembled system prompt, providing the model with explicit instruction on how to handle gaps in memory evidence.

---

## 3. Summary of Diff Themes

The production injection module has evolved in the following dimensions since the 2026-04-10 baseline:

1. **Retrieval function naming:** Updated from `retrieve_memories` to `retrieve_memories_for_text`
2. **New parameter:** `include_local` support added
3. **Explicit budget:** `DEFAULT_MAX_TOKENS = 2500` made explicit
4. **Per-memory truncation:** `MAX_SINGLE_MEMORY_CHARS = 400` introduced
5. **L0 support:** New L0 token budget (`200`) and char limit (`600`) with dedicated `_format_l0_block()`
6. **Guardrail:** `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` added to system prompt

These are substantive changes to the production injection pipeline.

---

## 4. Crucial Caveat: Benchmark Harness Independence

### 4.1 The Historical Benchmark Harness

**Critically important:** The benchmark harness at commit `91ab1662` (the historical benchmark run) used:

- `tests/longmemeval/evaluate.py::build_answer_prompt()`
- `tests/longmemeval/evaluate.py::answer_with_llm()`

This harness **does not use** `orchestrator/memory/injection.py`. It constructs a single user message via a separate simple prompt builder.

### 4.2 Implication for Historical Diff

This means:

1. **The production injection diffs (Section 2 above) do not apply to the benchmark path.** The benchmark harness was not using the production injection module in the historical baseline either, so changes to the production module cannot explain benchmark score changes between baseline and current.

2. **Any production injection changes are architecturally irrelevant to benchmark scores** unless the benchmark harness is updated to use the production injection path.

3. **The benchmark collapse, if it occurred between 2026-04-10 and the current state, cannot be attributed to production injection module changes** because the benchmark path has always bypassed that module.

---

## 5. What This Means for Diagnosis

The production injection historical diff is a **weak explanatory candidate** for the Wave 0 benchmark collapse for the following reasons:

| Reason | Explanation |
|--------|-------------|
| Benchmark bypass | The benchmark harness has always used the evaluate-path prompt builder, not `orchestrator/memory/injection.py` |
| Historical consistency | The benchmark harness at `91ab1662` (the failing run) already used the evaluate path |
| No injection dependency | Production injection changes since 2026-04-10 do not affect what the benchmark harness sends to the LLM |
| Budget check | IL3 shows no truncation is occurring in sampled queries, so even if the production path were used, budget is not a bottleneck |

**Therefore, attributing the benchmark collapse to production injection changes would be incorrect. The benchmark path and production path are architecturally independent, and the benchmark has always been independent.**

---

## 6. Related Evidence References

- **IL1** (`wave0_injection_trace.md`): Correct memories are present in actual benchmark prompts — rules out injection omission
- **IL2** (`wave0_injection_audit.md`): Documents the two divergent paths and confirms benchmark bypasses production injection
- **IL3** (`wave0_injection_budget_check.md`): Shows zero truncation in 50-query sample

---

## 7. Summary Table

| Change | Affects Production? | Affects Benchmark? |
|--------|--------------------|--------------------|
| Import name (`retrieve_memories` → `retrieve_memories_for_text`) | Yes | No |
| `include_local` parameter | Yes | No |
| `DEFAULT_MAX_TOKENS = 2500` | Yes | No |
| `MAX_SINGLE_MEMORY_CHARS = 400` | Yes | No |
| L0 support (`L0_TOKEN_BUDGET`, `MAX_L0_CHARS`, `_format_l0_block()`) | Yes | No |
| `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` | Yes | No |

**None of these production injection changes affect the benchmark evaluation path, which bypasses `orchestrator/memory/injection.py` entirely.**
