# T9 — Static Call-Graph Parity Assertion

**Status**: PASS

## T9 Gate Verdict

The parity path (`parity_evaluate_single` in `tests/longmemeval/parity_harness.py`) satisfies the no-transform invariant after production `assemble_system_prompt()` returns.

**T10 is UNBLOCKED.**

---

## Chain Documentation: Parity Path (`parity_evaluate_single`)

### Entry: Production `build_memory_context()`

**Location**: `parity_harness.py:125-129`

```python
memory_context = await build_memory_context(
    store,
    answer_conversation_id,
    max_tokens=MAX_TOKENS,
)
```

**Classification**: ALLOWED — Assignment only; no transform on return value.

---

### Step 1: Production `assemble_system_prompt()`

**Location**: `parity_harness.py:131-134`

```python
system_prompt = await assemble_system_prompt(
    memory_context=memory_context,
    conversation_id=answer_conversation_id,
)
```

**Classification**: ALLOWED — Assignment only; no transform on return value.

---

### Step 2: Pass to `answer_with_llm()`

**Location**: `parity_harness.py:136-141`

```python
hypothesis = await answer_with_llm(
    question_text,
    memories,
    system_prompt=system_prompt,  # <-- direct pass, unchanged
)
```

**Classification**: ALLOWED — `system_prompt` passed directly as keyword argument. No concat, no strip, no slicing, no format, no regex, no normalization, no encode/decode.

---

### Step 3: `judge_answer()` receives raw `hypothesis`

**Location**: `parity_harness.py:143-145`

```python
judgment = await judge_answer(question_text, hypothesis, reference)
```

**Classification**: ALLOWED — `hypothesis` is raw LLM output passed directly; no transform applied to production prompt.

---

### Step 4: Result dict assembly

**Location**: `parity_harness.py:149-165`

```python
result: dict[str, Any] = {
    "question_id": question_id,
    ...
    "memory_context": memory_context,
    "system_prompt": system_prompt,
}
```

**Classification**: ALLOWED — Structured payload inclusion via dict assignment; `memory_context` and `system_prompt` stored as-is.

---

### Step 5: Return

**Location**: `parity_harness.py:167`

```python
return result
```

**Classification**: ALLOWED — Direct return.

---

## Allowed Operations Classification

| Operation | After `assemble_system_prompt()` return | Location |
|-----------|----------------------------------------|----------|
| Assignment (`=`) | YES | Line 131 |
| Function argument passing | YES | Line 140 |
| Dict key inclusion | YES | Lines 163-164 |
| String concatenation | NO | — |
| String slicing | NO | — |
| Regex | NO | — |
| String formatting (f-string, `.format()`) | NO | — |
| `.strip()` | NO | — |
| `.lower()` / `.upper()` | NO | — |
| `.encode()` / `.decode()` | NO | — |
| Sorting | NO | — |
| Truncation | NO | — |
| Normalization (`split`/`join`) | NO | — |

---

## T1 Inventory Comparison: Legacy vs Parity Paths

### Legacy Path — `evaluate_single()` (T1 Inventory)

**Location**: `evaluate.py:627-714`

| Step | Function | Operation on `system_prompt` |
|------|----------|------------------------------|
| 1 | `embed_query()` | N/A — question embedding |
| 2 | `retrieve_user_memories()` | N/A — retrieval only |
| 3 | `build_assembled_system_prompt(memories)` → internally calls `_format_eval_memory_block()` → then `assemble_system_prompt()` | Assignment only |
| 4 | `_format_eval_memory_block(memories, [])` standalone | Called again for metadata only |
| 5 | `answer_with_llm(..., system_prompt=system_prompt)` | Direct pass |
| 6 | `judge_answer()` | `hypothesis` is raw LLM output |
| 7 | Result dict | Structured inclusion |

**Gap vs parity**: Legacy path calls `_format_eval_memory_block()` (benchmark-local substitute) rather than production `build_memory_context()`.

### Parity Path — `parity_evaluate_single()` (T7)

**Location**: `parity_harness.py:64-167`

| Step | Function | Operation on `system_prompt` |
|------|----------|------------------------------|
| 1 | `embed_query()` | N/A — question embedding |
| 2 | `retrieve_memories_for_text()` | N/A — retrieval only |
| 3 | `build_memory_context()` | Assignment only |
| 4 | `assemble_system_prompt()` | Assignment only |
| 5 | `answer_with_llm(..., system_prompt=system_prompt)` | Direct pass |
| 6 | `judge_answer()` | `hypothesis` is raw LLM output |
| 7 | Result dict | Structured inclusion |

**Parity with production**: Full production call chain used; no benchmark-local formatter.

---

## Chain Coverage Comparison

### T1 Inventory Call-Site Names vs T7 Parity Path

| T1 Inventory Symbol | T1 Line(s) | T7 Parity Usage | T7 Line(s) |
|---------------------|------------|-----------------|-------------|
| `_format_eval_memory_block` | 434, 487, 652 | NOT USED | — |
| `build_assembled_system_prompt` | 477, 490, 651 | NOT USED | — |
| `evaluate_single` | 627 | NOT USED | — |
| `build_memory_context` | NOT called (T1 gap) | `parity_evaluate_single:125` | 125-129 |
| `assemble_system_prompt` | 50, 490 | `parity_evaluate_single:131` | 131-134 |
| `answer_with_llm` | 572, 653 | `parity_evaluate_single:136` | 136-141 |

**Key distinction**: T1 inventory documented the legacy path's use of `_format_eval_memory_block` as a benchmark-local substitute. T7 parity path replaces this with direct production `build_memory_context()`.

---

## Disallowed Operations Evidence

```bash
# grep for any string transforms on system_prompt / memory_context in parity_harness.py
grep -n 'system_prompt.*\.strip\|system_prompt.*\.lower\|system_prompt.*\.upper\|system_prompt.*\.encode\|system_prompt.*\.decode\|system_prompt.*\.format\|system_prompt.*\+ \|memory_context.*\.strip\|memory_context.*\.lower\|memory_context.*\.upper\|memory_context.*\.encode\|memory_context.*\.decode\|memory_context.*\.format\|memory_context.*\+ ' /home/sol/daemon/tests/longmemeval/parity_harness.py
```

**Result**: No matches found.

---

## T10 Unblock Statement

- [x] `harness_parity_static_check.md` exists
- [x] Status declared as PASS
- [x] Line references provided for all chain steps
- [x] Allowed/disallowed operation classification complete
- [x] T1 inventory comparison complete
- [x] No disallowed operations found in parity chain

**T10 is UNBLOCKED.**