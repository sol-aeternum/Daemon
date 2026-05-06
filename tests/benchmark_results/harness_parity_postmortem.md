# Harness Parity Postmortem

- **Task**: T22 — Write harness parity postmortem
- **Generated**: 2026-05-06
- **Authoritative shipped anchor**: `harness-parity-shipped` → `d5d7bce81ed3cdee2ccf5485c83d7890382129d2` (`.sisyphus/evidence/task-21-tag-head.txt`)
- **Scope note**: This document synthesizes T1-T21 artifacts only. It does not introduce new benchmark counts, code changes, or roadmap decisions.

## (a) Inventory and classifications

T1/T2 establish that the current harness gap was a **Wave 0 Path A miss**, not a later drift. The required T1 findings are listed below by name, with their working classification in this postmortem.

| T1 finding name | Working classification | T2 status | Primary evidence |
|---|---|---|---|
| formatter deletion gate | scope-wall gate | `path_a_miss` context retained into T6 | `tests/benchmark_results/harness_parity_strip_diff.md:31-129` |
| system prompt builder deletion gate | scope-wall gate | `path_a_miss` context retained into T6 | `tests/benchmark_results/harness_parity_strip_diff.md:33-41, 74-79, 121-123` |
| evaluate_single deletion gate | scope-wall gate | `path_a_miss` context retained into T6 | `tests/benchmark_results/harness_parity_strip_diff.md:42, 80-91` |
| TEST_USER_ID shared-user gate | scope/scoping defect | `path_a_miss` context retained into T6/T7 | `.sisyphus/notepads/longmemeval-harness-production-parity/issues.md:29-33`; `.sisyphus/notepads/longmemeval-harness-production-parity/learnings.md:18-21, 231-235` |
| dual formatter call anomaly | benchmark-local redundancy | `path_a_miss` | `tests/benchmark_results/harness_parity_inventory.md:123-140`; `tests/benchmark_results/harness_parity_path_a_reconstruction.md:34` |
| standalone `run_evaluation()` isolation bypass | legacy contamination vector | `path_a_miss` | `tests/benchmark_results/harness_parity_inventory.md:144-155`; `tests/benchmark_results/harness_parity_path_a_reconstruction.md:41` |
| token budget gap | production-parity defect | `path_a_miss` | `tests/benchmark_results/harness_parity_inventory.md:43, 56, 82, 138, 184-190, 258`; `tests/benchmark_results/harness_parity_path_a_reconstruction.md:36` |
| empty summaries gap | production-surface mismatch | `path_a_miss` | `tests/benchmark_results/harness_parity_inventory.md:54, 80, 136, 191, 259`; `tests/benchmark_results/harness_parity_path_a_reconstruction.md:37` |

The broader T1 inventory also documented related structural gaps that shaped later tasks: `build_memory_context()` was never called on the answer path, L0 rendering was flattened, preferences were omitted, query text came from `question_text` rather than the production conversation surface, and the canonical runner still hashed `_format_eval_memory_block()` as the active formatter contract (`tests/benchmark_results/harness_parity_inventory.md:175-203, 252-260`; `tests/benchmark_results/harness_parity_path_a_reconstruction.md:31-42`).

## (b) Path A reconstruction and root cause

T2's bottom line is that **all T1 findings classify as `path_a_miss` and none qualify as `post_w0_drift`** (`tests/benchmark_results/harness_parity_path_a_reconstruction.md:9-16, 58-64`). The decisive reconstruction is not merely that the benchmark failed to call `build_memory_context()`, but that Path A accepted reuse of `assemble_system_prompt()` as sufficient while leaving the benchmark-local formatter on the active consumer path.

The verbatim T2 sentence that best captures the miss is:

> In short: **Path A missed `_format_eval_memory_block()` because it audited whether the benchmark reused production `assemble_system_prompt()`, not whether the benchmark had stopped using `_format_eval_memory_block()` as the active consumer-path formatter.** (`tests/benchmark_results/harness_parity_path_a_reconstruction.md:23`)

That miss propagated in two concrete ways:

1. `build_assembled_system_prompt()` still fed production `assemble_system_prompt()` with benchmark-local `memory_context` instead of production `build_memory_context()` output (`tests/benchmark_results/harness_parity_inventory.md:64-85`).
2. The canonical benchmark contract still hashed `_format_eval_memory_block()` via `active_memory_formatter_sha256`, proving the measurable harness contract never moved off the benchmark-local formatter (`tests/benchmark_results/harness_parity_path_a_reconstruction.md:31-42`; `tests/benchmark_results/harness_parity_strip_diff.md:37-41, 119-123`).

## (c) Production dependency audit and (b)/(c) counts

T3 answered a narrower but critical question: if the harness were corrected to route through production prompt assembly, would production memory code itself need to change first? The answer was **no**.

Dependency classification counts from `tests/benchmark_results/harness_parity_dependency_audit.md:212-221`:

| Class | Count | Meaning |
|---|---:|---|
| (a) trivially available | 11 | Valid empty/default production state or always-present runtime constants |
| (b) harness pre-population using existing code paths | 13 | User, conversation, message, extracted-memory, optional summary/entity/profile surfaces |
| (c) production change required | 0 | No blocker requiring `orchestrator/memory/**` edits |

The most important production-surface rulings were:

- **Synthetic-user isolation is the production-faithful scope mechanism.** `build_memory_context()` derives scope from `conversation_id -> conversations.user_id` and does not accept `allowed_source_conversation_ids` (`tests/benchmark_results/harness_parity_dependency_audit.md:47-56`).
- **Inline extraction already existed as a valid production path.** The harness could call `process_extraction()` synchronously and still exercise extraction, dedup, memory writes, extraction-log writes, and conversation-summary updates without ARQ/debounce (`tests/benchmark_results/harness_parity_dependency_audit.md:184-191`).
- **No production memory changes were required.** The consumer-path gap was in the harness and benchmark contract, not in `orchestrator/memory/**` (`tests/benchmark_results/harness_parity_dependency_audit.md:57-63, 218-221`).

## (d) Defects beyond _format_eval_memory_block

The table below records the non-formatter defects and adjacent anomalies that remained relevant after T1-T16. Each row includes the operative file:line evidence and the disposition established by the artifacts.

| Defect | File:line evidence | What the defect means | Disposition |
|---|---|---|---|
| standalone `run_evaluation()` isolation bypass | `tests/longmemeval/evaluate.py:838`; `orchestrator/eval/runner.py:423-438` (captured in `.sisyphus/notepads/longmemeval-harness-production-parity/issues.md:62-71`) | The standalone loop calls `evaluate_single()` without `allowed_source_conversation_ids`, enabling unfiltered retrieval across the shared benchmark user. | Recorded as a legacy contamination vector; parity work avoided this path by introducing synthetic-user isolation in the harness-native entry point rather than patching the legacy standalone runner. |
| summary-state parity split | `orchestrator/memory/injection.py:260-264`; `orchestrator/memory/consolidation.py:471-503` (summarized in `tests/benchmark_results/harness_parity_dependency_audit.md:149-156, 193-203`) | Inline extraction updates `conversations.summary`, but production prompt assembly reads summary memories via `get_recent_summaries()` / `memories.category='summary'`. | Empty summary-memory state was ratified as production-valid default; no local summary formatting was added. Non-empty summary parity remains possible only through the existing consolidation path. |
| entity-expansion parity gap | `orchestrator/worker/jobs.py:877-981` (write path summarized in `tests/benchmark_results/harness_parity_dependency_audit.md:157-163, 204-208`) | Inline extraction does not automatically populate entity rows or alias links, so entity-expanded retrieval is a separate surface from baseline memory extraction. | Empty entity state was ratified as production-valid default; entity-aware parity remains an optional future prepopulation step through existing production entity-resolution paths. |
| dual formatter call anomaly | `tests/longmemeval/evaluate.py:651-652` and inner call at `tests/longmemeval/evaluate.py:487` (`tests/benchmark_results/harness_parity_inventory.md:123-140`) | The legacy answer path calls `_format_eval_memory_block()` once through `build_assembled_system_prompt()` and again for checkpoint metadata only. | Left intact in the legacy path because T6-GATE blocked safe deletion in out-of-scope consumers; parity validation bypassed it by using `tests/longmemeval/parity_harness.py` instead. |
| token budget gap | `tests/longmemeval/evaluate.py:416-474` vs `orchestrator/memory/injection.py:292-298` (`tests/benchmark_results/harness_parity_inventory.md:252-259`) | The benchmark-local formatter had no production token-budget trimming loop. | Eliminated from the parity path by routing prompt construction through production `build_memory_context()`; still part of the historical legacy harness explanation. |
| empty summaries gap | `tests/longmemeval/evaluate.py:651` with defaulted summary path at `tests/longmemeval/evaluate.py:487`; production summary read noted at `orchestrator/memory/injection.py:260-264` (`tests/benchmark_results/harness_parity_inventory.md:136, 191, 259`) | The legacy harness always passed `summaries=[]`, so the answer path never exercised production summary-memory reads. | T5 approved empty summaries as the default production-valid parity state; the gap is documented, not papered over with synthetic local formatting. |
| ABS/category mapping inconsistency | `tests/longmemeval/evaluate.py:830-833`; `tests/benchmark_results/harness_parity_category_paths.md:118-129`; `.sisyphus/notepads/longmemeval-harness-production-parity/issues.md:146-155` | ABS handling was internally inconsistent across code and historical result artifacts: code marks `_abs` questions as `ABS`, while older result artifacts often retained the parent category. | Treated as a data/evidence interpretation issue, not a prompt-surface blocker. Category-path analysis preserved the fact that ABS changes labeling, not the memory assembly path. |
| evidence hygiene lapse | `.sisyphus/evidence/task-7-preflight-broken-model.txt:24-25`; `.sisyphus/notepads/longmemeval-harness-production-parity/issues.md:132-143` | A prior evidence file briefly contained raw example credentials before redaction. | Corrected in place by replacing raw values with redacted placeholders; documented as a process defect worth carrying forward into evidence-review practice, not as a parity failure. |

Two adjacent scope defects also matter contextually even though they are more about execution constraints than runtime behavior: the shared-user `TEST_USER_ID` legacy benchmark contract (`.sisyphus/notepads/longmemeval-harness-production-parity/issues.md:29-33`) and the runner/config-pin hash contract that kept `_format_eval_memory_block()` and `build_assembled_system_prompt()` alive as out-of-scope consumer dependencies (`tests/benchmark_results/harness_parity_strip_diff.md:31-129`).

## (e) Clean format-change comparison

**HALT — baseline undeterminable.**

The correct clean comparison for this plan was supposed to compare the parity-fixed prompt-construction path against the historical Wave 0 Option A anchor while holding retrieval scope constant through **per-question synthetic-user isolation**. That isolation matters: once each question runs under its own deterministic synthetic user, the intended comparison isolates the **prompt-format / prompt-construction change** instead of mixing prompt-format differences with shared-user retrieval-scope contamination (`tests/benchmark_results/harness_parity_oracle_review.md:12-16, 39-52`).

However, the required full-corpus raw outputs do not exist in the T14/T15 artifact chain:

- `tests/benchmark_results/harness_parity_baseline_run.json` reports `status: "halt"`, `halt_reason: "Full haystack-bearing LongMemEval_S corpus unavailable"`, `aggregate_adjusted_score: null`, `per_category_scores: null`, and `records: null` (`tests/benchmark_results/harness_parity_baseline_run.json:4-5, 97-103`).
- `tests/benchmark_results/harness_parity_baseline_decision.md` therefore declares `HALT — baseline undeterminable` and explicitly states that aggregate deviation, per-category rank movement, replay math, and confirmation-run logic are all non-executable (`tests/benchmark_results/harness_parity_baseline_decision.md:26-45, 47-61`).
- `.sisyphus/evidence/task-14-score-recompute.json` confirms why no raw replay can be done: both LongMemEval_S HuggingFace URLs returned 404, local cache was empty, Wave 0 results lacked `haystack_sessions`, and the dev subset had only 50 questions (`.sisyphus/evidence/task-14-score-recompute.json:4-27`).
- `.sisyphus/evidence/task-15-anomaly-math.json` and `.sisyphus/evidence/task-15-confirmation-decision.json` both record that anomaly math and confirmation are blocked because T14 never produced a completed baseline (`.sisyphus/evidence/task-15-anomaly-math.json:27-47`; `.sisyphus/evidence/task-15-confirmation-decision.json:8-28`).

So the honest comparison statement for this section is:

> **HALT — baseline undeterminable.** The only numeric anchor still on record is the historical pre-parity Wave 0 Option A figure (`49 / 473 = 0.10359408033826638`, rounded as `10.4%`), and this postmortem must not promote that historical anchor into a new post-parity baseline because the T14/T15 chain produced no completed full-corpus raw counts. See `tests/benchmark_results/harness_parity_baseline_decision.md`.

## (f) Wave-priority implications from T16

T16 intentionally stopped short of inventing roadmap implications from missing data. Its governing conclusion is that the roadmap remains on **historical priors**, not on a fresh parity-era baseline (`tests/benchmark_results/harness_parity_baseline_oracle.md:10-18, 32-47`).

Practical implications:

1. **No threshold-crossing analysis is executable.** T16 states this explicitly; the right result is “not assessable,” not “none” (`tests/benchmark_results/harness_parity_baseline_oracle.md:34-37, 41-47`).
2. **Priority order is unrefreshed, not reapproved.** Existing roadmap category values remain the last production-aligned historical priors on file, but they were not reconfirmed by a new T14/T15 run (`tests/benchmark_results/harness_parity_baseline_oracle.md:20-29, 34-37`).
3. **Wave-planning should treat parity as shipped but baseline replacement as blocked.** In other words: the harness-native production path exists, but the plan did not earn a new full-corpus baseline because the corpus was unavailable.

## (g) Surgical patches

The documentation/tag follow-through after the parity work stayed surgical and source-backed:

| Path | Surgical scope | Evidence |
|---|---|---|
| `tests/benchmark_results/wave0_closure_memo.md` | One additive correction section appended after line 342; diff hunk added 53 lines and touched no existing lines. | `.sisyphus/evidence/task-17-additive-diff.txt:1-59`; `.sisyphus/evidence/task-17-reference-check.txt:60-100` |
| `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md` | Two sentence groups only: lines 40-45 and 106-113 reframed `49/473` / `10.4%` as historical harness artifact, added T15 HALT citation, and avoided fabricating a new baseline. | `.sisyphus/evidence/task-18-surgical-diff.txt:17-74`; `.sisyphus/evidence/task-18-framing-check.txt:9-40` |
| `.sisyphus/plans/wave1-prompt-surface-changes.md` | Targeted anchor rewrite only: `10.4%` count `2→1`, `pre-wave-1` `9→6`, `harness-parity-shipped` `0→3`; remaining `pre-wave-1` hits intentionally confined to TODO 18 rollback exception. | `.sisyphus/evidence/task-19-grep-reconcile.txt:6-84`; `.sisyphus/evidence/task-19-todo5-band.txt:15-38` |
| `docs/MEMORY_UPGRADE_ROADMAP.md` | Opening note only (lines 3-5 in working copy) rewritten to cite T15 HALT/T14 block; downstream category tables and wave sections left untouched. | `.sisyphus/evidence/task-20-roadmap-diff.txt:4-28`; `.sisyphus/evidence/task-20-table-guard.txt:4-28` |
| `harness-parity-shipped` tag | Lightweight local tag verified at the authoritative shipped SHA `d5d7bce81ed3cdee2ccf5485c83d7890382129d2`; no push occurred. | `.sisyphus/evidence/task-21-tag-head.txt:5-25`; `.sisyphus/evidence/task-21-no-push.txt:5-38` |

The net effect of T17-T21 was to make the record honest: parity-path implementation shipped, historical Wave 0 figures stayed labeled as historical harness artifacts, and downstream docs stopped pretending that a fresh numeric post-parity baseline existed.

## (h) Path A successor methodology recommendations

The main methodological lesson is that future parity audits need a **consumer-path successor check**, not just an assembly-surface check. A concrete successor check that would have caught `_format_eval_memory_block()` is:

1. **Static call-graph / AST-backed consumer-path assertion**: start at the real benchmark entry points (`orchestrator/eval/runner.py` for the legacy runner and any parity-native entry point under `tests/longmemeval/**`), walk the prompt-construction call graph through the model-bound system prompt, and fail the audit if any benchmark-local formatter still constructs `memory_context` or if any runner/config-pin surface still hashes that benchmark-local formatter as the active prompt contract.
2. **Pair the call-graph check with a byte-identity runtime spot check**: T5/T10 showed that verifying exact prompt bytes is valuable, but it should be treated as the runtime backstop after the static consumer-path check proves the benchmark is actually traversing production `build_memory_context()`.
3. **Separate scope-parity from benchmark-policy-parity**: synthetic-user isolation fixed the retrieval-scope side, but policy mismatches must still be documented explicitly instead of silently folded into benchmark scores.

The postmortem-only policy mismatch that remains most important is the **IE-assistant benchmark-vs-Daemon extraction-policy mismatch** already called out by the plan: Daemon selectively extracts assistant content only when it encodes user-side information, whereas LongMemEval single-session-assistant questions test remembering assistant utterances as such (`.sisyphus/plans/longmemeval-harness-production-parity.md:33, 963-964`). That mismatch belongs in postmortem/roadmap discussion, but **it was intentionally out of scope for implementation in this plan** and should not be retroactively treated as part of the harness-parity patch itself.
