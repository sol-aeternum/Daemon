# Wave 0 TODO 14 Verification

**Date**: 2026-04-23
**Status**: DEFECTIVE — all three dimensions fail

---

## Dimension 1: `extra_body={"provider": {"order": [...], "allow_fallbacks": false}}` Presence on Benchmark LLM Calls

### Finding: NOT PRESENT

No benchmark-path LLM call site currently passes the `extra_body={"provider": {"order": [...], "allow_fallbacks": false}}` contract.

Evidence from call site audits:

| File | Function | Parameters Used | Has Provider Extra Body? |
|------|----------|-----------------|--------------------------|
| `tests/longmemeval/evaluate.py` | `_call_llm_with_provider_config()` | model, messages, temperature, max_tokens, timeout, api_base, api_key, extra_headers | **NO** |
| `orchestrator/memory/extraction.py` | `_get_provider_call_params()` | normalized model, timeout, api_base, api_key, extra_headers | **NO** |
| `orchestrator/memory/dedup.py` | `check_contradiction()` | model, messages, temperature, max_tokens, seed, max_retries | **NO** |

**Conclusion**: The provider routing contract specified in TODO 14 is absent from all benchmark-path LLM call sites. Benchmark runs do not enforce provider ordering or disable fallback routing.

---

## Dimension 2: Provider Slug Specificity

### Finding: DEFECTIVE — aliases used, not full endpoint slugs

Benchmark model strings in use:

- `openrouter/openai/gpt-4o-mini`
- `openrouter/openai/gpt-4o`
- `openrouter/deepseek/deepseek-chat`

These are **provider/model aliases**, NOT dated snapshots.

Per OpenRouter documentation:
- A base provider slug like `openai` matches **all** endpoints/regions
- Strict pinning requires the **full endpoint slug** from the model detail page
- `provider.order` alone is insufficient; `allow_fallbacks: false` is also required

**Example**: `openrouter/openai/gpt-4o-mini` routes to an unspecified OpenAI-compatible endpoint. There is no guarantee that:
1. The same physical endpoint is used across runs
2. Different endpoint selections do not introduce latency or routing variance

**Conclusion**: Provider slug specificity is insufficient for deterministic routing. Even if `extra_body` were present, the current alias-based model strings would not enable precise endpoint pinning.

---

## Dimension 3: Model String Alias vs Dated Snapshot

### Finding: ALIASES — not dated snapshots

The model strings `openrouter/openai/gpt-4o-mini`, `openrouter/openai/gpt-4o`, and `openrouter/deepseek/deepseek-chat` are **provider/model aliases**.

Implications:
- OpenRouter resolves these aliases at call time
- The resolved endpoint may change over time (e.g., region failover, model version updates)
- No version pinning is in place
- `DISABLE_BM_FINGERPRINT_FAIL_FAST=1` is not committed in `.env` (though the code honors it)

**Conclusion**: Model strings are aliases, not dated snapshots. This is a known source of non-determinism per OpenRouter's architecture.

---

## Summary

| Dimension | Status | Root Cause |
|-----------|--------|------------|
| `extra_body` provider contract | ❌ NOT PRESENT | No call site implements the TODO 14 contract |
| Provider slug specificity | ❌ DEFECTIVE | Alias slugs used, not full endpoint slugs |
| Model string type | ❌ ALIAS | Dated snapshots not in use; aliases only |

**Overall TODO 14 Status**: DEFECTIVE — none of the three sub-tasks are correctly implemented. The benchmark is running without provider routing controls, using only model aliases with no endpoint pinning.

---

## Impact on Prior Runs (ABL-1, ABL-2)

ABL-1 and ABL-2 both ran against `tests/benchmark_longmemeval/fixtures/dev_subset.json` without the TODO 14 controls. Any provider routing variance observed in those runs is consistent with this defect.

---

## Amendment — 2026-04-24

**This document is stale against later-revised Task 11 implementation.**

The TODO 14 verification above reflects the state of the benchmark harness as of the original ABL-1/ABL-2 runs. Subsequent Task 11 revisions introduced dated snapshot models, `extra_body` provider pinning, seed=42, and fingerprint enforcement into the harness answer path and judge path (see `wave0_deterministic_mode_coverage.md`). The DEFECTIVE verdicts above apply specifically to the pre-revision harness state.

**However, the Wave 0 halt decision does NOT rely on TODO 14.** The halt condition triggering this amendment was triggered by Step A (pre-full-corpus determinism question), not by TODO 14 status. The halt is documented in `wave0_closure_memo.md` and `wave0_halt_escalation.md`.

This amendment is additive. Prior content is preserved in full.
