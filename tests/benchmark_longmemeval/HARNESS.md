# LongMemEval Benchmark Harness — Reproducibility Contract

**Date:** 2026-04-18
**Scope:** `tests/benchmark_longmemeval/` — artifact directory only. Live benchmark source lives in
`orchestrator/eval/longmemeval.py`, `orchestrator/eval/runner.py`, `orchestrator/eval/longmemeval_fast.py`,
`tests/longmemeval/ingest.py`, and `tests/longmemeval/evaluate.py`.

---

## 1. Canonical Lane vs Fast Lane

### Canonical lane (ONLY valid lane for extraction / dedup / entity-linking claims)

```text
DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon \
PYTHONPATH=. python -m orchestrator.eval.longmemeval run \
  --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json \
  --output-dir tests/benchmark_results/dev_subset_baseline/runN
```

Entrypoint: `orchestrator/eval/longmemeval.py` → `LongMemEvalRunner` in `orchestrator/eval/runner.py` →
`tests/longmemeval/ingest.py`. Uses the shared fixed benchmark user
`longmemeval@daemon.test` / `12345678-1234-5678-1234-567812345678`. Ingestion runs full
`process_extraction()` → dedup → store pipeline per corpus session. Retrieval is scoped to the
question's checkpoint-derived `allowed_source_conversation_ids`.

**The canonical lane is the ONLY valid path for any claim about extraction fidelity, dedup behavior,
or entity-linking correctness.** The fast lane bypasses all of those stages.

### Fast lane (retrieval / answer / judge / chunking studies ONLY)

```text
PYTHONPATH=. python -m orchestrator.eval.longmemeval_fast run \
  --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json \
  --output-dir tests/benchmark_results/dev_subset_fast/runN
```

Entrypoint: `orchestrator/eval/longmemeval_fast.py`. Minted fresh per-run UUID user.
Direct-inserts chunk memories via `insert_chunk_memories()` — never calls `process_extraction()`,
`extract_memories()`, or `poll_extraction_complete()`. Per-question cleanup destroys evidence after
every question.

**The fast lane MUST NOT be used to make extraction, dedup, or entity-linking claims.** It bypasses
the canonical extraction/dedup pipeline entirely. See `ISOLATION_AUDIT.md` §Fast lane audit and
`BARRIER_AUDIT.md` §Fast harness for details.

---

## 2. Locked Fixture Authority

| Fixture | Authority | Location |
|---|---|---|
| Dev subset dataset | 50-case locked fixture, exact `question_id` order preserved | `tests/benchmark_longmemeval/fixtures/dev_subset.json` |
| Dev subset coverage map | Cell floors, selection rules, corpus-plan snapshot | `tests/benchmark_longmemeval/fixtures/dev_subset_coverage.md` |
| Canonical result/checkpoint/score filenames | `longmemeval_results.jsonl`, `longmemeval_checkpoint.json`, `longmemeval_score.json`; checkpoint version `2` | `orchestrator/eval/runner.py:83-88` |
| Benchmark user identity | Shared fixed user `longmemeval@daemon.test` / `12345678-1234-5678-1234-567812345678` | `tests/longmemeval/ingest.py:37-39` |
| Subset cell coverage | 9 IE-user, 9 IE-assistant, 10 MR, 10 TR, 9 KU, 5 abstention (overlap); 2,079 unique normalized corpus sessions | `dev_subset_coverage.md` |

---

## 3. Pinned Config Authority

Full inventory in `CONFIG_PINNING.md`. Key pinned constants for canonical reproducibility:

| Surface | Authority | Value / Source |
|---|---|---|
| Extraction model | Hardcoded constant | `openrouter/openai/gpt-4o-mini` (`EXTRACTION_MODEL`) |
| Extraction prompt | Hardcoded template SHA256 | `orchestrator/memory/extraction.py:140-309` |
| Extraction sampling | Hardcoded constants | `MAX_EXTRACTION_INPUT_CHARS`, `EXTRACTION_TEMPERATURE`, `EXTRACTION_TOP_P`, `EXTRACTION_MAX_TOKENS` |
| Confidence calibration | Hardcoded constants | `DEFAULT_EXTRACTED_CONFIDENCE`, `HEDGE_OVERRIDE_CONFIDENCE`, `STRONG_OVERRIDE_CONFIDENCE`, `CORRECTION_MIN_CONFIDENCE` |
| Answer model | Hardcoded constant | `ANSWER_MODEL` from `tests/longmemeval/evaluate.py:90-92` |
| Judge model | Hardcoded constant | `JUDGE_MODEL` from `tests/longmemeval/evaluate.py:94-96` |
| Retrieval call contract | Hardcoded flags | `TOP_K_MEMORIES`, `include_l0=True`, `include_dream_observations=True`, `retrieval_triggered_by="longmemeval"` |
| Dedup thresholds | Live config (env-overridable) | `dedup_merge_threshold`, `dedup_supersede_threshold`, `dedup_supersede_same_slot_threshold` via `get_settings()` |
| Query embedding model | Live config | `embedding_query_model` via `Settings` |
| Document embedding model | Live config | `embedding_document_model` via `Settings` |

The runner emits `benchmark_effective_config` and `benchmark_config_drift_warnings` into every
checkpoint. See `BENCHMARK_CONFIG_PIN_PATH = tests/benchmark_longmemeval/longmemeval_config_pin.json`.
Pinned config SHA256s are committed in that file; effective config is snapshotted at runtime.

---

## 4. Prerequisites

All commands below assume the daemon stack is running under **Docker Compose** and database
credentials are available in the host shell environment. Benchmark runs execute against the
localhost-mapped PostgreSQL endpoint, not the Docker-internal host names.

### Docker Compose stack

```bash
# Verify stack is up
docker compose ps

# Backend and worker must be running; postgres and redis must be healthy
docker compose ps | grep -E "Up|healthy"
```

The benchmark shell commands use the host-mapped endpoint
`postgresql://daemon:daemon@127.0.0.1:5432/daemon` — resolved by the host Docker network,
not from inside containers. See `QUICKSTART.md` for full stack bring-up.

### Environment check

The benchmark harness executes entirely from the **host shell** (not inside containers).
All provider keys must be present in the host environment before running.

```bash
# Required provider keys for extraction, answer, and judge steps
# Benchmark will fail at extraction/answer/judge phases without these set in the host shell
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY must be set in host shell}"
: "${VOYAGE_API_KEY:?VOYAGE_API_KEY must be set in host shell}"

# Verify database connectivity (host-mapped localhost) — asyncpg is used by the harness itself
# The psql example below is illustrative only; psql is NOT required on the host.
# If psql is unavailable, the Python-based connectivity check below is sufficient.
# psql "postgresql://daemon:daemon@127.0.0.1:5432/daemon" -c "SELECT 1"

# Python-based connectivity check (does not require psql on the host):
PYTHONPATH=. python -c "import asyncio, asyncpg; asyncio.run(asyncpg.connect('postgresql://daemon:daemon@127.0.0.1:5432/daemon'))" && echo "DB connectivity OK"

# Verify Python path and CLI discovery
PYTHONPATH=. python -m orchestrator.eval.longmemeval --help
```

### Dataset bootstrap

The dev subset fixture is committed at `tests/benchmark_longmemeval/fixtures/dev_subset.json` and
does not require network access. For the full corpus (later work):

```bash
# Full corpus is cached at /tmp/longmemeval-review/data/longmemeval_s.json
# by tests/longmemeval/ingest.py:ensure_dataset() if absent
PYTHONPATH=. python -c "from tests.longmemeval.ingest import ensure_dataset; import asyncio; asyncio.run(ensure_dataset())"
```

---

## 5. Cleanup / Reset Behavior

### Canonical shared-user cleanup (REQUIRED before each fresh run)

The canonical lane shares one benchmark user across all runs. Persistent state leaks between runs
unless cleanup is run explicitly. See `ISOLATION_AUDIT.md` §Canonical lane audit and
`TEARDOWN_AUDIT.md` §Canonical lane snapshots.

```bash
# Destructive: removes the shared benchmark user and ALL attached conversations, messages,
# memories, extraction logs, and retrieval logs via cleanup_test_user(). Run this before run1 of any new baseline.
PYTHONPATH=. python -m tests.longmemeval.ingest --cleanup
```

After cleanup the DB should read zeros across all benchmark tables. Verify with:

```bash
# Python-based verification (works without psql on the host):
PYTHONPATH=. python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://daemon:daemon@127.0.0.1:5432/daemon')
    rows = await conn.fetch('''
        SELECT '\''users'\'', count(*) FROM users WHERE email = '\''longmemeval@daemon.test'\''
        UNION ALL SELECT '\''conversations'\'', count(*) FROM conversations WHERE user_id = '\''12345678-1234-5678-1234-567812345678'\''::uuid
        UNION ALL SELECT '\''messages'\'', count(*) FROM messages WHERE user_id = '\''12345678-1234-5678-1234-567812345678'\''::uuid
        UNION ALL SELECT '\''memories'\'', count(*) FROM memories WHERE user_id = '\''12345678-1234-5678-1234-567812345678'\''::uuid
        UNION ALL SELECT '\''memory_extraction_log'\'', count(*) FROM memory_extraction_log WHERE user_id = '\''12345678-1234-5678-1234-567812345678'\''::uuid
        UNION ALL SELECT '\''retrieval_log'\'', count(*) FROM retrieval_log WHERE user_id = '\''12345678-1234-5678-1234-567812345678'\''::uuid
    ''')
    for r in rows:
        print(f'{r[0]}: {r[1]}')
    await conn.close()
asyncio.run(check())
"

# Or with psql if available on the host (optional, not required):
# psql "postgresql://daemon:daemon@127.0.0.1:5432/daemon" -c \
#   "SELECT 'users' as tbl, count(*) as c FROM users WHERE email = 'longmemeval@daemon.test'
#    UNION ALL SELECT 'conversations', count(*) FROM conversations WHERE user_id = '12345678-1234-5678-1234-567812345678'::uuid
#    ..."
```

### Fast lane cleanup

The fast lane calls `cleanup_benchmark_state()` automatically before and after every question,
and deletes the per-run UUID user at end-of-run. No manual cleanup required. However, final
user deletion is not in an outer `finally` — unexpected process interruption can leave orphan
rows. See `ISOLATION_AUDIT.md` §Fast lane audit.

---

## 6. Canonical Run Invocation

### Full pipeline (ingest → evaluate → score)

```bash
# Canonical lane — full pipeline
DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon \
PYTHONPATH=. python -m orchestrator.eval.longmemeval run \
  --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json \
  --output-dir tests/benchmark_results/dev_subset_baseline/runN
```

The runner auto-writes checkpoint after each phase. Resume is automatic — a partially-completed
run will resume from its checkpoint on re-invocation.

### Per-phase invocations

```bash
# Phase 1: ingest only
DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon \
PYTHONPATH=. python -m orchestrator.eval.longmemeval ingest \
  --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json \
  --output-dir tests/benchmark_results/dev_subset_baseline/runN

# Phase 2: evaluate only (requires checkpoint from ingest)
DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon \
PYTHONPATH=. python -m orchestrator.eval.longmemeval evaluate \
  --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json \
  --output-dir tests/benchmark_results/dev_subset_baseline/runN

# Phase 3: score only (requires results from evaluate)
PYTHONPATH=. python -m orchestrator.eval.longmemeval score \
  --output-dir tests/benchmark_results/dev_subset_baseline/runN
```

### Artifact locations produced by canonical lane

| Phase | File | Contents |
|---|---|---|
| ingest / run | `longmemeval_checkpoint.json` | Phase statuses, corpus-plan ingest results, `benchmark_effective_config`, `benchmark_config_drift_warnings` |
| evaluate / run | `longmemeval_results.jsonl` | Per-question `{question_id, hypothesis, reference, judgment, category, ...}` |
| score / run | `longmemeval_score.json` | `strict_accuracy`, `result_count`, category accuracies, `generated_at` |

---

## 7. Current Dev-Subset Variance Outcome

**Phase 0 is incomplete. The variance gate has FAILED after two completed scored runs.**

Per `tests/benchmark_results/dev_subset_baseline/VARIANCE.md`:

| Run | Outcome | `strict_accuracy` | Notes |
|---|---|---|---|
| `run1` | completed | **32.0%** (16/50 correct) | Checkpoint: ingest 2079, evaluate 50, score 50 |
| `run2` | completed | **22.0%** (11/50 correct) | Checkpoint: ingest 2079, evaluate 50, score 50 |
| `run3` | **not started** | n/a | Not started because gate already violated after run2 |

- `run1 = 32.0%` (16/50 correct)
- `run2 = 22.0%` (11/50 correct)

- **Spread:** `32.0pp − 22.0pp = 10.0pp`
- **Variance gate:** `≤ 3pp`
- **Verdict:** Phase 0 reopened. run3 was not attempted.

`run1` judgment mix: 16 correct, 32 incorrect, 2 partially_correct.
`run2` judgment mix: 11 correct, 37 incorrect, 2 partially_correct.

Category breakdowns are in `VARIANCE.md`. `run2` strictly dominates `run1` by 5 additional
question failures — the failure sets are `run2 ⊃ run1` at the question_id level, meaning the
additional failures in run2 are not new types but regraded versions of run1 correct answers.

The five regraded question IDs and the active-memory state at time of judgment are **not**
preserved in the committed baseline artifacts. Extraction-failed rows are present in the
checkpoint (`extraction_failed`, `extraction_timeout`). See `issues.md` §2026-04-18T13:40:00Z.

---

## 8. Extraction Barrier Contract

Canonical ingest uses a corrected two-stage extraction barrier:

1. **Primary barrier** — `await process_extraction(...)` inside `ingest_session()`. This is the
   authoritative completion fence. `process_extraction()` calls `store.log_extraction()` before
   returning, so the awaited call itself proves extraction finished.
2. **Secondary polling barrier** — `poll_extraction_complete()` against `memory_extraction_log`.
   This is a configurable poll with a `5.0s` total timeout, `0.1s` initial interval, `x2.0` backoff,
   and `2.0s` poll cap. The old `90s / 2.0s` cadence is retired.

Corrected barrier defaults (`ExtractionPollConfig()`) from `tests/longmemeval/ingest.py`:

| Parameter | Value |
|---|---|
| `max_wait_seconds` | `5.0` |
| `poll_interval` (initial) | `0.1` |
| `backoff_multiplier` | `2.0` |
| `poll_cap` | `2.0` |

When the deadline is exhausted the session is marked `extraction_timeout` in the checkpoint result
and ingestion continues to the next session. See `BARRIER_AUDIT.md` for the full delegation table.

---

## 9. Artifact Interpretation

### Reading strict accuracy

```python
import json

with open("tests/benchmark_results/dev_subset_baseline/runN/longmemeval_score.json") as f:
    score = json.load(f)

print(f"Strict accuracy: {score['accuracy']['strict_accuracy']:.1%}")  # e.g. "32.0%"
print(f"Result count: {score['result_count']}")
for cat, acc in score["accuracy"]["category_accuracies"].items():
    print(f"  {cat}: {acc:.1%}")
```

### Detecting extraction failures in checkpoint

```python
with open("tests/benchmark_results/dev_subset_baseline/runN/longmemeval_checkpoint.json") as f:
    ckpt = json.load(f)

failed = [
    r for r in ckpt["phases"]["ingest"]["results"].values()
    if r.get("status") in ("extraction_failed", "extraction_timeout")
]
print(f"Extraction failures: {len(failed)} / {len(ckpt['phases']['ingest']['results'])}")
```

### Variance gate check

```python
import json

runs = {}
for run_n in ["run1", "run2", "run3"]:
    score_path = f"tests/benchmark_results/dev_subset_baseline/{run_n}/longmemeval_score.json"
    try:
        with open(score_path) as f:
            runs[run_n] = json.load(f)["accuracy"]["strict_accuracy"]
    except FileNotFoundError:
        runs[run_n] = None

spread = max(v for v in runs.values() if v is not None) - min(v for v in runs.values() if v is not None)
gate = 0.03  # 3pp
print(f"Spread: {spread:.1%}, Gate: ≤{gate:.1%}, Passed: {spread <= gate}")
```

---

## 10. Full-Corpus Verification Contract

The dev-subset variance gate failure means **Phase 0 is reopened**. The locked 50-case dev subset
is a tractable iteration tool, not the final authoritative baseline.

**Later work will run the full 500-case canonical corpus through the same three-run reproducibility
protocol.** The full-corpus path is excluded from this HARNESS because:

- Canonical full-corpus ingest throughput projects to ~32h per run after the corrected barrier
  (`tests/benchmark_results/dev_subset_baseline/VARIANCE.md` §dev-subset pace evidence supports
  the ~3.79h/dev-subset ingest lower bound, extrapolated to the full 18,464 unique normalized
  sessions).
- Three full-corpus runs for variance lock would require ~96h of ingest alone before
  evaluation/scoring — outside any single session window.
- Dev-subset Phase 0 must resolve first; its purpose is to catch reproducibility failures cheaply
  before committing full-corpus resources.

When full-corpus Phase 0 is later attempted, the contract is:

```text
DATABASE_URL=postgresql://daemon:daemon@127.0.0.1:5432/daemon \
PYTHONPATH=. python -m orchestrator.eval.longmemeval run \
  --dataset /tmp/longmemeval-review/data/longmemeval_s.json \
  --output-dir tests/benchmark_results/full_corpus_baseline/runN
```

Full-corpus authoritative verification will produce `tests/benchmark_results/full_corpus_baseline/`
artifacts with the same checkpoint/result/score artifact contract. The dev-subset `run1`/`run2`
artifacts under `tests/benchmark_results/dev_subset_baseline/` remain the Phase 0 truth record
even after full-corpus work begins.

---

## 11. Verification Checklist

Before claiming a reproducible benchmark result, confirm all of:

- [ ] Clean reset via `python -m tests.longmemeval.ingest --cleanup` with DB zeros verified
- [ ] Canonical lane only — fast lane not used for extraction/dedup/linking claims
- [ ] Dev subset fixture at `tests/benchmark_longmemeval/fixtures/dev_subset.json` is unmodified
- [ ] One, two, or three runs (`run1`, `run2`, `run3`) exist under `tests/benchmark_results/dev_subset_baseline/`, matching whether the variance gate halted early or the full three-run sequence completed
- [ ] Spread across all **completed scored** runs is `≤ 3pp`; if the gate is already violated, `VARIANCE.md` explicitly records Phase 0 reopened and no further runs are started
- [ ] `benchmark_config_drift_warnings` in checkpoint is empty (no config drift vs pinned pin)
- [ ] Extraction failures documented and stable across completed runs
- [ ] Per-phase artifacts verified for each completed run: checkpoint phase counts, result count, category accuracies

---

## 12. Key File Map

| Purpose | File |
|---|---|
| Canonical CLI entrypoint | `orchestrator/eval/longmemeval.py` |
| Canonical runner + config pinning | `orchestrator/eval/runner.py` |
| Fast lane entrypoint | `orchestrator/eval/longmemeval_fast.py` |
| Canonical ingest logic | `tests/longmemeval/ingest.py` |
| Canonical evaluate/score logic | `tests/longmemeval/evaluate.py` |
| Dev subset fixture (50 cases) | `tests/benchmark_longmemeval/fixtures/dev_subset.json` |
| Dev subset coverage + selection rules | `tests/benchmark_longmemeval/fixtures/dev_subset_coverage.md` |
| Isolation audit | `tests/benchmark_longmemeval/ISOLATION_AUDIT.md` |
| Barrier audit | `tests/benchmark_longmemeval/BARRIER_AUDIT.md` |
| Config pinning inventory | `tests/benchmark_longmemeval/CONFIG_PINNING.md` |
| Teardown audit | `tests/benchmark_longmemeval/TEARDOWN_AUDIT.md` |
| Variance baseline record | `tests/benchmark_results/dev_subset_baseline/VARIANCE.md` |
| Checkpoint pin artifact | `tests/benchmark_longmemeval/longmemeval_config_pin.json` |
