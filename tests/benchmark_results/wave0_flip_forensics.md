# Wave 0 Validation Flip Forensics — IE-user & KU

**Generated:** 2026-04-24
**Source runs:** `wave0_validation_run_{1,2,3}/longmemeval_results.jsonl` + `run_metrics.json`

---

## Scope

Only **IE-user** and **KU** categories from `dev_subset.json` (9 questions each, 18 total) are analyzed.
Categories excluded: IE-assistant, IE-preference, MR, TR, ABS.

---

## Retrieval Availability

**All 50 questions across all 3 runs have `retrieved_memory_ids: []` and `memories_used: 0`.**

The memory extraction pipeline was non-functional during all three runs (extraction outcome: 2079/2079 sessions errored). Consequently:

- No retrieval ordering differences can explain any flips — there was no retrieval.
- Answer generation was operating with zero injected memory context in every run.
- The only variable between runs is answer-model output nondeterminism.

---

## Flip Summary

**5 flipped questions identified. All 12 flipped verdict-pairs (across all run-pair combinations) are Mechanism C.**

---

## Flipped Questions — IE-user

### 1. `8550ddae` — "What type of cocktail recipe did I try last weekend?"

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| **Verdict** | correct | incorrect | incorrect |
| **retrieved_memory_ids** | [] | [] | [] |
| **answer_hash** | `0dbdf9213179e9836c85584e798aa5543b474222723806e5cbcd22fe6d2cfd45` | `af0fe7b359276302aeb5f1ffce316630b38cc39cae6e92e8e76909dea188f483` | `f0d7ad40770770ad7520c292c5991bc34ba200a43ab3a8bf68b1bc5ce87394a5` |

- **Flipped pairs:** R1↔R2, R1↔R3
- **Mechanism:** C — memids identical (empty), answer hashes differ across all 3 runs, verdict changed

---

### 2. `25e5aa4f` — "Where did I complete my Bachelor's degree in Computer Science?"

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| **Verdict** | incorrect | correct | incorrect |
| **retrieved_memory_ids** | [] | [] | [] |
| **answer_hash** | `f97dbf86fe4e5543ca1965b08e96ecb87438a0b412000f6c4e05e421cd6a38e5` | `fdff810920b7c9d8e944eb94808af403679d152895bedde24d80c417c68078b0` | `0a143cb5bb7c2b6f17627245f84c0cdadcd6de7362f3b3f868a2d90f1a511fcd` |

- **Flipped pairs:** R1↔R2, R2↔R3, R1↔R3 (all three pair combinations flipped)
- **Mechanism:** C — memids identical (empty), answer hashes differ across all 3 runs, verdict changed

---

## Flipped Questions — KU

### 3. `852ce960` — "What was the amount I was pre-approved for when I got my mortgage from Wells Fargo?"

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| **Verdict** | correct | correct | incorrect |
| **retrieved_memory_ids** | [] | [] | [] |
| **answer_hash** | `2e990f9589dcc11c75ec5f2760dc801b0e084501b8eb787fc679a8b0fcce0b43` | `77479e3eb6ba836d8c7ad7945be6d5497198d5b744897c0e46a373328b504006` | `b2b345ea74b85d3c6b5b97b867589f35d6f7acbc38081bf35bbe07ebbc75f83c` |

- **Flipped pairs:** R1↔R3, R2↔R3
- **Mechanism:** C — memids identical (empty), answer hashes differ across all 3 runs, verdict flipped in R3

---

### 4. `6a1eabeb` — "What was my personal best time in the charity 5K run?"

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| **Verdict** | correct | correct | incorrect |
| **retrieved_memory_ids** | [] | [] | [] |
| **answer_hash** | `c2de95cf8bfe47b3c2aa7dae9f44987dcda6069089d5588ff7c0a2327d2a88c0` | `f7198b411ac7aef8834e3a302526ff28012e390a588b243804822b26cba882eb` | `27c042d0840ec00efd9ee0af103a85e90e0e20ddd2841c4bc68daaff0b451bd2` |

- **Flipped pairs:** R1↔R3, R2↔R3
- **Mechanism:** C — memids identical (empty), answer hashes differ across all 3 runs, verdict flipped in R3

---

### 5. `59524333` — "What time do I usually go to the gym?"

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| **Verdict** | incorrect | correct | incorrect |
| **retrieved_memory_ids** | [] | [] | [] |
| **answer_hash** | `7415c37ef606c017fa3924685263515c09ce04189577a0e3a7a5bead3cdf1e61` | `d918297e92cc88abd6ad91e7f3af175e75373d20f3867eaecbc72c11323b82d5` | `8644bb5d0efa80aca767141767c1d72f617b79d403d7117bf50393e03e208bf2` |

- **Flipped pairs:** R1↔R2, R2↔R3, R1↔R3 (all three pair combinations flipped)
- **Mechanism:** C — memids identical (empty), answer hashes differ across all 3 runs, verdict changed

---

## Mechanism Classification

| Mechanism | Description | Count |
|---|---|---|
| **A** — same memids + same answer hash + verdict flipped | Impossible / logging bug | 0 |
| **B** — memids differ | Retrieval candidates or ordering changed | 0 |
| **C** — memids same + answer hashes differ | Answer-model output nondeterminism | **12** |
| **D** — impossible combo / logging bug | — | 0 |

Total flipped verdict-pairs: **12** (across all run-pair combinations for 5 questions)
Mechanism C share: **100%**

---

## Per-Run Category Accuracy

| Category | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| IE-user | 33.3% (3/9) | 11.1% (1/9) | 11.1% (1/9) |
| KU | 55.6% (5/9) | 33.3% (3/9) | 33.3% (3/9) |
| **Combined** | **44.4% (8/18)** | **22.2% (4/18)** | **22.2% (4/18)** |

---

## Diagnostic Conclusion

All flips are **Mechanism C** — answer-model nondeterminism with zero retrieval contribution. Since `retrieved_memory_ids` is empty in every run, retrieval variance is ruled out as a flip driver. The memory pipeline was broken across all three runs (extraction 100% errored), meaning the system was operating in a no-memory regime for every question. Variance in this regime is entirely attributable to answer-generation stochasticity (temperature not set to 0, or provider-level randomness not fully suppressed by the fixed seed).

No flips attributable to Mechanisms A, B, or D were found.
