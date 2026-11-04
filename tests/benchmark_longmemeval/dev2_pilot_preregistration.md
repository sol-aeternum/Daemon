# dev2 pilot — pre-registration (#601)

**Status: frozen before any spend. No provider call, database connection, dataset
read or ingest has been made for this pilot at the time of writing. Numbers in
this document are limits and method, not measurements.**

Owner-authorised Daemon tooling scope. Route policy: paid Go DeepSeek ZDR through
2026-11-30 (see `AGENTS.md` §3.1); the *pilot arms themselves* run on
`luna-azure-eu`, not on the tooling route. This file records the plan; it does not
authorise anything beyond it.

## 1. Questions

Fourteen dev2 IDs, selected by `select_pilot` (stratified largest-remainder over
`QuestionMetadata.stratum`, SHA-256 rank within stratum, seed 42) and dealt into
arms by `assign_arms` (round-robin over `(stratum, rank)`):

| Arm (concurrency) | Questions | Order |
|---|---|---|
| c=1 | 2 | first, to completion |
| c=4 | 4 | second, to completion |
| c=8 | 8 | third, to completion |

Each arm finishes before the next starts; c=4 carries the resume test (§4).
`ARM_SIZES` in `dev2_pilot.py` is authoritative if it and this table disagree.

<!-- PRIMARY FILLS: pilot IDs manifest hash, arm file hashes (arm_c1/arm_c4/arm_c8), code SHA -->

## 2. Pinned ingest

- Route: production extraction route **`luna-azure-eu`**, executed through the
  **`background`** worker path. No route substitution, no fallback model.
- Code: one recorded clean code SHA, recorded above before the first arm starts.
  Every arm and the resume use that exact SHA. `--allow-code-change` is never
  passed.
- Users: each question gets its own benchmark user; no user is shared across
  questions and no arm reuses another arm's user.
- Ingest command shape:
  `python -m orchestrator.eval.longmemeval ingest --dataset <arm_cN.json> --output-dir <dir> --concurrency N`.
- Forbidden flags, always: `--retry-failed-sessions`, `--allow-state-mismatch`,
  `--allow-code-change`. A failure that would need one of them is a stop, not a
  retry.
- The checkpoint is `<output-dir>/longmemeval_checkpoint.json`, pinned to the
  dataset bytes and the code SHA.

## 3. Count-toward condition

Pilot ingests count toward a full dev2 run **only** under an identical code pin
and the identical `luna-azure-eu` route. Any code change, route change or flag
change voids the count-toward claim and the pilot is re-run.

Stated plainly: **the checkpoint pins the dataset bytes, so the current harness
cannot resume a full dev2 run from the pilot's per-arm checkpoints.** Reusing
pilot work in a full run would require separately reviewed work (e.g. a merge of
per-question results that is verified independently of checkpoint resume). Until
such work is reviewed and accepted, the pilot is measurement, not a head start.

## 4. Resume test

In the c=4 arm only:

1. Start the arm normally.
2. Send **one** SIGINT once at least 1 question has completed. The first SIGINT
   finishes in-flight work and exits resumably (`stop_rule` untouched; this is
   `interrupt_after_completed`, not a stop reason).
3. Rerun the **same command** with the same `--output-dir`, dataset and code SHA.
4. Verify the **final state**, not before-versus-after alone, with two checks:
   `verify_resume(before, final, expected_sessions)` and
   `verify_db_counts(conversation-count rows, expected-per-user)`, where the
   expected session count comes from `expected_sessions_from_dataset` (the same
   `corpus_key` de-duplication the harness applies). A harness rebuild can
   legitimately discard the interrupted question's partial session, so a
   before/after drop in that question's sessions is allowed **only** when the
   final count equals the expected count for every question and the database
   conversation count equals the expected session count per benchmark user. A
   duplicate session, a final count that differs from expected, an
   `extraction_failed` count above the pre-resume count, or more conversations
   than expected is a violation. A before/after drop that the final state does
   not explain is a violation too.

A failed resume is a **hard stop** for the whole pilot.

## 5. Measures

Recorded per arm, numbers only (no question, session or answer text):

| Measure | Source |
|---|---|
| Wall time, sessions/hour | driver (`run_arm` wall clock, completed sessions) |
| Tokens/hour | spend ledger token sums divided by wall hours |
| `rate_limited` and other `failure_category` counts | **process log only**: the JSON object after each `routing_event ` marker, when its `event` is `attempt_outcome` (`count_rate_limits`), reported with `routing_events_seen` (`log_lines_seen_routing_events`) |
| `extraction_failed` rate (gate below 1%) | checkpoint per-question records (`session_invariants`, `failed_rate`) |
| Provider-reported cost per session | `entitlement_reservations` via `SPEND_SQL`/`spend_breakdown_reader`: settled `actual_microusd` **plus** the `reserved_microusd` still held by `open` rows, summed over the **entire database** with no time window and no user filter, i.e. cumulative across arms and including calls still in flight (`SpendBreakdown` reports settled and held separately); per-model detail via `summarize_compute_spend` (`orchestrator/eval/bench_db.py:280-312`) |
| CPU, memory, disk growth | sampled during the arm (`sample_resources`), reported as min/max |
| DB size growth | database size before/after each arm |
| Overnight slowdown | wall time and sessions/hour compared across day vs night windows, if any |

**429s are log-derived, never persisted.** No table records them; only the routing
log does: `routing_event {"event":"attempt_outcome","failure_category":"rate_limited"}`
(`orchestrator/routing_log.py`). 429 counts are log-derived and only meaningful if
routing events were seen; zero routing events means the count is **unknown**, and
the report says unknown, not zero. `run_arm` therefore reports
`routing_events_seen` alongside `rate_limits`.

**Spend is settled plus open holds, whole database, cumulative.** A reservation's
full bound is counted while it is still `open`, so the reading never understates
in-flight work; the driver still samples on an interval, so the pre-registered
margin of **USD 1.00** (`cap_usd=6.00`, `margin_usd=1.00`; the arm is interrupted
at USD 5.00) must cover a call that starts and settles between two polls.

Cost is conservative: a failed dispatch settles at the full bound in
`entitlement_reservations`, so `actual_microusd` overstates rather than understates,
and an open reservation's held `reserved_microusd` is already in the reading.

Labelling (separate from ingest, charged to the same cap):

- cost per question and latency per question (provider-reported);
- schema-valid rate;
- `answerable_from_store` rate;
- owner spot-check results on a sampled subset of **at least 2** questions.

## 6. Hard stops

Stop the pilot immediately, do not retry, and report:

1. Spend reaches **USD 6.00**, provider-reported (`entitlement_reservations`:
   settled `actual_microusd` plus open holds, whole database), **including
   labelling**, cumulative across arms. Stop margin is applied before the cap by
   the driver (`StopRule.cap_usd=6.00`, `StopRule.margin_usd=1.00`).
2. `extraction_failed` at or above **1%** in any arm (with at least one failure).
3. An unbounded 429 loop — `rate_limited` above the pre-registered
   `max_rate_limited` for the arm.
4. The existing `daemon_lme_bench` fingerprint changing at any point.
5. Free disk below **3×** the projected pilot growth.
6. A resume that duplicates or loses sessions (§4).
7. A route that lacks ZDR, a data-collection deny, or the required attestation.
8. Any need to touch dev2 retrieval, exposure or answer computation to proceed.
9. A guard failure (`stop_reason = guard_failed`): any spend, log, checkpoint or
   resource reader raising is a hard stop, not a zero. The driver sends one
   SIGINT, waits for the child to exit (escalating once at the grace period) and
   reports the arm as failed with the unread numbers absent. A missing checkpoint
   before the first save is *not* a guard failure: `read_checkpoint_file`
   returns the empty shell the harness leaves.

## 7. Deliverable

A single report containing:

- projected full-dev2 wall time at the best concurrency that meets the gate;
- projected full-dev2 cost;
- labelling cost and time;
- one platform recommendation — laptop with resume, homelab, or a separate VPS.
  **Never production Hetzner.**

The report makes **no decision** on the M2 threshold or on design; those stay with
the owner.

## 8. Prohibitions

- No retrieval, gather, exposure, answer or judge calls on any dev2 question.
- No reading of rankings.
- Labelling reads the extraction tables only, filtered to answer-session sources.
- No restore.
- No write to `daemon_lme_bench`.
- No holdout access beyond the IDs file.
