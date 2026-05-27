# Wave 0 — Diagnostic Mini-Task Memo

**Date:** 2026-04-23
**File:** `tests/benchmark_results/wave0_diagnostic_mini_task.md`
**Scope:** Diagnostic summary of TODO 7, TODO 15, residual interpretation, contamination assessment, and recommendation
**Status:** Internal memo only — no production code changes

---

## 1. TODO 7 — Implementation Summary and Voyage Determinism Verdict

### What was implemented

TODO 7 was implemented in `tests/longmemeval/ingest.py`. The changes were:

1. **`poll_extraction_complete()` deprecated** (lines 227–276): The polling function is no longer called in the canonical benchmark path. Its docstring explicitly marks it deprecated with the note that `process_extraction()` is an inline await that guarantees completion before returning, making the post-call poll a redundant second barrier.

2. **Inline `process_extraction()` call** (lines 336–341): `ingest_session()` now awaits `process_extraction()` inline rather than enqueueing for background processing and then polling for completion. This removes the race condition window between enqueue and poll.

3. **Explicit `ExtractionOutcome` accounting** (lines 45–59, 332–347): An `ExtractionOutcome` enum was introduced with four explicit states — `COMPLETED`, `EMPTY`, `TIMED_OUT`, `ERRORED` — replacing implicit error accounting. Outcome counts are aggregated in `run_ingestion()` and logged.

### Voyage determinism verdict

From `tests/benchmark_results/wave0_embedding_determinism_refs.md`:

- **No seed parameter** exists in the Voyage AI embedding API.
- **No `system_fingerprint` equivalent** is returned.
- Community evidence confirms conditional nondeterminism: repeated identical calls can produce different vectors, with cosine similarity as low as ~0.9924 observed in production.
- Voyage embeddings are classified as the **highest-risk component** for reproducibility because there is no API-level mechanism to enforce or verify determinism.

The verdict: **Voyage embedding nondeterminism is a structural, provider-level constraint — not a code bug. It cannot be resolved without either (a) a provider change or (b) an embedding cache layer.**

---

## 2. TODO 15 — Status: NOT Complete

### Required artifacts that do not exist

TODO 15 in the Wave 0 plan (`wave0_validation_run_{1,2,3}.json`, `wave0_validation_summary.md`, `wave0_postmortem.md`) has **not been completed**. The required validation artifacts for Wave 0's post-wave0 reproducibility check do not exist in `tests/benchmark_results/`.

This is a **no-change-needed closure is not valid** situation: the validation run artifacts that TODO 15 requires are absent. The task cannot be marked complete without them.

### Disambiguation: unrelated `task15_*` artifacts

The files `task15_failure_analysis.json`, `task15_mr_comparison.md`, and `task15_dream_quality_review.md` are **separate investigations** from a different context. They share the "task15" name but are not the Wave 0 TODO 15 validation artifacts. Key distinguishing facts:

- **`task15_dream_quality_review.md`** concluded: *"Dream quality passes after the prompt-only calibration fix. No retrieval-weight, contradiction, entity, or dream-inclusion changes were needed."* This was a prompt-only calibration for dream confidence bands, unrelated to Wave 0 reproducibility validation.
- **`task15_mr_comparison.md`** compares standard vs. reflect paths on a 10-question subset, also unrelated to Wave 0 reproducibility gates.
- **`task15_failure_analysis.json`** analyzes per-question judgment quality, also unrelated.

These artifacts do not satisfy Wave 0 TODO 15. They must not be conflated with it.

---

## 3. The 6pp Residual — Correct Answer: (a)

### The question

The Wave 0 variance attribution design (`wave0_variance_attribution_design.md`, §5.1) defines residual variance as:

> *"Residual variance = spread that remains after Phase 1 (embedding isolation) or Phase 2 (full isolation), after accounting for identified sources."*

Three candidate interpretations were offered:

- **(a)** the post-wave0 residual spread between ABL-1 and ABL-2 runs
- **(b)** the pre-wave0 combined spread between run1 and run2
- **(c)** embedding nondeterminism co-varying with another pipeline component

### Direct quotes establishing the answer

**From `wave0_variance_attribution_results.md` §2.2:**

> "Post-Wave0 Deterministic Residual (ABL-1 vs ABL-2): [table showing] ABL-1: **28.0%**, ABL-2: **34.0%**, **Residual Spread: 6.0pp**"

**From `wave0_variance_attribution_results.md` §2.4:**

> "Pre-wave0 spread: 10.0pp [vs] Post-wave0 spread: 6.0pp [indicating] The wave0 fixes reduced observed spread from 10pp to 6pp — a 4pp reduction attributable to answer temperature locking and seed-based extraction determinism. The remaining 6pp is **embedding nondeterminism**."

**From `wave0_oracle_checkpoint_1.md` §26:**

> "The key current measurement is the post-wave0 residual spread between the two deterministic runs: **6.0pp** (ABL-1 28.0% vs ABL-2 34.0%). That is directly measured from completed artifacts, not inferred."

**From `wave0_oracle_checkpoint_1.md` §29:**

> "Wave 0 validation later requires aggregate spread **<=3pp**. A directly measured **6pp** swing between two back-to-back deterministic runs is already above that gate."

**From `wave0_variance_attribution_design.md` §5.2:**

> "Oracle review is **mandatory** before validation if: 1. **Residual spread > 3pp** after full attribution..."

### Conclusion

The 6pp residual is **(a) — the post-wave0 residual spread between ABL-1 and ABL-2 runs**, directly measured at 28.0% vs 34.0%. It is not (b) because the pre-wave0 spread was 10pp and was reduced to 6pp by wave0 fixes. It is not (c) because the Oracle checkpoint identifies the residual as embedding nondeterminism in isolation (the dominant source), not as a co-variation with another pipeline component.

---

## 4. Voyage Drift Test — Split Result

**⚠️ FLAG: Split result in `wave0_voyage_drift_test.md`**

| Mode | Model | input_type | Cosine Min | Cosine Mean | Byte-identical pairs |
|---|---|---|---|---|---|
| document | voyage-4-large | document | **1.0** | **1.0** | **45/45 YES** |
| query | voyage-4-lite | query | 0.999958 | 0.999992 | **36/45 NO** |

**voyage-4-large (document mode):** ALL 45/45 pairwise outputs are byte-identical. Full determinism confirmed for this input and model.

**voyage-4-lite (query mode):** 9/45 pairs are NOT byte-identical. Embedding drift confirmed. Cosine range: [0.999958, 1.0], mean=0.999992.

This is a **split result**: document embeddings are deterministic, but query embeddings are not. Daemon's pipeline uses `voyage-4-lite` for query embeddings (at retrieval time), which means the drift affects the retrieval step — the most consequential point in the pipeline, since different retrieved memory sets lead directly to different answers.

The attribution report's "embedding nondeterminism" diagnosis applies specifically to `voyage-4-lite` query mode, consistent with the drift test findings.

---

## 5. ABL-1/ABL-2 Isolation vs. Contamination Assessment

### What ABL-1 and ABL-2 share

Both runs used:
- `BENCHMARK_MODE=1`
- `seed=42`
- `DISABLE_BM_FINGERPRINT_FAIL_FAST=1` (bypasses fingerprint fail-fast on extraction and dedup)
- Identical dataset and configuration

### Why they are NOT perfectly embedding-isolated

From `wave0_variance_attribution_results.md` §3.4:

> "Voyage AI's `voyage-4-lite` does not support `seed` or `system_fingerprint` parameters. Even with `seed=42` set on the LLM calls, the embedding step itself produces slightly different vectors for identical text across runs."

> "**Workaround applied:** `DISABLE_BM_FINGERPRINT_FAIL_FAST=1` bypasses fingerprint checking in extraction and dedup. This allows runs to complete but does not eliminate embedding variance."

From `wave0_variance_attribution_results.md` §3.5:

> "During ABL-1 execution: `BenchmarkSamplingError: Benchmark fingerprint drift in extraction: expected 'fp_e61ea1dda4', got 'fp_255abcd69b'`"

> "The OpenRouter/OpenAI provider returned different `system_fingerprint` values for identical calls with seed=42. **Seed-based reproducibility is best-effort, not guaranteed.**"

### Contamination verdict

**Mixed-signal contamination is possible.** ABL-1/ABL-2 are not perfectly embedding-isolated because:

1. **Extraction fingerprint drift was bypassed**, not eliminated. The `DISABLE_BM_FINGERPRINT_FAIL_FAST=1` flag allows the run to complete despite fingerprint mismatches in extraction, but those mismatches mean the LLM output (and therefore the extracted facts) may differ between runs even with identical seed.

2. **Dedup fingerprint drift was also bypassed.** The same bypass applies to dedup, so deduplication decisions may differ between runs independently of embeddings.

3. **The 11-question bidirectional disagreement** (22% disagreement rate) is consistent with embedding variance, but the bypass of fingerprint fail-fast means a component of the disagreement could be extraction variance rather than pure embedding drift.

**Bottom line:** Embeddings are the dominant measured source, but ABL-1/ABL-2 do not constitute a perfectly clean ablation isolating only embedding effects. The 6pp is real and embedding-driven, but not exclusively so.

---

## 6. Ranked Causes

| Rank | Source | Contribution | Confidence | Basis |
|---|---|---|---|---|
| 1 | **Embedding nondeterminism** (`voyage-4-lite` query mode) | ~6pp | High (directly measured) | ABL-1 vs ABL-2 residual with all deterministic fixes ON |
| 2 | **Answer temperature = 0.7** | ~4pp | Medium (inferred) | Delta between pre-wave0 10pp and post-wave0 6pp spreads |
| 3 | **Judge/Extraction** | 0pp | High | Extraction outcome counts statistically identical between baseline runs |
| 4 | **Fingerprint co-variation** (bypassed) | Unknown | Low | Not isolated; bypass flag prevents clean attribution |
| **Total** | — | **~10pp** | — | Accounts for observed pre-wave0 spread |

---

## 7. Mitigation Options

### (a) Embedding fixture cache keyed by SHA256(input_text + model + input_type)

**Description:** Pre-compute and cache all document and query embeddings for the benchmark dataset before running evaluation. On each evaluation run, replace live API calls with cached vectors.

**Pros:**
- Eliminates embedding variance entirely for repeated runs
- No changes to production embedding code required
- Enables perfect reproducibility of retrieval decisions across runs

**Cons:**
- Would invalidate the ability to detect semantic drift over time (cached embeddings become stale)
- Does not fix the root cause (non-deterministic provider)
- Requires pipeline modification to support cache lookup and invalidation
- Would need to be re-run if dataset or embedding model changes
- If `wave0_validation_run_{1,2,3}.json` artifacts were generated with live embeddings, they cannot be compared against cached runs

**Verdict:** Viable for reproducibility but is a workaround, not a fix.

### (b) Targeted fix to identified non-embedding co-variation

**Description:** Address the ~4pp answer-temperature contribution by locking answer temperature to 0.0 (instead of 0.7) and/or running ABL-3/ABL-4 to isolate answer-temperature and judge/extraction contributions independently.

**Pros:**
- Addresses a real, measurable source of variance
- ABL-3/ABL-4 would provide independent confirmation of attribution
- Does not require embedding cache infrastructure

**Cons:**
- Does not eliminate embedding nondeterminism (~6pp would remain)
- Temperature=0.0 may produce lower-quality answers (less diverse reasoning)
- Would require a new validation run set to assess residual

**Verdict:** Worth running ABL-3/ABL-4 for confirmation, but does not achieve the <=3pp gate alone.

### (c) Accept blocked verdict and halt Wave 0

**Description:** Accept the Oracle no-go. The 6pp residual exceeds the <=3pp gate. The embedding nondeterminism is structural and cannot be fixed without a provider change. Halt further Wave 0 work and escalate to the Orchestrator for a decision on whether to (i) switch embedding providers, (ii) relax the reproducibility gate, or (iii) redesign the benchmark.

**Pros:**
- Honest about the irreducible constraint
- Avoids spending cycles on mitigations that cannot achieve the gate

**Cons:**
- Wave 0 reproducibility is not achieved
- Downstream validation cannot proceed under the current plan

**Verdict:** Accurate assessment of the current state, but not a path to completion.

---

## 8. Recommendation

**Recommendation: (b) Targeted fix — run ABL-3 and ABL-4 for independent confirmation, and evaluate embedding cache (option a) as the path to achieving the <=3pp gate.**

Rationale:

1. The 6pp residual is real and directly measured, but ABL-3/ABL-4 remain unexecuted. Running them would provide independent confirmation of whether the ~4pp answer-temperature component can be reduced further, and whether the 6pp embedding residual is fully isolated or partially contaminated by bypassed fingerprint drift.

2. If ABL-3/ABL-4 confirm the attribution, the path to the <=3pp gate runs through option (a) — an embedding cache keyed by `SHA256(input_text + model + input_type)`. This is a benchmark infrastructure change, not a production code change, and does not require modifying `orchestrator/memory/`.

3. **Do not conflate the unrelated `task15_*` artifacts with Wave 0 TODO 15.** The dream quality, MR comparison, and failure analysis investigations are separate workstreams. Wave 0 TODO 15 is not complete and requires validation artifacts that do not exist.

4. The split voyage drift result (deterministic `voyage-4-large` document mode vs. non-deterministic `voyage-4-lite` query mode) narrows the risk to the retrieval step specifically. An embedding cache would eliminate retrieval variance; the extraction and judge steps would remain at best-effort reproducibility.

---

## Appendix: Key Quotes Preserved

**On residual definition:**
> "Residual variance = spread that remains after Phase 1 (embedding isolation) or Phase 2 (full isolation), after accounting for identified sources." — `wave0_variance_attribution_design.md` §5.1

**On measured 6pp:**
> "A directly measured **6pp** swing between two back-to-back deterministic runs is already above that gate." — `wave0_oracle_checkpoint_1.md` §29

**On blocking:**
> "Task 15 must not proceed because the current measured residual (**6pp**) exceeds the later Wave 0 validation gate (**<=3pp**)." — `wave0_oracle_checkpoint_1.md` §47

**On embedding nondeterminism as structural:**
> "The remaining 6pp is **embedding nondeterminism** (voyage-4-lite has no seed/fingerprint support)." — `wave0_variance_attribution_results.md` §2.4

---

*Memo generated from verified artifacts only. No production code was read or modified.*
