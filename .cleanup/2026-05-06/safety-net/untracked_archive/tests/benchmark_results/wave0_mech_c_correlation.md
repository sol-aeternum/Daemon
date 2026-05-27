# Wave 0 — Mechanism-C / Empty-Retrieval Correlation Analysis

**Generated:** 2026-04-24
**Source:** `wave0_validation_run_{1,2,3}/longmemeval_results.jsonl` + `wave0_flip_forensics.md`

---

## Finding: Zero Variance in Retrieval State

**All 18 IE-user/KU questions across all 3 runs have `retrieved_memory_ids: []`.**

There is zero retrieval variance in this dataset. The memory extraction pipeline was broken during all three wave-0 validation runs (extraction outcome: 2079/2079 sessions errored), so every question — flipped or not — operated in a null-retrieval regime.

Because there is no retrieval variance, it is **impossible to test whether Mechanism-C flips are confined to empty-retrieval questions**. There are no non-empty-retrieval questions to form a comparison group.

---

## (1) Flipped IE-user/KU Questions — All Empty Retrieval ✓

Confirmed from `wave0_flip_forensics.md` and cross-checked against raw JSONL:

| question_id | Category | Flipped Pairs | R1 retrieved_memory_ids | R2 retrieved_memory_ids | R3 retrieved_memory_ids |
|---|---|---|---|---|---|
| `8550ddae` | IE-user | R1↔R2, R1↔R3 | `[]` | `[]` | `[]` |
| `25e5aa4f` | IE-user | R1↔R2, R2↔R3, R1↔R3 | `[]` | `[]` | `[]` |
| `852ce960` | KU | R1↔R3, R2↔R3 | `[]` | `[]` | `[]` |
| `6a1eabeb` | KU | R1↔R3, R2↔R3 | `[]` | `[]` | `[]` |
| `59524333` | KU | R1↔R2, R2↔R3, R1↔R3 | `[]` | `[]` | `[]` |

**All 5 flipped questions had empty retrieved_memory_ids across all 3 runs.** ✓

---

## (2) Non-Flipped IE-user/KU Questions — Answer Hash Stability

Since all questions had empty retrieval, every non-flipped question is a same-regime control case. Below are up to 5 non-flipped IE-user/KU questions with their answer hashes across all 3 runs.

### Non-Flipped IE-user

| question_id | Question (truncated) | R1 verdict | R2 verdict | R3 verdict | Stable? |
|---|---|---|---|---|---|
| `b86304ba` | "How much is the painting of a sunset worth...?" | incorrect | incorrect | incorrect | ✗ hash varies |
| `86f00804` | "What book am I currently reading?" | incorrect | incorrect | incorrect | ✗ hash varies |
| `19b5f2b3` | "How long was I in Japan for?" | incorrect | incorrect | incorrect | ✗ hash varies |
| `caf9ead2` | "How long did it take to move to the new apartment?" | incorrect | incorrect | incorrect | ✗ hash varies |
| `ad7109d1` | "What speed is my new internet plan?" | incorrect | incorrect | incorrect | ✗ hash varies |
| `c5e8278d` | "What was my last name before I changed it?" | correct | correct | correct | ✗ hash varies |
| `545bd2b5` | "How much screen time on Instagram per day?" | incorrect | incorrect | incorrect | ✗ hash varies |

### Non-Flipped KU

| question_id | Question (truncated) | R1 verdict | R2 verdict | R3 verdict | Stable? |
|---|---|---|---|---|---|
| `5831f84d` | "How many Crash Course videos have I watched...?" | correct | correct | correct | ✗ hash varies |
| `f685340e` | "How often do I play tennis...?" | incorrect | incorrect | incorrect | ✗ hash varies |
| `184da446` | "How many pages of 'A Short History of Nearly Everything'...?" | correct | correct | correct | ✗ hash varies |
| `f685340e_abs` | "How often do I play table tennis with my friends...?" | correct | correct | correct | ✗ hash varies |
| `0977f2af` | "What new kitchen gadget did I invest in before getting the Air Fryer?" | incorrect | incorrect | incorrect | ✗ hash varies |
| `3ba21379` | "What type of vehicle model am I currently working on?" | incorrect | incorrect | incorrect | ✗ hash varies |

**Finding: Not a single non-flipped IE-user or KU question had stable answer hashes across all 3 runs.** Every question's answer_hash differed between runs, yet the judgment happened to be stable (same correct/incorrect verdict in all 3 runs despite different answer content). This illustrates that verdict stability and answer stability are independent phenomena — the judgment threshold is coarse enough that different answer strings can still clear or fail it.

### Example: `c5e8278d` — "What was my last name before I changed it?"
| | Answer Hash | Verdict |
|---|---|---|
| Run 1 | `7a41d308ee8c4631b3e44d5408f694d7732c12101ebcaa7ed9a82e4755cb004e` | correct |
| Run 2 | `fa6e4bc5800b2f8906aab0df35cf2cf53aa5baef9b576806df107fd6ef06b982` | correct |
| Run 3 | `0a4c4e1dea6c71b49a8d297a4f58e5d7c84c8c2e3b81e4cce462d8a1dd357318` | correct |

Three entirely different answer strings; same correct verdict in all 3 runs. The model hallucinated "Johnson" (the reference answer) differently each time but the hallucination was close enough to pass.

### Example: `b86304ba` — "How much is the painting of a sunset worth...?"
| | Answer Hash | Verdict |
|---|---|---|
| Run 1 | `30065d33ad36256dfa3364f84835671e29fb825d4d6a3653f5cb80d6c5e9e79d` | incorrect |
| Run 2 | `6957c8bb22ff4d6262cc75a980c9891bd174be1200db322033b33870ca86d3ec` | incorrect |
| Run 3 | `67ac1bdf967bf35ae62e21836692f6bf157c9c3415ea576f27f79eb8c4a9b732` | incorrect |

Three different hallucinated guesses; all wrong, same incorrect verdict.

---

## (3) Hypothesis Assessment

Given the data:

- **(a) Null-context speculation only** — Answer-model nondeterminism operating on questions with no retrieval context causes the flips. The model must hallucinate, and when it hallucinates differently across runs, the verdict can cross the judgment threshold.
- **(b) Retrieval-dependent flips** — Retrieval state is non-empty and causally influences flip behavior.
- **(c) Mixed: null-context speculation + retrieval-dependent flips** — Both mechanisms operate in different question subsets.

**What the data shows:**

All 5 flipped questions have empty retrieval — this is confirmed, but it is also true of every non-flipped question. The absence of any retrieval variance means hypothesis (b) and hypothesis (c) cannot be distinguished from (a) in this dataset.

**The data does NOT support hypothesis (a) exclusively** — it merely fails to disprove it. The correct conclusion is:

> **Hypotheses (b) and (c) are unresolvable with this data.** The data is consistent with (a) but equally consistent with (b) or (c) if the same-flipped-pattern would have occurred even with non-empty retrieval. There is no signal that discriminates between these possibilities.

The only thing that is definitively ruled out is that retrieval state **caused** the flips, because the flipped questions had the same retrieval state as stable ones (both empty).

---

## (4) Conclusion

| Claim | Status |
|---|---|
| All 5 flipped IE-user/KU questions have empty `retrieved_memory_ids` across all 3 runs | **Confirmed** |
| There exist non-flipped IE-user/KU questions with non-empty `retrieved_memory_ids` to serve as a control group | **Not found — zero questions with non-empty retrieval in any run** |
| Mechanism-C flips are confined to empty-retrieval questions | **Undeterminable** — no non-empty-retrieval comparison group exists |
| Hypotheses (b)/(c) are ruled out | **Not ruled out** — the data cannot distinguish (a) from (b)/(c) |

**Bottom line:** The wave-0 validation runs, as currently documented, cannot answer the question of whether Mechanism-C flips are specific to empty-retrieval questions. All questions had empty retrieval. The only actionable conclusion is that the 5 flipped questions are a pure subset of the null-retrieval regime, and their flips are attributable solely to answer-model output nondeterminism.

**To resolve the open hypothesis, wave-1 runs must have a functional memory pipeline** with successful extractions and non-empty retrieval for at least some questions. Only then can a comparison between flipped-vs-stable within non-empty-retrieval questions be made.

---

*Analysis by: Sisyphus-Junior | Source: wave0_validation_run_{1,2,3}/longmemeval_results.jsonl + wave0_flip_forensics.md*
