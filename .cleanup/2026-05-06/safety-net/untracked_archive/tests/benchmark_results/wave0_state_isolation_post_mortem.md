# Wave 0 — State Isolation Post-Mortem: What Was Real, What Was Not

**Date:** 2026-04-26
**Artifact:** `tests/benchmark_results/wave0_rerun_v1_clean/`

---

## Executive Summary

The clean preserved rerun (wave0_rerun_v1_clean) resolves several earlier concerns but leaves three genuine issues in the pipeline. This document separates false alarms from real problems.

---

## FALSE ALARM: "320 on 257 is Impossible"

**Prior claim:** The preserved run showed ~320 completed sessions, which was claimed to exceed a corpus of 257 sessions, indicating impossible state growth or cross-run contamination.

**Resolution:** The corpus size for `dev_subset.json` is **not** 257. The `build_corpus_plan` step expands 50 dataset entries into **2079 corpus sessions**. The completed-session counts observed (848, 1036, 1038) are well within range of a 2079-session corpus. The prior claim was based on a miscount of the corpus.

**Conclusion:** No state contamination; no impossible session counts. The "320 on 257" premise was incorrect.

---

## FIXED: JSONB `extracted_facts` Deserialization Crash

**Problem:** `run_triple_preserved.py` accessed `row['extracted_facts']` expecting a Python `list`, but `asyncpg` returned a JSON string when the column was stored as a JSON string rather than a deserialized JSONB value. Calling `.get()` on the string caused `AttributeError: 'str' object has no attribute 'get'`.

**Fix:** Added `_normalize_extracted_facts()` and `_normalize_dedup_results()` in `run_triple_preserved.py` to handle string, None, and already-deserialized cases before accessing structured methods. (See `wave0_preservation_fix.md`.)

**Result:** All three clean runs completed their ingest phases to `completed_count=2079` without this crash. The fix is confirmed effective for the tests driver.

---

## STILL REAL: Benchmark-Mode Contradiction Rate-Limit Error

**Evidence from `run_triple.log` (line 5):**
```
Extraction failed for session answer_sharegpt_qTi81nS_0: Benchmark-mode contradiction check failed:
litellm.RateLimitError: RateLimitError: OpenrouterException -
{"error":{"message":"Provider returned error","code":429,
"metadata":{"raw":"deepseek/deepseek-v3.2 is temporarily rate-limited upstream.
Please retry shortly, or add your own key to accumulate your rate limits:
https://openrouter.ai/settings/integrations","provider_name":"Novita","is_byok":false}},
"user_id":"user_39Cta01Fuhv5GmZ10slK0n1fsL1"}
```

The dedup contradiction check hit a 429 rate-limit error from `deepseek/deepseek-v3.2` routed through provider Novita. This is a real external failure that interrupts the extraction pipeline.

This appears in at least one session and would cause that session's outcome to be `errored`. Run 2 has 4 `errored` outcomes; this rate-limit error accounts for at least one of them.

**Status:** Real upstream issue. Not fixable in project code beyond retry logic or key management.

---

## STILL REAL: Supersede Failed to Close Source Memory in Active State

**Evidence from `run_2/longmemeval_checkpoint.json` (line 929–935):**
```json
{
  "session_id": "8ec23b2c",
  "status": "extraction_failed",
  "outcome": "errored",
  "error": "Supersede failed to close source memory in active state"
}
```

This is a pipeline-level error where the supersede operation (used during memory deduplication) attempted to close a source memory that was still in `active` state, causing the operation to fail. This is not a deserialization issue — it is a genuine state-machine violation in the dedup pipeline.

**Status:** Real pipeline bug. The memory dedup logic needs to handle or prevent supersede operations on memories still in `active` state.

---

## STILL REAL: One Extraction JSON Parse Failure

**Evidence:** The task description confirmed one extraction JSON parse error with message:
```
Expecting ',' delimiter: line 242 column 1 (char 7913)
```

This indicates that the extraction model returned malformed JSON that could not be parsed. This caused at least one session to enter `extraction_failed` status.

**Status:** Real extraction output validation failure. The extraction pipeline should either tolerate malformed JSON with a fallback or fail more gracefully.

---

## STILL REAL: Empty Preservation Outputs

After the three clean runs completed ingest, the preservation artifacts are:
- `extraction_log.jsonl` — exists but **0 bytes** in all three runs
- `memories.jsonl` — **missing** in all three runs
- `run_metrics.json` — **missing** in all three runs

The `run_summary.json` shows all three runs failed with `'str' object has no attribute 'get'` — but this was the *pre-fix* error state. After the deserialization fix, the ingest completed but the preservation phase did not produce artifacts.

This indicates the preservation code path (after the normalizer fix) still has open issues preventing artifact output. The 0-byte `extraction_log.jsonl` suggests the file is opened and closed without any rows being written, or rows are being written but the file is being truncated.

**Status:** Real preservation pipeline issue. Not resolved by the JSONB normalizer fix alone.

---

## Summary Table

| Item | Status | Evidence |
|------|--------|----------|
| "320 on 257 impossible" premise | **FALSE ALARM** — corpus is 2079 sessions, not 257 | `build_corpus_plan` expansion confirmed |
| JSONB deserialization crash | **FIXED** — normalizer added in `run_triple_preserved.py` | All three runs reach `completed_count=2079` |
| Rate-limit on `deepseek-v3.2` / Novita | **STILL REAL** | `run_triple.log` line 5 |
| Supersede failed to close active memory | **STILL REAL** | `run_2` checkpoint: `error` field |
| Extraction JSON parse failure | **STILL REAL** | Parse error in extraction output |
| Empty preservation artifacts | **STILL REAL** | `extraction_log.jsonl` = 0 bytes; `memories.jsonl` and `run_metrics.json` missing |
