# Wave 0 — BENCHMARK_DETERMINISTIC_MODE Coverage Audit

**Scope:** Three LLM call classes relevant to Step 2: (a) extraction, (b) judge, (c) orchestrator/answer generation.
**Standard:** Model version pinning (dated snapshot), seed=42, temperature=0.0 enforced at runtime, system_fingerprint captured and drift-checked, provider pinned via `extra_body.provider.order`.
**Codebase focus only. No modifications.**

---

## (a) Extraction — `orchestrator/memory/extraction.py`

**Entry point:** `extract_facts_from_text()` (line 444)

| Control | Covered? | Details |
|---|---|---|
| Dated model version | **YES** | `BENCHMARK_EXTRACTION_MODEL = "openrouter/openai/gpt-4o-mini-2024-07-18"` (`extraction.py:95`). In benchmark mode `_get_provider_call_params()` routes to this dated snapshot (line 63). |
| Temperature = 0.0 | **YES** | Guard at line 466: raises `BenchmarkSamplingError` if `EXTRACTION_TEMPERATURE != 0.0`. `EXTRACTION_TEMPERATURE` is hardcoded `0.0` at line 99, so the guard always passes in practice, but the enforcement structure exists. |
| Fixed seed = 42 | **YES** | `BENCHMARK_SEED = 42` (`extraction.py:20`). Injected into `call_params` at line 499 when `benchmark_mode` is true. |
| Fingerprint enforcement | **YES** | `system_fingerprint` captured from response at line 515. Stored in `_BM_METADATA["extraction"]` at line 533. Subsequent calls compare fingerprint at lines 523–531 and raise `BenchmarkSamplingError` on drift. |
| Provider pinning | **YES** | `extra_body={"provider":{"order":[BENCHMARK_EXTRACTION_ENDPOINT_SLUG],"allow_fallbacks":False}}` added at lines 81–87 when `benchmark_mode` is true. |

**Test coverage:** `tests/memory/test_extraction_determinism.py` (500 lines) verifies temperature, seed, fingerprint capture, fingerprint drift abort, non-benchmark absence of seed/tracking. `tests/benchmark/test_provider_pinning.py` `TestExtractionExtraBodyContract` (lines 483–562) verifies `extra_body`, dated model, and non-benchmark exclusion.

**Verdict: FULLY COVERED at runtime.**

---

## (b) Judge — `tests/longmemeval/evaluate.py`

**Entry point:** `judge_answer()` (line 429) calls `_call_llm_with_provider_config()` (line 264).

| Control | Covered? | Details |
|---|---|---|
| Dated model version | **YES** | `BENCHMARK_JUDGE_MODEL = "openrouter/openai/gpt-4o-2024-08-06"` (`evaluate.py:106`). `_get_benchmark_model_for_call("judge")` returns this at line 249, overriding the alias in the call. |
| Temperature = 0.0 | **YES** | Guard at line 285: raises `BenchmarkSamplingError` if `temperature != 0.0` for the call. |
| Fixed seed = 42 | **YES** | `BENCHMARK_SEED = 42` (`evaluate.py:114`). Injected at line 307 when `is_benchmark` (i.e., `bm_call_key` is set). |
| Fingerprint enforcement | **YES** | `system_fingerprint` captured at line 345. Stored in `_BM_METADATA[bm_call_key]` at line 364. Subsequent calls compare at lines 354–362 and raise `BenchmarkSamplingError` on drift. |
| Provider pinning | **YES** | `extra_body` added at lines 310–315 with `allow_fallbacks: False`. |

**Test coverage:** `tests/benchmark/test_provider_pinning.py` `TestEvaluateExtraBodyContract.test_benchmark_judge_includes_extra_body_with_provider_order` (line 401) explicitly verifies judge sends `extra_body` with correct endpoint slug. `TestEvaluateProviderFailFast` (line 24) verifies fail-fast on provider errors.

**Verdict: FULLY COVERED at runtime.**

---

## (c) Orchestrator / Answer Generation

### (c-i) Benchmark harness answer path — `tests/longmemeval/evaluate.py`

**Entry point:** `answer_with_llm()` (line 470) calls `_call_llm_with_provider_config()` (line 264).

| Control | Covered? | Details |
|---|---|---|
| Dated model version | **YES** | `BENCHMARK_ANSWER_MODEL = "openrouter/openai/gpt-4o-2024-08-06"` (`evaluate.py:105`). `_get_benchmark_model_for_call("answer")` returns this at line 247. |
| Temperature = 0.0 | **YES** | Line 477: `temperature = 0.0 if benchmark_mode else ANSWER_TEMPERATURE`. Guard at line 285 then validates it is 0.0. |
| Fixed seed = 42 | **YES** | Injected by `_call_llm_with_provider_config` at line 307 when `bm_call_key="answer"`. |
| Fingerprint enforcement | **YES** | Same `_call_llm_with_provider_config` path as judge — fingerprint captured at line 345, stored in `_BM_METADATA["answer"]`, drift checked at lines 354–362. |
| Provider pinning | **YES** | `extra_body` added at lines 310–315 for `bm_call_key="answer"`. |

**Test coverage:** `tests/benchmark/test_provider_pinning.py` `TestEvaluateExtraBodyContract.test_benchmark_answer_includes_extra_body_with_provider_order` (line 371) and `test_benchmark_uses_dated_snapshot_model` (line 457) verify answer dispatch.

**Verdict (harness answer): FULLY COVERED at runtime.**

---

### (c-ii) Production orchestrator streaming path — `orchestrator/tools/completion.py`

**Entry point:** `completion_with_tools()` (the litellm streaming call made from `stream_sse_chat` in `daemon.py`).

| Control | Covered? | Details |
|---|---|---|
| Dated model version | **NO** | No benchmark model override. `model` comes from tier config via `get_model_for_tier()`. No `_get_benchmark_model_for_call` equivalent. |
| Temperature = 0.0 | **NO** | Temperature is set per tier/config. No runtime guard enforces 0.0 in benchmark mode. No `BENCHMARK_MODE` check in `completion.py`. |
| Fixed seed = 42 | **NO** | No `seed` parameter injected. |
| Fingerprint enforcement | **NO** | `system_fingerprint` is never read from responses. No `_BM_METADATA` equivalent. |
| Provider pinning | **NO** | No `extra_body` construction for `benchmark_mode`. |

**No test coverage for benchmark determinism on the production streaming path.**

**Verdict (production orchestrator): NOT COVERED at runtime.**

---

## Summary

| Call site | File:line | Dated model | Temp=0 enforced | Seed injected | Fingerprint checked | Provider pinned |
|---|---|---|---|---|---|---|
| (a) Extraction | `orchestrator/memory/extraction.py:444` | YES — `gpt-4o-mini-2024-07-18` | YES (guard at line 466) | YES (line 499) | YES (lines 513–536) | YES (lines 81–87) |
| (b) Judge | `tests/longmemeval/evaluate.py:429` | YES — `gpt-4o-2024-08-06` | YES (guard at line 285) | YES (line 307) | YES (lines 344–367) | YES (lines 310–315) |
| (c-i) Harness answer | `tests/longmemeval/evaluate.py:470` | YES — `gpt-4o-2024-08-06` | YES (line 477 + guard at 285) | YES (line 307) | YES (lines 344–367) | YES (lines 310–315) |
| (c-ii) Production orchestrator | `orchestrator/tools/completion.py` | **NO** | **NO** | **NO** | **NO** | **NO** |

**Key distinction:** The benchmark harness (canonical LongMemEval runner) fully covers controls (a), (b), and (c-i). The production `stream_sse_chat` → `completion_with_tools` path in the orchestrator has zero benchmark determinism coverage. These are separate code paths — enabling `BENCHMARK_MODE=1` affects extraction and dedup (which read the env var directly) but has no effect on the production streaming LLM call.
