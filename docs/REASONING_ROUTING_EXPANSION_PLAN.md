# Reasoning routing expansion plan (Package B)

Date: 1 October 2026. Status: **blocked pending explicit authorization** (A-core gate met once the repair plan's last PR merges).
Prerequisite: [repair plan (Package A)](REASONING_ROUTING_REPAIR_PLAN.md).

This is a conditional, evidence-led roadmap. It decides no demand bands, no new
roles and no escalation mechanism in advance. Every stage needs its own explicit
authorization and, where it spends money, an approved budget.

## Gate

Package B may start only when **both** hold:

1. A-core (A-PR1 to A-PR5) is merged to `main`. Optional work O1 to O7 does not gate
   Package B.
2. The product owner explicitly authorizes the specific Package B stage.

Before any authorized Package B work, rebase onto the actual merged `main` and
complete the [revalidation checklist](#revalidation-checklist). Do not apply this
plan from the assumptions recorded on 1 October 2026.

## Evidence it starts from

- Luna at low effort is the only automatic candidate for routine, background and
  research. That rests on one screen: 8 held-out cases × 2 repeats, 16/16 acceptable
  for Luna-low, Sol-high and Sonnet-high, with no premium advantage *in that
  envelope* (`docs/MODEL_ROUTING.md`, `docs/MODEL_ROUTING_FOLLOWUP_PLAN.md`). It does
  not show that stronger reasoning has no value elsewhere.
- The reasoning profile's placements are unmeasured (`config/model_routing.json`
  notes). The Sol 6.1 evaluation does not certify demanding reasoning or coding
  (`docs/SOL_UPGRADE_EVALUATION.md`).
- Current escalation is availability-only: later groups are reached only when
  earlier candidates are ineligible or fail before output.

## Concepts kept separate

Every record, configuration and decision keeps these distinct:

| Concept | Meaning |
| --- | --- |
| Role / workload | What kind of work (routine, research, …); evaluation slices |
| Estimated reasoning demand | A latent estimate; no bands chosen in advance |
| Configuration | Model + provider effort + tool budget |
| Authority ceiling | Which route classes the turn may use |
| Action risk | Governed by approvals for consequential actions, never by model choice |

Routine and research stay independently remappable. Every stage keeps a
role-remapping regression test: point one profile at another model in a temporary
catalog and assert the other profiles and presets do not move.

## Statistical method

Fix these before setting any threshold:

- **No per-case certification.** At k = 3 repeats, 3/3 successes has a one-sided 95%
  Clopper-Pearson lower bound of about 0.37; certifying 0.9 with no failures needs
  about 29 runs. Per-case labels are Beta-Binomial posterior summaries, partially
  pooled within a slice, in three classes: default sufficient, needs more,
  undetermined. Three to five repeats is a pilot.
- **Decide on slices and policies, not cases.** Estimate success per slice with
  cluster-bootstrap intervals (resample cases, keep repeats together). Compare
  configurations with paired tests on the same cases.
- **Undetermined cases** never tune anything; at evaluation they are scored both ways
  as a sensitivity check.
- **Sampling.** Design effect = 1 + (k−1)·ICC (how strongly repeats of one case
  agree). Cases needed ≈ n_eff·(1 + (k−1)·ICC)/k, where n_eff ≈ 1.96²·p(1−p)/h² for
  target reliability p and interval half-width h, both declared per slice before
  running. Prefer more cases over more repeats. Add repeats only where configurations
  disagree; add cases where a slice interval straddles its threshold. The adaptive
  rule is declared in advance and used only on development and validation sets.
- **Splits.** Development (tune rules, prompts, thresholds), validation (choose a
  policy), held-out test (frozen and hashed before any router work, scored once).
  The chosen policy and baselines are scored on held-out with **fresh runs**, so the
  same outcomes never both select and score the router. Any redesign gets a new
  held-out version.
- **Spend formula** (an estimate, not a cap):
  `Σ_configs Σ_cases k × Σ_calls [P_in·T_in + P_out·(T_out + T_reason)] + f_unknown × hold`.
  P are route ceiling prices (upper bounds on actual prices). The `f_unknown × hold`
  term exists because unknown-usage attempts settle at the full hold under current
  rules. Admission also needs cap headroom ≥ concurrency × max per-call hold
  (`P_in·B_in + P_out·max_tokens`). T values, calls per attempt and f_unknown come
  from a small calibration run; treat every cost and latency figure as provisional
  until measured.

## Stages

| Stage | Inputs → outputs | Specific dependencies | Approval / budget | Success | Stop / rollback |
| --- | --- | --- | --- | --- | --- |
| **B1 Freeze protocol and corpus** | Concepts and slices → development, validation and test corpora with rubrics and hashes | **Merged** A-PR1/A-PR2, only so baseline routing labels come from the repaired classifier | Authorization; synthetic or consented content only | Independent reviewer confirms rubric/prompt alignment and fixture behaviour | Rubric ambiguity → revise before any calls |
| **B2 Calibration and pilot paired comparison** | Development and validation sets → per-slice success/cost frontier, ICC, token and latency distributions, rubric agreement | Runner seam on `main` (`completion_dispatch`, #370); approved routes valid **at run time**; isolated catalog. A-core telemetry not required (the runner records what it sends). #343 limits only configurations that continue signed reasoning across tool rounds; record emission and drop. Fresh single calls at different efforts are not blocked | Approved cap from the formula | Measurement quality: stable variance, acceptable rubric agreement | No configuration beats the default beyond noise on any slice and the required sample is unaffordable → stop; keep current policy; consider explicit user control alone |
| **B3 Select a policy** | Frontier → candidate policies scored on validation; chosen policy confirmed on test with fresh runs | **Merged** A-core (repaired classifier is the current-policy baseline) | Budget for the main run | Non-inferior to the always-deliberate baseline within a pre-set δ on critical slices, at a pre-set fraction of its cost per success; missed and unnecessary escalations reported separately | No policy beats the current one → keep it |
| **B4 Implement only what B3 justified** | Chosen capability → code behind a kill switch | See [capability dependencies](#capability-dependencies) | Separate approval per capability; schema, SSE or API changes flagged | Tests per capability | — |
| **B5 Log-only shadow** | Policy decisions logged beside actual routing | **Deployed** A-PR4 telemetry and repaired classifier. O1 to O3 not required. A semantic-triage shadow makes real calls, so it needs a budget and a decision on whether shadow calls charge users or an operator account | Shadow cost, if any | Agreement and predicted cost close to B3 | Distribution shift → return to B1 or B3 |
| **B6 Bounded rollout** | Opt-in or percentage rollout | Capability merged and deployed, plus its dependencies | Rollout approval | Pre-set thresholds | **Roll back** on rising refusal rate, cost per success or regenerate rate; audit misses beyond tolerance; any canary injection that escalates; any overage suspension |

Configurations to compare in B2/B3: Luna-low (current default), Luna-medium,
Luna-high, and the stronger configurations accepted at revalidation (as of 1 October
2026: Sonnet 5 high, Sol 6.1 high). Policies to compare in B3, simplest first:
current policy; a uniform default change; explicit user "think harder" control;
rules v2; escalation after a tool round on task-state evidence; gated semantic
triage; combinations. Choose the simplest within the declared tolerance of the best.

## Capability dependencies

| Capability | Needs | Limitations on current holds |
| --- | --- | --- |
| Uniform default change (e.g. Luna-medium) | Preset change approval | Effort does not change the hold. On Free's 4096-token cap, reasoning tokens can truncate answers; measure in B2 |
| Explicit "think harder" control | API, SSE and UI contract approval | Same-model effort stays on routine routes and needs no new authority; a stronger model is today's explicit pick |
| Rules v2 | Classifier change | As today |
| Escalation after a tool round | Authority/profile decision if premium; #343 for same-model continuation of signed reasoning; revision semantics | Near-exhausted Pro: an escalation the hold cannot cover falls back to the un-escalated path, never refuses the turn. Unknown-usage full-hold charges grow on premium. Concurrent holds add up. O2/O3 would reduce these but are not required |
| Gated semantic triage | Budget, latency target, a reservation per triage call | Each triage call needs explicit `max_tokens`; noticeable against Free's budget |

## Transition rules (only if B3 justifies a transition)

- Never switch inside a streaming completion; a failure after output stays
  unreplayed (`orchestrator/compute_runtime.py`, streaming fallback stop).
- Re-route only before a **new** completion, after a finished tool round with every
  tool result recorded. An incomplete streamed completion is not a transition point.
- At most one escalation per turn, only upward; de-escalate only at turn boundaries.
- Never re-run side effects. A model change never widens approvals.
- Never silently rewrite an answer already shown. A stronger pass produces a new,
  marked revision linked to the original, which stays visible, and says what changed.

## Authority and profile (explicit decision before any premium escalation)

Today the outer profile sets the premium ceiling for the whole turn. Options:

1. Decide the ceiling at ingress from plan and consent, separately from the starting
   workload.
2. Keep the coupling; allow only same-model effort changes or next-turn changes.
3. Confirm each escalation with the user.

Under every option: one account scope per turn, a reservation before every attempt,
unchanged spending ceilings and cancellation, and separate approvals for
consequential actions.

## External instructions versus evidence of difficulty

- The router may read the user's text with quotes stripped, approved continuity
  state, and typed events from trusted code (deterministic validation failures, tool
  error codes, structured conflict detection). It never reads tool or retrieved
  content.
- An LLM conflict detector reads untrusted content and is exposed to injection. Its
  output may only trigger capped, budget-bounded escalation, never authority.
- Tests: zero unwanted escalations on the *frozen* injection corpus, and escalation on
  structurally confirmed conflicts. These are test criteria, not a universal
  guarantee; budget caps limit harm but do not prove resistance.
- Missing information triggers retrieval or a clarifying question before more
  reasoning.

## When more reasoning is unavailable

- **Limited answer, disclosed:** only for low-stakes, reversible, informational
  requests.
- **Clarify:** when the difficulty comes from ambiguity.
- **Defer under DEC09:** for consequential or long work.
- **Explicit model requests:** honoured exactly or refused truthfully.

Disclosure alone does not make a degraded answer acceptable.

## Roles

Coding/debugging, planning/decisions and evidence synthesis start as evaluation
slices. A slice becomes an independently routed category only with all three: a
measured benefit larger than the cost of misrouting it; acceptable detection at
ingress; and a need for a distinct tool, permission or output contract. Deep research
is treated as research with higher demand and durable execution, not a fixed model
category.

## Continuity

Short follow-ups ("why?") inheriting the previous turn's routing is new behaviour,
not a repair: no current document promises it. Evaluate it in B2/B3 (follow-up and
topic-shift slices). Any persistent routing state needs its own approval.

## Revalidation checklist

Before any authorized Package B stage:

- [ ] Rebase onto merged `main`; record the SHA.
- [ ] Re-read `orchestrator/model_router.py`, `orchestrator/compute_runtime.py`,
      `orchestrator/main.py` and `orchestrator/tools/completion.py`.
- [ ] Re-run the A-PR1 classification fixture and the chart check.
- [ ] Confirm D2 and D4 were implemented as approved.
- [ ] Check route approvals are current (deployment approvals expired 6 October
      2026 unless renewed), model IDs, prices and commercial numbers.
- [ ] Check the status of #342 and #343.
- [ ] For B5/B6: confirm `routing_event` records are emitted in the deployed
      environment.

## Status

| Stage | State | Notes |
| --- | --- | --- |
| B1 | Done (2 October 2026) | Commencement authorized 2 October 2026; revalidated against `main` `9fca6139`; protocol and corpus v1 frozen in [REASONING_EVAL_PROTOCOL.md](REASONING_EVAL_PROTOCOL.md) |
| B2 | In progress | Budget approved 2 October 2026: calibration at its USD 4.65 worst case; pilot (worst case USD 27.98) only if calibration-measured costs project it within that. Runner `scripts/reasoning_eval.py`; A-core deployed 2 October 2026. Calibration (USD 0.138) and pilot (600 attempts, USD 0.881) run 2 October 2026, see the protocol. Waiting on human verdicts for the blinded review packets |
| B3 | Done (3 October 2026) | P1 (Luna medium default) passed the pre-registered rule on the test split (0 points below always-Sol, 22.8% of its cost per success); the test split could not separate P1 from the current policy. See the protocol's B3 result. Operator adjudication pending |
| B4 | Done (3 October 2026) | Operator approved the preset change; Luna's default effort is `medium` for routine, background and research (group renamed `luna`); rollback is reverting the preset |
| B5 | Blocked | Also needs A-core deployed |
| B6 | Blocked | |
