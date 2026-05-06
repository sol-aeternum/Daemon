# T1 - Out-of-Scope Runner Consumer Mapping

**Date**: 2026-05-06
**Task**: Map read-only consumers of LongMemEval harness memory formatting symbols

## Decision Gates Identified

### Gate 1: `_format_eval_memory_block` Deletion
- **Files that would break**:
  - `orchestrator/eval/runner.py:212, 626, 636` — imports, uses in `_build_answer_prompt_contract_payload()`, hashes for `active_memory_formatter_sha256`
  - `tests/longmemeval/evaluate.py:487, 652` — internal harness use
- **Evidence**: `runner.py:636` computes `_sha256_source(_format_eval_memory_block)` directly — this is the canonical proof the benchmark uses benchmark-local formatter
- **Would require**: Coordinated update to runner.py imports/call-sites AND `_build_answer_prompt_contract_payload()` hashing logic

### Gate 2: `build_assembled_system_prompt` Deletion
- **Files that would break**:
  - `orchestrator/eval/runner.py:213, 633, 634` — imports, hashes for `active_system_prompt_builder_sha256`
  - `tests/test_longmemeval_evaluate.py:15, 630-637` — direct test use
  - `tests/benchmark_longmemeval/test_config_pinning.py:66` — monkeypatch target
- **Would require**: Update runner hash + test updates

### Gate 3: `evaluate_single` Deletion
- **Files that would break**:
  - `orchestrator/eval/runner.py:216, 1727-1738` — canonical runner
  - `orchestrator/eval/longmemeval_fast.py:21, 478-487` — fast runner
  - Multiple test files
- **Would require**: Simultaneous update to both runner.py and longmemeval_fast.py

### Gate 4: `TEST_USER_ID` Value Change
- **Files that would break**:
  - `orchestrator/eval/runner.py:225, 260, 768` — config pin, cleanup, reset
- **Evidence**: Canonical runner reuses same `TEST_USER_ID` across all corpus sessions
- **Would require**: Coordinated data migration

## NOT Consumers (False Positives)
- `scripts/test_session_memory_alignment.py:14` — defines own local constant, does NOT import
- `scripts/test_retrieval_quality.py:18` — defines own local constant, does NOT import

## Key Evidence for T6-GATE
1. `runner.py:636`: `active_memory_formatter_sha256 = _sha256_source(_format_eval_memory_block)` — hashes benchmark-local formatter
2. `runner.py:212-213`: Imports both `_format_eval_memory_block` and `build_assembled_system_prompt` from `tests.longmemeval.evaluate`
3. Both runner.py and longmemeval_fast.py call `evaluate_single` which internally uses `_format_eval_memory_block`

## Inventory File
Full details: `tests/benchmark_results/harness_parity_inventory_runner_consumers.tmp.md`

---

## T1 Additional Anomalies (2026-05-06)

### Anomaly 1: Dual-call to _format_eval_memory_block per question
- **Severity**: warning
- **Scope**: project
- **Encountered during**: T1 inventory mapping
- **Category**: performance / code-quality
- **Blocked current task**: no
- **What happened**: `_format_eval_memory_block()` is called TWICE per question in `evaluate_single()`: once inside `build_assembled_system_prompt()` at line 487, and again standalone at line 652 for checkpoint metadata. The standalone call at line 652 is redundant from a model-input perspective.
- **Evidence**: `evaluate.py:651-652`
- **Likely cause**: Intentional — the second call feeds `answer_prompt_metadata.memory_content` in the result dict. But this means the formatter runs twice for every question with no caching.
- **Suggested action**: Consider caching the result if the same `memories` list is used for both paths.

### Anomaly 2: run_evaluation() bypasses allowed_source_conversation_ids isolation
- **Severity**: warning
- **Scope**: project
- **Encountered during**: T1 inventory mapping
- **Category**: config / benchmark-contamination
- **Blocked current task**: no
- **What happened**: Standalone `run_evaluation()` at line 838 calls `evaluate_single()` without forwarding `allowed_source_conversation_ids`, causing unfiltered retrieval (`allowed_source_conversation_ids=None`) across the entire shared benchmark user. The canonical runner (`orchestrator/eval/runner.py`) correctly passes scoped conversation IDs.
- **Evidence**: `evaluate.py:838` vs `runner.py:423-438`
- **Likely cause**: Legacy — standalone path predates the isolation fix. This is a known contamination vector.
- **Suggested action**: Document as known limitation of standalone path; canonical runner path is correct.

### Anomaly 3: _format_eval_memory_block has no token-counting budget
- **Severity**: info
- **Scope**: project
- **Encountered during**: T1 inventory mapping
- **Category**: benchmark-gap
- **Blocked current task**: no
- **What happened**: `_format_eval_memory_block()` performs no token-counting. It truncates individual memories at 400 chars but has no equivalent of production's `estimate_tokens()` budget loop. This means the harness can produce memory text exceeding production token budgets.
- **Evidence**: `evaluate.py:416-474` vs `injection.py:292-298`
- **Likely cause**: Oversight — the benchmark adapter was written for correctness, not budget fidelity.
- **Suggested action**: Future task could add token budget enforcement to the harness path if production-level token control is needed in the aligned benchmark.

### Anomaly 4: Summaries passed as empty list to build_assembled_system_prompt
- **Severity**: info
- **Scope**: project
- **Encountered during**: T1 inventory mapping
- **Category**: benchmark-gap
- **Blocked current task**: no
- **What happened**: `evaluate_single()` calls `build_assembled_system_prompt(memories)` without passing summaries, so `summaries=[]` is used. Production `build_memory_context()` calls `get_recent_summaries()` and includes them.
- **Evidence**: `evaluate.py:651` — `build_assembled_system_prompt(memories)` vs `evaluate.py:487` — `summaries if summaries else []`
- **Likely cause**: Benchmark scope — LongMemEval does not have session summary context naturally available.
- **Suggested action**: Future task could add summary retrieval if benchmark scope expands.

## T2 Issues — 2026-05-06

- Multiple older Wave 0 markdown artifacts still describe pre-Path-A benchmark behavior or retain now-corrected guardrail assumptions, so reconstruction had to privilege the T1 inventory, `wave0_closure_path_a_audit.md`, and `wave1_benchmark_consumer_path.md` over older divergence docs when evidence conflicted.

## T3 Issues — 2026-05-06

- Summary-state parity is split across two production surfaces: `process_extraction()` updates `conversations.summary`, while `build_memory_context()` reads only summary memories via `get_recent_summaries()`. Any future parity implementation must choose explicitly whether empty summary state is acceptable or whether it will also prepopulate summary memories through the existing consolidation path.
- Entity-expansion parity is not automatic in the inline ingest path. If later tasks need alias/entity retrieval behavior rather than plain vector+BM25 retrieval, they must schedule an additional existing entity-resolution path after memory extraction instead of assuming `process_extraction()` already covers it.

---

## T4 Issues (2026-05-06)

### Issue 1: HuggingFace Dataset Unavailable
- **Severity**: warning
- **Scope**: tooling
- **Encountered during**: T4 category enumeration
- **Category**: dependency / upstream
- **Blocked current task**: no (used benchmark artifacts as fallback)
- **What happened**: The canonical `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s.json` URL returns 404 Not Found
- **Evidence**: `httpx.HTTPStatusError: Client error '404 Not Found'`
- **Likely cause**: Dataset was taken down or URL structure changed on HuggingFace
- **Suggested action**: Find alternative dataset URL or use benchmark artifacts as the de facto corpus

### Issue 2: score.json vs results.jsonl Category Discrepancy
- **Severity**: info
- **Scope**: project
- **Encountered during**: T4 category verification
- **Category**: data-inconsistency
- **Blocked current task**: no
- **What happened**: `longmemeval_score.json` shows ABS category with accuracy 0.0, but `longmemeval_results.jsonl` has NO entries with `category="ABS"` — all ABS questions show their parent category instead
- **Evidence**: score.json accuracy dict has `"ABS": 0.0`; results category counts show IE-assistant=56, IE-preference=30, IE-user=70, KU=78, MR=133, TR=133 (total=500, no ABS)
- **Likely cause**: Score file generated from a different run, or scoring function initializes all ACCURACY_CATEGORIES regardless of actual results
- **Suggested action**: For category counts, use results file as source of truth; score file anomaly is cosmetic

## T7 Issues (2026-05-06)

### Issue 1: Evidence file contained raw database URL and test encryption key
- **Severity**: warning
- **Scope**: project
- **Encountered during**: T7 pre-flight evidence review
- **Category**: security / evidence-hygiene
- **Blocked current task**: yes (T7 cannot be marked complete until redacted — NOW RESOLVED)
- **What happened**: `.sisyphus/evidence/task-7-preflight-broken-model.txt` contained a raw database URL and a raw test encryption-key literal as example env var values in the intentional broken-model test command block.
- **Evidence**: Raw credentials appeared in lines 24-25 within the bash command block showing the test setup.
- **Fix applied**: Replaced with `<redacted host-local DATABASE_URL>` and `<redacted test encryption key>` — evidence still clearly explains the intentional invalid-model preflight scenario.
- **Likely cause**: Author copied actual shell command without redacting credentials first.
- **Suggested action**: Audit all remaining `.sisyphus/evidence/*.txt/*.md` files for similar credential leakage (multiple other evidence files already flagged by grep with raw `postgresql://daemon:daemon` patterns in task-5, task-9, task-10, task-11).

---

### Issue 3: ABS Category Mapping Inconsistency
- **Severity**: info
- **Scope**: project
- **Encountered during**: T4 category verification
- **Category**: data-inconsistency
- **Blocked current task**: no
- **What happened**: `evaluate.py:830-831` sets `category = "ABS"` for _abs questions, but the results file shows parent category (IE-user/MR/TR/KU) for those same questions
- **Evidence**: `evaluate.py:830-833` shows ABS logic, but `longmemeval_results.jsonl` has 30 _abs questions all with non-ABS category values
- **Likely cause**: The results file may predate the ABS logic change, or the results were post-processed differently
- **Suggested action**: Unclear if this is a data issue or code issue; further investigation needed if ABS scoring matters
