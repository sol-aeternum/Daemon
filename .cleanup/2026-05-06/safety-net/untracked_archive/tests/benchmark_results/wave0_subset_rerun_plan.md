# Wave 0 — Subset Rerun Execution Plan

**Generated:** 2026-04-25T00:30:00+00:00
**Status:** READY FOR EXECUTION
**Scope:** Tests-only — no production code changes, no `orchestrator/memory/` modifications

---

## Preconditions Satisfied

| Precondition | Source | Status |
|---|---|---|
| Extraction slug fixed (`"openai"`) | `wave0_d4_extraction_provider_override.md` | ✅ PASS |
| Extraction output deterministic | F2 (`wave0_extraction_output_determinism.md`): 10/10 canonical hash `bf1aef61b17292f9` | ✅ Stable |
| Contradiction model fixed (`deepseek-v3.2`) | `contradiction_single_verify.md` | ✅ PASS |
| Contradiction provider fixed (`novita`) | `contradiction_single_verify.md` | ✅ PASS |
| Fingerprint drift → diagnostic (not fatal) | F6 (`wave0_fingerprint_policy_decision.md`) | ✅ Policy set |
| D5 rerun PASS (0.0% errored, 210 sessions) | `wave0_ingestion_health_check_rerun.md` | ✅ Halt rule met |
| G1: Provider health check implemented | `wave0_infrastructure_guardrails.md` | ✅ Implemented |
| G3: Errored-floor gate implemented (5%) | `wave0_infrastructure_guardrails.md` | ✅ Implemented |
| Irreducible variance characterized (~6pp) | `wave0_variance_attribution_results.md` (project memory block lines 46–53) | ⚠️ Accepted |

---

## Subset Rerun Harness

**Entrypoint:** `tests/benchmark_harness/ingestion_rerun.py`

This script applies all 4 patches inline inside the subprocess before calling the production extraction/dedup paths:

| # | Patch | Module | Original → Patched |
|---|---|---|---|
| P1 | Extraction endpoint slug | `orchestrator.memory.extraction` | `'openrouter/openai/gpt-4o-mini-2024-07-18'` → `'openai'` |
| P2 | Extraction fingerprint | `orchestrator.memory.extraction.extract_facts_from_text` | catches `BenchmarkSamplingError` → returns empty outcome (diagnostic only) |
| P3 | Contradiction model | `orchestrator.memory.dedup` | `'openrouter/deepseek/deepseek-chat-v3-5'` → `'openrouter/deepseek/deepseek-v3.2'` |
| P4 | Contradiction provider | `orchestrator.memory.dedup` | `'openrouter/deepseek/deepseek-chat-v3-5'` → `'novita'` |
| P5 | Contradiction fingerprint | `orchestrator.memory.dedup.check_contradiction` | catches `DedupBenchmarkSamplingError` → returns `False` (advisory only) |

All patches are applied via `PATCH_CODE` string inside the subprocess — **no production files are modified**.

---

## Guardrail Integration

### Before ingestion (G1)

```python
from tests.benchmark_harness.guardrails import run_provider_health_check
run_provider_health_check()  # raises RuntimeError on failure
```

### After checkpoint exists (G3)

```python
from tests.benchmark_harness.guardrails import check_errored_floor
check_errored_floor(checkpoint)  # raises AssertionError if errored_rate > 5%
```

### After checkpoint (G5 — log only)

```python
from tests.benchmark_harness.guardrails import log_credit_instrumentation
log_credit_instrumentation("post_ingestion")
```

---

## Execution Sequence

```
1. G1: run_provider_health_check()
   └── RuntimeError → HALT if provider unreachable

2. ingestion_rerun.py (STEP 1: RESET)
   ├── Applies PATCH_CODE (P1–P5)
   ├── Calls reset_canonical_benchmark(pool)
   └── Exits non-zero → HALT

3. ingestion_rerun.py (STEP 2: INGEST)
   ├── Applies PATCH_CODE (P1–P5)
   ├── Runs LongMemEvalRunner.ingest() on dev_subset.json
   └── Exits non-zero → HALT

4. G3: check_errored_floor(checkpoint)
   └── AssertionError → HALT if errored_rate > 5%

5. G5: log_credit_instrumentation("post_ingestion")
   └── Log only, never halts
```

---

## Artifact Naming

The harness writes all outputs to the `wave0_ingestion_health_check_rerun/` directory (the same path used by the D5 ingestion health check). There is no per-run `{N}` subdirectory — the harness overwrites on each execution.

| Artifact | Location |
|---|---|
| Output dir | `tests/benchmark_results/wave0_ingestion_health_check_rerun/` |
| Ingestion checkpoint | `wave0_ingestion_health_check_rerun/longmemeval_checkpoint.json` |
| Ingestion results | `wave0_ingestion_health_check_rerun/longmemeval_results.jsonl` |
| Score output | `wave0_ingestion_health_check_rerun/longmemeval_score.json` |
| Ingestion log | `wave0_ingestion_health_check_rerun/ingest.log` |
| Run report | `tests/benchmark_results/wave0_ingestion_health_check_rerun.md` |

---

## Halt Conditions

| Condition | Signal | Action |
|---|---|---|
| G1 provider health check fails | `RuntimeError` | Do not proceed to ingestion |
| Reset step exit code ≠ 0 | subprocess returncode | Do not proceed to ingestion |
| Ingest step exit code ≠ 0 | subprocess returncode | Do not run G3 |
| G3 errored-floor breach (>5%) | `AssertionError` | Halt — recovery failed |
| Checkpoint missing after ingest | `FileNotFoundError` | Halt — data integrity issue |
| Keyboard interrupt / SIGTERM | Signal | Clean abort |

---

## Command

```bash
# Run from project root
PYTHONPATH=. python tests/benchmark_harness/ingestion_rerun.py
```

**Prerequisites:**
- Docker services running (`postgres`, `redis`)
- `BENCHMARK_MODE=1` set in environment (set by the script itself)
- `DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon` (set by the script)
- Provider API keys present in environment

**First run:** uses the `wave0_ingestion_health_check_rerun/` output naming scheme and runs fresh reset + ingest. If rerunning, manually remove `tests/benchmark_results/wave0_ingestion_health_check_rerun/` to avoid checkpoint conflicts.

---

## Expected Outcome

- **Sessions:** Same 210-session dataset as D5 rerun
- **Errored rate:** <5% (goal: 0%, accept up to 5%)
- **Variance:** ~6pp irreducible from voyage-4-lite embedding nondeterminism (per Wave 0 variance attribution results)
- **Completed sessions:** Target ≥133 (D5 rerun baseline)
- **Empty sessions:** Expected ~77 (not a halt condition — separate investigation if needed)

---

## What This Plan Does NOT Cover

- Full-corpus baseline run (blocked on embedding variance resolution)
- Production code changes under `orchestrator/memory/`
- Investigation of the 77 "empty" sessions
- G2 (post-ingestion minimum-memory gate) or G4 (extraction log gate) — documented as non-critical in `wave0_infrastructure_guardrails.md`

---

*Plan type: execution artifact*
*Drives: Wave 0 subset rerun*
*Does NOT modify: production code, any `orchestrator/memory/` files*
