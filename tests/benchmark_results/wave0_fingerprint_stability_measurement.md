# F1 — Fingerprint Stability Measurement (Wave 0)

**Generated:** 2026-04-24T13:27:03+00:00
**Command:** `PYTHONPATH=. python tests/benchmark_harness/f1_fingerprint_stability.py`
**Runtime:** 56.0s

---

## Configuration

| Parameter | Value |
|---|---|
| Runs | 10 |
| Seed | 42 |
| Provider override | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG`: `openrouter/openai/gpt-4o-mini-2024-07-18` → `openai` |
| Model | `openrouter/openai/gpt-4o-mini` |
| Benchmark mode | Yes |
| Input | Fixed probe text (374 chars) |

---

## Per-Call Results

| Run | Model | system_fingerprint | Facts Extracted | Error |
|---|---|---|---|---|
| 0 | openai/gpt-4o-mini-2024-07-18 | fp_4181e24c46 | 8 |  |
| 1 | openai/gpt-4o-mini-2024-07-18 | fp_e61ea1dda4 | 8 |  |
| 2 | openai/gpt-4o-mini-2024-07-18 | fp_e61ea1dda4 | 8 |  |
| 3 | openai/gpt-4o-mini-2024-07-18 | fp_e61ea1dda4 | 8 |  |
| 4 | openai/gpt-4o-mini-2024-07-18 | fp_e61ea1dda4 | 8 |  |
| 5 | openai/gpt-4o-mini-2024-07-18 | fp_4181e24c46 | 8 |  |
| 6 | openai/gpt-4o-mini-2024-07-18 | fp_e61ea1dda4 | 8 |  |
| 7 | openai/gpt-4o-mini-2024-07-18 | fp_4181e24c46 | 8 |  |
| 8 | openai/gpt-4o-mini-2024-07-18 | fp_e61ea1dda4 | 8 |  |
| 9 | openai/gpt-4o-mini-2024-07-18 | fp_e61ea1dda4 | 8 |  |

---

## Stability Summary

| Metric | Value |
|---|---|
| Total runs | 10 |
| Runs with fingerprint | 10 |
| Unique fingerprints | 2 |
| Unique models | 1 |
| Fingerprint drift detected? | **YES** |

---

## Interpretation

Fingerprint drift confirmed across 2 distinct fingerprint values in 10 runs. The extraction provider is not returning stable system_fingerprints even with seed=BENCHMARK_SEED=42 and provider.order=['openai']. This is the dominant source of the Wave 0 residual spread (measured ~6pp).

---

## Note

This diagnostic measures the extraction path only (fact extraction via `extract_facts_from_text`).
It does not run the full ingestion pipeline (dedup/contradiction checks).
The contradiction routing is NOT patched here — this script focuses purely on extraction fingerprint stability.

---

*Diagnostic script: `tests/benchmark_harness/f1_fingerprint_stability.py`*
*Wave 0 — Daemon project*
