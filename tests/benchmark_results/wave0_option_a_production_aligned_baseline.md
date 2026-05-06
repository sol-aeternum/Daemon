# Wave 0 — Option A Production-Aligned Baseline

**Date:** 2026-05-04
**Task:** C1-D
**Status:** LOCKED

## Source of Truth

All values are sourced directly from verified C1-C rerun artifacts at
`tests/benchmark_results/wave0_closure_option_a_rerun/`. C1-D performed
no re-ingestion, code changes, or external LLM calls; it locks values from
already-completed C1-C artifacts only.

## Raw Artifact Score (Preserved for Traceability)

| Field | Value |
|---|---|
| Attempted | 500 |
| Unique question IDs | 500 |
| Success count | 473 |
| Error count | 27 |
| Correct (judgment == "correct") | 49 |
| Incorrect | 427 |
| Partially correct | 24 |
| **Official artifact score** | **49 / 500 = 0.098** |

## Option A Disposition-Adjusted Baseline

Under **Wave 0 Closure Option A** (user-authorized), the 27 invalid-ciphertext
rows are treated as bounded error-class exclusions from C1-A. They are NOT
relitigated or re-evaluated — the exclusion is analytical and carries forward.

| Field | Value |
|---|---|
| Excluded rows (invalid ciphertext) | 27 |
| Disposition denominator | 473 |
| Correct (unchanged) | 49 |
| **Option A disposition-adjusted baseline** | **49 / 473 = 0.10359408033826638** |

The disposition-adjusted score is higher than the raw artifact score purely
because the denominator shrinks. The numerator (49 correct judgments) is
identical (~10.4% / 49/473). This baseline is the **Wave 0 Option A historical
harness-artifact anchor only** — it is pre-parity, prior to the T14/T15
full-corpus evaluation run. It MUST NOT be treated as a post-parity T15 baseline.

## Per-Category Score Artifacts (from C1-C score JSON)

| Category | Rows | Correct | Official Accuracy |
|---|---|---|---|
| ABS | 30 | 16 | 0.5333 |
| IE-assistant | 56 | 7 | 0.1250 |
| IE-preference | 30 | 5 | 0.1667 |
| IE-user | 64 | 7 | 0.1094 |
| KU | 72 | 10 | 0.1389 |
| MR | 121 | 3 | 0.0248 |
| TR | 127 | 1 | 0.0079 |

ABS official accuracy: **16 / 30 = 0.5333** (verified end-to-end in C1-C rerun).

## Invalid-Ciphertext Exclusion Detail (C1-A Carry-Forward)

27 rows errored with `Invalid ciphertext: decryption failed (wrong key or
corrupted data)` during retrieval decryption. Per-question attribution is
structurally impossible — the exception fires at `store.py:903` before the
async retrieval log write is scheduled (`retrieval.py:696`), so none of the
27 rows wrote a `retrieval_log` entry.

**Question IDs (27):**
`e47becba`, `118b2229`, `51a45a95`, `3b6f954b`, `dccbc061`, `b320f3f8`,
`c14c00dd`, `f4f1d8a4_abs`, `2788b940`, `gpt4_ab202e7f`, `gpt4_2f91af09`,
`8a2466db`, `4adc0475`, `0ea62687`, `60159905`, `gpt4_ec93e27f`, `982b5123`,
`gpt4_4cd9eba1`, `gpt4_2f56ae70`, `gpt4_5438fa52`, `ce6d2d27`,
`6aeb4375_abs`, `8aef76bc`, `71a3fd6b`, `6222b6eb`, `352ab8bd`, `28bcfaac`

These 27 IDs are identical to those in C1-A evidence. No new rows were added.
Key/config recovery is not possible without `orchestrator/memory/` changes
(prohibited under N1).

## NoneType.strip Fix Verification (7401057b — C1-A)

The single-row `NoneType.strip` defect (`question_id=7401057b`) was fixed
via a one-line null guard in `tests/longmemeval/evaluate.py:405-407`. C1-C
rerun confirms the fix:

| Field | Value |
|---|---|
| question_id | 7401057b |
| error | null |
| hypothesis | non-empty |
| memories_used | 5 |
| judgment | incorrect |
| none_type_strip_present | false |

## Legacy Gate Policy (Superseded)

The following old gates are **NOT final pass/fail criteria** under Option A.
They were pre-data diagnostic sanity bounds. User Option A explicitly
supersedes them.

| Old Gate | Raw Value | Would Pass? | Final Gate? |
|---|---|---|---|
| `aggregate > 0.15` | 0.098 (49/500) | No | **No — superseded** |
| `success_count >= 495` | 473 | No | **No — superseded by bounded exclusions** |
| Per-category floor gates | Multiple fail | No | **No — superseded** |

The raw official score of **49/500 = 0.098** is preserved in this document
and the evidence JSON. It is not hidden, rounded, or adjusted upward.
The Option A disposition-adjusted figure of **49/473 = 0.1036 (~10.4%)** is the
**Wave 0 Option A harness-artifact anchor only** — a pre-parity benchmark
artifact that is not a valid T15 baseline. Per `tests/benchmark_results/harness_parity_baseline_decision.md`
(generated 2026-05-06), T15 is **HALT — baseline undeterminable** because
the full haystack-bearing LongMemEval_S corpus is unavailable and T14 produced
no completed run. No numeric T15 baseline exists.

## Memories Used (C1-C Rerun)

| Statistic | All Rows | Reported Rows Only |
|---|---|---|
| Min | 0.0 | 0.0 |
| Median | 5.0 | 5.0 |
| Max | 5.0 | 5.0 |
| Mean | 4.244 | 4.486 |
| Count | 500 | 473 |

## No Re-ingestion, No Memory Changes

- No re-ingestion was performed during C1-C.
- No `orchestrator/memory/**` files were modified.
- `git diff -- orchestrator/memory/` confirmed clean throughout C1-C.

## Rerun Artifacts

| Artifact | Path |
|---|---|
| Checkpoint | `tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_checkpoint.json` |
| Results | `tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_results.jsonl` |
| Score | `tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_score.json` |

## Dispositions Chain

```
C1-A (bounded exclusions + null guard)
  └─▶ C1-B (ABS category wiring fix)
        └─▶ C1-C (canonical rerun — verify A + B end-to-end)
              └─▶ C1-D (baseline locked here)
```

- **C1-A:** 27 invalid-ciphertext rows → bounded error-class exclusion; 7401057b → null guard fixed
- **C1-B:** `_abs` suffix detection added to `evaluate.py` and `runner.py` → ABS bucket correctly populated (30 rows, 16/30 = 0.5333)
- **C1-C:** Resumed evaluate from checkpoint (484 completed), finished final 16, scored full corpus — verified A and B end-to-end
- **C1-D:** Baseline locked; no relitigation of superseded gates; ready for E5–E9
