# Wave 0 — Infrastructure Recovery (IR2)

**File:** `tests/benchmark_results/wave0_infrastructure_recovery.md`
**Checkpoint:** `tests/benchmark_results/wave0_full_corpus_baseline/longmemeval_checkpoint.json`
**Run log:** `tests/benchmark_results/wave0_full_corpus_baseline/run.log`
**IR1:** `tests/benchmark_results/wave0_db_outage_diagnosis.md`
**Generated:** 2026-04-28

**Status:** Documentation-only. No production code changes. No `orchestrator/memory/` edits.

---

## Summary

IR1 established that 7,298 `status="error"` rows in the full-corpus checkpoint represent a **contiguous PostgreSQL service disruption** (ECONNREFUSED on port 5432) during the run. The current harness PASS is invalid because it ignores those rows. This document (IR2) captures: (a) tests-only / host-runtime recovery options, (b) what evidence remains missing, and (c) preconditions for any clean retry. It does **not** patch production code or create `baselines.md` or `wave0_oracle_checkpoint_2.md`.

---

## 1. Immediate Recovery Options

All options below are **tests-only** or **host/runtime operational steps**. No production code changes are included.

### 1A. Tests-Only Harness Verdic Fix (Low-Risk, Local Only)

**Purpose:** Count `status="error"` rows so future runs produce a valid PASS/fail.

| Step | Action | File |
|------|--------|------|
| 1 | Modify `check_errored_floor` in `tests/benchmark_harness/guardrails.py` to count rows where `status == "error"` alongside `outcome == "errored"` | `tests/benchmark_harness/guardrails.py` |
| 2 | Re-evaluate the current checkpoint with the fixed guardrail — expect FAIL at ~39.6% error rate | `tests/benchmark_results/wave0_full_corpus_baseline/longmemeval_checkpoint.json` |
| 3 | If a clean re-run succeeds, the re-evaluated checkpoint replaces the current invalid one | — |

**This does not repair the 7,298 sessions.** It only fixes the monitoring layer for future runs. The 7,298 sessions remain unprocessed.

### 1B. Selective Re-Ingestion of Failed Sessions (Tests-Only)

**Purpose:** Re-process only the 7,298 `status="error"` sessions.

| Step | Action |
|------|--------|
| 1 | Extract `session_id` list from `status="error"` rows in the checkpoint |
| 2 | Filter the dataset to those session IDs |
| 3 | Run `ingestion_rerun_full_corpus.py` (or a filtered variant) on that subset |
| 4 | Merge results back into checkpoint, overwriting `status="error"` entries |

**Constraint:** This requires the dataset to still contain those sessions. If the dataset has been regenerated or the source `longmemeval_s.json` modified, the sessions may not be re-extractable.

### 1C. Host/Runtime: Docker Restart Policy Audit

**If PostgreSQL runs in Docker:**

| Step | Action | Command |
|------|--------|---------|
| 1 | Check current restart policy | `docker inspect <postgres_container> --format '{{.HostConfig.RestartPolicy.Name}}'` |
| 2 | If `unless-stopped` or `always`: document that a host reboot or Docker daemon restart would restart PostgreSQL mid-run | — |
| 3 | If `no` or `on-failure`: PostgreSQL would NOT restart automatically — consider `unless-stopped` for future runs (but see §3) | — |

**`unless-stopped` semantics:** Docker will NOT restart a container that was explicitly stopped (e.g., `docker stop`) before the daemon restart. It WILL restart containers that crashed. This distinction is not visible in container metadata after the fact — only `docker events` at the time can confirm.

**To make restart policy changes durable across daemon restarts:**

```bash
# View current policy
docker inspect <container> --format '{{json .HostConfig.RestartPolicy}}'

# Update policy on a running container (requires Docker API 1.22+)
docker update --restart unless-stopped <container_id>
```

### 1D. Host/Runtime: Unattended-Upgrades Disabling (Ubuntu Hosts)

**Purpose:** Prevent automatic service restarts during benchmark runs.

```bash
# Check if unattended-upgrades is installed
dpkg -l | grep unattended-upgrades

# View recent unattended-upgrades activity
ls -la /var/log/unattended-upgrades/
cat /var/log/unattended-upgrades/unattended-upgrades-dpkg.log

# Temporarily disable for a session
sudo systemctl stop unattended-upgrades
sudo systemctl mask unattended-upgrades  # prevents start on next boot

# Re-enable after session
sudo systemctl unmask unattended-upgrades
sudo systemctl start unattended-upgrades
```

**Why this matters:** Ubuntu's `unattended-upgrades` can restart services (including PostgreSQL) after security package updates without operator consent. A mid-run restart would produce exactly the observed pattern.

---

## 2. What Evidence Is Still Missing

The following cannot be determined from current local artifacts alone:

| Question | Missing Evidence | How to Obtain |
|----------|-----------------|---------------|
| Was it Docker or bare PostgreSQL? | Docker event logs from the run window | `docker events --since <run_start> --until <run_end> --filter type=container` on the host |
| What triggered the restart? | systemd journal / pg logs / kernel ring buffer | `journalctl -u postgresql --since "<run_start>" --until "<run_end>"` |
| Exact start/end time of outage | PostgreSQL connection log or pg audit log | Enable `log_connections = on` in PostgreSQL config; check `pg_log/` |
| Duration of outage | Same as above | Same as above |
| Did the harness script resume after DB came back? | Run log timestamps with DB reconnect events | Current run.log is too sparse and buffered to confirm |
| Is this reproducible (single event vs. systemic)? | Multiple run histories | Run future full-corpus baselines and monitor for recurrence |
| Was it pre- or post- ABL-1/ABL-2 (April 26–27)? | Timestamps in checkpoint header | Checkpoint spans `2026-04-26T20:56:42` to `2026-04-27T14:32:12` — overlap confirmed, ordering unclear |

---

## 3. Recommended Preconditions Before Any Clean Retry

The following must be satisfied before initiating a new full-corpus ingestion run:

### 3.1. Host-Side Preconditions

- [ ] **PostgreSQL health confirmed:** `pg_isready -h localhost -p 5432` returns OK immediately before run
- [ ] **No pending security updates:** `unattended-upgrades` disabled or confirmed idle (`apt-get update && apt-get -y upgrade --dry-run` shows no pending`)
- [ ] **Docker restart policy reviewed** for PostgreSQL container (if applicable)
- [ ] **Disk space verified:** `df -h` on PostgreSQL data volume shows >2× expected dataset size
- [ ] **Memory available:** `free -m` confirms no swap pressure during the run window
- [ ] **No other intensive jobs** scheduled during the run window (cron, backup jobs, etc.)

### 3.2. Harness Preconditions

- [ ] **G1 provider health check passes** before ingestion begins
- [ ] **`check_errored_floor` updated** to count `status="error"` rows (see §1A)
- [ ] **Checkpoint schema validated:** confirm all rows have either `outcome` or `status` field populated — no silent drops
- [ ] **Run log ring buffer flushed:** ensure logging is not dropping ERROR-level events to stdout (check handler configuration)

### 3.3. Monitoring Preconditions

- [ ] **DB connection monitoring active:** a separate lightweight monitor (e.g., `pg_isready` loop, `docker events` tail) should run alongside the harness to capture restart timing
- [ ] **Run log has timestamps enabled:** confirm the harness logs timestamps at ERROR level, not only at INFO
- [ ] **Slack / ntfy alerting configured:** for long runs, set up a connection-loss alert to notify before the full run completes with a compromised checkpoint

---

## 4. asyncpg and LiteLLM Retry Guidance

### 4.1. asyncpg Connection Loss Categories

| Error | Meaning | Default Behavior | Recommended Response |
|-------|---------|-----------------|----------------------|
| `Errno 111` (ECONNREFUSED) | PostgreSQL not listening | Raises immediately | Retry after reconnect — no backoff on first attempt |
| `Errno 104` (ECONNRESET) | Connection reset by peer | Raises immediately | Retry with exponential backoff |
| `Errno 110` (ETIMEDOUT) | Connection timed out | Raises after 30s default | Retry with backoff; check pool size |
| `psycopg2.OperationalError` | Connection dead | Raises immediately | Retry; check pg_bouncer if used |

**Application-layer backoff recommendation:**

```python
import asyncio

async def fetch_with_backoff(coro, max_retries=5, base_delay=1.0):
    for attempt in range(max_retries):
        try:
            return await coro
        except (ConnectionRefusedError, OSError) as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            await asyncio.sleep(delay)
```

**Note:** This guidance applies to production retry logic if implemented. This IR2 document does not propose adding retry logic to production code.

### 4.2. LiteLLM Retry / Fallback Behavior

| Setting | Effect | Default |
|---------|--------|---------|
| `litellm.num_retries` | Number of retries per request | `3` (if enabled) |
| `litellm.retry_after` | Seconds to wait before retry | `2.0` |
| `litellm.request_timeout` | Timeout per request | `60s` |

**For extraction endpoint calls in the harness:**
- LiteLLM retries do NOT cover ECONNREFUSED (connection refused) — that is a transport-layer error, not an HTTP response
- LiteLLM does retry on 429 (rate limit), 500, 502, 503, 504
- If LiteLLM retries are enabled, ensure the extraction function is idempotent (safe to re-call)

**Fallback routing:**
- If `openai` extraction fails after retries, the harness can fall back to `openrouter/openai/gpt-4o-mini` or another provider
- This is already handled by the provider routing in `extract_facts_from_text` — no new code needed

---

## 5. Blocked Artifacts

The following remain **blocked** until result validity is repaired:

| Artifact | Blocker |
|----------|---------|
| `baselines.md` | Current full-corpus checkpoint is invalid — 7,298 sessions missing from verdict computation |
| `wave0_oracle_checkpoint_2.md` | Oracle cannot evaluate a baseline with ~40% uncounted errors |
| Local tag | Tag quality would be degraded by missing sessions |

These artifacts may proceed only after:
1. The 7,298 sessions are re-processed, OR
2. The current checkpoint is explicitly marked as invalid and excluded from baseline comparison, OR
3. A new clean full-corpus run completes successfully

---

## 6. Open Questions for Further Investigation

| # | Question | Priority |
|---|----------|----------|
| 1 | Does the host machine have `unattended-upgrades` configured? | High |
| 2 | Is PostgreSQL running bare or in a Docker container? | High |
| 3 | What is the current Docker restart policy for the PostgreSQL container? | High |
| 4 | Are there `docker events` logs from the host that cover the run window? | Medium |
| 5 | Are there `journalctl` entries for PostgreSQL from the run window? | Medium |
| 6 | Does `pg_log/` contain any entries from the run window? | Medium |
| 7 | Has this pattern occurred in prior full-corpus runs? | Medium |
| 8 | Is there a pg_bouncer or connection pooler in front of PostgreSQL that could mask restarts? | Low |

---

*Document: IR2 — Infrastructure Recovery*
*Parent: IR1 (`wave0_db_outage_diagnosis.md`)*
*Next: Execute recovery options (§1) → re-evaluate with fixed guardrail → decide on clean re-run*
