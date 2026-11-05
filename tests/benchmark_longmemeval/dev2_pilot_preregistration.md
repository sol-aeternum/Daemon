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

**Frozen pins (2026-11-04).** Outside Git, in the task evidence area, because the arm files hold dataset text:

- `pilot_manifest.json` SHA-256: `5228c9ce35005593f6464e02b9d8fb31264580370b3b72e05e2e0b1228e3effe`
- `arm_c1.json`: `007f0edee6094b3f649dbb6c2a09ee5a75c18d799e7ebbf1fdc5637c600f0d3a`
- `arm_c4.json`: `94b5562aa54e5b89a44f01df440bb3d6bf8590a884e6fab6f87a0001a8262381`
- `arm_c8.json`: `0d9a011c8b55dc77205dd24f8a7f5192594a7f77a8e99c3f2729fdb663af8223`
- Tooling at `4500f526b9d5bddde91b1df81fcee4a12ca45ce9`. **The run's code pin is the commit that last changed this file**, which differs from the tooling only in this file.

Volumes: 671 sessions and 7,043 turns (c=1 98, c=4 185, c=8 390 sessions; two sessions are shared across arms). Projected ingest: $5.39 by tokens, $6.07 by sessions, from the Phase D ledger.

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
| `rate_limited` and other `failure_category` counts | **process log only**: the JSON record the routing handler writes per line, when its `event` is `attempt_outcome` (`count_rate_limits`), reported with `routing_events_seen` (`log_lines_seen_routing_events`) |
| `extraction_failed` rate (gate below 1%) | checkpoint per-question records (`session_invariants`, `failed_rate`) |
| Provider-reported cost per session | `entitlement_reservations` via `SPEND_SQL`/`spend_breakdown_reader`: settled `actual_microusd` **plus** the `reserved_microusd` still held by `open` rows, summed over the **entire database** with no time window and no user filter, i.e. cumulative across arms and including calls still in flight (`SpendBreakdown` reports settled and held separately); per-model detail via `summarize_compute_spend` (`orchestrator/eval/bench_db.py:280-312`) |
| CPU, memory, disk growth | sampled during the arm (`sample_resources`), reported as min/max |
| DB size growth | database size before/after each arm |
| Overnight slowdown | wall time and sessions/hour compared across day vs night windows, if any |

**429s are log-derived, never persisted.** No table records them; only the routing
log does, as one JSON object per line, `{"event":"attempt_outcome","failure_category":"rate_limited",...}`
(`orchestrator/routing_log.py`, `orchestrator/safe_logging.py:61-78`). 429 counts are log-derived and only meaningful if
routing events were seen; zero routing events means the count is **unknown**, and
the report says unknown, not zero. `run_arm` therefore reports
`routing_events_seen` alongside `rate_limits`.

**Spend is settled plus open holds, whole database, cumulative.** A reservation's
full bound is counted while it is still `open`, so the reading never understates
in-flight work; the driver still samples on an interval, so the pre-registered
margin of **USD 1.00** (`cap_usd=8.50`, `margin_usd=1.00`; the arm is interrupted
at USD 7.50) must cover a call that starts and settles between two polls.

The margin is sized from Phase D: $19.22 over 17,360 active seconds at concurrency 8–16 is
about $0.0011/s, or roughly $0.02 per 15 s poll, and about $0.009 per session. After a stop
signal each stream finishes its in-flight session, so 8 streams add on the order of $0.10 at
the average. USD 1.00 is about ten times that. Largest-session cost is not separately
established; the record reports actual spend after the stop.

`--output-dir` must be outside the repository, and `run-arm` refuses otherwise: run output
would dirty the tree and block the resume's clean-tree check.

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

1. Spend reaches **USD 8.50** (owner-raised from 6.00 on 2026-11-04, before any spend, because ingest alone projects to $5.39–6.07), provider-reported (`entitlement_reservations`:
   settled `actual_microusd` plus open holds, whole database), **including
   labelling**, cumulative across arms. Stop margin is applied before the cap by
   the driver (`StopRule.cap_usd=8.50`, `StopRule.margin_usd=1.00`).
2. `extraction_failed` at or above **1%** in any arm (with at least one failure).
3. An unbounded 429 loop — `rate_limited` above `max_rate_limited` = **100** for the
   arm: about 1% of the roughly 11,700 calls this volume implies at Phase D's 17.4 calls
   per session. Phase D saw none; isolated retried 429s are not a stop.
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

## Amendment, 2026-11-05 (owner decision, after the c=8 gate tripped)

**What happened.** c=1 and c=4 completed at code pin `626819d1`: 283 sessions, no failures, resume
test passed. c=8 stopped at 161 of 392 sessions after 4 failed attempts on one session,
`sharegpt_xxk9JDz_21`: 1.02% against a 392-session denominator, counted cumulatively across
invocations, a rule fixed before the first resume. That met the 1% hard stop. Each attempt was a pair
of calls stopping at exactly 2,000 output tokens (#670).

**Owner decisions.** Investigate rather than excuse the failure; then raise `EXTRACTION_MAX_TOKENS` to 4000
and detect truncation (#671).

**Changes from here.**
1. c=8 restarts from scratch at a new code pin: the commit carrying this amendment, which adds #671 and a
   `run-arm` correction to the tooling. A failed child now records `stop_reason=child_failed`; it previously
   read as success.
2. Before the restart: `pg_dump` the database to the evidence area, then delete only the eight c=8 question
   users' content with the per-user statements of `CANONICAL_RESET_STATEMENTS`. The harness's own reset is not
   used, because it clears every `longmemeval+` user and would destroy c=1 and c=4. `entitlement_reservations`
   is untouched, so the spend guard keeps all prior spend. The aborted run's records stay in `run/c8/`.
3. The restart uses a new output directory, `run/c8r/`. The USD 8.50 cap stays cumulative and includes the aborted
   attempts. The 1% gate stays cumulative across invocations of the restarted arm: 4 failures on its 392 sessions.
4. **Comparability, stated plainly:** c=1 and c=4 ran at the 2,000 cap and c=8 at 4,000. Throughput and cost are
   comparable because few replies approach either cap. The extraction cap differs from Phase D and dev-50.
5. **Reported in addition:** count of `extraction_truncated` diagnostics and of calls above 2,000 output tokens. If
   `finish_reason` is never `length` when a reply reaches a cap, say so: it would leave the #670 diagnosis unconfirmed.
