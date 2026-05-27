# Wave 0 — Provider Routing & Benchmark-Path LLM Call Audit

**Generated:** 2026-04-21
**Scope:** Answer generation, judge evaluation, extraction, dedup contradiction checks
**Authority:** Live code inspection of `tests/longmemeval/evaluate.py`, `orchestrator/memory/extraction.py`, `orchestrator/memory/dedup.py`, `orchestrator/config.py`, and shared provider wrappers.

---

## Summary

Four benchmark-path LLM call sites were identified and audited. **None of them capture `system_fingerprint` or equivalent provider-side model-version metadata.** All calls go through LiteLLM (`litellm.acompletion`) which proxies to OpenRouter. The answer/judge/extraction calls are hardcoded model constants; the dedup contradiction check uses a live config knob (`background_reasoning_model`).

**Benchmark-mode loud failure must be injected at the call site level**, not at a shared wrapper, because:
1. No shared LiteLLM wrapper exists for these call sites
2. Each call site handles LiteLLM responses differently (extraction uses JSON parsing, answer/judge use `_extract_content`, dedup swallows exceptions)
3. The `system_fingerprint` field is available in LiteLLM's raw response object but is never extracted

---

## Call Site Inventory

### 1. Answer Generation

| Property | Value |
|---|---|
| **File** | `tests/longmemeval/evaluate.py` |
| **Function** | `answer_with_llm()` (lines 323–341) |
| **Wrapper** | `_call_llm_with_provider_config()` (lines 191–229) — local to `evaluate.py`, not shared |
| **Model** | `ANSWER_MODEL = "openrouter/openai/gpt-4o"` (line 90, hardcoded constant) |
| **Temperature** | `ANSWER_TEMPERATURE = 0.7` (line 91) |
| **Max tokens** | `ANSWER_MAX_TOKENS = 256` (line 92) |
| **Transport** | `litellm.acompletion(...)` — direct LiteLLM call, no streaming |
| **`system_fingerprint` available?** | **NO** — raw response passed to `_extract_content()` (lines 232–248), which extracts only `.content` from `choices[0].message`. Response object `model_dump()` or `dict()` is called but the result dict is not inspected for `system_fingerprint`. |
| **Fail-fast location** | `_call_llm_with_provider_config()` returns `None` on exception (line 227–229) and caller treats `None` as empty string `""`. **No loud failure.** |

**Call chain:**
```
evaluate_single() [line 393]
  └─ answer_with_llm() [line 330]
       └─ _call_llm_with_provider_config() [line 191]
            └─ litellm.acompletion(**call_params) [line 225]
```

**Pinning authority:** Hardcoded constant. Answer model is NOT swappable via tier or env config.

**Benchmark-mode loud failure plan:** Inject fingerprint capture at line 225/226 in `_call_llm_with_provider_config()`. On non-`None` response, extract `response_data.get("system_fingerprint")` and assert it matches the pinned value (or record it for drift detection). Raise `BenchmarkProviderDriftError` if fingerprint changes between calls or differs from run metadata.

---

### 2. Judge Evaluation

| Property | Value |
|---|---|
| **File** | `tests/longmemeval/evaluate.py` |
| **Function** | `judge_answer()` (lines 289–320) |
| **Wrapper** | `_call_llm_with_provider_config()` — same local wrapper as answer generation |
| **Model** | `JUDGE_MODEL = "openrouter/openai/gpt-4o"` (line 94, hardcoded constant) |
| **Temperature** | `JUDGE_TEMPERATURE = 0.0` (line 95) |
| **Max tokens** | `JUDGE_MAX_TOKENS = 256` (line 96) |
| **Transport** | `litellm.acompletion(...)` — direct LiteLLM call, no streaming |
| **`system_fingerprint` available?** | **NO** — same `_extract_content()` extraction pattern. No fingerprint captured. |
| **Fail-fast location** | Same as answer generation. `_call_llm_with_provider_config()` returns `None` → caller returns `"incorrect"` (line 300). **No loud failure.** |

**Call chain:**
```
evaluate_single() [line 396]
  └─ judge_answer() [line 292]
       └─ _call_llm_with_provider_config() [line 191]
            └─ litellm.acompletion(**call_params) [line 225]
```

**Pinning authority:** Hardcoded constant. Judge model is NOT swappable via tier or env config.

**Benchmark-mode loud failure plan:** Same injection point as answer generation. Since both answer and judge use the same `_call_llm_with_provider_config()` wrapper, a single fingerprint check in that wrapper covers both call sites.

---

### 3. Extraction

| Property | Value |
|---|---|
| **File** | `orchestrator/memory/extraction.py` |
| **Function** | `extract_facts_from_text()` (lines 395–517) |
| **Wrapper** | `_get_provider_call_params()` (lines 19–45) — local to `extraction.py` |
| **Model** | `EXTRACTION_MODEL = "openrouter/openai/gpt-4o-mini"` (line 48, hardcoded constant) |
| **Temperature** | `EXTRACTION_TEMPERATURE = 0.0` (line 50) |
| **Max tokens** | `EXTRACTION_MAX_TOKENS = 2000` (line 52) |
| **Transport** | `litellm.acompletion(...)` at line 435 — direct LiteLLM call |
| **`system_fingerprint` available?** | **NO** — response is parsed for JSON content (lines 437–454) then fed to `json.loads`. The `response_data` dict (result of `model_dump()`) is built but never inspected for `system_fingerprint`. |
| **Retry path** | Lines 540–560 implement retry with a `retry_hint` re-injecting the prompt. Retry uses the same model. |
| **Fail-fast location** | Exception at line 509 → returns empty `ExtractionOutcome`. **No loud failure.** |

**Call chain:**
```
process_extraction() [line 537]
  └─ extract_facts_from_text() [line 435]
       └─ litellm.acompletion(**call_params)
            └─ via _get_provider_call_params() [line 409]
```

**Pinning authority:** Hardcoded constant. Extraction model is NOT swappable via tier or env config during benchmark runs (per `CONFIG_PINNING.md`).

**Benchmark-mode loud failure plan:** Inject fingerprint capture at line 435 before JSON parsing. Assert `response_data.get("system_fingerprint")` consistency across extraction calls within a benchmark run. Raise `BenchmarkProviderDriftError` on drift.

**Known risk:** Extraction is called from `process_extraction()` which is triggered by the ingest pipeline (`tests/longmemeval/ingest.py:298-304`). The extraction retry logic at line 540–560 may produce multiple LLM calls with potentially different fingerprints for the same input text. This could confound fingerprint-based drift detection.

---

### 4. Dedup Contradiction Checks

| Property | Value |
|---|---|
| **File** | `orchestrator/memory/dedup.py` |
| **Function** | `check_contradiction()` (lines 143–195) |
| **Wrapper** | None — direct `litellm.acompletion()` call at line 154 |
| **Model** | `get_settings().background_reasoning_model` (live config, default: `"openrouter/deepseek/deepseek-chat"`) |
| **Temperature** | `CONTRADICTION_TEMPERATURE = 0.1` (line 55, hardcoded) |
| **Max tokens** | `max_tokens=50` (line 167) |
| **Transport** | `litellm.acompletion(...)` at line 154 — direct LiteLLM call |
| **`system_fingerprint` available?** | **NO** — exception handler at line 194 swallows all errors and returns `(False, "")`. No fingerprint captured. |
| **Fail-fast location** | Exception → returns `(False, "")`. **No loud failure.** |

**Call chain:**
```
deduplicate_facts() [line 463]
  └─ check_contradiction() [line 154]
       └─ litellm.acompletion(...)
```

**Pinning authority:** Live config (`background_reasoning_model` in `orchestrator/config.py:412`). This is different from answer/judge/extraction which use hardcoded constants.

**Benchmark-mode loud failure plan:** Inject fingerprint capture at line 154. Unlike the other call sites, this is a **live config** model, so benchmark-mode must also assert the model ID matches the pinned value. Raise `BenchmarkProviderDriftError` if model ID or fingerprint drifts.

**Oracle review note (from inherited context):** Dedup contradiction checks were identified as a likely gap if provider pinning only covers eval/extraction wrappers. This audit confirms the gap — contradiction checks use a separate code path with no wrapper, no fingerprint capture, and a different model source (config vs. hardcoded constant).

---

## Shared Wrapper Analysis

| Wrapper | Location | Used By | Fingerprint Captured? |
|---|---|---|---|
| `_call_llm_with_provider_config()` | `tests/longmemeval/evaluate.py:191` | Answer, Judge | **NO** |
| `_get_provider_call_params()` | `orchestrator/memory/extraction.py:19` | Extraction | **NO** |
| (none) | `orchestrator/memory/dedup.py:154` | Contradiction | **NO** |

**Conclusion:** There is no shared LiteLLM wrapper that could serve as a central injection point for benchmark-mode pinning. Each call site must be patched individually.

---

## Metadata Availability Per Call Site

| Call Site | `system_fingerprint` in Response? | Other Model-Version Metadata? | Currently Captured? |
|---|---|---|---|
| Answer generation | Available in LiteLLM response dict | `model` field in response | **NO** |
| Judge evaluation | Available in LiteLLM response dict | `model` field in response | **NO** |
| Extraction | Available in LiteLLM response dict | `model` field in response | **NO** |
| Dedup contradiction | Available in LiteLLM response dict | `model` field in response | **NO** |

**Note on LiteLLM response structure:** LiteLLM's `acompletion()` returns a response object that, when `.model_dump()` is called, produces a dict containing `system_fingerprint`, `model`, `created`, `usage`, and `choices`. The `model` field reflects the resolved model ID (e.g., `openrouter/openai/gpt-4o`). All four call sites call `.model_dump()` or `.dict()` on the response, but none inspect `system_fingerprint` or `model` for drift detection.

---

## Benchmark-Mode Loud Failure Injection Points

| Call Site | Injection Point | What to Assert | Error Type |
|---|---|---|---|
| Answer | `_call_llm_with_provider_config()` line 226, after `litellm.acompletion()` succeeds | `response_data.get("system_fingerprint")` matches pinned value | `BenchmarkProviderDriftError` |
| Judge | `_call_llm_with_provider_config()` line 226 (same wrapper) | `response_data.get("system_fingerprint")` matches pinned value | `BenchmarkProviderDriftError` |
| Extraction | `extract_facts_from_text()` line 435, before JSON parsing | `response_data.get("system_fingerprint")` matches pinned value | `BenchmarkProviderDriftError` |
| Dedup contradiction | `check_contradiction()` line 154, after `litellm.acompletion()` succeeds | Model ID (`response_data.get("model")`) matches pinned `background_reasoning_model` AND `system_fingerprint` matches | `BenchmarkProviderDriftError` |

**Critical distinction for dedup:** Since `background_reasoning_model` is a live config, benchmark-mode must assert BOTH:
1. The resolved model ID in the LiteLLM response matches the expected model
2. The `system_fingerprint` is consistent across calls (or record it for post-hoc drift analysis)

---

## Gap Analysis: What Could Masquerade as Benchmark Variance

| Gap | Risk | How It Manifests |
|---|---|---|
| Answer/judge use `_call_llm_with_provider_config()` which swallows exceptions silently | Provider outage or credit exhaustion returns `None` → empty string answer → `"incorrect"` judgment | False "incorrect" judgments that look like model quality issues |
| Extraction returns empty `ExtractionOutcome` on any exception | Network timeout, rate limit, or model change silently produces 0 facts | Missing memory entries that look like retrieval failures |
| Dedup contradiction returns `(False, "")` on any exception | Provider issues silently skip contradiction detection | Incorrect supersession decisions that look like dedup threshold calibration issues |
| No `system_fingerprint` capture anywhere | Cannot distinguish provider-side model updates from actual benchmark variance | Undetectable model-version drift |
| Extraction retry doubles LLM calls with potentially different fingerprints | Same input → two different fingerprints → ambiguous drift attribution | Hard to diagnose per-call fingerprint variance |

---

## Recommendations for Tasks 8, 9, 11, 12

1. **Task 8/9 (Eval/extraction wrapper pinning):** Patch `_call_llm_with_provider_config()` and `extract_facts_from_text()` to capture `system_fingerprint` from LiteLLM response dict. Add `BenchmarkProviderDriftError` that carries the fingerprint and model ID.

2. **Task 11 (Dedup contradiction pinning):** This is the most gap-prone site — it has no wrapper, uses a live config model, and swallows all exceptions. The injection point must also assert the model ID against the pinned `background_reasoning_model`.

3. **Task 12 (Consolidation/consistency):** If consolidation or other background LLM calls are in scope for benchmark-mode, they follow the same pattern — direct `litellm.acompletion()` with no fingerprint capture. See `consolidation.py:437`, `dreaming.py:267`, `summary.py:136`, `titles.py:73`, `entities.py:739`.

---

## Files Inspected

| File | Key Findings |
|---|---|
| `tests/longmemeval/evaluate.py` | Answer/judge both use local `_call_llm_with_provider_config()` wrapper. No fingerprint. |
| `orchestrator/memory/extraction.py` | Extraction uses `_get_provider_call_params()`. No fingerprint. Has retry logic. |
| `orchestrator/memory/dedup.py` | Contradiction uses direct `litellm.acompletion()`. No wrapper, no fingerprint. |
| `orchestrator/config.py` | `background_reasoning_model` defaults to `"openrouter/deepseek/deepseek-chat"`. Answer/judge/extraction models are hardcoded, not from config. |
| `orchestrator/memory/embedding.py` | Uses Voyage AI API directly (not LiteLLM). Not an LLM call site for benchmark purposes. |
| `tests/benchmark_longmemeval/CONFIG_PINNING.md` | Confirms answer/judge models are hardcoded constants; contradiction uses live config. |

---

## Notepad Updates

Findings appended to:
- `.sisyphus/notepads/wave-0-baseline-reproducibility-lock/learnings.md`
- `.sisyphus/notepads/wave-0-baseline-reproducibility-lock/issues.md`
