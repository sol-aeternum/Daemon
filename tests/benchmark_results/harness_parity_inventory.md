# Harness Parity Inventory — T1 Artifact
**Task**: Map all harness-side memory-formatting code paths
**Generated**: 2026-05-06
**Scope**: `tests/longmemeval/**` — memory shape, ordering, retrieval scope, or rendering before the answering model call

---

## 1. Executive Summary

The LongMemEval harness in `tests/longmemeval/evaluate.py` has **two parallel memory-formatting code paths**:

1. **Path A (production-aligned)**: `_format_eval_memory_block()` → `assemble_system_prompt()` via `build_assembled_system_prompt()`
2. **Path B (benchmark-only substitute)**: `_format_eval_memory_block()` standalone — used only for checkpoint metadata

The harness **never calls** production `build_memory_context()`. This is the core parity gap.

---

## 2. Classification Taxonomy

| Class | Description |
|---|---|
| **(a) calls production assembly** | Calls `assemble_system_prompt()` from `orchestrator.memory.injection` |
| **(b) substitutes for production assembly** | Benchmark-local formatter that replaces `build_memory_context()` behavior |
| **(c) post-processes production output** | Takes already-assembled prompt and modifies it |
| **(d) orchestrator** | Combines multiple paths above |
| **(r) retrieval infrastructure** | Wraps retrieval without formatting memory text |

---

## 3. File Inventory

### `tests/longmemeval/evaluate.py` (966 lines)

#### 3.1 `_format_eval_memory_block()` — **[CLASSIFICATION: (b) substitute]**

**Location**: lines 434-474

**What it does**:
- Formats pre-retrieved `memories` and `summaries` into "About this user:" / "Recent context:" text blocks
- Truncates each memory to `MAX_SINGLE_MEMORY_CHARS = 400` chars
- Limits to `MAX_MEMORY_ITEMS = 5` memories and summaries
- No token-counting budget loop
- No L0-differentiated rendering (renders all as `"- Fact: ..."`)
- No `[FROZEN MEMORIES]` header

**Preserves/changes fields**:
| Field | Behavior | Gap? |
|---|---|---|
| `allowed_source_conversation_ids` | N/A (retrieval, not this function) | — |
| `retrieval_triggered_by` | N/A (retrieval, not this function) | — |
| `include_dream_observations` | N/A (retrieval, not this function) | — |
| L0 formatting | ALL memories formatted identically as `"- Fact: ..."` | ⚠️ GAP: production has `[FROZEN MEMORIES]` header |
| Summaries | Rendered as `"- Session: ..."` | ✅ Matches production render |
| Preferences | NOT included | ⚠️ GAP: production can include via `preferences_block` |
| Token-budget trimming | NONE | ⚠️ GAP: production has `estimate_tokens()` budget loop |

**Callers**:
- `build_assembled_system_prompt()` at line 487
- `evaluate_single()` at line 652 (standalone, only for checkpoint metadata)

---

#### 3.2 `build_assembled_system_prompt()` — **[CLASSIFICATION: (a) calls production assembly]**

**Location**: lines 477-490

**What it does**:
- Calls `_format_eval_memory_block(memories, summaries if summaries else [])` first
- Then calls production `assemble_system_prompt(memory_context=memory_context)`
- `assemble_system_prompt()` prepends `DAEMON_SYSTEM_PROMPT` and appends memory-tools message

**Preserves/changes fields**:
| Field | Behavior | Gap? |
|---|---|---|
| `allowed_source_conversation_ids` | Passthrough via retrieval | ✅ Preserved |
| `retrieval_triggered_by` | Passthrough via retrieval | ✅ Preserved |
| `include_dream_observations` | Passthrough via retrieval | ✅ Preserved |
| L0 formatting | GAP: `_format_eval_memory_block` flattens all to `"- Fact: ..."` | ⚠️ GAP |
| Summaries | ✅ Rendered | ✅ Preserved |
| Preferences | NOT included (no `preferences_block` passed) | ⚠️ GAP |
| Token-budget trimming | NONE in harness; production `assemble_system_prompt()` has none either | ⚠️ GAP in production too |

**Note**: Despite calling production `assemble_system_prompt()`, the memory_context fed to it is produced by the benchmark-local `_format_eval_memory_block()`, NOT by production `build_memory_context()`. This means **any production change to `build_memory_context()` will NOT be measured by the benchmark**.

**Callers**:
- `evaluate_single()` at line 651

---

#### 3.3 `retrieve_user_memories()` — **[CLASSIFICATION: (r) retrieval infrastructure]**

**Location**: lines 604-624

**What it does**:
- Wraps production `retrieve_memories_for_text()` with hardcoded flags:
  - `include_l0=True`
  - `retrieval_triggered_by="longmemeval"`
  - `include_dream_observations=True`
  - `allowed_source_conversation_ids` (passthrough)
- `log_retrieval` passthrough

**Hardcoded retrieval flags**:
| Flag | Value | Production default varies? |
|---|---|---|
| `include_l0` | `True` | Yes — production call sites vary |
| `retrieval_triggered_by` | `"longmemeval"` | N/A (benchmark-only) |
| `include_dream_observations` | `True` | Yes — `memory_read` uses `False` |
| `allowed_source_conversation_ids` | passthrough | N/A (caller-dependent) |

**Callers**:
- `evaluate_single()` at line 641

---

#### 3.4 `evaluate_single()` — **[CLASSIFICATION: (d) orchestrator]**

**Location**: lines 627-714

**What it does**:
1. Embeds question text via `embed_query()`
2. Calls `retrieve_user_memories()` → gets list of memory dicts
3. Calls `build_assembled_system_prompt(memories)` → gets `system_prompt` string
4. **Also** calls `_format_eval_memory_block(memories, [])` standalone → gets `memory_context` string (for checkpoint metadata only)
5. Calls `answer_with_llm(question_text, memories, system_prompt=system_prompt)`
6. Calls `judge_answer(question_text, hypothesis, reference)`
7. Returns result dict including `answer_prompt_metadata` with `system_message`, `memory_content`, `memories_raw`

**Preserves/changes fields**:
| Field | Behavior | Gap? |
|---|---|---|
| `allowed_source_conversation_ids` | Passed to `retrieve_user_memories()` → `retrieve_memories_for_text()` | ✅ Preserved |
| `retrieval_triggered_by` | Hardcoded in `retrieve_user_memories()` | ⚠️ Hardcoded, not caller-configurable |
| `include_dream_observations` | Hardcoded in `retrieve_user_memories()` | ⚠️ Hardcoded, not caller-configurable |
| L0 | Retrieved but formatted identically to L1 by `_format_eval_memory_block()` | ⚠️ GAP |
| Summaries | Passed as `[]` to `build_assembled_system_prompt()` | ⚠️ GAP: production includes summaries |
| Preferences | Not included | ⚠️ GAP |
| Token-budget trimming | Not performed | ⚠️ GAP |

**Dual-call anomaly**: `build_assembled_system_prompt()` is called at line 651, and `_format_eval_memory_block()` is called again at line 652. Both use the same `memories` list. The second call's result only goes into `answer_prompt_metadata.memory_content`, not to the model.

---

#### 3.5 `run_evaluation()` — **[CLASSIFICATION: out of scope — orchestration only]**

**Location**: lines 770-892

**What it does**:
- Loads dataset and checkpoint
- For each question, calls `evaluate_single()` at line 838
- Does NOT pass `allowed_source_conversation_ids` — this is the **standalone legacy path** that uses unfiltered retrieval
- Canonical runner (`orchestrator/eval/runner.py`) DOES pass `allowed_source_conversation_ids`

**Key finding**: Standalone `run_evaluation()` path (line 838) does NOT pass `allowed_source_conversation_ids`, meaning `retrieve_user_memories()` receives `None` and performs unfiltered vector similarity search across the entire shared benchmark user. This is the **legacy contamination vector** documented in `ISOLATION_AUDIT.md`.

---

### `tests/longmemeval/ingest.py` (525 lines)

**Classification for memory-formatting**: N/A — this file handles **ingestion**, not answer-time memory formatting.

#### Relevant functions for retrieval scope:

- `ingest_session()` lines 279-356: creates conversation, inserts messages, calls `process_extraction()`
- `run_ingestion()` lines 359-473: orchestrates full ingest; calls `ingest_session()` per corpus session

**Memory-formatting relevance**: None for the answer path. The ingest path populates the memory store. The answer path reads from it.

---

## 4. Production Entry Points (Read-Only Reference)

### `orchestrator/memory/injection.py`

#### `build_memory_context()` — lines 168-308 **[PRODUCTION — NOT called by harness]**

**What it does**:
1. Gets conversation + user_id
2. Retrieves L0 memories → renders via `_format_l0_block()` with `[FROZEN MEMORIES]` header
3. Gets recent messages → builds query_text
4. Embeds query_text → calls `retrieve_memories_for_text()`
5. Gets summaries
6. Renders memory_lines + summary_lines
7. Applies token-budget trimming via `estimate_tokens()`
8. Returns formatted string (or `l0_block` alone if no L1 results)

**Gap vs harness**: Production `build_memory_context()`:
- Has `[FROZEN MEMORIES]` header for L0 (harness renders all as `"- Fact: ..."`)
- Has token-budget trimming (harness has none)
- Derives query_text from recent messages (harness uses question text directly)
- Includes summaries (harness passes `[]`)
- Does NOT include preferences_block (neither does harness)

#### `assemble_system_prompt()` — lines 311-336 **[PRODUCTION — called by harness]**

**What it does**:
1. Prepends `DAEMON_SYSTEM_PROMPT`
2. Optionally inserts `preferences_block`
3. Inserts `memory_context`
4. Appends memory-tools message if not present

**Gap vs harness call**: Harness passes `memory_context` from `_format_eval_memory_block()`, NOT from `build_memory_context()`. Also harness does not pass `preferences_block`.

---

## 5. Out-of-Scope Consumers (Read-Only Evidence)

These files import or depend on harness symbols but are **NOT modified** in T1:

### `orchestrator/eval/runner.py` (READ ONLY)
- **Imports** from `tests.longmemeval.evaluate` (lines 196-221): `_format_eval_memory_block`, `build_assembled_system_prompt`, `evaluate_single`, `build_answer_prompt`, etc.
- **Hashes** `_format_eval_memory_block` into `active_memory_formatter_sha256` (lines 621-646)
- **Calls** `evaluate_single()` at line 1593-1789 `LongMemEvalRunner.evaluate()`
- **Passes** `allowed_source_conversation_ids` to `evaluate_single()` (lines 423-438)

### `orchestrator/eval/longmemeval_fast.py` (READ ONLY)
- **Imports** `evaluate_single` from `tests.longmemeval.evaluate` (line 21)
- **Passes** `allowed_source_conversation_ids` to `evaluate_single()` (lines 478-487)

### `tests/test_longmemeval_evaluate.py` (READ ONLY)
- **Tests**: `build_assembled_system_prompt()` includes `DAEMON_SYSTEM_PROMPT` (lines 630-637)
- **Tests**: `evaluate_single()` uses assembled system prompt (lines 656-713)

### `tests/test_longmemeval_runner.py` (READ ONLY)
- **Mocks** `evaluate_single` at multiple lines (298, 1381, 1464, 1558, 1751)
- **Tracks** `allowed_source_conversation_ids` scope (lines 318, 402)

### `tests/benchmark_longmemeval/test_config_pinning.py` (READ ONLY)
- Line 66: references `orchestrator.eval.runner.build_assembled_system_prompt`

### `tests/benchmark_longmemeval/test_teardown_audit.py` (READ ONLY)
- Lines 19, 380, 421, 585: imports and calls `evaluate_single`

### `tests/benchmark_longmemeval/longmemeval_config_pin.json` (READ ONLY)
- Stores pinned retrieval contract: `retrieval_triggered_by`, `include_dream_observations`, `include_l0`

---

## 6. Retrieval Scope Analysis

| Parameter | `retrieve_user_memories()` value | Production `build_memory_context()` value | Parity? |
|---|---|---|---|
| `include_l0` | `True` (hardcoded) | `True` (via `store.get_l0_memories()`) | ✅ Parity |
| `retrieval_triggered_by` | `"longmemeval"` (hardcoded) | N/A | N/A (benchmark-only) |
| `include_dream_observations` | `True` (hardcoded) | Varies by call site | ⚠️ Difference |
| `allowed_source_conversation_ids` | `retrieve_user_memories()` accepts and passes through to `retrieve_memories_for_text()`; canonical runner passes scoped IDs; standalone `run_evaluation()` passes `None` | Not a `build_memory_context()` parameter — production isolates via deterministic synthetic users/conversations (per revised plan architecture), not via an allowlist argument | ⚠️ Harness has the mechanism; production does not use this parameter |
| `limit` | `TOP_K_MEMORIES = 5` | `MAX_MEMORY_ITEMS = 5` | ✅ Parity |
| Query text source | `question_text` (passed in) | Last user message in recent messages | ⚠️ Difference |

---

## 7. Summary of Parity Gaps

| Gap | Severity | Location | Production behavior | Harness behavior |
|---|---|---|---|---|
| `build_memory_context()` not called | **Critical** | `evaluate.py` | Full pipeline with token budget, L0 header, summary inclusion | `_format_eval_memory_block` substitute |
| `[FROZEN MEMORIES]` header | High | `injection.py:104-114` vs `evaluate.py:446-456` | L0 gets special header | All formatted identically |
| Token-budget trimming | High | `injection.py:292-298` vs `evaluate.py:416-474` | `estimate_tokens()` budget loop | No token counting |
| Summaries included | Medium | `injection.py:260-264` vs `evaluate.py:651` | Yes — from `get_recent_summaries()` | Passed as `[]` to `build_assembled_system_prompt()` |
| Preferences block | Medium | `injection.py:128-165` vs `evaluate.py:490` | Available via `preferences_block` param | Not passed — `None` |
| `include_dream_observations` | Low | retrieval varies | `True` in harness, varies in production | ⚠️ Harness hardcoded, production varies |
| Query text source | Low | `injection.py:194-206` vs `evaluate.py:639` | Derived from last user message | Direct question text |

---

## 8. Evidence Verification Checklist

- [x] `_format_eval_memory_block` — lines 434, 487, 652 in `evaluate.py` — **CLASSIFIED: (b) substitute**
- [x] `build_assembled_system_prompt` — lines 477, 490, 651 in `evaluate.py` — **CLASSIFIED: (a) calls production assembly**
- [x] `build_memory_context` — NOT called by harness — **EXPLICITLY EXCLUDED**
- [x] `assemble_system_prompt` — imported at line 50, called at line 490 — ** REPRESENTED**
- [x] `memory_context` — lines 487, 490, 652, 706 — **REPRESENTED**
- [x] `allowed_source_conversation_ids` — lines 611, 621, 634, 648 — **REPRESENTED**
- [x] `retrieval_triggered_by` — line 622 — **REPRESENTED**
- [x] `include_dream_observations` — line 623 — **REPRESENTED**
- [x] `include_l0` — line 619 — **REPRESENTED**
- [x] All grep hits represented in inventory or explicitly excluded as docs-only
- [x] Out-of-scope consumers recorded as read-only
- [x] Classification per participant: (a), (b), (c), or (d)
- [x] `allowed_source_conversation_ids` preservation/change recorded
- [x] `retrieval_triggered_by` preservation/change recorded
- [x] `include_dream_observations` preservation/change recorded
- [x] L0 handling recorded
- [x] Summaries handling recorded
- [x] Preferences handling recorded
- [x] Token-budget trimming recorded

---

## 9. Files Created

| File | Purpose |
|---|---|
| `tests/benchmark_results/harness_parity_inventory.md` | This document |
| `.sisyphus/evidence/task-1-inventory-grep.txt` | Full grep evidence with line numbers |
| `.sisyphus/evidence/task-1-missing-paths.txt` | Missing paths, exclusions, symbol dependency graph |
