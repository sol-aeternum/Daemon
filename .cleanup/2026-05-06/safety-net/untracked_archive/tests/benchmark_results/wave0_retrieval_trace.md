# Wave 0 Retrieval Trace Analysis

## Scope

This document presents detailed retrieval traces for the first five `judgment == 'incorrect'` questions from the full-corpus evaluation run (`tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_results.jsonl`). Each trace examines what the retrieval pipeline returned versus what the answer-generation step produced, to distinguish between retrieval failures and answer-generation failures.

---

## Trace 1: `e47becba`

**Question:** "What degree did I graduate with?"
**Reference answer:** Business Administration

**Retrieval result:** The pipeline returned 5 memories. The top 2 by composite score were:

1. `education.degree.bachelors.computer_science` — score `0.6644`
   - Content: "User graduated with a Bachelor's degree in Computer Science on May 15th, 2022"
2. `education.bachelor.business_administration` — score `0.6493`
   - Content: "User graduated with a Bachelor's degree in Business Administration five years ago"

**Conclusion:** The correct fact (`Business Administration`) was present in retrieval at rank 2, only 0.0151 score units below the top result. The system answered "I'm sorry, the provided memories do not contain information about your degree." This is a clear **answer-generation failure despite correct retrieval**. The presence of the CS degree at higher score may have caused the model to overweight it or simply fail to compare the two competing candidates.

---

## Trace 2: `118b2229`

**Question:** "How long is my daily commute to work?"
**Reference answer:** 45 minutes each way

**Retrieval result:** Multiple commute-related memories were returned, including:
- "commute lasts about 30 minutes"
- "train commute 25 min"
- "walking 10–15 min"
- "route adds 10 min"

**Conclusion:** No single retrieved memory captured "45 minutes each way." The retrieval produced a decomposed set of related but ambiguous commute fragments — 30 minutes, 25 minutes, 10–15 minutes, 10 minutes — none matching the reference value. This is a **retrieval gap**: the store held commute-related facts but not the specific 45-minute figure, or the query embedding failed to surface it above the fragmentary alternatives.

---

## Trace 3: `51a45a95`

**Question:** "Where did I redeem a $5 coupon on coffee creamer?"
**Reference answer:** Target

**Retrieval result:** The retrieved memories included highly relevant coupon entries, including a coffee-creamer coupon usage memory and a "Target coupon for $5 off $25 or more." The retrieval clearly found Target-adjacent coupon activity.

**Conclusion:** Despite the presence of relevant coupon memories, the system answered that it had no information. The returned memories were semantically close but not a direct match for "redeemed a $5 coupon on coffee creamer at Target." This is a **semantic mismatch / recall ambiguity** failure: the memory existed but was not connected to the specific (store, product, discount) triple the question asked for.

---

## Trace 4: `58bf7951`

**Question:** "What play did I attend at the local community theater?"
**Reference answer:** The Glass Menagerie

**Retrieval result:** The top retrieved memory was:
- Score `0.3978`
- Content: "User attended a local production of The Glass Menagerie at their community theater in late March"

**Conclusion:** Retrieval was **exactly correct** — the right play, the right venue, the right timeframe. Yet the system answered: "Based on the provided memories, you recently auditioned for a role in 'The Crucible,' but there is no specific memory indicating you attended a play at the local community theater." This is a textbook **answer-generation failure despite correct retrieval**. The model either ignored the top-scoring memory, misread it, or allowed a competing memory (the audition for The Crucible) to override it.

---

## Trace 5: `c5e8278d`

**Question:** "What was my last name before I changed it?"
**Reference answer:** Johnson

**Retrieval result:** Retrieved memories referenced a name change from "Emily Patricia Wilson" to "Emily Rose Thompson." Nothing in the retrieved set matched "Johnson."

**Conclusion:** This is a **wrong entity/state in store** failure. The memory system had a name-change fact, but it recorded a different prior surname (Wilson → Thompson), not the reference value (Johnson). The correct prior name either was never stored, was stored under a different user identity, or was overwritten during a dedup merge.

---

## Cross-Cutting Observations

**Mixed failure modes confirmed across five traces:**

| Trace | Failure Mode | Description |
|---|---|---|
| `e47becba` | Answer-generation | Correct retrieval ignored or misweighted |
| `118b2229` | Retrieval gap | Related facts returned but wrong specific value |
| `51a45a95` | Semantic mismatch | Relevant memories retrieved; specific triple not matched |
| `58bf7951` | Answer-generation | Correct retrieval actively overridden by wrong memory |
| `c5e8278d` | Wrong store state | No matching fact exists for the reference answer |

The dominant finding is that **retrieval and answer-generation failures are both common**, and they are largely independent. A correct retrieval does not guarantee a correct answer (traces 1 and 4). An incorrect answer does not always imply a retrieval failure (traces 2, 3, and 5 show retrieval returning relevant or even correct facts).

**ILIKE existence checks:** Exact-literal string matching against the store (e.g., searching for "Johnson", "45 minutes", "Target") was largely inconclusive across these traces. Most reference facts are paraphrased, temporally scoped, or stored as partial strings in the memory content, making exact-match lookups unreliable as a diagnostic tool. Semantic (embedding-based) retrieval is necessary but insufficient on its own.

---

*Generated from `tests/benchmark_results/wave0_full_corpus_recovery/longmemeval_results.jsonl`*
