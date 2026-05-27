# Wave 0 — Full-Corpus Baseline Plan

**Generated:** 2026-04-25
**Status:** PLAN ONLY — not yet approved for execution
**Scope:** Tests-only — no production code changes, no `orchestrator/memory/` modifications

---

## Caveat

This document is a **plan only**. It does not constitute approval to run. The orchestrator
must explicitly choose to proceed. The full-corpus baseline consumes significant time and
provider resources. Embedding variance (~6pp irreducible, per `wave0_variance_attribution_results.md`)
remains present and is not addressed by this plan.

---

## Preconditions Satisfied

All preconditions are carried forward from the subset rerun (`wave0_subset_rerun_plan.md`).
The full-corpus baseline builds on the same patched harness and guardrails.

| Precondition | Source | Status |
|---|---|---|
| Extraction slug fixed (`"openai"`) | `wave0_d4_extraction_provider_override.md` | ✅ PASS |
| Extraction output deterministic | F2 (`wave0_extraction_output_determinism.md`): 10/10 canonical hash `bf1aef61b17292f9` | ✅ Stable |
| Contradiction model fixed (`deepseek-v3.2`) | `contradiction_single_verify.md` | ✅ PASS |
| Contradiction provider fixed (`novita`) | `contradiction_single_verify.md` | ✅ PASS |
| Fingerprint drift → diagnostic (not fatal) | F6 (`wave0_fingerprint_policy_decision.md`) | ✅ Policy set |
| Subset rerun PASS (0.0% errored, 210 sessions) | `wave0_ingestion_health_check_rerun.md` | ✅ Halt rule met |
| G1: Provider health check implemented | `wave0_infrastructure_guardrails.md` | ✅ Implemented |
| G3: Errored-floor gate implemented (5%) | `wave0_infrastructure_guardrails.md` | ✅ Implemented |
| G5: Credit instrumentation (log-only) | `wave0_infrastructure_guardrails.md` | ✅ Implemented |
| Irreducible variance characterized (~6pp) | `wave0_variance_attribution_results.md` (project memory block lines 46–53) | ⚠️ Accepted |
| Patches P1–P5 available in harness | `ingestion_rerun.py:41–99` (PATCH_CODE) | ✅ Available |

---

## Full-Corpus Scope

| Item | Value |
|---|---|
| Dataset | `/tmp/longmemeval-review/data/longmemeval_s.json` (full canonical corpus) |
| Unique normalized sessions | ~18,464 (per HARNESS.md §10) |
| Output naming scheme | `full_corpus_baseline/run{N}` (per HARNESS.md line 367) |
| Tests-only harness | `tests/benchmark_harness/ingestion_rerun.py` (PATCH_CODE P1–P5) |

The full-corpus dataset is bootstrapped by `tests/longmemeval/ingest.py:ensure_dataset()` if
absent at the cached path. The harness in this plan uses the patched
`ingestion_rerun.py` entrypoint, which is the same tests-only pattern used for the
subset rerun, but pointed at the full-corpus dataset and an output directory that does
not conflict with the subset rerun artifacts.

---

## Proposed Execution Command

The full-corpus baseline is driven by the patched `ingestion_rerun.py` harness with
two modifications from the subset rerun invocation:

1. **Output directory:** `wave0_full_corpus_baseline/` (not `wave0_ingestion_health_check_rerun/`)
2. **Dataset path:** `/tmp/longmemeval-review/data/longmemeval_s.json` (not `dev_subset.json`)

A dedicated harness variant — `tests/benchmark_harness/ingestion_rerun_full_corpus.py` — should
be created before execution, applying the same PATCH_CODE (P1–P5) as `ingestion_rerun.py` but
with the full-corpus output path and dataset path. The command to run after that file is created:

```bash
# Run from project root — full-corpus baseline
PYTHONPATH=. python tests/benchmark_harness/ingestion_rerun_full_corpus.py
```

The proposed harness file `tests/benchmark_harness/ingestion_rerun_full_corpus.py` follows the
same structure as `ingestion_rerun.py` but with these overrides:

```python
OUTPUT_DIR = Path("tests/benchmark_results/wave0_full_corpus_baseline")
DATASET = Path("/tmp/longmemeval-review/data/longmemeval_s.json")
REPORT_FILE = OUTPUT_DIR.parent / "wave0_full_corpus_baseline.md"
```

If creating a dedicated harness file is not desired, the same effect can be achieved by
running `ingestion_rerun.py` with an environment-variable override for `OUTPUT_DIR` and
`DATASET` before calling `runner.ingest()` — but a separate harness file is preferred to
avoid accidental cross-contamination with the subset rerun checkpoint.

---

## Guardrail Order

G1 → reset/ingest → G3 → G5, identical to the subset rerun sequence.

### Before ingestion (G1)

```python
from tests.benchmark_harness.guardrails import run_provider_health_check
run_provider_health_check()  # raises RuntimeError on failure
```

### After reset step (STEP 1)

```python
# Subprocess returncode check only
# Non-zero → HALT before ingest
```

### After ingest step (STEP 2) and checkpoint exists (G3)

```python
from tests.benchmark_harness.guardrails import check_errored_floor
check_errored_floor(checkpoint)  # raises AssertionError if errored_rate > 5%
```

### After G3 passes (G5 — log only)

```python
from tests.benchmark_harness.guardrails import log_credit_instrumentation
log_credit_instrumentation("post_ingestion")  # log only, never halts
```

---

## Artifact Destinations

All artifacts are written to `tests/benchmark_results/wave0_full_corpus_baseline/`.

| Artifact | Location |
|---|---|
| Output dir | `tests/benchmark_results/wave0_full_corpus_baseline/` |
| Ingestion checkpoint | `wave0_full_corpus_baseline/longmemeval_checkpoint.json` |
| Ingestion results | `wave0_full_corpus_baseline/longmemeval_results.jsonl` |
| Score output | `wave0_full_corpus_baseline/longmemeval_score.json` |
| Ingestion log | `wave0_full_corpus_baseline/ingest.log` |
| Run report | `tests/benchmark_results/wave0_full_corpus_baseline.md` |

These are distinct from the subset rerun artifacts in `wave0_ingestion_health_check_rerun/`
and do not overwrite them.

---

## Halt / Abort Conditions

| Condition | Signal | Action |
|---|---|---|
| G1 provider health check fails | `RuntimeError` | Do not proceed to reset |
| Reset step exit code ≠ 0 | subprocess returncode | Do not proceed to ingest |
| Ingest step exit code ≠ 0 | subprocess returncode | Do not run G3 |
| G3 errored-floor breach (>5%) | `AssertionError` | Halt — recovery failed |
| Checkpoint missing after ingest | `FileNotFoundError` | Halt — data integrity issue |
| Dataset file missing | `FileNotFoundError` | Halt — bootstrap required first |
| Keyboard interrupt / SIGTERM | Signal | Clean abort |

---

## What This Plan Does NOT Cover

- Evaluation and scoring phases (Phase 2 / Phase 3) — these run after the full-corpus
  ingest checkpoint is established and G3 passes
- Variance gate assessment — the full-corpus reproducibility protocol (3-run spread ≤ 3pp)
  is addressed separately after ingest/evaluate/score completes for each run
- Embedding variance reduction — ~6pp irreducible from voyage-4-lite embedding
  nondeterminism; not fixable via configuration changes
- Production code changes under `orchestrator/memory/`

---

## Embedding Variance Acknowledgment

Per `wave0_variance_attribution_results.md` (project memory block lines 46–53):
- **ABL-1** (all fixes ON, seed=42, BENCHMARK_MODE=1, fingerprint bypass): **28.0% (14/50)**
- **ABL-2** (identical config): **34.0% (17/50)**
- **Residual spread: 6pp** — irreducible without embedding provider changes
- `voyage-4-lite` has no seed/fingerprint support — embedding variance is the dominant
  irreducible source

The full-corpus baseline will exhibit this same variance. Results should be interpreted
as falling within the characterized distribution, not as regressions or improvements
relative to any single run.

---

*Plan type: execution plan*
*Drives: Wave 0 full-corpus baseline (future execution)*
*Does NOT modify: production code, any `orchestrator/memory/` files*
