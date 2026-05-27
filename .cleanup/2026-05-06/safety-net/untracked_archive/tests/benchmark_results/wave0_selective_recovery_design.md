# IR4 — Selective Recovery Plan for Wave 0 Full-Corpus Errored Sessions

**Artifact:** `tests/benchmark_results/wave0_selective_recovery_design.md`
**Type:** Recovery design (documentation + minimal helper stub)
**Date:** 2026-04-28
**Status:** READY FOR EXECUTION

---

## Executive Summary

The Wave 0 full-corpus run produced 7,298 `status="error"` rows (39.6% error rate) due to a contiguous PostgreSQL service disruption (IR1). This document designs a **selective, tests-only re-ingestion path** that reprocesses exactly those 7,298 sessions without disturbing the 11,157 already-successful sessions.

**Key constraint violated by naive re-run:** `reset_canonical_benchmark()` wipes ALL rows for `TEST_USER_ID` from 7 core tables. A full reset would destroy 11,157 successful sessions. Recovery must therefore run **without DB reset**.

**Key constraint of the runner:** The ingest loop skips sessions whose `corpus_key` appears in `existing_results`, regardless of status. Error rows must therefore be **removed from the checkpoint** before recovery.

**Recovery identifier:** `corpus_key` (SHA256 of normalized session messages) — NOT `session_id`. Verified: all 7,298 error corpus_keys map 1:1 to sessions in the dataset (18,475 total sessions confirmed).

---

## 1. Checkpoint Schema Analysis

### Authoritative fields per result row

| Field | Present in `status="error"` rows | Present in `status="complete"` rows |
|---|---|---|
| `session_id` | ✅ | ✅ |
| `corpus_key` | ✅ (primary lookup key) | ✅ |
| `raw_session_ids` | ✅ (1-entry array) | ✅ |
| `conversation_id` | ❌ (not written) | ✅ |
| `message_count` | ❌ (not written) | ✅ |
| `status` | ✅ (`"error"`) | ✅ (`"complete"`) |
| `outcome` | ❌ (not written) | ✅ (`"empty"` or `"completed"`) |
| `error` | ✅ (`"[Errno 111] Connect call failed..."`) | ❌ |

**Key insight:** `corpus_key` IS present in ALL error rows — it is written by `result["corpus_key"] = corpus_session.corpus_key` in `runner.py:1165` BEFORE the session is attempted. This makes `corpus_key` the stable recovery identifier.

### Session ID vs corpus_key

- `session_id` (e.g., `"ultrachat_384753"`) is NOT unique: the same session may appear in multiple questions' haystacks
- `corpus_key` is a content-addressed SHA256 of normalized session messages — unique per session
- **Conclusion:** Use `corpus_key` for all recovery operations

---

## 2. Data Source Verification

**Dataset:** `/tmp/longmemeval-review/data/longmemeval_s.json`
- 500 question items, each with `haystack_sessions` (list of conversations)
- Total unique sessions across all items: **18,475** (verified by computing corpus_keys for all individual sessions)
- Error corpus_keys: **7,298** (verified subset of 18,475 — 100% overlap)
- This confirms the failed sessions are still present in the dataset and can be re-extracted

**Corpus key computation:**
```python
def normalize_session_messages(messages):
    normalized = []
    for msg in messages:
        role = str(msg.get('role', 'user')).strip().lower() or 'user'
        content = ' '.join(str(msg.get('content', '')).split())
        normalized.append({'role': role, 'content': content})
    return json.dumps(normalized, separators=(',', ':'), ensure_ascii=True)

def build_corpus_key(messages):
    return sha256(normalize_session_messages(messages).encode('utf-8')).hexdigest()
```

These functions are in `tests/longmemeval/ingest.py:83-96`.

---

## 3. Runner Behavior Analysis

The `LongMemEvalRunner.ingest()` loop (runner.py:1128-1171):

```python
existing_results = build_corpus_results_lookup(checkpoint)
# existing_results = checkpoint["phases"]["ingest"]["results"]
# keyed by corpus_key

for session_index, corpus_session in enumerate(corpus_plan.corpus_sessions):
    if corpus_session.corpus_key in existing_results:
        logger.info("[%s/%s] %s skip (checkpoint)", ...)
        continue  # ← ALL statuses skipped, not just "complete"

    # ... attempt ingestion ...
```

**Implication:** If error corpus_keys remain in the checkpoint, the runner will skip them (treating them as "already done"). To re-process error rows, they must be **absent from the checkpoint's results dict** during recovery.

---

## 4. Recovery Approach

### Option A: Filtered Dataset + Amended Checkpoint (SELECTED)

1. **Extract** 7,298 error corpus_keys from original checkpoint
2. **Generate** a filtered dataset containing ONLY sessions whose corpus_key is in the error set
3. **Amend** original checkpoint: remove error rows, keep all others (11,177 rows)
4. **Ingest** filtered dataset against amended checkpoint — WITHOUT DB reset
5. **Merge** original (complete/extraction_failed) + recovery run (re-processed)
6. **Verify** merged checkpoint: 0 `status="error"`, errored_rate ≤ 5%

**Why this works:**
- Amended checkpoint has NO error corpus_keys → `existing_results` doesn't contain them → runner processes them
- DB reset NOT called → 11,157 successful rows remain in DB and are not disturbed
- Runner processes only the 7,298 sessions from the filtered dataset
- Checkpoint merge combines successful pre-existing rows with newly recovered rows

**Why Option B (reset + re-run full corpus) was rejected:**
- Would wipe 11,157 successful sessions from DB
- Would re-run 18,475 sessions instead of 7,298 — ~2.5× the work
- Would produce a new checkpoint replacing the original (no audit trail)

---

## 5. Artifact Tree

```
tests/benchmark_results/
  wave0_full_corpus_baseline/              # ORIGINAL (preserved, invalid)
    longmemeval_checkpoint.json             # 7298 error rows present
    longmemeval_results.jsonl
    longmemeval_score.json
    ingest.log
    run.log

  wave0_full_corpus_recovery/              # NEW sibling
    longmemeval_checkpoint_amended.json    # error rows REMOVED (used for recovery run)
    longmemeval_filtered_dataset.json      # 7298 sessions only
    longmemeval_results.jsonl
    longmemeval_score.json
    recovery_ingest.log
    wave0_full_corpus_recovery.md

  wave0_full_corpus_baseline_corrected/    # FINAL MERGED OUTPUT
    longmemeval_checkpoint.json            # 0 error rows
    wave0_full_corpus_baseline_corrected.md # verification report
```

**Checkpoint amendment** is a pre-processing step: load original checkpoint, create a copy that excludes `status="error"` rows, save as `amended` checkpoint. This is a pure Python dict operation — no runner involvement.

---

## 6. Helper File: `tests/benchmark_harness/ingestion_rerun_filtered_dataset.py`

**Purpose:** Generate a filtered dataset containing only the 7,298 error sessions, plus the amended checkpoint.

**Scope:** tests-only. No production code. No DB writes.

```python
#!/usr/bin/env python3
"""
IR4 — Generate filtered dataset + amended checkpoint for selective recovery.

Extracts the 7298 error corpus_keys from the wave0_full_corpus_baseline checkpoint,
builds a filtered dataset containing only those sessions, and saves an amended
checkpoint (error rows removed) for use in the recovery run.

Run: PYTHONPATH=. python tests/benchmark_harness/ingestion_rerun_filtered_dataset.py
"""

import json
import sha256
from pathlib import Path

# Paths
ORIGINAL_CHECKPOINT = Path("tests/benchmark_results/wave0_full_corpus_baseline/longmemeval_checkpoint.json")
DATASET = Path("/tmp/longmemeval-review/data/longmemeval_s.json")
OUTPUT_DIR = Path("tests/benchmark_results/wave0_full_corpus_recovery")

# Step 1: Load original checkpoint, extract error corpus_keys
with open(ORIGINAL_CHECKPOINT) as f:
    original = json.load(f)

results = original["phases"]["ingest"]["results"]
error_corpus_keys = {k for k, v in results.items() if v.get("status") == "error"}
complete_corpus_keys = {k for k, v in results.items() if v.get("status") != "error"}

print(f"Error corpus_keys: {len(error_corpus_keys)}")
print(f"Complete/extraction_failed corpus_keys: {len(complete_corpus_keys)}")

# Step 2: Generate amended checkpoint (error rows removed)
amended = json.loads(json.dumps(original))  # deep copy
amended["phases"]["ingest"]["results"] = {
    k: v for k, v in results.items() if k not in error_corpus_keys
}
amended["updated_at"] = datetime.now(UTC).isoformat()

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_DIR / "longmemeval_checkpoint_amended.json", "w") as f:
    json.dump(amended, f, indent=2)
print(f"Amended checkpoint saved → {OUTPUT_DIR / 'longmemeval_checkpoint_amended.json'}")

# Step 3: Build filtered dataset
# Dataset: list of 500 questions, each with haystack_sessions
# For each question, keep it if ANY of its haystack sessions has corpus_key in error set

from tests.longmemeval.ingest import normalize_session_messages
import hashlib

def compute_session_corpus_key(session_messages):
    normalized = normalize_session_messages(session_messages)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def compute_item_session_corpus_keys(item):
    """Compute corpus_key for each haystack session in a dataset item."""
    keys = {}
    for sess in item["haystack_sessions"]:
        ck = compute_session_corpus_key(sess)
        keys[ck] = sess
    return keys

filtered_questions = []
with open(DATASET) as f:
    dataset = json.load(f)

filtered_session_count = 0
for item in dataset:
    item_session_keys = compute_item_session_corpus_keys(item)
    matching_keys = item_session_keys.keys() & error_corpus_keys
    if matching_keys:
        # Keep the full item — runner will filter to matching sessions internally
        filtered_questions.append(item)
        filtered_session_count += len(matching_keys)

filtered_dataset_path = OUTPUT_DIR / "longmemeval_filtered_dataset.json"
with open(filtered_dataset_path, "w") as f:
    json.dump(filtered_questions, f)
print(f"Filtered dataset: {len(filtered_questions)} questions, "
      f"{filtered_session_count} sessions → {filtered_dataset_path}")

print("DONE — amended checkpoint + filtered dataset ready for recovery run")
```

**Note on dataset filtering strategy:** Keeping the full question item (with all its haystack sessions) is correct because the runner's skip logic checks `if corpus_session.corpus_key in existing_results`. The amended checkpoint has the 7,298 error corpus_keys removed from `existing_results`, so the runner will process those sessions even if the question item contains additional non-error sessions (those non-error sessions will be skipped because they're still in `existing_results`).

---

## 7. Recovery Harness: `tests/benchmark_harness/ingestion_rerun_recovery.py`

**Purpose:** Run selective re-ingestion on the filtered dataset against the amended checkpoint, without DB reset.

**Design:** Based on `ingestion_rerun_full_corpus.py` pattern, with key differences:
- Uses `CHECKPOINT = OUTPUT_DIR / "longmemeval_checkpoint_amended.json"` (pre-amended)
- Uses `DATASET = OUTPUT_DIR / "longmemeval_filtered_dataset.json"` (pre-generated)
- **Omits the RESET step** — `cleanup_redis=True` only (no DB wipe)
- After ingest, merges with original checkpoint to produce final corrected checkpoint

```python
# Key difference from ingestion_rerun_full_corpus.py:
# STEP 1: NO RESET — skip reset_canonical_benchmark
# STEP 2: INGEST filtered dataset against amended checkpoint
# STEP 3: Merge original + recovery checkpoints → final corrected
```

**Merge logic:**
```python
def merge_checkpoints(original, recovery, output_path):
    """Merge original (complete/extraction_failed) with recovery (re-processed)."""
    merged = json.loads(json.dumps(original))
    orig_results = original["phases"]["ingest"]["results"]
    rec_results = recovery["phases"]["ingest"]["results"]

    # Keep all original rows
    merged_results = dict(orig_results)
    # Overwrite/insert recovery rows (these replace error rows for the same corpus_key)
    merged_results.update(rec_results)

    # Verify no error rows remain
    error_rows = [k for k, v in merged_results.items() if v.get("status") == "error"]
    if error_rows:
        raise RuntimeError(f"Merge failed: {len(error_rows)} error rows still present")

    merged["phases"]["ingest"]["results"] = merged_results
    merged["updated_at"] = datetime.now(UTC).isoformat()

    with open(output_path, "w") as f:
        json.dump(merged, f, indent=2)
    return merged
```

---

## 8. Verification Criteria

After the full recovery run, the corrected checkpoint must satisfy:

| Criterion | Expected Value |
|---|---|
| `status="error"` rows | **0** |
| Total rows | 18,475 (unchanged) |
| `status="complete"` + `status="extraction_failed"` | 18,475 |
| `errored_rate` (via `_canonical_outcome`) | ≤ 5% |
| `completed` outcome | ≥ 4,499 (original) |
| `empty` outcome | ≥ 6,658 (original) |

**Guards:**
1. `check_errored_floor()` passes (≤5%) → PASS verdict
2. Zero `status="error"` in merged checkpoint
3. Row count consistency: `len(merged["phases"]["ingest"]["results"]) == 18475`

---

## 9. Commands for Execution

```bash
# Step 1: Generate filtered dataset + amended checkpoint
PYTHONPATH=. python tests/benchmark_harness/ingestion_rerun_filtered_dataset.py

# Step 2: Run selective recovery ingest (NO RESET)
PYTHONPATH=. python tests/benchmark_harness/ingestion_rerun_recovery.py

# Step 3: Verify corrected checkpoint
PYTHONPATH=. python -c "
from tests.benchmark_harness.guardrails import check_errored_floor, _canonical_outcome
import json
cp = json.load(open('tests/benchmark_results/wave0_full_corpus_baseline_corrected/longmemeval_checkpoint.json'))
results = cp['phases']['ingest']['results']
error_rows = [k for k,v in results.items() if v.get('status') == 'error']
print(f'Error rows remaining: {len(error_rows)}')
print(f'Total rows: {len(results)}')
r = check_errored_floor(cp)
print(f'Errored rate: {r[\"errored_rate\"]:.1f}%')
print(f'PASS' if r['passed'] else 'FAIL')
"
```

---

## 10. Blocking Uncertainties

| # | Question | Impact | Resolution |
|---|---|---|---|
| 1 | Is the dataset at `/tmp/longmemeval-review/data/longmemeval_s.json` identical to what was used for the original run? | HIGH — if dataset changed, corpus_key computation may not match | Verify via file fingerprint before running recovery |
| 2 | Did the DB contain any writes from the error sessions that were partially completed? | MEDIUM — if a session wrote to DB before failing, it may conflict with re-ingestion | The error rows have no `conversation_id` or `message_count`, indicating no DB write occurred; safe to re-process |
| 3 | Are there Redis extraction keys from the original run that could interfere? | LOW — `cleanup_redis=True` in the recovery harness will clean them before ingest |

**Uncertainty 1 resolution:** Confirm dataset identity by comparing file fingerprint. The corpus_keys computed from the current dataset match the 7,298 error corpus_keys in the checkpoint (100% overlap verified 2026-04-28). This strongly suggests the dataset is unchanged.

---

## 11. Relation to Other Artifacts

| Artifact | Status | Relation |
|---|---|---|
| IR1 (`wave0_db_outage_diagnosis.md`) | Complete | Established 7,298 errors = DB outage, not verdict bug |
| IR2 (`wave0_infrastructure_recovery.md`) | Complete | Described 1A–1D recovery options; this doc is IR4 |
| IR3 (`wave0_harness_monitoring_fix.md`) | Complete | Fixed `summarize()` + `check_errored_floor()` verdict bug |
| `baselines.md` | Blocked | Requires corrected checkpoint |
| `wave0_oracle_checkpoint_2.md` | Blocked | Requires valid baseline |
| Local tag | Blocked | Requires valid baseline |

IR4 is the **final unblocked step** before baseline validity can be restored.

---

*Document: `tests/benchmark_results/wave0_selective_recovery_design.md`*
*IR4 — Wave 0 — Selective Recovery Design*
