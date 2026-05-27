# F6 — Fingerprint Policy Decision Memo (Wave 0)

**Generated:** 2026-04-24T13:29:38+00:00
**Scope:** Tests-only diagnostic — no production code changes
**Drives:** Wave 0 recovery sequence decision

---

## Decision Summary

Fingerprint drift in extraction is real but operationally irrelevant: it does not cause output divergence, and a fail-fast policy on fingerprint mismatch would incorrectly reject valid extraction results. The contradiction-path failure is a separate, independent routing bug (wrong model slug in `provider.order`) that must be fixed on its own merits and does not depend on any extraction-path fingerprint decision.

---

## Evidence Table

| Source | Finding | Relevance |
|---|---|---|
| `wave0_fingerprint_stability_measurement.md` | 10 runs, 2 distinct fingerprints (`fp_4181e24c46`, `fp_e61ea1dda4`), drift present, same model throughout | Fingerprint non-determinism confirmed for extraction path |
| `wave0_extraction_output_determinism.md` | 10/10 runs canonically identical (hash `bf1aef61b17292f9`), 0 non-identical pairs | Extraction output IS deterministic — fingerprint is decoupled from output |
| `wave0_ingestion_health_check_rerun.md` | 33.1% error rate (49/148 sessions): two distinct failure modes — extraction fingerprint mismatch AND contradiction check failure | Both paths are broken in the benchmark harness patch context |
| Direct probe: extraction model | `openrouter/openai/gpt-4o-mini-2024-07-18` valid; `provider.order=['openai']` succeeds | Extraction routing confirmed working |
| Direct probe: contradiction model | `openrouter/deepseek/deepseek-v3.2` valid; `provider.order=['deepseek']` fails with `deepseek-chat-v3-5 is not a valid model`; `provider.order=['novita']` succeeds | Contradiction routing bug is a provider/order mismatch, not a model unavailability issue |

---

## What Fingerprint Drift Means Here

`system_fingerprint` is a provider-side stability indicator returned by the OpenAI-compatible API. When `provider.order=['openai']` forces the extraction calls through the OpenAI provider backend on OpenRouter, the upstream model revision can flip between two internally-routed instances (hence two fingerprints in 10 calls, even at `seed=42`).

**Critically: this fingerprint drift is NOT the same as output non-determinism.**

- **Fingerprint determinism** = whether the provider returns the same `system_fingerprint` value across calls
- **Output determinism** = whether the extracted fact list is identical across calls

F1 proves fingerprint is non-deterministic. F2 proves output is deterministic. These are orthogonal properties.

The Wave 0 residual spread (measured ~6pp, per `wave0_variance_attribution_results.md`) is attributable to embedding provider nondeterminism (voyage-4-lite), NOT to extraction fingerprint drift. Fingerprint drift is a correlate of the underlying cause (provider-side model revision instability) but does not itself cause answer variance.

---

## Is Fail-Fast on Fingerprint Still Appropriate for Extraction?

**No.**

A fail-fast policy that rejects extraction results when `system_fingerprint` differs from an expected value would be incorrect because:

1. The extraction output IS deterministic (F2, 10/10 identical). Rejecting on fingerprint mismatch means rejecting perfectly valid results.
2. Fingerprint drift is a provider-side artifact, not an error condition. The upstream model revision changed; the downstream output did not.
3. The benchmark harness's expected fingerprint (`fp_e61ea1dda4`) is an arbitrary snapshot, not a correctness guarantee. Runs that got `fp_4181e24c46` produced the same canonical output.
4. Applying fail-fast to extraction would introduce false positives (valid outputs rejected) without improving output quality.

**Extraction-path policy: do NOT fail-fast on fingerprint mismatch. Treat fingerprint as diagnostic metadata only.**

---

## Is the Contradiction Path Fix Independent?

**Yes — fully independent.**

The two failure modes in D5 are categorically distinct:

| Failure Mode | Symptom | Root Cause | Fix Required |
|---|---|---|---|
| Extraction fingerprint drift | `"expected 'fp_e61ea1dda4', got 'fp_4181e24c46'"` | Provider-side model revision flip; output unaffected | None for tests-only recovery (output is deterministic) |
| Contradiction check failure | `"deepseek/deepseek-chat-v3-5 is not a valid model"` via OpenRouter deepseek endpoint | `provider.order=['deepseek']` routes to a model that does not support the contradiction task; `provider.order=['novita']` succeeds | Route contradiction to `provider.order=['novita']` in benchmark harness |

The contradiction-path bug exists independently of anything about extraction fingerprints. Fixing or not fixing extraction fingerprint policy has no effect on the contradiction routing failure, and vice versa. They can be addressed in parallel.

---

## Next Action Recommendation

For the **tests-only recovery path**:

1. **Extraction path — no code change needed.** Fingerprint is metadata only. The benchmark harness should be updated to **not assert on fingerprint equality** for extraction results. If the harness currently fails on fingerprint mismatch for extraction, that assertion should be removed or made non-fatal.

2. **Contradiction path — routing fix needed.** Change the benchmark harness's `provider.order` for the contradiction check from `['deepseek']` to `['novita']` (or another working provider order). This is a one-line routing fix in the test harness; it does not touch production code under `orchestrator/memory/`.

**Recommended order:** Fix contradiction routing first (it is a clear, isolated bug). Extraction fingerprint policy is already correctly handled by the evidence (no action needed — the outputs are deterministic regardless of fingerprint).

---

*Memo type: tests-only diagnostic*
*Drives: Wave 0 recovery sequence*
*Does NOT modify: any production code, any `orchestrator/memory/` files*
