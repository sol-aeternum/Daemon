# F2 — Extraction Output Determinism Measurement (Wave 0)

**Generated:** 2026-04-24T13:27:02+00:00
**Command:** `PYTHONPATH=. python tests/benchmark_harness/f2_extraction_output_determinism.py`
**Runtime:** 55.1s

---

## Configuration

| Parameter | Value |
|---|---|
| Runs | 10 |
| Provider override | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG`: `openrouter/openai/gpt-4o-mini-2024-07-18` → `openai` |
| Model | `openrouter/openai/gpt-4o-mini` |
| Benchmark mode | Yes |
| Input | Fixed probe text (374 chars) |

---

## Per-Run Results

| Run | Facts | Raw | Rejected | Canonical Hash | Error |
|---|---|---|---|---|---|
| 0 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 1 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 2 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 3 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 4 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 5 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 6 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 7 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 8 | 8 | 8 | 0 | bf1aef61b17292f9 |  |
| 9 | 8 | 8 | 0 | bf1aef61b17292f9 |  |

---

## Determinism Summary

| Metric | Value |
|---|---|
| Total runs | 10 |
| Total pairs | 45 |
| Canonically identical pairs | 45 |
| Non-identical pairs | 0 |
| All runs canonically identical? | **YES** |

---

## Canonical Output Example (Run 0, first 5 facts)

| Content (truncated) | Category | Slot |
|---|---|
| User has a dog named Bella | fact | relationship.pet |
| User is 32 years old | fact | identity.age |
| User lives in Sydney, Australia | fact | location.city |
| User loves coffee | preference | food.preference.coffee |
| User uses Python every day | fact | language.python |

---

## Interpretation

All 10 runs produced canonically identical fact outputs. Extraction is deterministic for this fixed input under the restored provider override. The Wave 0 residual spread is NOT caused by extraction output non-determinism.

---

## Note

Canonicalization normalizes: fact ordering (sorted by content), content whitespace,
and category strings. Confidence values are excluded from the canonical form
since they may vary slightly due to model temperature effects even at temperature=0.0.

This diagnostic measures the extraction path only (fact extraction via `extract_facts_from_text`).
It does NOT run the full ingestion pipeline (dedup/contradiction checks).
The contradiction routing is NOT patched here — this script focuses purely on extraction output determinism.

---

*Diagnostic script: `tests/benchmark_harness/f2_extraction_output_determinism.py`*
*Wave 0 — Daemon project*
