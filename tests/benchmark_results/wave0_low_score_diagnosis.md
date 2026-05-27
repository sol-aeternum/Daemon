# Wave 0 Low Score Diagnosis (Revised)

**Artifact type:** Revised diagnosis
**Evidence basis:** IL1 injection trace, IL2 injection audit, IL3 budget check, plus prior DB-A through DB-E evidence
**Date:** 2026-04-29
**Supersedes:** Previous `wave0_low_score_diagnosis.md`

---

## 1. Revised Executive Summary

The Wave 0 benchmark scoring failure is attributable to two primary failure modes:

1. **Benchmark-path answer-generation / context-use failure** — the model receives correct memories in a thin, unstructured prompt but fails to use them correctly
2. **Store-state ambiguity / conflicting facts** — the store contains decomposed, conflicting, or insufficiently specific facts that prevent correct answering even when retrieval works

Production injection truncation has been **explicitly ruled out** as a primary cause by three independent lines of evidence:
- IL1: Actual prompt traces for `e47becba` and `58bf7951` show correct memories are present in the prompt (not omitted)
- IL2: The benchmark harness bypasses the production injection module entirely, using a separate simple prompt builder
- IL3: Token budget check across 50 sampled queries shows zero truncations and no budget pressure

Retrieval infrastructure, extraction pipeline health, and threshold filtering are all functioning within expected parameters and do not explain the observed failures.

---

## 2. Ranked Failure Contributors

| Rank | Contributor | Confidence | Key Evidence |
|------|------------|------------|--------------|
| 1 | **Benchmark-path answer-generation / context-use failure** | High | IL1: `e47becba` and `58bf7951` both have correct memories present in the prompt, yet the model answered incorrectly. The benchmark harness sends a single user message with a bullet list — no system grounding, no categories, no production guardrails. The model appears to default to parametric knowledge or the wrong memory instead of using the correct retrieved fact. |
| 2 | **Store-state ambiguity / conflicting facts** | High | `e47becba`: competing Computer Science (rank 1, score 0.6644) vs Business Administration (rank 2, score 0.6493). Score gap is narrow and within embedding noise. `118b2229`: commute memories decomposed/ambiguous, no exact 45-minute fact. `c5e8278d`: wrong entity/state stored (Wilson → Thompson instead of Johnson). |
| 3 | **Retrieval ranking ambiguity near decision boundary** | Medium | Correct fact beaten by higher-scoring wrong fact in `e47becba`. Score gap (0.6644 vs 0.6493) suggests sensitivity to embedding noise. However, median top score across 50 queries is 0.263, well above threshold, so threshold is not the bottleneck. |
| 4 | **Production injection bug / token truncation** | **Explicitly demoted** | IL1 + IL2 + IL3 collectively rule this out as a primary cause. The benchmark path does not use production injection, and no budget truncation was observed in the sampled queries. |
| 5 | **Extraction quality (de-emphasized)** | Low | DB-C: 20/20 randomly sampled decrypted memories classified clean. Extraction pipeline is not the dominant failure mode. |
| 6 | **Threshold filtering (de-emphasized)** | Low | DB-E: Median top score per query is 0.263; only 4 of 50 queries had top scores below 0.17. MIN_FINAL_SCORE = 0.15 is not culling relevant memories at scale. |

---

## 3. What IL1, IL2, and IL3 Establish

### IL1: Injection Trace (e47becba, 58bf7951)

- **Finding:** For both traces, the correct memory is present in the actual prompt text sent by the benchmark harness.
- **Implication:** The model was not prevented from seeing the correct memory. This rules out "injection omission" as the failure cause for these two traces.
- **Caveat:** These are two specific traces. Other DB-D traces (`118b2229`, `51a45a95`, `c5e8278d`) involve store-state issues (decomposed facts, wrong entities) that may have different characteristics.

### IL2: Injection Audit (Production vs Benchmark Path)

- **Finding:** The benchmark harness (`tests/longmemeval/evaluate.py`) uses `build_answer_prompt()` and `answer_with_llm()`, which construct a single user message with a bullet list. This path **bypasses `orchestrator/memory/injection.py` entirely**.
- **Finding:** The production path uses `build_memory_context()` and `assemble_system_prompt()` with the full injection module, including `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`, token budgets, L0 support, and per-memory truncation.
- **Implication:** Any change to `orchestrator/memory/injection.py` has no effect on benchmark scores unless the benchmark harness is also updated. Production injection bugs cannot directly cause benchmark failures because the benchmark path does not use the production injection module.

### IL3: Injection Budget Check (50-query sample)

- **Finding:** `truncation_queries = 0`, `truncation_rate = 0.0`, `max_dropped = 0` across 50 sampled queries.
- **Finding:** `max_pre_tokens = 164` against a budget of 2500 tokens (6.5% utilization).
- **Implication:** Under current retrieval cardinality and memory formatting, the production injection budget would not truncate any of the sampled queries. Token budget pressure is not active in the sampled set.

### Combined Verdict

IL1, IL2, and IL3 collectively demote "production injection bug / token budget truncation" as a primary cause of the benchmark collapse. The correct memories are demonstrably present in the benchmark prompts (IL1), the benchmark path does not use the production injection module (IL2), and no budget truncation is occurring (IL3).

---

## 4. What the Evidence Rules Out

The following hypotheses are explicitly ruled out by the verified evidence:

- **Model never saw the correct memory:** IL1 shows correct memories are present in actual prompt traces for `e47becba` and `58bf7951`. The model answered incorrectly despite having the correct memory available.

- **Production injection truncation:** IL2 + IL3 show the benchmark bypasses production injection and no budget truncation was observed in the sampled set.

- **Wrong database or empty retrieval:** DB-A confirms the eval harness connects to the populated benchmark-user store. Retrieval returns candidates for every evaluated query (median 5 candidates per query).

- **Sparse or implausible store:** DB-B shows 27,599 active memories across all tiers with plausible category distribution. The store is large and well-populated.

- **Extraction content quality as dominant failure:** DB-C's 20/20 clean sample verdict stands. Extraction pipeline is not the bottleneck.

- **Threshold filtering as dominant failure:** DB-E demonstrates scores are generally above MIN_FINAL_SCORE = 0.15. The problem is not filtering but incorrect selection or answer-generation failure.

---

## 5. The Three Distinct Failure Modes

The Wave 0 evidence reveals **three distinct failure modes** that must be distinguished:

### Mode 1: Benchmark-path answer-use failure (IL1 confirmed)

**Cases:** `e47becba`, `58bf7951`

The correct memory is retrieved and present in the prompt, but the model generates an incorrect answer. The benchmark harness provides no system-level grounding, categories, or guardrails — only a bullet list of memories in a single user message. The model appears to default to parametric knowledge or select the wrong memory despite correct context.

**Evidence strength:** IL1 provides direct trace evidence. High confidence.

### Mode 2: Store-state ambiguity / conflicting facts (DB-D)

**Cases:** `e47becba` (competing memories), `118b2229` (decomposed/ambiguous), `c5e8278d` (wrong entity stored)

The store contains competing, decomposed, or wrong facts that make correct answering impossible regardless of retrieval or injection. For `e47becba`, both Computer Science and Business Administration degrees are stored — the model cannot determine which is correct without additional disambiguating information.

**Evidence strength:** DB-D shows this is a mixed failure mode affecting multiple traces. High confidence.

### Mode 3: Retrieval ranking ambiguity (DB-D)

**Cases:** `e47becba` (score 0.6644 vs 0.6493 for correct answer)

The correct fact is retrieved but ranked below a wrong fact with a narrow score gap within embedding noise. The ranking algorithm is working as designed but the noise floor prevents reliable discrimination near the decision boundary.

**Evidence strength:** Score gap evidence from DB-D. Medium confidence — requires embedding noise analysis to confirm.

---

## 6. What Not to Do Next

Based on the revised evidence, the following directions are contraindicated:

- **Do not invest in production injection tuning as a benchmark fix.** The benchmark harness bypasses the production injection module. Changes to `orchestrator/memory/injection.py` will not affect benchmark scores.

- **Do not invest in extraction quality improvements.** DB-C shows the extraction pipeline is already producing clean output. This is not the bottleneck.

- **Do not raise MIN_FINAL_SCORE or tighten threshold filtering.** DB-E shows the threshold is permissive enough. Tightening it would cull valid candidates without solving the core problem.

- **Do not add more memories to the store.** The store has 27,599 memories. Adding more decomposed or ambiguous memories would worsen the ambiguity problem.

---

## 7. Diagnosis-Only Framing

This document provides diagnosis, not a fix recommendation. The evidence points clearly to:

1. **Benchmark-path prompt design** — the thin, unstructured prompt gives the model insufficient guidance to use retrieved memories correctly
2. **Store-state ambiguity** — conflicting and decomposed facts create situations where even perfect retrieval cannot produce a correct answer

But recommending a specific fix is outside the scope of this diagnosis. Potential hypotheses include: strengthening the benchmark prompt structure, adding a verification step, or improving store specificity — but these require further investigation before commitment.
