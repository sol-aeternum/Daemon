# Wave 0 Path A Routing Diagnosis

## Summary

The harness-local answer/judge routing fix is:

- keep the **model** field pinned to the dated LiteLLM/OpenRouter model id
  `openrouter/openai/gpt-4o-2024-08-06`
- change `extra_body.provider.order` to the **OpenRouter provider slug** `openai`
- keep `allow_fallbacks=false`, `seed=42`, and benchmark-mode fingerprint tracking intact

This matches the earlier extraction diagnosis pattern: the provider-order field must carry a
provider slug, not a full model slug.

## File:line evidence

### 1. Benchmark model string remains the full LiteLLM/OpenRouter model id

- `tests/longmemeval/evaluate.py:103-112`
  - `BENCHMARK_ANSWER_MODEL = "openrouter/openai/gpt-4o-2024-08-06"`
  - `BENCHMARK_JUDGE_MODEL = "openrouter/openai/gpt-4o-2024-08-06"`
  - `BENCHMARK_ANSWER_ENDPOINT_SLUG = "openai"`
  - `BENCHMARK_JUDGE_ENDPOINT_SLUG = "openai"`

Why this matters: LiteLLM still needs the full model id in `model=...` for deterministic
routing/fingerprint tracking, but OpenRouter only accepts the provider slug in
`extra_body.provider.order`.

### 2. `provider.order` value and extra_body shape

- `tests/longmemeval/evaluate.py:305-316`
  - benchmark mode sets `call_params["model"] = bm_model`
  - benchmark mode sets `call_params["seed"] = BENCHMARK_SEED`
  - benchmark mode sets:

```python
call_params["extra_body"] = {
    "provider": {
        "order": [endpoint_slug],
        "allow_fallbacks": False,
    }
}
```

- `tests/benchmark/test_provider_pinning.py:371-424`
  - answer assertion now checks `order == ["openai"]`
  - judge assertion now checks `order == ["openai"]`

### 3. API key / provider-config path

- `tests/longmemeval/evaluate.py:292-323`
  - answer/judge calls resolve `provider_config = settings.get_provider_config("openrouter")`
  - benchmark calls then pass `api_base`, `api_key`, and `extra_headers` from that provider config

- `orchestrator/config.py:323-326`
  - OpenRouter credentials/config live under:
    - `openrouter_api_key`
    - `openrouter_base_url`
    - `openrouter_referer`
    - `openrouter_title`

- `orchestrator/config.py:608-616`
  - `get_provider_config("openrouter")` returns `ProviderConfig(...)` with:
    - `base_url=self.openrouter_base_url`
    - `api_key=self.openrouter_api_key`

### 4. Answer path vs judge path

- `tests/longmemeval/evaluate.py:554-577` — **answer path**
  - uses `[system, user]` when `system_prompt` is present
  - calls `_call_llm_with_provider_config(..., bm_call_key="answer")`

- `tests/longmemeval/evaluate.py:513-528` — **judge path**
  - sends the judge prompt as a single user message
  - calls `_call_llm_with_provider_config(..., bm_call_key="judge")`

### 5. Pinned benchmark metadata updated to the live-routable contract

- `orchestrator/eval/runner.py:441-481`
  - benchmark-mode description now describes `order: [<provider_slug>]`
  - answer/judge pinned metadata both use `BENCHMARK_*_ENDPOINT_SLUG`

- `tests/benchmark_longmemeval/longmemeval_config_pin.json:10-53`
  - the pinned benchmark contract now records:
    - `description: ... order: [<provider_slug>]`
    - `endpoint_slug: "openai"`
    - `extra_body_provider_order: ["openai"]`

## Smoke-test implication

After this harness-local fix, the targeted `e47becba` smoke no longer fails with the prior
OpenRouter 404 (`No endpoints found for openai/gpt-4o-2024-08-06.`). The benchmark now reaches
both answer generation and judge evaluation, which is the intended routing-layer outcome.

The remaining smoke issue is no longer transport/routing — it is the content path captured in
`tests/benchmark_results/wave0_path_a_smoke_test.md`:

- one emitted result row for `e47becba`
- `provider_endpoint_slug = "openai"`
- prompt metadata captured successfully
- `memories_used = 0`
- incorrect live answer text

That means Path A is now exercising the live aligned answer path rather than failing in OpenRouter
provider selection.
