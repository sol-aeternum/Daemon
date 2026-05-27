# D4 — Extraction Provider-Order Override (Wave 0)

**Generated:** 2026-04-24
**Status:** PASSED — Single-call verification probe succeeded

---

## Problem

When `BENCHMARK_MODE=1` is active, `orchestrator/memory/extraction.py` passes
`BENCHMARK_EXTRACTION_ENDPOINT_SLUG` to `extra_body.provider.order`:

```python
# extraction.py:96
BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openrouter/openai/gpt-4o-mini-2024-07-18"

# extraction.py:82-87 (inside _get_provider_call_params when benchmark_mode=True)
call_params["extra_body"] = {
    "provider": {
        "order": [BENCHMARK_EXTRACTION_ENDPOINT_SLUG],  # → ["openrouter/openai/gpt-4o-mini-2024-07-18"]
        "allow_fallbacks": False,
    }
}
```

OpenRouter returns:
```
{"error":{"message":"No endpoints found for openai/gpt-4o-mini-2024-07-18.","code":404}}
```

The full model-identifier slug is not valid in `provider.order` — only the
provider name is accepted.

## Prior Probe Results

| provider.order value | Result |
|---|---|
| `['openrouter/openai/gpt-4o-mini-2024-07-18']` | ❌ 404 |
| `['openai']` + `allow_fallbacks=false` | ✅ 200 |

## Fix Applied

**Tests-only runtime patch** — no production code changes.

File: `tests/benchmark_harness/extraction_provider_override.py`

```python
# Runtime-patch the module-level constant before use
_extraction_module = __import__("orchestrator.memory.extraction", ...)
setattr(_extraction_module, "BENCHMARK_EXTRACTION_ENDPOINT_SLUG", "openai")
os.environ["BENCHMARK_MODE"] = "1"
```

The patch replaces the broken full-model slug with `"openai"`, matching the
verified working configuration from the prior probe.

## Single-Call Verification

**Command:**
```bash
PYTHONPATH=. python tests/benchmark_harness/extraction_provider_override.py
```

**Result:** PASSED

```
[D4 override] Original BENCHMARK_EXTRACTION_ENDPOINT_SLUG = 'openrouter/openai/gpt-4o-mini-2024-07-18'
[D4 override] Patched BENCHMARK_EXTRACTION_ENDPOINT_SLUG = 'openai'
[D4 override] BENCHMARK_MODE=1 set in environment

[D4 probe] ExtractionOutcome: raw=8, calibrated=8, validated=8, rejected=0
[D4 probe] Sample extracted facts:
  - [fact] User's name is Alex... (conf=0.9)
  - [fact] User is 32 years old... (conf=0.9)
  - [fact] User lives in Sydney, Australia... (conf=0.9)

[D4 probe] RESULT: PASS
D4 VERIFICATION: PASSED
```

Extraction produced 8 raw facts, all 8 calibrated and validated — zero
exceptions or 404 errors.

## Scope

- ✅ `tests/benchmark_harness/extraction_provider_override.py` — new file
- ✅ No changes to `orchestrator/memory/extraction.py`
- ✅ No changes to `orchestrator/memory/dedup.py`
- ✅ No changes to any production code outside `tests/`

## Dedup Path

The dedup contradiction call also uses a benchmark endpoint slug:

```python
# dedup.py:95
BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "openrouter/deepseek/deepseek-chat-v3-5"
```

Whether this requires a similar override depends on the dedup call path used in
Step 3. This was **not patched** in D4 — it is out of scope per the
task constraints (no dedup changes unless required for ingestion-only).

## Next Steps

- D5/D6 can now proceed using the `extraction_provider_override` harness
  as the benchmark-mode extraction path.
- If Step 3 dedup/contradiction also fails with 404, a second patch for
  `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "deepseek"` may be needed.
