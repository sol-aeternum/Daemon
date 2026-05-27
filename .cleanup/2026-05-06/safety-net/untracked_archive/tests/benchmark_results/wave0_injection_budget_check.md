# Wave 0 Injection Budget Check

**Artifact type:** Token budget analysis
**Evidence basis:** Simulation against 50 sampled queries using retrieved memories
**Date:** 2026-04-29

---

## 1. Purpose

This document reports the results of an injection budget check performed against 50 sampled benchmark queries. The purpose is to determine whether **token budget pressure** in the production injection module could explain truncation or omission of relevant memories, thereby contributing to the Wave 0 benchmark collapse.

---

## 2. Methodology

- **Sample size:** 50 queries
- **Budget setting:** `DEFAULT_MAX_TOKENS = 2500` (production injection module default)
- **Measurement:** Tokens were simulated/estimated for pre-injection (retrieved memories before formatting) and post-injection (after memory formatting and assembly into prompt)

---

## 3. Token Budget Figures

| Metric | Value |
|--------|-------|
| `sample_size` | 50 |
| `budget` | 2500 tokens |
| `truncation_queries` | 0 |
| `truncation_rate` | 0.0 (0%) |
| `median_pre_tokens` | 122.0 |
| `median_final_tokens` | 131.0 |
| `max_pre_tokens` | 164 |
| `max_dropped` | 0 |

### Interpretation of figures

- **Zero truncations:** Of the 50 sampled queries, none exceeded the 2500-token budget.
- **Median post-formatting overhead:** The gap between `median_pre_tokens` (122.0) and `median_final_tokens` (131.0) is ~9 tokens, representing the formatting overhead of the injection module — very small.
- **Maximum tokens seen:** `max_pre_tokens = 164` tokens, far below the 2500-token budget.
- **No dropped memories:** `max_dropped = 0` across all sampled queries.

---

## 4. Analysis

Under the current production injection configuration (`DEFAULT_MAX_TOKENS = 2500`):

1. **No query in the 50-sample set approaches the budget ceiling.** The maximum observed pre-formatting token count (164) is ~6.5% of the available budget.

2. **The formatting overhead is minimal.** Memory formatting adds only ~9 tokens on median, indicating the retrieved memory set is small and concise.

3. **Truncation is not occurring** in the sampled queries under current retrieval cardinality and memory formatting.

4. **Even with L0 support** (`L0_TOKEN_BUDGET = 200`, `MAX_L0_CHARS = 600`) enabled, the token headroom is substantial and no truncation would be triggered for these queries.

---

## 5. Relationship to Benchmark Collapse

The benchmark collapse is **NOT attributable to token budget pressure** under the current production injection settings.

- `truncation_rate = 0.0` across 50 sampled queries
- `max_dropped = 0` — no memories were dropped due to budget
- The budget ceiling (2500) is ~15x higher than the maximum observed token usage (164)

This rules out production injection truncation as a direct cause of benchmark failures in the sampled set.

---

## 6. Caveats

- **50-query sample:** This is a sample, not the full corpus. The conclusion applies to the sampled set and may not generalize if the full corpus contains outliers with substantially larger retrieved memory sets.

- **Current retrieval cardinality:** The low token counts reflect current retrieval behavior (median 5 candidates per query). If retrieval were modified to return more candidates, token usage could increase.

- **Benchmark path bypass:** As documented in `wave0_injection_audit.md`, the benchmark path bypasses the production injection module entirely. The budget check applies to production injection behavior, not to the simplified benchmark prompt builder.

---

## 7. Summary

| Hypothesis | Supported? |
|------------|------------|
| Token budget truncation causes memory omission | **No** — 0 truncations in 50-query sample |
| Budget ceiling too low for typical queries | **No** — max observed is 164 tokens, budget is 2500 |
| Production injection budget is a bottleneck | **No** — substantial headroom remains |

**Verdict:** Token budget pressure is not a strong explanatory candidate for the Wave 0 benchmark collapse.
