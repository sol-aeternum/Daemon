# Wave 0 Benchmark vs Production Injection Divergence

**Artifact type:** BH1 — Architecture divergence documentation
**Evidence basis:** IL1 injection trace, IL2 injection audit, IL3 budget check, IL5 historical diff, plus code-level verification of `evaluate.py` and `injection.py`
**Date:** 2026-04-29

---

## 1. Decisive Implication

**Benchmark scores reflect benchmark-path behavior, not production injection behavior.**

The benchmark harness (`tests/longmemeval/evaluate.py`) bypasses `orchestrator/memory/injection.py` entirely and uses a separate, independent prompt builder (`build_answer_prompt()`). This architectural separation means:

- Changes to `orchestrator/memory/injection.py` — including token budgets, truncation logic, L0 support, guardrails, and formatting — have **no effect** on what the benchmark LLM sees.
- Observed benchmark failures cannot be directly attributed to production injection behavior.
- Production injection was designed for live chat and has always been decoupled from the benchmark path.

This is not a recent regression. The benchmark harness has always used the independent evaluate-path prompt builder. Production injection changes since the 2026-04-10 baseline are architecturally irrelevant to benchmark scores.

---

## 2. Architecture Split

### Production Chat Path

```
orchestrator/main.py
  → build_memory_context()
  → assemble_system_prompt()
  → orchestrator/memory/injection.py (FULL module)
  → SYSTEM PROMPT (structured, categorized, with guardrails and budget enforcement)
  → USER MESSAGE (live user query)
```

**Entry points:** `stream_sse_chat()` in `orchestrator/daemon.py`, `/v1/chat/completions` in `orchestrator/main.py`

**Relevant functions in `orchestrator/memory/injection.py`:**
- `build_memory_context()` — retrieves and formats memory context
- `assemble_system_prompt()` — assembles full system prompt with guardrails
- `retrieve_memories_for_text()` — hybrid retrieval (vector + BM25 + recency + confidence + trust)

### Benchmark Evaluation Path

```
tests/longmemeval/evaluate.py
  → retrieve_user_memories() → retrieve_memories_for_text()
  → build_answer_prompt()
  → answer_with_llm()
  → SINGLE USER MESSAGE ONLY (no system prompt, no production injection)
```

**Entry point:** `evaluate_single()` in `tests/longmemeval/evaluate.py`

**Relevant functions in `tests/longmemeval/evaluate.py`:**
- `retrieve_user_memories()` — thin wrapper around `retrieve_memories_for_text()` with `include_l0=True`
- `build_answer_prompt()` — simple bullet-list prompt builder
- `answer_with_llm()` — sends prompt as a single user message via litellm

---

## 3. Side-by-Side Difference Matrix

| Attribute | Production Path | Benchmark Path |
|-----------|---------------|----------------|
| **Entry point** | `orchestrator/main.py` / `daemon.py` | `tests/longmemeval/evaluate.py` |
| **Injection module** | `orchestrator/memory/injection.py` (full) | **Bypassed entirely** |
| **Prompt structure** | System prompt + user message | Single user message only |
| **Memory formatting** | Categorized (`Fact:`, `Project:`, etc.), per-category labels | Plain bullet list (`- memory content`) |
| **Memory ordering** | L0 frozen block first, then retrieved L1, then session summaries | Flat bullet list, order determined by `retrieve_memories_for_text()` ranking |
| **Per-memory truncation** | `MAX_SINGLE_MEMORY_CHARS = 400` | None |
| **Token budget enforcement** | `DEFAULT_MAX_TOKENS = 2500` via `estimate_tokens()` | Not enforced |
| **L0 support** | `_format_l0_block()` with `L0_TOKEN_BUDGET = 200`, `MAX_L0_CHARS = 600` | `include_l0=True` passed to retrieval, but no L0 formatting block in prompt |
| **System prompt grounding** | Full `DAEMON_SYSTEM_PROMPT` with role instructions | None |
| **Guardrails** | `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` appended to system prompt | None |
| **Memory label prefix** | Yes (`Fact:`, `Project:`, `Preference:`, `Session:`) | No |
| **Retrieval function** | `retrieve_memories_for_text()` | Same function, but result goes directly into thin prompt builder |
| **Retrieval cardinality** | `MAX_MEMORY_ITEMS = 5` | `TOP_K_MEMORIES = 5` (same value, different constant) |
| **Summary injection** | Yes — recent session summaries appended after memories | No |
| **Preferences injection** | Yes — `format_preferences_block()` via `assemble_system_prompt()` | No |
| **Trust signal recording** | Yes — `record_retrieved_memories()` called after retrieval | No |
| **Retrieval logging** | Opt-in via `log_retrieval` | `force_retrieval_logging` on by default |

---

## 4. Detailed Attribute Comparison

### 4.1 Memory Format

**Production (`injection.py`):**
```text
About this user:
- Fact: User graduated with a Bachelor's degree in Computer Science on May 15th, 2022
- Fact: User graduated with a Bachelor's degree in Business Administration five years ago
- Project: User is working on a Python project

Recent context:
- Session: Previous conversation about job applications
```

Each memory is prefixed with a category label. The production path formats L0 memories separately with `[FROZEN MEMORIES]` header, then appends the retrieved L1 memories under `About this user:`, then session summaries under `Recent context:`.

**Benchmark (`evaluate.py::build_answer_prompt()`):**
```text
You are a helpful assistant. Use the provided memories to answer the question concisely.

Memories:
- User graduated with a Bachelor's degree in Computer Science on May 15th, 2022
- User graduated with a Bachelor's degree in Business Administration five years ago
- User is working on a Python project

Question: What degree did I graduate with?

Answer:
```

A flat bullet list with no category labels, no frozen-memory differentiation, no summary section, and no guardrail text.

### 4.2 Inclusion Logic

**Production (`build_memory_context()` in `injection.py`):**
1. Fetch L0 frozen memories → format as `[FROZEN MEMORIES]` block → apply `L0_TOKEN_BUDGET = 200` truncation if needed
2. Retrieve latest user message from conversation history to form query
3. Call `retrieve_memories_for_text()` with `limit=MAX_MEMORY_ITEMS = 5`
4. Truncate each memory to `MAX_SINGLE_MEMORY_CHARS = 400`
5. Format memories with category labels (`Fact:`, `Project:`, etc.)
6. Fetch recent summaries (`MAX_SUMMARY_ITEMS = 3`)
7. Render full context block
8. Apply `DEFAULT_MAX_TOKENS = 2500` budget — iteratively remove memory lines, then summary lines if over budget
9. Return context string; `assemble_system_prompt()` then prepends `DAEMON_SYSTEM_PROMPT` and appends `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`

**Benchmark (`build_answer_prompt()` in `evaluate.py`):**
1. Call `retrieve_user_memories()` which calls `retrieve_memories_for_text()` with `limit=5` and `include_l0=True`
2. Join all retrieved memories with `"\n\n".join(f"- {memory.get('content', '')}" for memory in memories)`
3. Interpolate into hardcoded prompt template (no further processing, no truncation, no budget check)

### 4.3 Token Budget Handling

**Production (`injection.py` lines 269–311):**
```python
effective_token_budget = max(1, max_tokens)  # default max_tokens = 2500

while estimate_tokens(context) > effective_token_budget and memory_lines:
    _ = memory_lines.pop()
    context = render(memory_lines, summary_lines)

while estimate_tokens(context) > effective_token_budget and summary_lines:
    _ = summary_lines.pop()
    context = render(memory_lines, summary_lines)
```

**Benchmark:** No token budget handling exists in `evaluate.py`. The benchmark harness sends the full retrieved memory list without any budget enforcement or truncation based on token count.

### 4.4 System-vs-User Placement

**Production:** Memories are placed in the **system prompt** via `assemble_system_prompt()`, which prepends `DAEMON_SYSTEM_PROMPT`, inserts the memory block, and appends `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`. The user message is the live query.

**Benchmark:** Memories are placed in a **single user message** alongside the instruction and question. There is no system prompt contribution from the memory system.

### 4.5 Abstention Guardrail

**Production (`injection.py` line 330):**
```python
parts.append(MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL.strip())
```

The production path appends `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` (sourced from `orchestrator/prompts.py`) to the assembled system prompt, instructing the model how to handle cases where memory evidence is insufficient.

**Benchmark:** No guardrail text is included. The `build_answer_prompt()` template only contains:
```
You are a helpful assistant. Use the provided memories to answer the question concisely.
```

---

## 5. What This Means for Benchmark Interpretation

| Question | Answer |
|----------|--------|
| Are benchmark scores representative of production injection behavior? | **No.** The benchmark path bypasses production injection entirely. Benchmark scores reflect only the simplified evaluate-path prompt design. |
| Can production injection bugs cause benchmark failures? | **No.** The benchmark does not use the production injection module. |
| Do production injection changes (token budget, L0, guardrails, truncation) affect benchmark scores? | **No.** The two paths are architecturally independent. |
| What do benchmark scores actually measure? | The evaluate-path prompt design — thin bullet-list prompt with no system grounding, no guardrails, no budget enforcement, no category labels. |
| Can production injection improvements improve benchmark scores? | Only if the benchmark harness is updated to use the production injection path. As-is, production changes have zero effect on measured benchmark behavior. |

---

## 6. Evidence Summary

| Source | Key Finding |
|--------|-------------|
| IL1 (`wave0_injection_trace.md`) | Correct memories confirmed present in actual benchmark prompts for `e47becba` and `58bf7951` — rules out injection omission as cause of those failures |
| IL2 (`wave0_injection_audit.md`) | Benchmark harness bypasses `orchestrator/memory/injection.py` entirely — two architecturally independent paths |
| IL3 (`wave0_injection_budget_check.md`) | Zero truncations in 50-query sample; production budget (2500 tokens) is not active — even if production path were used, no truncation would occur |
| IL5 (`wave0_injection_historical_diff.md`) | Production injection changes since 2026-04-10 are a weak explanatory candidate — the benchmark path was already independent before those changes |
| `evaluate.py` lines 391–401 | `build_answer_prompt()` is a simple 12-line function building a bullet-list user message |
| `evaluate.py` lines 470–491 | `answer_with_llm()` sends a single user message with no system prompt contribution |
| `injection.py` lines 171–341 | Full production injection pipeline with budget, L0, truncation, guardrails, and formatting |

---

## 7. Decisive Headline Conclusion

**The benchmark harness evaluates a fundamentally different prompt construction path than production — it uses a thin bullet-list user message with no system grounding, no guardrails, no budget enforcement, and no category structure. Benchmark scores measure evaluate-path behavior only and cannot be used as a proxy for production injection quality.**
