# Wave 0 Memory Store Stats

**Date:** 2026-04-29
**Scope:** DB-B — memory store population and tier/category distribution
**Benchmark user:** `12345678-1234-5678-1234-567812345678`

---

## Evidence

### Direct DB Counts

| Metric | Value |
|--------|-------|
| `db_total_memories` | 27,599 |
| `db_active_memories` | 27,599 |
| `status_counts` | `{'active': 27599}` |
| `tier_counts` | `{'l1': 27599}` |
| `source_type_counts` | `{'extracted': 27599}` |

### Category Distribution

| Category | Count |
|----------|-------|
| `fact` | 12,142 |
| `project` | 11,343 |
| `preference` | 4,025 |
| `correction` | 89 |

---

## Interpretation

The store is heavily populated at **27,599 memories**, all active, all L1 extracted. The category distribution is broad: fact and project memories dominate (~85% combined), with a smaller preference tier and a negligible correction count.

The rough expectation cited in prior analysis was in the range of ~20k–50k memories. **27,599 falls within this range and is not sparse.** The store is consistent with a fully-ingested long session corpus.

---

## Verdict

**DB-B ruling: store is plausible / not sparse.**

27,599 active L1 extracted memories is within expected bounds. The store is not undersized relative to prior benchmarks. The benchmark user has a richly populated memory store, and there is no evidence of ingestion failure or truncation.
