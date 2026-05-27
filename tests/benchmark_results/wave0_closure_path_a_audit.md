# Wave 0 Closure — Path A Production-Injection Completeness Audit

**Task**: I5 — Audit Path A production-injection completeness with one trace capture
**Artifact type**: Investigation artifact (bounded static audit, no provider calls)
**Date**: 2026-05-01
**Scope**: `tests/longmemeval/evaluate.py` consumer path → `orchestrator/memory/injection.py` production injection
**Status**: Complete

---

## 1. Verdict Summary

| Check | Result | Evidence |
|-------|--------|----------|
| `assemble_system_prompt()` called by benchmark | ✅ YES | `evaluate.py:50` import; `evaluate.py:472` call |
| `build_memory_context()` called by benchmark | ❌ NO | `_format_eval_memory_block()` benchmark adapter used instead (evaluate.py:416–456) |
| `DAEMON_SYSTEM_PROMPT` in assembled prompt | ✅ YES | Confirmed in trace `e47becba`; injection.py:318 |
| `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` in assembled prompt | ❌ NO — NOT appended | `assemble_system_prompt()` (injection.py:311–336) does not append guardrail; `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` defined in `prompts.py:3` but never passed to or used by `assemble_system_prompt()` |
| L0 frozen-memory blocks in assembled prompt | ⚠️ RETRIEVED BUT NOT ISOLATED | `include_l0=True` is set (evaluate.py:601); L0 memories retrieved and formatted as regular memories by `_format_eval_memory_block()`; no `[FROZEN MEMORIES]` header in benchmark path |
| `MAX_SINGLE_MEMORY_CHARS=400` enforcement | ✅ YES | `evaluate.py:399` defines it; evaluate.py:435 uses it in `_truncate_to_chars()` |
| `DEFAULT_MAX_TOKENS=2500` enforcement | ❌ NO — NOT ENFORCED | No token-counting in benchmark path; production injection.py:266 enforces via `estimate_tokens()` loop; benchmark adapter has no equivalent |
| Conversation history | N/A | LongMemEval has no production conversation state; `_format_eval_memory_block()` bypasses this (gap is benchmark-appropriate) |
| User preferences | N/A | No preferences block in benchmark path; `assemble_system_prompt()` accepts `preferences_block` param but no preferences are passed from evaluate.py |
| Recent session summaries | ⚠️ BENCHMARK DEFAULT | `summaries=[]` hardcoded in `evaluate_single()` at evaluate.py:634 |
| `retrieval_triggered_by` | ✅ BENCHMARK VALUE SET | `"longmemeval"` at evaluate.py:604 |
| `include_dream_observations` | ✅ TRUE | evaluate.py:605 |

---

## 2. Call Graph — Retrieval to Answer-Model Call

```
evaluate_single()  [evaluate.py:609]
  │
  ├─ embed_query(question_text)  [evaluate.py:621]
  │
  ├─ retrieve_user_memories()  [evaluate.py:623]
  │     └─ retrieve_memories_for_text()  [retrieval.py]
  │           └─ MemoryStore.retrieve_memories()  [store.py]
  │                 └─ get_memories_by_vector(include_l0=True)  [store.py]
  │                     → L0 memories retrieved AND formatted as regular memories
  │
  ├─ build_assembled_system_prompt(memories)  [evaluate.py:633]
  │     ├─ _format_eval_memory_block(memories, summaries=[])  [evaluate.py:416–456]
  │     │     → Formats "About this user:" + "Recent context:" block
  │     │     → Truncates each memory to MAX_SINGLE_MEMORY_CHARS=400
  │     │     → L0 memories NOT given separate [FROZEN MEMORIES] formatting
  │     └─ assemble_system_prompt(memory_context=...)  [evaluate.py:472; injection.py:311]
  │           → Concatenates: DAEMON_SYSTEM_PROMPT + memory_block + memory-tools message
  │           → Does NOT append MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL
  │
  └─ answer_with_llm(question, memories, system_prompt=assembled)  [evaluate.py:635]
        └─ _call_llm_with_provider_config(bm_call_key="answer")  [evaluate.py:571]
              → model: BENCHMARK_ANSWER_MODEL (openrouter/openai/gpt-4o-2024-08-06)
              → temperature: 0.0 (benchmark mode enforced)
              → seed: 42
              → provider.order: ["openai"]
```

---

## 3. `assemble_system_prompt()` Called vs `build_memory_context()` Called

### `assemble_system_prompt()` — YES, called

- **Import** (`evaluate.py:50`): `from orchestrator.memory.injection import assemble_system_prompt`
- **Call site** (`evaluate.py:472`): `return await assemble_system_prompt(memory_context=memory_context)`
- **Called from**: `build_assembled_system_prompt()` at `evaluate.py:459–472`

### `build_memory_context()` — NO, not called

The benchmark harness **does not call** `build_memory_context()` from `injection.py:171–311`.

Instead, `evaluate.py:416–456` implements `_format_eval_memory_block()` — a **benchmark-only adapter** that:
1. Takes pre-retrieved memories and (optionally) pre-retrieved summaries
2. Formats them into the production-style `About this user:` and `Recent context:` block format
3. Truncates each memory item to `MAX_SINGLE_MEMORY_CHARS=400`
4. Does NOT require a live conversation context

### Adapter Equivalence and Gaps

| Production `build_memory_context()` | Benchmark `_format_eval_memory_block()` | Gap? |
|-------------------------------------|----------------------------------------|------|
| Retrieves from live conversation state | Uses pre-retrieved memories | Appropriate — LongMemEval has no conversation state |
| Formats L0 as `[FROZEN MEMORIES]` block with `L0_TOKEN_BUDGET=200` | No L0-specific formatting | **Gap**: L0 memories formatted as plain `Fact:` items |
| Truncates to `MAX_SINGLE_MEMORY_CHARS=400` | Same truncation applied | ✅ Match |
| Enforces `DEFAULT_MAX_TOKENS=2500` via `estimate_tokens()` loop | No token budget enforcement | **Gap**: No budget enforcement in benchmark path |
| Calls `retrieve_memories_for_text()` internally | Memory already retrieved before call | Appropriate |
| Formats with `Fact:`, `Project:`, `Session:` labels | Same labeling applied | ✅ Match |

---

## 4. `DAEMON_SYSTEM_PROMPT` — Confirmed Present

The production `assemble_system_prompt()` (injection.py:318) prepends `DAEMON_SYSTEM_PROMPT`:

```python
parts = [DAEMON_SYSTEM_PROMPT.strip()]
```

Trace evidence: Full `e47becba` system message captured from `wave0_full_corpus_aligned/longmemeval_results.jsonl` (see evidence file `.sisyphus/evidence/task-4-path-a-prompt.md`). The system message begins with the full `DAEMON_SYSTEM_PROMPT` text (version 3, `DAEMON_PROMPT_VERSION = 3` from `prompts.py:1`).

**IMPORTANT**: For `e47becba`, `memories_used = 0` in the aligned run — the memory store was not populated for this question. The captured system message contains `DAEMON_SYSTEM_PROMPT` + the injected memory-tools message, but no memory context block (because none was retrieved).

---

## 5. `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` — NOT Present

### Finding

`assemble_system_prompt()` in `injection.py:311–336` does **NOT** append `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`. The guardrail constant is defined in `orchestrator/prompts.py:3–6`:

```python
MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL = """When a question depends on retrieved memory or recent context, treat that memory as evidence rather than permission to guess.
If the available memory does not directly answer the question, say that you do not know or that the available memory is insufficient.
Do not fill gaps with nearby but non-answering details, inferred timelines, or best guesses.
Only answer confidently when the memory evidence directly supports the answer."""
```

This constant is **never imported or used in `injection.py`**. The `assemble_system_prompt()` function adds, in order:
1. `DAEMON_SYSTEM_PROMPT.strip()`
2. `preferences_block` (if provided)
3. `memory_context` (if provided)
4. A memory-tools access message (if `"memory tools"` not already in text)

### Existing Documentation Error

`tests/benchmark_results/wave0_path_a_implementation.md:70–72` states:
> "MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL from `orchestrator/prompts.py` is appended to the assembled system prompt when `memory_context` is non-empty (injection.py:330)."

This is **factually incorrect**. `injection.py:330` is `if "memory tools" not in assembled.lower():` — it appends the memory-tools message, not the guardrail. The existing Path A implementation doc contains a documentation error.

### Code Evidence

**`injection.py:311–336` (`assemble_system_prompt`):**
```python
async def assemble_system_prompt(
    memory_context: str,
    preferences_block: str | None = None,
    conversation_id: uuid.UUID | None = None,
) -> str:
    del conversation_id

    parts = [DAEMON_SYSTEM_PROMPT.strip()]

    prefs = (preferences_block or "").strip()
    if prefs:
        parts.append(prefs)

    memory_block = memory_context.strip()
    if memory_block:
        parts.append(memory_block)

    assembled = "\n\n".join(part for part in parts if part)
    if "memory tools" not in assembled.lower():
        assembled = (
            assembled
            + "\n\n"
            + "You have access to memory tools for reading and writing durable user and project context."
        )

    return assembled
```

`MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` does not appear in this function. The function does not import it. It is never appended.

**Production chat path** (`daemon.py:stream_sse_chat()` → `assemble_system_prompt()`) also does not append the guardrail, as confirmed by the function source.

### Conclusion

The `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is **not present** in the benchmark-assembled system prompt. This is the **production behavior** — the guardrail is defined in `prompts.py` but is not wired into `assemble_system_prompt()`. The existing Path A implementation doc's claim that the guardrail IS appended is a documentation error. The guardrail text exists but is **not operational** in either the production chat path or the benchmark path via `assemble_system_prompt()`.

---

## 6. L0 Frozen-Memory Blocks

### What the Benchmark Path Does

- `include_l0=True` is set in `retrieve_user_memories()` (evaluate.py:601)
- L0 memories are retrieved via `retrieve_memories_for_text()` with `include_l0=True`
- L0 memories appear in the `memories` list returned to `evaluate_single()`
- `_format_eval_memory_block()` formats ALL memories (including L0) identically: `"- Fact: {truncated_text}"`
- **No `[FROZEN MEMORIES]` header** is produced in the benchmark path

### What Production Does (for reference)

Production `build_memory_context()` calls `_format_l0_block()` (injection.py:104–114) which:
- Produces `[FROZEN MEMORIES]` header
- Applies `MAX_L0_CHARS = 600` truncation per item
- Applies `L0_TOKEN_BUDGET = 200` across the whole L0 block

### Gap

L0 memories are **not differentiated** from regular memories in the benchmark assembled prompt. The `[FROZEN MEMORIES]` header is absent. This is a formatting gap between benchmark adapter and production `build_memory_context()`.

---

## 7. `MAX_SINGLE_MEMORY_CHARS=400` — Confirmed Enforced

`evaluate.py:399`:
```python
MAX_SINGLE_MEMORY_CHARS = 400
```

Used at `evaluate.py:435` in `_format_eval_memory_block()`:
```python
text = _truncate_to_chars(
    _normalize_content(memory.get("content")), MAX_SINGLE_MEMORY_CHARS
)
```

Production `injection.py:35` also defines `MAX_SINGLE_MEMORY_CHARS = 400`. **Values match.**

---

## 8. `DEFAULT_MAX_TOKENS=2500` — NOT Enforced in Benchmark Path

Production `injection.py:34`:
```python
DEFAULT_MAX_TOKENS = 2500
```

Production `injection.py:266` enforces it via an `estimate_tokens()` loop (lines 292–298).

The benchmark adapter `_format_eval_memory_block()` has **no token-counting logic**. There is no equivalent of the `estimate_tokens()` budget loop. This is a gap — a future E-task (not in scope for this audit) would need to add token budget enforcement to the benchmark path if production-level token control is required in the aligned benchmark.

**Recommended E-task fix** (identified, not implemented): Add `estimate_tokens()` call and iterative truncation to `build_assembled_system_prompt()` in evaluate.py, using the same `DEFAULT_MAX_TOKENS=2500` constant or a benchmark-specific value.

---

## 9. Documented Defaults — All Covered

| Component | Benchmark Default | Classification | Source |
|-----------|------------------|-----------------|--------|
| Conversation history | N/A — no live conversation state | Benchmark gap (appropriate) | `_format_eval_memory_block()` bypasses conversation context |
| User preferences | No preferences block passed | Benchmark gap | `build_assembled_system_prompt()` receives `preferences_block=None`; no preferences derived |
| Recent session summaries | `summaries=[]` hardcoded | Benchmark default | `evaluate.py:634` |
| `retrieval_triggered_by` | `"longmemeval"` | Benchmark value | `evaluate.py:604` |
| `include_dream_observations` | `True` | Benchmark value | `evaluate.py:605` |
| `include_l0` | `True` | Benchmark value | `evaluate.py:601` |
| `MAX_SINGLE_MEMORY_CHARS` | `400` | Production match | `evaluate.py:399` |
| Token budget enforcement | None | Gap | No `estimate_tokens()` loop in benchmark adapter |
| `DAEMON_SYSTEM_PROMPT` | Present in assembled prompt | Production behavior | `injection.py:318`; confirmed in trace |
| `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` | Not present | Production behavior (not a bug) | `assemble_system_prompt()` does not append it |

---

## 10. Trace Capture — `e47becba`

### Source

`tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl` — row for `question_id: e47becba`

### Question Details

| Field | Value |
|-------|-------|
| question_id | `e47becba` |
| question | `What degree did I graduate with?` |
| reference | `Business Administration` |
| category | `IE-user` |
| `memories_used` | **0** (no memories retrieved) |
| `judgment` | `incorrect` |
| `hypothesis` | `Let me check that for you. Please hold on a moment.` |

### Prompt Metadata (redacted)

```
system_message: [DAEMON_SYSTEM_PROMPT v3 + Memory Categories section + memory-tools message]
                 Total length: 9232 characters
                 Does NOT contain MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL
user_message:    "What degree did I graduate with?"
memory_content:  "" (empty — no memories retrieved)
memories_raw:    [] (empty list)
model:           openai/gpt-4o-2024-08-06
fingerprint:     fp_3028a26f07
provider_endpoint_slug: "openai"
seed:            42
temperature:     0.0
```

### Why Zero Memories

The aligned run used the production `retrieve_memories_for_text()` path, but the memory store was not populated with test-user memories at the time of the aligned run. This is consistent with the fact that all 500 rows in `wave0_full_corpus_aligned/longmemeval_results.jsonl` have `memories_used = 0`. The prompt structure is correct, but the memory retrieval returned empty for all questions.

### Full Prompt

The complete system message is stored in `.sisyphus/evidence/task-4-path-a-prompt.md`. The memory context section is empty for `e47becba`. The structure when memories ARE retrieved would be:

```
[DAEMON_SYSTEM_PROMPT]

[Memory Categories section from prompts.py]

[About this user:]
- Fact: {truncated memory 1}
- Fact: {truncated memory 2}
...

[Recent context:]
- Session: {summary 1}
- Session: {summary 2}
...

You have access to memory tools for reading and writing durable user and project context.
```

Note: `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is absent from this structure (see Section 5).

---

## 11. Key Finding: Existing Path A Documentation Contains Guardrail Error

`tests/benchmark_results/wave0_path_a_implementation.md:70–72` states that `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is "appended to the assembled system prompt when `memory_context` is non-empty (injection.py:330)."

This is **incorrect**. `injection.py:330` is:
```python
if "memory tools" not in assembled.lower():
```

This appends the memory-tools message. `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is never imported into `injection.py` and is never appended by `assemble_system_prompt()`.

The guardrail text was written to `prompts.py` and may have been intended for injection, but it is not wired into the production `assemble_system_prompt()` function or any call site thereof.

This means:
- The benchmark-assembled prompt **does not contain** the abstention guardrail (confirmed)
- The production-assembled prompt **also does not contain** the guardrail (confirmed by code inspection)
- The existing Path A implementation doc **incorrectly claims** the guardrail is present

A future task may decide to wire `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` into `assemble_system_prompt()` or a calling site, but that is outside the scope of this audit.

---

## 12. Summary Table — Production Elements in Benchmark Path

| Element | In Benchmark Assembled Prompt? | How |
|---------|-------------------------------|-----|
| `DAEMON_SYSTEM_PROMPT` | ✅ YES | `assemble_system_prompt()` prepends it (injection.py:318) |
| `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` | ❌ NO | `assemble_system_prompt()` does not append it |
| Memory categories section | ✅ YES | From `prompts.py` — embedded in `DAEMON_SYSTEM_PROMPT` |
| `About this user:` block | ✅ YES | From `_format_eval_memory_block()` |
| `Recent context:` block | ✅ YES | From `_format_eval_memory_block()` |
| `[FROZEN MEMORIES]` header | ❌ NO | L0 memories formatted as regular `Fact:` items |
| Memory-tools access message | ✅ YES | `assemble_system_prompt()` appends it when not present |
| Token budget enforcement | ❌ NO | No `estimate_tokens()` loop in benchmark adapter |
| Per-item 400-char truncation | ✅ YES | `_truncate_to_chars()` at evaluate.py:435 |

---

## 13. Files Referenced

| File | Role in This Audit |
|------|-------------------|
| `tests/longmemeval/evaluate.py` | Benchmark harness — Path A consumer |
| `orchestrator/memory/injection.py` | Production injection — `assemble_system_prompt()` |
| `orchestrator/prompts.py` | `DAEMON_SYSTEM_PROMPT`, `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` |
| `orchestrator/memory/retrieval.py` | `retrieve_memories_for_text()` — called by benchmark |
| `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl` | Trace capture source |
| `tests/benchmark_results/wave0_path_a_implementation.md` | Existing doc — contains guardrail error |
| `tests/benchmark_results/wave0_path_a_smoke_test.md` | Prior smoke context |
| `tests/benchmark_results/wave0_benchmark_vs_production_injection.md` | Prior divergence doc |
