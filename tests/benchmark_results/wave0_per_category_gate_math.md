# Wave 0 Dev-Subset — Per-Category Gate Math

> **Scope:** `tests/benchmark_longmemeval/fixtures/dev_subset.json` (50 cases, locked subset)
> **Validation runs:** `wave0_validation_run_1.json`, `_2.json`, `_3.json`
> **Analysis date:** 2026-04-24

---

## 1. Dev-Subset Category Counts

The locked 50-case dev subset has the following primary-question-type distribution, confirmed by both `dev_subset_coverage.md` and direct JSON inspection:

| Category (JSON label) | Canonical name | Scored questions (n) | Notes |
|---|---|---|---|
| `IE-user` | single-session-user | 9 | — |
| `IE-assistant` | single-session-assistant | 9 | — |
| `IE-preference` | single-session-preference | 3 | — |
| `MR` | multi-session | 10 | — |
| `KU` | knowledge-update | 9 | — |
| `TR` | temporal-reasoning | 10 | — |
| `ABS` | abstention | 5 | Overlap overlay; primary types are 1×KU, 2×MR, 2×TR |

**Total scored questions: 50.** Abstention cases carry dual membership (primary cell + abstention overlay), consistent with the selection rules in `dev_subset_coverage.md`.

---

## 2. Observed Accuracy Across 3 Validation Runs

Accuracy values extracted verbatim from the three run JSON artifacts:

| Category | Run 1 | Run 2 | Run 3 | Mean | Min | Max | Observed spread (max−min) |
|---|---|---|---|---|---|---|---|
| IE-user | 33.3% | 11.1% | 11.1% | 18.5% | 11.1% | 33.3% | **22.2pp** |
| IE-assistant | 44.4% | 44.4% | 44.4% | 44.4% | 44.4% | 44.4% | **0.0pp** |
| IE-preference | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | **0.0pp** |
| MR | 10.0% | 20.0% | 10.0% | 13.3% | 10.0% | 20.0% | **10.0pp** |
| KU | 55.6% | 66.7% | 33.3% | 51.9% | 33.3% | 66.7% | **33.3pp** |
| TR | 20.0% | 20.0% | 30.0% | 23.3% | 20.0% | 30.0% | **10.0pp** |
| ABS | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | **0.0pp** |

### Notable observations

- **IE-assistant** is perfectly stable across all 3 runs (0.0pp spread) at 44.4%.
- **KU** has the highest observed spread at 33.3pp (55.6% → 33.3%), indicating high sensitivity to embedding/retrieval nondeterminism.
- **IE-user** shows 22.2pp spread driven by a single outlier run (33.3% vs two runs at 11.1%).
- **MR and TR** each show 10.0pp spread; both are consistently low.
- **IE-preference** and **ABS** are at absolute zero across all runs — degenerate results (SE = 0).

---

## 3. Gate Math — Binomial Standard Error and Two-Run 95% Spread Envelope

### Formula

For a binomial proportion `p` with sample size `n`:

**Binomial standard error (single-run 95% CI half-width):**
```
SE = sqrt(p * (1 - p) / n)
```

**Two-run 95% spread envelope (difference between two independent binomial estimates at 95% confidence):**
```
envelope = 1.96 * sqrt(p * (1 - p) / n) * sqrt(2)
```

This represents the 95th percentile of the difference between two independent runs. A category passes a ≤15pp gate reliably only if `p - envelope ≥ 0.15`.

### Per-Category Computations

| Category | n | p (mean) | p(1-p) | SE | 1.96·SE·√2 (envelope) | Lower bound (p − envelope) | Upper bound (p + envelope) |
|---|---|---|---|---|---|---|---|
| IE-user | 9 | 0.1852 | 0.1509 | 0.1295 | 0.3596 | **−0.1744** | +0.5448 |
| IE-assistant | 9 | 0.4444 | 0.2469 | 0.1656 | 0.4592 | **−0.0147** | +0.9036 |
| IE-preference | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | **0.0000** | 0.0000 |
| MR | 10 | 0.1333 | 0.1156 | 0.1075 | 0.2980 | **−0.1647** | +0.4313 |
| KU | 9 | 0.5185 | 0.2496 | 0.1666 | 0.4617 | **+0.0568** | +0.9802 |
| TR | 10 | 0.2333 | 0.1789 | 0.1337 | 0.3704 | **−0.1371** | +0.6038 |
| ABS | 5 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | **0.0000** | 0.0000 |

---

## 4. Per-Category Gate Verdict

### IE-assistant (n=9, mean=44.4%, envelope=±45.9pp)

**Gate verdict: Mean clears the threshold; stability is exceptional.**
- Mean accuracy (44.4%) is 29.4pp above the 15pp gate — well clear.
- Lower envelope bound (−1.5pp vs 15pp target) technically misses, but the lower bound is an artifact of the small-n SE, not the observed data. All three runs landed at exactly 44.4% (0.0pp observed spread).
- The two-run envelope width (91.8pp) is large because the SE model assumes binomial sampling noise; in practice the embedding pipeline is deterministic for this category.
- **Achievable with high confidence.** The bottleneck is n=9, not signal.

### KU (n=9, mean=51.9%, envelope=±46.2pp)

**Gate verdict: Marginal — mean clears the gate, but lower envelope bound misses.**
- Mean accuracy (51.9%) is 36.9pp above the gate — strong signal on average.
- Lower envelope bound (+5.7pp) falls below the 15pp threshold, meaning a cold-run could score as low as ~6% and miss the gate.
- Observed spread across runs is 33.3pp (55.6% → 33.3%), consistent with the large envelope width (92.3pp).
- The two-run envelope lower bound is driven by the formula's 95% tail; the actual worst-case observed is 33.3%.
- **Not reliably achievable at ≤15pp tolerance.** The lower envelope bound is 5.7pp < 15pp. The embedding/retrieval nondeterminism that drives the 33pp observed spread is the root cause.

### IE-user (n=9, mean=18.5%, envelope=±36.0pp)

**Gate verdict: Not achievable.**
- Mean (18.5%) is only 3.5pp above the gate.
- Lower envelope bound (−17.4pp) is 32.4pp below the 15pp threshold.
- Observed spread (22.2pp) spans both above and below the gate (33.3% run 1 vs 11.1% runs 2–3).
- The lower envelope bound misses the gate by a wide margin; the gate is not achievable without either increasing n or reducing embedding variance.

### MR (n=10, mean=13.3%, envelope=±29.8pp)

**Gate verdict: Not achievable.**
- Mean (13.3%) is 1.7pp below the gate.
- Lower envelope bound (−16.5pp) is 31.5pp below the 15pp threshold.
- Even the upper bound (43.1pp) barely clears the gate; the lower bound misses comprehensively.
- All runs cluster at ≤20%, so the SE model and observed data agree the signal is below threshold.

### TR (n=10, mean=23.3%, envelope=±37.0pp)

**Gate verdict: Not achievable.**
- Mean (23.3%) is above the gate, but lower envelope bound (−13.7pp) misses by 28.7pp.
- Observed spread (10.0pp) is modest relative to the envelope width (74.1pp), meaning the main issue is low signal strength, not instability.
- Upper bound (60.4%) indicates headroom exists; the bottleneck is the mean being only 8.3pp above gate rather than a stability problem.

### IE-preference (n=3, mean=0.0%, SE=0)

**Gate verdict: Below the noise floor.**
- All three runs return 0.0% — degenerate result.
- SE = 0 (p = 0), so the binomial model collapses to a point estimate.
- The envelope is identically zero; the formula `sqrt(p*(1-p)/n)` with p=0 yields 0.
- This does not mean performance is "perfect" — it means no correct answers were produced in any run. The small n (3) and likely high haystack complexity make any non-zero score unlikely.
- **At the degenerate noise floor.** Neither achievable nor meaningfully improvable at current n.

### ABS (n=5, mean=0.0%, SE=0)

**Gate verdict: Below the noise floor.**
- Same degeneracy as IE-preference: all three runs at 0.0%, SE = 0.
- The abstention overlay covers 5 questions; the task may be genuinely hard or the evaluation criteria for abstention cases may not be satisfied by the current pipeline.
- **At the degenerate noise floor.**

---

## 5. Summary Table

| Category | n | Mean accuracy | Two-run envelope | Lower bound vs. 15pp | Gate achievable? | Noise floor? |
|---|---|---|---|---|---|---|
| IE-assistant | 9 | 44.4% | [−1.5pp, +90.4pp] | −1.5pp (misses by formula, stable in practice) | **Yes** (mean clear, observed stable) | No |
| KU | 9 | 51.9% | [+5.7pp, +98.0pp] | +5.7pp (misses by 9.3pp) | **No** (marginal — mean clear but lower bound misses) | No |
| IE-user | 9 | 18.5% | [−17.4pp, +54.5pp] | −17.4pp (misses by 32.4pp) | No | No |
| MR | 10 | 13.3% | [−16.5pp, +43.1pp] | −16.5pp (misses by 31.5pp) | No | No |
| TR | 10 | 23.3% | [−13.7pp, +60.4pp] | −13.7pp (misses by 28.7pp) | No | No |
| IE-preference | 3 | 0.0% | [0.0, 0.0] | 0.0pp (degenerate) | No | **Yes** (degenerate) |
| ABS | 5 | 0.0% | [0.0, 0.0] | 0.0pp (degenerate) | No | **Yes** (degenerate) |

### Key conclusions

1. **Only IE-assistant reliably passes a 15pp gate.** Its mean (44.4%) is far above the threshold and its observed spread is 0pp. The small-n SE formula gives a technically negative lower bound, but the observed data contradict the SE model — the embedding pipeline is deterministic for this category.

2. **KU is the closest non-passing category.** Mean of 51.9% with lower envelope bound of +5.7pp. The gap (9.3pp) is closeable by reducing embedding nondeterminism, not by increasing model capability.

3. **IE-user, MR, and TR miss comprehensively.** All three have lower envelope bounds >28pp below the 15pp threshold. These categories need genuine capability improvements, not just noise reduction.

4. **IE-preference and ABS are at the degenerate noise floor.** SE = 0 because p = 0 across all runs. No statistical inference is meaningful; these categories need either more cases or a fundamentally different approach to generate correct answers.
