# LongMemEval on a laptop — runbook

How to run the production-parity fact harness (`python -m orchestrator.eval.longmemeval`) so a
multi-hour run survives sleep, a lid close, a shutdown or a killed process, and never touches a real
database.

Spend gate: anything larger than the `--limit 3` smoke test needs an owner-approved cost and time
estimate first.

## 0. Rules

- **Benchmark database only.** The harness deletes everything the benchmark user owns. It refuses
  to start unless `DATABASE_URL` names a database starting with `daemon_lme_bench` *and* that
  database carries the marker comment `daemon-longmemeval-benchmark-v1`
  (`orchestrator/eval/bench_db.py`). `bench_db.sh run` always supplies the benchmark DSN. Never set
  the marker on any other database, and never point the harness at production `.env` values.
- **One checkout per run.** A run is pinned to the git SHA recorded in its `run_manifest.json`.
  Resuming on a different SHA (or a dirty tree that changed) is refused unless you pass
  `--allow-code-change`, which records the change in the manifest. Never `git pull` or rebase the
  run's checkout while a run is in progress; use a dedicated worktree.
- **Results live outside the repo** until the run is finished. Copy in only what you would publish
  (step 9).

## 1. One-time setup

```sh
git worktree add ../daemon-bench <sha-under-test>      # dedicated, never pulled mid-run
cd ../daemon-bench
uv sync --locked
tests/benchmark_longmemeval/bench_db.sh up              # project daemon-lme-bench, 127.0.0.1:55432
tests/benchmark_longmemeval/bench_db.sh init            # migrate + stamp the marker
tests/benchmark_longmemeval/bench_db.sh status          # expect marker=daemon-longmemeval-benchmark-v1
```

The stack (`compose.bench.yml`) is its own Compose project, volume (`daemon_lme_bench_pgdata`),
database and port; it shares nothing with `docker-compose.yml`. `init` also creates a
benchmark-only Fernet key at `~/.local/share/daemon-lme-bench/encryption.key` (override the
directory with `LME_BENCH_HOME`). Keep that key: snapshots are unreadable without it. Never commit
it.

`bench_db.sh run` refuses to start if the checkout has a `.env`, because `Settings` would read it.

## 2. Credentials and route

```sh
export OPENROUTER_API_KEY=...        # bash/zsh;  fish: set -gx OPENROUTER_API_KEY ...
```

Export only the provider key. Do not source a production `.env`. `bench_db.sh run` sets
`DATABASE_URL`, `DAEMON_ENCRYPTION_KEY`, `PYTHONPATH` and
`DAEMON_INFERENCE_POLICY=config/inference_policy.production.json`.

| Mode | Set | Effect |
|---|---|---|
| Qualified embeddings | `EMBEDDING_ROUTE_ID=azure-openrouter-small-1024` | Ingest stores document embeddings, evaluate embeds the query |
| Lexical | leave `EMBEDDING_ROUTE_ID` unset | Retrieval uses the lexical fallback, as production does without a route |

Document embeddings are written during **ingest**. To compare the two retrieval modes on one
corpus, ingest once *with* the route, then run one evaluate with it (A) and one without it (B). The
manifest records which mode each phase actually used (`embedding`, `phases.evaluate.retrieval_modes`).

## 3. Start a phase that survives the laptop

```sh
RUN=~/.local/share/daemon-lme-bench/runs/$(date +%F)-$(git rev-parse --short HEAD)
tmux new -s lme
systemd-inhibit --what=sleep:idle:handle-lid-switch --who=longmemeval --why="benchmark run" \
  tests/benchmark_longmemeval/bench_db.sh run -- \
  uv run --locked python -m orchestrator.eval.longmemeval ingest \
    --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json \
    --output-dir "$RUN/ingest"
```

Detach with `C-b d` and reattach with `tmux attach -t lme`. `systemd-inhibit` keeps the machine
awake while the command runs; on AC power you may still want to disable lid-close suspend in your
desktop settings. Everything the run needs to resume is under `--output-dir`.

## 4. Stopping and resuming

| What happened | What the harness does |
|---|---|
| `Ctrl-C` / `SIGTERM` / `SIGHUP` once | Finishes the in-flight session or question, saves, exits 130/143/129 |
| The same signal twice | Abandons the in-flight item at once and saves |
| `SIGKILL`, crash, power loss, suspend that killed the process | Nothing on exit. Every completed item is already on disk |

Resume by re-running **the same command** in the same checkout. On resume:

- **Ingest.** A session's conversation id is checkpointed before its messages are written. Any
  benchmark conversation the checkpoint doesn't reference as complete (at most one, from the
  interrupted session) is rolled back (its memories, extraction log and conversation), then
  re-ingested. More than one unexplained conversation means the database wasn't built by this
  checkpoint, and the run refuses. Restore the matching snapshot or start a fresh output
  directory. Rollbacks are recorded in `ingest_rollbacks`. Dedup merges or supersessions an
  interrupted session already applied to other sessions' memories are not undone.
- **Evaluate.** Each result is appended to `longmemeval_results.jsonl` and fsynced before the
  checkpoint is saved. Resume adopts results that reached the file, drops a torn trailing line and
  duplicates, and never re-asks a completed question (`results_reconciliations`). Questions that
  errored are retried.
- `--retry-failed-sessions` (ingest) rolls back and re-ingests sessions whose extraction failed.

Each invocation is logged in `run_manifest.json` (`invocations[]`: start, end, outcome,
`resumed`, active seconds).

Stop the database only after the harness has exited: `bench_db.sh down` (the volume is kept).
After a reboot: `bench_db.sh up`, then resume.

## 5. Snapshot the finished ingest

```sh
tests/benchmark_longmemeval/bench_db.sh snapshot "$RUN/ingest.dump"   # prints sha256; never overwrites
```

## 6. Evaluate-only reruns

Evaluate reads memory without side effects (no access touches, no trust writes). The manifest
fingerprints the database before and after (`state_matches_ingest_at_start`,
`state_unchanged_by_evaluate`). Still restore the snapshot before every evaluate so each run starts
from the exact ingest state:

```sh
tests/benchmark_longmemeval/bench_db.sh restore "$RUN/ingest.dump"
EMBEDDING_ROUTE_ID=azure-openrouter-small-1024 \
systemd-inhibit --what=sleep:idle:handle-lid-switch --who=longmemeval --why="benchmark run" \
  tests/benchmark_longmemeval/bench_db.sh run -- \
  uv run --locked python -m orchestrator.eval.longmemeval evaluate \
    --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json \
    --from-ingest "$RUN/ingest/longmemeval_checkpoint.json" \
    --ingest-snapshot "$RUN/ingest.dump" \
    --output-dir "$RUN/eval_a_qualified"

tests/benchmark_longmemeval/bench_db.sh restore "$RUN/ingest.dump"
# same command without EMBEDDING_ROUTE_ID, --output-dir "$RUN/eval_b_lexical"
```

(fish: prefix with `env EMBEDDING_ROUTE_ID=...` instead.) The evaluate refuses if the database
fingerprint differs from the ingest's. `--allow-state-mismatch` overrides this, is recorded, and
makes the results non-comparable.

## 7. Score and compare

```sh
for d in eval_a_qualified eval_b_lexical; do
  tests/benchmark_longmemeval/bench_db.sh run -- uv run --locked python -m orchestrator.eval.longmemeval score \
    --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json --output-dir "$RUN/$d"
done
tests/benchmark_longmemeval/bench_db.sh run -- uv run --locked python -c \
  'import sys; from pathlib import Path; from orchestrator.eval.substrate import assert_substrate_match as m; m(Path(sys.argv[1]), Path(sys.argv[2])); print("substrate ok")' \
  "$RUN/eval_a_qualified/longmemeval_score.json" "$RUN/eval_b_lexical/longmemeval_score.json"
```

Scores carry `accuracy` (partial = 0.5), `strict_accuracy`, per-category counts and errored
question ids. Spend comes from the benchmark account's own ledger (`phases.*.compute_spend`).

## 8. Smoke test (no approval needed)

Use `--limit 3` with a separate `--output-dir`. To exercise resume, `kill -9` the process mid-ingest
and mid-evaluate, re-run the same command, and check `ingest_rollbacks`,
`results_reconciliations` and `invocations` in the manifest.

## 9. What to commit

Copy into `tests/benchmark_results/<name>/` only `run_manifest.json`, `longmemeval_score.json` and
`longmemeval_results.jsonl`, and only after reading them. They hold public LongMemEval text,
model output and hashes. Never commit `*.dump`, the encryption key, checkpoints with local paths
you'd rather not publish, or anything from a `.env`. gitleaks runs in pre-commit.

## 10. Tear down

```sh
tests/benchmark_longmemeval/bench_db.sh down
docker compose -f tests/benchmark_longmemeval/compose.bench.yml down -v   # deletes the benchmark volume
```
