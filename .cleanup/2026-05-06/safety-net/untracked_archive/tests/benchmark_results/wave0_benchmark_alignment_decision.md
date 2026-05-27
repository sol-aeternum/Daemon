# Wave 0 Benchmark Alignment Decision

**Artifact type:** BH4 — Decision and recommendation
**Evidence basis:** BH1, BH2, BH3, revised low-score diagnosis, full-corpus sanity check
**Date:** 2026-04-29
**Scope:** Diagnosis-only decision artifact. No code changes, no baselines update, no tag creation.

---

## 1. Problem Statement

The full-corpus Wave 0 score of **22.4%** is **blocked from promotion** as a production-memory quality baseline by the sanity gate (±8pp around 67.8%). However, the 22.4% figure cannot be treated as representative of production-memory quality either, because it was measured on the **benchmark evaluation path** — which is architecturally independent of the production injection pipeline (`orchestrator/memory/injection.py`).

Three preceding artifacts established the facts:

- **BH1** (`wave0_benchmark_vs_production_injection.md`): The benchmark harness (`tests/longmemeval/evaluate.py`) bypasses `orchestrator/memory/injection.py` entirely. It uses `build_answer_prompt()` — a thin bullet-list user message with no system grounding, no categories, no guardrails, no budget enforcement.
- **BH2** (`wave0_dual_injection_test.md`): The prompt difference is large enough to invalidate using benchmark scores as a production-memory quality proxy. Direct trace evidence (IL1) confirms correct memories are present in benchmark prompts for `e47becba` and `58bf7951` yet the model fails to use them — a benchmark-path answer-generation/context-use failure.
- **BH3** (`wave0_benchmark_injection_origin.md`): The independent benchmark path is **deliberate** (design-for-isolation), not the result of a hard technical blocker. `retrieve_memories_for_text()` is directly accessible from `evaluate.py`.

The revised low-score diagnosis (`wave0_low_score_diagnosis.md`) confirms the primary failure mode is **benchmark-path answer/use-of-context failure** plus **store-state ambiguity** — not production injection truncation.

---

## 2. Why 22.4% Cannot Be Promoted as a Production-Memory Baseline

The 22.4% full-corpus score is a measurement of the **evaluate-path prompt design** only:

| Property | Production Path | Benchmark Path (which produced 22.4%) |
|----------|----------------|----------------------------------------|
| Memory formatting | Categorized (`Fact:`, `Project:`) | Flat bullet list, no labels |
| Prompt structure | System prompt + user message | Single user message only |
| System grounding | Full `DAEMON_SYSTEM_PROMPT` | None |
| Guardrails | `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` | None |
| Token budget | `DEFAULT_MAX_TOKENS = 2500` enforced | Not enforced |
| L0 frozen memories | Separate `[FROZEN MEMORIES]` block | Raw retrieval output |
| Session summaries | Appended under `Recent context:` | None |

Because the benchmark path and production path are architecturally independent, the 22.4% score measures evaluate-path behavior — not how Daemon's production memory injection performs in live chat. **Treating 22.4% as a production-memory baseline would be a category error.** The score reflects a stripped-down prompt task, not the full production memory retrieval task.

---

## 3. Path A: Align Benchmark Harness to Production Injection

### Description

Update `tests/longmemeval/evaluate.py` to use `orchestrator/memory/injection.py` for prompt construction instead of the independent `build_answer_prompt()`. Specifically:

1. Replace `build_answer_prompt()` + `answer_with_llm()` with calls to `build_memory_context()` and `assemble_system_prompt()` from `orchestrator/memory/injection.py`.
2. Send the assembled production-style prompt (system + user message) to the LLM.
3. Re-run the full-corpus evaluation against the aligned harness.
4. Use the resulting score as the production-memory quality baseline (subject to sanity gate).

### Pros
- **Architecturally honest**: Scores measure what production actually does.
- **Enables valid comparison**: Benchmark scores can be compared to production quality.
- **No new test infrastructure needed**: `retrieve_memories_for_text()` is already accessible from `evaluate.py` (BH3 confirmed).
- **No production code changes**: Harness change only, isolated to `tests/longmemeval/`.
- **Unblocks baselines.md and Oracle checkpoint 2**: With alignment, the score is a valid production-memory baseline.

### Cons
- **Harness change requires careful mapping**: `evaluate.py` must correctly replicate production chat flow.
- **May expose new failure modes**: Once the benchmark measures production behavior, different errors may surface.

### Hard Blockers Evaluated
BH3 confirmed **no hard technical blocker** prevents the benchmark harness from calling production injection. `retrieve_memories_for_text()` is callable from `evaluate.py`. The only required change is routing the retrieved memories through `build_memory_context()` → `assemble_system_prompt()` instead of `build_answer_prompt()`.

### Decision Criteria Status
- ✅ Architecturally feasible (no hard blocker)
- ✅ `retrieve_memories_for_text()` accessible from `evaluate.py`
- ✅ No production code changes required
- ✅ Produces a valid production-memory quality baseline

---

## 4. Path B: Retain Benchmark-Path Harness, Relabel Score Semantics

### Description

Keep the current benchmark harness (`build_answer_prompt()`) unchanged. Accept that the 22.4% score measures evaluate-path prompt quality only. Relabel the score semantics to make it explicit that it is a **prompt-design benchmark** rather than a **production-memory baseline**. Document the architectural split prominently in `baselines.md` and do not use benchmark scores to characterize production memory quality.

### Pros
- **No harness change**: Zero changes to `evaluate.py`.
- **Honest about what is measured**: Score is clearly labeled as evaluate-path prompt quality.

### Cons
- **Does not unblock baselines.md as a production-memory baseline**: The 22.4% remains architecturally misaligned.
- **Does not enable production quality tracking**: Without alignment, there is no way to measure whether production injection changes improve the benchmark score.
- **Score remains blocked by sanity gate**: Without alignment, the 22.4% cannot be compared to the 67.8% historical reference.

### When to Choose Path B
Path B is appropriate only if a **verified hard technical blocker** exists that prevents the benchmark harness from using production injection — e.g., circular imports, infrastructure requirements that make standalone evaluation impossible, or a fundamental architectural incompatibility. BH3 found no such blocker.

---

## 5. Recommendation

**Path A is recommended.**

The evidence is clear and unanimous:

1. **No hard blocker exists** (BH3). The evaluate-path independence was a deliberate design-for-isolation choice, not a technical constraint.
2. **The 22.4% score cannot be promoted** as a production-memory baseline under the current architectural split. It is a measurement of the evaluate-path prompt design only.
3. **Production injection improvements would be invisible** to the current benchmark. Unless the harness is aligned, there is no valid signal for whether production injection changes help or hurt.
4. **Path A is a harness-only change** — no modifications to `orchestrator/memory/injection.py` or any production code.

Path B is the correct fallback only if a future implementation attempt uncovers a verified hard blocker that makes Path A infeasible. In that case, Path B should be adopted with an explicit list of the blockers that forced that choice.

---

## 6. Decision

| | Path A | Path B |
|---|---|---|
| **Action** | Align benchmark harness to production injection | Retain benchmark-path harness, relabel semantics |
| **Score becomes** | Valid production-memory baseline | Evaluate-path prompt quality only |
| **baselines.md** | Unblocked | Remains blocked |
| **Oracle checkpoint 2** | Unblocked | Remains blocked |
| **Tag creation** | Unblocked | Remains blocked |
| **Hard blocker** | None found | Only fallback if blocker exists |
| **Recommended** | **Yes** | Only if Path A infeasible |

**Decision: Path A.**

---

## 7. Next Steps (Post-BH4)

Once this decision is accepted, the following become unblocked in sequence:

1. **Harness alignment implementation**: Update `evaluate.py` to use `build_memory_context()` / `assemble_system_prompt()` instead of `build_answer_prompt()`.
2. **Full-corpus re-run**: Execute the aligned benchmark against the full 500-query corpus.
3. **Sanity gate re-evaluation**: Apply ±8pp envelope around the new baseline. If passed, proceed to update `baselines.md`.
4. **Oracle checkpoint 2**: Evaluate whether the aligned score warrants updating the baselines document and creating the Oracle checkpoint 2 artifact.
5. **Tag creation**: If Oracle checkpoint 2 passes, proceed with tag creation per the Wave 0 plan.

---

*BH4 — Diagnosis-only decision artifact. No code changes, no baselines update, no tag creation in this step.*
