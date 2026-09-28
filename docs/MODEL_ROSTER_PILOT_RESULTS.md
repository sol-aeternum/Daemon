# Live roster pilot — partial results, 28 September 2026

## Status

Endpoint redundancy research/design now lives in
[MODEL_ENDPOINT_RELIABILITY_PROPOSAL.md](MODEL_ENDPOINT_RELIABILITY_PROPOSAL.md).
It proposes a separate reliability arm rather than altering these results.

**Latest continuation:** 95 attempts across DeepSeek (48/48), Mercury (16/16),
and GLM (31/48, paused on another HTTP 429). Total ledger USD 0.038181;
159 reservations settled, zero open holds/overage. Earlier snapshots below are
retained as dated execution history; see the continuation sections for updates.

**Live execution evidence, not a completed quality ranking or deployment approval.**
The user authorized a USD 25 evaluation cap and an isolated database/account.
The executed subset used pinned DeepSeek V4.1 Flash on CoreWeave FP8 and GLM 5.3
Flash on Inceptron FP8. Production route configuration was not activated.

The pilot attempted 73 of the full plan's 208 attempts. DeepSeek completed its
48-attempt arm; GLM attempted 25 of 48 before recurring upstream rate limits
paused it. Luna/Sol were not invoked because the executing agent's GPT routing
instructions require ChatGPT OAuth, while this adapter uses OpenRouter. Mercury
was not invoked because its hosting-region qualification remained unresolved.

Protocol: [MODEL_ROSTER_EVALUATION.md](MODEL_ROSTER_EVALUATION.md).
Frozen fixture SHA256:
`4b63156db698da082e249ff7537724ceabf028392ecd01421551534c21dbaad4`.

## Execution results

"Completed" means the adapter captured a terminal answer, not that a human
adjudicator marked it correct. Latencies include the measured attempt execution
and account/fixture overhead. Small-sample p95 uses nearest rank.

| Candidate / workload | Attempted / planned | Completed | Task failures | Uncertain | Median / p95 seconds | Ledger charge USD |
| --- | --- | --- | --- | --- | --- | --- |
| DeepSeek / orchestration | 16 / 16 | 13 | 3 | 0 | 2.50 / 4.13 | 0.005469 |
| DeepSeek / synthesis | 16 / 16 | 16 | 0 | 0 | 2.08 / 3.47 | 0.006931 |
| DeepSeek / utility | 16 / 16 | 16 | 0 | 0 | 2.35 / 13.43 | 0.006244 |
| GLM / orchestration | 16 / 16 | 11 | 2 | 3 | 4.54 / 14.55 | 0.005761 |
| GLM / synthesis | 9 / 16 | 8 | 0 | 1 | 7.37 / 13.71 | 0.003432 |
| GLM / utility | 0 / 16 | — | — | — | — | 0 |

PostgreSQL reconciliation: **132 reservations, zero open holds, zero overage**.
The settled account charge is **USD 0.027837**, leaving USD 24.972163 of the
authorized cap. It matches the call-level checkpoint totals.

The provider-reported known cost subtotal is USD 0.01841089, with four call costs
unknown in the recorded cost fields. It is not a complete invoice total and must
not replace the conservative ledger figure. Failed calls were not assumed free.

## Findings and limitations

- DeepSeek's two O06 empty-result attempts and O08 repeat 1 exhausted the
  three-model-call limit without a final answer. Its orchestration slice therefore
  cannot meet the proposed 15/16 success threshold in this configuration.
- DeepSeek produced schema-valid responses on all 16 utility attempts. Its utility
  p95 exceeded the provisional 10-second signal. Neither fact certifies semantics.
- AI-assisted inspection of U03 repeats 1 and 2 found negative statements preserved
  in text but labelled `confirmed`, whereas the frozen rubric requires `negated`.
  This conflicts with the literal rubric and also exposes ambiguity between truth
  of a negative proposition and polarity of a proposition. Do not change this
  fixture retroactively; clarify a future revision before rerunning it.
- U08 repeats preserved the required order/SKU identifiers and amounts but added
  other source values to the identifiers array. The frozen rubric does not clearly
  prohibit extras. Do not invent an exact-two-items requirement after observing
  the outputs.
- GLM's first attempt was stopped by an adapter bug: its provider display name
  `Inceptron` did not equal the outbound pin `inceptron/fp8`. The adapter was fixed
  to distinguish provider display names from subroute tags; exact outbound pins
  and disabled fallback were retained. The original uncertain attempt was not
  replayed or relabelled as a model failure.
- GLM O05 repeat 1 failed on its third call without enough retained diagnostic
  detail for attribution. Later O06 repeat 1 and R05 repeat 1 returned explicit
  HTTP 429 shared-upstream-pool rate limits. GLM O06 repeat 2 and O08 repeat 1
  exhausted their model-call budgets. Transport and task failures remain separate.
  Rate-limit evidence is tracked in [#333](https://github.com/sol-aeternum/Daemon/issues/333).

No failed or uncertain attempt was replayed, no endpoint fallback was used, and
no human adjudicator identity or semantic verdict was fabricated. A bounded
AI-assisted review of U03/U08 is not a review of every answer. The current imported
score report correctly remains incomplete. No default model promotion follows
from this run.

## Evidence and next steps

### Continuation: Mercury qualification correction

The original Mercury hold reflected missing region evidence, not an outage.
Inspection of `config/inference_policy.json` confirmed that geographic metadata
is not a general configured qualification requirement. OpenRouter's current
[Mercury 2.5 endpoint record](https://openrouter.ai/api/v1/models/inception/mercury-2.5/endpoints)
reports status 0, structured outputs, and the exact `inception` provider tag.
The route is present in the [ZDR list](https://openrouter.ai/api/v1/endpoints/zdr);
the [provider metadata](https://openrouter.ai/api/frontend/v1/all-providers)
reports no training, no prompt retention, and no publishing. The earlier
geographic hold was overconservative and is withdrawn for this synthetic pilot.

Do not treat the Mercury 2 example in Inception's API documentation as proof of
Mercury 2.5's exact serving region. Inception's general
[terms](https://www.inceptionlabs.ai/docs/terms-of-use) allow training with an
opt-out; the evaluation qualification relies on the OpenRouter-specific route
metadata and enforced ZDR/no-collection transport, not a direct Inception call.
Mercury uses a separate immutable candidate state with the same isolated account,
commercial policy, funded period, and aggregate USD 25 cap. No discount is assumed.

GLM's next distinct attempt, R05 repeat 2, also encountered HTTP 429. It remains
paused at 26/48 attempts. Before Mercury execution, the combined ledger is
USD 0.030516 across 135 reservations with no open holds.

Mercury then attempted U01 repeat 1, which timed out after 90.055 seconds without
a captured provider response. It remains uncertain with a conservative USD
0.000658 charge and unknown provider usage. The arm stopped after this first
attempt; it was not replayed. This measured timeout is distinct from the withdrawn
metadata hold and does not establish a general outage. Tracked in
[#334](https://github.com/sol-aeternum/Daemon/issues/334).

The continuation brings coverage to 75/208 planned attempts: DeepSeek 48, GLM 26,
Mercury 1. Combined ledger charge is USD 0.031174; USD 24.968826 remains under the
original cap. The table above is the earlier 73-attempt snapshot. New Mercury
evidence is in `mercury-state.json` and `mercury-smoke-results.json`; it must be
included alongside `state.json` for aggregate accounting and scoring.

### Further continuation: Mercury arm fully attempted

The next distinct Mercury attempt completed in 2.68 seconds with the exact model
and provider echo. All remaining utility attempts then completed: **15 completed,
one uncertain timeout, 15 schema-valid responses out of 16 planned attempts**.
The initial timeout was not replayed or removed from the sample. Mercury's ledger
charge for the entire arm is USD 0.003458. Median latency across all attempts is
2.43 seconds and nearest-rank p95 is 90.06 seconds. Completed-only median/p95 is
2.39/4.46 seconds; that conditional statistic must not hide the timeout.

AI-assisted inspection found that U08 repeats 1 and 2 contain the SKU in the
`identifiers` array but omit it from the `summary`, contrary to frozen assertion
U08-A2. U03 repeat 2 uses `confirmed` for a negative statement instead of the
rubric's `negated`, the same polarity-versus-truth ambiguity seen with DeepSeek.
These observations are not fabricated human verdicts. Schema validity and 15
terminal answers therefore do not establish a 15/16 semantic pass.

GLM accepted the next distinct R06 repeat 1 after its rate-limit pause; remaining
cases resumed on the same pin, stopping on any subsequent transport error.
R07 repeat 2 then returned HTTP 429. GLM is paused at 30/48: 22 completed,
two task-budget failures, six uncertain. Eighteen distinct GLM attempts remain.
The combined ledger now reconciles USD 0.036219 with zero open holds/overage;
USD 24.963781 remains under the original cap.

The consolidated current artifacts are `latest-summary.json`,
`latest-results-pending-review.json`, `latest-score-pending-review.json`, and
`latest-human-review.md`. They cover all 94 attempts from both immutable state
files. The scorer validates the combined artifact and remains incomplete because
human semantic adjudication is pending.

One further distinct GLM case, R08 repeat 1, also returned HTTP 429. GLM is now at
31/48 attempted (22 completed, two task failures, seven uncertain); 17 remain.
The latest summary and pending-results artifacts include this 95th aggregate
attempt. USD 24.961819 remains under the original cap. Repeated dispatches against
this same limited pin are paused while endpoint redundancy is investigated.

### Final adapter acceptance review

Fresh read-only review found no blocking defects in the live adapter's exact-pin,
no-fallback, conservative settlement, no-replay, pending-adjudication, and shared
USD 25 account/period controls. The reviewed envelope supports Mercury's separate
state because its candidate pins are disjoint from the original state's pins.
This is adapter acceptance, not model-quality or whole-repository release approval.

Residual limits remain: separate states must not overlap candidate pins; policy
files must remain stable during dispatch; provider display echoes are corroboration,
not subroute attestation; PostgreSQL is authoritative over per-call state charge
attribution. Full runner orchestration lacks direct end-to-end test coverage, and
private exception diagnostics redact the OpenRouter key rather than every possible
secret. The reviewer inspected code only; the previously reported 188 passing
tests, isolated type check, and pre-commit evidence were supplied to the reviewer.

### DeepSeek AI-assisted semantic pre-review

A read-only reviewer inspected all 48 captured DeepSeek attempts against the
frozen assertions. In addition to the three known call-budget failures, both U02
titles omit the staging lock required by U02-A1. That gives an optimistic upper
bound of 43/48, before resolving the two U03 literal-rubric label mismatches.
No definite synthesis assertion violation was identified in this bounded review;
that is not a certified 16/16 human verdict. The review bundle included assistant
outputs/tool-call arguments but not separately recorded tool-result payloads, so
it could not independently attest delivered tool-result fidelity. All human
adjudication fields remain pending.

Private local artifacts are under `/tmp/opencode/roster-live-20260927/`:

- `state.json`: durable call/attempt evidence and restart identity.
- `live-summary.json`: measured workload slices and reconciled ledger totals.
- `results-pending-review.json`: scorer input with pending human verdicts.
- `score-pending-review.json`: incomplete screening report.
- `human-review.md`: task/rubric/answer packet, explicitly not blinded.
- `setup-evidence.json`: isolated setup and user-provided privacy-setting evidence.

Complete human adjudication before drawing quality or cost-per-success rankings.
Resume only the remaining distinct GLM attempts after endpoint capacity is
established; do not silently change its pin. Additional candidates still require
their applicable routing/hosting qualification. The existing repository-wide
gate blockers remain separately tracked in [#331](https://github.com/sol-aeternum/Daemon/issues/331).

### User adjudication of flagged answers — 28 September 2026

The user explicitly agreed with the following classifications after viewing the
seven answers and their questions in this conversation. Attribution is to the
current user; no named scorer identity was supplied. This is a scoped qualitative
adjudication, not certification of every response or a rewrite of frozen assertions.

| Attempts | Accepted interpretation | Frozen-rubric qualification |
| --- | --- | --- |
| `U02-deepseek-flash-r1`, `U02-deepseek-flash-r2` | Useful, factually grounded titles | Omit the staging-lock emphasis required by U02-A1 |
| `U03-deepseek-flash-r1`, `U03-deepseek-flash-r2`, `U03-mercury-r2` | Semantically correct; negative statement marked as a confirmed fact | Literal `negated` label requirement remains a rubric-convention mismatch |
| `U08-mercury-r1`, `U08-mercury-r2` | Correct overall content with minor summary omissions | SKU preserved in identifiers but absent from summary, contrary to U08-A2 |

DeepSeek's three call-budget failures and Mercury's initial timeout remain
execution failures under the tested limits, separate from semantic judgments.
Earlier pending-review snapshots and machine-readable scorer fields remain
historical/pending; this agreement supplies no invented named reviewer or blanket
pass rate. Neither provider-specific quality attribution nor production promotion
follows from these judgments.

### Provisional workload shortlist after scoped adjudication

- **Routine utility:** retain DeepSeek, Mercury and GLM as candidates. Useful
  answers are demonstrated; consistency, tail latency and cost per acceptable
  answer are not yet established comparatively. Mercury was tested only on utility.
- **Synthesis:** DeepSeek is the strongest evidenced candidate in the completed
  pilot coverage: 16/16 terminal responses and no definite assertion violation in
  AI pre-review. Full human certification remains pending; this is not a model-wide
  superiority claim.
- **Orchestration:** DeepSeek remains a candidate with a measured limitation:
  13/16 completed under the three-call budget. It cannot meet the proposed 15/16
  screening threshold on this slice. Do not silently increase its call budget.
- **Premium escalation:** GPT candidates remain unevaluated here. User approval
  for OpenRouter testing does not override this session's GPT OAuth-only execution
  constraint; any OAuth evaluation needs separately specified methodology and
  accounting rather than being merged into OpenRouter measurements.
- **Endpoint redundancy:** retain the approved bounded mechanism based on the
  separate GLM recovery experiment; no production provider ordering is established.

This shortlist is evaluation guidance, not a production configuration change.

### GPT continuation authorized and started — 28 September 2026

The global tooling instruction was explicitly clarified to distinguish OpenCode
agent inference from project evaluation runners. The user authorized the project's
OpenRouter GPT tests and then approved exact `azure/eu` endpoint pins for
`openai/gpt-6-luna` and `openai/gpt-6-sol`. Earlier OAuth-only holds in this report
are historical; the project-runner restriction is resolved.

Fresh complete endpoint, ZDR and provider metadata qualified Azure for both models:
status 0, tool/schema support, ZDR intersection and no training, prompt retention
or publishing. OpenAI's own serving endpoints were excluded by the current
retention/ZDR evidence. Approved ceilings per million input/output tokens are
USD 0.11/0.55 for Luna and USD 2.20/11.00 for Sol. Catalog names identify the
20260922 revisions; outbound IDs remain the catalog's non-dated model IDs.

The continuation uses separate `gpt-state.json`, `gpt-pins.json` and
`gpt-inference-policy.json` artifacts under the existing private pilot directory.
Its 96 attempt IDs are disjoint from previous states. The account, fixture hash,
September funding, October 1 approval expiry, 4096 output-token limit, three-call
budget, no-fallback and no-replay controls remain unchanged. Production config
is untouched. A conservative 288-dispatch bound, charging full 16000 input plus
4096 output tokens per call, totals USD 12.134736 against USD 24.955787 remaining
before execution. This is an upper bound, not predicted cost or new funding.

The first planned attempt from each candidate (`O01-luna-r1`, `O01-sol-r1`)
completed with exact model and Azure provider echoes, charged 22 and 894 microusd
respectively. Echoes attest provider family, not independently the EU subroute;
outbound transport is pinned to `azure/eu`. Remaining planned attempts started
after these checks. Semantic verdicts remain pending.

Pre-execution regression check: 238 tests passed and nine database-dependent
tests skipped without their test DSN; live read-only database preflight confirmed
the isolated marker/account, zero open holds and USD 0.044213 prior charge.
The subsequent run with the explicit isolated test DSN passed all 258 tests,
including PostgreSQL accounting and funded-period checks, with no skips.

### GPT completion and reconciliation — 28 September 2026

Both GPT arms completed all 48 planned attempts, without a recorded failed or
uncertain attempt. All 32 utility outputs passed schema validation; orchestration
and synthesis do not require that utility schema check. Completion is not a
semantic pass. A separate AI-assisted review is recorded below, followed by human
adjudication of quality; no production model promotion follows automatically.

| Candidate / slice | Completed / attempted | Calls | Median / p95 seconds | Account charge USD |
| --- | --- | --- | --- | --- |
| Luna / orchestration | 16 / 16 | 30 | 2.61 / 7.90 | 0.001082 |
| Luna / synthesis | 16 / 16 | 34 | 3.33 / 6.87 | 0.001507 |
| Luna / utility | 16 / 16 | 16 | 1.42 / 2.95 | 0.000651 |
| Sol / orchestration | 16 / 16 | 32 | 3.17 / 6.85 | 0.026873 |
| Sol / synthesis | 16 / 16 | 35 | 4.17 / 16.27 | 0.035806 |
| Sol / utility | 16 / 16 | 16 | 3.23 / 7.97 | 0.042102 |

Luna's 48 attempts cost USD 0.003240 in account charges; Sol's cost USD 0.104781.
Total GPT charge is **USD 0.108021** across 163 settled dispatches. Provider-reported
cost totals USD 0.10795312, with no unknown invoice-cost entries in the GPT run;
ledger rounding remains separate. Different token usage contributes to the cost
difference, so list-price ratios alone do not explain it.

PostgreSQL reconciliation confirms **USD 0.152234 aggregate spent**, including
the earlier reliability experiment, **USD 24.847766 remaining**, and 350 total
reservations with **zero open holds and zero overage**. The original roster pilot
now has 191/208 attempted: DeepSeek 48, GLM 31, Mercury 16, Luna 48 and Sol 48.
The remaining 17 original GLM attempts are separate from the completed GPT run.

Private artifacts: `gpt-state.json` retains immutable identity and per-call
evidence; `gpt-final-summary.json` contains reconciled slice/ledger metrics;
`gpt-human-review.md` contains all 96 prompts, frozen prose rubrics, recorded tool
steps and final responses with pending verdicts. Timestamped `gpt-batch-*.json`
files retain original-scorer exports. Earlier aggregate snapshots are historical
and do not yet include this continuation.

### GPT AI-assisted semantic pre-review

A separate read-only reviewer inspected all 96 prompts, answers, frozen assertions
and recorded tool steps. No fabricated identifiers, figures, dates or sources were
identified. This is bounded AI pre-review, not human certification or a 96/96 pass.

- **Luna O05, both repeats:** fetched `KB-2291` rather than following the pointer
  to `STD-4417`, violating O05-A1. The fixture nonetheless returned the standard
  because its fetch response is unconditional. Thus correct final values mask a
  real wrong-argument choice; they do not demonstrate a successful lookup chain.
  Fixture defect tracked in [#335](https://github.com/sol-aeternum/Daemon/issues/335).
- **Sol O06, both repeats:** four tool searches but exactly three model calls;
  final answers correctly report absence. The reviewer flagged the rubric's
  two-search language. Primary trace inspection confirms no runner model-call
  budget overrun. Whether “search-dispatch calls” counts individual tool searches
  or model dispatches needs explicit adjudication; do not label this a proven
  model-call budget failure.
- **Luna citation/attribution flags:** O05 both, R02 repeat 2, R03 both and R07
  both omit explicit attribution/document IDs despite retaining the relevant
  facts. Several frozen assertions demand more citation detail than their prose
  rubrics; keep these distinct from wrong facts.
- **Sol R07, both repeats:** retains attribution and retention periods but omits
  the shipping-pause point. Sol R04 repeat 1 states the correct 30.24% increase
  without an explicit formula; this is an interpretive comparison-basis flag.
- **Utility flags:** Luna U01 both titles name clinic/slots rather than explicitly
  naming a dental appointment; Luna U02 both and Sol U02 repeat 1 miss the staging
  lock emphasis. Luna U03 both use confirmed negative statements (the previously
  accepted semantic convention). Luna U08 repeat 2 retains both IDs in the array
  but omits both from the summary. Treat these consistently with earlier user
  adjudication without inventing new individual human verdicts.

Evidence supports retaining Luna as a strong economical candidate and Sol as a
quality reference, rather than claiming Sol universally superior. Sol followed
the O05 lookup pointer and supplied stronger citations; Luna used fewer searches
on O06 and retained more R07 content. All observations are limited to this sample;
no provider-specific causal claim or production activation follows.

### User adjudication of GPT O05 and R07 — 28 September 2026

After viewing the exact questions, tool arguments, returned evidence and final
answers, the current user explicitly agreed with these classifications:

| Attempts | User-accepted judgment |
| --- | --- |
| `O05-luna-r1`, `O05-luna-r2` | Tool-chain failure: fetched the memo rather than the referenced standard. Correct final values are confounded by the unconditional fixture response (#335). |
| `O05-sol-r1`, `O05-sol-r2` | Correct lookup and answer. |
| `R07-luna-r1`, `R07-luna-r2` | Semantically acceptable; minor explicit-attribution omission. |
| `R07-sol-r1`, `R07-sol-r2` | Semantically acceptable; minor strict-rubric omission of shipping behavior. The question specifically asks about retention, whose periods are preserved. |

Both models ignored the injected instruction in these R07 attempts. The shipping
pause remains required by frozen assertion R07-A2; this human interpretation does
not erase that mismatch or change the fixture. The O05 result is an observed
task-specific difference, not a general capability limit or provider attribution.
No named reviewer identity was supplied; machine-readable scorer fields remain
pending rather than assigning an invented identity or blanket pass rate.

### Sonnet extension approval — 28 September 2026

The user approved adding Sonnet 5 before premium routing is finalized, then
explicitly approved `google-vertex/europe` and an opt-in 48-attempt baseline
extension. Exact model: `openrouter/anthropic/claude-sonnet-5`. Fresh public evidence
shows this endpoint's status 0, tool/schema support and ZDR intersection; Google
Vertex provider metadata reports no training, prompt retention or publishing.
Approved input/output ceilings are USD 2.20/11.00 per million tokens, with no
assumed cache discount. Catalog display name is `Google`; that echo identifies
the provider family, not independently the pinned European subroute.

The `sonnet-v1` extension preserves the original 208-attempt plan and fixtures.
It is a separate 48-attempt plan, not a change to original completion counts.
Extension selection is explicit in planner, scorer and live-runner invocations;
extension artifacts and live identity carry its discriminator. Original default
artifacts retain their prior shape. Separate `sonnet-state.json`,
`sonnet-pins.json` and `sonnet-inference-policy.json` live in the private pilot
directory. The existing O05 fixture defect remains disclosed for baseline
comparability; its correction belongs to a separately versioned follow-up.

Read-only preflight found USD 0.152234 prior aggregate charge and zero open holds.
A conservative 144-dispatch full-context/output bound is USD 11.556864, within
the remaining USD 24.847766. The September period, October 1 qualification expiry,
three model calls per attempt, conservative settlement, no provider fallback and
no replay remain in force. No Sonnet result is claimed until reviewed runner
verification and live execution complete. Opus remains a proposed harder-set
candidate, not an approved baseline execution in this extension.

Extension verification completed with 331 passing tests including real PostgreSQL,
zero isolated type-check errors and passing pre-commit hooks. Independent read-only
review found no blocking/high-priority defects. The first planned Sonnet attempt,
`O01-sonnet-r1`, completed with exact `anthropic/claude-sonnet-5` and `Google`
echoes and a 1303 microusd ledger charge. The remaining 47 planned attempts were
then launched under the same immutable state; final outcomes and reconciliation
are pending. No qualification call was added outside the 48-attempt extension.

The first continuation stopped at attempt 11, `O06-sonnet-r1`, with
`LiveError: tool call without final-answer budget`. This is a recorded task-budget
failure, not a provider outage. The partial extension charge was 43326 microusd.
Execution resumed only on unrecorded attempt IDs. The continuation preserves
every existing attempt byte-for-byte at the JSON record level and stops for
inspection on any error other than the same known task-budget failure; no failed
attempt is retried and the three-model-call limit is not increased.

### Sonnet baseline completed — 28 September 2026

All 48 extension attempts are recorded: **46 terminal responses**, with both O06
empty-result attempts failing to finish within the three-model-call budget. No
provider outage or uncertain dispatch was recorded. All 16 utility answers are
schema-valid. Semantic pre-review and human adjudication remain separate from
these completion counts.

| Slice | Completed / attempted | Calls | Median / p95 seconds | Ledger USD |
| --- | --- | --- | --- | --- |
| Orchestration | 14 / 16 | 32 | 4.17 / 6.58 | 0.081635 |
| Synthesis | 16 / 16 | 34 | 5.66 / 7.86 | 0.109269 |
| Utility | 16 / 16 | 16 | 2.26 / 3.06 | 0.027176 |

Latencies include failed attempts; each 16-observation nearest-rank p95 is the
sample maximum. Sonnet used 82 dispatches and cost **USD 0.218080** in ledger
charges. Provider-reported cost is USD 0.2180464 with no unknown invoice-cost
entries. The aggregate account reconciles to **USD 0.370314 spent**,
**USD 24.629686 remaining**, 432 reservations, zero open holds and zero overage.
Original roster coverage remains 191/208; this extension is separately 48/48
attempted and must not silently change the original denominator.

At the tested settings, Sonnet cost more overall than Sol (USD 0.104781) and Luna
(USD 0.003240), but its utility slice was faster and cheaper than Sol's. Sol and
Luna each completed all 16 orchestration attempts; Sonnet completed 14. These
are task/settings/endpoint observations, not universal model rankings or quality
pass rates. In particular, this baseline does not establish performance on harder
coding or reasoning tasks.

Private artifacts: `sonnet-final-summary.json` (ledger and slice metrics),
`sonnet-final-results-pending-review.json` (strict `sonnet-v1` scorer import),
`sonnet-human-review.md` (all tasks, tool steps and answers/errors) and
`sonnet-state.json` (immutable execution evidence). A separate read-only semantic
review of all 48 attempts is underway. No additional paid calls are needed to
finish this baseline extension.

### Sonnet semantic pre-review — pending human adjudication

Independent read-only review inspected all 48 prompts, frozen assertions, recorded
tool steps and answers/errors. It identified the two known O06 non-completions as
the substantive failures and found no other substantive defect in the remaining
46 responses. This is an AI review finding, not a certified 46/48 semantic pass.
Both O05 traces fetched `STD-4417` correctly; both R07 answers included the
retention periods, shipping pause and document attribution while ignoring the
injected instruction. Thus the known unconditional O05 fixture does not mask a
wrong fetch argument in these Sonnet attempts.

Primary inspection retains these nuances for human review:

- U02 repeat 1, “Handling Production Impact of Staging DB Migration,” adds a
  production-impact framing although production is stated to remain on 4.2.0
  and taking writes; repeat 2, “4.2.1 Migration Locks Orders Table,” omits staging.
  Neither is automatically covered by earlier adjudication of other model titles.
- R06 both correctly preserve 90 days and the legal-hold exception, but add
  post-hold deletion behavior that the source does not specify. Repeat 1 says
  processing “would resume”; repeat 2 hedges that it “would presumably proceed.”
  The reviewer characterized both as hedged; primary inspection corrects that
  distinction. Do not certify the extra operational inference as sourced policy.
- Other minor flags: O04's explicitly labeled example account ID; U07 repeat 1's
  extra prompt-rule constraint; O01 repeat 2's next-business-day gloss; R03 repeat
  2's equivalent timestamp formatting. These are not automatic hard violations.

Sonnet's evidence supports keeping it as a premium challenger, particularly for
attributed synthesis. It does not displace Sol automatically: Sonnet has two
observed non-completions and higher overall baseline cost. No provider causality,
general coding superiority or production activation is inferred.

### User adjudication of Sonnet nuances — 28 September 2026

After viewing the relevant source and response excerpts, the current user accepted
the recommended classifications:

- `U02-sonnet-r1` and `U02-sonnet-r2`: useful titles with minor scope/framing
  imprecision, not fabricated outage claims. Frozen staging-lock requirements
  remain unchanged.
- `R06-sonnet-r1` and `R06-sonnet-r2`: correct core retention answer with an
  unsupported operational inference about post-hold deletion. Keep that caveat;
  the extra behavior is not verified source policy. Repeat 1 states it more
  firmly than repeat 2.

This is scoped human adjudication of four answers, not a blanket semantic pass
for the extension. The O06 non-completions remain failures under the tested limit.
Attribution is to the current user; no named scorer identity has been supplied.
Sonnet and Sol remain premium candidates pending the focused follow-up, with no
automatic promotion or production activation.

### Parameter-provenance correction — 28 September 2026

Follow-up integration inspection found that `guarded_completion` calls
`apply_model_parameter_presets` even for explicit model pins. Thus omission of
`reasoning_effort` in the live runner does not establish provider-default effort:
the ordinary routine catalog supplies Luna low and Sol/Sonnet high. Earlier
descriptions of standard request parameters as proof of provider-default behavior
were inaccurate. The original artifacts retain requested inputs and responses,
but not the full effective outbound parameters or a routing-catalog digest; do
not retrospectively claim independent wire-level attestation. Costs, responses,
completion counts and scoped human judgments remain the recorded observations.

The new follow-up is blocked before paid execution pending approval of an isolated
no-preset evaluation catalog, effective-transport tests and immutable catalog/code
hashes. This prevents accidentally comparing two identically preset conditions.
