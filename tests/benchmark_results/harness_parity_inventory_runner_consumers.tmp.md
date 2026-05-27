# LongMemEval Harness Runner Consumers Inventory

**Task**: T1 - Map read-only out-of-scope consumers of LongMemEval harness memory formatting symbols
**Generated**: 2026-05-06
**Scope**: All consumers of `_format_eval_memory_block`, `build_assembled_system_prompt`, `evaluate_single`, `TEST_USER_ID`, `TEST_USER_EMAIL`, `active_memory_formatter_sha256`, `answer_prompt_contract`

---

## Summary of Search Evidence

### grep commands run:
```bash
# Symbol: _format_eval_memory_block
grep -rn "_format_eval_memory_block" /home/sol/daemon --include="*.py" --include="*.md"
# Results: 31 matches across 6 files

# Symbol: build_assembled_system_prompt
grep -rn "build_assembled_system_prompt" /home/sol/daemon --include="*.py" --include="*.md"
# Results: 25 matches across 10 files

# Symbol: evaluate_single
grep -rn "evaluate_single" /home/sol/daemon --include="*.py" --include="*.md"
# Results: 116 matches across 26 files

# Symbol: TEST_USER_ID
grep -rn "TEST_USER_ID" /home/sol/daemon --include="*.py" --include="*.md"
# Results: 74 matches across 22 files

# Symbol: TEST_USER_EMAIL
grep -rn "TEST_USER_EMAIL" /home/sol/daemon --include="*.py" --include="*.md"
# Results: 15 matches across 9 files

# Symbol: active_memory_formatter_sha256
grep -rn "active_memory_formatter_sha256" /home/sol/daemon --include="*.py" --include="*.md"
# Results: 2 matches across 2 files

# Symbol: answer_prompt_contract
grep -rn "answer_prompt_contract" /home/sol/daemon --include="*.py" --include="*.md"
# Results: 6 matches across 1 file

# Symbol: from tests.longmemeval.evaluate import
grep -rn "from tests.longmemeval.evaluate import" /home/sol/daemon --include="*.py"
# Results: 10 matches across 7 files

# Symbol: _sha256_source
grep -rn "_sha256_source" /home/sol/daemon --include="*.py"
# Results: 8 matches in runner.py
```

---

## Consumer Classification

### Classification Key:
- **SCOPE-BLOCKER**: Out-of-scope file that would BREAK if the symbol is deleted/changed without corresponding updates
- **SAFE-CONSUMER**: In-scope or internal use that would not block mutation
- **DOCUMENTATION-ONLY**: Markdown/docs referencing the symbol, not code

---

## 1. `_format_eval_memory_block`

**Definition**: `tests/longmemeval/evaluate.py:434-474`
**Purpose**: Benchmark-only adapter that formats memories/summaries WITHOUT calling production `build_memory_context()`

### Consumers:

| File | Line(s) | Classification | Would Break if Deleted? | Notes |
|------|---------|----------------|------------------------|-------|
| `orchestrator/eval/runner.py` | 212 (import), 626, 636 | **SCOPE-BLOCKER** | YES | Imported from `tests.longmemeval.evaluate`. Used in `_build_answer_prompt_contract_payload()` at line 626 to render sentinel memory block. Hashed via `_sha256_source(_format_eval_memory_block)` at line 636 for `active_memory_formatter_sha256` in benchmark config contract. |
| `tests/longmemeval/evaluate.py` | 487, 652 | SAFE-CONSUMER | YES | Internal use within harness itself. Line 487: called in `build_assembled_system_prompt()`. Line 652: called in `evaluate_single()`. |
| `tests/benchmark_results/wave1_benchmark_consumer_path.md` | 6, 10, 15, 17, 20, 41, 43, 58 | DOCUMENTATION-ONLY | N/A | Documents the benchmark consumer path and memory formatter hashing. |

**T6-GATE Relevance**: `runner.py:636` hashes `_format_eval_memory_block` directly into `active_memory_formatter_sha256`. This is the canonical proof that the benchmark-local formatter — NOT production `build_memory_context()` — is the active consumer-path memory formatter.

---

## 2. `build_assembled_system_prompt`

**Definition**: `tests/longmemeval/evaluate.py:477-490`
**Purpose**: Builds production-aligned system prompt for eval by calling `_format_eval_memory_block()` then `assemble_system_prompt()`

### Consumers:

| File | Line(s) | Classification | Would Break if Deleted? | Notes |
|------|---------|----------------|------------------------|-------|
| `orchestrator/eval/runner.py` | 213 (import), 633, 634 | **SCOPE-BLOCKER** | YES | Imported from `tests.longmemeval.evaluate`. Hashed via `_sha256_source(build_assembled_system_prompt)` at line 633 for `active_system_prompt_builder_sha256` in answer prompt contract. |
| `tests/test_longmemeval_evaluate.py` | 15 (import), 630, 637 | SAFE-CONSUMER | YES | Imports `build_assembled_system_prompt` and tests it. Uses at line 630-637 to verify system prompt assembly includes DAEMON_SYSTEM_PROMPT. |
| `tests/benchmark_longmemeval/test_config_pinning.py` | 66 (monkeypatch) | SAFE-CONSUMER | YES | Monkeypatches `orchestrator.eval.runner.build_assembled_system_prompt` at line 66 to verify config drift detection works. This is a TEST-ONLY consumer of the runner's import, not the evaluate.py definition. |
| `tests/benchmark_results/wave1_benchmark_consumer_path.md` | 10, 14, 17, 41 | DOCUMENTATION-ONLY | N/A | Documents benchmark consumer path. |
| `tests/benchmark_results/wave0_closure_path_a_audit.md` | 16, 19, 22, 43, 68, 227, 236, 341, 342 | DOCUMENTATION-ONLY | N/A | Documents path A audit findings. |
| `tests/benchmark_results/wave0_final_summary.md` | 57 | DOCUMENTATION-ONLY | N/A | Notes production-style prompt assembly. |

**T6-GATE Relevance**: `runner.py:633` hashes `build_assembled_system_prompt` into `active_system_prompt_builder_sha256`. Since `build_assembled_system_prompt` internally calls `_format_eval_memory_block`, any change to the formatter function changes both hashes.

---

## 3. `evaluate_single`

**Definition**: `tests/longmemeval/evaluate.py:627-714`
**Purpose**: Core evaluation function that builds system prompt and calls answer LLM

### Consumers:

| File | Line(s) | Classification | Would Break if Deleted? | Notes |
|------|---------|----------------|------------------------|-------|
| `orchestrator/eval/runner.py` | 216 (import), 1727-1738 | **SCOPE-BLOCKER** | YES | Imported from `tests.longmemeval.evaluate`. Called at line 1727 in `LongMemEvalRunner.evaluate()` with `allowed_source_conversation_ids` parameter. |
| `orchestrator/eval/longmemeval_fast.py` | 21 (import), 478-487 | **SCOPE-BLOCKER** | YES | Imported from `tests.longmemeval.evaluate`. Called at line 478 in `LongMemEvalFastRunner.run()`. Passes `user_id=benchmark_user_id` (fresh per-run) and `allowed_source_conversation_ids=conversation_ids`. |
| `tests/test_longmemeval_runner.py` | 298-320, 401-418, 1381-1605, 1751-1810 | SAFE-CONSUMER | YES | Mocks `evaluate_single` at multiple locations. Uses `monkeypatch.setattr("orchestrator.eval.runner.evaluate_single", ...)` to control behavior. |
| `tests/test_longmemeval_evaluate.py` | 16 (import), 656-714, 1015-1074 | SAFE-CONSUMER | YES | Imports and tests `evaluate_single` directly. Tests at lines 656-714 verify `uses_assembled_system_prompt`, at 1015-1074 verify `includes_retrieved_memory_ids_and_hashes`. |
| `tests/benchmark_longmemeval/test_teardown_audit.py` | 19 (import), 212-213, 380, 421, 585 | SAFE-CONSUMER | YES | Imports `evaluate_single`. Line 212-213: documents canonical vs fast lane units. Lines 380, 421, 585: calls `evaluate_single` directly for teardown verification. |
| `tests/benchmark_results/wave0_closure_memories_used_zero_diagnosis.md` | 115, 448 | DOCUMENTATION-ONLY | N/A | Documents `user_id=TEST_USER_ID` default behavior. |
| `tests/benchmark_results/wave1_benchmark_consumer_path.md` | 10, 11, 12, 42 | DOCUMENTATION-ONLY | N/A | Documents canonical consumer path. |
| `tests/benchmark_longmemeval/ISOLATION_AUDIT.md` | 39, 49, 70, 104 | DOCUMENTATION-ONLY | N/A | Documents isolation properties of `evaluate_single`. |
| `tests/benchmark_longmemeval/CONTAMINATION_ANALYSIS.md` | 23, 88 | DOCUMENTATION-ONLY | N/A | Documents standalone vs canonical evaluation paths. |

**T6-GATE Relevance**: Both canonical runner (`runner.py:1727`) and fast runner (`longmemeval_fast.py:478`) call `evaluate_single`. These are the two primary entry points for benchmark evaluation. Deleting `evaluate_single` would require replacing both call sites.

---

## 4. `TEST_USER_ID`

**Definition**: `tests/longmemeval/ingest.py:40` and `tests/longmemeval/evaluate.py:84`
**Value**: `uuid.UUID("12345678-1234-5678-1234-567812345678")`

### Consumers:

| File | Line(s) | Classification | Would Break if Deleted? | Notes |
|------|---------|----------------|------------------------|-------|
| `orchestrator/eval/runner.py` | 225 (import), 260, 366, 768, 1868, 1871 | **SCOPE-BLOCKER** | YES | Imported from `tests.longmemeval.ingest`. Used for canonical cleanup (`cleanup_canonical_benchmark_state` deletes rows for `TEST_USER_ID`), benchmark user identity in config, and reset operations. |
| `orchestrator/eval/longmemeval_fast.py` | 27 (import) | SAFE-CONSUMER | YES (but fast uses per-run users) | Imports `TEST_USER_ID` but fast lane creates fresh per-run users. Import exists for type reference only. |
| `tests/longmemeval/evaluate.py` | 84, 635 | SAFE-CONSUMER | YES | Defines and uses `TEST_USER_ID` as default `user_id` parameter in `evaluate_single()`. |
| `tests/longmemeval/ingest.py` | 40, 205 | SAFE-CONSUMER | YES | Defines `TEST_USER_ID` and uses it in `build_benchmark_user()` and `ingest_session()`. |
| `tests/test_longmemeval_ingest.py` | 15 (import), 80, 105, 130, 152, 160, 186, 211, 236, 350 | SAFE-CONSUMER | YES | Imports `TEST_USER_ID` and uses it to verify test behavior. |
| `tests/test_longmemeval_runner.py` | 667 (import), 830, 902 | SAFE-CONSUMER | YES | Imports `TEST_USER_ID` and uses it for assertions. |
| `scripts/test_session_memory_alignment.py` | 14 | **NOT A CONSUMER** | NO | Defines its own local `TEST_USER_ID = "12345678-1234-5678-1234-567812345678"` string constant. Does NOT import from `tests.longmemeval.ingest`. |
| `scripts/test_retrieval_quality.py` | 18 | **NOT A CONSUMER** | NO | Defines its own local `TEST_USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")`. Does NOT import from `tests.longmemeval.ingest`. |
| `tests/benchmark_harness/reset_verify_helper.py` | 33, 82, 91, 102, 108, 116 | SAFE-CONSUMER | YES | Uses `TEST_USER_ID` constant directly for reset verification. |
| `tests/benchmark_harness/verify_reset_completeness.py` | 38, 53, 115 | SAFE-CONSUMER | YES | Imports and uses `TEST_USER_ID` for reset verification. |
| `tests/benchmark_longmemeval/dedup_sweep.py` | 40 (import), 245, 273, 276, 305, 344, 348, 352 | SAFE-CONSUMER | YES | Imports `TEST_USER_ID` and uses it for sweep operations. |
| `tests/benchmark_longmemeval/top_k_sweep.py` | 29 (import), 226, 437, 442, 452, 467, 482 | SAFE-CONSUMER | YES | Imports `TEST_USER_ID` and uses it for top-k sweep. |

**T6-GATE Relevance**: `runner.py` uses `TEST_USER_ID` for canonical cleanup and user identity. The canonical runner does NOT use a fresh per-run user; it reuses the shared `TEST_USER_ID` across all corpus sessions. This is an architectural constraint, not an implementation detail.

---

## 5. `TEST_USER_EMAIL`

**Definition**: `tests/longmemeval/ingest.py:39` and `tests/longmemeval/evaluate.py:85`
**Value**: `"longmemeval@daemon.test"`

### Consumers:

| File | Line(s) | Classification | Would Break if Deleted? | Notes |
|------|---------|----------------|------------------------|-------|
| `orchestrator/eval/runner.py` | 224 (import), 769 | **SCOPE-BLOCKER** | YES | Imported from `tests.longmemeval.ingest`. Used in `build_longmemeval_pinned_config()` at line 769 for canonical benchmark user email in config. |
| `orchestrator/eval/longmemeval_fast.py` | 27 (import) | SAFE-CONSUMER | YES (but fast uses per-run emails) | Imports but fast lane creates fresh per-run emails. |
| `tests/longmemeval/ingest.py` | 39, 198, 213, 223 | SAFE-CONSUMER | YES | Defines and uses in user creation/deletion. |
| `tests/longmemeval/__init__.py` | 3, 5 | SAFE-CONSUMER | YES | Re-exports `TEST_USER_EMAIL` for module consumers. |
| `tests/test_longmemeval_ingest.py` | 14 (import), 348 | SAFE-CONSUMER | YES | Imports and verifies value. |
| `tests/test_longmemeval_runner.py` | 667 (import) | SAFE-CONSUMER | YES | Imports for test assertions. |

**T6-GATE Relevance**: `runner.py:769` pins `TEST_USER_EMAIL` in the benchmark config. Fast lane does NOT use this email (it creates fresh per-run emails), but the canonical lane uses it for the shared benchmark user.

---

## 6. `active_memory_formatter_sha256`

**Definition**: `orchestrator/eval/runner.py:636`
**Value**: `_sha256_source(_format_eval_memory_block)`

### Consumers:

| File | Line(s) | Classification | Would Break if Changed? | Notes |
|------|---------|----------------|------------------------|-------|
| `orchestrator/eval/runner.py` | 636 | **SCOPE-BLOCKER** | YES | This IS the definition. The value is computed from `_format_eval_memory_block` source. |
| `tests/benchmark_results/wave1_benchmark_consumer_path.md` | 20 | DOCUMENTATION-ONLY | N/A | Documents that this hash proves the benchmark-local formatter is the active consumer-path formatter. |

**T6-GATE Relevance**: CRITICAL. This hash is the smoking gun that proves the benchmark uses `_format_eval_memory_block` (benchmark-local) rather than production `build_memory_context()`.

---

## 7. `answer_prompt_contract` / `_build_answer_prompt_contract_payload()`

**Definition**: `orchestrator/eval/runner.py:621-646`
**Purpose**: Builds the answer prompt contract payload with SHA256 hashes of all prompt-building components

### Consumers:

| File | Line(s) | Classification | Would Break if Deleted? | Notes |
|------|---------|----------------|------------------------|-------|
| `orchestrator/eval/runner.py` | 621-646 (def), 651 (call) | **SCOPE-BLOCKER** | YES | Defines `_build_answer_prompt_contract_payload()` and calls it at line 651 in `build_longmemeval_pinned_config()`. |
| `tests/benchmark_results/wave1_benchmark_consumer_path.md` | 20 | DOCUMENTATION-ONLY | N/A | References the contract hashing. |

**T6-GATE Relevance**: This function ties together `build_assembled_system_prompt` and `_format_eval_memory_block` hashes into the benchmark config contract. Any change to either function changes the contract.

---

## 8. Import Chain Summary: `from tests.longmemeval.evaluate import`

### Files that import from `tests.longmemeval.evaluate`:

| File | Symbols Imported | Classification |
|------|-----------------|----------------|
| `orchestrator/eval/runner.py:196-221` | `_format_eval_memory_block`, `build_assembled_system_prompt`, `evaluate_single`, `ANSWER_MAX_TOKENS`, `ANSWER_MODEL`, `ANSWER_TEMPERATURE`, `BENCHMARK_*`, `CATEGORY_MAP`, `CHECKPOINT_FILENAME`, `JUDGE_*`, `RESULTS_FILENAME`, `TOP_K_MEMORIES`, `get_benchmark_tracking`, `print_results`, `score_accuracy`, `write_results_jsonl` | **SCOPE-BLOCKER** |
| `orchestrator/eval/longmemeval_fast.py:19-26` | `CATEGORY_MAP`, `evaluate_single`, `load_checkpoint`, `save_checkpoint`, `score_accuracy`, `write_results_jsonl` | **SCOPE-BLOCKER** |
| `tests/test_longmemeval_evaluate.py:8-21` | `BENCHMARK_SEED`, `BenchmarkSamplingError`, `TOP_K_MEMORIES`, `_call_llm_with_provider_config`, `_BM_METADATA`, `answer_with_llm`, `build_assembled_system_prompt`, `evaluate_single`, `judge_answer`, `reset_benchmark_tracking`, `get_benchmark_tracking`, `score_accuracy` | SAFE-CONSUMER |
| `tests/test_longmemeval_runner.py` | Mocks `evaluate_single` via monkeypatch (not direct import in source, but used in test) | SAFE-CONSUMER |
| `tests/benchmark_longmemeval/test_teardown_audit.py:19` | `evaluate_single` | SAFE-CONSUMER |
| `tests/benchmark_longmemeval/test_config_pinning.py:15` | `build_answer_prompt` | SAFE-CONSUMER |
| `tests/benchmark/test_provider_pinning.py:11, 316, 340` | Various prompt functions | SAFE-CONSUMER |
| `tests/benchmark_longmemeval/test_retrieval_log_smoke.py:15` | `retrieve_user_memories` | SAFE-CONSUMER |

---

## Decision Gates Identified

### Gate 1: Deleting `_format_eval_memory_block`
**Impact**: CRITICAL - Would break:
1. `runner.py:636` - `active_memory_formatter_sha256` hash would fail (ImportError)
2. `runner.py:626` - `_build_answer_prompt_contract_payload()` would fail
3. `evaluate.py:487, 652` - Internal harness use would fail

**Required Action**: If `_format_eval_memory_block` is to be replaced with production `build_memory_context()`, must also:
- Update `runner.py` imports and call sites
- Update `_build_answer_prompt_contract_payload()` to hash production function instead
- Update `evaluate.py` to use production path (or keep adapter with different name)

### Gate 2: Deleting `build_assembled_system_prompt`
**Impact**: HIGH - Would break:
1. `runner.py:633` - `active_system_prompt_builder_sha256` hash would fail
2. `tests/test_longmemeval_evaluate.py:15, 630-637` - Test would fail
3. `tests/benchmark_longmemeval/test_config_pinning.py:66` - Monkeypatch target would be gone

**Required Action**: If replaced, must also update runner hash and tests.

### Gate 3: Deleting `evaluate_single`
**Impact**: CRITICAL - Would break:
1. `runner.py:216, 1727` - Canonical runner cannot evaluate
2. `longmemeval_fast.py:21, 478` - Fast runner cannot evaluate
3. Multiple test files would fail

**Required Action**: Both `runner.py` and `longmemeval_fast.py` must be updated simultaneously, or a coordinated replacement strategy is needed.

### Gate 4: Changing `TEST_USER_ID` Value
**Impact**: HIGH - Would break:
1. `runner.py:260, 768` - Config pin would have wrong user ID
2. All cleanup operations targeting `TEST_USER_ID` would leave stale data

**Required Action**: Cannot change without coordinated migration of all benchmark data.

### Gate 5: `scripts/test_session_memory_alignment.py` and `scripts/test_retrieval_quality.py`
**Status**: NOT CONSUMERS - These scripts define their OWN `TEST_USER_ID` constant locally and do NOT import from `tests.longmemeval.ingest`. They reference the same UUID value but are not coupled to the module.

---

## T6-GATE Evidence Summary

The following are DIRECT EVIDENCE that the benchmark harness does NOT use production `build_memory_context()`:

1. **`runner.py:636`**: `active_memory_formatter_sha256 = _sha256_source(_format_eval_memory_block)` — hashes benchmark-local formatter
2. **`runner.py:633`**: `active_system_prompt_builder_sha256 = _sha256_source(build_assembled_system_prompt)` — hashes harness adapter
3. **`runner.py:212, 213`**: Imports `_format_eval_memory_block` and `build_assembled_system_prompt` directly from `tests.longmemeval.evaluate`
4. **`evaluate.py:487`**: `build_assembled_system_prompt()` calls `_format_eval_memory_block()` first, then `assemble_system_prompt()`

**Conclusion**: The benchmark answer path constructs `memory_context` using the benchmark-local `_format_eval_memory_block()` adapter, NOT the production `build_memory_context()` function. Any Wave 1 changes confined to production prompt-surface construction in `orchestrator/memory/injection.py` cannot be measured by the current benchmark consumer path.
