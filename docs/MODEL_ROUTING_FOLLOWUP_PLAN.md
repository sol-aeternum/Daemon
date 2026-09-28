# Routing follow-up: corrected fixtures and intended presets

Date: 28 September 2026. Status: **user-approved design, settings, acceptance
criteria and USD 20 sub-cap, including the isolated-catalog parameter correction;
all 120 attempts completed; all 48 held-out answers adjudicated by Human**.
Parent: [routing proposal](MODEL_ROUTING_PILOT_PROPOSAL.md).

## Decision to inform

Determine whether Luna remains acceptable for bounded routine/tool/synthesis work,
and whether Sol or Sonnet offers a repeatable benefit on more demanding evidence
chains. This is a focused routing screen, not general coding or flagship ranking.
Preserve AC07/AC09/AC12, DEC09 and the existing commercial/provider contract.

The baseline and scoped human adjudications remain in
[MODEL_ROSTER_PILOT_RESULTS.md](MODEL_ROSTER_PILOT_RESULTS.md). Its frozen fixture
and original/extension states must not be modified or replayed. The new experiment
has new case IDs, corpus version, state identity and result format discriminator.

## Candidates and parameter conditions

| Candidate | Proposed exact model / existing evaluation pin | Explicit-effort condition |
| --- | --- | --- |
| Luna | `openai/gpt-6-luna` / `azure/eu` | `low` |
| Sol | `openai/gpt-6-sol` / `azure/eu` | `high` |
| Sonnet | `anthropic/claude-sonnet-5` / `google-vertex/europe` | `high` |

The diagnostic set pairs **provider-default effort** with the explicit-effort
condition above. A pre-execution integration check found that field omission in
the original runner was not sufficient: `guarded_completion` applies catalog
presets even for explicit model pins. The ordinary routine catalog injects
low/high/high for these candidates. Earlier descriptions of the baseline method
as provider-default were inaccurate; original state did not record the full
effective outbound parameter set or catalog digest, so do not claim independent
wire-level verification from its omitted request field.

Proposed correction: use an isolated evaluation-only routing catalog with these
three models' parameter presets empty, retaining supported-effort declarations.
Default requests then omit effort through the actual transport; explicit requests
send low/high/high. Freeze the isolated catalog and runner/runtime hashes in the
new experiment identity and assert the effective transport in integration tests.
The runner rejects a catalog that would inject presets. The user approved this
correction on 28 September 2026; no follow-up provider calls had been made at
approval. The generated `followup-model-routing.json` is passed only to the
experiment process through `DAEMON_MODEL_ROUTING`, with source/artifact provenance.

The explicit-effort settings are the catalog's
default presets, not validation of every profile override. In particular Sol's
`reasoning` profile `xhigh` remains untested. No seed or sampling option is invented
for cross-provider uniformity; supported exact request parameters are recorded.
There is no assertion that provider-default effort equals any named effort level.

All conditions use the same 4096 maximum output tokens, 16000 context ceiling,
three model calls and 90-second whole-attempt deadline. Record reasoning tokens
where reported; do not count hidden reasoning as free or require its disclosure.
These are experiment ceilings, not proposed production latency targets.

## Stage A: six diagnostic cases

Six cases × three candidates × two conditions × two repeats = **72 attempts**.
Deterministically interleave candidates and conditions; each candidate's first
scheduled block includes one attempt in each condition to check both parameter
paths early, within the existing count. Use new wording/identifiers
while retaining these known diagnostic patterns:

1. **Pointer following:** distinguish memo ID from referenced standard ID. Fetch
   responses match the requested ID; unsupported IDs return a typed missing result.
   The caller must use the referenced ID. This addresses fixture defect #335.
2. **Empty search:** disclose at most two search tool invocations, including calls
   bundled in one model response; require a final absence report within three
   model calls. Record both tool and model counts separately. Count a disallowed
   proposed third search as a budget/instruction failure and stop without a
   synthetic extra completion call.
3. **Recoverable search timeout:** one synthetic retryable tool timeout, then a
   valid result; require evidence-backed completion under the stated limits.
4. **Source attribution:** two documents with scoped values; require document IDs
   and preserve scope. Citation requirements appear in the prompt, not only rubric.
5. **Qualified policy:** preserve an exception, explicitly separate unstated
   post-exception behavior from known policy, and avoid invented operational rules.
6. **Structured compression:** retain both identifiers and the amount in the array
   and summary; explicitly state this dual-location requirement in the prompt.

Tool simulation must be argument-sensitive wherever the answer depends on the
argument. Negative tests verify wrong-ID responses before live use. Do not repair
answers, add an extra call, or reclassify a failed attempt to make a condition pass.

## Stage B: held-out screen

Eight new cases × three candidates × two repeats = **48 attempts**, using only
the predeclared explicit-effort condition. The cases test lookup chains, absence,
source conflict/recency, citation/qualifier fidelity and structured extraction,
including source-injection resistance. They must be substantively new scenarios,
not just identifier substitutions of Stage A cases.

Freeze both sets and their rubrics/hashes before any Stage A call. A separate
read-only reviewer checks prompt/rubric alignment and fixture behavior. Keep Stage
B content out of diagnostic tuning; do not change prompts, tools, settings or
thresholds after observing Stage A. If a material design defect requires changes,
report it and propose a new version rather than consuming the held-out set as a
debugging loop. Human reviewers see randomized opaque attempt labels with a
retained mapping; conceal model identity where feasible, without claiming a blind
review if prior exposure reveals it.

### Proposed acceptance criteria

- Stage A is diagnostic: publish paired outcomes and all costs, without treating
  improvement on known failure patterns as a promotion certificate.
- Stage B candidate eligibility: **at least 15/16 acceptable completed answers**,
  **zero hard violations**, and zero wrong-identifier tool fetches. Require valid
  schema where applicable, explicit tool/model budgets and task-specific checks.
- A pass requires human adjudication against the frozen rubric; use an actual
  reviewer-supplied name/handle. Review all held-out outputs, not just AI flags.
- Separate material defects, minor misses and rubric ambiguity. Define which
  minor misses still satisfy each case before execution. A materially ambiguous
  fixture cannot be retroactively counted as a pass; report the affected decision
  as inconclusive and seek a revised test plan. Apply ambiguity handling to both
  repeats for all candidates symmetrically. Removing one case leaves only 14
  decidable attempts per candidate, insufficient for 15/16; the screen is then
  inconclusive, not a chance to lower the threshold or change denominators.
- Latency screens: utility p95 <=10s; tool/evidence-chain p95 <=30s; synthesis
  p95 <=60s, using nearest-rank and including failures. Publish actual subgroup
  sample sizes and maxima; they are coarse signals, not SLO estimates. Assign
  each case's latency class when freezing it.
- Rank cost per human-acceptable answer only among candidates meeting the same
  held-out acceptance rules, on identical coverage, including all failures in
  cost numerators. Unknown invoice costs prevent invoice-cost ranking; conservative
  ledger totals remain independently reported. Do not use the whole diagnostic
  plus held-out mix to claim production cost superiority.
- Passing qualifies only for consideration within the tested task envelope, not
  broad reasoning/coding or every memory helper. Retain existing operation-specific
  tests and deployment gates. Failed screening diagnoses follow-up needs, not a
  claim that a model is inherently incapable.

## Budget, state and stopping rules

Maximum **120 logical attempts / 360 provider dispatches**, no additional smokes
or retries outside that envelope. An initial diagnostic attempt per candidate
serves as the compatibility check. The selected routes remain exact, provider-side
fallback stays disabled, and recorded interrupted/failed attempts never replay.
This experiment does not also test endpoint fallback; do not stack retry systems.

Proposed incremental sub-cap: **USD 20 inside the existing USD 25 aggregate cap**.
At the currently approved endpoint ceilings, conservatively charging 16000 input
plus 4096 output tokens for every call gives:

Bounds round up each call to integer microusd before summing.

| Candidate | Maximum calls | Conservative ledger bound USD |
| --- | --- | --- |
| Luna | 120 | 0.481560 |
| Sol | 120 | 9.630720 |
| Sonnet | 120 | 9.630720 |
| Total | 360 | **19.743000** |

The prior aggregate charge is USD 0.370314; the maximum combined bound is
USD 20.113314, below USD 25. These are deliberately loose bounds, not expected
spend. Admission checks actual reserved/spent amounts and rejects whichever cap
would be exceeded. Freeze an experiment ledger baseline only with zero open holds;
require exclusive experiment execution so other batches cannot obscure attribution.
No new funding, trial grants or period rollover refill is authorized.

Each call gets its own reservation/settlement. Unknown usage retains the full
bound. Stop for privacy, auth, parameter incompatibility, accounting uncertainty,
unexpected transport failure or interrupted execution; investigate without replay.
Known semantic/task-budget failures remain recorded and may be followed only by
distinct scheduled attempts. Early stopping may leave an incomplete screen; never
report unrun attempts as successes or compare unequal coverage without disclosure.

Revalidate routes, prices and supported parameters before execution. Existing
approvals expire October 1 and funding is explicitly `2026-09`; stop before the
period/approval boundary with sufficient time for the per-attempt deadline. If
execution cannot fit, request new period authority rather than moving the old
allowance. Production policies and defaults remain untouched.

## Opus and Astra

Reserve Opus 5.5 and Astra for a **separate harder-set proposal after this screen**,
using independent ground truth and tasks where premium candidates demonstrably
struggle. No endpoint, parameter, call count or paid execution for those models
is authorized by this plan. Do not add them to these easy fixtures simply to spend
the remaining allowance. Held-out content used here cannot also serve as unseen
evidence for a tuned flagship comparison.

## Approval and execution boundary

The user approved the 120-attempt design, explicit-effort conditions, acceptance
rules and USD 20 sub-cap on 28 September 2026. Implement the separate versioned
runner/fixtures and complete tests and independent review before any live calls.
This approval does not
authorize production config changes, deployment, API/schema changes or purchases.
Required verification includes the application accounting path for denial,
cancellation, restart/no-replay and original-period settlement; endpoint/provider
and effort controls must survive that path rather than only pass mocked tests.

## Pre-execution verification checkpoint

The corpus was corrected before any inference to include missing-data acknowledgment
as well as disambiguation in H02, and to clarify D01's optional final-answer source
ID. The revised frozen hash is
`dac5ba663539fdafa2ed350b9c9253e98e5bb46fa1f6987efbc32ae30d0bd4fe`.
No calls used the earlier pre-live hash. Independent corpus re-review accepted
the alignment with the approved design.

The initial runner/fixture suite passes 43 tests, including actual runtime dispatch
with mocked transport proving effort omission versus explicit effort, separate
settlement, cancellation and blocked interrupted-state replay. Integration fixed
token/cost-bound confusion and a nested tool-schema accessor before execution.
Independent runner review found no blocker-level defect, but recommended preserving
provider assistant reasoning/signature fields across tool turns; that behavior now
matches the baseline runner and has regression coverage. Catalog declarations are
required, and identity hashing includes accounting helper modules as well as runtime
and catalog. These checks do not replace final preflight or approval of the isolated
no-preset parameter correction. No paid calls had occurred at this checkpoint.

### Execution start checkpoint

The user approved the isolated-catalog correction. Final verification passed
374 integrated tests including PostgreSQL accounting, zero isolated basedpyright
errors and all pre-commit hooks. Independent runner re-review accepted the fixes.
The private catalog uses `parameter_presets: {"default": {}}` for the three
models: initial read-only preflight rejected a missing default key before any
state or dispatch, and the artifact was corrected before identity freeze.

Final dry-run checked all 120 request conditions with both parameter paths,
reported 19743000 microusd maximum reachable cost, 370314 microusd prior ledger
exposure and zero open holds, and created no live state. The diagnostic stage
was then launched for 72 scheduled attempts with new private `followup-state.json`
and a fresh timestamped results path. Held-out execution remains a separate phase,
gated on all diagnostic attempts being recorded without unresolved stops.
No prompt, fixture, threshold, setting or implementation change is permitted
between these stages under the frozen identity.

### Execution completed — 28 September 2026

All 72 diagnostic and 48 held-out attempts completed without recorded task failures
or stops, using 258 provider calls. No experiment implementation, corpus or settings
changed between stages. Follow-up ledger charge: **USD 0.293419**; reported provider
invoice subtotal: **USD 0.29330829**, with zero unknown invoice costs. The authoritative
September account ledger reconciles to **USD 0.663733** aggregate, zero reserved
balance and zero open holds; **USD 24.336267** remains under the original cap.

| Stage / condition | Model | Completed | Calls | Ledger USD | Median s | p95 s |
| --- | --- | --- | --- | --- | --- | --- |
| Diagnostic / default | Luna | 12/12 | 26 | 0.000967 | 3.82 | 8.33 |
| Diagnostic / default | Sol | 12/12 | 26 | 0.019081 | 4.31 | 6.65 |
| Diagnostic / default | Sonnet | 12/12 | 26 | 0.064880 | 4.86 | 12.64 |
| Diagnostic / explicit | Luna | 12/12 | 26 | 0.000931 | 3.06 | 8.68 |
| Diagnostic / explicit | Sol | 12/12 | 26 | 0.020819 | 3.89 | 5.01 |
| Diagnostic / explicit | Sonnet | 12/12 | 26 | 0.064616 | 4.93 | 6.26 |
| Held-out / explicit | Luna | 16/16 | 34 | 0.001370 | 4.20 | 9.58 |
| Held-out / explicit | Sol | 16/16 | 34 | 0.027427 | 3.65 | 7.54 |
| Held-out / explicit | Sonnet | 16/16 | 34 | 0.093328 | 4.95 | 11.45 |

Held-out latency classes each have orchestration n=6, synthesis n=8 and utility
n=2 per candidate. Nearest-rank p95 equals the subgroup maximum at these sizes:
Luna 9.58/7.25/4.35s, Sol 5.21/7.54/3.04s, Sonnet 10.93/11.45/3.08s respectively.
All meet the predeclared held-out latency screens. Diagnostic Sonnet/default's
utility n=2 maximum is 12.64s, above the 10s screen; diagnostic outcomes are not
the held-out eligibility decision. Small samples do not establish production SLOs
or a causal benefit from explicit effort.

Private artifacts under `/tmp/opencode/roster-live-20260927/` include
`followup-final-summary.json`, `followup-heldout-human-review.{json,md}` and a
separate opaque-label mapping `followup-heldout-review-map.json`. All 48 human
verdicts and reviewer identities remain pending. Independent AI semantic screening
reviewed all 48 outputs and found no material defects, wrong-ID fetches or hard
violations. Six labels had non-blocking observations: REVIEW-12/48 used equivalent
subtraction rather than showing a separate subtotal; REVIEW-28/37 mentioned an
archival branch without fetching it; REVIEW-29/31 noted an injected instruction
without reproducing its payload. The frozen rubrics permit these behaviors.
The reviewer summary's clean-label count said 41, but its listed ranges contain
42 clean labels plus six observations, covering all 48. Primary state inspection
confirms 102 held-out model calls, at most three per attempt and no disallowed
tool proposals or recorded task failures. AI screening cannot substitute for the
approved human acceptance gate. These
costs are raw measurements, not cost-per-human-acceptable-answer rankings.

### Human adjudication completed

The user reviewed all 48 outputs in eight six-answer batches with model labels
concealed, then explicitly supplied the reviewer handle **Human**. Decisions are
recorded separately in `followup-human-adjudications.json`; raw execution evidence
and the original pending-review export remain unchanged. Human accepted 46 answers
without qualification and REVIEW-28/37 as minor-but-acceptable (both Sonnet,
mentioning the archival branch without fetching it). No answer was rejected or
declared ambiguous. These judgments apply only to the presented held-out answers,
not the diagnostic stage or previous pilot.

| Candidate / effort | Human-acceptable | Minor included | Hard violations / wrong-ID fetches | Held-out ledger USD per acceptable answer |
| --- | --- | --- | --- | --- |
| Luna / low | 16/16 | 0 | 0 / 0 | 0.000085625 |
| Sol / high | 16/16 | 0 | 0 / 0 | 0.0017141875 |
| Sonnet / high | 16/16 | 2 | 0 / 0 | 0.005833 |

All three meet the predeclared held-out acceptance, schema, budget and latency
screens within this narrow synthetic task envelope. The equal-coverage ledger
cost ranking is Luna, Sol, Sonnet; invoice accounting remains separate. Sol costs
about 20.0 times Luna and Sonnet about 68.1 times Luna (3.4 times Sol) on this set.
The set establishes no premium semantic advantage: all three were acceptable on
every presented answer. It supports Luna-first consideration for these bounded
tasks. Sol is the more economical tested premium candidate here; deciding when a
premium model is worth using needs harder evidence. No production activation,
general reasoning/coding qualification or diagnostic semantic pass follows.
