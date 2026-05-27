# Wave 0 Extraction Cache Feasibility

**Date**: 2026-04-23
**Purpose**: Assess feasibility of a tests-only extraction output cache

---

## Background

Prior attribution work identified that ABL-1 and ABL-2 achieved **28.0%** and **34.0%** respectively on the dev subset (n=50). The 6pp spread was decomposed into:
- Embedding contribution: ~6pp (measured)
- Answer temperature: ~4pp (inferred)

Extraction variance was previously observed to be near-zero, suggesting extraction caching may not be the primary fix. This document assesses feasibility anyway for completeness.

---

## Production vs Test Harness Call Chains

### Production Call Chain
```
orchestrator/worker/jobs.py
  → process_extraction()
    → extract_facts_from_text()
```

### Test Harness Call Chain
```
tests/longmemeval/ingest.py
  → process_extraction()
```

Both chains converge on `process_extraction()` as the shared boundary function.

---

## Feasibility Assessment

### Minimal Interception Point

A tests-only cache is feasible by monkeypatching `extract_facts_from_text` or `process_extraction` from the harness side.

**Recommended interception point**: A test-side wrapper around `process_extraction()` in `tests/longmemeval/ingest.py` or a shared conftest fixture.

### Estimated LOC

| Component | LOC Estimate |
|-----------|-------------|
| Cache dictionary / file store | ~15 |
| Key computation (SHA256 of canonicalized payload) | ~15 |
| Cache lookup / write logic | ~20 |
| Integration / wiring | ~20 |
| Tests for the cache itself | ~10 |
| **Total** | **~80 LOC** |

### Key Shape

```
SHA256(messages_json + extraction_prompt + model_string)
```

Where:
- `messages_json`: JSON-serialized message list (canonicalized, e.g., sorted keys if dicts)
- `extraction_prompt`: The system prompt string used for extraction
- `model_string`: The model identifier (e.g., `openrouter/openai/gpt-4o-mini`)

Alternative: Use `hash((messages_serialized, prompt, model))` with a stable serialization.

### Scope Limitation

This cache is **tests-only**:
- Lives entirely in `tests/` or a test-specific module
- Does NOT modify `orchestrator/memory/`
- Does NOT alter production extraction behavior
- Can be enabled/disabled via a test fixture or environment variable

---

## Caveat

Extraction variance previously appeared **near-zero** in the variance attribution analysis. The embedding contribution (~6pp) is the dominant observed factor. Therefore, extraction caching is unlikely to be the primary variance reduction mechanism.

**Recommended use case**: Only if extraction-level reproducibility is explicitly desired for test determinism, not as a primary variance reduction strategy.

---

## Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Key collision (SHA256) | Low | Negligible | SHA256 is sufficient for test fixture sizes |
| Serialization non-determinism | Medium | Possible | Canonicalize JSON (sorted keys, consistent encoding) |
| Model string changes | Low | Possible | Pin model string in test config |
| Maintenance burden | Low | Medium | Simple dict-based cache; well-isolated |

**Overall Risk**: LOW — the implementation is simple, isolated, and does not touch production code.

---

## Conclusion

A tests-only extraction output cache is **feasible** with approximately 80 LOC. The interception point is `process_extraction()` in the test harness. Key shape is SHA256 of a canonicalized payload. This does not require modifying `orchestrator/memory/`.

However, given that extraction variance was observed to be near-zero, this cache is not the recommended primary action for reducing the 6pp spread. It is a low-risk option if test determinism at the extraction layer is a goal.
