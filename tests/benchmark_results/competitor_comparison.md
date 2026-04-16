# LongMemEval Competitor Comparison

**Date**: 2026-04-14
**Trusted Baseline**: `longmemeval_tier2_fast` — Daemon Tier 2 fast harness, 500 questions
**Overall Accuracy**: 81.1%

---

## Summary

This comparison places Daemon's Tier 2 fast baseline against published memory-augmented LLM systems evaluated on the LongMemEval benchmark.

**Note**: Competitor category breakdowns are not publicly available; only overall accuracy values are known from published sources. Daemon's category breakdown is from the trusted fast-harness run.

---

## System Comparison Table (Overall Accuracy)

| System | Overall Accuracy | Notes |
|--------|:---------------:|-------|
| **OMEGA** | **95.4%** | From published benchmarks |
| **Hindsight** | **91.4%** | From published benchmarks |
| **Supermemory** | **85.4%** | From published benchmarks |
| **EverMemOS** | **83.0%** | From published benchmarks |
| **Daemon (Tier 2 fast)** | **81.1%** | 500 questions; fast harness |
| **Full-ctx GPT-4o** | **60–64%** | Full context; no retrieval |
| **Zep** | **71.2%** | From published benchmarks |
| **Mem0** | **49.0%** | From published benchmarks |

---

## Daemon Category Breakdown (Trusted Baseline)

| Category | Accuracy | Questions | Assessment |
|----------|----------|-----------|------------|
| MR (Memory Reasoning) | 86.1% | 133 | Strong |
| TR (Temporal Reasoning) | 84.6% | 133 | Strong |
| KU (Knowledge Update) | 79.5% | 78 | Moderate |
| IE-assistant | 82.1% | 56 | Moderate |
| IE-user | 76.4% | 70 | Moderate |
| IE-preference | 56.7% | 30 | Weak — largest gap |

**Competitor category breakdowns**: Not publicly available.

---

## Interpretation

- **OMEGA (95.4%)** and **Hindsight (91.4%)** outperform Daemon by 10+ points overall
- **Supermemory (85.4%)** and **EverMemOS (83.0%)** are within a few points of Daemon
- **Daemon (81.1%)** sits between EverMemOS and Supermemory
- **Full-ctx GPT-4o (60–64%)** confirms retrieval significantly outperforms full-context for memory tasks
- **Zep (71.2%)** and **Mem0 (49.0%)** trail Daemon substantially
- **MR and TR are Daemon's strongest categories** (86.1% / 84.6%)
- **IE-preference is Daemon's weakest category** (56.7%)

---

## Benchmark Contract

This comparison uses Daemon's **trusted Tier 2 fast baseline** (`longmemeval_tier2_fast/`), which represents:

- Direct session embedding + production retrieval (not a separate benchmark-only path)
- Isolated per-run benchmark user to prevent cleanup races
- 500 questions, zero harness-error rows
- Per-category accuracy derived from 500-question judgment distribution

All future Daemon benchmark comparisons should reference this baseline.

---

## Caveats

- Competitor overall accuracy values are from published sources and may use different benchmark versions or evaluation protocols
- Category-level breakdowns for competitors are not publicly disclosed
- Full-ctx GPT-4o range (60–64%) reflects variation across benchmark subsets
- Daemon's 81.1% is from a single 500-question run; no confidence intervals available

---

## Files

- `competitor_comparison.md` — This document
- `competitor_comparison.json` — Machine-readable version
- `longmemeval_tier2_fast.json` — Trusted Daemon baseline numbers
- `longmemeval_tier2_fast/longmemeval_fast_results.jsonl` — Raw 500-question results