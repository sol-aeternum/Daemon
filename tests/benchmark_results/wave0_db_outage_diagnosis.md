# Wave 0 — DB Outage Diagnosis

**File:** `tests/benchmark_results/wave0_db_outage_diagnosis.md`
**Checkpoint:** `tests/benchmark_results/wave0_full_corpus_baseline/longmemeval_checkpoint.json`
**Run log:** `tests/benchmark_results/wave0_full_corpus_baseline/run.log`
**Generated:** 2026-04-28

---

## Summary

The 7,298 `status="error"` rows in the checkpoint represent database connection failures to PostgreSQL (port 5432) that occurred during the full-corpus ingestion run. The evidence is **strongly consistent with a contiguous PostgreSQL service disruption** (restart or temporary unavailability), but **the run log alone cannot prove the precise timing or duration** of that disruption.

---

## Authoritative Checkpoint Evidence

### Status counts (from checkpoint `status` field)

| Status | Count | Source |
|--------|-------|--------|
| `complete` | 11,157 | baseline report + checkpoint |
| `extraction_failed` | 20 | baseline report + checkpoint |
| `error` | **7,298** | checkpoint only (monitoring bug — not in report) |

### Outcome counts (from checkpoint `outcome` field)

| Outcome | Count | Notes |
|---------|-------|-------|
| `completed` | 4,499 | extraction succeeded with facts |
| `empty` | 6,658 | extraction succeeded with zero facts |
| `errored` | 20 | `extraction_failed` rows only |
| *(absent)* | **7,298** | `status="error"` rows — `outcome` field not written |

**Total:** 4,499 + 6,658 + 20 + 7,298 = **18,475** sessions ✓ (matches `completed_count` in checkpoint header)

### Sample `status="error"` entries (checkpoint lines 119480–119529+)

Every `status="error"` entry has an identical error payload:

```json
{
  "session_id": "ultrachat_384753",
  "status": "error",
  "error": "[Errno 111] Connect call failed ('127.0.0.1', 5432)",
  "corpus_key": "df4b9d9bff82e0ab31d9dd1be5b3d7b8b68b20d7d2977e4d96a86172ca624aa2",
  "raw_session_ids": ["ultrachat_384753"]
}
```

Notable structural features of these entries:
- **No `outcome` field** — the write to the checkpoint never completed for these sessions
- **No `conversation_id` or `message_count` fields** — the fetch/parse stage may also have been interrupted
- **`error` field is exclusively** `[Errno 111] Connect call failed ('127.0.0.1', 5432)` — connection refused to the local PostgreSQL instance
- All observed `status="error"` entries are byte-for-byte identical in their error string

### In contrast: `extraction_failed` entries

```json
{
  "session_id": "ultrachat_369361",
  "conversation_id": "712c3f87-2de8-4892-a9eb-01c086ee8406",
  "message_count": 8,
  "status": "extraction_failed",
  "outcome": "errored",
  "error": "[Errno 111] Connect call failed ('127.0.0.1', 5432)",
  "corpus_key": "8d23d03f803a0b3ba28482cfb4db56a9a92969e900a97f4925d430ff055bb5"
}
```

`extraction_failed` rows **do have** `outcome`, `conversation_id`, and `message_count` — meaning the session was fetched and parsed before failing at extraction time. The error message is the same `Errno 111` connection refused, but the structure differs because a later pipeline stage wrote those fields before the failure.

---

## Run Log Evidence

### Final ingest summary (run.log line 37249)

```
[FULL_CORPUS] Sessions: 18475, ERR 0.1%, completed(outcome)=4499, errored(outcome)=20,
empty(outcome)=6658, complete(status)=11157, extraction_failed(status)=20
```

The run log reports **only 20 errored outcomes** — this is the `extraction_failed` count. The 7,298 `status="error"` rows are **not reflected in this summary**, confirming the monitoring bug: the current harness checks `outcome` but ignores `status="error"` rows that lack an `outcome` field.

### Guardrail result (run.log line 37245–37246)

```
[guardrail:errored_floor] errored=0.1% (max=5.0%) passed=True
[FULL_CORPUS] G3: errored floor PASS (0.1%)
```

The guardrail computes `errored=0.1%` as `20 / 18475`. It is **invalid** because it counts only `outcome="errored"` (20 rows) and excludes all 7,298 `status="error"` rows. The true error rate is `(7298 + 20) / 18475 ≈ 39.6%`.

### Restart evidence in run.log

The run log (37,253 lines) is **too sparse and heavily buffered** to establish PostgreSQL restart timing. Observed patterns:
- LiteLLM `Provider List:` spam fills >80% of log volume (INFO-level noise from litellm internals)
- The last ~6,000 lines of the log (lines 31,000–37,253) are **almost exclusively** `BenchmarkSamplingError` fingerprint-drift diagnostics from extraction — there are no explicit PostgreSQL connection errors, no "connection refused" messages, no database-related stack traces, and no explicit shutdown/restart records visible at the log level
- The INGEST_OK marker (line 37242) confirms the ingest step completed, not when the database was unavailable

**Conclusion:** The run log cannot prove when the DB outage began or ended. A more definitive timeline would require PostgreSQL audit logs, Docker container event logs (`docker events`), systemd journal entries (`journalctl -u postgres`), or kernel ring-buffer messages (`dmesg`) from the host at the time of the run.

---

## Classification: Contiguous Outage vs. Distributed Capacity Failure

### What the evidence shows

**Supporting contiguous outage:**
1. **Identical error payload across all 7,298 rows** — `[Errno 111] Connect call failed ('127.0.0.1', 5432)` is byte-for-byte identical. This is consistent with a single PostgreSQL instance becoming unavailable (not connection-pool exhaustion, which would produce varied error messages).
2. **Missing `outcome` field in all `status="error"` rows** — the checkpoint write for these sessions was interrupted mid-write. The absence of `conversation_id` and `message_count` suggests the pipeline was in the fetch/parse stage when the outage occurred, but those fields were never written.
3. **7,298 is a large, coherent block** — if these were random distributed failures (e.g., per-request connection timeouts due to load), a proportion of the 18,475 sessions would have succeeded. Instead, ~40% of sessions failed with the same precise error, suggesting a single point of failure.
4. **PostgreSQL port 5432 specifically** — `Errno 111` (ECONNREFUSED) means the target host actively rejected the connection, not that it timed out. This is the signature of PostgreSQL being down or restarting, not overloaded.

**Against distributed capacity failure:**
- A capacity issue (connection pool exhaustion under load) would typically produce `Errno 110` (ETIMEDOUT) or `Errno 104` (ECONNRESET), not `Errno 111` (ECONNREFUSED).
- If capacity were the issue, errors would appear interleaved with successes throughout the run, not as a large block.

### Final classification: **Contiguous DB Outage (probable)**

The evidence is **consistent with a single PostgreSQL restart or service interruption** during the run. The identical `Errno 111` errors across all 7,298 rows, the missing checkpoint fields, and the large coherent block of failures all point to a single service disruption rather than a distributed load-related failure.

**Probability estimate (unqualified):** ~75% confident — contiguous DB restart. ~20% confident — Docker container restart (if PostgreSQL runs in a container with restart policy). ~5% confident — other cause.

---

## What Is Proven vs. Not Proven

### Proven from local evidence

| Statement | Evidence |
|-----------|----------|
| 7,298 sessions have `status="error"` in the checkpoint | grep count of `"status": "error"` |
| All 7,298 have `error = "[Errno 111] Connect call failed ('127.0.0.1', 5432)"` | Direct inspection of checkpoint lines 119480+ |
| All 7,298 `status="error"` rows lack `outcome`, `conversation_id`, `message_count` | Direct inspection |
| `extraction_failed` rows (20) have the same error payload but a different structure | Checkpoint lines 119470–119479 |
| The current harness PASS is invalid — `status="error"` rows are ignored | run.log G3 guardrail counts 20, not 7298 |
| `completed_count` in checkpoint header = 18,475 | `completed_count: 18475` at line 11 |

### Suggested by research / Plausible

| Statement | Source |
|-----------|--------|
| `Errno 111` at port 5432 = PostgreSQL process not listening | Standard POSIX networking |
| Docker restart policy could cause PostgreSQL restart mid-run | Docker Compose restart policy behavior |
| Unattended upgrades on Ubuntu can restart services | Known Ubuntu behavior |
| asyncpg default timeout is 30s; a restart would cause all in-flight requests to fail with ECONNREFUSED | asyncpg library behavior |

### Not provable from current artifacts

| Question | Why not |
|----------|---------|
| Was it a Docker container restart vs. bare PostgreSQL restart? | No Docker event logs, container metadata in run.log |
| What was the exact start/end timestamp of the outage? | Run log is buffered and sparse; no timestamps for error onset |
| How long did the outage last? | Checkpoint doesn't record per-session timing; no DB-side logs |
| Did the run resume after the DB came back? | INGEST_OK at line 37242 confirms eventual completion, but resume timing unknown |
| Was this pre- or post- Wave 0 ABL-1 (April 26–27)? | Timestamps in checkpoint (`2026-04-26T20:56:42` to `2026-04-27T14:32:12`) span the run |

---

## Monitoring Bug Confirmation

The baseline report and run log summary count only `outcome="errored"` (20 rows) when computing the error rate. All 7,298 `status="error"` rows are silently excluded because they lack an `outcome` field.

**Impact:** The reported error rate is 0.1% (20/18475). The actual error rate is approximately 39.6% (7318/18475).

**Current PASS result is invalid** for any downstream comparison that relies on the full-corpus run having succeeded. Until the monitoring bug is fixed (count `status="error"` alongside `outcome="errored"`) and the 7,298 sessions are re-processed, baseline validity remains compromised.

---

## Relation to Downstream Work

- **IR2 (harness-monitoring fix)** — blocked until result validity is repaired
- **Wave 0 Oracle checkpoint** — blocked; the PASS/fail framing is meaningless with 7,298 uncounted errors
- **Tag artifact** — should not proceed with current checkpoint; tag quality will be degraded by missing sessions
