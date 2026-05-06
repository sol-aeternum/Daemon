# Harness Parity Strip Diff — T6 Gate

**Task**: `6. Strip `_format_eval_memory_block` and sibling parallel formatters`
**Date**: 2026-05-06
**Gate Status**: **T6-GATE HALT**

---

## Scope-Wall Check Result

### T6-GATE VERDICT: **HALT**

The scope-wall check found that `orchestrator/eval/runner.py` and `tests/test_longmemeval_evaluate.py` are **out-of-scope consumers** that would break if the T1 (b) substitute formatters were deleted. Deletion cannot proceed without scope expansion.

---

## T6-GATE Classification

### PASS Condition
> T6 proves deletion/routing can proceed while keeping all out-of-scope consumers read-only and without breaking required execution paths.

**Result**: NOT MET.

### HALT Condition
> T6 proves `orchestrator/eval/runner.py`, test files, or config-pin files must be changed to keep imports, hashes, or corpus execution functional.

**Result**: MET. The following out-of-scope consumers require edits to remain functional:

---

## Blocking Consumers

### 1. `orchestrator/eval/runner.py` — **SCOPE-BLOCKER** (read-only evidence surface)

| Line | Symbol | Usage | Blocking reason |
|---|---|---|---|
| 212 | `_format_eval_memory_block` | Import from `tests.longmemeval.evaluate` | Deletion causes `ImportError` |
| 213 | `build_assembled_system_prompt` | Import from `tests.longmemeval.evaluate` | Deletion causes `ImportError` |
| 626 | `_format_eval_memory_block` | `_build_answer_prompt_contract_payload()` — renders sentinel memory block | Deletion breaks function |
| 633 | `build_assembled_system_prompt` | Hashes into `active_system_prompt_builder_sha256` | Deletion changes hash, breaks config-pin contract |
| 636 | `_format_eval_memory_block` | Hashes into `active_memory_formatter_sha256` | Deletion changes hash, breaks config-pin contract |
| 1727 | `evaluate_single` | Calls `evaluate_single()` in `LongMemEvalRunner.evaluate()` | Deletion causes `NameError` |

**Evidence**:
```
orchestrator/eval/runner.py:212: _format_eval_memory_block,
orchestrator/eval/runner.py:213: build_assembled_system_prompt,
orchestrator/eval/runner.py:626: rendered_memory_block = _format_eval_memory_block(
orchestrator/eval/runner.py:633: "active_system_prompt_builder_sha256": _sha256_source(
orchestrator/eval/runner.py:634:     build_assembled_system_prompt
orchestrator/eval/runner.py:636: "active_memory_formatter_sha256": _sha256_source(_format_eval_memory_block),
orchestrator/eval/runner.py:1727: result = await evaluate_single(
```

### 2. `tests/test_longmemeval_evaluate.py` — **SCOPE-BLOCKER** (read-only evidence surface)

| Line | Symbol | Usage | Blocking reason |
|---|---|---|---|
| 15 | `build_assembled_system_prompt` | Direct import | Deletion causes `ImportError` |
| 16 | `evaluate_single` | Direct import | Deletion causes `ImportError` |
| 630-637 | `build_assembled_system_prompt` | Tests that `build_assembled_system_prompt` includes `DAEMON_SYSTEM_PROMPT` | Deletion breaks test assertions |
| 656-713 | `evaluate_single` | Tests `evaluate_single` uses assembled system prompt | Deletion breaks test |
| 1015-1074 | `evaluate_single` | Tests `evaluate_single` includes retrieved memory IDs and hashes | Deletion breaks test |

**Evidence**:
```
tests/test_longmemeval_evaluate.py:15: build_assembled_system_prompt,
tests/test_longmemeval_evaluate.py:16: evaluate_single,
tests/test_longmemeval_evaluate.py:630: """System prompt assembled via build_assembled_system_prompt includes DAEMON_SYSTEM_PROMPT."""
tests/test_longmemeval_evaluate.py:656: async def test_evaluate_single_uses_assembled_system_prompt(
tests/test_longmemeval_evaluate.py:1015: async def test_evaluate_single_includes_retrieved_memory_ids_and_hashes(
```

### 3. `tests/benchmark_longmemeval/test_config_pinning.py` — **SCOPE-BLOCKER** (read-only evidence surface)

| Line | Symbol | Usage | Blocking reason |
|---|---|---|---|
| 66 | `orchestrator.eval.runner.build_assembled_system_prompt` | Monkeypatches the runner's import of `build_assembled_system_prompt` | Deleting `build_assembled_system_prompt` from `evaluate.py` does not directly break this, but the runner's hash contract would diverge |

### 4. `tests/benchmark_longmemeval/test_teardown_audit.py` — **SCOPE-BLOCKER** (read-only evidence surface)

| Line | Symbol | Usage | Blocking reason |
|---|---|---|---|
| 19 | `evaluate_single` | Imports `evaluate_single` directly | Deletion causes `ImportError` |
| 380, 421, 585 | `evaluate_single` | Calls `evaluate_single` directly for teardown verification | Deletion causes `NameError` |

### 5. `tests/test_longmemeval_runner.py` — **SCOPE-BLOCKER** (read-only evidence surface)

| Line | Symbol | Usage | Blocking reason |
|---|---|---|---|
| 298, 1381, 1464, 1558, 1751 | `evaluate_single` | Mocks `evaluate_single` via monkeypatch | Deletion would change the interface being mocked |

---

## T1 (b) Substitute Formatters — Candidates for Deletion

These are the symbols classified as (b) substitute in T1 inventory that would be deleted if scope allowed:

| Symbol | Location | Used only by (b) formatter? | Deletion would break |
|---|---|---|---|
| `_format_eval_memory_block()` | `evaluate.py:434-474` | No — also called at `evaluate.py:652` | `runner.py:626`, `runner.py:636` (hash), `evaluate.py:487`, `evaluate.py:652` |
| `MAX_MEMORY_ITEMS = 5` | `evaluate.py:416` | Yes | None (used only by `_format_eval_memory_block`) |
| `MAX_SINGLE_MEMORY_CHARS = 400` | `evaluate.py:417` | Yes | None (used only by `_format_eval_memory_block`) |
| `_normalize_content()` | `evaluate.py:420-423` | Yes | None (used only by `_format_eval_memory_block`) |
| `_truncate_to_chars()` | `evaluate.py:426-431` | Yes | None (used only by `_format_eval_memory_block`) |

The duplicate standalone call at `evaluate.py:652` (`memory_context = _format_eval_memory_block(memories, [])`) is the Path B metadata-only call that the plan already identifies as redundant.

---

## Decision Section

```
[DECISION NEEDED: authorize runner.py/test/config-pin scope expansion, or commission a separate plan / alternate harness-native entry point]
```

**Current repository state**: The parity plan cannot proceed with deletion of `_format_eval_memory_block` and sibling formatters because:

1. **`orchestrator/eval/runner.py:636`** hashes `_format_eval_memory_block` into `active_memory_formatter_sha256` as part of the benchmark config-pin contract. Deleting the function would cause an `ImportError` at hash computation time, breaking the canonical runner.

2. **`orchestrator/eval/runner.py:633`** hashes `build_assembled_system_prompt` into `active_system_prompt_builder_sha256`. Deleting `build_assembled_system_prompt` would break this contract.

3. **`runner.py`** imports both `_format_eval_memory_block` and `build_assembled_system_prompt` at lines 212-213. These imports are live references.

4. **`tests/test_longmemeval_evaluate.py`** imports and tests both functions directly.

**Allowed alternative (per T6-GATE)**: A new parity-specific corpus entry point can be implemented entirely under `tests/longmemeval/**` without deleting/rewiring out-of-scope runner imports. Under this path, `orchestrator/eval/runner.py` remains **legacy/out-of-scope for this parity run** and the new harness-native entry point is used for T14's parity corpus run. T7 should implement this path if authorized.

**Test/Config-pin scope expansion path**: If the user explicitly authorizes scope expansion, `runner.py`, `tests/test_longmemeval_evaluate.py`, `tests/benchmark_longmemeval/test_config_pinning.py`, and `tests/benchmark_longmemeval/test_teardown_audit.py` would be updated to remove references to the deleted formatters, and the config-pin contract would be re-hashed against the new production-aligned entry point.

---

## Evidence

- Formatter absent check: `.sisyphus/evidence/task-6-formatter-absent.txt`
- Production clean check: `.sisyphus/evidence/task-6-production-clean.txt`
- Full grep evidence: inherited from T1 evidence files

---

## Status

**T6-GATE: HALT**
**T7: BLOCKED** — do not proceed to T7 implementation until T6-GATE decision is resolved.
