# LongMemEval harness parity baseline — 2026-10-26

**Status:** harness parity and resumability are implemented and smoke-tested at `cc4b8045` (ingest at `b8c59918`). The dev-subset baseline
(Phase D) **has not been run**. It waits for owner approval of the cost and time estimate in §6.

These numbers are **not comparable** to the historical 10.4% / 67.8% / 81.1% results. Those came from a
harness that did not build the production memory prompt. On `main`, its ingest also could not extract
facts at all (§1). Any comparison must follow the proposed rule in
[`ACCEPTANCE_RULE_PROPOSED.md`](ACCEPTANCE_RULE_PROPOSED.md) (Proposed — owner approval required).

Base: `origin/main` @ `1253b255`. Branch `claude-work/memory-harness-parity-20261026`.

## 1. Phase A findings (read-only verification of `main`)

### 1.1 The harness answered from a prompt production never builds
- The answer path was `tests/longmemeval/evaluate.py` `evaluate_single()` → `answer_with_llm()` →
  `build_answer_prompt()` (`:333-343` on `main`): one user message, "You are a helpful assistant…
  Memories:\n- <content>". It had no Daemon system prompt, `<memory_records>` fence, category labels,
  `[FROZEN MEMORIES]` L0 block, summaries or token budget.
- `fact_harness.build_assembled_system_prompt()` existed but was only hashed. A `_uses_committed_pin`
  short-circuit skipped even that, so the pin (`active_message_roles: [system, user]`) never detected the drift.
- The fact harness never passed `benchmark_mode`. Answers used floating `openrouter/openai/gpt-4o` at
  temperature 0.7, unseeded.
- The richer harness described in `wave1_benchmark_consumer_path.md` (`_format_eval_memory_block`,
  `answer_prompt_metadata`) was never committed to `main`. `git log -S` finds nothing.
- `HARNESS.md` documented a 5 s extraction barrier and `strict_accuracy` keys. The code polled for 90 s and
  returned a flat per-category dict.

### 1.2 Ingest could not extract on `main`
- Extraction runs through `guarded_completion()`, which raises `ComputeUnavailable("account_unavailable")`
  outside an `account_compute` scope (`orchestrator/compute_runtime.py` `current_scope()`). `extraction.py`
  swallows that error, so the call returns `succeeded=False` with 0 facts. This was verified offline.
- `process_extraction()` then returns without writing `memory_extraction_log`. The harness ignored the
  return value and polled 90 s, so every session ended `extraction_timeout` with zero memories. That is
  about 52 h for a 0-fact dev-subset corpus.
- The harness's raw `INSERT INTO users` created no entitlement account, and `EntitlementService.resolve()`
  refuses unknown accounts.
- The smoke run surfaced two more process-startup gaps, both now handled (§3):
  - A bare process never runs the ZDR route attestation that backend and worker run at startup. Every
    monitored route failed closed (`zdr_attestation_unknown`), so the `background` profile had 0 candidates.
  - `MemoryStore` needs the auth pepper, which production configures. Every chunk failed with
    `PepperValidationError`.

### 1.3 Ingest truncated sessions
The harness sent each whole session as one `process_extraction(text=…)`, and extraction keeps only the last
4,000 chars. The production worker splits messages losslessly with `_chunk_messages_for_extraction()` and
makes one extraction call per ≤ 4,000-char chunk. The dev subset has 2,079 sessions and 24.0 M transcript
chars in 8,413 worker chunks. The old harness could see at most about 8.3 M of those chars.

### 1.4 Evaluate mutated the state it measured
- `retrieve_memories_for_text()` always scheduled `bulk_touch_memories` (`access_count+1`,
  `last_accessed_at=NOW()`). Both are ranking inputs (access boost, recency), so question N perturbed question
  N+1 and every later run.
- `build_memory_context()` records trust signals (`conversations.last_retrieved_memory_ids`,
  `memories.last_retrieved_at`).
- `retrieval_log` rows are diagnostics only. No ranking path reads them.

### 1.5 Durability
- Checkpoint (`fact_harness.write_json`), `evaluate.save_checkpoint` and `write_results_jsonl` all used plain
  `"w"` writes, the results file being rewritten in full after every question. A kill mid-write could tear
  the only copy of the 2,079-entry ingest map.
- A kill between a session's DB writes and the checkpoint save re-ingested that session on resume,
  duplicating its conversation and facts.
- Errored questions were stored as completed `incorrect` rows and never retried.
- Evaluate could not reuse an ingest from another output directory.

## 2. What changed

The PR describes the changes. In short:

- `build_memory_context()` = `gather_memory_context_inputs()` + `render_memory_context()`. The split is
  byte-identical against a frozen copy of the original across 1,296 cases.
- The harness answers with `[system: compose_system_prompt(rendered block), user: question]` and the
  shared renderer, with side effects off.
- Ingest uses the worker chunker inside `account_compute(... profile="background")`, after the worker's
  startup steps (pepper, route attestation).
- Durable progress, evaluate-only reruns, the run manifest and the benchmark-DB guard. See
  `RUNBOOK_LAPTOP.md`.

## 3. Parity exceptions

Recorded verbatim in every `run_manifest.json` (`parity_exceptions`), from
`orchestrator/eval/fact_harness.py` `PARITY_EXCEPTIONS`.

| id | Difference from a production chat turn | Impact |
|---|---|---|
| `query_source` | Question text stands in for the latest user message | Same normalisation and retrieval call; no prior turns in the query |
| `haystack_scope` | Retrieval, L0 and summaries are scoped to the question's haystack conversations | Needed because 50 questions share one benchmark user; L0 and summary scoping filters after the store limit |
| `read_only_evaluate` | No trust recording, no access touches | Production would shift later ranking; the harness keeps every question on the ingest state |
| `retrieval_log_label` | Retrieval logging forced on, `retrieval_triggered_by="longmemeval"` | Diagnostics only |
| `system_prompt_extras` | No preferences, skills index, answer controls or tools | Memory block and base prompt match; the reader can't call memory tools |
| `answer_judge_models` | Pinned `gpt-4o-2024-08-06` on the Azure endpoint, temperature 0, seed 42, no fallback | Fixed reader, not end-to-end product quality |
| `no_post_extraction_jobs` | Extraction and dedup only: no entity resolution, summaries, dreaming or consolidation | Entity expansion and summaries are empty |
| `message_timestamps` | Messages stored at ingest time; `query_reference_time=None` | Temporal questions are disadvantaged relative to real use |

Production-identical: memory renderer and fence, base system prompt, retrieval call (limit 5, L0 via
`get_l0_memories`, 8 s embedding timeout, lexical fallback), extraction prompt and chunker, the `background`
compute route, dedup, route attestation, and encryption at rest.

## 4. Smoke (`--limit 3`) and resume evidence

All smoke runs used `tests/benchmark_longmemeval/fixtures/dev_subset.json --limit 3`, a fresh benchmark
database (`bench_db.sh`, marker verified), `config/inference_policy.production.json` and the qualified
embedding route `azure-openrouter-small-1024` for ingest. Run directory:
`~/.local/share/daemon-lme-bench/runs/smoke-2026-10-26-b8c59918/` (not committed: it holds a database
dump and local paths).

### 4.1 Earlier attempts (each found a real gap, fixed on this branch)

| Head | What happened | Fix |
|---|---|---|
| `0cb9acf4` | Every chunk failed: no admitted route for the `background` profile (`zdr_attestation_unknown`) | `05651a73`: run production route attestation per phase |
| `05651a73` | Every chunk failed with `PepperValidationError`; the 5-session breaker stopped the run | `0da35493`: validate and initialise the auth pepper like the worker |
| `0da35493` | Extraction worked and SIGKILL resumed, but SIGTERM was ignored (importing the arq worker installs its own handlers) and killed invocations stayed `running` | `b8c59918`: reassert handlers after the import; close dead invocations as `killed` |
| `b8c59918` (evaluate) | Every question failed: `No endpoints found for openai/gpt-4o-2024-08-06` (the account's ZDR guardrail removes the OpenAI endpoint, and fallbacks are off) | `1e9e2e50`: pin the Azure endpoint for the same snapshot (owner decision) |
| `42c5fb31` (evaluate) | One question per run failed with `BenchmarkSamplingError`: Azure rotates `system_fingerprint` across nodes | `cc4b8045`: record per-row fingerprints instead of failing (owner decision) |

### 4.2 Ingest with interruptions (`b8c59918`)

| Leg | Window (UTC, 2026-10-26) | Signal | Outcome |
|---|---|---|---|
| 1 | 13:07:05 – 13:42:16 | `SIGKILL` once ≥ 8 sessions were checkpointed | exit 137; invocation later closed as `killed` at its last progress (2,111 s active) |
| 2 | 13:42:23 – 14:14:06 | `SIGTERM` once ≥ 20 sessions were checkpointed | Finished the in-flight session, then exit 143, `interrupted:SIGTERM`, 21 sessions checkpointed, no `pending_session` (1,903 s) |
| 3 | 14:14:10 – 16:23:08 | none | exit 0, `completed`, 126 sessions |

- **Resume after SIGKILL:** `ingest_rollbacks` = 1 entry: 1 orphan conversation, 0 memories, 0 extraction-log rows,
  `restored_pre_session_state: true`. The cross-session fingerprint after the rollback equalled the one
  checkpointed before the session started, so the rollback was exact.
- **Resume after SIGTERM:** no rollback was needed.
- **Final state:** 126 sessions `complete`, 510 chunks, 0 failed chunks, 1,453 memories, all 1,453 embedded
  (`openrouter:azure:openai/text-embedding-3-small:1024`). State fingerprint `f5c41f30…`; snapshot
  `ingest.dump` sha256 `872c19dd…`.
- **Route attestation** ran on every leg: 9 of 9 routes attested, 0 check failures. Extraction always used
  `luna-azure-eu` (`openrouter/openai/gpt-6-luna`, 510 of 510 chunks).
- **Manifest:** `baseline_qualified: true` and no `disqualifications`. All three invocations show `resumed: true`.
  That flag was wrong at `b8c59918` and is fixed in `42c5fb31`; the evaluate manifests below show the
  corrected values.
- **Provider health:** 8 of about 2,170 luna completion attempts failed (7 `deadline_exceeded`, 1 unspecified),
  and no chunk failed. Latency varied about 4× over the night. During the first 40 minutes, extraction
  scopes averaged 76 s (median 55 s, max 232 s), against 19.5 s in the `0da35493` run.

### 4.3 Evaluate legs (`cc4b8045`, from the ingest above)

These ran on a later head than the ingest. The code between `b8c59918` and `cc4b8045` changes only the
answer/judge endpoint, fingerprint recording and the `resumed` flag, and none of these is used by ingest. Phase D
runs all phases on one SHA.

| Run | Retrieval | Interruption | Strict | Errors | DB state |
|---|---|---|---|---|---|
| A | qualified embeddings (3 of 3 questions) | `SIGKILL` as soon as the first result row was fsynced; resumed | 3/3 | 0 | matches ingest, unchanged by evaluate |
| B | lexical (3 of 3), evaluate-only rerun from the same snapshot | none | 1/3 | 0 | matches ingest, unchanged by evaluate |

- **Resume after SIGKILL (A):** `results_reconciliations` = `{file_rows: 1, completed: 1, errored: 0,
  duplicates_dropped: 0}`. The completed question was not asked again. Invocations: `killed (resumed: false)`, then
  `completed (resumed: true)`.
- `assert_substrate_match` passed on the two score files.
- **Fingerprints:** answer `fp_6d4f7d9eea` × 3 in both runs. Judge: A had `fp_6d4f7d9eea` × 2 and `fp_5650d8d081` × 1; B had `fp_6d4f7d9eea` × 2 and `fp_91c2c6d730` × 1.
- **The one correct lexical answer is a judge false positive.** In question `86f00804` the reader said "I don't have
  information on the book you're currently reading", and the judge returned CORRECT. I replayed that exact
  judgment 3 times (temperature 0, seed 42, same fingerprint `fp_91c2c6d730`) and got CORRECT once and
  INCORRECT twice. The judge prompt is the pinned one from `main` (it includes "Be generous with
  CORRECT"). Judge nondeterminism is part of the evaluate noise *F* in the proposed rule, and it is
  tracked in #574.

### 4.4 Time and spend per phase

| Phase | Active time | Calls | Tokens | Spend |
|---|---|---|---|---|
| Ingest (126 sessions, 510 chunks) | 11,752 s (3.26 h): 93 s/session, 23 s/chunk | 2,160 luna, 1,555 embedding | luna 2.66 M in / 0.68 M out; embedding 29 k | $1.1757 luna + $0.0016 embedding (benchmark account ledger) |
| Evaluate A (3 questions) | 16 s | 3 answer + 3 judge + 4 query embeddings | answer 7.3 k in / 61 out; judge 0.8 k in / 72 out | ≈ $0.022 gpt-4o (from row token counts) |
| Evaluate B (3 questions) | 9 s | 3 answer + 3 judge | answer 7.0 k in / 114 out; judge 0.9 k in / 85 out | ≈ $0.022 gpt-4o |

Benchmark-mode answer and judge calls go straight through LiteLLM, not through an account compute scope.
Their cost is therefore computed from the per-row `answer_usage` / `judge_usage` at gpt-4o list price
($2.50 / M input, $10 / M output), not read from the ledger. The killed evaluate leg left one open
embedding reservation in the benchmark account's ledger (`open_reservations: 1`), which is expected
after a SIGKILL mid-call and harmless.

## 5. Scores

No dev-subset scores yet (Phase D not approved). The smoke scores in §4 come from 3 questions and are
not a result.

## 6. Phase D estimate (awaiting approval)

Dev subset: 50 questions (10 multi-session, 10 temporal, 9 single-session-user, 9 single-session-assistant,
9 knowledge-update, 3 preference); 2,079 sessions; 8,413 worker chunks, 16.5 × the smoke ingest.

| Item | Projection | Basis |
|---|---|---|
| Ingest spend | **≈ $19.5** (range $16–24) | $1.177 × 8,413 / 510 chunks; ± 20 % for token variance per chunk |
| Ingest time, sequential | **≈ 54 h** (46 h at the `0da35493` rate; much longer if the overnight 4× slowdown persists) | 23 s/chunk × 8,413 |
| Snapshot + restore × 2 | minutes | `pg_dump` / `pg_restore` of the benchmark DB |
| Evaluate A + B | **≈ $0.75**, about 10 min total | 50 questions × ≈ $0.0073 × 2 runs |
| **Total** | **≈ $20 (cap $30)**, about 2.5 days of wall time | |

- **Qualified embedding credentials exist locally.** `OPENROUTER_API_KEY` with
  `EMBEDDING_ROUTE_ID=azure-openrouter-small-1024` attests, and it embedded 1,453 of 1,453 memories in the smoke.
- **Noise runs.** The proposed rule (§3 of `ACCEPTANCE_RULE_PROPOSED.md`) also needs two more
  evaluate-only reruns for *F*, about $0.75. A repeat ingest for *G* would cost another ≈ $20 and take ≈ 54 h.
  Both are optional and separately approvable.
- **Faster ingest would need a design decision.** Running sessions concurrently would cut the wall time, but
  ingest order drives dedup and supersession across sessions. Exact rollback also assumes a single
  in-flight session. So concurrency changes what is measured and is not proposed here.
- **Laptop:** the run follows `RUNBOOK_LAPTOP.md` (tmux + `systemd-inhibit`, one pinned worktree). Every
  interruption above resumed without losing or duplicating work.
