# Wave 0 — Preservation Fix: JSONB String Deserialization

**Date:** 2026-04-26
**Artifact:** `tests/benchmark_results/wave0_rerun_v1_clean/`

---

## Root Cause

The `run_triple_preserved.py` driver queries `memory_extraction_log` rows and accesses `row['extracted_facts']` and `row['dedup_results']` expecting Python `list`/`dict` objects. However, both columns are stored as PostgreSQL `JSONB` columns. When `asyncpg` returns a `JSONB` column, it deserializes it — but only if the column was *written* as a deserialized value. If the pipeline stored these columns as raw JSON strings (as can happen when JSON is serialized before写入), `asyncpg` returns the value as a Python `str`, not as the expected `list`/`dict`.

The crash occurred at preservation time with:

```
AttributeError: 'str' object has no attribute 'get'
```

This means `row['extracted_facts']` was a `str` and the code called `.get()` on it as if it were a `dict`.

The same applies to `row['dedup_results']` — it could arrive as a `str` instead of `dict`.

---

## The Fix (Tests-Only)

**File:** `tests/benchmark_harness/run_triple_preserved.py` (lines 88–108)

Two normalizer functions were added:

```python
def _normalize_extracted_facts(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return raw if isinstance(raw, list) else []


def _normalize_dedup_results(raw):
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}
```

These replace the raw `row['extracted_facts']` and `row['dedup_results']` assumptions at lines 215 and 225:

```python
facts = _normalize_extracted_facts(row['extracted_facts'])
...
dedup_results': _normalize_dedup_results(row['dedup_results']),
```

The normalizers handle all cases:
- Already-deserialized `list`/`dict` (pass-through)
- JSON string (parse and return)
- `None` or malformed (return safe empty)

---

## What This Fix Replaces

The earlier narrative around the preservation crash attributed the error to a missing guard in the main pipeline code. That analysis was incomplete. The crash was reproducible *in the tests driver* specifically because `run_triple_preserved.py` did not normalize the raw DB return values before treating them as structured objects.

The fix is **tests-only**: no production code under `orchestrator/memory/` was modified. It addresses only the artifact-writing path in the benchmark harness.

---

## Status After Fix

After this fix was applied, the three clean preserved runs (`wave0_rerun_v1_clean/run_{1,2,3}`) completed their ingest phases with `completed_count=2079` across all three runs. The `extraction_log.jsonl` files were created but are 0 bytes in all three runs, and `memories.jsonl` / `run_metrics.json` are missing entirely — indicating the preservation phase still did not produce output artifacts. The `'str' object has no attribute 'get'` crash was eliminated by the normalizers, but the artifact files remain empty, suggesting there are remaining issues in the preservation pipeline (see `wave0_state_isolation_post_mortem.md`).
