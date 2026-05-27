# Wave 0 — Infrastructure Guardrails

**Generated:** 2026-04-25T00:00:00+00:00
**Status:** IMPLEMENTED
**Scope:** Tests-only — no production code changes

---

## Implemented Guardrails

### G1: Provider Health Check

**File:** `tests/benchmark_harness/guardrails.py`
**Function:** `run_provider_health_check(provider_slug="openai", model=...)`

Probes the extraction endpoint with a minimal litellm call before a long ingestion run.
Raises `RuntimeError` if the provider does not respond with content.

```python
from tests.benchmark_harness.guardrails import run_provider_health_check
run_provider_health_check()  # raises RuntimeError on failure
```

---

### G3: Extraction Errored-Floor Gate

**File:** `tests/benchmark_harness/guardrails.py`
**Function:** `check_errored_floor(checkpoint, max_errored_rate=5.0)`

Reads `checkpoint["phases"]["ingest"]["results"]`, counts `outcome=errored` sessions,
and raises `AssertionError` if errored rate exceeds `max_errored_rate` (default 5%).

```python
from tests.benchmark_harness.guardrails import check_errored_floor
check_errored_floor(checkpoint)  # raises AssertionError on breach
```

---

### G5: Credit Instrumentation (Log-Only)

**File:** `tests/benchmark_harness/guardrails.py`
**Function:** `log_credit_instrumentation(context="...")`

Logs available credit/quota info (`VIDEO_CREDITS_BALANCE`, `OPENROUTER_API_KEY` presence).
Always runs, never fails. Not a blocking guardrail.

---

## Documented But Not Implemented

These are non-critical for the current recovery sequence. They can be added
without structural changes if needed later.

| ID | Name | Reason not implemented |
|---|---|---|
| G2 | Post-ingestion minimum-memory gate | Not needed for current recovery — D5 rerun 2 shows healthy memory counts |
| G4 | Extraction log gate | Not needed for current recovery — extraction is functioning |

---

## Integration

```python
# Before ingestion
from tests.benchmark_harness.guardrails import run_provider_health_check
run_provider_health_check()

# After ingestion checkpoint exists
from tests.benchmark_harness.guardrails import check_errored_floor, log_credit_instrumentation
check_errored_floor(checkpoint)
log_credit_instrumentation("post_ingestion")
```

---

*Module: `tests/benchmark_harness/guardrails.py` (152 LOC)*
*Wave 0 — Daemon project*
