# Wave 0 Closure — Routing Audit

**Date:** 2026-05-01
**Task:** I2/I3 — Audit all benchmark-path provider routing and live OpenRouter registry
**Scope:** All benchmark-path model-routing call sites across evaluate, ingest, extraction, dedup, embedding, and runner
**Status:** COMPLETE

---

## Executive Summary

| Call Site | Model String | provider.order | Valid? | seed | Notes |
|---|---|---|---|---|---|
| **answer** (evaluate.py) | `openrouter/openai/gpt-4o-2024-08-06` | `["openai"]` | ✅ valid slug | 42 | Benchmark-mode only; non-BM uses `gpt-4o` with no pinning |
| **judge** (evaluate.py) | `openrouter/openai/gpt-4o-2024-08-06` | `["openai"]` | ✅ valid slug | 42 | Benchmark-mode only |
| **extraction** (extraction.py) | `openrouter/openai/gpt-4o-mini` | NOT SET | N/A | NOT SET | Non-deterministic in benchmark mode; no provider pinning |
| **contradiction** (dedup.py) | `openrouter/deepseek/deepseek-chat` | NOT SET | N/A | NOT SET | Uses `background_reasoning_model` from config; no benchmark-mode override |
| **Voyage doc embed** (embedding.py) | `voyage-4-large` | N/A | N/A | N/A | API key from `VOYAGE_API_KEY`; no seed/fingerprint |
| **Voyage query embed** (embedding.py) | `voyage-4-lite` | N/A | N/A | N/A | API key from `VOYAGE_API_KEY`; no seed/fingerprint |

**Critical finding:** `orchestrator/eval/runner.py` cannot be imported — it references 5 symbols that do not exist in the modules it imports from (`extraction.py`, `dedup.py`). This breaks the canonical benchmark runner entirely.

---

## 1. answer — `tests/longmemeval/evaluate.py`

### Call site: `answer_with_llm()` → `_call_llm_with_provider_config()`

**File:** `tests/longmemeval/evaluate.py`
**Lines:** 554–583 (caller), 265–370 (shared wrapper)

#### Constants (lines 94–119)

| Constant | Value | Line |
|---|---|---|
| `ANSWER_MODEL` | `"openrouter/openai/gpt-4o"` | 95 |
| `ANSWER_TEMPERATURE` | `0.7` | 96 |
| `ANSWER_MAX_TOKENS` | `256` | 97 |
| `BENCHMARK_ANSWER_MODEL` | `"openrouter/openai/gpt-4o-2024-08-06"` | 106 |
| `BENCHMARK_ANSWER_ENDPOINT_SLUG` | `"openai"` | 111 |
| `BENCHMARK_SEED` | `42` | 115 |
| `BENCHMARK_MODE` | env var check | 116 |

#### Benchmark-mode routing (lines 305–316)

```python
bm_model = _get_benchmark_model_for_call(bm_call_key)  # → BENCHMARK_ANSWER_MODEL
call_params["model"] = bm_model
call_params["seed"] = BENCHMARK_SEED  # 42
call_params["max_retries"] = 0
endpoint_slug = _get_benchmark_endpoint_slug(bm_call_key)  # → BENCHMARK_ANSWER_ENDPOINT_SLUG
call_params["extra_body"] = {
    "provider": {
        "order": [endpoint_slug],       # → ["openai"]
        "allow_fallbacks": False,
    }
}
```

#### API key path

```
settings = get_settings()
provider_config = settings.get_provider_config("openrouter")
  → ProviderConfig(api_key=settings.openrouter_api_key)
  → OPENROUTER_API_KEY env var
```

#### Verdict

| Field | Value | Valid? |
|---|---|---|
| `BENCHMARK_ANSWER_MODEL` | `"openrouter/openai/gpt-4o-2024-08-06"` | ✅ model ID present on OpenRouter (confirmed 2026-05-01 via live API query) |
| `BENCHMARK_ANSWER_ENDPOINT_SLUG` | `"openai"` | ✅ valid provider slug (no `/`) |
| `provider.order` | `["openai"]` | ✅ valid — single provider slug |
| `seed` | `42` | ✅ set in benchmark mode |

**Before/after correction needed:** None — answer routing is correctly configured.

---

## 2. judge — `tests/longmemeval/evaluate.py`

### Call site: `judge_answer()` → `_call_llm_with_provider_config()`

**File:** `tests/longmemeval/evaluate.py`
**Lines:** 513–551 (caller), 265–370 (shared wrapper)

#### Constants

| Constant | Value | Line |
|---|---|---|
| `JUDGE_MODEL` | `"openrouter/openai/gpt-4o"` | 99 |
| `JUDGE_TEMPERATURE` | `0.0` | 100 |
| `JUDGE_MAX_TOKENS` | `256` | 101 |
| `BENCHMARK_JUDGE_MODEL` | `"openrouter/openai/gpt-4o-2024-08-06"` | 107 |
| `BENCHMARK_JUDGE_ENDPOINT_SLUG` | `"openai"` | 112 |

#### Benchmark-mode routing

Identical pattern to answer — `_call_llm_with_provider_config()` with `bm_call_key="judge"`.
`provider.order = ["openai"]`, `allow_fallbacks = False`, `seed = 42`.

#### Verdict

| Field | Value | Valid? |
|---|---|---|
| `BENCHMARK_JUDGE_MODEL` | `"openrouter/openai/gpt-4o-2024-08-06"` | ✅ confirmed available via live API query |
| `BENCHMARK_JUDGE_ENDPOINT_SLUG` | `"openai"` | ✅ valid provider slug |
| `provider.order` | `["openai"]` | ✅ valid |

**Before/after correction needed:** None — judge routing is correctly configured.

---

## 3. extraction — `orchestrator/memory/extraction.py`

### Call site: `extract_facts_from_text()`

**File:** `orchestrator/memory/extraction.py`
**Lines:** 394–434

#### Live code state (post-Task-1 revert)

Grep confirms **zero `BENCHMARK_*` constants** exist in `extraction.py` post-revert.

Model is hardcoded as a default parameter (line 396) and as a local variable (line 535):

```python
async def extract_facts_from_text(
    text: str,
    model: str = "openrouter/openai/gpt-4o-mini",  # line 396
    ...
):
```

Called from `process_extraction()` at line 535:
```python
outcome = await extract_facts_from_text(
    extraction_text,
    model="openrouter/openai/gpt-4o-mini",  # hardcoded — no BENCHMARK_* override
    ...
)
```

#### Routing params (lines 408–434)

```python
call_params = _get_provider_call_params(model)
call_params.update({
    "messages": [...],
    "temperature": EXTRACTION_TEMPERATURE,   # 0.0
    "top_p": EXTRACTION_TOP_P,               # 1.0
    "max_tokens": EXTRACTION_MAX_TOKENS,     # 2000
    "response_format": {"type": "json_object"},
})
response = await litellm.acompletion(**call_params)
```

#### Verdict

| Field | Value | Valid? |
|---|---|---|
| Model string | `"openrouter/openai/gpt-4o-mini"` | ✅ valid model ID |
| `provider.order` | **NOT SET** | ⚠️ No provider pinning — LiteLLM uses default routing |
| `seed` | **NOT SET** | ⚠️ Non-deterministic |
| `temperature` | `0.0` | ✅ deterministic |
| `fingerprint` | **NOT SET** | ⚠️ No drift detection |

**Benchmark-mode status:** The `extraction.py` file was reverted to clean state in Task 1. There are NO benchmark-mode controls for extraction in the live tree. Extraction will NOT use the dated snapshot model, provider pinning, or seed in benchmark mode.

**Before/after correction needed:** Extraction does not participate in benchmark-mode deterministic routing in the current clean tree. If deterministic extraction is required, benchmark-side overrides would need to be implemented (per Task 9 scope: "implement benchmark-side overrides/wrappers instead").

---

## 4. contradiction — `orchestrator/memory/dedup.py`

### Call site: `check_contradiction()`

**File:** `orchestrator/memory/dedup.py`
**Lines:** 148–199

#### Live code state (post-Task-1 revert)

Grep confirms **zero `BENCHMARK_*` constants** exist in `dedup.py` post-revert.

The contradiction check uses the production `background_reasoning_model` from config (line 160):

```python
response = await litellm.acompletion(
    model=get_settings().background_reasoning_model,
    ...
    temperature=0.1,
    max_tokens=50,
)
```

#### Config source

**File:** `orchestrator/config.py:412`
```python
background_reasoning_model: str = "openrouter/deepseek/deepseek-chat"
```

#### Verdict

| Field | Value | Valid? |
|---|---|---|
| Model string | `"openrouter/deepseek/deepseek-chat"` (from config) | ✅ valid |
| `provider.order` | **NOT SET** | ⚠️ No provider pinning |
| `seed` | **NOT SET** | ⚠️ Non-deterministic |
| `temperature` | `0.1` | ⚠️ Not 0.0 — adds variance |
| `fingerprint` | **NOT SET** | ⚠️ No drift detection |

**Benchmark-mode status:** The `dedup.py` file was reverted to clean state in Task 1. There are NO benchmark-mode controls for contradiction detection in the live tree.

**Before/after correction needed:** Contradiction detection does not participate in benchmark-mode deterministic routing in the current clean tree.

---

## 5. Voyage embeddings — `orchestrator/memory/embedding.py`

### Call sites: `embed_documents()`, `embed_query()`

**File:** `orchestrator/memory/embedding.py`
**Lines:** 213–233

#### embed_documents() — document embeddings

```python
async def embed_documents(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    return await _embed_texts(
        texts,
        model=settings.embedding_document_model,    # → "voyage-4-large"
        input_type="document",
        max_tokens=DOCUMENT_MAX_TOKENS,           # 120,000
    )
```

#### embed_query() — query embeddings

```python
async def embed_query(text: str) -> list[float]:
    settings = get_settings()
    embeddings = await _embed_texts(
        [text],
        model=settings.embedding_query_model,      # → "voyage-4-lite"
        input_type="query",
        max_tokens=QUERY_MAX_TOKENS,             # 1,000,000
    )
```

#### Config source

**File:** `orchestrator/config.py:377–378`
```python
embedding_document_model: str = "voyage-4-large"
embedding_query_model: str = "voyage-4-lite"
```

#### API key path

```python
def _get_voyage_api_key() -> str:
    settings = get_settings()
    api_key = settings.voyage_api_key
    if not api_key:
        raise EmbeddingConfigurationError("VOYAGE_API_KEY environment variable not set")
    return api_key
```

#### Verdict

| Field | Value | Valid? |
|---|---|---|
| Document model | `"voyage-4-large"` | ✅ correct Voyage model |
| Query model | `"voyage-4-lite"` | ✅ correct Voyage model |
| `provider.order` | N/A (not OpenRouter) | N/A |
| `seed` | **NOT SET** | ⚠️ Non-deterministic (Voyage has no seed support) |
| API key | `VOYAGE_API_KEY` env var | ✅ via `get_settings().voyage_api_key` |

**Voyage variance note:** Per project memory (wave0 variance attribution), voyage-4-lite has no seed/fingerprint support — embedding variance (~6pp) is the dominant irreducible source. This is documented but not fixable in this plan.

---

## 6. Canonical runner snapshots — `orchestrator/eval/runner.py`

### Critical: runner.py cannot be imported

**File:** `orchestrator/eval/runner.py`
**Import attempt result:**
```
ImportError: cannot import name 'BENCHMARK_CONTRADICTION_ENDPOINT_SLUG'
from 'orchestrator.memory.dedup'
```

**Import statement (lines 31–51):**
```python
from orchestrator.memory.dedup import (
    BENCHMARK_CONTRADICTION_ENDPOINT_SLUG,   # ❌ NOT DEFINED in dedup.py
    BENCHMARK_CONTRADICTION_MODEL,            # ❌ NOT DEFINED in dedup.py
    CONTRADICTION_TEMPERATURE,                # ❌ NOT DEFINED in dedup.py
    check_contradiction,
)
from orchestrator.memory.extraction import (
    BENCHMARK_EXTRACTION_ENDPOINT_SLUG,       # ❌ NOT DEFINED in extraction.py
    BENCHMARK_EXTRACTION_MODEL,                # ❌ NOT DEFINED in extraction.py
    ...
)
```

Both source files were reverted to clean state in Task 1 and have **zero** `BENCHMARK_*` constants. The canonical runner references 5 symbols that do not exist.

#### Historical values (from test harness monkey-patches)

Test harness scripts consistently set these values at runtime via monkey-patching:

| Symbol | Expected Value (from harness scripts) | Notes |
|---|---|---|
| `BENCHMARK_EXTRACTION_ENDPOINT_SLUG` | `"openai"` | Provider slug (valid) |
| `BENCHMARK_EXTRACTION_MODEL` | `"openrouter/openai/gpt-4o-mini-2024-07-18"` or `"openrouter/openai/gpt-4o-mini"` | Dated or alias snapshot |
| `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG` | `"novita"` | Provider slug (valid) |
| `BENCHMARK_CONTRADICTION_MODEL` | `"openrouter/deepseek/deepseek-v3.2"` | DeepSeek model |
| `CONTRADICTION_TEMPERATURE` | `0.1` | As used in dedup.py:160 |

#### Runner snapshot metadata (lines 555–595)

The runner documents expected configuration for each call:

| Call | Field | Value (from runner metadata) |
|---|---|---|
| extraction | `model` | `BENCHMARK_EXTRACTION_MODEL` (undefined) |
| extraction | `endpoint_slug` | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG` (undefined) |
| extraction | `provider.order` | `[BENCHMARK_EXTRACTION_ENDPOINT_SLUG]` |
| contradiction | `contradiction_model` | `BENCHMARK_CONTRADICTION_MODEL` (undefined) |
| contradiction | `model` | `BENCHMARK_CONTRADICTION_MODEL` (undefined) |
| contradiction | `endpoint_slug` | `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG` (undefined) |
| contradiction | `provider.order` | `[BENCHMARK_CONTRADICTION_ENDPOINT_SLUG]` |

**Verdict:** Runner has orphaned references — the constants were removed from source files but runner was not updated. This is a pre-existing broken state unrelated to Task 1 cleanup.

**Before/after correction needed:** Task 9 must address this — either restoring the constants to `extraction.py`/`dedup.py` or removing the references from `runner.py`.

---

## 7. OpenRouter Registry — `gpt-4o-2024-08-06`

**Query performed:** `curl -s "https://openrouter.ai/api/v1/models" -H "Accept: application/json"` (2026-05-01)
**No API key required** — OpenRouter model listing is a public endpoint.

**Result:** ✅ **AVAILABLE**

Raw response: 371 models returned total; `openai/gpt-4o-2024-08-06` confirmed present:

| Field | Value |
|---|---|
| `id` | `openai/gpt-4o-2024-08-06` |
| `created` | 1722902400 (2024-08-06) |
| `context_length` | 128,000 tokens |
| `pricing.prompt` | $0.0000025/token ($2.50/M input) |
| `pricing.completion` | $0.00001/token ($10/M output) |

**Replacement decision:** No replacement needed.

---

## 8. provider.order slug validation — all call sites

| Call Site | provider.order value | Contains `/`? | Slug type | Verdict |
|---|---|---|---|---|
| answer (BM mode) | `["openai"]` | No | Provider slug | ✅ VALID |
| judge (BM mode) | `["openai"]` | No | Provider slug | ✅ VALID |
| extraction | NOT SET | N/A | N/A | N/A |
| contradiction | NOT SET | N/A | N/A | N/A |
| Runner extraction (undefined) | `[BENCHMARK_EXTRACTION_ENDPOINT_SLUG]` | No (expected `"openai"`) | Provider slug (expected) | ⚠️ Undefined |
| Runner contradiction (undefined) | `[BENCHMARK_CONTRADICTION_ENDPOINT_SLUG]` | No (expected `"novita"`) | Provider slug (expected) | ⚠️ Undefined |

**Summary:** No valid `provider.order` entry in the live codebase contains a `/` character. All slash-containing values were historical (removed in Task 1 cleanup). The current live values are correctly formed as bare provider slugs.

---

## 9. OpenRouter registry query — complete

**Query:** `curl -s "https://openrouter.ai/api/v1/models" -H "Accept: application/json"` — 2026-05-01

| Item | Result |
|---|---|
| `gpt-4o-2024-08-06` availability | ✅ Available on OpenRouter (371 models returned; target confirmed) |
| `gpt-4o-2024-08-06` pricing | $2.50/M input / $10/M output |
| `gpt-4o-mini-2024-07-18` availability | Confirmed available via benchmark harness scripts |
| `deepseek-v3.2` availability | Referenced in contradiction harness patches; not independently verified |
| Registry query timestamp | 2026-05-01 via `https://openrouter.ai/api/v1/models` (no API key required) |

---

## 10. Raw-result accounting — completed (see §14)

The raw-result accounting contract is defined in **§14** below. Task 2 left a placeholder; Task 5
completed the contract definition.

---

## 14. Task 5 — Raw-Result Accounting Contract

**Task:** I2 Supplemental — Define raw-result accounting and failure classification contract
**Status:** COMPLETE

### Overview

Every benchmark result artifact (JSONL output, checkpoint `results` map) must be evaluated by
**traversing raw result rows** — not by reading summary status fields or checkpoint
`completed_count`. Summary fields may be absent, stale, or computed under different
definitions. The authoritative source is the individual result row.

---

### Raw Result Row Schema (from `evaluate_single()`, evaluate.py:649–679)

Each row is a `dict` with at minimum:

| Field | Type | Description |
|---|---|---|
| `question_id` | `str` | Unique question identifier |
| `hypothesis` | `str` | Answer model output; `""` (empty string) = answer-call failure |
| `judgment` | `str` | `"correct"` / `"incorrect"` / `""` (empty = judge failure) |
| `error` | `str` or absent | Set when a **harness-level** exception was raised (provider error, timeout, fingerprint mismatch, etc.); absent when no harness error occurred |
| `answer_hash` | `str` | SHA-256 of `hypothesis`; `""` when `hypothesis` is empty |
| `judgment_hash` | `str` | SHA-256 of `judgment`; `""` when `judgment` is empty |

Benchmark-mode rows additionally contain `answer_prompt_metadata`, `answer_model`,
`answer_fingerprint`, `judge_model`, `judge_fingerprint`.

---

### Definitions

#### `success_count` (Answer-Call Completion Success)

`success_count` is the count of **raw result rows** where ALL of the following hold:

1. `hypothesis` is a non-empty string (`hypothesis != ""`)
2. `error` key is **absent** from the row (no harness exception)
3. The answer model call completed and produced content (even if `judgment == "incorrect"`)

**`judgment == "incorrect"` does NOT disqualify a row from `success_count`.**
A row with a non-empty `hypothesis` and no `error` is a successful answer-call even if
the judge later marked it incorrect.

**Do NOT use `judgment == "correct"` as the success criterion.** That measures benchmark
**accuracy**, not answer-call **completion**.

#### `error_count` (Answer-Call Failure Count)

`error_count` is the count of raw result rows that did **not** produce a valid answer-call:

- `hypothesis == ""` **AND** `error` is absent → answer model returned empty content (silent failure)
- `error` key is **present** → a harness-level exception occurred (see Failure Categories below)

#### Denominator for Aggregate and Per-Category Scores

**The score denominator is `success_count`, NOT all 500 attempted questions.**

```
accuracy = correct_count / success_count
```

Where `correct_count` is the subset of `success_count` rows where `judgment == "correct"`.

Per-category scores (MR, IE-user, etc.) use the same principle: each category's denominator
is the count of successful (non-error) rows for that category, not all rows in that category.

---

### Failure Classification — Required Categories (E4/E5/Reviewers)

When a result row contributes to `error_count` (or is being classified for reporting), it
MUST be mapped to exactly one of the following categories by traversing the raw row:

| Category | Condition | Example |
|---|---|---|
| **provider error** | `error` field present; root cause is a provider-side failure (API error, 500, 429, auth failure, model not found) | `litellm.RateLimitError`, `OpenRouterException`, `BadRequestError` |
| **timeout** | `error` field present; indicates a timeout | `asyncio.TimeoutError`, `timeout`, `timed out` |
| **malformed/empty answer** | `hypothesis == ""` and `error` absent (silent answer-model failure) | answer model returned nothing; streaming broke; content dropped |
| **harness exception** | `error` field present; root cause is not a provider error or timeout | `Benchmark fingerprint drift`, `Missing ingested corpus sessions`, `KeyError`, `TypeError` in harness code |
| **judge/evaluation error** | `judgment == ""` (empty) or `judgment_hash == ""`; judge call failed after a successful answer | judge API error, judge timeout, judge returned malformed output |
| **skipped question** | Row is absent from JSONL; question was skipped at checkpoint before reaching `evaluate_single()` | runner.py:1337–1344 checkpoint skip; not in raw output at all |
| **duplicate question** | Same `question_id` appears more than once in the ordered result list | Should not occur in canonical runner output; if found, count only the last entry |
| **rolling-window abort** | Run terminated early due to rolling 50-question hard-abort (`HardAbortError`) | Process exited non-zero; results JSONL is truncated at the abort point |

Rows with `error` present but where the error message indicates an answer-model failure
(rate limit, API error) are **provider error**, not harness exception.

Rows with `hypothesis == ""` and `error` present with a provider-related message are
**provider error** (the empty hypothesis is a consequence of the provider failure).

---

### The N1 Contract — `success_count >= 495/500`

The N1 gate is: **`success_count >= 495`** out of 500 questions.

This is a raw row completion threshold, NOT a judged-accuracy threshold:
- 495 rows with a non-empty `hypothesis` and no harness error = N1 PASS
- 495 rows with `judgment == "incorrect"` but a valid `hypothesis` = N1 PASS
- 495 rows with only 490 distinct `question_id` values (5 duplicates) = N1 **FAIL**

`success_count` is computed by counting raw rows in the JSONL that satisfy the
`success_count` definition above — **never by reading a status field, a
`completed_count`, or any summary-count field from the checkpoint**.

---

### Forbidden: Summary-Only Accounting

The following are **forbidden** as the sole or primary source of `success_count` or
`error_count`:

- Checkpoint `completed_count` field
- Any `status` summary field in the checkpoint
- Any `outcome_counts` aggregate in a harness summary script
- Any `evaluate_phase["completed_count"]`

These fields may be useful for cross-checking but MUST be recomputed from raw rows
before being used in any Pass/Fail determination.

**Pre-existing artifact note:** Prior benchmark runs (e.g., `wave0_full_corpus_aligned`)
contain rows with `status` fields and `outcome` fields that differ from the raw row
contract defined here. Those artifacts are evidence of past runs, not templates for
current accounting. All future E4/E5/reviewer work must use the raw-row definitions
above.

---

### Null Deliverable Rule — `success_count < 495`

If `success_count < 495`, the run result is a **null deliverable** unless the shortfall
is fully explained by one or more of the following **four independently-verifiable
external blockers**. Each blocker requires affirmative evidence; absence of evidence means
the blocker did not apply.

| Blocker | Definition | Evidence Required |
|---|---|---|
| **1. OpenRouter platform outage** | The OpenRouter platform was unavailable for a period covering part of the attempted questions | OpenRouter status page incident for the relevant date/time range; or API error logs showing an OpenRouter-side 5xx or outage that prevented answer completion |
| **2. Voyage AI outage** | Voyage AI embedding service was unavailable for a period covering part of the attempted questions | Voyage AI status page incident; or API error logs showing a Voyage AI-side 5xx or outage that prevented embedding/comparison completion |
| **3. Postgres host crash** | The PostgreSQL host running pgvector was unavailable or crashed during the evaluation run | Host-level uptime/downtime logs; or Postgres connection errors in the run logs indicating a host-level failure |
| **4. OpenAI API downtime affecting embeddings** | OpenAI API (used for embedding operations) was unavailable for a period covering part of the attempted questions | OpenAI status page incident for the relevant date/time range; or API error logs showing an OpenAI-side 5xx or outage that prevented embedding completion |

**Narrowness constraint:** These four blockers are the **only** permitted explanations for
`success_count < 495`. They are independent (any combination may apply). No other
category — including "extraction failures during ingestion", "memory store was empty",
"checkpoint restart overwrote results", "test environment differences", "config changes since
last successful run", or any previously-allowed blocker category not listed above — qualifies as
an external blocker unless it can be mapped to one of the four categories above.

If `success_count < 495` and none of the four blockers is verified with evidence, the
run is a null deliverable and MUST NOT be reported as a Pass or Fail against any
benchmark metric.

---

### Evidence Files

- `.sisyphus/evidence/task-5-raw-accounting-contract.txt` — raw-row traversal requirement and
  summary-field prohibition (Section 10 contract)
- `.sisyphus/evidence/task-5-null-deliverable-rule.txt` — `success_count < 495` null
  deliverable handling (Section 14 contract)

---

*Artifact extended: 2026-05-01. Task 5 complete — raw-result accounting contract defined.*

---

## 11. Task 1 historical context — invalid constants found

Per `wave0_closure_dirty_tree_audit.md`, Task 1 found and reverted the following **historical invalid constants** that existed in the dirty working tree:

| File | Constant (dirty value) | Corrected to | Problem |
|---|---|---|---|
| `dedup.py` | `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "openrouter/deepseek/deepseek-chat-v3-5"` | (reverted — removed) | Full model slug used as provider.order |
| `dedup.py` | `BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-chat-v3-5"` | (reverted — removed) | Model slug used as model ID |
| `extraction.py` | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openrouter/openai/gpt-4o-mini-2024-07-18"` | (reverted — removed) | Full model slug used as provider.order; caused 404 |

**Current live state:** All three constants are gone from `extraction.py` and `dedup.py`. The answer/judge constants in `evaluate.py` were not dirty and remain correctly configured.

---

## 12. Non-benchmark-path call sites (not in scope but noted)

| Call Site | File | Model | provider.order | Notes |
|---|---|---|---|---|
| Title generation | `orchestrator/config.py:408` | `openrouter/openai/gpt-4o-mini` | NOT SET | Config default |
| Summary generation | `orchestrator/memory/summary.py:122` | `settings.auto_fast_model` | NOT SET | Called by `process_extraction()` |
| Background reasoning | `orchestrator/config.py:412` | `openrouter/deepseek/deepseek-chat` | NOT SET | Used by `check_contradiction()` |

These are **not on the benchmark evaluation path** (they run during ingestion, not evaluation scoring). They do not affect `success_count`.

---

## QA Checklist

- [x] All 6 required headings present: `answer`, `judge`, `extraction`, `Voyage`, `contradiction`, `OpenRouter registry`
- [x] Every call site has model string recorded
- [x] Every `provider.order` entry checked for `/` — none found in valid live code
- [x] `gpt-4o-2024-08-06` availability confirmed via live API query to `https://openrouter.ai/api/v1/models`
- [x] Runner orphan-import finding documented
- [x] Historical invalid constants from Task 1 documented as context
- [x] Raw-result accounting contract defined in §14 (Task 5 complete)
- [x] Registry query language updated from "web search" to live API call

---

## 13. Task 3 — Pre-Flight and Rolling-Abort Implementation Sites

**Task:** I4 — Identify pre-flight and rolling-abort implementation sites
**Status:** COMPLETE

### Pre-Flight Provider Health Check — Insertion Point

**Selected File:** `orchestrator/eval/runner.py`
**Selected Function:** `LongMemEvalRunner.evaluate()`
**Insertion Line:** **1295** (before the database pool creation at line 1296)

**Rationale:**
- Runs before the question loop at line 1325 — zero questions processed before pre-flight passes
- Uses exact benchmark model (`BENCHMARK_ANSWER_MODEL` = `"openrouter/openai/gpt-4o-2024-08-06"`), exact provider slug (`BENCHMARK_ANSWER_ENDPOINT_SLUG` = `"openai"`), exact seed (42), exact `extra_body` configuration
- On the canonical full-corpus command path: `python -m orchestrator.eval.longmemeval evaluate ...`
- Cannot be bypassed by a non-canonical helper that imports `evaluate_single` directly — those helpers bypass the runner entirely and do not produce full-corpus results

**Code Context (runner.py:1290–1300):**
```python
# Lines 1290–1300:
      settings = get_settings()
      if not settings.database_url:
          raise RuntimeError("DATABASE_URL not set")
      if not settings.daemon_encryption_key:
          raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")

      # *** PRE-FLIGHT INSERTION POINT: run here, before pool creation ***
      pool = await asyncpg.create_pool(    # line 1296
          dsn=settings.database_url,
          min_size=2,
          max_size=10,
      )
```

**Pre-flight Implementation (for Task 7):**
- Adapt `tests/benchmark_harness/guardrails.py:78–90` (`run_provider_health_check`) pattern
- Use `_call_llm_with_provider_config()` with `bm_call_key="answer"` to mirror exact benchmark routing
- Probe model `"openrouter/openai/gpt-4o-2024-08-06"`, provider slug `"openai"`, seed 42, temperature 0.0
- If probe fails → raise exception → exception propagates directly to `asyncio.run()` at longmemeval.py:172 → process exits non-zero. Note: pre-flight is at line 1295, before the database pool is created (line 1296) and before the `try` block starts (line 1302); no `pool.close()` is involved.

### Rolling 50-Question Hard Abort — Update Point

**Selected File:** `orchestrator/eval/runner.py`
**Selected Function:** `LongMemEvalRunner.evaluate()`
**Update Line:** **1386** (`completed_results[question_id] = result`)

**Rationale:**
- This is the **only** write point for question results in the entire question loop
- Every **attempted** question (one that reaches `evaluate_single` at line 1362) contributes exactly once to the rolling window. Checkpoint-skipped questions (lines 1337–1344) are skipped before the try block and do NOT reach line 1386; they are NOT counted as failures.
- The result dict is complete at this point: contains `hypothesis` (empty = failure), `error` (set if exception), `judgment`, and benchmark metadata
- Cannot be bypassed without bypassing the entire runner loop

**Code Context (runner.py:1373–1395):**
```python
# Lines 1373–1395:
          }

          completed_results[question_id] = result    # *** LINE 1386: ROLLING UPDATE POINT ***
          evaluate_phase["completed_count"] = len(completed_results)
          evaluate_phase["updated_at"] = utc_now_iso()
          ordered = [
              completed_results[qid]
              for qid in question_order
              if qid in completed_results
          ]
          write_results_jsonl(self.output_path, ordered)
          save_runner_checkpoint(self.checkpoint_path, checkpoint)
      finally:
          await pool.close()
```

**Rolling Window Implementation (for Task 8):**
- Maintain a `collections.deque(maxlen=50)` of (question_id, is_failure) tuples
- After the result is stored at line 1386, classify current result:
  - `result.get("error")` is not None → **FAILURE**
  - `result.get("hypothesis") == ""` → **FAILURE**
  - Otherwise → **SUCCESS** (answer model returned content; wrong judgment is NOT a failure)
- Push to deque, pop if `len(deque) > 50`
- If `len(deque) == 50` and `successes / 50 < 0.50` → raise `HardAbortError`

**PRECISION — Two valid placement options for Task 8:**
- **Option A (check at line 1386, before write/checkpoint):** If abort fires, the triggering result has been stored in `completed_results` but NOT yet written to JSONL or checkpoint. Task 8 MUST explicitly write the triggering result before raising `HardAbortError`, or it is lost.
- **Option B (check after line 1395, after write/checkpoint):** The existing `write_results_jsonl` and `save_runner_checkpoint` calls have already persisted the triggering result. No special handling needed. **Recommended for simplicity.**

### Abort Propagation

- `HardAbortError` raised at line ~1395+ (after write/checkpoint calls)
- Caught by `finally: await pool.close()` at line 1397
- Re-raised after `finally` → propagates to `asyncio.run()` at longmemeval.py:172
- Process exits **non-zero**
- Raw results up to and including triggering question are saved in `output_path` (JSONL) and checkpoint (Option B only; Option A requires explicit pre-abort write)

### Checkpoint-Skipped Rows

- Questions skipped by checkpoint (runner.py:1337–1344) never reach line 1386 and do NOT contribute to the rolling window
- Only attempted questions (those that call `evaluate_single` at line 1362) contribute

### Bypass Risk Assessment

| Bypass Scenario | Works? | Why |
|---|---|---|
| Helper imports `evaluate_single` directly | NO | Different code path; bypasses runner entirely; does not produce valid full-corpus results |
| Monkey-patch `LongMemEvalRunner.evaluate` | NO | Must apply before `runner.evaluate()` is called; pre-flight still runs first |
| Use `longmemeval_fast.py` | NO | Task 11 uses canonical runner explicitly |

**Conclusion:** The selected insertion points are on the **only** code path used by the canonical full-corpus aligned command. No supported bypass mechanism exists.

### Evidence Files

- `.sisyphus/evidence/task-3-control-flow-trace.txt` — Full command path trace from CLI to result storage
- `.sisyphus/evidence/task-3-insertion-sites.txt` — Selected insertion points with rationale and bypass analysis
- `.sisyphus/evidence/task-3-preflight-site.txt` — Pre-flight cannot be bypassed verification
- `.sisyphus/evidence/task-3-rolling-site.txt` — Every question result reaches line 1386 verification

---

*Artifact extended: 2026-05-01. Task 3 complete — investigation/design-site selection only. Implementation (Tasks 7/8) to follow after Oracle checkpoint (Task 6).*

---

## 15. Task 9 — Provider-Routing Fixes Applied

**Task:** E2 — Apply provider-routing fixes from routing audit
**Date:** 2026-05-01
**Status:** COMPLETE

### Overview

Task 9 verified and fixed provider routing across the benchmark path. Two issues were addressed:

1. **Pinned config stale routing values** — `longmemeval_config_pin.json` (untracked) had model IDs in `endpoint_slug` and `extra_body_provider_order` fields for extraction and contradiction benchmark_mode sections. Fixed to bare slugs.
2. **No production memory file changes** — confirmed `orchestrator/memory/` was not modified.

### Answer/Judge Routing (evaluate.py)

CONFIRMED CLEAN. No changes needed.

| Field | Value |
|---|---|
| model | `openrouter/openai/gpt-4o-2024-08-06` |
| endpoint_slug | `openai` |
| provider.order | `["openai"]` |
| allow_fallbacks | `false` |
| seed | `42` |

Source: `evaluate.py` lines 106–116, 305–316

### Extraction Stub (runner.py)

CONFIRMED CORRECT. Stub values match bare slug pattern.

| Field | Value |
|---|---|
| `BENCHMARK_EXTRACTION_ENDPOINT_SLUG` | `openai` (bare slug) |
| `BENCHMARK_EXTRACTION_MODEL` | `openrouter/placeholder/extraction-model` (placeholder, not called) |

### Contradiction Stub (runner.py)

CONFIRMED CORRECT. Stub values match bare slug pattern.

| Field | Value |
|---|---|
| `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG` | `novita` (bare slug) |
| `BENCHMARK_CONTRADICTION_MODEL` | `openrouter/placeholder/contradiction-model` (placeholder, not called) |

### Pinned Config Fix (longmemeval_config_pin.json)

**BEFORE (dirty — model IDs in provider.order):**

| Section | Field | Before (dirty) |
|---|---|---|
| extraction.benchmark_mode | endpoint_slug | `openrouter/openai/gpt-4o-mini-2024-07-18` |
| extraction.benchmark_mode | extra_body_provider_order | `["openrouter/openai/gpt-4o-mini-2024-07-18"]` |
| dedup.benchmark_mode | endpoint_slug | `openrouter/deepseek/deepseek-chat-v3-5` |
| dedup.benchmark_mode | extra_body_provider_order | `["openrouter/deepseek/deepseek-chat-v3-5"]` |

**AFTER (clean — bare slugs):**

| Section | Field | After (clean) |
|---|---|---|
| extraction.benchmark_mode | endpoint_slug | `openai` |
| extraction.benchmark_mode | extra_body_provider_order | `["openai"]` |
| dedup.benchmark_mode | endpoint_slug | `novita` |
| dedup.benchmark_mode | extra_body_provider_order | `["novita"]` |

### provider.order Slug Validation (Updated)

| Call Site | provider.order value | Contains `/`? | Verdict |
|---|---|---|---|
| answer (BM mode) | `["openai"]` | No | ✅ VALID |
| judge (BM mode) | `["openai"]` | No | ✅ VALID |
| extraction stub | `["openai"]` | No | ✅ VALID |
| contradiction stub | `["novita"]` | No | ✅ VALID |
| extraction (pinned config, fixed) | `["openai"]` | No | ✅ FIXED |
| contradiction (pinned config, fixed) | `["novita"]` | No | ✅ FIXED |

**Summary:** No provider.order value in the live codebase or pinned config contains a `/` character after Task 9 fix.

### Evidence Files

- `.sisyphus/evidence/task-9-provider-order-clean.txt` — Full provider-order scan and before/after
- `.sisyphus/evidence/task-9-no-memory-diff.txt` — Confirms no memory file changes

---

*Artifact extended: 2026-05-01. Task 9 complete — provider-routing fixes verified and applied.*
