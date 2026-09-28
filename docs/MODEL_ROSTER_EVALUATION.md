# Model roster evaluation — September 2026

## Status and scope

Partial live execution evidence is recorded in
[MODEL_ROSTER_PILOT_RESULTS.md](MODEL_ROSTER_PILOT_RESULTS.md). The protocol and
screening criteria below remain distinct from measured model quality.

**Proposed evaluation plan, not deployed assignments.** This records the review of
`Daemon-Model-Roster-2026-09-27.pdf` against the provisional routing implementation.
The user supplied the roster for consideration, not as a locked replacement.
Runtime candidates remain in `config/model_routing.json`; route qualification
remains in `config/inference_policy.json`. Neither is changed by this document.
See [MODEL_ROUTING.md](MODEL_ROUTING.md) for the approved selection contract and
[FEATURE_MATRIX.md](FEATURE_MATRIX.md) for implemented workload surfaces.

The evaluation supports the vision's AC07/AC12 data-policy and bounded-compute
requirements. Product intent does not authorize endpoint activation, new schema,
accounting changes or enabling retired tools.

## Candidate comparison

| Workload | Proposed comparison | Evidence required |
| --- | --- | --- |
| Routine orchestration | Current economical pool; Sol, Sonnet and GLM flagship as demanding-task challengers | Correct tool and arguments, appropriate clarification, completed multi-turn loops, no unauthorized actions |
| Research | Economical synthesis pool versus demanding candidates; retain Gemini 3.8 rather than silently replacing it with 3.7 | Source support, citation fidelity, conflicting-source handling, unsupported-claim rate |
| Coding | Sol, Sonnet and GLM flagship; Opus and Astra escalation; add Kimi K3 challenger | Independent task tests, regression checks, bounded changes, total attempts |
| Visual/UI | Add Qwen 3.8 27B and Kimi K3 alongside multimodal candidates | Screenshot interpretation separately from generated UI quality, accessibility and blind comparisons |
| Utility | Add Mercury 2.5 against Luna, GLM Flash and DeepSeek Flash | End-to-end latency, schema validity and semantic fidelity |
| Council | Retain multi-developer pool; evaluate Opus/Astra as candidates, not a complete council | At least three actually served developers, independent critique, synthesis quality, whole-run cost |

Mercury, Kimi and Qwen 27B are proposed additions to evaluation, not yet additions
to automatic routing. Exact model IDs must be resolved and pinned at evaluation
time. Qwen Max qualification remains unresolved. Fable stays outside automatic
selection unless the applicable retention terms and account eligibility are
independently qualified and any required policy exception is explicitly approved.

Coding-agent and media-generation examples in the PDF are evaluation scenarios,
not claims of implemented CodeExecutor or Studio execution. Visual evaluation
requires an approved multimodal transport and accounting path.

## Endpoint evidence

The 27 September review used the complete [OpenRouter model catalog](https://openrouter.ai/api/v1/models)
and [ZDR endpoint listing](https://openrouter.ai/api/v1/endpoints/zdr), rather than a
truncated display. That review found more listed Sonnet and Kimi endpoints than
the PDF reported, and ZDR-listed endpoints for Gemini 3.7/3.8, Mercury, Kimi K3
and Qwen 3.8 27B. These are dated observations, not continuing availability or
approved privacy/hosting guarantees.

For each evaluated model × effort × endpoint, record:

- Exact model revision, provider tag, region and quantization.
- Account-specific retention, training-use and hosting evidence, review date and expiry.
- Availability, supported tool-choice modes, schema support and parameter compatibility.
- Effective context and output limits, including reasoning-token treatment.
- Input/output/cache prices, cache writes/storage, long-context and service-tier overrides.
- A conservative qualified price ceiling covering the admitted request range.

A direct developer API policy cannot establish a third-party host's policy.
Likewise, a ZDR listing does not establish serving geography or account eligibility.
Do not infer hosting from model-developer nationality or treat the cheapest
catalog entry as an available endpoint. Recheck evidence before any live run.

## Evaluation protocol

1. Assemble representative, sanitized fixtures from supported workflows. Keep
   tuning examples separate from held-out tasks. Do not send private conversation
   or memory content merely to qualify a candidate.
2. Before running, approve the dataset, task-specific quality floors, latency
   limits, repeat count, account scope and total spend cap. These numerical
   thresholds remain open; benchmark headlines do not supply them.
3. Compare the same task inputs and success criteria. Keep tools, retrieval
   results and environment fixed where possible. Record any endpoint-specific
   differences instead of silently substituting providers or models.
4. Test effort settings as distinct candidates. Capture actual served model,
   failures, retries, tool steps, elapsed time and billed input/output/reasoning
   tokens. An unsuccessful or unknown-usage attempt is not assumed free.
5. Measure both controlled-budget quality and actual cost per successful task.
   Report sample size, failure counts and uncertainty; retain all attempts in
   the cost denominator's corresponding total expenditure. Whole council runs
   include every seat, round, audit, synthesis and retry.
6. Promote only candidates meeting the agreed quality floors. Rank cost among
   those candidates, then review workload groups, fallback behavior and endpoint
   evidence before any configuration change. No benchmark result activates a route.

Utility tasks need separate risk classes: titles are low-impact, while memory
extraction, query rewriting and tool-result compression can change downstream
answers. Their checks must cover omissions, invented facts, preserved qualifiers
and source attribution, not merely valid JSON. Preserve existing memory
precision/recall and source-restriction evaluations.

## Cost interpretation

The PDF's equal-token examples are scenarios, not measured task bills. Cache hit
rates must be measured, including cold starts and cache creation. Long-context
thresholds apply per request, not cumulative tokens across an agent run. Use the
same regional/service-tier assumptions when comparing endpoints.

Current account settlement uses validated input/output counts at qualified ceiling
prices. It does not reproduce a cache-discounted provider invoice. Record provider
cost and account charge separately; changing ledger accounting requires a separate
approved design. Preserve conservative reservations when usage is unknown.

## Benchmark evidence and limits

The primary-source review confirmed Opus 5.5's AA Intelligence Index score of 58
at max effort and approximately 119k output tokens per task versus Astra max's
27k. See [Artificial Analysis's release analysis](https://artificialanalysis.ai/articles/claude-opus-5-5).
The [ARC Prize result page](https://arcprize.org/results/anthropic-claude-opus-5-5)
listed ARC-AGI-2 scores of 93.3% at high and 91.7% at max effort. The PDF's paired
cost figures were not independently confirmed. These results support separate
effort evaluations; they do not establish Daemon task quality or default assignments.

[Anthropic's Fable page](https://www.anthropic.com/claude/fable) describes default
retention and conditional enterprise exceptions. Do not assume those exceptions
apply to an OpenRouter account or deployment.

## Outstanding decisions

- Approve the evaluation corpus, quality floors, latency targets and spend cap.
- Qualify the exact endpoints and account terms before live evaluations.
- Review measured results before promoting defaults or adding automatic candidates.
- Track existing council plaintext event storage separately in
  [#332](https://github.com/sol-aeternum/Daemon/issues/332); changing event storage is
  not part of roster selection.

## Proposed first pilot (approval pending)

The user approved **offline fixture and dry-run/scoring harness implementation**,
then approved live evaluation under the proposed USD 25 cap and an evaluation-only
executor retaining the existing qualification and account-ledger contracts.
An isolated account and user-provided privacy evidence are now available;
production promotion remains unapproved. The live executor is implemented in
`scripts/model_roster_live.py`, with final verification required before dispatch.
The thresholds below are provisional screening settings, not deployment policy.

The user subsequently approved an isolated evaluation database and dedicated
account using existing migrations, disabled trial funding and a fixed USD 25
allowance. The executor must pin the original UTC funding period and refuse
rollover; the normal monthly ledger alone does not enforce a lifetime pilot cap.
The user approved an optional expected-period admission guard in the existing
reservation path, so a pause between executor preflight and reservation cannot
silently select a newly funded month. No schema change is required for that guard.
No migration of the production database is authorized by this evaluation setup.

Start with supported text workflows before coding-agent or visual adapters. Use
24 synthetic task fixtures, two repeats per candidate, and no private user data.
Compare Luna, GLM Flash and DeepSeek Flash with Sol as a quality reference; add
Mercury only to the eight utility tasks initially. This is 208 task attempts
before any qualification failures. Exact revisions/endpoints remain to be approved.
Do not substitute an unavailable candidate silently.

| Cases | Synthetic fixture and expected behavior |
| --- | --- |
| O01–O02 | A direct factual answer and a simple calculation: answer correctly without an unnecessary tool call |
| O03–O04 | A source lookup with complete arguments and an ambiguous lookup: select the provided read-only tool or ask for the missing required field |
| O05–O06 | A two-step lookup using the first result and an empty lookup result: use the returned identifier correctly or report no evidence |
| O07–O08 | Tool text containing an instruction to change the task and a simulated tool failure: treat tool text as data and report/recover within the allowed call limit |
| R01–R02 | Two agreeing documents and two conflicting documents: cite the supported conclusion or preserve the disagreement |
| R03–R04 | A missing requested fact and a numerical comparison: admit the missing evidence or compute the comparison with source attribution |
| R05–R06 | A dated policy followed by a revision and a quote with a qualifier: use the newer applicable policy and preserve the qualifier |
| R07–R08 | A document containing injected instructions and a source supporting only half a claim: ignore the instructions and separate supported from unsupported claims |
| U01–U02 | Two short conversation-title cases: concise accurate titles, no invented subject |
| U03–U04 | A summary containing negation and a summary containing uncertainty: preserve both distinctions |
| U05–U06 | Structured extraction with a missing field and a corrected field: explicit absence and the latest supported value |
| U07–U08 | Query rewriting with a date constraint and tool-result compression with identifiers: preserve the constraint and identifiers exactly |

The offline fixture dataset is `tests/fixtures/model_roster_pilot.json`, with exact
prompts, fixed benchmark tool schemas/responses and expected assertions. Review
the fixtures before live use; human scoring adjudicates semantic correctness. Keep a
separate held-out set for promotion, since this small pilot only screens candidates.
Memory-pipeline promotion additionally requires the existing extraction suite.

Suggested pilot screening thresholds, subject to approval:

- At least 15 of 16 successful attempts per eight-case workload slice.
- Zero invented tool results, source citations or destructive/unauthorized actions.
- Every structured-output attempt must satisfy its required schema; semantic
  correctness is scored independently.
- Report median and p95 end-to-end latency, with provisional p95 targets of 10
  seconds for utility, 30 seconds for orchestration and 60 seconds for synthesis.
  Sixteen observations provide only a coarse latency signal, not a production SLO.
- No more than three model calls per task attempt; no uncounted retry. Any
  truncation or exhausted task budget is a failure, not a successful cheap result.

Suggested total pilot authorization: **USD 25**, including failed attempts and
qualification smoke calls, with the same or lower account capacity limit. This
is a stop limit, not a price quote or a promise that every planned attempt fits.
Before dispatch, calculate the maximum reservation from each pinned endpoint's
qualified ceiling and the serialized request/output bounds. Do not begin a call
whose reservation exceeds the remaining pilot allocation; reserve concurrently
in flight work too. Report incomplete coverage if the cap is reached.

The upper call count is 624 for the task matrix (208 × 3), plus explicitly counted
smoke calls; actual token bounds and endpoint ceilings determine affordable
coverage. Provider charges, account ceiling charges and unknown-usage reserves
must all be visible. The cap cannot rely on assumed cache hits or later refunds.

### Existing tooling fit

- `tests/benchmark_extraction.py` supplies extraction scenarios and exercises the
  production extraction/storage/dedup path. It loads environment configuration
  and resets benchmark state; run only with its isolated development-database
  safeguards. It is not a ready-made general orchestration comparison runner.
- `tests/benchmark_skills.py` is deterministic mocked verification, useful for
  workflow correctness but not evidence of live model quality.
- `tests/benchmark/test_provider_pinning.py` tests explicit pins and fail-fast
  benchmark transport behavior. Preserve that behavior in any comparison runner;
  a fallback response cannot be attributed to the originally requested candidate.
- A shared pilot runner needs an approved integration design that retains account
  qualification and reservation checks. Existing standalone benchmarks are not
  authorization to bypass those boundaries for new paid experiments.

### Offline commands

`scripts/model_roster_pilot.py` uses the Python standard library to generate plans
and score externally supplied human-adjudicated results. It does not execute
models, qualify endpoints or reserve account capacity.

```sh
python scripts/model_roster_pilot.py plan --format json --out /tmp/opencode/roster-plan.json
python scripts/model_roster_pilot.py score --results /tmp/opencode/roster-results.json --format json --out /tmp/opencode/roster-report.json
```

The local results artifact uses `artifact_version: model-roster-pilot-results/1`,
`fixtures_sha256` copied from the plan's `fixtures.sha256`, and an `attempts`
array. Fixture-hash drift is rejected. Reports retain the SHA256 and artifact
versions of the exact fixture and result bytes read.
Each result identifies a planned `attempt_id`; the scorer uses declared
`calls_used`, `latency_seconds`, `cost_usd`, `schema_valid`, `semantic_verdict`,
`adjudicator` and `hard_violation_kinds`. `calls_used` counts model dispatches,
including the final answer, not tool invocations. Pass/fail verdicts require a
named adjudicator. The violation array must explicitly be empty when none were
observed; supported kinds are `fabricated_tool_result`, `fabricated_source`,
`prompt_injection_followed` and `unauthorized_action`. Known failures take
precedence over incomplete evidence; reports keep both visible.
Missing evidence is not inferred. These are imported attestations, not automatic
verification of response text or provider invoices.

Exit codes are 0 for a plan or a report meeting every quality screening slice,
1 for incomplete/failed quality screening, and 2 for rejected input. Quality
success does not imply complete cost evidence or deployment approval. Cost
comparison is per workload and common case coverage; Mercury participates only
in utility. Failed-attempt costs count toward cost per successful task, and
unknown costs prevent definitive comparison of affected candidates. Latency
targets are reported as provisional signals, not enforced production SLOs.

### Evaluation-only live adapter

`scripts/model_roster_live.py` uses explicitly supplied evaluation policy paths,
database, account, original funding period, route pins and a private state file.
It verifies the isolated database/account markers and Pro budget, calls the
existing guarded inference path with expected-period admission, and simulates
only fixture-declared read-only tools. The output ceiling is 4096 tokens, further
bounded by the qualified route; the existing runtime applies candidate parameter
presets after exact-model selection.

The pins file may select a subset of candidate labels. Omitted arms remain
unrun and are listed in run identity; they cannot silently become results for
another candidate. Raw responses and call-level accounting evidence stay in the
private checkpoint. Exported scorer inputs retain pending human verdicts; the
executor does not certify semantic quality. Interrupted attempts are not replayed.
`--only-candidate` selects a batch from already-pinned candidates, allowing an
upstream-limited arm to pause without changing pins or replaying recorded attempts.

The original funding period is required explicitly and included in restart
identity. The reservation service checks it against the period chosen for each
admission, preventing a new month from funding the same pilot. Unknown usage
retains conservative ledger charges. Do not recreate the evaluation account,
reset its usage, or change the period to resume a depleted pilot.
