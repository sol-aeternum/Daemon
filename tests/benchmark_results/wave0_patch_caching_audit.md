# Wave 0 Patch Caching Audit

**Generated:** 2026-04-25
**Scope:** Tests-only benchmark harness patches — no production code changes

---

## Scope

This audit covers the recovery-patch scripts in `tests/benchmark_harness/` that were applied to restore the Wave 0 benchmark pipeline. It also covers relevant production-side cache references for completeness.

The question: **Did any of these patches introduce caching or short-circuit behavior that would render the three subset reruns non-independent?**

---

## Files Audited

| File | Purpose |
|---|---|
| `tests/benchmark_harness/ingestion_rerun.py` | Dual-provider-order override + reset + ingest |
| `tests/benchmark_harness/extraction_provider_override.py` | Extraction provider-order override probe |
| `tests/benchmark_harness/f1_fingerprint_stability.py` | Extraction fingerprint stability measurement |
| `tests/benchmark_harness/f2_extraction_output_determinism.py` | Extraction output determinism measurement |
| `tests/benchmark_harness/contradiction_single_verify.py` | Contradiction path single-call verification |
| `tests/benchmark_harness/guardrails.py` | Infrastructure guardrails (health, errored floor, credits) |

---

## Findings: Tests-Only Harness

### Caching of Extracted Outputs

**Any `dict`/`lru_cache`/memoization that caches extracted outputs?**

**No.**

None of the six files implement any form of extracted-output caching. Specifically:

- No `lru_cache` decorators on extraction functions.
- No `dict`-backed memoization of fact outputs.
- No file-based or fixture-based cache of extracted facts.
- The only `setattr()` operations are module-level runtime patches to constant values (`BENCHMARK_EXTRACTION_ENDPOINT_SLUG`, `BENCHMARK_CONTRADICTION_MODEL`, `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG`) and function replacements — none of which establish a cache of extraction results.

### Fixture/File-Based Cache of Extracted Facts

**Any fixture/file-based cache of extracted facts?**

**No.**

No file-based cache of extracted facts exists in the benchmark harness. Each harness script runs fresh and does not persist or reload extracted outputs between calls.

### Early-Return / Short-Circuit Behavior in Harness

**Any early-return or exception-short-circuit behavior in the tests-only harness?**

**Yes — in two files.**

#### `ingestion_rerun.py` (lines 58–71)

```python
async def _patched_extract_facts_from_text(...):
    try:
        return await _original_extract(...)
    except _BenchmarkSamplingError as e:
        print(f"[patched] extract_facts_from_text: BenchmarkSamplingError caught (diagnostic) -> {e}")
        from dataclasses import dataclass
        @dataclass
        class _EmptyOutcome:
            facts: list = None
            raw_count: int = 0
            calibrated_count: int = 0
            rejected_count: int = 0
            slot_coverage: int = 0
        return _EmptyOutcome()
```

**Effect:** When `BenchmarkSamplingError` (fingerprint drift) is raised during extraction, the patch converts it into an empty outcome (`_EmptyOutcome()`) rather than propagating the exception. Sessions that would have errored produce `outcome="empty"` instead.

**Location:** `ingestion_rerun.py:58-71`

**Is this an extracted-fact cache? No.** It is an exception-short-circuit that returns an empty struct. It does not return a cached result from a prior call.

#### `contradiction_single_verify.py` (lines 57–64)

```python
async def _patched_check_contradiction(...):
    try:
        return await _dedup_check_orig(...)
    except _DedupBenchmarkSamplingError as e:
        print(f"[patched] DedupBenchmarkSamplingError caught (advisory): {e}")
        return False, ""
```

**Effect:** When `DedupBenchmarkSamplingError` is raised during contradiction checking, the patch returns `(False, "")` rather than propagating.

**Location:** `contradiction_single_verify.py:57-64`

**Is this an extracted-fact cache? No.** It is an exception-short-circuit returning a default tuple. It does not return a cached result from a prior call.

### Module-Level Runtime Patching via `setattr()`

All harness scripts use `setattr()` to patch module-level constants at import time. This is global state mutation, not caching:

- `extraction_provider_override.py:46` — `setattr(_extraction_module, "BENCHMARK_EXTRACTION_ENDPOINT_SLUG", _fixed_slug)`
- `f1_fingerprint_stability.py:58` — same pattern
- `f2_extraction_output_determinism.py:72` — same pattern
- `ingestion_rerun.py:48,80-81,97` — `setattr()` on module constants and function replacement

None of these `setattr()` patches establish a result cache.

---

## Findings: Production-Side Cache References

For completeness, the following production-side caches were identified but are **not** extracted-fact caches:

### `orchestrator/config.py` (lines 703, 716)

Two `@lru_cache` decorators on settings/api-key helpers:
- `@lru_cache` on `get_settings()` — caches application settings singleton.
- `@lru_cache` on a second helper (line 716) — also settings-related.

**Not an extracted-fact cache.** Does not affect fact extraction results.

### `orchestrator/memory/embedding.py` (line 39)

```python
@lru_cache(maxsize=1)
```

Caches an API key helper (Voyage AI key lookup). **Not an extracted-fact cache.**

---

## Bottom Line: Were the Three Reruns Non-Independent?

### Caching

**No extracted-output caching was found in any recovery patch.** The `setattr()` patches mutate module constants and function references, but do not cache extraction results. No `lru_cache`, no `dict` memoization, no file cache of extracted facts.

### Short-Circuit Behavior

**Yes — exception short-circuit behavior exists**, but it converts errors to empty outcomes rather than returning cached results. In `ingestion_rerun.py`, `BenchmarkSamplingError` → `_EmptyOutcome()`. In `contradiction_single_verify.py`, `DedupBenchmarkSamplingError` → `(False, "")`.

**Effect on independence:** This short-circuit does not make runs non-independent in the sense of one run's result being derived from another's cached output. Rather, it suppresses failures and records them as empty outcomes. The three archived rerun directories (`run1/`, `run2/`, `run3/`) were produced by separate `reset + ingest` cycles — the short-circuit affects failure handling within each run, not cross-run caching.

### Conclusion

The three subset reruns were **not rendered non-independent by caching**. The short-circuit behavior in `ingestion_rerun.py` affects failure-mode reporting (errored → empty) but does not establish cross-run caching of extracted outputs.

---

## Recovery Memo Accuracy

The recovery closure memo (`wave0_closure_memo.md`) did not explicitly document the short-circuit behavior in `ingestion_rerun.py` and `contradiction_single_verify.py`. The short-circuit was present in the patch code but not called out as a behavioral caveat.

Specifically:
- `ingestion_rerun.py` converts `BenchmarkSamplingError` to `_EmptyOutcome()` — this was not highlighted in the closure memo.
- `contradiction_single_verify.py` converts `DedupBenchmarkSamplingError` to `(False, "")` — same.

These are **tests-only harness behaviors**, not production caching, and they do not alter the content of successful extraction runs. They do affect failure classification, which is relevant to error-rate metrics but not to the content of successfully extracted facts.

The closure memo's statement that "no extracted-fact caching" was found is **accurate** as to caching; the short-circuit caveat should be added as a footnote.

---

## Summary Table

| Question | Answer |
|---|---|
| Any `dict`/`lru_cache`/memoization caching extracted outputs? | **No** |
| Any fixture/file-based cache of extracted facts? | **No** |
| Any early-return / exception-short-circuit in tests harness? | **Yes** — `ingestion_rerun.py:58-71`, `contradiction_single_verify.py:57-64` |
| Those short-circuits cache extracted outputs? | **No** — they return empty/default values on exception |
| Were three reruns non-independent due to caching? | **No** |
| Were three reruns non-independent due to short-circuit? | **No** — short-circuits affect failure classification, not cross-run result derivation |

---

*Documentation artifact: `tests/benchmark_results/wave0_patch_caching_audit.md`*
*Wave 0 — Daemon project*
