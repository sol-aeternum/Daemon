# Wave 0 Diagnostic Revised

**Date**: 2026-04-23
**Purpose**: Revised diagnosis of Wave 0 variance results with updated interpretation

---

## Revised Diagnosis

### What is the 6pp Spread?

ABL-1 (seed=42, BENCHMARK_MODE=1, fingerprint bypass, all fixes ON) scored **28.0%** (14/50).
ABL-2 (identical config) scored **34.0%** (17/50).
**Observed spread: 6pp** (ABL-1 vs ABL-2, two independent runs).

### Is the 6pp a Genuine Determism Failure, Sampling Noise, Provider Routing Variance, Model Aliasing, or a Mix?

**Answer: Mix — sampling noise is the primary explanation at the corpus level, with provider routing variance and model aliasing as complicating background factors.**

Detailed decomposition:

| Component | Estimated Contribution | Confidence | Basis |
|-----------|----------------------|------------|-------|
| Sampling noise (binomial SE at n=50, p~0.28–0.34) | ~6.35–6.70pp per run | High | Single-run SE ranges from 6.35pp to 6.70pp |
| Two-run 95% spread envelope | ~±18.1pp | High | Simple sampling noise model |
| Provider routing variance | Unquantified | Medium | No `extra_body` contract in place; alias-based routing |
| Model aliasing (alias vs dated snapshot) | Unquantified | Medium | OpenRouter aliases resolved at call time |
| Embedding contribution (from `wave0_variance_attribution_results.md`) | ~6pp | Medium | ABL-1 vs ABL-2 spread within embedding layer |
| Answer temperature contribution | ~4pp | Low-Medium | Inferred from delta |

**Key point**: The observed 6pp spread does **NOT** exceed the simple sampling-noise envelope (~±18.1pp for two runs). It is fully consistent with binomial sampling variance at n=50.

### What Fraction is Attributable to Each Cause, With What Confidence?

| Cause | Fraction of 6pp Spread | Confidence |
|-------|----------------------|-------------|
| Sampling noise | ~100% (6pp is within the sampling envelope) | High |
| Provider routing variance | Unquantified; non-zero given TODO 14 defects | Medium |
| Model aliasing | Unquantified; non-zero given alias usage | Medium |
| Embedding nondeterminism (voyage-4-lite query mode) | Near-zero contribution to 6pp | Medium (query mode drift is 0.000008 mean cosine deviation) |

**Important**: The 6pp ABL-1 vs ABL-2 spread is a **two-run spread within one configuration**, not a triple-run spread across configurations. This makes it directly comparable to the sampling envelope.

The embedding contribution of ~6pp cited in `wave0_variance_attribution_results.md` was measured across ABL-1 vs ABL-2, which is the same 6pp observed here. That is: the embedding contribution accounts for the spread, but the spread itself is within sampling noise.

### What is the Correct Gate Threshold for the Corpus Actually Being Used?

- **Corpus**: `tests/benchmark_longmemeval/fixtures/dev_subset.json` — a 50-case stratified dev subset
- **Single-run binomial SE** at observed p~0.28: ~6.35pp
- **Single-run binomial SE** at observed p~0.34: ~6.70pp
- **Two-run 95% spread envelope**: ~±18.1pp
- **Hard <=3pp gate**: **Not realistically achievable at n=50** if treated as a hard threshold on this subset

**Correct gate threshold** should be adjusted to be sample-size-appropriate. For a two-run comparison at n=50:
- A <=3pp gate is within sampling noise and cannot be reliably used as a pass/fail criterion
- The minimum detectable effect size at 80% power for n=50 is substantially larger than 3pp

### Is Wave 0 Actually Blocked or Passed-Against-Wrong-Gate?

**Answer: Blocked on a wrong gate, not on the underlying question.**

The original Wave 0 gate (TODO 14 + ABL-1/ABL-2 within <=3pp) was:
1. Defined against a corpus (n=50) where a 3pp threshold is not statistically achievable
2. Applied to a TODO 14 implementation that was **defective** — missing `extra_body`, using alias slugs, no dated snapshots

The **underlying question** (is the system deterministic enough for Wave 1?) cannot be answered because:
1. The TODO 14 controls that would enable deterministic routing are not in place
2. The gate threshold is inappropriate for the corpus size
3. The embedding layer (voyage-4-lite query mode) shows non-determinism (0.000008 mean cosine deviation on 9/45 pairs) that is measurable but mechanistically insufficient to explain a 6pp score spread

**Wave 0 is blocked on implementation defects (TODO 14 not correctly done) and an inappropriate gate threshold, not on a definitive determinism failure.**

---

## Evidence Summary

| Fact | Source |
|------|--------|
| ABL-1 = 28.0% (14/50), ABL-2 = 34.0% (17/50) | Attribution results |
| 50-case dev subset | Fixture audit |
| SE ~6.35–6.70pp at observed p | Binomial calculation |
| Two-run 95% envelope ~±18.1pp | Binomial calculation |
| TODO 14: missing `extra_body` contract | Call site audit |
| TODO 14: alias slugs, not full endpoint slugs | Model string audit |
| TODO 14: model strings are aliases, not dated snapshots | OpenRouter docs interpretation |
| voyage-4-lite query mode: 9/45 non-identical pairs, mean cosine 0.999992 | `wave0_voyage_drift_test.md` |
| Embedding ~6pp, answer temp ~4pp | `wave0_variance_attribution_results.md` |

---

## Revised Conclusion

The 6pp ABL-1 vs ABL-2 spread is **fully consistent with sampling noise** at n=50 and does not constitute a definitive non-determinism failure. The TODO 14 controls that would make the system deterministic are not in place. The <=3pp gate is inappropriate for the corpus size. Wave 0 is blocked on wrong gate + incomplete implementation.

---

## Exactly One Recommended Next Action

**(ii) Fix TODO 14 properly (full endpoint slug + dated model snapshot), remove DISABLE_BM_FINGERPRINT_FAIL_FAST, re-run ABL-1/ABL-2**

**Rationale**: The only way to convert Wave 0 from "blocked on wrong gate + incomplete implementation" to a definitive pass/fail is to correctly implement TODO 14, use the correct gate threshold, and re-run. Options (iii) and (iv) (caches) do not address the underlying provider routing and model aliasing issues. Option (v) (full-corpus validation) cannot proceed until TODO 14 is correctly fixed. Option (vi) (accept blocked verdict) leaves the implementation defect unaddressed. Option (i) (re-gate with appropriate threshold) is a necessary but insufficient step — it addresses the gate but not the implementation defect.

**What "fix TODO 14 properly" entails**:
1. Use full endpoint slugs (not aliases like `openrouter/openai/gpt-4o-mini`)
2. Use dated model snapshots where available
3. Add `extra_body={"provider": {"order": [...], "allow_fallbacks": false}}` to all benchmark-path LLM call sites
4. Remove `DISABLE_BM_FINGERPRINT_FAIL_FAST` override from environment if present
5. Re-run ABL-1 and ABL-2 with these controls in place
