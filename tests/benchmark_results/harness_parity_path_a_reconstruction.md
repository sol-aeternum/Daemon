# Harness Parity — Wave 0 Path A Coverage Reconstruction

**Task**: T2 — Reconstruct Wave 0 Path A coverage  
**Date**: 2026-05-06  
**Scope**: Classify every T1 finding from `tests/benchmark_results/harness_parity_inventory.md` as either `path_a_miss` or `post_w0_drift` using Wave 0 / consumer-path documentary evidence.

---

## 1. Bottom line

Every T1 finding is classified as **`path_a_miss`**.

No item is classified as `post_w0_drift` because the required source set does not establish concrete post-Wave-0 history evidence for any listed gap, and this task must downgrade unsupported drift claims to `path_a_miss`.

The core miss is structural: Wave 0 Path A verified that the harness called production `assemble_system_prompt()`, but it accepted the benchmark-local `_format_eval_memory_block()` adapter as sufficient and therefore did **not** close the deeper consumer-path gap that the harness never calls production `build_memory_context()` and still hashes `_format_eval_memory_block()` as the active memory formatter.

---

## 2. Root-cause reconstruction

Wave 0 Path A covered the **prompt-assembly seam**, not the full **prompt-construction seam**. The Path A audit explicitly recorded that `assemble_system_prompt()` was called while `build_memory_context()` was not called and `_format_eval_memory_block()` remained the benchmark adapter (`tests/benchmark_results/wave0_closure_path_a_audit.md:15-22`, `:64-89`). That meant Path A treated “production assembly with benchmark-local `memory_context`” as sufficiently aligned, even though the benchmark answer path still built `memory_context` outside production and the runner contract still pinned `_format_eval_memory_block()` via `active_memory_formatter_sha256` (`tests/benchmark_results/harness_parity_inventory_runner_consumers.tmp.md:70-75`, `:167-180`, `tests/benchmark_results/wave1_benchmark_consumer_path.md:17-20`).

In short: **Path A missed `_format_eval_memory_block()` because it audited whether the benchmark reused production `assemble_system_prompt()`, not whether the benchmark had stopped using `_format_eval_memory_block()` as the active consumer-path formatter.**

---

## 3. Classification table

| T1 finding | T1 source | Classification | Why this is `path_a_miss` | Evidence |
|---|---|---|---|---|
| `build_memory_context()` not called | `tests/benchmark_results/harness_parity_inventory.md:15, 175-193, 256` | `path_a_miss` | Path A audit itself documented that the harness does not call `build_memory_context()` and instead uses `_format_eval_memory_block()`. This was a known unresolved coverage gap, not proven later drift. | `tests/benchmark_results/wave0_closure_path_a_audit.md:15-16`, `:70-89`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:40-44` |
| `_format_eval_memory_block()` substitute | `tests/benchmark_results/harness_parity_inventory.md:35-61, 84, 256` | `path_a_miss` | Path A left the benchmark-local formatter in place as the adapter feeding production assembly. Consumer-path evidence later reframed this as the decisive gap, but the formatter was already the active path during Path A. | `tests/benchmark_results/wave0_closure_path_a_audit.md:16`, `:43-49`, `:72-89`; `tests/benchmark_results/harness_parity_inventory_runner_consumers.tmp.md:70-75`, `:167-180` |
| `build_assembled_system_prompt()` calls production assembler with benchmark-local `memory_context` | `tests/benchmark_results/harness_parity_inventory.md:64-85` | `path_a_miss` | This is the precise Path A design: call `assemble_system_prompt()` but feed it benchmark-local formatted text. That choice existed in Path A coverage and is not shown as a later regression. | `tests/benchmark_results/wave0_closure_path_a_audit.md:15-16`, `:43-49`, `:64-89`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:14-18` |
| Dual-call metadata path (`build_assembled_system_prompt()` plus standalone `_format_eval_memory_block()`) | `tests/benchmark_results/harness_parity_inventory.md:123-140` | `path_a_miss` | The duplicate formatter call is present in the live harness path and explains that metadata preserves the benchmark-local flattened block separately from the assembled prompt. Path A did not remove or replace this. | `tests/longmemeval/evaluate.py:651-652, 703-707`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:14-19` |
| L0 `[FROZEN MEMORIES]` gap | `tests/benchmark_results/harness_parity_inventory.md:44-45, 53, 79, 135, 257` | `path_a_miss` | Path A audit explicitly documented that L0 memories were retrieved but not isolated, because `_format_eval_memory_block()` flattened them instead of using production `_format_l0_block()`. | `tests/benchmark_results/wave0_closure_path_a_audit.md:19`, `:82-87`, `:175-195`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:43` |
| Token-budget trimming gap | `tests/benchmark_results/harness_parity_inventory.md:43, 56, 82, 138, 184-190, 258` | `path_a_miss` | Path A audit explicitly recorded that benchmark formatting had no `estimate_tokens()` loop while production `build_memory_context()` does. This was already a documented non-covered gap. | `tests/benchmark_results/wave0_closure_path_a_audit.md:21`, `:82-87`, `:216-227`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:35-37` |
| Summaries gap | `tests/benchmark_results/harness_parity_inventory.md:54, 80, 136, 191, 259` | `path_a_miss` | Path A audit documented that `summaries=[]` was hardcoded / defaulted in the harness path, so production summary selection was not exercised. | `tests/benchmark_results/wave0_closure_path_a_audit.md:24`, `:237-244`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:42` |
| Preferences gap | `tests/benchmark_results/harness_parity_inventory.md:55, 81, 137, 192, 202, 260` | `path_a_miss` | Path A reused `assemble_system_prompt()` without passing a `preferences_block`, and the audit called this out as a benchmark gap/default. | `tests/benchmark_results/wave0_closure_path_a_audit.md:23`, `:236`, `:244`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:44` |
| `include_dream_observations` hardcoding | `tests/benchmark_results/harness_parity_inventory.md:52, 78, 99, 108, 134, 245, 261, 275, 282` | `path_a_miss` | T1 recorded that the harness hardcodes `include_dream_observations=True` in `retrieve_user_memories()`. No Wave 0 doc shows this as a post-closure change; it is part of the harness behavior Path A left in place. | `tests/benchmark_results/harness_parity_inventory.md:96-109, 133-138, 241-246`; `tests/longmemeval/evaluate.py:613-624` |
| Query text source difference | `tests/benchmark_results/harness_parity_inventory.md:190, 248, 262` | `path_a_miss` | Production `build_memory_context()` derives query text from recent messages, while the harness passes direct `question_text`. Because Path A never adopted `build_memory_context()`, this source difference remained unresolved. | `tests/benchmark_results/harness_parity_inventory.md:187-193, 248`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:32-37`; `orchestrator/memory/injection.py:194-206` |
| Standalone `run_evaluation()` allowlist bypass | `tests/benchmark_results/harness_parity_inventory.md:144-155, 246, 273, 280` | `path_a_miss` | T1 identified the standalone path as a legacy contamination vector because it does not pass `allowed_source_conversation_ids`. This is a known harness path left outside Path A coverage, not proven later drift. | `tests/benchmark_results/harness_parity_inventory.md:150-155, 246`; `tests/longmemeval/evaluate.py:770-892` (inventory-cited); `tests/benchmark_results/wave1_benchmark_consumer_path.md:8-20` |
| Runner hash/config-pin consumer (`active_memory_formatter_sha256`) | `tests/benchmark_results/harness_parity_inventory.md:211-215`; `tests/benchmark_results/harness_parity_inventory_runner_consumers.tmp.md:167-180, 257-266` | `path_a_miss` | The runner contract still pins `_format_eval_memory_block()` as the active consumer-path formatter. This proves Path A did not replace the benchmark-local formatter with `build_memory_context()` in the measurable harness contract. | `tests/benchmark_results/harness_parity_inventory_runner_consumers.tmp.md:70-75`, `:167-180`, `:257-266`; `tests/benchmark_results/wave1_benchmark_consumer_path.md:20` |

---

## 4. Coverage notes for older Wave 0 docs

Several older Wave 0 documents still describe the pre-Path-A architecture or contain assumptions later corrected by the Path A audit and consumer-path gate. For this reconstruction, the authoritative ordering is:

1. **T1 inventory** defines the source list of findings (`tests/benchmark_results/harness_parity_inventory.md`).
2. **Wave 0 Path A audit** shows what Path A explicitly checked and still left uncovered (`tests/benchmark_results/wave0_closure_path_a_audit.md`).
3. **Wave 1 consumer-path gate** explains why those uncovered seams are benchmark-critical (`tests/benchmark_results/wave1_benchmark_consumer_path.md`).

That ordering is why the reconstruction classifies the findings as **missed by Path A coverage**, not as newly introduced drift.

---

## 5. Final classification result

- **T1 finding count:** 12 required findings classified here
- **`path_a_miss`:** 12
- **`post_w0_drift`:** 0

Because no finding met the task’s requirement for concrete post-Wave-0 git/history proof, all findings remain classified as **`path_a_miss`**.
