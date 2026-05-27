# Wave 0 Halt Escalation

**Date:** 2026-04-24
**Subject:** Full-corpus baseline blocker — unresolved harness-path determinism question
**Related:** `wave0_closure_memo.md` Section 10, `wave0_mech_c_correlation.md`, `wave0_deterministic_mode_coverage.md`
**Status:** SUPERSEDED — see 2026-04-27 supersession notice below

---

## Supersession Notice — 2026-04-27

**This document is historically preserved but superseded.**

The halt condition described herein was predicated on the single-run point-estimate framing. This framing is now superseded by **V1.c (bounded-variance framing)** per `wave0_rerun_content_comparison_v2.md` (2026-04-27):

> "**Conclusion:** **V1.c (bounded-variance framing)** is the appropriate interpretation."

Under bounded-variance framing:
- The full-corpus baseline is no longer blocked by the harness-path determinism question
- Results are interpreted as point estimates within a characterized distribution (~6pp irreducible variance)
- The three-hypothesis framework (a/b/c) is superseded as the primary interpretive lens

This document is preserved as a historical record of the halt reasoning as of 2026-04-24. See `wave0_closure_memo.md` Section 11 and `wave0_validation_summary.md` for the updated framing.

---

## 1. Exact Blocker Description

All three wave-0 validation runs operated in a **null-extraction regime**:

- `benchmark_mode=false` (no fingerprint metadata)
- Null temperature fields in extraction calls
- Empty provider fingerprint metadata
- Extraction outcomes: `errored=2079` for all three validation runs

This means the memory extraction pipeline was non-functional during all wave-0 validation runs. As a consequence:

1. **All 18 IE-user/KU questions had empty `retrieved_memory_ids`** across all 3 runs
2. Even **non-flipped** IE-user/KU questions show **answer-hash drift** across runs (different answer strings, same verdict in all 3 runs)
3. This drift is observed even in questions whose verdicts are stable — meaning different answer content produces the same correct/incorrect judgment

The harness-path determinism question is: **Are the answer-hash drifts in non-flipped questions attributable solely to answer-model nondeterminism (hypothesis a), or is the production orchestrator streaming path introducing additional nondeterminism that hypotheses (b)/(c) cannot exclude?**

If hypothesis (b) or (c) is true, then the production orchestrator streaming path — which lacks benchmark determinism controls — could be causally contributing to flip behavior, making the full-corpus baseline unreliable.

---

## 2. The Three Hypotheses

| Hypothesis | Description | Status |
|---|---|---|
| (a) Null-context speculation only | Answer-model nondeterminism on empty-retrieval questions causes flips. Hallucination variance crosses judgment threshold. | Not ruled out; consistent with data |
| (b) Retrieval-dependent flips | Retrieval state is non-empty and causally influences flip behavior | Cannot be ruled out; no non-empty-retrieval comparison group |
| (c) Mixed | Both mechanisms operate in different question subsets | Cannot be ruled out; same as above |

The data cannot distinguish (a) from (b)/(c) because **every** question had empty retrieval. There is zero variance in retrieval state to form a comparison group.

---

## 3. Suspect Call Sites to Audit

The production orchestrator streaming path (`orchestrator/tools/completion.py`) has **zero benchmark determinism coverage** per `wave0_deterministic_mode_coverage.md`:

| Control | Harness Answer Path | Production Orchestrator Path |
|---|---|---|
| Dated model version | YES | NO |
| Temperature = 0.0 | YES | NO |
| Fixed seed = 42 | YES | NO |
| Fingerprint enforcement | YES | NO |
| Provider pinning | YES | NO |

**Specific call sites to audit:**

1. **`orchestrator/tools/completion.py`** — `completion_with_tools()`: The primary production LLM call. No `BENCHMARK_MODE` check. Temperature, seed, and fingerprint are uncontrolled.

2. **`orchestrator/daemon.py`** — `stream_sse_chat()`: The entry point that calls `completion_with_tools()`. Model selection comes from tier config, not from benchmark override. No fingerprint tracking.

3. **`orchestrator/memory/injection.py`** — `build_system_prompt()`: Called during prompt assembly; not itself an LLM call but relevant for whether system-prompt content could introduce non-determinism upstream of the streaming call.

The critical question is whether the **production orchestrator path** (which handles real conversations and would handle full-corpus ingest) uses the same streaming call structure, and if so, whether that path introduces answer-model temperature or seed variation that the harness answer path does not.

---

## 4. Test Strategy to Resolve the Question Conclusively

### Phase 1: Functional Extraction Baseline (prerequisite)

Before any non-determinism investigation can proceed, extraction must be fixed so that at least some questions have non-empty retrieval.

**Required:** A single validation run with `benchmark_mode=true` and successful extraction (`errored << 2079`) to confirm:
- Memory pipeline is functional end-to-end
- At least some IE-user/KU questions have non-empty `retrieved_memory_ids`
- Fingerprint metadata is populated

This is a prerequisite for any comparison between empty-retrieval and non-empty-retrieval questions.

### Phase 2: Compare Answer-Hash Stability Across Retrieval Regimes

With functional extraction, run a **single** additional validation run and collect:

1. For each question: `retrieved_memory_ids` count and content
2. For each question: `answer_hash` from the harness answer path
3. Cross-run comparison: within empty-retrieval questions, is answer-hash drift present? Within non-empty-retrieval questions, is answer-hash drift present or absent?

**Decision tree:**

- If non-empty-retrieval questions show **stable answer hashes** across runs → hypothesis (b)/(c) unlikely; answer-model nondeterminism is confined to empty-retrieval regime → proceed to Phase 3
- If non-empty-retrieval questions show **comparable answer-hash drift** to empty-retrieval questions → hypothesis (b)/(c) cannot be excluded without further investigation
- If non-empty-retrieval questions show **higher answer-hash drift** than empty-retrieval questions → hypothesis (b)/(c) likely; production orchestrator path investigation becomes urgent

### Phase 3: Production Orchestrator Path Audit (conditional)

If Phase 2 suggests production path contribution to nondeterminism:

1. **Instrument `completion_with_tools()`** to log temperature, seed, model, and fingerprint from each production streaming call (this is an audit-only change, not a code modification to be committed — log locally only)
2. Run two parallel single-question evaluations: one through the benchmark harness, one through the production orchestrator path
3. Compare answer hashes and fingerprints between the two paths for the same question

**Note:** Phase 3 requires coordination with the Orchestrator to ensure no production traffic is interfered with during the audit.

### Phase 4: Full-Corpus Baseline (only after Phase 2 or 3 resolves)

Only after the harness-path determinism question is resolved:
- If hypothesis (a) is confirmed: proceed to full-corpus baseline with acknowledged empty-retrieval regime
- If hypothesis (b) or (c) is confirmed: implement benchmark determinism controls on the production orchestrator path before running full-corpus baseline

---

## 5. Immediate Next Step

**Fix extraction and run a single validation run with `benchmark_mode=true` and successful extraction.** This is the only way to obtain a non-empty-retrieval comparison group. Without it, the harness-path determinism question is unresolvable.

The next Oracle checkpoint should verify that extraction is functional before proceeding to Phase 2.

---

*Escalation by: Sisyphus-Junior | Source: wave0_mech_c_correlation.md, wave0_deterministic_mode_coverage.md, wave0_validation_run_{1,2,3}*
