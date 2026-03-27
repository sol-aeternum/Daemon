# Extraction Model Benchmark Scorecard

**Date:** 2026-03-27  
**Benchmark:** `tests/benchmark_extraction.py` (v2.3, 8 scenarios)  
**Baseline Model:** `openrouter/openai/gpt-4o-mini`

---

## Results Summary

| Model | Precision | Recall | Accuracy | Scenarios Passed | Facts Extracted | Notable Failures | JSON Parse Errors |
|-------|-----------|--------|----------|------------------|-----------------|------------------|-------------------|
| **GPT-4o-mini** (baseline) | 0.97 | 0.93 | 1 | 7/8 | 28/30 | S6: missing Tailscale, LLM; S8: adversarial fail | 0 |
| **GPT-4.1-nano** | 0.96 | 0.80 | 1 | 6/8 | 24/30 | S1: missing Python, TypeScript, Neovim; S6: missing arch, Tailscale, LLM; S8: adversarial fail | 0 |
| **GPT-5-nano** | 1.00 | 0.80 | 1 | 7/8 | 24/30 | S1: missing Python, TypeScript, Neovim; S6: missing birthday, Tailscale, LLM | 0 |
| **GPT-5.4-nano** | 1.00 | 0.90 | 1 | 7/8 | 27/30 | S6: missing arch, Tailscale, LLM | 0 |

---

## Detailed Breakdown

### Baseline: GPT-4o-mini ✅
```
TOTAL: TP=28 FP=1 FN=2 | P=0.97 R=0.93 A=1.00
Scenarios: 7/8 passed
Failed: S6 (R=0.71), S8 (adversarial fail)
```

### Candidate 1: GPT-4.1-nano ⚠️
```
TOTAL: TP=24 FP=1 FN=6 | P=0.96 R=0.80 A=1.00
Scenarios: 6/8 passed
Failed: S1 (R=0.67), S6 (R=0.57), S8 (adversarial fail)
Regression: -13% recall vs baseline
```

### Candidate 2: GPT-5-nano ⚠️
```
TOTAL: TP=24 FP=0 FN=6 | P=1.00 R=0.80 A=1.00
Scenarios: 7/8 passed
Failed: S1 (R=0.67), S6 (R=0.57)
Note: Adversarial test PASSED (no false positives)
```

### Candidate 3: GPT-5.4-nano ✅ (BEST)
```
TOTAL: TP=27 FP=0 FN=3 | P=1.00 R=0.90 A=1.00
Scenarios: 7/8 passed
Failed: S6 (R=0.57)
Note: Adversarial test PASSED (no false positives)
BENCHMARK PASSED!
```

---

## Key Findings

1. **GPT-4.1-nano** shows measurable recall regression (-13%) vs baseline
   - Missing programming language facts (Python, TypeScript) in S1
   - Missing technical details (Tailscale, LLM) in S6
   - Adversarial test failed (false positive)

2. **GPT-5-nano** performs similarly to GPT-4.1-nano
   - P=1.00, R=0.80 (same as GPT-4.1-nano)
   - Adversarial test passed (improvement over GPT-4.1-nano)
   - Still misses programming language facts in S1

3. **GPT-5.4-nano is the BEST candidate**
   - P=1.00, R=0.90 (only -3% vs baseline, meets 0.90 threshold)
   - **BENCHMARK PASSED** (first nano model to pass!)
   - Adversarial test passed
   - Extracts Python, TypeScript, Neovim correctly (unlike other nanos)
   - Only fails on S6 (multi-turn technical details)

---

## Recommendation

**Consider GPT-5.4-nano as extraction model**

GPT-5.4-nano is the only nano model that:
- ✅ Passes the full benchmark (P≥0.90, R≥0.90, Adversarial=0)
- ✅ Extracts programming languages correctly
- ✅ Has perfect precision (no false positives)
- ✅ Only 3% recall drop vs baseline

Trade-offs vs GPT-4o-mini:
- Slightly lower recall (0.90 vs 0.93)
- Same S6 failure pattern (multi-turn technical details)
- Better precision (1.00 vs 0.97)

If maximum recall is critical, stay on GPT-4o-mini.
If precision and benchmark pass matter more, GPT-5.4-nano is a strong choice.

---

## Files Generated

- `tests/benchmark_results/baseline_gpt4o-mini.txt` — Full baseline run
- `tests/benchmark_results/gpt4.1-nano.txt` — Full GPT-4.1-nano run
- `tests/benchmark_results/gpt5-nano.txt` — Full GPT-5-nano run
- `tests/benchmark_results/gpt5.4-nano.txt` — Full GPT-5.4-nano run
- `tests/benchmark_results/SCORECARD.md` — This file