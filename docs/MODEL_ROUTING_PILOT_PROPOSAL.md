# Evidence-informed routing proposal

Date: 28 September 2026. Status: **Luna-first configuration approved;
configuration and background sampling correction reviewed; backend repairs verified;
no production activation**.

## Approved configuration increment

After the completed held-out human review, the user approved Luna low as the sole
automatic candidate for routine, background and bounded research. This supersedes
the earlier mixed economical groups and research premium fallbacks proposed below.
No alternate has been accepted for those automatic pools. Exact manual pins,
account contracts and independent endpoint qualification remain in force.
Demanding reasoning and council configuration remain provisional and unchanged.
Sol's favorable measured premium cost does not authorize automatic escalation.
Production inference routes remain inactive. The remaining proposal sections
record the historical design and separate, unapproved integration work.

Verification: 138 focused routing/runtime tests and 239 broader regressions pass;
isolated basedpyright reports zero errors and pre-commit passes. Independent review
accepted the configuration/test scope and identified incompatible background sampling
controls. The user approved their removal from automatic background calls, preserving
explicit pins. The correction passes 225 combined routing/helper/memory tests;
final independent review accepted it. Contradiction detection uses reasoning and
retains its previous parameters. The user subsequently approved repairs for the
reflection fixture (#310), migration/audit (#337), summary-worker checkpoint (#338)
and test mocks (#336). All passed independent review; full isolated testing reports
2712 passed, 3 skipped, with final type checks clean. Security inventories and
separate release debt remain open; see MODEL_ROUTING.md. Production stays inactive.

## Anthropic evaluation amendment

**Current follow-up conclusion:** the completed versioned follow-up has 16/16
held-out human-acceptable answers for each of Luna low, Sol high and Sonnet high,
adjudicated by the user under the supplied handle Human. All three meet the narrow
screen's other gates. Equal-coverage ledger cost per acceptable answer is
USD 0.000085625 / 0.0017141875 / 0.005833 respectively. This supports Luna-first
consideration for the tested bounded tasks; there is no measured semantic gain
from premium models on this set. Sol is the lower-cost tested premium option,
but a general premium escalation decision still requires harder-task evidence.
See [the completed follow-up](MODEL_ROUTING_FOLLOWUP_PLAN.md). Production routing
remains proposed. The amendment below records the earlier approval sequence.

Following this proposal, the user approved evaluating Sonnet 5 on the same
48-attempt baseline before finalizing premium routing, and reserving Opus 5.5 for
a smaller, harder held-out comparison. **Premium preference is Sol versus Sonnet,
pending evidence**, not a final Sol selection. Sol references below describe the
currently measured candidate only. Opus and Astra remain unproven escalation
candidates. No Anthropic call has yet been made.

Fresh OpenRouter evidence on 28 September identifies `google-vertex/europe` for
`anthropic/claude-sonnet-5` as a proposed exact pin: status 0, tools, all tool-choice
modes, structured output, ZDR intersection, and provider metadata reporting no
training, retention or publishing. Input/output prices are USD 2.20/11.00 per
million tokens. `amazon-bedrock/eu-west-1` also intersects ZDR, but its endpoint
parameter list does not advertise `structured_outputs`; Google is the recommended
first compatibility candidate for the full schema-based utility baseline.
Anthropic's own endpoint retains prompts and is absent from the ZDR list; Azure's
Sonnet endpoints are also absent from that list in this observation. Qualification
is model/endpoint specific, not inherited from Azure's GPT qualification.

The original planner recognizes five labels and 208 planned attempts. Adding
Sonnet requires a separately identified, opt-in 48-attempt extension with scorer
support, its own state/policy/pins and preserved original plan semantics. The
baseline fixture bytes remain unchanged (including the documented O05 defect),
so comparisons retain provenance. Corrected O05 tests belong to the later versioned
follow-up. The user subsequently approved the exact `google-vertex/europe`
endpoint and bounded extension implementation. Implementation and independent
review passed; the first planned attempt completed and the remaining baseline
batch was launched. Neither the original records nor their accounting may be
reset. See the roster results for execution status and verification evidence.

## Authority and evidence

This is the next bounded proposal under the approved capability-first routing
direction. It preserves [MODEL_ROUTING.md](MODEL_ROUTING.md), the
[commercial/provider contract](SUBSCRIPTION_ARCHITECTURE.md), and AC07 (qualified
processing), AC09 (no duplicate material effects), AC12 (bounded compute) and
DEC09 (truthful capacity interruption). It is not a replacement for those contracts.

Evidence: [roster results and scoped user adjudication](MODEL_ROSTER_PILOT_RESULTS.md)
and [endpoint reliability results](MODEL_ENDPOINT_RELIABILITY_RESULTS.md).
The roster has 191/208 recorded attempts; the separate reliability comparison has
24 logical attempts. Coverage is synthetic, small and uneven. User judgments cover
selected flagged answers, not a named, complete scorer certification. Do not turn
completion rates into semantic pass rates or compute certified cost per correct
answer from these records.

## Recommended candidate placement

These are workload recommendations, not permissions to serve a model. Every
dispatch still intersects approved endpoints, capabilities, context/output fit,
account entitlements and conservative request cost.

| Workload | Proposed first choice / acceptable group | Alternate or escalation | Evidence and boundary |
| --- | --- | --- | --- |
| Short titles, extraction and simple summaries (`background`) | Luna as initial preferred candidate | DeepSeek has full utility execution with partial adjudication; GLM Flash has only four-case reliability coverage; Mercury remains utility-only challenger | Luna utility 16/16 terminal/schema-valid, median 1.42s, p95 2.95s, USD 0.000651 ledger. Semantic omissions remain possible. GLM's original utility slice is unrun. This does not qualify memory extraction precision/recall or every background job. |
| Ordinary chat and simple tool tasks (`routine`) | Luna as initial preferred candidate | GLM Flash as provisional alternate; DeepSeek after investigation of its observed budget exhaustion, including harness effects | Luna completed 16/16 orchestration attempts, but both O05 traces chose the wrong lookup ID. A preferred candidate is not permission to trust arbitrary tool arguments. GLM evidence remains partial. |
| Bounded source-grounded synthesis (`research`) | Luna and DeepSeek in the first acceptable group; choose by conservative request cost | Sol for materially demanding synthesis after task classification and entitlement checks | Both economical models completed 16/16 synthesis attempts. Luna has citation omissions; DeepSeek's full human adjudication remains pending. Sol is more expensive and is not universally more complete. |
| Clearly demanding reasoning or multi-step evidence chains (`reasoning`) | Sol as the measured premium candidate for a narrow follow-up | Other existing flagship candidates remain unmeasured; no automatic expansion to Astra/Opus justified by this pilot | Sol followed O05 pointers correctly, but these fixtures do not certify general coding, long-context reasoning or difficult research. Existing broad candidate lists are hypotheses, not proven backups. |
| Council (`council`) | Preserve the direction in MODEL_ROUTING.md and existing roster preferences | No new seat promotion from this pilot | Luna and Sol share a developer and cannot supply two independent developer perspectives. At least three actually served developers remain required. |

For routine/background, make the first group Luna-only if the approved intent is
an initial Luna preference; merely placing Luna first inside the existing cheap
group cannot be relied upon to prefer it. The selector minimizes bounded cost within the first
eligible group. For synthesis, keeping Luna and DeepSeek together deliberately
allows that cost comparison. Alternative groups remain provisional and require
their own workload acceptance before production enablement.

Proposed target group shape is explicit: background `[Luna] -> [accepted routine-
class utility alternatives]`; routine `[Luna] -> [accepted routine-class tool
alternatives]`; research `[Luna, DeepSeek] -> [Sol]`; narrow reasoning `[Sol]`.
An empty accepted-alternatives group is omitted, with a truthful unavailable result
when no qualified first-group route remains. Do not inherit the existing demanding
fallback group as an accidental routine-cost escalation. This intentionally leaves
single-endpoint concentration until an alternate earns acceptance; it does not
claim redundancy for Luna. Exact alternate membership depends on the follow-up gates.

Gemini Flash, GLM flagship, Qwen Max, Sonnet, Opus and Astra remain unmeasured by
this pilot and would not enter these proposed measured automatic groups merely
because they appear in the existing catalog. Their catalog metadata and separate
council consideration need not be deleted. This proposed group redesign requires
explicit approval before changing configuration.

Routine/background placement requires independently approved **routine-class**
routes; placement does not reclassify a premium route. All table measurements used
the pilot's standard request parameters. In particular, Sol's general reasoning
profile and xhigh preset were not validated by this evaluation.

Mercury is not currently in the workload catalog. Adding it is a separate reviewed
catalog change, not a conclusion that utility evidence establishes orchestration
suitability. Its single endpoint and observed timeout limit redundancy options.

## Escalation rules

1. **Classify before dispatch:** use task requirements such as multi-step evidence
   dependencies or substantive reasoning; length alone is not complexity.
2. **Ordinary work stays routine-funded.** Do not change `routine` or `background`
   to allow premium spend silently. Demanding work must enter an already
   premium-capable profile and pass account entitlement and budget checks.
3. **Distinguish endpoint failure from model inadequacy.** A timeout does not prove
   the answer requires a stronger model. Try a qualified same-model endpoint only
   under the bounded transport rules below.
4. **Do not add automatic semantic repair in this increment.** A schema-valid
   answer may still omit facts; there is no general correctness oracle in the
   current router. A future validation-triggered repair/escalation needs a specific
   detector, call/output limits and accounting design before implementation.
5. **Exact manual selection stays exact.** No silent model upgrade or cross-model
   retry. Capacity denial remains an account-capacity outcome, not provider failure.

The existing profile group traversal primarily handles eligibility and availability;
it does not establish a quality-feedback loop. This proposal does not authorize
an unbounded sequence of cheap attempts followed by premium attempts.

## Same-model endpoint redundancy: bounded integration proposal

Retaining the mechanism is already approved. Production integration details here
remain proposed; the implemented sequential loop currently lives in the evaluation
runner, while the private runtime seam performs one dispatch per call.

- Start with nonstreaming, no-tool utility completions, matching the live recovery
  evidence. Streaming/tool-loop integration requires separate boundary tests.
- Maximum two sequential provider dispatches for one logical completion, including
  any retry at lower layers. Never stack this with another model-fallback loop to
  multiply the attempt cap.
- Only typed eligible pre-output transport failures: 429, 502, 503, 504, connection
  failures and timeouts. No retry on auth, privacy, invalid request, accounting,
  cancellation, unknown failure or emitted output/tool activity.
- Pin one independently qualified provider per outbound call. Preserve
  `allow_fallbacks=false`, ZDR, no collection and required-parameter enforcement.
- Recheck qualification and capacity at each dispatch, use separate reservations
  and settlement, and retain full conservative charges for unknown usage.
- Check the sum of possible call bounds before logical admission; this is not an
  atomic two-call reservation. Later ledger denial stops as capacity exhaustion.
- Preserve a finite overall deadline, per-dispatch remaining-time clamp and durable
  cooldowns. The experimental 45/90-second values are not automatically proposed
  as interactive production latency targets; choose targets by workload before
  activation.
- Initial proven pair is GLM Inceptron/Together. Prefer neither order as a universal
  default on eight comparisons per arm. GPT has only one evaluated pin (`azure/eu`);
  other endpoints are not automatically qualified backups. Mercury has no evaluated
  alternate. Never expand privacy permissions to obtain redundancy.

Record actual model/provider, every attempt charge and separate recovered failures,
cooldown-avoided dispatches, capacity stops and terminal failures. Do not change
public API/SSE contracts or add a persistence schema as part of this proposal.
The durable cooldown storage choice is an implementation decision still to resolve.

## Focused verification before configuration rollout

1. Correct O05 only in a separately versioned fixture (#335): match fetch arguments
   and return an explicit missing-document result for unsupported IDs. Preserve
   the original fixture, hashes and observations; a new test is not a replay.
2. Clarify whether O06 constrains tool invocations or model dispatches; disclose any
   intended tool limit in the new prompt. Sol used four searches within three model
   calls, so a runner call-budget overrun must not be claimed.
3. Compare the chosen production parameter presets on the focused lookup,
   citation and empty-result cases. The pilot's explicit pins used standard
   request parameters, not proof of the workload catalog's low/high/xhigh presets.
   Do not transfer default-effort results to a new reasoning setting as measured fact.
4. Validate the actual routing profile/endpoint selection and bounded accounting
   through the application path, including denial, cancellation and no-replay cases.
5. Qualify deployment routes afresh: evaluation approvals expire October 1 and
   belong to the isolated pilot. Production still has no approved inference routes.

The follow-up design must freeze acceptance criteria before paid execution:
sample sizes, held-out cases required by MODEL_ROSTER_EVALUATION.md, applicable
slice thresholds (including whether to retain the proposed 15/16), human
adjudication requirements, hard-violation rules and latency/cost bounds. These
are explicit open parameters, not a license to promote on a few corrected cases.

No additional paid test batch is launched by this document. Any follow-up needs a
bounded plan under explicit funding/period authority; unused September authorization
is not rollover funding. Existing repository-wide release blockers remain in force.

## Approval boundary and recommended sequence

Approve the candidate placement and pre-dispatch escalation direction first.
Then specify a narrow follow-up using corrected fixtures and intended presets.
After that evidence, implement reviewed config changes and the first bounded
endpoint-fallback integration with meaningful regressions and independent review.
Deployment, API/schema changes, budget-policy changes and renewal of production
provider approvals remain separate decisions.
