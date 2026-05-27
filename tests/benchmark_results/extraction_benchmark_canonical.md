# Canonical Extraction Benchmark Artifact

**Artifact Version:** 1.0
**Created:** 2026-05-27
**Plan:** `.sisyphus/plans/extraction-benchmark-recovery-rebuild.md` — Task 5
**Status:** `rebuild_required=false`

---

## 1. Path and Version

| Attribute | Value |
|-----------|-------|
| Canonical file | `tests/benchmark_extraction.py` |
| Benchmark version | `v2.4` |
| File size | 1288 lines |
| Git tracked | Yes |
| Git commits | `c4ae9376` (added) → `91ab1662` (last modified) |
| Branches present | `main`, `origin/main`, feature branches |

---

## 2. Scenario / Fact Contract

| Scenario | Name | Expected Facts |
|----------|------|---------------|
| S1 | Dense Personal Facts | 9 |
| S2 | Ephemeral vs Durable | 1 |
| S3 | Corrections/Supersession | 1 |
| S4 | Projects/Goals | 3 |
| S5 | Hedged Statements | 6 |
| S6 | Realistic Multi-Turn Session | 7 |
| S7 | Explicit Memory Instructions | 3 |
| S8 | Adversarial Empty | 0 |
| **Total** | | **30** |

**Contract:** exactly 8 scenarios, exactly 30 expected facts, confirmed by module data import.

---

## 3. Invocation

```bash
PYTHONPATH=. python tests/benchmark_extraction.py --json
```

For validation capture without saving to disk:

```bash
PYTHONPATH=. python tests/benchmark_extraction.py --json --no-save
```

**Note:** `PYTHONPATH=.` is required because lazy `orchestrator.*` imports (inside async functions at lines 442, 465, 466) only fire on execution, not at module import time. The `SCENARIOS` constant is importable without PYTHONPATH, but the full benchmark harness requires it.

---

## 4. Infrastructure Requirements

| Service | Host | Type | Safety |
|---------|------|------|--------|
| PostgreSQL | `localhost:5432` (rewritten from `postgres:5432`) | Disposable Docker | `_is_safe_db()` with `SAFE_HOSTS` guard |
| Redis | `localhost:6379` (rewritten from `redis:6379`) | Disposable Docker | `_is_safe_redis()` with `SAFE_HOSTS` guard |

**Safety guarantees:**
- `SAFE_HOSTS = ("localhost", "127.0.0.1", "postgres", "db", "0.0.0.0")`
- Benchmark refuses to run on non-SAFE_HOSTS database/Redis targets
- `db_wipe=true` wipes workspace between scenarios for isolation

---

## 5. External Services

| Service | Purpose | Fallback |
|---------|---------|----------|
| OpenRouter | Extraction model calls (GPT-4o-mini) | No local fallback |
| OpenRouter | Contradiction detection | No local fallback |
| Voyage AI | Embedding generation | OpenAI fallback (if configured) |

---

## 6. Internal Benchmark Health Thresholds

| Metric | Threshold | Notes |
|--------|-----------|-------|
| Precision (P) | `≥ 0.90` | Internal benchmark gate |
| Recall (R) | `≥ 0.90` | Internal benchmark gate |
| Adversarial FP (A) | `== 0` | Count of false positives in S8 |

**`A` definition for extraction gating:** When `A` is used for extraction benchmark gating, it is defined as `adversarial_fp` — the count of extracted facts from Scenario 8 (Adversarial Empty) that should have been zero-memory. An `A > 0` means the extraction system produced false positives against the adversarial scenario.

---

## 7. Downstream W1 Gate Thresholds

| Metric | Threshold | Label |
|--------|-----------|-------|
| Precision (P) | `≥ 0.95` | **W1 gate** |
| Recall (R) | `≥ 0.85` | **W1 gate** |

**Note:** These are the Wave 1 (W1) regression gate thresholds, distinct from the internal benchmark health thresholds above. The W1 gate requires higher precision (0.95 vs 0.90) but lower recall (0.85 vs 0.90) than the internal benchmark gate.

---

## 8. SCORECARD.md Accuracy/A Usage vs. Extraction Gating A

| Context | Metric | Meaning |
|---------|--------|---------|
| `SCORECARD.md` Accuracy column | `A` | Count of scenarios passed (e.g., `A=1` means 7/8 scenarios passed) |
| Extraction gating | `A` | `adversarial_fp` count (false positives from S8) |

**Important:** These are different definitions. The `Accuracy` column in `SCORECARD.md` is a scenario-pass rate (integer-like), not a false-positive count. When the extraction benchmark refers to `A=0` as a gate, it means `adversarial_fp=0`, not scenario accuracy.

---

## 9. Current Fresh Validation (Task 4, 2026-05-27)

| Attribute | Value |
|-----------|-------|
| Version | 2.4 |
| Scenarios | 8 |
| Expected facts | 30 |
| **Totals precision** | **1.00** |
| **Totals recall** | **1.00** |
| **adversarial_fp** | **0** |
| S8 extracted | 0 (expected 0) |
| db_wipe | true |
| decryption_available | true |
| Service failures | None |

**Note on Task 4 JSON structure:** The validation JSON uses top-level `totals` (not `overall`) for aggregate metrics.

---

## 10. Known S3 Dedup Advisory

**Issue:** Corolla expected inactive but remained active

| Pattern | Content | Expected Active | Actual Active | Pass |
|---------|---------|----------------|---------------|------|
| "Corolla" | User drives a 2019 Toyota Corolla | false | true | ❌ FAIL |
| "Corolla" | User sold the 2019 Toyota Corolla | false | true | ❌ FAIL |
| "Tesla" | User drives a 2023 Tesla Model 3 | true | true | ✅ PASS |

**Analysis:** The Corolla entries were expected to be superseded (active=false) since the user corrected to Tesla, but they remained active=true. This is the known S3 dedup substring matching asymmetry advisory. It is **not hidden** by aggregate P/R/A — the dedup_results are fully captured in the benchmark JSON. P/R/A = 1.00/1.00/0 because the Tesla Model 3 extraction was correctly matched as a true positive.

**No rebuild triggered** — this is a known advisory, not a producer regression.

---

## 11. Metric Lineage

### Historical Baselines

| Date | Version | Source | P | R | A | Notes |
|------|---------|--------|---|---|---|-------|
| 2026-03-27 | v2.3 | `SCORECARD.md` | 0.97 | 0.93 | 1 | GPT-4o-mini baseline; 7/8 scenarios passed; S6 and S8 failed |
| 2026-04-14 | v2.3→v2.4 | `extraction_benchmark_results.json` | 1.00 | 1.00 | 0 | 3-run deterministic transcript replay; all 3 runs = P=1.0, R=1.0, A=0 |

### Stale Metric Shorthand `P=1.00 R=0.93 A=0`

This shorthand appears in some project documentation. It is a **conflation of two different runs**, not a single valid current baseline:

- `P=1.00` — from the April 14 deterministic replay (v2.4 transition)
- `R=0.93` — from the March 27 GPT-4o-mini baseline (v2.3)
- `A=0` — from the April 14 deterministic replay (not `A=1` from the baseline)

**Current canonical baseline** is the Task 4 fresh validation (2026-05-27): **P=1.00, R=1.00, adversarial_fp=0**.

### Current Canonical Baseline (Task 4, 2026-05-27)

| Source | P | R | A (adversarial_fp) | Status |
|--------|---|---|---------------------|--------|
| `extraction_benchmark_results.json` (Apr 14) | 1.00 | 1.00 | 0 | Historical; 3-run median |
| Task 4 fresh validation (May 27) | 1.00 | 1.00 | 0 | **Current canonical** |

The `P=1.00 R=0.93 A=0` shorthand is **stale/composite/historical**, not a current canonical baseline.

---

## 12. Rejected Candidates

| Candidate | Reason Rejected |
|-----------|-----------------|
| `tests/benchmark/` | Provider pinning tests — not extraction benchmark; no 8-scenario/30-fact contract |
| `tests/benchmark_harness/` | Support infrastructure (harnesses, helpers, contradiction detection); none implement the 8-scenario/30-fact extraction contract |
| `tests/benchmark_longmemeval/` | Separate LongMemEval benchmark suite; different scenarios, metrics, and harness; explicitly warned against conflation |
| `tests/test_benchmark_extraction.py` | Unit tests for canonical benchmark harness functions; not a replacement for the benchmark itself |

---

## 13. rebuild_required

**`rebuild_required: false`**

Recovery is complete and confirmed:
- Canonical file `tests/benchmark_extraction.py` v2.4 exists and is git-tracked
- Structural validation passed (v2.4 marker, 8 scenarios, 30 facts, all-keyword word-boundary match_fact)
- Import validation passed (PYTHONPATH=. required for execution, module-level imports work without it)
- Fresh benchmark validation passed (P=1.00, R=1.00, adversarial_fp=0)
- No rebuild indicated

---

## 14. Required Verification Tokens

The following tokens must be present in any artifact claiming canonical status:

- [x] `tests/benchmark_extraction.py` (canonical path)
- [x] `v2.4` (benchmark version)
- [x] `8 scenarios` (scenario count)
- [x] `30 expected facts` (fact count)
- [x] `PYTHONPATH=. python tests/benchmark_extraction.py --json` (correct invocation)
- [x] `P≥0.90` (internal precision threshold)
- [x] `R≥0.90` (internal recall threshold)
- [x] `adversarial_fp==0` (adversarial gating condition)
- [x] `P≥0.95` (W1 gate precision)
- [x] `R≥0.85` (W1 gate recall)
- [x] `W1 gate` (explicit W1 label, not internal benchmark gate)
- [x] `rebuild_required=false` (recovery decision)
