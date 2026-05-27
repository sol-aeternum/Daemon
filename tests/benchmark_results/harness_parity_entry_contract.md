# Harness Parity Entry Contract

**Date:** 2026-05-23
**Status:** proceed-parity-entry-pinned
**Scope:** Pre-W1 LongMemEval baseline entry-path gate for the parity baseline completion plan.

---

## 1. Decision

**Decision:** `proceed-parity-entry-pinned`

The pre-W1 baseline measurement is pinned to exactly one allowed per-question entry point:

- `tests/longmemeval/parity_harness.py:parity_evaluate_single()`

The baseline token budget is also pinned here:

- `token budget: 2500`

This contract is proven statically from the plan and live source code before any Task 4 runner is created. The plan requires the measurement path to use only `parity_evaluate_single()` and requires the future ephemeral runner to import that function directly rather than route through legacy runner lanes (`.sisyphus/plans/longmemeval-parity-baseline-completion.md:79-82,279-299`).

## 2. Static Contract Proof

### 2.1 Plan authority

- The plan summary and research findings already define the parity path as `tests/longmemeval/parity_harness.py:parity_evaluate_single()` and classify the legacy runners as forbidden for this baseline (`.sisyphus/plans/longmemeval-parity-baseline-completion.md:4,37-39`).
- The plan's must-have section restates: `Measurement path: tests/longmemeval/parity_harness.py:parity_evaluate_single() only` and pins the current baseline token budget to `2500` (`.sisyphus/plans/longmemeval-parity-baseline-completion.md:79-82`).
- Task 4 is explicitly constrained to an ephemeral runner that imports `parity_evaluate_single()` and does not use the fast lane (`.sisyphus/plans/longmemeval-parity-baseline-completion.md:279-299`).

### 2.2 Allowed live code path

`tests/longmemeval/parity_harness.py` is the only inspected path in this task that:

1. creates a synthetic user scoped to the benchmark question,
2. ingests the haystack sessions for that scoped user,
3. creates an answer conversation for that same user,
4. builds `memory_context` via production `build_memory_context(...)`, and
5. assembles the final prompt via production `assemble_system_prompt(...)`.

The critical lines are:

- `parity_evaluate_single()` definition: `tests/longmemeval/parity_harness.py:64-75`
- token budget constant: `tests/longmemeval/parity_harness.py:25-27`
- production memory-context call: `tests/longmemeval/parity_harness.py:125-129`
- production system-prompt call: `tests/longmemeval/parity_harness.py:131-134`
- answer call consuming that prompt: `tests/longmemeval/parity_harness.py:136-140`

## 3. Production Prompt-Surface Call Chain

The static prompt-surface chain for the allowed measurement path is:

1. `tests/longmemeval/parity_harness.py:parity_evaluate_single()`
2. `orchestrator/memory/injection.py:build_memory_context()`
3. `orchestrator/memory/injection.py:assemble_system_prompt()`
4. `tests/longmemeval/evaluate.py:answer_with_llm(system_prompt=system_prompt)`

Supporting proof:

- `MAX_TOKENS = 2500` in parity harness: `tests/longmemeval/parity_harness.py:25-27`
- `build_memory_context(..., max_tokens=MAX_TOKENS)`: `tests/longmemeval/parity_harness.py:125-129`
- production default also remains `DEFAULT_MAX_TOKENS = 2500`: `orchestrator/memory/injection.py:32-35,168-172`
- `assemble_system_prompt(...)` appends `memory_context` directly into the assembled system prompt: `orchestrator/memory/injection.py:311-336`
- `answer_with_llm(..., system_prompt=system_prompt)` sends a real system message when supplied: `tests/longmemeval/evaluate.py:319-344`

That is the required production prompt surface for this baseline gate.

## 4. Forbidden Legacy Paths

The following paths are **forbidden** as baseline measurement entry points for this pre-W1 contract.

### 4.1 `orchestrator.eval.longmemeval`

Forbidden because its CLI constructs `LongMemEvalRunner` and dispatches `run`, `ingest`, and `evaluate` through the canonical runner path instead of the parity harness (`orchestrator/eval/longmemeval.py:77-79,155-173`).

### 4.2 `orchestrator/eval/runner.py`

Forbidden because the canonical runner's evaluation loop calls `evaluate_single(...)`, not `parity_evaluate_single(...)` (`orchestrator/eval/runner.py:1727-1738`).

### 4.3 `orchestrator.eval.longmemeval_fast`

Forbidden because the fast lane also dispatches into `evaluate_single(...)`, not the parity harness (`orchestrator/eval/longmemeval_fast.py:478-487`).

### 4.4 `tests/longmemeval/evaluate.py:evaluate_single()`

Forbidden because the legacy evaluator retrieves memories and then answers via `answer_with_llm(question_text, memories)` with **no** production `build_memory_context()` call and **no** production `assemble_system_prompt()` call in its path (`tests/longmemeval/evaluate.py:376-405`).

### 4.5 Why these are footguns for this baseline

This task is a prompt-surface validity gate, not a generic LongMemEval runner audit. Any lane that bypasses `parity_evaluate_single()` would either:

- skip the production `build_memory_context()` path,
- skip the production `assemble_system_prompt()` path,
- or route through an older runner stack that the plan explicitly labels out of scope for the parity baseline (`.sisyphus/plans/longmemeval-parity-baseline-completion.md:37-39,87-88,238-258`).

## 5. Contract Result

The contract is proven for Task 3:

- allowed measurement entry point: `tests/longmemeval/parity_harness.py:parity_evaluate_single()`
- production prompt-surface path present: `build_memory_context()` and `assemble_system_prompt()`
- forbidden legacy paths explicitly excluded: `orchestrator.eval.longmemeval`, `orchestrator.eval.longmemeval_fast`, `orchestrator/eval/runner.py`, and `tests/longmemeval/evaluate.py:evaluate_single()`
- token budget pinned for this baseline: `2500`

Task 4 may proceed only if the ephemeral runner continues to respect this exact entry contract.
