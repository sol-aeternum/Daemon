# Reasoning routing evaluation protocol (Package B, stage B1)

Date: 2 October 2026. Status: **B1 frozen protocol and corpus v1. B2 (paid
comparison) is blocked on an approved USD cap.** Parent:
[expansion plan](REASONING_ROUTING_EXPANSION_PLAN.md).

The product owner authorized Package B commencement on 2 October 2026. This document
records the revalidation the plan requires, then freezes the evaluation protocol and
corpus. It authorizes no inference: every paid run needs its own cap.

## Revalidation against merged `main` (2 October 2026)

| Check | Result |
| --- | --- |
| Baseline | `main` at `9fca6139` (A-core #384–#390, optional work #391–#394 merged; #396 and the O3 PR in review) |
| Routing code re-read | `orchestrator/model_router.py`, `orchestrator/compute_runtime.py`, `orchestrator/main.py`, `orchestrator/tools/completion.py` |
| Classification fixture | `tests/test_routing_classification_fixture.py` passes |
| Chart check | `tests/test_chat_routing_doc.py` passes |
| D2 / D4 | Implemented as approved (#386 / #384) |
| Route approvals | All deployment routes expire **2026-10-06T00:00:00Z**; any live B2 run after that needs renewed approvals |
| #342 | O2 (#394) and O3 resolve both halves once merged and deployed |
| #343 | Open. Limits only configurations that continue signed reasoning across tool rounds; B2 records emission and drop |
| Telemetry deployed | **No.** Deployment is pending, so B5/B6 (shadow, rollout) remain blocked; B1–B3 are offline |

## What is being measured

The decision B2/B3 informs: for each kind of chat work, which configuration (model +
provider effort) is the *cheapest that is reliably good enough*, and whether any
routing policy beats the current one by enough to justify its complexity. Role,
estimated demand, configuration, authority and action risk stay separate concepts
(expansion plan, "Concepts kept separate").

### Slices

Each case belongs to exactly one slice:

| Slice | What it probes |
| --- | --- |
| `everyday` | Ordinary questions, writing and lookups |
| `coding` | Code reading, debugging and small changes, with and without analytic keywords |
| `planning` | Decisions and plans with interacting constraints |
| `synthesis` | Combining provided evidence, including conflicts and qualifiers |
| `followup` | Short continuations ("why?", "and for Postgres?") whose difficulty lives in history |
| `topic_shift` | A new easy request after a hard turn, and vice versa |
| `quoted_injection` | Quoted or pasted text containing routing keywords or instructions |
| `revealed_by_tools` | Difficulty that appears only after a tool result (conflict, failure) |
| `missing_info` | Requests that should prompt retrieval or a clarifying question, not more reasoning |
| `non_english` | The same kinds of request in other languages |

### Strata

Each case also has one stratum: `easy`, `hard`, `deceptively_hard` (short or
plainly worded but needs careful reasoning), or `long_easy` (long input, simple
task). Strata let B3 check that a policy neither over-spends on long easy work nor
misses short hard work.

### Configurations (B2)

Luna `low` (current default), Luna `medium`, Luna `high`, and the stronger
configurations accepted at the time of the run (as of 2 October 2026: Sonnet 5 `high`,
Sol 6.1 `high`). Explicit `reasoning_effort` per configuration; presets disabled in an
isolated catalog, as in the follow-up screen.

## Corpus v1

`tests/fixtures/reasoning_eval/corpus_v1.json`, frozen by SHA-256 in
`tests/fixtures/reasoning_eval/MANIFEST.json` and checked by
`tests/test_reasoning_eval_corpus.py`. Any edit fails that test; a change is a new
version, never an in-place edit.

- **Size:** 60 cases, 6 per slice: 2 `dev`, 2 `val`, 2 `test`. This is a **pilot**
  corpus: enough to calibrate variance and rubric agreement in B2, not to certify a
  policy. B2's power analysis decides whether more cases are needed.
- **Content:** synthetic only. No user data, no real people, no private repository
  content.
- **Independent review (before freeze):** a separate reviewer re-derived every
  expected answer (arithmetic, dates and daylight saving, code and SQL semantics,
  facts) and found no blocking problems. Its 13 minor findings (over-strict or
  two-reading rubric items, stratum labels, and a held-out case reusing a validation
  input) were applied before the hash was taken; they are recorded case by case in
  `apply_review_revisions` in the corpus builder.
- **Case fields:** `case_id`, `split`, `slice`, `stratum`, `latency_class`,
  optional `history` (prior user/assistant turns), `prompt`, optional `tools` and
  `tool_responses` (argument-sensitive, as in earlier fixtures), and `rubric`:
  - `acceptable`: assertions an acceptable answer must satisfy;
  - `hard_violations`: any one makes the answer unacceptable regardless of the rest;
  - `notes`: what makes the case hard or easy, for reviewers only.
- **Held-out discipline:** `test` cases are frozen now and are not used to tune
  anything. Routers, prompts and thresholds are tuned on `dev`; policies are chosen
  on `val`; `test` is scored once, with fresh runs, for the chosen policy and the
  baselines.

## Scoring

- Each attempt is judged against its case's rubric by a human reviewer, blind to the
  configuration where feasible (randomized opaque labels, retained mapping).
- An attempt is **acceptable** when every `acceptable` assertion holds and no
  `hard_violation` occurs. Minor misses are recorded but do not change the verdict
  unless the rubric says so.
- A materially ambiguous case is marked **undetermined** for all configurations
  symmetrically; it is never retroactively passed.
- Record per attempt: verdict, model calls, tool calls, input/output/reasoning tokens
  where reported, wall time, first-token time, settled cost (ledger), and whether
  signed reasoning was emitted and dropped across tool rounds (#343).

## Statistics

As fixed in the expansion plan:

- No per-case certification: at k = 3 a 3/3 result has a one-sided 95% lower bound
  of about 0.37. Per-case labels are Beta-Binomial summaries pooled within a slice, in
  three classes: default sufficient, needs more, undetermined.
- Decisions are made on slices and policies with cluster-bootstrap intervals
  (resample cases, keep repeats together) and paired comparisons on the same cases.
- Design effect `1 + (k−1)·ICC`; prefer more cases over more repeats. The pilot
  estimates ICC and the disagreement rate between configurations.
- Adaptive sampling (extra repeats on discordant cases, extra cases where a slice
  interval straddles its threshold) is used only on `dev`/`val`, by a rule declared
  before B2 starts.

## B2 pilot plan and spend formula

Pilot: `dev` + `val` (40 cases) × 5 configurations × k = 3 repeats = **600 attempts**.

Spend estimate (not a cap):

    Σ_configs Σ_cases k × Σ_calls [P_in·T_in + P_out·(T_out + T_reason)] + f_unknown × hold

with P the route ceiling prices (upper bounds on actual prices). Before the pilot, a
**calibration run** of 2 cases per slice × 5 configurations × 1 repeat (100 attempts)
measures T_in, T_out, T_reason, calls per attempt and f_unknown, so the pilot cap can
be set from measured numbers. Admission also needs cap headroom of at least
concurrency × the largest per-call hold.

Worst-case reservation bounds from `scripts/reasoning_eval_plan.py` (dry run, no
provider calls; every call at the full 4,096-token output limit and the maximum calls
per attempt; 2 October 2026 price ceilings):

| Run | Luna low/medium/high (each) | Sonnet 5 high | Sol 6.1 high | Total |
| --- | --- | --- | --- | --- |
| Calibration (20 dev cases × 1) | $0.13 | $1.60 | $2.66 | **$4.65** |
| Pilot (40 dev+val cases × 3) | $0.80 | $9.61 | $15.97 | **$27.98** |

Actual spend is expected to be well below these bounds; the calibration run measures it.

**Approved 2 October 2026:** the calibration cap is its worst case, USD 4.65
(manifest `b2_calibration_20261002.json`). The pilot runs, under its own manifest,
only if the calibration's measured costs project it within its USD 27.98 worst case.
Route approvals must still be valid at run time.

## Calibration result (2 October 2026)

Run `b2-calibration-20261002-r2` (100 attempts, merged `main` `55379e76`): all
attempts recorded, no stops. The ledger moved by exactly the recorded USD 0.138,
with no open holds. Every call reported usage, so the formula's `f_unknown` was 0,
with no full-hold settlements and no unknown charges.

| Configuration | Calls | Mean prompt / completion / reasoning tokens per call | Charge (USD) | Median / p95 latency (s) |
| --- | --- | --- | --- | --- |
| Luna low | 22 | 64 / 79 / 35 | 0.0017 | 3.0 / 5.1 |
| Luna medium | 22 | 64 / 99 / 53 | 0.0021 | 3.2 / 4.9 |
| Luna high | 22 | 64 / 131 / 87 | 0.0027 | 2.9 / 7.0 |
| Sonnet 5 high | 23 | 196 / 275 / 37 | 0.0795 | 3.2 / 11.1 |
| Sol 6.1 high | 23 | 69 / 120 / 65 | 0.0525 | 5.6 / 11.5 |

- **Pilot projection:** USD 0.83 at the mean and USD 3.43 at the per-configuration
  maximum charge per attempt, against the USD 27.98 worst case. The pilot condition
  is met. Its manifest `b2_pilot_20261002.json` caps it at its own planning bound,
  USD 21.40.
- **Content filter:** `QIN-2` (quoted injection, hard) ended with
  `finish_reason=content_filter` on every Azure-served configuration (Luna low,
  medium, high and Sol 6.1). It is a recorded failure in the denominator; Sonnet 5
  on Vertex answered it.
- **#343:** no response carried provider reasoning metadata (`reasoning_details`)
  on these single non-streaming calls, including tool rounds.
- Semantic verdicts are pending human review of the blinded packet.

## Stop conditions

- Rubric ambiguity found in review: revise the corpus (new version) before any calls.
- Calibration shows costs that make the pilot unaffordable within the approved cap:
  stop and report.
- Pilot shows no configuration beats Luna `low` beyond noise on any slice, and the
  sample needed to detect the declared effect is unaffordable: stop Package B, keep
  the current policy, and consider explicit user controls alone.

## B2 runner

`scripts/reasoning_eval.py` runs one manifest under
`tests/fixtures/reasoning_eval/` (calibration: `b2_calibration_20261002.json`). It
loads this corpus strictly (digest-checked, multi-turn `history`, per-case call
limits: 3 calls and 4 tool calls for tool cases, 1 call otherwise) and reuses the
shared executor in `scripts/model_routing_followup.py` for pinned dispatch through
`guarded_completion`, pre-dispatch journaling, per-call settlement, cap admission,
ledger exclusivity and no replay. Each configuration sends its effort explicitly
through an isolated catalog with empty presets.

- `--dry-run` checks every scheduled request against the isolated account and
  policies and refuses a whole-run planning bound above the manifest cap. The
  calibration's planning bound is USD 3.56 (no system prompt is sent, unlike the
  planner's allowance).
- A completed run writes `results.json`, `summary.json`, `human-review.md` and
  `human-verdicts.json` (blinded: random labels, no model, effort, cost or latency)
  and a separate `review-map.json`. `--report` re-renders them offline, including
  for a partial run.
- `summary.json` reports, per planned configuration, planned, recorded, terminal,
  stopped and missing attempts, tokens, calls, charges and latency. It keeps three
  accounting rates apart:
  - `f_unknown_usage`, calls without provider usage; this is the formula's
    `f_unknown`, and such calls settle at the full hold;
  - `f_full_hold`, calls charged their whole hold;
  - `f_unknown_charge`, calls with no recorded ledger charge.
- The pilot projection is withheld, with reasons, unless every planned calibration
  attempt is recorded and terminal and every charge is known.
- Every run also refuses an evaluation database that has not applied every
  repository migration. The first calibration run (`b2-calibration-20261002`,
  2 October 2026) stopped at its first call because the isolated database lacked
  migrations 036 and 040–042: the reservation insert failed and the runtime
  refused the call as `account_unavailable`. No provider call was dispatched and
  the ledger did not move. The database was migrated, and the calibration re-runs
  as `b2-calibration-20261002-r2` under the same approved cap. The stopped run is
  never resumed or replayed.
- Limits: single non-streaming calls with no system prompt or memory, so this
  measures the models on the cases, not the production chat path; first-token time
  is not recorded. Answers can still reveal their model by style.
