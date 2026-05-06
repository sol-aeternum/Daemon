# Wave 1 Prompt-Surface Changes

## TL;DR
> **Summary**: Execute W1 as a prompt-surface benchmark cycle: JSON memory evidence, Chain-of-Note instruction, confidence bins, hedge/abstain guidance, restored abstention guardrail, and gated LongMemEval_S validation. Implementation changes are confined to `orchestrator/memory/injection.py`; if the benchmark consumer path cannot measure that production prompt surface, the wave halts before implementation.
> **Deliverables**: Diagnostic/probe artifacts, `injection.py` changes, extraction/encryption/smoke/full-corpus gate artifacts, ship-or-rollback execution, mandatory postmortem.
> **Effort**: Large
> **Parallel**: YES - after serial gates
> **Critical Path**: Consumer-path gate → R/F/A probe → diagnostic audit → baseline reproduction → implementation → validation/gate → ship/rollback → postmortem

## Context
### Original Request
- Lift LongMemEval_S from the historical pre-parity Wave 0 Option A harness artifact (~10.4%, 49/473) by changing only how retrieved memories are formatted into the answering model prompt.
- Bundle W1.a and W1.b into one benchmark cycle and one ship/no-ship decision.
- Keep implementation mutations confined to `orchestrator/memory/injection.py`.

### Research Summary
- Production functions: `build_memory_context()` at `orchestrator/memory/injection.py:168`, `assemble_system_prompt()` at `orchestrator/memory/injection.py:311`, `get_l0_memories()` at `orchestrator/memory/injection.py:341`, `estimate_tokens()` at `orchestrator/memory/injection.py:117`.
- Current L1 rendering is bullet prose at `orchestrator/memory/injection.py:268-276`; current L0 is separate `[FROZEN MEMORIES]`, fetched at `orchestrator/memory/injection.py:183` and prepended at `orchestrator/memory/injection.py:306-307`.
- Current `DEFAULT_MAX_TOKENS` is 2500 at `orchestrator/memory/injection.py:34`; W1 contract requires 1500.
- `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` exists at `orchestrator/prompts.py:3-6` but is not appended by `assemble_system_prompt()`; tests/comments expect it.
- Likely C3 artifact: `tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_results.jsonl`; the historical pre-parity Wave 0 Option A harness-artifact figure was 49/473 = 10.36% after 27 invalid-ciphertext exclusions.
- LongMemEval execution needs live PostgreSQL/Redis plus `DATABASE_URL` and `DAEMON_ENCRYPTION_KEY`.

### Metis / Oracle Review
- Critical risk: current LongMemEval may format memories separately in `tests/longmemeval/evaluate.py`, so `injection.py` changes could be invisible to the benchmark. This plan adds TODO 0 as a hard halt gate; it does not authorize `evaluate.py` edits.
- Guardrail is dead code today; W1 must restore and preserve it in `assemble_system_prompt()`.
- `provenance` is not a DB column; derive it from existing memory dict fields only.
- User requires L0 in the same JSON format; L0 must be array-head elements, not a separate stanza.

## Work Objectives
### Core Objective
Modify `orchestrator/memory/injection.py` so production memory prompt injection emits structured JSON evidence with confidence-aware instructions, then validate with benchmark artifacts that actually traverse the same consumer prompt surface.

### Definition of Done
- `git diff harness-parity-shipped..HEAD -- orchestrator/memory/` shows changes only in `orchestrator/memory/injection.py`.
- Production and benchmark prompt captures both contain parseable `<memories>` JSON arrays with fields `content`, `provenance`, `timestamp`, `confidence`, `source_type`.
- L0 entries are first when present; `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`, Chain-of-Note, and hedge/abstain guidance are present when memory evidence exists.
- Extraction benchmark: Precision ≥0.95, Recall ≥0.85, adversarial misfires ≤2.
- Encryption smoke succeeds on ≥20 sampled rows each from messages, memories, and extraction log snippet content.
- Gate passes only if LongMemEval_S aggregate lift is ≥+2pp over reproduced baseline, confirmation rule passes if needed, no previously-passing category drops below 5%, median `memories_used` >0, retrieval p95 <1500ms, and provider routing failure rate <5%.

### Must NOT Have
- No producer-layer, retrieval-layer, schema, frontend, reranker, embedding-key, time-filter, or pool-size changes.
- No edits to `tests/longmemeval/evaluate.py` in this plan. If required, halt and request a separate harness-parity plan or explicit user approval.
- No parser for Chain-of-Note output; it is prompt-shaping only.
- No new external packages.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: tests-after + existing pytest/benchmark harnesses; no committed test-file edits unless separately authorized.
- QA policy: Every task has an agent-executed happy path and failure/edge scenario.
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}` plus required `tests/benchmark_results/wave1_*` artifacts.

## Execution Strategy
### Parallel Execution Waves
Wave 0: TODO 0 consumer-path gate (serial hard gate)
Wave 1: TODOs 1-3 diagnostic probe (serial because each consumes prior artifact)
Wave 2: TODOs 4-5 audit and baseline (serial)
Wave 3: TODOs 6-11 implementation in `injection.py` (serial)
Wave 4: TODOs 12-14 pre-gate validation (12 and 13 parallel, then 14)
Wave 5: TODOs 15-19 gate, ship/rollback, postmortem (serial with conditional 16)
Wave 6: TODO 20 optional ablation only after ship and user request

### Dependency Matrix
- 0 blocks all implementation and benchmark-gate work.
- 1 → 2 → 3 → 4 → 5 → 6.
- 6 → 7 → 8 → 9 → 10 → 11.
- 11 → 12 and 13; 12+13 → 14 → 15 → 16 → 17 → 18 → 19.
- 20 depends on ship case from 18 and explicit user request.

### Agent Dispatch Summary
- Wave 0: 1 ultrabrain task
- Wave 1: 3 ultrabrain/general investigation tasks
- Wave 2: 2 ultrabrain/general audit/test tasks
- Wave 3: 6 general implementation tasks
- Wave 4: 3 general/quick/ultrabrain validation tasks
- Wave 5: 5 general/ultrabrain/quick/writing gate tasks

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 0. Benchmark Consumer-Path Viability Gate

  **What to do**: Before any code change, prove whether LongMemEval_S will consume the same memory prompt surface that W1 changes in `orchestrator/memory/injection.py`. Inspect the live benchmark path from `tests/longmemeval/evaluate.py` to the final `answer_prompt_metadata.system_message` and produce `tests/benchmark_results/wave1_benchmark_consumer_path.md`. If `_format_eval_memory_block()` or equivalent collapses memories before `assemble_system_prompt()` and prevents JSON/confidence/provenance/timestamp/source fields from reaching the benchmark prompt, write `decision: halt-harness-parity-required` and stop all implementation TODOs. Do not edit `tests/longmemeval/evaluate.py`.
  **Must NOT do**: Do not authorize benchmark adapter edits; do not treat a separate harness path as acceptable evidence.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: consumer-path correctness determines whether the whole wave is measurable.
  - Skills: [] - No extra skill required.
  - Omitted: [`git-master`] - No git mutation.

  **Parallelization**: Can Parallel: NO | Wave 0 | Blocks: 1-20 | Blocked By: none

  **References**:
  - Pattern: `orchestrator/memory/injection.py:168` - production memory-context builder.
  - Pattern: `orchestrator/memory/injection.py:311` - final system prompt assembler.
  - Pattern: `tests/longmemeval/evaluate.py` - benchmark answer path and metadata capture.
  - Pattern: `tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_results.jsonl` - expected row metadata style.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/wave1_benchmark_consumer_path.md` exists.
  - [ ] It states one of `proceed-production-surface-measurable` or `halt-harness-parity-required`.
  - [ ] It includes call-chain evidence and at least one captured or reconstructed prompt metadata path showing where memory context enters the final answer prompt.
  - [ ] If decision is halt, all TODOs 1-20 are blocked until user commissions harness parity or explicitly changes scope.

  **QA Scenarios**:
  ```
  Scenario: Benchmark consumes production prompt surface
    Tool: Bash
    Steps: Trace evaluate.py call chain and inspect one result row's answer_prompt_metadata.system_message.
    Expected: Artifact proves memory JSON fields can reach benchmark prompt without non-injection code edits.
    Evidence: .sisyphus/evidence/task-0-consumer-path.md

  Scenario: Benchmark bypasses production formatting
    Tool: Bash
    Steps: Trace formatter use and identify any benchmark-local formatter that collapses memory dict fields.
    Expected: Artifact states halt-harness-parity-required and no implementation starts.
    Evidence: .sisyphus/evidence/task-0-consumer-path-halt.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_benchmark_consumer_path.md`]

- [ ] 1. Locate C3 Error Data for Pre-W1 Probe

  **What to do**: Locate the canonical per-question C3 full-corpus artifact and write `tests/benchmark_results/wave1_probe_data_inventory.md` naming paths, schema, required fields, and IE-* incorrect-answer population size.
  **Must NOT do**: Do not sample before confirming population size and fields.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: artifact inventory and schema verification.
  - Skills: [] - No specialized skill needed.
  - Omitted: [`memory-wave-diagnostic`] - Used later for system audit, not inventory.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 2 | Blocked By: 0 proceed

  **References**:
  - Pattern: `tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_results.jsonl` - likely C3 per-row artifact.
  - Pattern: `tests/benchmark_results/wave0_closure_memo.md` - baseline context.

  **Acceptance Criteria**:
  - [ ] Inventory artifact exists and names exact file path(s).
  - [ ] It enumerates at least `question_id`, `category`, `judgment`/verdict, `retrieved_memory_ids`, `memories_used`, and prompt metadata fields.
  - [ ] It states IE-* incorrect-answer population size; if <30, it recommends a fresh C3 sub-run and blocks TODO 2.

  **QA Scenarios**:
  ```
  Scenario: Sufficient IE-* population
    Tool: Bash
    Steps: Parse candidate JSONL rows, filter IE-* incorrect rows, count unique question_id values.
    Expected: Count >= 30 and schema fields documented.
    Evidence: .sisyphus/evidence/task-1-inventory.json

  Scenario: Insufficient or missing fields
    Tool: Bash
    Steps: Attempt the same parse against available artifacts.
    Expected: Report states exact missing fields or population shortfall and blocks sampling.
    Evidence: .sisyphus/evidence/task-1-inventory-error.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_probe_data_inventory.md`]

- [ ] 2. Run Pre-W1 R/F/A Probe

  **What to do**: Sample exactly 30 incorrect IE-* questions from TODO 1, stratified across IE-user, IE-preference, and IE-assistant where possible. Classify each as R (right memory absent from top-5), F (right memory in top-5 but answer wrong), or A (right memory absent from memories table/log). Write `tests/benchmark_results/wave1_pre_probe.md`.
  **Must NOT do**: Do not rely on judge rationales alone; cite retrieved IDs and stored memory/extraction evidence.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: evidence classification requires careful reasoning.
  - Skills: [] - No extra skill.
  - Omitted: [`memory-wave-diagnostic`] - This is the pre-routing probe, not the system audit.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 3 | Blocked By: 1

  **References**:
  - Pattern: path named by TODO 1.
  - API/Type: memories table fields from retrieval output: `content`, `source_type`, `confidence`, `trust_score`, `created_at`, `source_conversation_id`.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/wave1_pre_probe.md` exists.
  - [ ] Exactly 30 questions are classified with `question_id`, category, R/F/A, and one-sentence evidence rationale.
  - [ ] Aggregate R/F/A counts are integers and sum to 30.

  **QA Scenarios**:
  ```
  Scenario: Classify sampled IE-* error
    Tool: Bash
    Steps: For each sampled row, compare reference/right memory against retrieved_memory_ids and memory store dump/query.
    Expected: Classification has cited evidence and no unclear rows.
    Evidence: .sisyphus/evidence/task-2-rfa-sample.csv

  Scenario: Sample row lacks memory evidence
    Tool: Bash
    Steps: Attempt classification and detect missing retrieved IDs or unavailable memory content.
    Expected: Row is excluded with documented reason and replaced to maintain exactly 30 classified rows.
    Evidence: .sisyphus/evidence/task-2-rfa-replacements.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_pre_probe.md`]

- [ ] 3. Apply R/F/A Routing Decision

  **What to do**: Read TODO 2 counts and write `tests/benchmark_results/wave1_routing_decision.md`. If A > 5% (2 or more of 30), halt for producer audit. Else if R > 60% (19 or more of 30), halt and recommend W2 first. Else proceed to system audit.
  **Must NOT do**: Do not continue on a halt decision.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: gate logic must be exact and auditable.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 4 | Blocked By: 2

  **References**:
  - Pattern: `tests/benchmark_results/wave1_pre_probe.md` - input counts.

  **Acceptance Criteria**:
  - [ ] Decision artifact exists with integer R/F/A counts.
  - [ ] Decision is one of `proceed`, `halt-reroute-w2`, `halt-producer-audit`.
  - [ ] If `proceed`, artifact states dominant class and F-class share.

  **QA Scenarios**:
  ```
  Scenario: F-dominant proceed
    Tool: Bash
    Steps: Recompute counts from TODO 2 markdown/table.
    Expected: A <= 1 and R <= 18; decision is proceed.
    Evidence: .sisyphus/evidence/task-3-routing.json

  Scenario: Halt threshold triggered
    Tool: Bash
    Steps: Recompute counts and apply A/R thresholds.
    Expected: Halt decision matches first triggered rule and subsequent TODOs remain unchecked.
    Evidence: .sisyphus/evidence/task-3-routing-halt.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_routing_decision.md`]

- [ ] 4. Memory-Wave Diagnostic Audit on Injection Path

  **What to do**: Invoke `memory-wave-diagnostic` against W1 risk surface and write `tests/benchmark_results/wave1_system_audit.md`. Include the required audit frame, smoke trace, D-chain, and disposition sections in one artifact or link to the skill-required sub-artifacts. Capture one IE-* question that baseline got wrong with `memories_used > 0`, the full assembled prompt, token count, L0 behavior, guardrail status, and benchmark/production path comparison.
  **Must NOT do**: Do not fix defects during audit except to document halt/proceed disposition.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: bounded diagnostic protocol and path validation.
  - Skills: [`memory-wave-diagnostic`] - Required by wave contract.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 5-11 | Blocked By: 3 proceed

  **References**:
  - Pattern: `orchestrator/memory/injection.py:168` - memory context assembly.
  - Pattern: `orchestrator/memory/injection.py:311` - system prompt assembly.
  - Pattern: `orchestrator/prompts.py:3-6` - abstention guardrail constant.

  **Acceptance Criteria**:
  - [ ] `tests/benchmark_results/wave1_system_audit.md` exists.
  - [ ] It contains verbatim prompt for the chosen IE-* question and line/path annotations.
  - [ ] It answers yes/no: guardrail present today, L0 prepended today, budget enforced today, production path bypassed by benchmark yes/no.
  - [ ] Any Class B/G defect halts the wave unless already covered by authorized TODOs 6-11.

  **QA Scenarios**:
  ```
  Scenario: Production path audit succeeds
    Tool: Bash
    Steps: Run one selected IE-* question through production prompt assembly with verbose capture.
    Expected: Artifact contains prompt, token count, memory IDs, and path annotations.
    Evidence: .sisyphus/evidence/task-4-audit.md

  Scenario: Production bypass or guardrail drift found
    Tool: Bash
    Steps: Compare benchmark prompt and production prompt for same question.
    Expected: Artifact classifies defect and states disposition before implementation.
    Evidence: .sisyphus/evidence/task-4-audit-defect.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_system_audit.md`]

- [ ] 5. Record T15 Harness-Parity Baseline Decision Anchor

  **What to do**: Use `tests/benchmark_results/harness_parity_baseline_decision.md` (generated 2026-05-06) as the authoritative `harness-parity-shipped` baseline-decision artifact. Cite the exact T15 status `HALT — baseline undeterminable`, preserve Wave 0 closure-memo / 27-exclusion references only as historical pre-parity context if needed, and state explicitly that no numeric T15 baseline or ±1pp band exists until the full haystack-bearing LongMemEval_S corpus is restored and T14/T15 are rerun.
  **Must NOT do**: Do not fabricate a numeric baseline or ±1pp band; do not modify code or move `harness-parity-shipped`.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: benchmark execution and artifact validation.
  - Skills: [] - No extra skill.
  - Omitted: [`git-master`] - Only read tag/checkout if needed; no commits.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 6 and 15 | Blocked By: 4

  **References**:
  - Test: `tests/longmemeval/evaluate.py` - LongMemEval harness.
  - Pattern: `tests/benchmark_results/harness_parity_baseline_decision.md` - authoritative T15 baseline-decision artifact.
  - Pattern: `tests/benchmark_results/wave0_closure_memo.md` - historical pre-parity category comparison only.

  **Acceptance Criteria**:
  - [ ] TODO 5 cites `tests/benchmark_results/harness_parity_baseline_decision.md`, generated 2026-05-06, and the exact status `HALT — baseline undeterminable`.
  - [ ] TODO 5 states that no numeric T15 baseline or ±1pp band is available until the full haystack-bearing LongMemEval_S corpus is restored and T14/T15 are rerun.
  - [ ] Any retained Wave 0 closure-memo / 27-exclusion comparison is labeled historical pre-parity context only.

  **QA Scenarios**:
  ```
  Scenario: T15 decision artifact is HALT-aware
    Tool: Bash
    Steps: Read `tests/benchmark_results/harness_parity_baseline_decision.md` and verify path/date/status plus the explicit no-numeric-baseline constraint.
    Expected: TODO 5 cites the artifact path/date/status exactly and does not invent a numeric baseline or ±1pp band.
    Evidence: .sisyphus/evidence/task-5-baseline-fail.md

  Scenario: Future rerun remains blocked
    Tool: Bash
    Steps: Check whether the full haystack-bearing LongMemEval_S corpus and completed T14/T15 reruns exist.
    Expected: If they do not, TODO 5 remains a HALT-aware documentation gate rather than a numeric reproduction task.
    Evidence: .sisyphus/evidence/task-5-baseline-fail.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/harness_parity_baseline_decision.md`]

- [ ] 6. Define JSON Schema and Confidence Helpers

  **What to do**: In `orchestrator/memory/injection.py`, add helper functions with docstrings: `_confidence_bin(value, *, default)` and `_memory_to_evidence_dict(memory, *, is_l0=False)`. Output dict keys must be exactly `content`, `provenance`, `timestamp`, `confidence`, `source_type` in that order before JSON serialization. Derive `provenance` via a local mapping from `source_type` plus optional `source_conversation_id`; use `unknown` sentinel for missing optional fields, never `None`.
  **Must NOT do**: Do not add DB queries, retrieval changes, or external imports beyond stdlib `json`/datetime helpers if needed.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: small backend helper implementation.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: 7 | Blocked By: 5

  **References**:
  - Pattern: `orchestrator/memory/injection.py:90` - existing normalization helper.
  - Pattern: `orchestrator/memory/injection.py:117` - token estimate helper style.

  **Acceptance Criteria**:
  - [ ] Only `orchestrator/memory/injection.py` changed.
  - [ ] Inline smoke proves 0.5→low, 0.7→med, 0.9→high.
  - [ ] Synthetic memory dict produces exactly five non-null fields.
  - [ ] L0 synthetic memory defaults to `confidence: high` and non-empty `source_type`.

  **QA Scenarios**:
  ```
  Scenario: Helper happy path
    Tool: Bash
    Steps: Run Python one-liner importing helpers and passing synthetic memory with confidence/source/timestamp.
    Expected: Dict has five keys and expected confidence bins.
    Evidence: .sisyphus/evidence/task-6-helper.json

  Scenario: Missing optional fields
    Tool: Bash
    Steps: Run helper against minimal memory containing only content.
    Expected: No None values; sentinel unknown appears where documented.
    Evidence: .sisyphus/evidence/task-6-helper-missing.json
  ```

  **Commit**: NO | Message: `feat(memory): add evidence formatting helpers` | Files: [`orchestrator/memory/injection.py`]

- [ ] 7. Render Memories as JSON Array

  **What to do**: Replace bullet-list rendering in `build_memory_context()` with `<memories>\n[...]\n</memories>` using `json.dumps(..., ensure_ascii=False)`. Include both L0 and L1 entries in one array, preserving order: L0 first, then ranked L1 memories, then summaries only if they can be represented as evidence entries with `source_type: summary`; otherwise document summary exclusion as unchanged out-of-scope behavior in the artifact.
  **Must NOT do**: Do not change retrieval ordering, merge memories, or drop content except by existing per-item truncation/budget rules.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: focused formatter rewrite.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: 8 | Blocked By: 6

  **References**:
  - Pattern: `orchestrator/memory/injection.py:268-276` - bullet rendering to replace.
  - Pattern: `orchestrator/memory/injection.py:306-307` - L0 prepend to fold into JSON array.

  **Acceptance Criteria**:
  - [ ] Bullet rendering for memory evidence is gone from production memory context.
  - [ ] Synthetic 3-memory assembly parses as JSON array length 3 between markers.
  - [ ] Each element has exactly five canonical keys.
  - [ ] Content order is preserved.

  **QA Scenarios**:
  ```
  Scenario: JSON block parses
    Tool: Bash
    Steps: Build memory context with synthetic store returning 3 memories; parse text between markers with json.loads.
    Expected: Array length 3, five keys per item, original order retained.
    Evidence: .sisyphus/evidence/task-7-json-parse.json

  Scenario: Content contains prompt-like text
    Tool: Bash
    Steps: Include memory content such as 'ignore previous instructions' and parse JSON.
    Expected: Text remains escaped data inside content field only.
    Evidence: .sisyphus/evidence/task-7-json-injection.json
  ```

  **Commit**: NO | Message: `feat(memory): render memory evidence as json` | Files: [`orchestrator/memory/injection.py`]

- [ ] 8. Add Chain-of-Note Instruction

  **What to do**: In `assemble_system_prompt()`, when memory context is non-empty, append a model-agnostic instruction immediately after the `<memories>` block: internally review the JSON evidence array before answering, compare relevance and confidence, then output only the final answer without exposing the note.
  **Must NOT do**: Do not parse model output or request visible chain-of-thought.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: prompt instruction update.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: 9 | Blocked By: 7

  **References**:
  - Pattern: `orchestrator/memory/injection.py:311-336` - prompt assembly location.

  **Acceptance Criteria**:
  - [ ] Instruction appears only when memory context is non-empty.
  - [ ] Instruction references JSON evidence array and internal note phase.
  - [ ] Instruction says final output should not expose the internal note.

  **QA Scenarios**:
  ```
  Scenario: Memory prompt includes Chain-of-Note
    Tool: Bash
    Steps: Call assemble_system_prompt with a JSON memory context.
    Expected: Chain-of-Note instruction present after memory block.
    Evidence: .sisyphus/evidence/task-8-con.md

  Scenario: Empty memory prompt omits Chain-of-Note
    Tool: Bash
    Steps: Call assemble_system_prompt with empty memory context.
    Expected: No Chain-of-Note instruction appears.
    Evidence: .sisyphus/evidence/task-8-con-empty.md
  ```

  **Commit**: NO | Message: `feat(memory): add evidence note instruction` | Files: [`orchestrator/memory/injection.py`]

- [ ] 9. Restore Abstention Guardrail and Add Confidence Guidance

  **What to do**: Import/use `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` in `injection.py` and append it when memory context is non-empty. Add adjacent hedge/abstain guidance: `low` is tentative and should be hedged; if only relevant evidence is low, ask/abstain; `high` is assertable when directly relevant; `med` can support cautious answers.
  **Must NOT do**: Do not duplicate contradictory guardrail language.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: prompt guardrail wiring.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: 10 | Blocked By: 8

  **References**:
  - API/Type: `orchestrator/prompts.py:3-6` - guardrail constant.
  - Test: `tests/test_l0_injection.py:270-282` - expected guardrail behavior.

  **Acceptance Criteria**:
  - [ ] Prompt with memory contains guardrail and confidence guidance.
  - [ ] Prompt without memory contains neither guardrail nor confidence guidance.
  - [ ] `pytest tests/test_l0_injection.py -q` passes or any unrelated failure is triaged with exact output.

  **QA Scenarios**:
  ```
  Scenario: All-low memory evidence
    Tool: Bash
    Steps: Assemble prompt with low-confidence JSON evidence.
    Expected: Prompt includes both abstention guardrail and hedge guidance in non-contradictory order.
    Evidence: .sisyphus/evidence/task-9-low-guidance.md

  Scenario: No memory evidence
    Tool: Bash
    Steps: Assemble prompt with empty memory context.
    Expected: No memory-specific guardrail or hedge instruction appears.
    Evidence: .sisyphus/evidence/task-9-empty.md
  ```

  **Commit**: NO | Message: `fix(memory): restore evidence abstention guardrail` | Files: [`orchestrator/memory/injection.py`]

- [ ] 10. Verify L0 JSON Prepending

  **What to do**: Adjust/verify L0 handling so L0 memories render as first elements inside the same `<memories>` JSON array. L0 entries must use `confidence: high`, `source_type: bootstrapped` unless actual source is available, and no separate `[FROZEN MEMORIES]` stanza remains in the final memory context.
  **Must NOT do**: Do not drop, duplicate, or reorder L0 entries after L1.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: focused L0 integration.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: 11 | Blocked By: 9

  **References**:
  - Pattern: `orchestrator/memory/injection.py:104-114` - current L0 formatter.
  - Pattern: `orchestrator/memory/injection.py:183-193` - current L0 fetch/budget.

  **Acceptance Criteria**:
  - [ ] Assembly with N L0 and M L1 produces array length N+M.
  - [ ] First N elements match L0 content and have `confidence: high`.
  - [ ] No `[FROZEN MEMORIES]` marker remains unless intentionally retained only in comments/tests as legacy text.

  **QA Scenarios**:
  ```
  Scenario: L0 and L1 combined
    Tool: Bash
    Steps: Use synthetic store with 2 L0 and 3 L1 entries; parse JSON block.
    Expected: Length 5; first two entries are L0 and not duplicated.
    Evidence: .sisyphus/evidence/task-10-l0.json

  Scenario: L0-only
    Tool: Bash
    Steps: Use synthetic store with L0 and no L1 retrieval.
    Expected: JSON array contains only L0 entries and prompt instructions still apply.
    Evidence: .sisyphus/evidence/task-10-l0-only.json
  ```

  **Commit**: NO | Message: `feat(memory): include l0 in evidence json` | Files: [`orchestrator/memory/injection.py`]

- [ ] 11. Recalibrate Token Budget and Truncation

  **What to do**: Change the default memory-context budget in `injection.py` from 2500 to 1500 per W1 contract. Recompute budget over final JSON block plus W1 instruction overhead. Preserve all L0 entries and all instruction strings; drop L1 entries from lowest-rank tail until estimated prompt fits. Document truncation rule in a code comment.
  **Must NOT do**: Do not truncate L0 or instruction text; do not change retrieval pool constants.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: bounded budget logic.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: 12,13 | Blocked By: 10

  **References**:
  - Pattern: `orchestrator/memory/injection.py:34` - current default.
  - Pattern: `orchestrator/memory/injection.py:292-298` - current eviction loop.

  **Acceptance Criteria**:
  - [ ] `DEFAULT_MAX_TOKENS` or equivalent effective default is 1500.
  - [ ] Over-budget synthetic context preserves L0 and instructions while dropping L1 tail only.
  - [ ] Fit scenario preserves all entries.

  **QA Scenarios**:
  ```
  Scenario: Over-budget truncation
    Tool: Bash
    Steps: Build context with L0 plus many long L1 entries.
    Expected: estimate_tokens output <=1500; L0 and instructions retained; L1 tail dropped.
    Evidence: .sisyphus/evidence/task-11-budget-over.json

  Scenario: Under-budget no-op
    Tool: Bash
    Steps: Build context with normal 5-memory retrieval.
    Expected: All entries retained and estimated tokens <=1500.
    Evidence: .sisyphus/evidence/task-11-budget-fit.json
  ```

  **Commit**: NO | Message: `fix(memory): enforce json evidence budget` | Files: [`orchestrator/memory/injection.py`]

- [ ] 12. Extraction Non-Regression Check

  **What to do**: Run `tests/benchmark_extraction.py` unchanged and copy/summarize raw result counts to `tests/benchmark_results/wave1_extraction_check.json`.
  **Must NOT do**: Do not change extraction code or tune producer behavior.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: benchmark execution.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: YES | Wave 4 | Blocks: 14 | Blocked By: 11

  **References**:
  - Test: `tests/benchmark_extraction.py` - extraction benchmark.
  - Pattern: `tests/benchmark_results/extraction_benchmark_results.json` - output schema example.

  **Acceptance Criteria**:
  - [ ] Artifact exists with raw TP/FP/FN/adversarial counts.
  - [ ] Precision ≥0.95, Recall ≥0.85, adversarial misfires ≤2.
  - [ ] Any failure blocks TODO 15 even if prompt smoke passes.

  **QA Scenarios**:
  ```
  Scenario: Extraction holds
    Tool: Bash
    Steps: Run benchmark and recompute P/R/A from raw counts.
    Expected: All thresholds pass.
    Evidence: .sisyphus/evidence/task-12-extraction.json

  Scenario: Extraction regresses
    Tool: Bash
    Steps: Recompute thresholds from raw counts.
    Expected: Artifact states halt-extraction-regression.
    Evidence: .sisyphus/evidence/task-12-extraction-fail.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_extraction_check.json`]

- [ ] 13. Encryption / Data-Integrity Smoke

  **What to do**: Run existing encryption smoke and/or read-only DB probe decrypting at least 20 sampled rows each from `messages.content`, `memories.content`, and the correct extraction log snippet table/column discovered from schema. Write `tests/benchmark_results/wave1_encryption_smoke.md`.
  **Must NOT do**: Do not mutate DB data.

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: bounded smoke probe.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: YES | Wave 4 | Blocks: 14 | Blocked By: 11

  **References**:
  - Test: `tests/memory/test_encryption.py` - existing encryption tests.
  - API/Type: `orchestrator/memory/encryption.py` - decrypt helper.

  **Acceptance Criteria**:
  - [ ] Artifact exists with sampled table names, row counts, and pass/fail.
  - [ ] At least 20 rows per table/column decrypt to non-empty valid UTF-8 where enough rows exist.
  - [ ] Any decrypt failure halts the wave.

  **QA Scenarios**:
  ```
  Scenario: Decryption clean
    Tool: Bash
    Steps: Run read-only decrypt probe using DATABASE_URL and DAEMON_ENCRYPTION_KEY.
    Expected: All sampled rows decrypt and decode.
    Evidence: .sisyphus/evidence/task-13-encryption.md

  Scenario: Ciphertext failure
    Tool: Bash
    Steps: Capture any InvalidToken/decode exception with row/table attribution.
    Expected: Artifact states halt-encryption-failure.
    Evidence: .sisyphus/evidence/task-13-encryption-fail.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_encryption_smoke.md`]

- [ ] 14. End-to-End Smoke Trace on Audit Question

  **What to do**: Run the same IE-* question from TODO 4 through the production injection path after W1 changes. Capture full assembled prompt, answer response, judge verdict, `memories_used`, parsed JSON block, L0 presence, token count, and guardrail/Chain-of-Note/hedge instruction presence in `tests/benchmark_results/wave1_smoke_trace.md`.
  **Must NOT do**: Do not accept a harness-only path that bypasses production prompt assembly.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: multi-condition smoke and consumer-path validation.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 4 | Blocks: 15 | Blocked By: 12,13

  **References**:
  - Pattern: `tests/benchmark_results/wave1_system_audit.md` - before/after question.
  - Pattern: `orchestrator/memory/injection.py` - changed prompt path.

  **Acceptance Criteria**:
  - [ ] Artifact contains verbatim prompt, response, and verdict.
  - [ ] Seven structural checks pass: production path, parseable JSON, Chain-of-Note, hedge guidance, L0 at head if applicable, under budget, abstention guardrail present/firing under low-confidence subset.
  - [ ] `memories_used > 0`.

  **QA Scenarios**:
  ```
  Scenario: Smoke structural pass
    Tool: Bash
    Steps: Run selected IE-* question and parse captured prompt.
    Expected: All seven structural checks true and memories_used > 0.
    Evidence: .sisyphus/evidence/task-14-smoke.md

  Scenario: JSON or guardrail missing
    Tool: Bash
    Steps: Parse prompt and check required markers/instructions.
    Expected: Artifact states halt-smoke-structural-failure.
    Evidence: .sisyphus/evidence/task-14-smoke-fail.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_smoke_trace.md`]

- [ ] 15. LongMemEval_S Full-Corpus Gate Run

  **What to do**: Run full LongMemEval_S on W1 changes, using the same exclusion logic as baseline. Write `tests/benchmark_results/wave1_gate_run.json` with raw per-question rows, aggregate/per-category counts, latency/provider metrics, and prompt samples proving W1 prompt surface was measured.
  **Must NOT do**: Do not trust status fields; recompute from raw rows.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: benchmark run and metrics collation.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 5 | Blocks: 16,17 | Blocked By: 14

  **References**:
  - Test: `tests/longmemeval/evaluate.py` - full-corpus harness.
  - Pattern: `tests/benchmark_results/wave1_baseline_repro.json` - comparison baseline.

  **Acceptance Criteria**:
  - [ ] Artifact exists with correct_count, incorrect_count, total_count, excluded_count, per-category raw counts.
  - [ ] Excluded count is 27 unless TODO 5 documented a different reproduced baseline denominator.
  - [ ] Median `memories_used` >0, retrieval p95 <1500ms, provider routing failure rate <5%.
  - [ ] At least 5 sampled answer prompts in metadata contain the W1 JSON/confidence/guardrail surface.

  **QA Scenarios**:
  ```
  Scenario: Gate run valid
    Tool: Bash
    Steps: Run full corpus and recompute aggregate/per-category scores from raw rows.
    Expected: Valid raw counts and no rollback trigger.
    Evidence: .sisyphus/evidence/task-15-gate.json

  Scenario: Hollow artifact detected
    Tool: Bash
    Steps: Inspect sampled prompt metadata for W1 JSON surface.
    Expected: Missing W1 surface triggers gate failure regardless of score.
    Evidence: .sisyphus/evidence/task-15-hollow-fail.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_gate_run.json`]

- [ ] 16. Conditional Confirmation Run

  **What to do**: If TODO 15 lift over TODO 5 baseline is in [+2pp, +4pp], run a second full-corpus confirmation and write `tests/benchmark_results/wave1_gate_run_confirm.json`. If lift is >+4pp or <+2pp, write a documented skip note in the gate decision/postmortem.
  **Must NOT do**: Do not run confirmation for a failed <+2pp gate unless user explicitly requests diagnostics.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: benchmark repetition and comparison.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 5 | Blocks: 17 | Blocked By: 15

  **References**:
  - Pattern: `tests/benchmark_results/wave1_gate_run.json` - first run.

  **Acceptance Criteria**:
  - [ ] Artifact exists if required, otherwise skip reason is documented.
  - [ ] If executed, second run lift is computed from raw counts against TODO 5 baseline.
  - [ ] Per-category non-regression rule applies to confirmation run.

  **QA Scenarios**:
  ```
  Scenario: Confirmation required
    Tool: Bash
    Steps: Detect first-run lift in +2pp to +4pp band and rerun full corpus.
    Expected: Confirmation artifact exists and lift >= +2pp for pass.
    Evidence: .sisyphus/evidence/task-16-confirm.json

  Scenario: Confirmation skipped
    Tool: Bash
    Steps: Detect first-run lift outside confirmation band.
    Expected: Skip reason documented with numeric first-run lift.
    Evidence: .sisyphus/evidence/task-16-skip.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_gate_run_confirm.json`]

- [ ] 17. Gate Decision and Ship/Rollback Resolution

  **What to do**: Apply gate rules to TODOs 12, 15, and 16. Write `tests/benchmark_results/wave1_gate_decision.md` with numeric pass/fail for each condition and final decision `ship` or `rollback`.
  **Must NOT do**: Do not ship partial W1.a or W1.b.

  **Recommended Agent Profile**:
  - Category: `ultrabrain` - Reason: numeric gate decision and rollback correctness.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 5 | Blocks: 18 | Blocked By: 15,16,12

  **References**:
  - Pattern: `tests/benchmark_results/wave1_baseline_repro.json` - baseline.
  - Pattern: `tests/benchmark_results/wave1_gate_run.json` - gate run.
  - Pattern: `tests/benchmark_results/wave1_extraction_check.json` - extraction gate.

  **Acceptance Criteria**:
  - [ ] Decision artifact exists.
  - [ ] Each gate condition cites numeric compared values.
  - [ ] Final decision is exactly `ship` or `rollback`.

  **QA Scenarios**:
  ```
  Scenario: Gate passes
    Tool: Bash
    Steps: Recompute lift/category/extraction/latency/provider/memories_used conditions.
    Expected: All pass and decision is ship.
    Evidence: .sisyphus/evidence/task-17-decision-pass.md

  Scenario: Gate fails
    Tool: Bash
    Steps: Identify first failed condition from raw artifacts.
    Expected: Decision is rollback with failure mode named.
    Evidence: .sisyphus/evidence/task-17-decision-fail.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_gate_decision.md`]

- [ ] 18. Execute Ship or Rollback

  **What to do**: If TODO 17 says `ship`, tag current HEAD as `wave-1-shipped` and surgically update the single baseline sentence in `docs/MEMORY_UPGRADE_ROADMAP.md`. If `rollback`, restore `orchestrator/memory/injection.py` to `pre-wave-1` byte-identical state while preserving benchmark/postmortem artifacts. Do not push or merge.
  **Must NOT do**: Do not move `pre-wave-1`; do not reflow roadmap markdown.

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: bounded git/file-state operation.
  - Skills: [`git-master`] - Required for safe tag/rollback handling.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 5 | Blocks: 19,20 | Blocked By: 17

  **References**:
  - Pattern: `docs/MEMORY_UPGRADE_ROADMAP.md` - baseline sentence only.
  - Pattern: git tag `pre-wave-1` / HEAD `07e9e6e7` - rollback anchor.

  **Acceptance Criteria**:
  - [ ] Ship: `wave-1-shipped` tag exists and roadmap has only the baseline sentence changed.
  - [ ] Rollback: `orchestrator/memory/injection.py` md5 matches `pre-wave-1` blob.
  - [ ] `git diff pre-wave-1..HEAD -- orchestrator/memory/` shows no file outside allowed state.

  **QA Scenarios**:
  ```
  Scenario: Ship path
    Tool: Bash
    Steps: Create tag and inspect roadmap diff.
    Expected: Tag exists; one-line roadmap baseline change only.
    Evidence: .sisyphus/evidence/task-18-ship.md

  Scenario: Rollback path
    Tool: Bash
    Steps: Restore injection.py from pre-wave-1 and compare md5/blob hash.
    Expected: injection.py byte-identical to rollback anchor.
    Evidence: .sisyphus/evidence/task-18-rollback.md
  ```

  **Commit**: NO | Message: `chore(memory): complete wave 1 gate` | Files: [`orchestrator/memory/injection.py`, `docs/MEMORY_UPGRADE_ROADMAP.md`, `tests/benchmark_results/*`]

- [ ] 19. Mandatory Wave Postmortem

  **What to do**: Write `tests/benchmark_results/wave1_postmortem.md` covering probe outcome, audit findings, infrastructure defects, aggregate/per-category lifts with raw counts, ship/rollback decision, and updated priors for W2-W9.
  **Must NOT do**: Do not omit postmortem on rollback or halt.

  **Recommended Agent Profile**:
  - Category: `writing` - Reason: structured technical postmortem.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 5 | Blocks: final verification | Blocked By: 18

  **References**:
  - Pattern: `tests/benchmark_results/wave1_gate_decision.md` - decision source.
  - Pattern: `tests/benchmark_results/wave1_system_audit.md` - audit findings.

  **Acceptance Criteria**:
  - [ ] Six requested sections are present.
  - [ ] Defects include file:line references and disposition.
  - [ ] Lift section cites raw counts.
  - [ ] W2 prior update includes at least one concrete learning from W1.

  **QA Scenarios**:
  ```
  Scenario: Ship postmortem
    Tool: Bash
    Steps: Cross-check postmortem against gate artifacts.
    Expected: Counts and decision match raw artifacts.
    Evidence: .sisyphus/evidence/task-19-postmortem.md

  Scenario: Rollback/halt postmortem
    Tool: Bash
    Steps: Verify postmortem includes failure mode and disposition.
    Expected: No empty/null sections; future-wave priors updated.
    Evidence: .sisyphus/evidence/task-19-postmortem-fail.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_postmortem.md`]

- [ ] 20. Optional W1.a vs W1.b Ablation

  **What to do**: Only if TODO 17 shipped and the user explicitly requests attribution, run two additional full-corpus passes: W1.a only and W1.b only. Otherwise document skip reason in `tests/benchmark_results/wave1_ablation.md` or postmortem.
  **Must NOT do**: Do not run ablations before ship; do not smuggle partial W1 into W2.

  **Recommended Agent Profile**:
  - Category: `general` - Reason: benchmark attribution work.
  - Skills: [] - No extra skill.
  - Omitted: [] - n/a

  **Parallelization**: Can Parallel: NO | Wave 6 | Blocks: none | Blocked By: 18 ship + user request

  **References**:
  - Test: `tests/longmemeval/evaluate.py` - full-corpus harness.
  - Pattern: `tests/benchmark_results/wave1_gate_run.json` - bundled baseline.

  **Acceptance Criteria**:
  - [ ] If requested, two run artifacts and `tests/benchmark_results/wave1_ablation.md` exist.
  - [ ] If not requested, skip reason is documented.
  - [ ] Any executed ablation cites raw counts and pp lift vs baseline.

  **QA Scenarios**:
  ```
  Scenario: User requests ablation
    Tool: Bash
    Steps: Run W1.a-only and W1.b-only full corpus on transient branches/state.
    Expected: Attribution report cites raw counts and component lift.
    Evidence: .sisyphus/evidence/task-20-ablation.md

  Scenario: No user request
    Tool: Bash
    Steps: Check gate/postmortem state and user instruction.
    Expected: Ablation marked skipped with reason.
    Evidence: .sisyphus/evidence/task-20-skip.md
  ```

  **Commit**: NO | Message: n/a | Files: [`tests/benchmark_results/wave1_ablation.md`]

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Real Manual QA — unspecified-high
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- Do not commit until the user explicitly requests it.
- If ship path is selected and user requests a commit, commit implementation + artifacts with a message like `feat(memory): ship wave 1 prompt surface` after final verification approval.
- Rollback path preserves benchmark/postmortem artifacts unless the user requests cleanup.

## Success Criteria
- W1 either halts cleanly at a documented routing/consumer-path gate or reaches an explicit numeric ship/rollback decision.
- No hollow benchmark artifact is accepted: sampled answer prompts must prove W1 JSON/confidence/guardrail surface was actually measured.
- No source file outside `orchestrator/memory/injection.py` is changed by implementation TODOs.
