# Wave 0 — Extraction Determinism Reference Pack

**Date:** 2026-04-21
**Scope:** Extraction model (`gpt-4o-mini` via OpenRouter) determinism references for LongMemEval benchmark reproducibility
**Status:** Source-backed; official/primary-source only

---

## 1. Official Documentation References

### 1.1 OpenAI Reproducible Outputs (Primary Source)

**Source:** [OpenAI Advanced Usage — Reproducible outputs](https://platform.openai.com/docs/guides/advanced-usage)
**Source:** [OpenAI Cookbook — Reproducible outputs with seed parameter](https://developers.openai.com/cookbook/examples/reproducible_outputs_with_the_seed_parameter)

**Key citations:**

> "Chat Completions are non-deterministic by default (which means model outputs may differ from request to request). That being said, we offer some control towards deterministic outputs by giving you access to the `seed` parameter and the `system_fingerprint` response field."

> "`seed`: If specified, our system will make a **best effort** to sample deterministically, such that repeated requests with the same seed and parameters should return the same result. **Determinism is not guaranteed**, and you should refer to the `system_fingerprint` response parameter to monitor changes in the backend."

### 1.2 LiteLLM Seed Parameter Passthrough (Primary Source)

**Source:** [LiteLLM Completion Input Params](https://docs.litellm.ai/docs/completion/input)

**Key citation:**

> "`seed`: integer or null (optional) — This feature is in Beta. If specified, our system will attempt to make deterministic samples — **Determinism is not guaranteed**, and you should refer to the `system_fingerprint` response parameter to monitor changes in the backend."

LiteLLM passes the `seed` parameter through to the OpenAI-compatible provider without transformation.

---

## 2. Daemon Extraction Configuration

From `orchestrator/memory/extraction.py:50–52` and `orchestrator/memory/extraction.py:428–430`:

```python
EXTRACTION_TEMPERATURE = 0.0
EXTRACTION_TOP_P = 1.0
EXTRACTION_MAX_TOKENS = 2000
```

```python
"temperature": EXTRACTION_TEMPERATURE,   # 0.0
"top_p": EXTRACTION_TOP_P,               # 1.0
"max_tokens": EXTRACTION_MAX_TOKENS,     # 2000
```

The extraction call is made via LiteLLM to `openrouter/openai/gpt-4o-mini`.

**Critical observation:**
- `temperature=0.0` and `top_p=1.0` are the most restrictive sampling settings available, minimizing but not eliminating nondeterminism.
- No `seed` parameter is passed in the current implementation.
- `system_fingerprint` is not captured from the response.
- The extraction prompt template SHA256 is pinned in `longmemeval_config_pin.json`; the prompt content itself (system prompt + user input) varies per corpus session, so identical-parameter reproducibility is only possible for repeated calls with **identical input text**.

---

## 3. Extraction-Specific Variance Considerations

### 3.1 Prompt Variation Amplifies Variance

Unlike the judge (which evaluates a fixed hypothesis/reference pair), extraction operates over unique per-session conversation content. Even infinitesimal differences in how the extraction model parses borderline content can produce divergent `memory_content` outputs, which then:

1. Produce different embedding vectors (if extracted content differs even slightly)
2. Trigger different dedup outcomes (merge vs. supersede vs. store new)
3. Lead to different downstream retrieval sets
4. Ultimately affect judge accuracy indirectly

### 3.2 Extraction Timeout as a Variance Source

From `HARNESS.md §8` and `tests/longmemeval/ingest.py`:

The corrected two-stage barrier uses:
- Primary: `await process_extraction(...)` — authoritative fence
- Secondary: `poll_extraction_complete()` with 5.0s max wait, 0.1s initial poll, 2.0x backoff, 2.0s cap

When the deadline is exhausted, sessions are marked `extraction_timeout` in the checkpoint and ingestion continues. Extraction timeouts are therefore **a deterministic artifact of network latency and provider throughput**, not of the model itself — but they represent a form of pipeline variance if the timeout boundary is reached inconsistently across runs.

---

## 4. Provenance of Extraction Variance

| Source | Controllable? | Variance Impact |
|---|---|---|
| Temperature / top_p settings | Yes (hardcoded 0.0/1.0) | Minimal residual |
| `seed` parameter | Partially (beta, best-effort) | Not currently used |
| `system_fingerprint` tracking | Yes (not done) | Required for post-hoc variance attribution |
| Prompt content variation | No (per-session text) | Amplifies any model nondeterminism |
| Extraction timeout boundary | Partially (5.0s fixed) | Can produce inconsistent "complete" vs "timeout" outcomes |
| Provider-side model updates | No | Can change extraction behavior between runs |

---

## 5. Seed Best-Effort Caveat

**Explicit statement:** Seed support for `gpt-4o-mini` via OpenRouter/LiteLLM is best-effort and reproducibility requires **fingerprint recording and validation** rather than blind trust.

Even if `seed` were added to the extraction call:
1. The OpenAI backend does not guarantee determinism with seed set
2. `system_fingerprint` must match across runs for reproducibility to be credible
3. The embedding pipeline (Voyage) has no seed parameter and conditional nondeterminism
4. Therefore, extraction reproducibility cannot be achieved through seed alone even in the ideal case

---

## 6. Citation List

1. OpenAI. "Advanced Usage — Reproducible outputs." https://platform.openai.com/docs/guides/advanced-usage
2. OpenAI. "How to make your completions outputs consistent with the new seed parameter." https://developers.openai.com/cookbook/examples/reproducible_outputs_with_the_seed_parameter
3. LiteLLM. "Completion Input Params." https://docs.litellm.ai/docs/completion/input
4. LiteLLM. "Streaming Responses & Async Completion." https://docs.litellm.ai/docs/completion/stream
