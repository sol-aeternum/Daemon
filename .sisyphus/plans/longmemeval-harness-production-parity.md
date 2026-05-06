# LongMemEval Harness–Production Parity

## TL;DR
> **Summary**: Route LongMemEval prompt assembly through Daemon's production memory-injection path, prove harness/production prompt parity, and replace the Wave 0 10.4% harness-artifact baseline with a post-parity LongMemEval_S baseline.
> **Deliverables**: parity inventory/audits; harness adapter; static + runtime parity checks; smoke/extraction/encryption gates; full corpus baseline; surgical doc/W1 patches; local `harness-parity-shipped` tag; postmortem.
> **Effort**: XL
> **Parallel**: YES - 7 waves
> **Critical Path**: T1-T4 investigation → T5 Oracle ratification → T6 scope gate → T7-T8 implementation/clean diff → T9-T13 verification → T14-T16 baseline decision → T17-T22 docs/tag/postmortem

## Context
### Original Request
Create a pre-W1 infrastructure plan that restores LongMemEval harness-production parity by removing harness-local memory prompt formatting and routing prompt assembly through the canonical production entry point in `orchestrator/memory/injection.py`. Reclassify the Wave 0 10.4% LongMemEval_S number as a harness artifact and replace it with the post-parity baseline.

### Interview Summary
The user supplied complete scope, architecture decisions, 22 TODOs, guardrails, review rules, and completion criteria. No preference interview was required. Revised architecture correction: v1's aggregated-store/unscoped-retrieval framing was wrong for LongMemEval. Each LongMemEval question is a self-contained haystack; multi-session questions reason across sessions within one question, not across questions. The parity harness must therefore model each question as one deterministic synthetic user, ingest that question's haystack under that user, run production synchronous extraction inline, then invoke production user-scoped retrieval and prompt assembly for that same synthetic user.

### Metis Review (gaps addressed)
- **Retrieval scoping correction**: v1's proposal to accept unscoped retrieval over an aggregated 500-question store is retracted. Aggregated unscoped retrieval would introduce cross-haystack distractor pollution, unrelated knowledge-update supersession, and temporal timestamp incoherence. Revised plan uses deterministic per-question synthetic users so production's existing user-scoped retrieval remains unchanged and faithful.
- **Path mismatch**: requested `tests/benchmark_results/wave0_aligned_baseline.md` does not exist; actual discovered target is `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md`. Plan requires T18 to patch the discovered actual file and record the mismatch.
- **Roadmap path mismatch**: roadmap is `docs/MEMORY_UPGRADE_ROADMAP.md`, not root. Plan uses discovered path.
- **Extraction benchmark path uncertainty**: `tests/benchmark_extraction.py` side investigation is out of scope for this plan. The plan must not find/rebuild that benchmark; commission a separate producer-layer regression-detector plan.
- **Guardrail hash drift**: `runner.py` hashing of `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is out of scope for this plan. Commission a separate ~30-minute investigation before W1 resumes.

### Architecture Decisions (Revised)
- **Isolation unit**: One LongMemEval question = one synthetic Daemon user.
- **Synthetic user ID**: Deterministic UUID5 from `question_id` (recommended namespace must be recorded in T3/T7) so single-question reruns target the same state.
- **Haystack ingestion**: Ingest the question's full `haystack_sessions` as conversations/messages under that synthetic user.
- **Extraction**: Run production extraction synchronously inline against the haystack. Bypass arq and its 30s debounce; do not bulk-load pre-extracted memories.
- **Prompt assembly**: Run the question under the same synthetic user. Production `build_memory_context()` and `assemble_system_prompt()` scope to that user without modification.
- **No per-question teardown**: Move to the next question under a different synthetic user. Do not clear state between questions.
- **Post-run persistence**: All 500 synthetic users persist after corpus run for inspection. Cleanup is a separate post-run script/drop-by-synthetic-naming-pattern operation, not part of the per-question loop.
- **L0**: Leave L0 empty for each synthetic user by default, matching LongMemEval's no-prebaked-profile assumption.
- **Postmortem note only**: IE-assistant limitations from Daemon's selective assistant-content extraction policy are documented for future roadmap consideration; do not change extraction policy here.

## Work Objectives
### Core Objective
Make the LongMemEval harness consume the same production prompt-surface assembly code that live chat uses, then establish a new production-faithful LongMemEval_S baseline for W1+.

### Deliverables
- `tests/benchmark_results/harness_parity_inventory.md`
- `tests/benchmark_results/harness_parity_path_a_reconstruction.md`
- `tests/benchmark_results/harness_parity_dependency_audit.md`
- `tests/benchmark_results/harness_parity_category_paths.md`
- `tests/benchmark_results/harness_parity_oracle_review.md`
- Deterministic synthetic-user adapter in `tests/longmemeval/**` that ingests each question's `haystack_sessions`, runs synchronous production extraction inline, and calls production prompt assembly under that synthetic user.
- Harness code changes confined to `tests/longmemeval/**`. If investigation proves `orchestrator/eval/runner.py`, `tests/test_*.py`, or config-pin files must change for a functional parity fix, the executing agent must halt and record `[DECISION NEEDED: authorize scope expansion beyond the user's guardrail, or commission a separate plan]`; no out-of-scope file may be edited silently.
- Verification artifacts T8-T16 under `tests/benchmark_results/`.
- Surgical patches to `tests/benchmark_results/wave0_closure_memo.md`, `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md`, `.sisyphus/plans/wave1-prompt-surface-changes.md`, and `docs/MEMORY_UPGRADE_ROADMAP.md`.
- Local lightweight git tag `harness-parity-shipped`.
- `tests/benchmark_results/harness_parity_postmortem.md`.

### Definition of Done (verifiable conditions with commands)
- `git diff -- 'orchestrator/memory/**'` is empty.
- `git grep '_format_eval_memory_block' -- tests/longmemeval` returns zero matches, unless a postmortem quote includes it; code references inside allowed harness files must be zero.
- `git grep 'build_memory_context' tests/longmemeval` shows harness routing through production memory context construction.
- `tests/benchmark_results/harness_parity_spot_check.md` reports 20/20 equivalence passes.
- `tests/benchmark_results/harness_parity_baseline_run.json` includes `synthetic_user_id` for every per-question record.
- `tests/benchmark_results/harness_parity_baseline_decision.md` declares the new baseline or halts with explicit cause.
- `git rev-parse harness-parity-shipped` equals `git rev-parse HEAD`; no push occurred.

### Must Have
- Bounded probes before fixes for every memory-pipeline anomaly.
- Halt on any required production memory change.
- Byte-identity equivalence unless Oracle ratifies explicit normalization rules.
- Per-question baseline records include `question_id`, `synthetic_user_id`, `category`, `judge_verdict`, `retrieved_memory_ids`, `memories_used`.
- Production extraction runs synchronously inline for each question haystack; bulk-loading pre-extracted/oracle memories is forbidden.
- Surgical docs only; no markdown reflow.

### Must NOT Have
- No `orchestrator/memory/**` modifications.
- No W1 prompt-surface feature work.
- No frontend changes.
- No judge-category or benchmark-feature expansion.
- No aggregated 500-question retrieval store.
- No cross-question state clearing/teardown inside the per-question loop.
- No extraction-policy changes for IE-assistant.
- No `tests/benchmark_extraction.py` discovery/rebuild or guardrail-hash investigation in this plan.
- No remote push.
- No moving/deleting `pre-wave-1`.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: tests-after + benchmark gates; pytest exists, no CI.
- QA policy: Every task produces a machine-readable or reviewable artifact and includes happy/failure checks.
- Evidence: `tests/benchmark_results/harness_parity_*.{md,json,jsonl}` and `.sisyphus/evidence/task-{N}-{slug}.{ext}` where command transcripts are needed.

## Execution Strategy
### Parallel Execution Waves
Wave 1: T1-T4 investigation and bounded audits.
Wave 2: T5 Oracle equivalence ratification.
Wave 3: T6 out-of-scope dependency gate, T7-T8 implementation and production-clean invariant.
Wave 4: T9-T13 parity, smoke, inline extraction, encryption verification.
Wave 5: T14-T16 full baseline, anomaly decision, baseline interpretation.
Wave 6: T17-T20 surgical documentation patches.
Wave 7: T21-T22 tag and postmortem.

### Dependency Matrix (full, all tasks)
T1: none. T2: T1. T3: T1. T4: T1. T5: T1-T4. T6: T5. T6-GATE: T6. T7: T6-GATE PASS only. T8: T7. T9: T8. T10: T9. T11: T10. T12: T11. T13: T11. T14: T12,T13. T15: T14. T16: T15. T17-T20: T15. T21: T17-T20. T22: T21.

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 4 tasks → general, ultrabrain.
- Wave 2 → 1 task → ultrabrain/oracle.
- Wave 3 → 3 tasks → general, quick.
- Wave 4 → 5 tasks → general, quick.
- Wave 5 → 3 tasks → general, ultrabrain/oracle.
- Wave 6 → 4 tasks → writing.
- Wave 7 → 2 tasks → quick, writing.

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. Map all harness-side memory-formatting code paths

  **What to do**: Inventory every LongMemEval harness function/method/branch in `tests/longmemeval/**` that touches memory shape, ordering, retrieval scope, or rendering before the answering model call. Start from `tests/longmemeval/evaluate.py` `_format_eval_memory_block()` (~434-474), `build_assembled_system_prompt()` (~477-490), and `evaluate_single()` (~627-714). Confirm production identifiers in `orchestrator/memory/injection.py`: `build_memory_context()` (~168-308) and `assemble_system_prompt()` (~311-336). Read-only note: if runner/config-pin references in out-of-scope files depend on deleted harness symbols, record them as scope blockers in the inventory; do not edit them under this plan without explicit scope expansion.
  **Must NOT do**: Do not edit source. Do not treat `assemble_system_prompt()` alone as parity if `build_memory_context()` is bypassed.

  **Recommended Agent Profile**:
  - Category: `exploration` - Reason: file/code-path mapping only.
  - Skills: [] - no specialized skill needed.
  - Omitted: `memory-wave-diagnostic` - used in T3 where bounded memory probes are required.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: T2,T3,T4,T5 | Blocked By: none

  **References**:
  - Pattern: `tests/longmemeval/evaluate.py:434-490` - harness-local formatter and wrapper.
  - Pattern: `orchestrator/memory/injection.py:168-336` - production context builder and system prompt assembler.
  - Pattern: `orchestrator/eval/runner.py` - read-only check for out-of-scope benchmark consumer references.
  - Doc: `tests/benchmark_results/wave1_benchmark_consumer_path.md` - existing halt rationale.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_inventory.md` exists.
  - [ ] Inventory lists every harness/runner participant with file:line and classifies each as (a) calls production assembly, (b) substitutes for production assembly, or (c) post-processes production output.
  - [ ] `_format_eval_memory_block` appears as a (b) substitute.
  - [ ] Inventory explicitly records whether each path preserves or changes `allowed_source_conversation_ids`, `retrieval_triggered_by`, `include_dream_observations`, L0, summaries, preferences, and token-budget trimming.

  **QA Scenarios**:
  ```
  Scenario: Inventory finds headline gap
    Tool: Bash
    Steps: Run git grep for _format_eval_memory_block, build_assembled_system_prompt, build_memory_context, assemble_system_prompt; compare grep output against inventory entries.
    Expected: Every grep code hit is represented or explicitly excluded as documentation-only; _format_eval_memory_block is classified substitute.
    Evidence: .sisyphus/evidence/task-1-inventory-grep.txt

  Scenario: Missing path check
    Tool: Bash
    Steps: Run git grep for memory_context and allowed_source_conversation_ids in tests/longmemeval and orchestrator/eval.
    Expected: No unclassified code path touching prompt memory remains.
    Evidence: .sisyphus/evidence/task-1-missing-paths.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_inventory.md`

- [x] 2. Reconstruct Wave 0 Path A coverage

  **What to do**: Using T1 inventory, inspect `tests/benchmark_results/wave0_closure_path_a_audit.md`, `tests/benchmark_results/wave0_benchmark_injection_origin.md`, `tests/benchmark_results/wave0_benchmark_vs_production_injection.md`, `tests/benchmark_results/wave0_benchmark_alignment_decision.md`, and related Wave 0 docs. Classify each T1 finding as `path_a_miss` or `post_w0_drift` with evidence.
  **Must NOT do**: Do not rewrite Wave 0 docs in this task.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: forensic methodology reconstruction.
  - Skills: [] - no special skill needed.
  - Omitted: `git-master` - use only read-only git log/diff if needed.

  **Parallelization**: Can Parallel: YES after T1 | Wave 1 | Blocks: T5,T17,T22 | Blocked By: T1

  **References**:
  - Doc: `tests/benchmark_results/wave0_closure_path_a_audit.md` - Path A audit.
  - Doc: `tests/benchmark_results/wave0_benchmark_injection_origin.md` - documented isolation decision.
  - Doc: `tests/benchmark_results/wave1_benchmark_consumer_path.md` - W1 halt.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_path_a_reconstruction.md` exists.
  - [ ] Every T1 finding is individually classified with file/section evidence.
  - [ ] At least one root-cause sentence explains how the method missed `_format_eval_memory_block` if classified `path_a_miss`.

  **QA Scenarios**:
  ```
  Scenario: Classification coverage
    Tool: Bash
    Steps: Extract finding names from T1 inventory and compare against T2 reconstruction headings/list entries.
    Expected: Counts match exactly; no finding omitted.
    Evidence: .sisyphus/evidence/task-2-classification-coverage.txt

  Scenario: Drift evidence check
    Tool: Bash
    Steps: For any post_w0_drift classification, run git log --follow -S '<symbol>' -- <file>.
    Expected: Reconstruction cites the observed commit/date or downgrades to path_a_miss if no post-W0 change exists.
    Evidence: .sisyphus/evidence/task-2-drift-evidence.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_path_a_reconstruction.md`

- [x] 3. Audit production assembly dependencies with bounded D-chain

  **What to do**: Apply `memory-wave-diagnostic` to production prompt assembly under the revised synthetic-user-per-question architecture. Enumerate every state element that must be populated for one synthetic user before `build_memory_context()` and `assemble_system_prompt()` run: deterministic UUID5 user row, conversation rows for each `haystack_sessions` session, encrypted message rows with correct roles/timestamps, extracted memory rows, extraction log rows, retrieval log expectations, empty L0/default profile state, summaries/preferences if production reads them, token budgets, and relevant settings/env. Identify the synchronous-inline production extraction call path that can process the haystack without arq/debounce, and classify each dependency as (a) trivially available, (b) requires harness pre-population using existing code paths, or (c) requires production change. Confirm that production's existing user-scoped retrieval needs no modification because each question runs under its own synthetic user.
  **Must NOT do**: Do not edit `orchestrator/memory/**`. If any (c) appears, halt plan.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: dependency and halt-branch analysis.
  - Skills: [`memory-wave-diagnostic`] - bounded probes/root-cause classification.
  - Omitted: `git-master` - no git mutation needed.

  **Parallelization**: Can Parallel: YES after T1 | Wave 1 | Blocks: T5,T7,T8 | Blocked By: T1

  **References**:
  - API/Type: `orchestrator/memory/injection.py:168-336` - production entry points.
  - Pattern: `tests/longmemeval/ingest.py` - benchmark test user/conversation creation.
  - Pattern: `orchestrator/memory/store.py` - memory DB CRUD contracts.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_dependency_audit.md` exists.
  - [ ] All dependencies have file:line citations and (a)/(b)/(c) classification.
  - [ ] For every (b), document exact existing pre-population code path/table and whether it runs once per synthetic user or once per haystack session/message.
  - [ ] Count of (c) is zero, or artifact declares halt and no later task proceeds.
  - [ ] Audit explicitly rejects aggregated unscoped retrieval and confirms synthetic-user isolation as the production-faithful scope mechanism.
  - [ ] Audit records the synchronous-inline extraction path and states why arq/debounce is bypassed without bypassing production extraction logic.

  **QA Scenarios**:
  ```
  Scenario: Production dependency completeness
    Tool: Bash
    Steps: Run static inspection/grep of build_memory_context, assemble_system_prompt, and production extraction entry points; compare each external call/settings access to audit list.
    Expected: No unlisted dependency reads remain; each required state item is assigned to synthetic-user setup or halt.
    Evidence: .sisyphus/evidence/task-3-dependency-static.txt

  Scenario: Halt branch validation
    Tool: Bash
    Steps: Search audit for '(c)' entries and 'HALT'.
    Expected: If count >0, plan runner stops before T4+ implementation; if count=0, audit says proceed.
    Evidence: .sisyphus/evidence/task-3-halt-check.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_dependency_audit.md`

- [x] 4. Enumerate LongMemEval categories and category-specific assembly paths

  **What to do**: Enumerate categories in LongMemEval_S corpus (KU, IE-user, IE-preference, IE-assistant, MR, TR, ABS and subtypes if present). Count questions per category and map each category to the assembly path identified in T1.
  **Must NOT do**: Do not add categories or alter exclusions.

  **Recommended Agent Profile**:
  - Category: `exploration` - Reason: corpus/path mapping.
  - Skills: [] - no skill needed.
  - Omitted: `memory-wave-diagnostic` - no anomaly disposition unless counts fail.

  **Parallelization**: Can Parallel: YES after T1 | Wave 1 | Blocks: T5,T10,T14 | Blocked By: T1

  **References**:
  - Pattern: `tests/longmemeval/**` - category handling.
  - Artifact: `tests/benchmark_results/wave0_full_corpus*/` and related Wave 0 corpus-run artifacts - previous per-category data.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_category_paths.md` exists.
  - [ ] Every corpus category has question count and assembly path file:line.
  - [ ] Document states whether all categories converge or branch; no `unknown` remains.

  **QA Scenarios**:
  ```
  Scenario: Category count totals
    Tool: Bash
    Steps: Run the harness category counting command/script used in the artifact.
    Expected: Counts sum to 500 before exclusions; excluded count separately identified.
    Evidence: .sisyphus/evidence/task-4-category-counts.json

  Scenario: Branch coverage
    Tool: Bash
    Steps: Compare category-path map against T1 assembly path inventory.
    Expected: Every category path appears in T1; no category-specific formatter omitted.
    Evidence: .sisyphus/evidence/task-4-branch-coverage.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_category_paths.md`

- [x] 5. Oracle ratifies equivalence definition and adapter strategy

  **What to do**: Ask Oracle to review T1-T4 and ratify exact equivalence under the corrected architecture. Default proposal: each LongMemEval question maps to a deterministic UUID5 synthetic user; harness ingests that question's `haystack_sessions` under the synthetic user, runs synchronous production extraction inline, leaves L0 empty, calls `build_memory_context()` and `assemble_system_prompt()` directly, applies no transformations to returned prompt, and verifies byte identity between harness-sent prompt and a direct production assembly call with the same synthetic-user state. Oracle must explicitly retract v1 aggregated unscoped retrieval and rule on synthetic-user isolation, inline extraction contract, timestamp variance, equal-rank ordering, encryption decoding (not ciphertext determinism; Fernet non-determinism is expected), whitespace, empty L0, and pre-population paths.
  **Must NOT do**: Do not proceed with T6 until Oracle signs off. Do not ask user unless Oracle flags an unavoidable user decision; instead encode halt if needed.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` / subagent `oracle` - Reason: architecture gate and equivalence definition.
  - Skills: [] - no skill needed.
  - Omitted: `memory-wave-diagnostic` - prior audit already applied.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: T6 | Blocked By: T1,T2,T3,T4

  **References**:
  - Artifact: `tests/benchmark_results/harness_parity_inventory.md`.
  - Artifact: `tests/benchmark_results/harness_parity_dependency_audit.md`.
  - Artifact: `tests/benchmark_results/harness_parity_category_paths.md`.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_oracle_review.md` exists.
  - [ ] Equivalence is stated as byte-identity OR concrete normalization rules.
  - [ ] Oracle explicitly rules on synthetic-user isolation, inline extraction, timestamp variance, ordering, encryption decoding, whitespace, empty L0, retrieval scoping, and pre-population.
  - [ ] Document is ratified, not draft; any rejection includes halt instructions.

  **QA Scenarios**:
  ```
  Scenario: Equivalence definition usable
    Tool: Bash
    Steps: Parse Oracle artifact for 'Ratified equivalence definition' and normalization list.
    Expected: T9/T10 can implement comparison without interpretation.
    Evidence: .sisyphus/evidence/task-5-equivalence-definition.txt

  Scenario: Concern coverage
    Tool: Bash
    Steps: Check artifact contains the required concern terms.
    Expected: Synthetic-user isolation, inline extraction, empty L0, and all previous equivalence concern classes present with explicit rulings.
    Evidence: .sisyphus/evidence/task-5-concern-coverage.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_oracle_review.md`

- [x] 6. Strip `_format_eval_memory_block` and sibling parallel formatters

  **What to do**: Before deleting anything, perform a scope-wall check for out-of-scope consumers: `orchestrator/eval/runner.py`, config-pin files, and tests that import/hash/call `_format_eval_memory_block`, `build_assembled_system_prompt`, `evaluate_single`, `TEST_USER_ID`, or `TEST_USER_EMAIL`. If any such consumer must be edited to keep repository imports/tests functional, do **not** delete the formatter yet; write the halt/decision section in `tests/benchmark_results/harness_parity_strip_diff.md` and stop at T6-GATE. If the scope-wall check passes, delete all T1 (b)-classified substitute formatters in `tests/longmemeval/**`, including duplicate constants/helpers in `tests/longmemeval/evaluate.py` if only used by `_format_eval_memory_block`: `MAX_MEMORY_ITEMS`, `MAX_SINGLE_MEMORY_CHARS`, `_normalize_content()`, `_truncate_to_chars()`. Remove direct call sites inside allowed harness files; leave clear TODO-shaped gaps only long enough for T7 in same work wave.
  **Must NOT do**: Do not modify `orchestrator/memory/**`. Do not leave dead imports.

  **Recommended Agent Profile**:
  - Category: `business-logic` - Reason: benchmark harness code edits with tests.
  - Skills: [] - no special skill.
  - Omitted: `git-master` - no commit/tag yet.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: T6-GATE | Blocked By: T5

  **References**:
  - Pattern: `tests/longmemeval/evaluate.py:416-490` - formatter and duplicate helpers.
  - Pattern: `orchestrator/eval/runner.py` - read-only evidence for any out-of-scope dependency/halt.
  - Test: `tests/test_longmemeval_evaluate.py` - read-only evidence for any out-of-scope test update requirement.
  - Test: `tests/benchmark_longmemeval/test_config_pinning.py` - read-only evidence for any out-of-scope config-pin update requirement.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_strip_diff.md` lists each removed function/helper/call site OR declares `[DECISION NEEDED: authorize runner.py/test/config-pin scope expansion, or commission a separate plan / alternate harness-native entry point]` before any deletion.
  - [ ] If T6-GATE passes, `git grep '_format_eval_memory_block' -- tests/longmemeval` returns zero matches after T7 completes; if T6-GATE halts, this criterion is explicitly marked not reached.
  - [ ] No `orchestrator/memory/**` file appears in diff.
  - [ ] `TEST_USER_ID` / `TEST_USER_EMAIL` in `tests/longmemeval/**` are classified as preserved legacy constants or deprecated harness-parity constants; they are not repurposed for synthetic-user parity.
  - [ ] Stale guardrail hash mismatch is documented in artifact as out-of-scope if it requires changing files outside the allowed modification set.

  **QA Scenarios**:
  ```
  Scenario: Deleted formatter absent from code
    Tool: Bash
    Steps: Run git grep '_format_eval_memory_block' -- tests/longmemeval.
    Expected: Zero matches in allowed harness code if T6-GATE passed; if T6-GATE halted, artifact records the out-of-scope dependency before deletion.
    Evidence: .sisyphus/evidence/task-6-formatter-absent.txt

  Scenario: Production clean during strip
    Tool: Bash
    Steps: Run git diff -- 'orchestrator/memory/**'.
    Expected: Empty output.
    Evidence: .sisyphus/evidence/task-6-production-clean.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/longmemeval/**`, `tests/benchmark_results/harness_parity_strip_diff.md`

### T6-GATE. Runner/test/config-pin scope decision gate
> This is a mandatory gate, not a separate implementation TODO. It preserves the user guardrail that only `tests/longmemeval/**`, `tests/benchmark_results/**`, the W1 plan, and roadmap may be modified.

- **PASS condition**: T6 proves deletion/routing can proceed while keeping all out-of-scope consumers read-only and without breaking required execution paths. Continue to T7.
- **HALT condition**: T6 proves `orchestrator/eval/runner.py`, test files, or config-pin files must be changed to keep imports, hashes, or corpus execution functional. Stop and present `[DECISION NEEDED: authorize runner.py/test/config-pin scope expansion, or commission a separate plan / alternate harness-native entry point]`. Do not silently edit those files.
- **Allowed alternative inside current scope**: If T6 proves a new parity-specific corpus entry point can be implemented entirely under `tests/longmemeval/**` without deleting/rewiring out-of-scope runner imports, T7 may implement that harness-native entry point and T14 must use it. The artifact must explicitly mark `orchestrator/eval/runner.py` as legacy/out-of-scope for this parity run.

- [x] 7. Route harness through production memory context and system prompt assembly

  **What to do**: Implement the thin adapter in harness code. For each LongMemEval question, derive deterministic `synthetic_user_id = UUID5(<recorded namespace>, question_id)`, create/ensure the user row, ingest that question's full `haystack_sessions` as conversations/messages under that synthetic user, run production extraction synchronously inline against those haystack messages, leave L0 empty, call production `build_memory_context(store, conversation_id)` or `build_memory_context(store, conversation_id, max_tokens=...)` where `conversation_id` belongs to a conversation owned by the synthetic user and production derives `user_id` internally, then call `assemble_system_prompt(memory_context=...)`. Pass the final prompt to `answer_with_llm()` unchanged. Move to the next question under a different synthetic user; do not clear anything in the per-question loop. Preserve harness-only concerns: question selection, exclusions, judging, retry/skip, artifact serialization. Record `synthetic_user_id`, `memory_context`, final system prompt, extraction counts, and relevant conversation/message IDs in metadata for T10/T11/T14.
  **Must NOT do**: No string concat/slicing/regex/formatting/normalization after production return. No production-memory edits. Do not aggregate all questions under one user/store. Do not bulk-load pre-extracted/oracle memories. Do not add per-question teardown/clear steps. Do not preserve scoped retrieval by modifying `build_memory_context()`.

  **Recommended Agent Profile**:
  - Category: `business-logic` - Reason: data-flow adapter and artifact metadata.
  - Skills: [] - no special skill.
  - Omitted: `memory-wave-diagnostic` - verification tasks handle anomaly probes.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: T8,T9,T10,T11 | Blocked By: T6-GATE PASS

  **References**:
  - API/Type: `orchestrator/memory/injection.py:168-336` - production calls to invoke.
  - Pattern: `tests/longmemeval/evaluate.py:627-714` - per-question evaluation flow.
  - Pattern: `tests/longmemeval/ingest.py` - extend within allowed scope with `create_synthetic_user(question_id: str) -> uuid.UUID` and parameterize `ingest_session()` or equivalent session-ingest helper to accept target `user_id`.
  - Pattern: `orchestrator/eval/runner.py:1593+` - read-only evaluation orchestration context; halt if out-of-scope changes are required.

  **Acceptance Criteria**:
  - [ ] Every T6-emptied call site invokes production entry point(s) per Oracle-ratified strategy.
  - [ ] One-question LongMemEval smoke runs end-to-end without exception and produces deterministic `synthetic_user_id`, extraction outputs, non-empty prompt, and model response.
  - [ ] Static review confirms no transformations between production prompt return and model invocation.
  - [ ] `tests/longmemeval/ingest.py` exposes or extends a helper equivalent to `create_synthetic_user(question_id: str) -> uuid.UUID` and a session ingest helper that accepts target `user_id`; existing `create_test_user()` / `TEST_USER_ID` / `TEST_USER_EMAIL` behavior is preserved for legacy/non-parity paths or explicitly marked deprecated, not repurposed.
  - [ ] Per-question metadata includes `synthetic_user_id`, haystack conversation/message IDs, extraction counts, and enough fields for T10 direct-vs-harness comparison and T14 W1 probe consumption.

  **QA Scenarios**:
  ```
  Scenario: One-question harness execution
    Tool: Bash
    Steps: Run canonical evaluate on one known IE-* question with existing test DB/config; capture result JSON.
    Expected: Result has deterministic synthetic_user_id, non-empty answer_prompt_metadata.system_message, extraction count metadata, and no exception.
    Evidence: .sisyphus/evidence/task-7-one-question.json

  Scenario: Prompt no-transform chain
    Tool: Bash
    Steps: Run AST/grep check for operations on the production prompt variable between call and answer_with_llm and for cleanup/delete calls inside the per-question loop.
    Expected: Only assignment, payload inclusion, or function argument passing for prompt; no per-question cleanup/delete calls.
    Evidence: .sisyphus/evidence/task-7-no-transform.txt

  Scenario: Synthetic ingest helpers
    Tool: Bash
    Steps: Inspect tests/longmemeval/ingest.py for create_synthetic_user(question_id) and target-user session ingest support; run a one-question ingest twice with same question_id.
    Expected: Both runs resolve the same synthetic_user_id; conversations/messages belong to that user; legacy TEST_USER_ID remains unused for parity path.
    Evidence: .sisyphus/evidence/task-7-synthetic-ingest.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/longmemeval/**` only; halt artifact if other files require edits

- [x] 8. Verify zero `orchestrator/memory/**` modifications

  **What to do**: Establish `harness-parity-base` as the commit at plan start if not already tagged. Run `git diff harness-parity-base..HEAD -- 'orchestrator/memory/**'` or equivalent documented base command. If no base tag exists, record `git rev-parse HEAD` at T1 start in the artifact and use it as base.
  **Must NOT do**: Do not proceed if diff is non-empty.

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: git invariant check.
  - Skills: [`git-master`] - safe git inspection.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: T9 | Blocked By: T7

  **References**:
  - Guardrail: `orchestrator/memory/**` read-only.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_production_clean.md` exists.
  - [ ] Diff output is empty; if non-empty, artifact includes diff verbatim and HALT declaration.

  **QA Scenarios**:
  ```
  Scenario: Production diff empty
    Tool: Bash
    Steps: Run git diff <base>..HEAD -- 'orchestrator/memory/**'.
    Expected: Empty output.
    Evidence: .sisyphus/evidence/task-8-production-diff.txt

  Scenario: Halt wording present on failure
    Tool: Bash
    Steps: If diff non-empty, inspect artifact for HALT.
    Expected: HALT appears before any later task begins.
    Evidence: .sisyphus/evidence/task-8-halt-check.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_production_clean.md`

- [x] 9. Static call-graph parity assertion

  **What to do**: Walk each allowed harness assembly path from production entry call to model invocation. Allowed operations on prompt return: assignment, structured payload inclusion, function argument. Disallowed: concatenation, substitution, slicing, formatting, conditional rewriting, encoding/decoding, normalization. If model invocation is routed through an out-of-scope file, document the chain read-only and halt if a code edit there is required.
  **Must NOT do**: Do not rely only on author claims; inspect code chain.

  **Recommended Agent Profile**:
  - Category: `business-logic` - Reason: code-flow verification.
  - Skills: [] - no special skill.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: NO | Wave 4 | Blocks: T10 | Blocked By: T8

  **References**:
  - Artifact: T1 inventory call-site list.
  - Code: `tests/longmemeval/evaluate.py`; read-only context: `orchestrator/eval/runner.py`.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_static_check.md` exists.
  - [ ] Every call-site → model-invocation chain is line-by-line documented.
  - [ ] All operations are allowed; any disallowed op causes HALT.

  **QA Scenarios**:
  ```
  Scenario: Static chain approval
    Tool: Bash
    Steps: Run grep/AST commands listed in artifact against prompt variable names.
    Expected: No disallowed operation hits.
    Evidence: .sisyphus/evidence/task-9-static-chain.txt

  Scenario: Consumer-path coverage
    Tool: Bash
    Steps: Compare chains in static check to T1 inventory.
    Expected: Same count and names.
    Evidence: .sisyphus/evidence/task-9-chain-coverage.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_static_check.md`

- [x] 10. Stratified runtime parity spot-check

  **What to do**: Select 20 questions stratified by T4 category map: at least 2 per category present, weighted toward IE-user, IE-preference, IE-assistant, MR, TR. For each, prepare that question's synthetic user and haystack state through the harness path, then capture (A) exact prompt the harness sends to the answering model under that synthetic user, and (B) direct `build_memory_context()` + `assemble_system_prompt()` output for the same `synthetic_user_id` and prepared state. Apply T5 equivalence. Include side-by-side excerpts for at least one IE-*, one MR, one TR.
  **Must NOT do**: No skipped questions, no vague manual visual equivalence.

  **Recommended Agent Profile**:
  - Category: `business-logic` - Reason: runtime harness/prod comparison.
  - Skills: [] - no special skill.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: NO | Wave 4 | Blocks: T11 | Blocked By: T9

  **References**:
  - Artifact: `tests/benchmark_results/harness_parity_oracle_review.md` - equivalence rules.
  - Artifact: `tests/benchmark_results/harness_parity_category_paths.md` - stratification.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_spot_check.md` exists.
  - [ ] 20/20 comparisons pass; zero skipped; every comparison records `question_id` and `synthetic_user_id`.
  - [ ] Failures include divergent excerpt/root cause and HALT before T11.

  **QA Scenarios**:
  ```
  Scenario: 20-question parity pass
    Tool: Bash
    Steps: Run spot-check command/script recorded in artifact.
    Expected: 20 results with question_id + synthetic_user_id, all pass per equivalence definition.
    Evidence: .sisyphus/evidence/task-10-spot-check.json

  Scenario: Stratification check
    Tool: Bash
    Steps: Count categories in spot-check artifact.
    Expected: At least 2 per present category; IE/MR/TR represented.
    Evidence: .sisyphus/evidence/task-10-stratification.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_spot_check.md`

- [x] 11. Smoke trace — single IE-* question end-to-end

  **What to do**: Run one known IE-* question end-to-end through parity-fixed harness under its deterministic synthetic user. Capture `question_id`, `synthetic_user_id`, haystack ingestion counts, inline extraction counts, full assembled prompt, model response, judge verdict, `memories_used`, selected/retrieved IDs, retrieval latency, encryption decode status, provider route. Apply rollback triggers: `memories_used=0`, latency p95 >1500ms, encryption failure, provider routing failure, or any retrieved memory from a different synthetic user.
  **Must NOT do**: No status claims without same-turn probe evidence.

  **Recommended Agent Profile**:
  - Category: `business-logic` - Reason: end-to-end benchmark smoke.
  - Skills: [`memory-wave-diagnostic`] - smoke trace discipline.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: NO | Wave 4 | Blocks: T12,T13 | Blocked By: T10

  **References**:
  - Skill: `memory-wave-diagnostic` Step 2 smoke trace.
  - Artifact: previous Wave 0 smoke docs for format examples.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_smoke.md` exists.
  - [ ] Prompt is non-empty and includes at least one known retrievable memory substring from the same synthetic user.
  - [ ] `memories_used > 0`, p95 latency <1500ms, encryption decode success, no provider routing error, no cross-synthetic-user memory retrieval.

  **QA Scenarios**:
  ```
  Scenario: IE smoke passes rollback gates
    Tool: Bash
    Steps: Run one-question smoke and parse telemetry.
    Expected: All rollback gates pass, including same-synthetic-user retrieval.
    Evidence: .sisyphus/evidence/task-11-smoke.json

  Scenario: Memory substring present
    Tool: Bash
    Steps: Compare prompt text to known retrievable memory set for selected IE question and synthetic_user_id.
    Expected: At least one exact substring appears and belongs to the same synthetic user.
    Evidence: .sisyphus/evidence/task-11-memory-substring.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_smoke.md`

- [x] 12. Synthetic-user inline extraction sanity check

  **What to do**: Verify that the new synthetic-user adapter invokes production extraction synchronously inline for haystack messages, without arq/debounce and without pre-extracted memory loading. Run a bounded 3-question sample spanning at least IE-user, MR, and TR. For each sample, record `question_id`, `synthetic_user_id`, haystack session/message counts, extraction invocation count, created memory count, extraction errors, and whether retrieved memories for the question come from the same synthetic user only. This is an adapter sanity check, not the separate `tests/benchmark_extraction.py` producer-layer benchmark.
  **Must NOT do**: Do not search for, rebuild, or run `tests/benchmark_extraction.py` in this plan. Do not bulk-load pre-extracted memories. Do not alter production extraction policy or `orchestrator/memory/**`.

  **Recommended Agent Profile**:
  - Category: `business-logic` - Reason: adapter extraction path verification.
  - Skills: [] - no special skill.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: YES after T11 with T13 | Wave 4 | Blocks: T14 | Blocked By: T11

  **References**:
  - Artifact: `tests/benchmark_results/harness_parity_dependency_audit.md` - synchronous extraction path chosen in T3.
  - Pattern: `tests/longmemeval/**` - synthetic-user adapter code.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_inline_extraction_check.json` exists.
  - [ ] Artifact includes 3 sampled questions with `synthetic_user_id`, haystack counts, extraction invocation count, created memory count, and extraction error count.
  - [ ] Zero sample uses pre-extracted/oracle memories; zero retrieval result belongs to a different synthetic user.
  - [ ] Artifact explicitly states that `tests/benchmark_extraction.py` discovery/rebuild is out of scope and separately commissioned.

  **QA Scenarios**:
  ```
  Scenario: Inline extraction path fires
    Tool: Bash
    Steps: Run the 3-question synthetic-user extraction sanity command recorded in artifact; parse invocation/memory/error counts.
    Expected: Each sample invokes production extraction synchronously, produces recorded memory counts, and has zero extraction errors.
    Evidence: .sisyphus/evidence/task-12-inline-extraction.json

  Scenario: No oracle memory loading
    Tool: Bash
    Steps: Inspect adapter code and sample artifact for bulk/pre-extracted memory load paths.
    Expected: No pre-extracted memory import/load path; all sample memories originate from inline extraction under the sample synthetic user.
    Evidence: .sisyphus/evidence/task-12-no-oracle-load.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_inline_extraction_check.json`

- [x] 13. Encryption smoke

  **What to do**: Decrypt at least 20 sampled rows from `messages.content`, `memories.content`, and the actual extraction log table/column discovered in migrations/store (`memory_extraction_log.input_snippet` if present; otherwise exact discovered equivalent). Confirm non-empty valid UTF-8. Any failure halts.
  **Must NOT do**: Do not print decrypted secret/user content in logs beyond safe short redacted shape checks.

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: bounded DB smoke.
  - Skills: [] - no special skill.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: YES after T11 with T12 | Wave 4 | Blocks: T14 | Blocked By: T11

  **References**:
  - API/Type: `orchestrator/memory/encryption.py` - Fernet wrapper.
  - API/Type: `orchestrator/memory/store.py` - encrypted columns.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_encryption_smoke.md` exists.
  - [ ] At least 20 rows from each target decoded successfully, non-empty UTF-8.
  - [ ] Failures include table, row identifier, error class, and HALT.

  **QA Scenarios**:
  ```
  Scenario: Three-table decrypt sample
    Tool: Bash
    Steps: Run smoke script/query using project encryption key; redact values in output.
    Expected: 60+ successful decodes, zero failures.
    Evidence: .sisyphus/evidence/task-13-encryption-smoke.md

  Scenario: No plaintext leakage
    Tool: Bash
    Steps: Inspect artifact for raw decrypted long content; values should be redacted/truncated to shape.
    Expected: No full sensitive content printed.
    Evidence: .sisyphus/evidence/task-13-redaction-check.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_encryption_smoke.md`

- [x] 14. Full LongMemEval_S corpus run under parity-fixed harness

  **What to do**: Run full 500-question LongMemEval_S with same 27-question exclusion list as Wave 0 baseline using the parity-fixed entry point from T7. If T6-GATE kept `orchestrator/eval/runner.py` out of scope, this must be a harness-native entry point under `tests/longmemeval/**`; do not call the legacy runner unless T6-GATE explicitly passed for that path or the user authorized scope expansion. For each question, derive its deterministic synthetic user, ingest its haystack, run synchronous inline extraction, answer/judge under that synthetic user, then move on without clearing state. Capture per-question records: `question_id`, `synthetic_user_id`, `category`, `judge_verdict`, `retrieved_memory_ids`, `memories_used`, extraction counts/errors, raw model response, raw judge response. Compute aggregate adjusted score and per-category scores from raw counts. After the run, leave all 500 synthetic users in DB for inspection; cleanup is a separate post-run script/drop-by-synthetic-naming-pattern operation, not embedded in T14.
  **Must NOT do**: Do not read aggregate status fields as source of truth; recompute. Do not silently route through `orchestrator/eval/runner.py` if T6-GATE marked it legacy/out-of-scope.

  **Recommended Agent Profile**:
  - Category: `business-logic` - Reason: long-running benchmark execution and artifact validation.
  - Skills: [] - no special skill.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: NO | Wave 5 | Blocks: T15,T16,T17-T20 | Blocked By: T12,T13

  **References**:
  - CLI: parity-fixed command recorded by T7, preferably under `tests/longmemeval/**` if runner remains out of scope; legacy `python -m orchestrator.eval.longmemeval run --dataset <LongMemEval_S>` is allowed only if T6-GATE proves it remains valid without out-of-scope edits or user authorized expansion.
  - Artifact convention: `tests/benchmark_results/{run}/longmemeval_results.jsonl`.
  - Previous baseline: `tests/benchmark_results/wave0_full_corpus*/` and related Wave 0 corpus-run artifacts.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_baseline_run.json` exists.
  - [ ] Total=500, excluded=27, correct+incorrect+excluded=500.
  - [ ] Every per-question record contains required W1 probe fields, including `synthetic_user_id`.
  - [ ] Median `memories_used` >0; provider routing failure rate <5%.
  - [ ] Artifact states synthetic users persisted post-run and names the separate cleanup command/script without executing it during the per-question loop.
  - [ ] Artifact records the exact corpus command and whether it used the harness-native entry point or legacy runner path; if legacy runner path was used, artifact cites the T6-GATE PASS evidence.

  **QA Scenarios**:
  ```
  Scenario: Raw count recomputation
    Tool: Bash
    Steps: Parse per-question JSON and recompute aggregate/per-category scores.
    Expected: Recomputed values match artifact values; total equation holds.
    Evidence: .sisyphus/evidence/task-14-score-recompute.json

  Scenario: W1 probe field completeness
    Tool: Bash
    Steps: Validate every record has question_id, synthetic_user_id, category, judge_verdict, retrieved_memory_ids, memories_used, extraction counts/errors.
    Expected: 500/500 records valid; synthetic_user_id deterministic from question_id; retrieved_memory_ids explicit list or empty marker.
    Evidence: .sisyphus/evidence/task-14-field-validation.json
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_baseline_run.json`

- [x] 15. Anomaly check and conditional confirmation run

  **What to do**: Compare T14 aggregate to 10.4% and per-category rank order to closure memo category numbers. Anomaly if aggregate deviation >10pp absolute OR more than 3 of 6 reported category ranks move. If anomalous, run second full corpus under identical config; declare baseline only if runs agree within ±2pp. Default declaration rule: no anomaly = T14 number; anomaly + agreement = mean of two aggregate scores; anomaly + disagreement = HALT baseline undeterminable.
  **Must NOT do**: Do not treat lower score as failure unless parity/rollback gates fail.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: statistical/anomaly interpretation.
  - Skills: [] - no special skill.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: NO | Wave 5 | Blocks: T16-T20 | Blocked By: T14

  **References**:
  - Artifact: `tests/benchmark_results/harness_parity_baseline_run.json`.
  - Doc: `tests/benchmark_results/wave0_closure_memo.md`.
  - Doc: `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md`.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_baseline_decision.md` exists.
  - [ ] Aggregate deviation and rank-reorder count shown numerically.
  - [ ] Confirmation artifact exists if anomaly triggered.
  - [ ] New baseline declared or HALT declared.

  **QA Scenarios**:
  ```
  Scenario: Anomaly math replay
    Tool: Bash
    Steps: Recompute deviation and rank movement from raw artifacts.
    Expected: Matches decision artifact.
    Evidence: .sisyphus/evidence/task-15-anomaly-math.json

  Scenario: Confirmation agreement
    Tool: Bash
    Steps: If second run exists, recompute aggregate difference.
    Expected: Difference <=2pp or HALT declared.
    Evidence: .sisyphus/evidence/task-15-confirmation.json
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_baseline_decision.md`, optional `tests/benchmark_results/harness_parity_baseline_run_confirm.json`

- [x] 16. Oracle reviews baseline interpretation and roadmap-priority implications

  **What to do**: Ask Oracle to review T15 baseline by category against `docs/MEMORY_UPGRADE_ROADMAP.md`. Identify categories crossing 5% threshold in either direction and any wave-priority implications. This is informational and does not gate shipping unless Oracle identifies data invalidity.
  **Must NOT do**: Do not rewrite roadmap priorities in this task. Do not recommend extraction-policy changes for IE-assistant here; reserve that design mismatch for T22 postmortem section (h).

  **Recommended Agent Profile**:
  - Category: `ultrabrain` / subagent `oracle` - Reason: model-diverse interpretation.
  - Skills: [] - no special skill.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: YES after T15 with docs prep | Wave 5 | Blocks: T22 | Blocked By: T15

  **References**:
  - Artifact: `tests/benchmark_results/harness_parity_baseline_decision.md`.
  - Roadmap: `docs/MEMORY_UPGRADE_ROADMAP.md`.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/harness_parity_baseline_oracle.md` exists.
  - [ ] Each category has old number, new number, delta.
  - [ ] Threshold crossings and priority implications are specific or explicitly none with numeric reasoning.

  **QA Scenarios**:
  ```
  Scenario: Category delta coverage
    Tool: Bash
    Steps: Compare categories in T15 artifact to Oracle review.
    Expected: Every category listed with old/new/delta.
    Evidence: .sisyphus/evidence/task-16-category-deltas.txt

  Scenario: Threshold crossing check
    Tool: Bash
    Steps: Recompute 5% crossings from values.
    Expected: Matches Oracle artifact list.
    Evidence: .sisyphus/evidence/task-16-thresholds.json
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_baseline_oracle.md`

- [x] 17. Additive amendment to Wave 0 closure memo

  **What to do**: Append a footnote or post-closure correction section to `tests/benchmark_results/wave0_closure_memo.md`. Additions only. Name `_format_eval_memory_block`, cite this plan path, cite new baseline number from T15, quote at least one sentence from T2 root cause.
  **Must NOT do**: No deletion/reflow/modification of pre-existing lines.

  **Recommended Agent Profile**:
  - Category: `writing` - Reason: surgical doc patch.
  - Skills: [`git-master`] - diff verification only.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: YES with T18-T20 | Wave 6 | Blocks: T21 | Blocked By: T15

  **References**:
  - Doc: `tests/benchmark_results/wave0_closure_memo.md`.
  - Artifact: `tests/benchmark_results/harness_parity_path_a_reconstruction.md`.
  - Artifact: `tests/benchmark_results/harness_parity_baseline_decision.md`.

  **Acceptance Criteria**:
  - [ ] Diff shows additions only.
  - [ ] Appended section references plan file, `_format_eval_memory_block`, new baseline, and T2 quote.

  **QA Scenarios**:
  ```
  Scenario: Additive-only diff
    Tool: Bash
    Steps: Run git diff -- tests/benchmark_results/wave0_closure_memo.md.
    Expected: Diff contains only added lines.
    Evidence: .sisyphus/evidence/task-17-additive-diff.txt

  Scenario: Required references present
    Tool: Bash
    Steps: Grep appended section for required path/name/baseline.
    Expected: All required references present.
    Evidence: .sisyphus/evidence/task-17-reference-check.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/wave0_closure_memo.md`

- [x] 18. Surgical update to actual Wave 0 aligned baseline document

  **What to do**: Patch discovered actual file `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md` because `tests/benchmark_results/wave0_aligned_baseline.md` is absent. Locate 10.4% / 49/473 production-aligned baseline sentence(s). Replace surgically so 10.4% remains labeled harness-artifact pre-parity, and the T15 number becomes the production-faithful post-parity anchor with artifact path/date. Record missing requested path in diff summary.
  **Must NOT do**: No reflow/header/table churn. If actual file structure cannot be matched, stop and report.

  **Recommended Agent Profile**:
  - Category: `writing` - Reason: surgical doc patch.
  - Skills: [`git-master`] - diff verification.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: YES with T17,T19,T20 | Wave 6 | Blocks: T21 | Blocked By: T15

  **References**:
  - Doc: `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md`.
  - Missing requested path: `tests/benchmark_results/wave0_aligned_baseline.md`.

  **Acceptance Criteria**:
  - [ ] Targeted sentence replacement only.
  - [ ] 10.4% preserved with harness-artifact framing.
  - [ ] New baseline number, source artifact, and date cited.
  - [ ] Diff summary quotes exact before/after text.

  **QA Scenarios**:
  ```
  Scenario: Surgical diff only
    Tool: Bash
    Steps: Run git diff -- tests/benchmark_results/wave0_option_a_production_aligned_baseline.md.
    Expected: Only targeted sentence/line replacement plus optional one inserted line.
    Evidence: .sisyphus/evidence/task-18-surgical-diff.txt

  Scenario: Baseline framing check
    Tool: Bash
    Steps: Grep file for 10.4%, harness-artifact, T15 baseline number, and harness_parity_baseline_run.json.
    Expected: All present.
    Evidence: .sisyphus/evidence/task-18-framing-check.txt
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md`

- [x] 19. Surgical patch to W1 plan anchors

  **What to do**: Patch `.sisyphus/plans/wave1-prompt-surface-changes.md`: replace 10.4% baseline references with T15 new baseline; replace `pre-wave-1` rollback/baseline anchor references with `harness-parity-shipped` except explicitly allowed W1 TODO 18 rollback-target exception; patch TODO 5 description/Form/Substance to use new baseline artifact and ±1pp band around new baseline. Before editing, record grep counts for `10.4%` and `pre-wave-1`.
  **Must NOT do**: No reflow. If TODO 5 structure differs, stop and report.

  **Recommended Agent Profile**:
  - Category: `writing` - Reason: surgical plan patch.
  - Skills: [`git-master`] - diff/grep counts.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: YES with T17,T18,T20 | Wave 6 | Blocks: T21 | Blocked By: T15

  **References**:
  - Plan: `.sisyphus/plans/wave1-prompt-surface-changes.md`.
  - Artifact: `tests/benchmark_results/harness_parity_baseline_decision.md`.

  **Acceptance Criteria**:
  - [ ] Diff changes only named insertion-point classes.
  - [ ] Replacement counts match pre-edit grep counts minus documented exception.
  - [ ] W1 TODO 5 references new baseline and ±1pp band.
  - [ ] `harness-parity-shipped` used as W1+ anchor where applicable.

  **QA Scenarios**:
  ```
  Scenario: Grep count reconciliation
    Tool: Bash
    Steps: Compare pre-edit count artifact to post-edit grep for 10.4% and pre-wave-1.
    Expected: Counts reconcile with documented exception only.
    Evidence: .sisyphus/evidence/task-19-grep-reconcile.txt

  Scenario: TODO 5 band check
    Tool: Bash
    Steps: Inspect W1 TODO 5 patched section.
    Expected: New baseline artifact path and ±1pp numeric band present.
    Evidence: .sisyphus/evidence/task-19-todo5-band.txt
  ```

  **Commit**: NO | Message: n/a | Files: `.sisyphus/plans/wave1-prompt-surface-changes.md`

- [x] 20. Surgical update to roadmap baseline reference

  **What to do**: Patch `docs/MEMORY_UPGRADE_ROADMAP.md` opening note sentence that states production-aligned baseline as 10.4%. Replace with T15 baseline number, cite T14 artifact, and note 10.4% was harness-artifact pre-parity. Leave priors/lift tables and other 10.4% contextual references untouched.
  **Must NOT do**: Do not edit root `MEMORY_UPGRADE_ROADMAP.md` unless discovered to exist; do not reflow roadmap.

  **Recommended Agent Profile**:
  - Category: `writing` - Reason: surgical doc patch.
  - Skills: [`git-master`] - diff verification.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: YES with T17-T19 | Wave 6 | Blocks: T21 | Blocked By: T15

  **References**:
  - Roadmap: `docs/MEMORY_UPGRADE_ROADMAP.md`.

  **Acceptance Criteria**:
  - [ ] Diff shows single targeted opening-section sentence replacement.
  - [ ] New baseline and artifact cited.
  - [ ] No other roadmap section changed.

  **QA Scenarios**:
  ```
  Scenario: Single-hunk roadmap diff
    Tool: Bash
    Steps: Run git diff -- docs/MEMORY_UPGRADE_ROADMAP.md.
    Expected: One targeted opening-section hunk only.
    Evidence: .sisyphus/evidence/task-20-roadmap-diff.txt

  Scenario: Out-of-scope tables untouched
    Tool: Bash
    Steps: Compare table sections before/after using git diff context.
    Expected: No per-wave priors/lift/decision table edits.
    Evidence: .sisyphus/evidence/task-20-table-guard.txt
  ```

  **Commit**: NO | Message: n/a | Files: `docs/MEMORY_UPGRADE_ROADMAP.md`

- [ ] 21. Create lightweight tag `harness-parity-shipped`

  **What to do**: After T17-T20 pass and working tree contains verified plan changes, create local lightweight tag `harness-parity-shipped` at HEAD. If changes have not been committed yet, first ask user/execute approved commit workflow in Sisyphus context; do not tag an uncommitted worktree. Do not push.
  **Must NOT do**: Do not annotate tag unless explicitly requested. Do not move/delete `pre-wave-1`. Do not push.

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: git tag operation.
  - Skills: [`git-master`] - safe git protocol.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: NO | Wave 7 | Blocks: T22 | Blocked By: T17,T18,T19,T20

  **References**:
  - Git tag: `harness-parity-shipped`.

  **Acceptance Criteria**:
  - [ ] `git tag -l harness-parity-shipped` lists tag.
  - [ ] `git rev-parse harness-parity-shipped` equals `git rev-parse HEAD`.
  - [ ] Tag is lightweight; no push occurred.

  **QA Scenarios**:
  ```
  Scenario: Tag points at HEAD
    Tool: Bash
    Steps: Run git rev-parse harness-parity-shipped and git rev-parse HEAD.
    Expected: Hashes identical.
    Evidence: .sisyphus/evidence/task-21-tag-head.txt

  Scenario: Remote untouched
    Tool: Bash
    Steps: Run git status -sb and git log @{u}..HEAD if upstream exists; verify no push command was run.
    Expected: Local-only tag/change state; no remote mutation.
    Evidence: .sisyphus/evidence/task-21-no-push.txt
  ```

  **Commit**: YES | Message: `test(memory): route LongMemEval through production injection` | Files: all implementation/docs/artifacts before tagging

- [ ] 22. Write harness parity postmortem

  **What to do**: Create `tests/benchmark_results/harness_parity_postmortem.md` with eight sections: (a) inventory and classifications, (b) Path A reconstruction/root cause, (c) production dependency audit and (b)/(c) counts, (d) defects beyond `_format_eval_memory_block` with file:line/disposition, (e) clean format-change comparison with new baseline raw counts/per-category deltas, (f) wave-priority implications from T16, (g) surgical patches with paths/line counts, (h) Path A successor methodology recommendations plus postmortem-only IE-assistant design-mismatch note.
  **Must NOT do**: Do not summarize from memory when artifacts contain raw counts; cite artifacts and recompute where required.

  **Recommended Agent Profile**:
  - Category: `writing` - Reason: technical postmortem synthesis.
  - Skills: [] - no special skill.
  - Omitted: [] - none.

  **Parallelization**: Can Parallel: NO | Wave 7 | Blocks: Final verification | Blocked By: T21

  **References**:
  - Artifacts: T1-T16 outputs.
  - Diffs: T17-T20 evidence.
  - Tag: `harness-parity-shipped`.
  - Context: IE-assistant note — Daemon selectively extracts assistant content only when it encodes user-side information; LongMemEval single-session-assistant questions test remembering assistant utterances, so this is a benchmark-vs-Daemon policy mismatch to document only.

  **Acceptance Criteria**:
  - [ ] Postmortem exists with all eight sections.
  - [ ] Section (a) lists every T1 finding by name.
  - [ ] Section (d) includes each defect with file:line and disposition.
  - [ ] Section (e) reports raw counts from T14/T15, not narrative-only values, and explicitly states that with per-question synthetic-user isolation the comparison isolates prompt-format change rather than mixing prompt-format with retrieval-scope changes.
  - [ ] Section (h) names at least one static call-graph successor check that would have caught `_format_eval_memory_block` and includes the IE-assistant design-mismatch note without proposing implementation in this plan.

  **QA Scenarios**:
  ```
  Scenario: Eight-section completeness
    Tool: Bash
    Steps: Grep headings in postmortem.
    Expected: Sections (a) through (h) present; section (h) contains IE-assistant and extraction-policy mismatch note.
    Evidence: .sisyphus/evidence/task-22-section-check.txt

  Scenario: Raw count consistency
    Tool: Bash
    Steps: Recompute baseline raw counts from T14 and compare to postmortem section (e).
    Expected: Counts match exactly and section (e) frames attribution as prompt-format-only under synthetic-user isolation.
    Evidence: .sisyphus/evidence/task-22-count-check.json
  ```

  **Commit**: NO | Message: n/a | Files: `tests/benchmark_results/harness_parity_postmortem.md`

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [ ] F1. Plan Compliance Audit — oracle
  - Verify all 22 TODOs and halt branches were followed; especially synthetic-user-per-question isolation, synchronous inline production extraction, no `orchestrator/memory/**` edits, no remote push, no moved/deleted `pre-wave-1`, and W1 anchors patched to `harness-parity-shipped`.
- [ ] F2. Code Quality Review — unspecified-high
  - Independently inspect synthetic-user adapter, deterministic UUID5 user derivation, inline extraction path, prompt variable flow, dead-code removal, and any documented halt for out-of-scope tests/config-pin changes.
- [ ] F3. Real Manual QA — unspecified-high
  - Re-run T9/T10 static/runtime parity checks, T12 inline extraction sanity checks, and T14/T15 raw-score recomputation from artifacts; do not rely on status fields.
- [ ] F4. Scope Fidelity Check — deep
  - Compare final diff against scope: allowed files only, surgical docs only, production memory untouched, W1 feature work absent, no extraction-benchmark rebuild, no guardrail-hash investigation, no IE-assistant policy change.

## Commit Strategy
- One implementation commit after T20 and before T21 if user has authorized committing in execution context: `test(memory): route LongMemEval through production injection`.
- Tag `harness-parity-shipped` after the verified commit. No push.
- If pre-commit hooks modify files, re-run relevant T8/T9/T17-T20 checks before tagging.

## Success Criteria
- Harness calls production memory context/system prompt path directly.
- Each LongMemEval question runs under deterministic synthetic user isolation with synchronous inline production extraction and no per-question teardown.
- Runtime spot-check passes 20 stratified prompt-equivalence comparisons.
- Full LongMemEval_S baseline is declared from raw per-question records.
- Documentation reclassifies 10.4% as harness-artifact and points W1+ at post-parity baseline/tag.
- Postmortem records methodology failure and successor static-call-graph check.
