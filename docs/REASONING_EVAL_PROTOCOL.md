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

**Decision needed before B2:** a USD cap for the calibration run (and, after it, for
the pilot). Nothing in B2 runs until a cap is approved and route approvals are valid
at run time.

## Stop conditions

- Rubric ambiguity found in review: revise the corpus (new version) before any calls.
- Calibration shows costs that make the pilot unaffordable within the approved cap:
  stop and report.
- Pilot shows no configuration beats Luna `low` beyond noise on any slice, and the
  sample needed to detect the declared effect is unaffordable: stop Package B, keep
  the current policy, and consider explicit user controls alone.

## What B2 needs to build

The existing runners (`scripts/model_routing_followup.py`, `scripts/model_upgrade.py`)
validate the earlier frozen experiments only (fixed case ids and call counts). B2
needs a generalised loader for this corpus schema (multi-turn `history`, per-case
call limits, `split`/`slice`/`stratum`), reusing the existing isolated-catalog,
accounting and journaling path through `guarded_completion`. That is implementation
work inside B2, after the cap is approved.
