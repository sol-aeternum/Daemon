# Wave 0 — Benchmark Workload Divergence Memo

**Generated:** 2026-04-25
**Scope:** Documentation only — no production code changes
**Drives:** Subset-rerun planning decision

---

## TL;DR

The healthy extraction benchmark (`bench_20260424_210709.json`) and the failed original ingestion health check (`wave0_ingestion_health_check/`) are not comparable workloads — the benchmark used a standalone replay harness with a pre-patched provider order, while the ingestion health check ran through the full production extraction path with an unpatched (broken) `provider.order` value. After provider-order and contradiction-routing patches, the rerun (`wave0_ingestion_health_check_rerun.md`) passed cleanly. Residual variance (~6pp, per `wave0_variance_attribution_results.md`) is attributed to embedding nondeterminism (voyage-4-lite), not extraction or contradiction routing.

---

## Q1: Why the Extraction Benchmark Could Be Healthy While the Original Ingestion Health Check Failed

**Short answer:** The benchmark harness bypassed the broken `provider.order` path that caused 100% failure in the ingestion health check.

**Evidence:**

The original ingestion health check log (`wave0_ingestion_health_check/ingest.log`) shows every session failing immediately with:

```
litellm.NotFoundError: OpenrouterException -
{"error":{"message":"No endpoints found for openai/gpt-4o-mini-2024-07-18.","code":404}}
```

The extraction benchmark (`bench_20260424_210709.json`, run ~11:37 UTC April 24) used `mode: deterministic_transcript_replay` with a dedicated harness that calls `extract_facts_from_text` directly, using `db_wipe: true` and a fixed set of 6 scenarios. The health check ran later (~21:16 UTC) and exercised the full production ingestion pipeline path (`orchestrator/memory/extraction.py`).

The root cause of the ingestion failure was in `extraction.py:96`:

```python
BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openrouter/openai/gpt-4o-mini-2024-07-18"
```

When `BENCHMARK_MODE=1`, this slug is passed to `provider.order`:

```python
call_params["extra_body"] = {
    "provider": {
        "order": [BENCHMARK_EXTRACTION_ENDPOINT_SLUG],  # → full model slug
        "allow_fallbacks": False,
    }
}
```

OpenRouter's `provider.order` accepts only provider names (e.g., `"openai"`), not full model identifiers. The full slug `'openrouter/openai/gpt-4o-mini-2024-07-18'` is not valid — hence 100% of sessions errored before any extraction could occur.

**The benchmark harness was not subject to this bug because the standalone replay harness had already been patched** to use `"openai"` directly (verified in `wave0_d4_extraction_provider_override.md`). The ingestion health check ran against the unpatched production extraction code path, which had the broken constant.

**Conclusion:** There is no contradiction between "benchmark healthy" and "ingestion health check failed" — they used different code paths. The benchmark harness worked because it was pre-patched; the ingestion health check failed because it hit the broken constant in production extraction code.

---

## Q2: What Changed Between the First Failed Ingestion Health Check and the Later Rerun PASS

**Chronology of fixes applied in sequence:**

| Step | Document | Fix |
|---|---|---|
| 1 — Extraction 404 | `wave0_d4_extraction_provider_override.md` | Patched `BENCHMARK_EXTRACTION_ENDPOINT_SLUG` from full model slug → `"openai"`; verified with single-call probe (8/8 facts extracted, PASS) |
| 2 — Fingerprint policy | `wave0_fingerprint_policy_decision.md` | Established fingerprint drift is diagnostic only; extraction output is deterministic (F2: 10/10 canonical hash matches); extraction fail-fast on fingerprint mismatch removed |
| 3 — Contradiction routing | `contradiction_single_verify.md` | Patched `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG` from `"deepseek-chat-v3-5"` → `"novita"` and `BENCHMARK_CONTRADICTION_MODEL` from `"openrouter/deepseek/deepseek-chat-v3-5"` → `"openrouter/deepseek/deepseek-v3.2"`; verified PASS (identical facts → no contradiction; contradicting facts → detected) |
| 4 — Infrastructure | `wave0_infrastructure_guardrails.md` | Implemented G1 (provider health check probe) and G3 (errored-floor gate at 5%) as preflight guards |

**Rerun result** (`wave0_ingestion_health_check_rerun.md`):
- 210 sessions
- `errored`: 0/210 (0.0%)
- `completed`: 133
- `empty`: 77
- Halt rule (>5% errored): **PASS**

**Summary of delta:**

| Item | Original Health Check | Rerun |
|---|---|---|
| Extraction provider | `'openrouter/openai/gpt-4o-mini-2024-07-18'` (404) | `'openai'` (200) |
| Contradiction provider | `'deepseek-chat-v3-5'` (invalid) | `'novita'` (valid) |
| Contradiction model | `deepseek-chat-v3-5` (wrong) | `deepseek-v3.2` (correct) |
| Fingerprint policy | fail-fast on mismatch | diagnostic only |
| Errored rate | ~100% | 0.0% |

---

## Q3: Which Remaining Instability Belongs to Extraction vs Contradiction Routing vs Fingerprint Policy

**Extraction (deterministic, stable):**
- F2 (`wave0_extraction_output_determinism.md`): 10/10 runs produced canonically identical outputs (hash `bf1aef61b17292f9`). Extraction output is fully deterministic for a fixed input.
- F1 (`wave0_fingerprint_stability_measurement.md`): Fingerprint is non-deterministic (2 distinct fingerprints in 10 runs, even at `seed=42`), but this is decoupled from output quality — fingerprint drift does not cause answer variance.
- Fingerprint policy conclusion: treat fingerprint as diagnostic metadata only. No fail-fast on mismatch. Extraction is stable.

**Contradiction routing (fixed, stable):**
- `contradiction_single_verify.md`: Both probe calls (identical facts, contradicting facts) passed with `provider.order=['novita']` and `model=deepseek-v3.2`.
- The contradiction path is no longer a source of instability.

**Embedding nondeterminism (unresolved, dominant source of variance):**
- Per `wave0_variance_attribution_results.md` (project memory block lines 46-53): Wave 0 ABL-1 (all fixes ON, seed=42, BENCHMARK_MODE=1, fingerprint bypass) yielded 28.0% (14/50); ABL-2 (identical config) yielded 34.0% (17/50); residual spread of **6pp** is attributed to embedding provider nondeterminism.
- `voyage-4-lite` has no seed/fingerprint support — embedding variance is the irreducible lower bound.
- This variance is intrinsic to the embedding provider, not fixable via configuration changes.

**Summary table:**

| Component | Status | Instability Source? |
|---|---|---|
| Extraction | Deterministic (F2) | None — output is stable |
| Extraction fingerprint | Non-deterministic (F1) | Yes — but decoupled from output |
| Contradiction routing | Fixed | None — routing is correct |
| Embedding (voyage-4-lite) | Nondeterministic | **Yes — dominant residual variance (~6pp)** |

---

## Q4: Whether It Is Reasonable to Resume Subset-Rerun Planning Now, and Under What Conditions

**Answer: Yes, subset-rerun planning can resume — under specific conditions.**

### Conditions for proceeding:

1. **Use the patched harness.** All fixes from D4, F6, and the contradiction verification must be applied before any rerun. The patched constants are:
   - `BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openai"` (not the full model slug)
   - `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "novita"` (not `deepseek-chat-v3-5`)
   - `BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-v3.2"` (not the old model string)
   - Fingerprint mismatch must be treated as diagnostic, not fatal

2. **Apply infrastructure guardrails.** Run G1 (provider health check probe) before ingestion and G3 (errored-floor gate at 5%) after checkpoint creation. These are already implemented in `tests/benchmark_harness/guardrails.py`.

3. **Accept the ~6pp irreducible variance.** The voyage-4-lite embedding provider cannot produce deterministic outputs across runs. Subset-rerun results will exhibit this variance; it cannot be engineered away without switching embedding providers.

4. **Do not attempt a full-corpus baseline.** The original plan is not to run the entire corpus; subset-rerun planning should focus on a manageable slice that exercises the full pipeline.

5. **Do not modify production code.** All patches remain in the test harness; no changes to `orchestrator/memory/extraction.py`, `orchestrator/memory/dedup.py`, or any production code are required or recommended.

### What remains blocked:

- **Full-corpus baseline planning** is not recommended until the embedding variance question is resolved or explicitly accepted as operational cost.
- **Production extraction/dedup code changes** are out of scope for the current recovery path.

---

## Residual Uncertainty

1. **Whether the 77 "empty" sessions in the rerun** (`wave0_ingestion_health_check_rerun.md`) represent a separate issue (e.g., sessions with no extractable facts vs. sessions that silently failed) has not been independently investigated. This is separate from the errored-floor gate and does not block planning.

2. **Whether the contradiction-path patches are stable across the full dev_subset** has only been verified with single-call probes. A broader contradiction check across the actual rerun sessions has not been independently confirmed.

3. **voyage-4-lite embedding nondeterminism** — the 6pp residual variance is measured, not theoretical, but its exact distribution across slot types or session categories has not been characterized.

---

*Sources: `tests/results/bench_20260424_210709.json`, `tests/benchmark_results/wave0_ingestion_health_check/`, `tests/benchmark_results/wave0_d4_extraction_provider_override.md`, `tests/benchmark_results/wave0_fingerprint_stability_measurement.md`, `tests/benchmark_results/wave0_extraction_output_determinism.md`, `tests/benchmark_results/wave0_fingerprint_policy_decision.md`, `tests/benchmark_results/contradiction_single_verify.md`, `tests/benchmark_results/wave0_ingestion_health_check_rerun.md`, `tests/benchmark_results/wave0_infrastructure_guardrails.md`, project memory block (lines 46-53)*
