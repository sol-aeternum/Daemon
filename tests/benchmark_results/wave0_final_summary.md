# Wave 0 Final Summary

## Original Wave 0 plan

Wave 0 originally aimed to lock a reproducible LongMemEval baseline, close determinism and recovery gaps, and carry one approved full-corpus score forward as the anchor for post-Wave-0 work. The plan assumed benchmark results could stand in for production-memory behavior once routing and benchmark hygiene were tightened. Path A tested that assumption directly by forcing the benchmark answer path to use the production-style prompt contract rather than the old thin benchmark-only prompt.

## What actually happened

Path A succeeded as an alignment exercise and as a baseline-establishment exercise. The harness now builds a production-style system prompt, sends a `[system, user]` pair, and evaluates the full 500-question corpus from recovered populated state. The Option A corrected run produced valid artifacts: 500 attempted, 473 successful answer-model responses, 49 correct judgments, and per-category breakdowns. The Option A disposition-adjusted baseline is **49 / 473 = 0.10359408033826638**.

## Option A accepted baseline

Wave 0 closes under **User Option A** with the first accepted production-aligned LongMemEval_S baseline. The raw artifact score (49 / 500 = 0.098) is preserved and not hidden. The Option A disposition-adjusted baseline (49 / 473 = 0.1036) is the accepted production-aligned value, where 27 invalid-ciphertext rows are analytically excluded as a bounded error-class from C1-A carry-forward.

### Raw artifact numbers

| Field | Value |
|---|---|
| Attempted | 500 |
| Success count | 473 |
| Error count | 27 |
| Correct | 49 |
| Partially correct | 24 |
| Incorrect | 427 |
| **Raw artifact score** | **49 / 500 = 0.098** |

**success_count=473, error_count=27, correct=49, partial=24, incorrect=427**

### Option A disposition-adjusted baseline

| Field | Value |
|---|---|
| Excluded rows | 27 invalid-ciphertext (bounded error-class exclusion) |
| Denominator | 473 |
| Correct (unchanged) | 49 |
| **Disposition-adjusted baseline** | **49 / 473 = 0.10359408033826638** |

**Provider/model: openai / gpt-4o-2024-08-06 · seed=42 · temperature=0.0 · Artifact: tests/benchmark_results/wave0_closure_option_a_rerun/**

### Per-category breakdown

| Category | Correct | Total | Accuracy |
|---|---|---|---|
| ABS | 16 | 30 | 0.5333 |
| IE-assistant | 7 | 56 | 0.1250 |
| IE-preference | 5 | 30 | 0.1667 |
| IE-user | 7 | 64 | 0.1094 |
| KU | 10 | 72 | 0.1389 |
| MR | 3 | 121 | 0.0248 |
| TR | 1 | 127 | 0.0079 |

## Infrastructure defects fixed and surfaced

Wave 0 and Path A closed or surfaced benchmark-infrastructure defects and control gaps:

- **Harness prompt shape**: corrected from thin bullet-list to production-style system prompt
- **Production-style prompt assembly**: `build_assembled_system_prompt()` / `assemble_system_prompt()` now used in benchmark path
- **Single-question targeting**: canonical runner supports `--question-id` targeting mode
- **Prompt/fingerprint audit metadata**: captured in `answer_prompt_metadata` per result row
- **Checkpoint leakage prevention**: targeted evaluate no longer inherits full-corpus checkpoint state
- **Deterministic run metadata**: seed=42, temperature=0.0, pinned endpoint slug, fingerprint logging
- **Config pinning**: bare provider slugs (`openai`) in `provider.order`; model strings with `openrouter/` prefix
- **7401057b null-content guard**: one-line null guard at `evaluate.py:405-407`
- **ABS category wiring**: `_abs` suffix detection at `evaluate.py:829-833` and `runner.py:1716-1720`
- **Invalid-ciphertext bounded storage anomaly**: 27-row error-class exclusion; per-question attribution structurally impossible
- **Old gate/variance-contract mismatch surfaced**: triple-run `≤3pp` gate is dead; variance is bounded single-run point estimate

## Architectural finding

The critical architectural finding is **benchmark-production injection decoupling**. That split was deliberate: LongMemEval had no real conversation rows or message history, while production `build_memory_context()` depends on exactly those inputs, so the benchmark evolved a thin benchmark-only prompt path that bypassed production assembly. Path A resolved this structurally by implementing production-style prompt assembly in the benchmark harness. As a result, historical benchmark-only scores (67.8, 81.1, 28-34, 22.4) are **superseded for production-memory claims**. They remain historical artifacts, not evidence of how the production-aligned prompt path performs.

## Disposition chain

```
C1-A (bounded exclusions + null guard)
  └─▶ C1-B (ABS category wiring fix)
        └─▶ C1-C (canonical rerun — verify A + B end-to-end)
              └─▶ C1-D (baseline locked)
                    └─▶ E5 (sanity structural assessment — pass)
                          └─▶ E6 (closure memo Section 14)
                                └─▶ E7 (baseline ledger updated)
                                      └─▶ E8 (roadmap variance callout)
                                            └─▶ E9 (final summary + baseline commit + pre-wave-1 tag)
```

## Variance contract

The accepted Wave 0 baseline is a **single-run point estimate with bounded variance** — not zero-variance and not triple-run-locked. Variance sources: OpenAI token/fingerprint drift (seed=42, temperature=0.0 where fingerprints are stable), Voyage AI retrieval/embedding nondeterminism, and arq/background job timing. W1+ gates require redesign before W1 commissions under the new semantics.

## W1+ follow-up items (not Wave 0 blockers)

| Item | Classification | Status |
|---|---|---|
| Production guardrail injection | W1+ (N1 deferral) | Deferred |
| Invalid-ciphertext storage anomaly | W1+ (requires memory code changes) | Bounded analytically; W1+ resolution |
| Future W1 gate redesign | W1+ (structural) | Required before W1 commissions |

## First production-aligned baseline

**first production-aligned LongMemEval baseline** — This is the first accepted production-aligned LongMemEval_S baseline under User Option A. Historical benchmark-harness scores and the earlier blocked 0.0 artifact (`wave0_full_corpus_aligned/longmemeval_score.json`) are superseded for W1 comparisons.
