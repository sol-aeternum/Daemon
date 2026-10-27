# LongMemEval on a laptop — runbook

How to run the production-parity fact harness (`python -m orchestrator.eval.longmemeval`) so a
multi-hour run survives sleep, a lid close, a shutdown or a killed process, and never touches a real
database.

Spend gate: anything larger than the `--limit 3` smoke test needs an owner-approved cost and time
estimate first.

## 0. Rules

- **Benchmark database only.** The harness deletes everything the benchmark users own. It refuses
  to start unless `DATABASE_URL` names a database starting with `daemon_lme_bench` *and* that
  database carries the marker comment `daemon-longmemeval-benchmark-v1`
  (`orchestrator/eval/bench_db.py`). `bench_db.sh run` always supplies the benchmark DSN. Never set
  the marker on any other database, and never point the harness at production `.env` values.
- **One checkout per run.** A run is pinned to the git SHA *and* a hash of the code content
  (`git.code_sha256`: uncommitted and untracked changes under `orchestrator/`, `providers/`,
  `config/`, `migrations/`, the harness and the lockfiles) recorded in its `run_manifest.json`.
  Resuming with either changed is refused. `--allow-code-change` overrides the refusal, but
  records `code_changed_mid_run` and marks the run `baseline_qualified: false`. Never `git pull`
  or rebase the run's checkout while a run is in progress; use a dedicated worktree.
- **One configuration per run.** The manifest records `effective_config`: hashes of every
  non-secret `Settings` field, the inference policy, model routing, the config pin and the harness
  options. Resuming with any of them changed (for example a different `EMBEDDING_ROUTE_ID`) is
  refused with no override. Start a fresh `--output-dir` instead.
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
benchmark-only Fernet key (`encryption.key`) and auth pepper (`auth.pepper`) in
`~/.local/share/daemon-lme-bench/` (override with `LME_BENCH_HOME`). Keep both: snapshots are
unreadable without the key, and stored keyed hashes depend on the pepper. Never commit them.

`bench_db.sh run` refuses to start if the checkout has a `.env`, because `Settings` would read it.

## 2. Credentials and route

```sh
export OPENROUTER_API_KEY=...        # bash/zsh;  fish: set -gx OPENROUTER_API_KEY ...
```

Export only the provider key. Do not source a production `.env`. `bench_db.sh run` sets
`DATABASE_URL`, `DAEMON_ENCRYPTION_KEY`, `DAEMON_AUTH_PEPPER`, `PYTHONPATH` and
`DAEMON_INFERENCE_POLICY=config/inference_policy.production.json`.

| Mode | Set | Effect |
|---|---|---|
| Qualified embeddings | `EMBEDDING_ROUTE_ID=azure-openrouter-small-1024` | Ingest stores document embeddings, evaluate embeds the query |
| Lexical | leave `EMBEDDING_ROUTE_ID` unset | Retrieval uses the lexical fallback, as production does without a route |

Document embeddings are written during **ingest**. To compare the two retrieval modes on one
corpus, ingest once *with* the route, then run one evaluate with it (A) and one without it (B). The
manifest records which mode each phase actually used (`embedding`, `phases.evaluate.retrieval_modes`).

## 3. Start a phase that survives the laptop

Each question is ingested into its **own benchmark user**: `uuid5(BENCHMARK_USER_NAMESPACE,
"longmemeval:<question_id>")`, email `longmemeval+<hex>@daemon.test`. That user's memory holds
only that question's haystack, as LongMemEval intends and as one production account holds one
history. A session that appears in several questions' haystacks is ingested once per question.
Within a question the sessions go in `haystack_dates` order (stable; dataset order if any date
doesn't parse). The manifest records it under `benchmark_users`: questions, sessions, and how many
questions were reordered.

Because users never share memory, `--concurrency N` ingests N questions side by side without
changing what any one user sees. Each user still ingests one session at a time. The default is 1.
Use the smoke test (§8) to check the provider tolerates N before a long run.

```sh
RUN=~/.local/share/daemon-lme-bench/runs/$(date +%F)-$(git rev-parse --short HEAD)
tmux new -s lme
systemd-inhibit --what=sleep:idle:handle-lid-switch --who=longmemeval --why="benchmark run" \
  tests/benchmark_longmemeval/bench_db.sh run -- \
  uv run --locked python -m orchestrator.eval.longmemeval ingest \
    --dataset tests/benchmark_longmemeval/fixtures/dev_subset.json \
    --concurrency 8 \
    --output-dir "$RUN/ingest"
```

Each phase first repeats the worker's startup: it validates the auth pepper and runs the
production ZDR route attestation (backend and worker do the same at startup: two public OpenRouter listings, recorded in the benchmark database). Without it every
monitored route fails closed. Ingest refuses to start if the `background` profile has no admitted
route, and stops after 5 consecutive failed sessions. The manifest records `route_attestations`
and `routes.extraction.approved_route_ids`.

Detach with `C-b d` and reattach with `tmux attach -t lme`. `systemd-inhibit` keeps the machine
awake while the command runs; on AC power you may still want to disable lid-close suspend in your
desktop settings. Everything the run needs to resume is under `--output-dir`.

## 4. Stopping and resuming

| What happened | What the harness does |
|---|---|
| `Ctrl-C` / `SIGTERM` / `SIGHUP` once | Finishes the in-flight sessions (one per running question) or question, saves, exits 130/143/129 |
| The same signal twice | Abandons the in-flight item at once and saves |
| `SIGKILL`, crash, power loss, suspend that killed the process | Nothing on exit. Every completed item is already on disk |

Resume by re-running **the same command** in the same checkout. On resume:

- **Ingest.** Each session is checkpointed after it finishes. Before a session starts, the
  checkpoint records it in `pending_sessions[<question_id>]` together with a fingerprint of that
  question's user. With `--concurrency N` up to N questions can have a pending session. On resume,
  each in-flight session's conversation, memories and extraction log are deleted, and that user's
  fingerprint is taken again:
  - It matches: the rollback is exact (`ingest_rollbacks[].restored_pre_session_state: true`), and
    the session is re-ingested.
  - It differs: the interrupted session had already changed another session's memories (a dedup
    merge or supersession). The harness refuses to resume and keeps the pending entry. Restore the
    last snapshot that matches this checkpoint, or start a fresh output directory and database.
  - A user with an unexplained conversation and no pending session, or more than one, means the
    database wasn't built by this checkpoint, and the run refuses.
  - A session that raises in one question stops the run. The other questions finish their
    in-flight session first, so the checkpoint is resumable.
- **Evaluate.** Each result carries the run's `run_id` and is appended to
  `longmemeval_results.jsonl` and fsynced before the checkpoint is saved. Resume adopts results of
  this run that reached the file, drops a torn trailing line and duplicates, and never re-asks a
  completed question (`results_reconciliations`). Rows from another run (or without a `run_id`)
  are refused, never adopted, so a reused output directory can't mix runs. Questions that errored
  are retried.
- `--retry-failed-sessions` (ingest) rolls back and re-ingests sessions whose extraction failed.
  It marks the run `baseline_qualified: false` (`retried_failed_sessions`).

Each invocation is logged in `run_manifest.json` (`invocations[]`: start, end, outcome,
`resumed`, active seconds). An invocation that never closed (SIGKILL, power loss) is closed as
`killed` at its last recorded progress when the next one starts. `baseline_qualified` and
`disqualifications[]` record anything that keeps the run from counting as a baseline.

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

An evaluate-only run inherits the source ingest's qualification from its `run_manifest.json`
(`ingest_source.baseline_qualified` and its `disqualifications`). If the source is disqualified, or its
manifest is missing or belongs to another run, the evaluate run is marked `baseline_qualified: false`
(`source_ingest_not_qualified`). It still runs, so the results can be used for diagnosis.

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

Use `--limit 3 --concurrency 3` with a separate `--output-dir`. To exercise resume, `kill -9` the
process mid-ingest and mid-evaluate, re-run the same command, and check `ingest_rollbacks` (one
per question that had a session in flight), `results_reconciliations` and `invocations` in the
manifest. Check the log for provider 429s or failed chunks before you pick `--concurrency` for a
long run.

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
