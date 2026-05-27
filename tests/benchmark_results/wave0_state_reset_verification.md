# Wave 0 State Reset Verification — R5

**Date:** 2026-04-25
**Task:** R5 — Empirically verify clean state (double reset) and document findings
**Source:** `tests/benchmark_results/_r4_verify/verification_result.json`
**Helper:** `tests/benchmark_harness/reset_verify_helper.py` (`double_reset_for_confirmation`)
**Verification script:** `tests/benchmark_harness/verify_reset_completeness.py`

---

## 1. Command Run

```bash
python -m tests.benchmark_harness.verify_reset_completeness
```

This invokes `double_reset_for_confirmation(pool, checkpoint_path)` from `reset_verify_helper.py`, which:

1. Calls `full_reset_with_verification(..., cleanup_redis=True)` — production reset + extended table cleanup + Redis cleanup + zero-row verification
2. Sleeps 0.5 s to allow async tasks to settle
3. Calls `full_reset_with_verification(..., cleanup_redis=True)` a second time
4. Returns `confirmed_clean = second_result.all_zero`

---

## 2. Tables Covered

The extended reset covers **9 tables total**:

### 7 production-reset tables (from `cleanup_canonical_benchmark()`)
| Table | Notes |
|---|---|
| `conversations` | Core benchmark table |
| `messages` | Core benchmark table |
| `memories` | Core benchmark table |
| `memory_extraction_log` | Core benchmark table |
| `retrieval_log` | Core benchmark table; async writes land post-reset |
| `dream_log` | Core benchmark table |
| `entities` | Core benchmark table |

### 2 extended tables (from R4 fix)
| Table | Notes |
|---|---|
| `skill_consolidation_log` | Missing from production reset (R4 finding) |
| `skill_nudge_user_state` | Missing from production reset (R4 finding) |

The helper constant `ALL_RESET_TABLES` in `reset_verify_helper.py:46–56` enumerates all 9.

---

## 3. Pre-Reset Counts

All 9 tables had **zero rows** for `TEST_USER_ID` (`12345678-1234-5678-1234-567812345678`) **before** either reset pass was invoked:

```
conversations:              0
messages:                   0
memories:                   0
memory_extraction_log:      0
retrieval_log:              0
dream_log:                  0
entities:                    0
skill_consolidation_log:    0
skill_nudge_user_state:      0
```

---

## 4. Both Reset Passes

### First Reset Pass

| Field | Value |
|---|---|
| `success` | `true` |
| `tables_cleared` (7 core) | All 7 tables cleared; deletions ranged 0 rows each |
| `extended_tables_cleared` | `skill_consolidation_log: 0`, `skill_nudge_user_state: 0` |
| `total_rows_deleted` | `0` |
| `row_counts_after_reset` | All 9 tables at `0` |
| `all_zero` | **`true`** |
| `error` | `null` |

### Second Reset Pass

| Field | Value |
|---|---|
| `success` | `true` |
| `tables_cleared` (7 core) | All 7 tables cleared; 0 rows each |
| `extended_tables_cleared` | `skill_consolidation_log: 0`, `skill_nudge_user_state: 0` |
| `total_rows_deleted` | `0` |
| `row_counts_after_reset` | All 9 tables at `0` |
| `all_zero` | **`true`** |
| `error` | `null` |

### `confirmed_clean: true`

---

## 5. Redis Cleanup

Redis cleanup was **enabled** in this invocation (`cleanup_redis=True` passed to both `full_reset_with_verification` calls). The helper's `cleanup_runner_redis()` scans and deletes keys matching:

- `extract:*`
- `arq:job:extract:*`
- `arq:result:extract:*`
- `arq:retry:extract:*`

Redis key deletion counts are stored in `ExtendedResetResult.redis_keys_deleted`; the `verification_result.json` does not include this field, indicating it was 0 or not captured in the JSON serialization.

---

## 6. Empirical Result

**`confirmed_clean: true`** — both passes returned `all_zero: true`.

This proves:
- The zero-row verification path (`verify_zero_row_state`) is functional and correctly iterates all 9 tables.
- The extended table cleanup (`extended_cleanup_tables`) is present and operational.
- Redis cleanup is re-enabled in the helper path (the triple-run harness had disabled it).
- Two consecutive resets both reach and confirm zero-row state when starting from a clean DB.

---

## 7. Documented Limitation

**This invocation did NOT exercise a dirty-to-clean transition.**

The database was already clean before the first reset was invoked — all 9 tables showed 0 rows in `counts_before`. Therefore, while this run confirms:

1. The helper's **zero-row verification logic** is correct (correct tables, correct query pattern).
2. The **extended reset covers all 9 tables** (7 + skill_*).
3. **Redis cleanup is re-enabled** in the helper path.
4. The **double-reset confirmation pattern** works (`confirmed_clean` propagates from `all_zero`).

It does **not** empirically demonstrate that the helper can clean a **dirty** database in a single invocation. That proof would require a pre-reset state with non-zero counts in at least one table (e.g., `skill_consolidation_log` accumulating rows from a prior benchmark run).

The theoretical correctness of dirty-state cleanup is established by the helper's SQL (DELETE FROM each table WHERE user_id = $1), not by this particular invocation's preconditions.

---

## 8. Relationship to R4

R4 added `skill_consolidation_log` and `skill_nudge_user_state` to the reset scope and re-enabled Redis cleanup in the harness path. R5 confirms those additions are wired correctly and that the verification helper's double-reset pattern produces a clean `confirmed_clean` result.

R6 (preserved rerun artifacts) will use this helper to establish clean-state checkpoints before each preserved run, providing recurring empirical evidence that the DB is clean before re-ingestion.

---

## 9. Source Artifacts

| Artifact | Path |
|---|---|
| Verification result (JSON) | `tests/benchmark_results/_r4_verify/verification_result.json` |
| Reset helper | `tests/benchmark_harness/reset_verify_helper.py` |
| Verification script | `tests/benchmark_harness/verify_reset_completeness.py` |
| Audit (R1) | `tests/benchmark_results/wave0_state_reset_audit_v2.md` |
