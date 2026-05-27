# Wave 0 Injection Trace

**Artifact type:** Documentation (IL1)
**Evidence basis:** Actual benchmark-path prompt traces recovered from current store
**Date:** 2026-04-29

---

## 1. Overview

This document records the exact prompts sent to the LLM for two specific benchmark traces — `e47becba` and `58bf7951` — as recovered from the current store state. The purpose is to determine whether an **injection omission bug** (the model never received the correct memory) could explain the benchmark collapse for these cases.

---

## 2. Trace `e47becba`

**Question:** "What degree did I graduate with?"

### Exact prompt structure

`tests/longmemeval/evaluate.py::build_answer_prompt()` creates a prompt that is sent as a **single user message** (not a system prompt) by `answer_with_llm()`. The prompt contains:

A bullet list of five degree-related memories, including **both** of the following:

- "User graduated with a Bachelor's degree in Computer Science on May 15th, 2022"
- "User graduated with a Bachelor's degree in Business Administration five years ago"

Plus three additional degree-related memories.

### Key memory presence check

| Memory | Status |
|--------|--------|
| Bachelor's in Computer Science (May 15th, 2022) | **PRESENT** in prompt |
| Bachelor's in Business Administration (five years ago) | **PRESENT** in prompt |

Both degree memories are present in the prompt sent to the LLM.

### Conclusion for `e47becba`

**IL1 shows NO injection omission bug.** The correct memory (Business Administration) is present in the prompt text. The model was not prevented from seeing the correct fact — it was present alongside a conflicting fact (Computer Science). The failure is downstream: the model selected the wrong fact or failed to use the correct one.

---

## 3. Trace `58bf7951`

**Question:** "What play did I attend at the local community theater?"

### Exact prompt structure

`tests/longmemeval/evaluate.py::build_answer_prompt()` creates a prompt sent as a **single user message** by `answer_with_llm()`. The prompt contains:

A bullet list of theater-related memories including:

- "User attended a local production of The Glass Menagerie at their community theater in late March"
- "Rent" (movie-related memory)
- "The Crucible" ushering memory
- Two additional theater-related memories

### Key memory presence check

| Memory | Status |
|--------|--------|
| The Glass Menagerie at community theater in late March | **PRESENT** in prompt |
| Rent | **PRESENT** in prompt |
| The Crucible (ushering) | **PRESENT** in prompt |

The correct memory (The Glass Menagerie) is present in the prompt.

### Conclusion for `58bf7951`

**IL1 shows NO injection omission bug.** The correct memory (The Glass Menagerie) is present in the prompt text. The model answered "The Crucible" despite The Glass Menagerie being available in context. This is an answer-generation or context-use failure, not a memory injection failure.

---

## 4. IL1 Verdict

For both `e47becba` and `58bf7951`, the verified evidence from actual prompt traces shows:

1. The correct memory is **present in the prompt** sent to the LLM.
2. The correct memory is **not omitted or truncated** by the injection pipeline.
3. The failure mode is **answer-generation / context-use failure**, not injection omission.

**IL1 rules out "model never saw the correct memory" as the primary failure cause for these two traces.**

---

## 5. Caveats

- These are two specific traces. Other traces in DB-D (`118b2229`, `51a45a95`, `c5e8278d`) are described as ambiguous/decomposed/wrong store state cases and require separate investigation.
- The prompt traces reflect the current store state. If the store has changed since the original benchmark run, these traces may not perfectly reflect what was sent during the failed benchmark.
- These traces were recovered from the current recovered store, which may have been affected by subsequent reset/recovery operations.
