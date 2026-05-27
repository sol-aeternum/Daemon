# Wave 0 — Judge Determinism Reference Pack

**Date:** 2026-04-21
**Scope:** Judge model (`gpt-4o`) determinism references for LongMemEval benchmark reproducibility
**Status:** Source-backed; official/primary-source only

---

## 1. Official Documentation References

### 1.1 OpenAI Reproducible Outputs (Primary Source)

**Source:** [OpenAI Advanced Usage — Reproducible outputs](https://platform.openai.com/docs/guides/advanced-usage)
**Source:** [OpenAI Cookbook — Reproducible outputs with seed parameter](https://developers.openai.com/cookbook/examples/reproducible_outputs_with_the_seed_parameter)
**Source:** [OpenAI GitHub Cookbook — Reproducible_outputs_with_the_seed_parameter.ipynb](https://github.com/openai/openai-cookbook/blob/main/examples/Reproducible_outputs_with_the_seed_parameter.ipynb)

**Key citations:**

> "Chat Completions are non-deterministic by default (which means model outputs may differ from request to request). That being said, we offer some control towards deterministic outputs by giving you access to the `seed` parameter and the `system_fingerprint` response field."

> "`seed`: If specified, our system will make a **best effort** to sample deterministically, such that repeated requests with the same seed and parameters should return the same result. **Determinism is not guaranteed**, and you should refer to the `system_fingerprint` response parameter to monitor changes in the backend."

> "`system_fingerprint`: This fingerprint represents the backend configuration that the model runs with. It can be used in conjunction with the `seed` request parameter to understand when backend changes have been made that might impact determinism."

**Canonical quote on limitations:**

> "If the `seed`, request parameters, and `system_fingerprint` all match across your requests, then model outputs will be *mostly* deterministic. [...] This feature is in beta and only currently supported for `gpt-4-1106-preview` and `gpt-3.5-turbo-1106`."

*Note: The beta model constraint in the original cookbook has since expanded to newer models, but the "determinism not guaranteed" caveat remains in force across all models.*

### 1.2 LiteLLM Seed Parameter Passthrough (Primary Source)

**Source:** [LiteLLM Completion Input Params](https://docs.litellm.ai/docs/completion/input)
**Source:** [LiteLLM Streaming + Async](https://docs.litellm.ai/docs/completion/stream)

**Key citation:**

> "`seed`: integer or null (optional) — This feature is in Beta. If specified, our system will attempt to make deterministic samples — **Determinism is not guaranteed**, and you should refer to the `system_fingerprint` response parameter to monitor changes in the backend."

LiteLLM passes the `seed` parameter through to the OpenAI-compatible provider. LiteLLM does **not** implement its own seed logic; it relies on the upstream provider's implementation.

---

## 2. Community / Developer Evidence of Limitations

### 2.1 Seed Deprecation Discussion

**Source:** [OpenAI Community — Is the seed parameter getting deprecated?](https://community.openai.com/t/is-the-seed-parameter-getting-deprecated/1363139) (Oct 2025)

**Key citations:**

> "All OpenAI models now available are indeed non-deterministic. We don't know why."

> "The seed is part of the sampling that comes after logit calculation and softmax, which is meant to be random. You can ask the AI to roll 1d20 at temperature 1.5, and every call gets you different results because of the random token selection from all possible. Set the seed the same and you'd always get the same result back — except for the [backend changes that invalidate reproducibility]."

### 2.2 Seed Does Not Guarantee Determinism

**Source:** [OpenAI Community — Seed param and reproducible output do not work](https://community.openai.com/t/seed-param-and-reproducible-output-do-not-work/487245) (Nov 2023 – Aug 2025)

**Key citations:**

> "After many tries gpt-3.5-turbo-1106 produces different results on each call."

> "The seed inference parameter for reproducibility" — response from OpenAI staff: "we do our best" — "Is it because it's still a beta service? Are there any plans for further improvements?"

> "In my experience, the seeds 'do work by chance'. Like out of 5 requests I get 3 of them identical (not to mention the [fingerprints matching but outputs still diverging]."

---

## 3. Daemon Judge Configuration

From `tests/longmemeval/evaluate.py:91–96`:

```python
JUDGE_MODEL = "openrouter/openai/gpt-4o"
JUDGE_TEMPERATURE = 0.0
```

**Observation:**
- `temperature=0.0` eliminates sampling randomness at the logit-selection level, but does **not** eliminate backend configuration changes or GPU-level nondeterminism (batch scheduling, kernel selection, floating-point race conditions).
- The judge is called via LiteLLM through OpenRouter → OpenAI backend.
- No `seed` parameter is currently passed to the judge in the benchmark harness.
- `system_fingerprint` is **not** currently captured in the benchmark results.

---

## 4. Implications for LongMemEval Judge Reproducibility

| Factor | Controllable? | Impact |
|---|---|---|
| `temperature=0.0` | Yes (hardcoded) | Reduces logit-selection variance; does not eliminate backend variance |
| `seed` parameter | Partially (beta, not guaranteed) | Best-effort only; requires fingerprint matching for credibility |
| `system_fingerprint` tracking | Yes (not currently done) | Required for attribution when reproducibility fails |
| Provider-side model updates | No | Can invalidate reproducibility even with identical parameters |
| GPU kernel/batch nondeterminism | No | Infrastructure-level; outside user control |

**Conclusion:** The judge (gpt-4o via LiteLLM/OpenRouter) has **no path to guaranteed reproducibility**. Temperature=0 reduces but does not eliminate variance. Any reproducibility protocol must treat judge variance as a **measured risk**, not an assumed certainty, and must capture `system_fingerprint` on every call to enable post-hoc attribution.

---

## 5. Citation List

1. OpenAI. "Advanced Usage — Reproducible outputs." https://platform.openai.com/docs/guides/advanced-usage
2. OpenAI. "How to make your completions outputs consistent with the new seed parameter." https://developers.openai.com/cookbook/examples/reproducible_outputs_with_the_seed_parameter
3. OpenAI Cookbook. "Reproducible_outputs_with_the_seed_parameter.ipynb." https://github.com/openai/openai-cookbook/blob/main/examples/Reproducible_outputs_with_the_seed_parameter.ipynb
4. LiteLLM. "Completion Input Params." https://docs.litellm.ai/docs/completion/input
5. LiteLLM. "Streaming Responses & Async Completion." https://docs.litellm.ai/docs/completion/stream
6. OpenAI Community. "Is the seed parameter getting deprecated?" (Oct 2025). https://community.openai.com/t/is-the-seed-parameter-getting-deprecated/1363139
7. OpenAI Community. "Seed param and reproducible output do not work." (Nov 2023 – Aug 2025). https://community.openai.com/t/seed-param-and-reproducible-output-do-not-work/487245
