# IR3 — Harness Monitoring / Verdict Bug — Wave 0 Full-Corpus Run

**Artifact:** `tests/benchmark_results/wave0_harness_monitoring_fix.md`
**Type:** Diagnosis — documentation only, no production code changes
**Date:** 2026-04-28
**Status:** REQUIRES FIX BEFORE VERDICT IS VALID

---

## Executive Summary

The Wave 0 full-corpus ingestion run produced a **spurious PASS verdict** (0.1% errored) because the harness verdict layer reads the `outcome` field from checkpoint results, but the canonical runner (`orchestrator/eval/runner.py`) writes the `status` field — and the two fields have **different value schemas**.

Specifically:
- 7,298 rows have `status="error"` → canonical `_extract_outcome()` maps this to `"errored"`
- Harness functions look for `outcome="errored"` → finds only 20 rows with `outcome="errored"` (the `extraction_failed` subset)
- Result: `errored_rate = 20/18475 ≈ 0.1%` instead of the correct `7298/18475 ≈ 39.5%`

**The PASS verdict is invalid.** A corrected errored-floor check would FAIL (39.5% ≫ 5% threshold).

---

## Bug Chain

### Step 1 — Canonical runner writes checkpoint results

In `orchestrator/eval/runner.py`, the ingest phase writes results with a `status` field:

```python
# runner.py lines 1159–1163
result = {
    "session_id": corpus_session.canonical_session_id,
    "status": "error",          # ← written here
    "error": str(exc),
}
```

Or, on success:

```python
result = {
    "session_id": ...,
    "status": "complete",        # ← written here
    ...
}
```

The runner does **not** write an `outcome` field at this point.

---

### Step 2 — Canonical `_extract_outcome()` maps `status` → `outcome`

When the runner generates the `run_metrics.json` artifact, it uses the canonical mapping:

```python
# runner.py lines 761–770
def _extract_outcome(status: str) -> str:
    if status in ("completed", "complete"):
        return "completed"
    if status == "extraction_failed":
        return "errored"         # extraction_failed → errored
    if status == "extraction_timeout":
        return "timed_out"
    if status == "error":
        return "errored"         # error → errored (THIS IS THE KEY MAPPING)
    return "unknown"
```

This mapping is applied inside `build_run_metrics_artifact()` when aggregating counts for the run metrics artifact. It is **not** applied when writing the checkpoint.

---

### Step 3 — Harness `summarize()` reads the wrong field

The harness `summarize()` function in `ingestion_rerun_full_corpus.py` (lines 184–208) reads `outcome` directly:

```python
def summarize(checkpoint: dict[str, Any]) -> dict[str, Any]:
    results = checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    outcome_counts: dict[str, int] = {"completed": 0, "errored": 0, "empty": 0}
    for r in results.values():
        outcome = r.get("outcome", "unknown")   # ← reads 'outcome'
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
```

Since the checkpoint never had an `outcome` field written, `outcome = "unknown"` for all rows, and the `errored` bucket stays at 0.

The function also reads `status` but only to populate `status_counts` (for display), not to derive outcomes.

---

### Step 4 — Harness `check_errored_floor()` has the same bug

In `tests/benchmark_harness/guardrails.py` (lines 98–138), `check_errored_floor()` also reads `outcome`:

```python
def check_errored_floor(checkpoint: dict[str, Any], ...) -> dict[str, Any]:
    results = checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    outcome_counts = {"completed": 0, "errored": 0, "empty": 0}
    for r in results.values():
        outcome = r.get("outcome", "unknown")   # ← same bug
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
    errored_rate = outcome_counts.get("errored", 0) / total * 100
```

Again, `outcome="errored"` finds only the 20 rows where someone happened to write that value. The 7,298 `status="error"` rows are invisible.

---

### Step 5 — Spurious PASS verdict

With `errored_count = 20` instead of `7298`:
- `errored_rate = 20/18475 * 100 ≈ 0.1%`
- `0.1% ≤ 5% threshold` → **PASS**

This is wrong. The correct rate would be `(20 + 7298) / 18475 * 100 ≈ 39.5%`.

---

## Checkpoint Evidence

From the authoritative full-corpus checkpoint:

| Field | Count |
|---|---|
| `status="complete"` | 11,157 |
| `status="extraction_failed"` | 20 |
| `status="error"` | 7,298 |
| **Total rows** | **18,475** |
| `outcome="errored"` (any value) | ~20 (visible to harness) |
| `outcome_missing` | ~18,455 (harness sees as "unknown") |

The checkpoint was read via `load_checkpoint_or_fail()` and the counts confirmed against the raw checkpoint JSON.

---

## Canonical Mapping Reference

The canonical `status` → `outcome` mapping in `runner.py::_extract_outcome()` is:

| `status` value | `outcome` result |
|---|---|
| `"completed"`, `"complete"` | `"completed"` |
| `"extraction_failed"` | `"errored"` |
| `"extraction_timeout"` | `"timed_out"` |
| `"error"` | `"errored"` |
| *(any other)* | `"unknown"` |

A correct errored count should be:
```
errored = count(status="extraction_failed") + count(status="error")
        = 20 + 7298
        = 7318
errored_rate = 7318 / 18475 * 100 ≈ 39.6%
```

---

## Minimal Fix Surface (Tests-Only)

The fix is entirely within `tests/benchmark_harness/`. No production code changes required.

### File 1: `tests/benchmark_harness/ingestion_rerun_full_corpus.py`

**Function:** `summarize()` (lines 184–208)

**Current (buggy):**
```python
def summarize(checkpoint: dict[str, Any]) -> dict[str, Any]:
    results = checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    outcome_counts: dict[str, int] = {"completed": 0, "errored": 0, "empty": 0}
    status_counts: dict[str, int] = {"complete": 0, "extraction_failed": 0}
    for r in results.values():
        outcome = r.get("outcome", "unknown")
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
        status = r.get("status", "unknown")
        if status in status_counts:
            status_counts[status] += 1
        ...
```

**Fixed (apply canonical mapping):**
```python
def _extract_outcome(status: str) -> str:
    """Mirror of runner.py::_extract_outcome — must stay in sync."""
    if status in ("completed", "complete"):
        return "completed"
    if status == "extraction_failed":
        return "errored"
    if status == "extraction_timeout":
        return "timed_out"
    if status == "error":
        return "errored"
    return "unknown"

def summarize(checkpoint: dict[str, Any]) -> dict[str, Any]:
    results = checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    outcome_counts: dict[str, int] = {"completed": 0, "errored": 0, "empty": 0, "unknown": 0}
    status_counts: dict[str, int] = {"complete": 0, "extraction_failed": 0, "error": 0}
    for r in results.values():
        status = r.get("status", "unknown")
        outcome = _extract_outcome(status)       # ← apply canonical mapping
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
        if status in status_counts:
            status_counts[status] += 1
        ...
```

The `errored` bucket now correctly accumulates both `status="extraction_failed"` and `status="error"` rows.

---

### File 2: `tests/benchmark_harness/guardrails.py`

**Function:** `check_errored_floor()` (lines 98–138)

**Current (buggy):**
```python
def check_errored_floor(checkpoint: dict[str, Any], ...) -> dict[str, Any]:
    results = checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    outcome_counts = {"completed": 0, "errored": 0, "empty": 0}
    for r in results.values():
        outcome = r.get("outcome", "unknown")
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
    errored_rate = outcome_counts.get("errored", 0) / total * 100
```

**Fixed (apply canonical mapping):**
```python
def _extract_outcome(status: str) -> str:
    """Mirror of runner.py::_extract_outcome."""
    if status in ("completed", "complete"):
        return "completed"
    if status == "extraction_failed":
        return "errored"
    if status == "extraction_timeout":
        return "timed_out"
    if status == "error":
        return "errored"
    return "unknown"

def check_errored_floor(checkpoint: dict[str, Any], ...) -> dict[str, Any]:
    results = checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    outcome_counts = {"completed": 0, "errored": 0, "empty": 0, "unknown": 0}
    for r in results.values():
        status = r.get("status", "unknown")
        outcome = _extract_outcome(status)       # ← apply canonical mapping
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
    errored_rate = outcome_counts.get("errored", 0) / total * 100
```

---

### File 3: Dev-subset harness (if `summarize()` logic is shared)

If the dev-subset harness (`tests/benchmark_harness/ingestion_rerun.py` or similar) shares the same `summarize()` pattern, it must receive the same fix. A shared helper module (`tests/benchmark_harness/_outcome_utils.py` or similar) would reduce duplication but is not required — the minimal fix is to copy the `_extract_outcome()` function into each harness file that needs it.

---

## What a Corrected Verdict Would Show

After applying the fix and re-running the verdict logic against the same checkpoint:

| Metric | Buggy (current) | Corrected |
|---|---|---|
| `errored_count` | 20 | 7,318 |
| `errored_rate` | 0.1% | 39.6% |
| `complete_count` | ~11,157 | 11,157 |
| G3 threshold | 5% | 5% |
| Verdict | **PASS** | **FAIL** |

The run would FAIL the errored-floor guardrail with a corrected rate of ~39.6% (well above the 5% threshold).

---

## Root Cause Classification

| Aspect | Value |
|---|---|
| **Immediate cause** | Harness verdict layer reads `outcome` field; checkpoint has only `status` field |
| **Underlying cause** | `summarize()` and `check_errored_floor()` reimplement outcome counting without mirroring the canonical `_extract_outcome()` mapping from `runner.py` |
| **Scope** | Tests-only (`tests/benchmark_harness/`) |
| **Production impact** | None directly — but the invalid PASS could trigger incorrect downstream decisions (baseline acceptance, Oracle gating, tag work) |
| **Fix complexity** | Trivial — add ~8-line `_extract_outcome()` function and change 2 lines per affected harness |

---

## Relationship to IR1

IR1 established that the full-corpus checkpoint exists with 18,475 total rows and the status distribution described above. IR3 builds on IR1 by identifying **why the harness reported PASS despite 39.6% of rows having `status="error"`**.

The fix is a prerequisite to any subsequent baseline, Oracle, or tagging work — those activities should not proceed from an invalid PASS.

---

## Verification

After applying the fix:

1. Re-run `summarize()` against the existing checkpoint — `errored_rate` should show ~39.6%
2. Re-run `check_errored_floor()` against the existing checkpoint — it should raise `AssertionError` with `errored=39.6% (max=5.0%)`
3. Confirm no production code (`orchestrator/`) was modified

No re-ingestion required — the fix operates on the already-written checkpoint.

---

*Document: `tests/benchmark_results/wave0_harness_monitoring_fix.md`*
*IR3 — Wave 0 — Harness Monitoring Bug Diagnosis*
