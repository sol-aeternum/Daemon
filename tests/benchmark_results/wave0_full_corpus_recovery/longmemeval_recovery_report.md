# Wave 0 — Full-Corpus Recovery Report

**Generated:** 2026-04-28T18:03:02Z
**Status:** RECOVERY COMPLETE
**Harness:** `tests/benchmark_harness/ingestion_rerun_recovery.py`

---

## Recovery Summary

| Item | Value |
|---|---|
| Original baseline sessions | 18,475 |
| Error sessions (status="error") | 7,298 |
| Sessions preserved from baseline | ~11,177 (complete + extraction_failed) |
| Recovery sessions processed | 7,298 |
| Total sessions (merged) | 18475 |
| Errored (corrected) | 0.5% |
| Wall time | 45565s |

## Outcome Counts (canonical mapping applied)

| Outcome | Count |
|---|---|
| completed | 18385 |
| errored | 90 |
| empty | 0 |

## Status Counts (checkpoint status field)

| Status | Count |
|---|---|
| complete | 18385 |
| extraction_failed | 90 |
| error | 0 |

## G3 Guardrail (Errored Floor ≤ 5%)

| Result | Value |
|---|---|
| Errored rate | 0.5% |
| Threshold | 5.0% |
| Verdict | PASS |

## Sample Errors

```
Supersede failed to close source memory in active state
Supersede failed to close source memory in active state
Supersede failed to close source memory in active state
Supersede failed to close source memory in active state
Supersede failed to close source memory in active state
```

---

*Recovery harness: `tests/benchmark_harness/ingestion_rerun_recovery.py`*
