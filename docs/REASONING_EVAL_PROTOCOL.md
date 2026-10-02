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

## Pilot result (2 October 2026)

Run `b2-pilot-20261002` (600 attempts, merged `main` `22080085`, cap USD 21.40):
all attempts recorded, no stops. The ledger moved by exactly the recorded
USD 0.881, with no open holds. Every call reported usage; there were no full-hold
settlements and no unknown charges. Total B2 spend, including the calibration, is
USD 1.019.

| Configuration | Calls | Mean completion / reasoning tokens per call | Charge (USD) | Mean per attempt (USD) | Median / p95 latency (s) |
| --- | --- | --- | --- | --- | --- |
| Luna low | 135 | 73 / 29 | 0.0104 | 0.00009 | 2.8 / 4.7 |
| Luna medium | 138 | 113 / 68 | 0.0152 | 0.00013 | 3.1 / 6.4 |
| Luna high | 136 | 142 / 93 | 0.0182 | 0.00015 | 3.1 / 6.8 |
| Sonnet 5 high | 138 | 296 / 57 | 0.5099 | 0.00425 | 3.8 / 15.9 |
| Sol 6.1 high | 138 | 123 / 66 | 0.3268 | 0.00272 | 4.7 / 9.5 |

- The only failures are `QIN-2`'s Azure content-filter refusals: all 3 repeats on
  Luna low, medium and high and on Sol 6.1. Sonnet 5 answered every attempt.
- **Pending:** human verdicts on the blinded packets (calibration 100 answers, pilot
  600) in the private run directories. ICC, rubric agreement, per-slice frontiers and
  the B3 sample size need those verdicts; nothing here measures answer quality.

## B3 pre-registration (2 October 2026, before any test-split run)

Operator decisions on 2 October 2026:
- **Success rule:** the chosen policy passes if both hold.
  - On `hard` and `deceptively_hard` test cases, its acceptance rate is at most
    **5 percentage points** below the always-deliberate baseline (always Sol 6.1 high).
  - Its cost per acceptable answer is at most **25%** of that baseline's.
- **Verdicts:** a panel of three blind AI reviewers decides by majority. The operator
  adjudicates every disagreement, plus the hard `planning`, `revealed_by_tools` and
  `synthesis` cases; an adjudication replaces the majority.
- **Test cap:** USD 10.50.

**Policies, simplest first:**
- P0 current: Luna low for routine work, reasoning in the current order (Sonnet 5 first).
- P1 uniform Luna medium default, reasoning unchanged.
- P2 cost-ordered reasoning: Sol 6.1 first.
- P1 + P2.
- References: always Sonnet 5 high, always Luna medium.

**Simulation:** each case is classified with the current
`orchestrator.model_router.select_model_tier`, using its prompt and a turn count of
history + 1. Routine and research cases take the policy's routine configuration;
reasoning cases take its first reasoning configuration. No fallback is modelled: a
content-filter refusal is a completed, unacceptable response under every policy.
The classifier routes one case per split to reasoning (COD-2, PLN-4 and one test
case).

**Validation scores** (pilot, one blind AI reviewer; the panel is pending):

| Policy | Dev | Validation | Validation cost per success (USD) |
| --- | --- | --- | --- |
| P0 | 50/60 | 58/60 | 0.00042 |
| P1 | 53/60 | 60/60 | 0.00043 |
| P2 | 49/60 | 58/60 | 0.00035 |
| P1 + P2 | 52/60 | 60/60 | 0.00037 |
| Always Sonnet 5 high | 60/60 | 58/60 | 0.00457 |
| Always Sol 6.1 high | 56/60 | 60/60 | 0.00275 |
| Always Luna medium | 53/60 | 60/60 | 0.00013 |

**Selection:** P1, the simplest policy at the best validation score. It needs a
preset change only, no code. It is re-selected only if the three-reviewer panel
changes the validation ranking, and any re-selection is recorded here before test
verdicts are unblinded.

**Test confirmation:** manifest `b3_test_20261002.json` runs 20 untouched test cases
× Luna low, Luna medium, Sonnet 5 high and Sol 6.1 high × 3 repeats: 240 attempts,
planning bound USD 10.37.

**Reporting:** P1 is checked against the success rule. Missed escalations are
routine-routed cases where P1 fails and always-Sol passes. Unnecessary escalations
are reasoning-routed cases the routine configuration also passes. Both are reported
separately. If P1 fails the rule, the current policy stays.

**Observation recorded before the test:** on dev and validation, the cases where a
premium model helps (PLN-2, RBT-1, SYN-2) are routed to routine, and the cases the
classifier escalates are ones Luna medium already passes.

## B3 result (3 October 2026)

**Pre-test re-selection check** (recorded 2026-10-02T14:29Z, before any test
verdict existed):
- The pilot panel was three blind AI reviewers: two Opus sessions and one Sonnet
  session. Pairwise Cohen's κ was 0.81–0.88.
- A Haiku reviewer was excluded for heuristic judging. Its rationales were
  boilerplate and its κ against the others was 0.29–0.31.
- Majority verdicts left the validation ranking unchanged, so P1 stays selected.

**Test run** (`b3-test-20261002`, merged `main` `baf13275`):
- 240 attempts, all completed, no stops.
- USD 0.425, matching the ledger exactly; every call reported usage.
- No content-filter refusals on the test split.
- The test panel, with the same reviewer composition, was unanimous except on
  three NEN-6 answers listing *maçã*, accepted 2–1.

| Policy | All (60) | Hard + deceptively hard (36) | Cost per success (USD) |
| --- | --- | --- | --- |
| P0 current | 60 | 36 | 0.00062 |
| **P1 Luna medium** | **60** | **36** | **0.00064** |
| P2 Sol first | 60 | 36 | 0.00038 |
| P1 + P2 | 60 | 36 | 0.00040 |
| Always Sonnet 5 high | 60 | 36 | 0.00409 |
| Always Sol 6.1 high | 60 | 36 | 0.00280 |

**Rule outcome: P1 passes.**
- It is 0 points below always-Sol, against 5 allowed.
- Its cost per success is 22.8% of always-Sol's, against 25% allowed.
- Most of P1's cost comes from the one escalated test case (COD-5), which runs on
  Sonnet 5.

**Limits of this result:**
- Every policy scored 60/60 on the test split, including the current policy. The
  test therefore confirms that P1 is non-inferior, but it cannot separate P1 from
  P0.
- The evidence for P1 over P0 rests on dev (53 vs 50 of 60) and validation (60 vs 58
  of 60). That gain comes mainly from two tool cases (RBT-1, RBT-3) and one planning
  case (PLN-2), and is not significant on its own.
- Corpus v1's test split is too easy to discriminate between policies. A harder,
  larger corpus (v2) is needed before claims beyond non-inferiority.

**Escalation accounting:**
- Unnecessary escalations: COD-2, PLN-4 and COD-5, every case the classifier
  escalated across dev, validation and test. The routine configuration also passed
  each one.
- Missed escalations: none on test. On dev and validation, premium helped on PLN-2,
  RBT-1, RBT-3 and SYN-2, all routed to routine.

**Spend:** USD 0.425 for B3; Package B total USD 1.444.

**Pending:**
- Operator adjudication: the 15 pilot disagreements (PLN-2, EVD-3, PLN-1, COD-2),
  the 3 NEN-6 test answers, and the hard planning, revealed-by-tools and synthesis
  cases.
- B4 needs separate approval of the preset change: Luna medium as the routine,
  background and research default.

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
