# Same-model endpoint redundancy — proposal

Date: 28 September 2026. Status: **approved experiment executed; human quality adjudication pending**.

Measured outcomes and final accounting are in
[MODEL_ENDPOINT_RELIABILITY_RESULTS.md](MODEL_ENDPOINT_RELIABILITY_RESULTS.md).

The user requested endpoint research and a bounded fallback proposal following
the single-endpoint pilot, then explicitly approved the design and bounded GLM
reliability test, subject to resolving blocking review findings. Existing results
remain unchanged. Implementation and the bounded live experiment are complete; this does
not grant a new evaluation budget or production activation.

## Post-experiment decision — approved direction

On 28 September 2026, after reviewing the completed comparison, the user accepted
the recommendation to **retain bounded same-model endpoint redundancy as a
reliability mechanism**. Preserve exact model selection, separately qualified
endpoints, one provider per dispatch, eligible pre-output transport failures only,
bounded attempts/deadlines, cooldowns and separate reservation/settlement for every
dispatch, including conservative charges for unknown usage.

The implemented fallback loop remains in the evaluation runner. Production
integration and endpoint ordering remain separate decisions, informed by broader
quality, latency and cost evidence; the comparison does not certify semantic
quality. This acceptance does not activate routes or extend the completed
experiment, its funding period or its qualification approvals.

## Goal and existing contracts

Improve availability when an exact selected model has multiple independently
qualified serving routes. A provider failure must not silently substitute a
different model, expand data-processing permissions, or erase failed-call cost.
This supports AC07 (qualified data-policy routing), AC09 (no duplicate material
effects), AC12 (bounded compute), and DEC09 (truthful capacity interruption).

Current code deliberately requires one provider per outbound request:
`TransportPrivacy.rejection_reasons` / `as_transport_kwargs` in
`orchestrator/entitlements/policy.py`. `guarded_completion` in
`orchestrator/compute_runtime.py` selects routes by model; explicit-model failures
terminate rather than trying another route. The original live pilot additionally
requires exactly one eligible route per model. Merely adding another approved
route does not implement the proposed behavior.

## Endpoint evidence and proposed pool

Public APIs were fetched in full and intersected by exact model ID and provider
tag on 28 September 2026. GLM had 33 endpoint records, 28 intersecting the ZDR
list; DeepSeek had 27 records, 22 intersecting it; Mercury had one, also in ZDR.
These are dated discovery counts, not counts of approved or operational routes.

| Exact model / provider tag | Status | ZDR list | Training / prompt retention / publishing | Observed USD per million input / output | Disposition |
| --- | --- | --- | --- | --- | --- |
| GLM 5.3 Flash / `inceptron/fp8` | 0 | Yes | false / false / false | 0.11 / 0.45 | Existing primary; repeated live shared-pool 429s |
| GLM 5.3 Flash / `together` | 0 | Yes | false / false / false | 0.15 / 0.50 | Recommended alternate for isolated evaluation qualification |
| GLM 5.3 Flash / `fireworks` | 0 | Yes | false / false / false | 0.15 / 0.50 | Additional eligible candidate; not needed for initial two-route experiment |
| GLM 5.3 Flash / `cloudflare` | 0 | No | false / **true** / false | 0.30 / 1.00 | Excluded by current retention policy |
| DeepSeek V4.1 Flash / `fireworks` | 0 | Yes | false / false / false | 0.22 / 0.66 | Public privacy/capability checks pass; exact serving-region evidence still pending before execution here |
| Mercury 2.5 / `inception` | 0 | Yes | false / false / false | 0.04 / 0.15 | Existing sole OpenRouter provider; no second OpenRouter route |

The proposed GLM pair advertises `tools`, `tool_choice`, `response_format`,
`structured_outputs`, and all four tool-choice modes. Its context/output limits
exceed the proposed common 16,000/4,096-token experiment limits. These catalog
facts are not measured endpoint quality. Together's privacy policy states no
training without opt-in and offers ZDR; OpenRouter-specific no-retention metadata
and exact ZDR-list membership provide the evidence for this transport, not an
assumption that every direct Together account defaults to ZDR.

Together/Fireworks provider records give US headquarters but no datacenter field;
do not infer serving location from headquarters. CoreWeave records US datacenters
and Inceptron records FI. Shared infrastructure independence is not established.
Fireworks serves both GLM and DeepSeek; it is not a DeepSeek-exclusive provider.

Together and Fireworks rows above have `discount: 0` in the observed catalog.
Mercury's row has `discount: 0.8`; its observed current rates are not a promise of
post-promotion pricing. The existing Mercury pilot pins those current rates as
hard ceilings, so a price increase must deny rather than silently enlarge spend.
No inferred future discount or list price is needed for the proposed GLM pair.

For evaluation approval, propose the existing Inceptron ceiling 0.11/0.45 and a
Together ceiling 0.15/0.50, exact provider pins, unchanged account privacy settings,
and expiry at the original funded period end (2026-10-01T00:00:00Z). Refresh all
public evidence and check endpoint uniqueness immediately before smoke. No new
route is activated by this document. Public qualification is complete for the
proposed GLM alternate; live compatibility and explicit activation remain pending.

Sources:

- [GLM endpoints](https://openrouter.ai/api/v1/models/z-ai/glm-5.3-flash/endpoints)
- [DeepSeek endpoints](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints)
- [Mercury endpoints](https://openrouter.ai/api/v1/models/inception/mercury-2.5/endpoints)
- [Full ZDR list](https://openrouter.ai/api/v1/endpoints/zdr)
- [Provider policy records](https://openrouter.ai/api/frontend/v1/all-providers)
- [Together privacy policy, including section 2.6](https://www.together.ai/privacy)
- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)

Complete discovery payloads are retained under
`/tmp/opencode/roster-live-20260927/redundancy-*.json`.

OpenRouter documents base provider slugs as matching their variants. Where the
catalog only exposes a bare tag such as `together`, verify there is only one
applicable endpoint for that model and fail qualification if new variants make
the intended pin ambiguous. A display-name echo alone cannot attest a subroute.

## Recommended approach: application-managed sequential fallback

1. Select the exact model using the existing workload/manual-selection rules.
2. Build an ordered pool of approved routes for that **same exact model**. Every
   member must independently satisfy privacy, expiry, capability, token-limit,
   commercial entitlement and price-ceiling checks. Equal model names do not prove
   equal serving quality; retain route-specific evaluation evidence.
3. Select an explicit route ID internally. Keep outbound `provider.only` and
   `provider.order` pinned to that single endpoint, `allow_fallbacks=false`,
   `require_parameters=true`, `data_collection=deny`, and `zdr=true`.
4. Reserve that dispatch's route-specific worst-case charge through the existing
   account ledger, then dispatch with SDK retries disabled.
5. On an eligible failure, settle that dispatch before considering one different
   approved route. Unknown usage retains the full conservative charge. Recheck
   policy, period, deadline and available funds before reserving the second call.
6. Record both dispatches, their endpoints, failure categories, usage evidence,
   charges and timings. Return the second response only if valid and still within
   the operation's authority. No output or tool effects from the first dispatch
   may be replayed.

Manual model choice stays exact. An explicit provider/route pin, where requested,
stays exact too: failover must be separately enabled for that operation. Existing
callers retain current behavior by default.

### Proposed bounds for the first reliability experiment

- At most **two sequential provider dispatches per logical model call**, using
  distinct route IDs; no speculative parallel calls and no same-route retry loop.
- Common overall dispatch deadline: **90 seconds**, with at most **45 seconds per
  endpoint**, clipped by the remaining deadline. These are proposed experiment
  settings, not a silent change to the original pilot's 90-second single-call
  allowance. Test controls must include a matched 45-second single-endpoint arm
  so endpoint redundancy is not confused with a deadline change.
- Existing three-model-dispatch case budget counts **every** actual dispatch,
  including failed ones and failovers. Do not double the case budget by renaming
  retries as transport overhead. Use single-call utility/synthesis fixtures for
  the initial live redundancy experiment; orchestration needs a separate budget
  decision before claiming comparable quality improvements.
- Keep the original USD 25 **aggregate** account cap and original funded period;
  no trial money, grants or month rollover. A conservative upper bound for both
  proposed calls must fit the experiment's remaining allowance at admission.
  The authoritative ledger still admits each sequential reservation, so concurrent
  spending may prevent the second call; that is a truthful capacity stop.

### Eligible failures and stop conditions

Proposed retryable class: provider HTTP 429, 502, 503 or 504; connection failure or
timeout before any output/tool event is released. A timeout may mean the first
provider performed billed work; fallback is not a refund or exactly-once inference
guarantee. It is permitted only for inference with no uncertain external effects.

Stop on authentication/payment errors, invalid requests/schema parameters,
qualification/period/budget denial, served-model/provider drift, cancellation,
settlement failure, or unknown unclassified exceptions. Semantic errors and
schema-invalid answers remain quality failures rather than hidden provider retries.
Once streaming output or tool events have been released, stop failover. Tools
already committed to conversation history are not re-executed; uncertain tool
effects block replay and require reconciliation.

Honor provider retry guidance by marking that route unavailable until its retry
time; do not sleep and spin inference or route another call through the same pin.
Persistent health/circuit-breaker state across workers is a later architecture
decision. The first bounded evaluation can use process-local health evidence and
must label that limitation.

## Why not enable OpenRouter-managed fallback first?

A provider-managed approved allowlist can be useful, but it hides intermediate
provider attempts from the application's existing per-dispatch reservation and
audit model. It would also require changing the current single-pin transport
contract. Application-managed fallback preserves those per-request controls and
makes failed-attempt cost and recovery observable. It still depends on OpenRouter
availability, and different provider names alone do not prove infrastructure
independence.

## Approved implementation seams — verification required

### Review resolutions: exact implementation contract

Failover enablement lives only in the new evaluation runner
`scripts/model_endpoint_reliability.py`, through its explicitly selected arm.
`guarded_completion` gains internal `_route_id` and `_dispatch_timeout_s` arguments
for a **single** dispatch, not an inherited failover flag. Ordinary callers,
agents and subagents retain their existing behavior. The runner never delegates
an implicit two-attempt operation to a single runtime call.

Each runtime invocation therefore corresponds to exactly one durable dispatch
row. The runner writes intent first and records its exact route, monotonic timing,
sanitized failure metadata, raw synthetic response/usage when available, reserved
bound and ledger evidence. `ComputeUnavailable` exposes typed `category`,
`status_code`, `retry_after_seconds`, and `retryable` metadata; raw error strings
are not retry classifiers. The last selected route is corroboration, not an
attempt history. PostgreSQL remains authoritative for settled cost.

The case limit is **three actual provider dispatches total**, never three logical
calls with two dispatches each. These selected utility cases need one terminal
model response and no tools; the redundancy arm permits only two dispatches.
Smoke calls also count toward the experiment's global 34-dispatch/financial caps.

Before a primary-then-alternate logical attempt, check the sum of both possible
dispatch bounds against remaining experiment allowance and account funding. This
is a conservative eligibility check, **not an atomic two-call ledger hold**.
The existing ledger separately authorizes each dispatch and may deny the second
after concurrent account spending. Record that as `capacity_stop`, distinct from
provider unavailability, and include it separately in arm comparisons. Each
single-endpoint arm checks its one-call bound under the same rule. There is no
claim that failover is guaranteed once the first reservation is accepted.

| Arm | Mode | Endpoint timeout | Overall logical deadline | Maximum dispatches |
| --- | --- | --- | --- | --- |
| Primary only | Non-streaming utility, no tools | 45 seconds | 45 seconds | 1 |
| Alternate only | Non-streaming utility, no tools | 45 seconds | 45 seconds | 1 |
| Primary then alternate | Non-streaming utility, no tools | Up to 45 seconds each | 90 seconds total | 2 |

Each qualification smoke also has a 45-second deadline and one dispatch.

The original 90-second single-endpoint pilot is historical context, not a matched
control arm. Compute each dispatch's timeout as `min(45, logical_deadline-now)`;
the runtime clamps it against its own remaining deadline and applies the same
bound to the SDK timeout and asynchronous wait. Pacing and settlement consume
the overall logical deadline. No new streaming behavior is enabled by this pilot;
the existing no-fallback-after-emission contract remains intact.

The separate runner owns route-specific preflight for the two explicit route IDs.
It must not call the original pilot's single-eligible-route `pinned_routes` or
`verify_candidate` helpers against a two-route policy. The original runner,
fixtures, state identity and no-replay rules remain unchanged.

Cooldown evidence is a durable evaluation-state map from route ID to validated
UTC earliest-retry time, consulted before every dispatch including backups.
Accept only finite nonnegative retry delays; cap the stored deadline at the
original funded-period end, after which the period guard forbids all dispatches.
This avoids representing unbounded timestamps without retrying earlier inside
the authorized period. Malformed retry guidance must not enable immediate retry.
The predetermined arm order does not change during a cooldown. A blocked sole
endpoint yields an explicit skipped/unavailable row; a blocked backup yields
`backup_cooldown` rather than an unexplained error. A skipped primary with a
successful backup is an avoided dispatch, not recovered failure. Restart cannot
erase the cooldown or manufacture a fresh attempt identity. This is local to the
evaluation runner, not a distributed production circuit breaker.

- Add an internal exact-route constraint in compute selection. The two-dispatch
  same-model failover walk lives only in the evaluation runner, separate from
  manual model selection and automatic cross-model escalation. Do not mutate
  global policy files between attempts.
- Preserve structured, sanitized failure categories/status/retry metadata at the
  adapter boundary. Current generic `ComputeUnavailable` text is insufficient for
  safe retry decisions; do not classify failures by exception-message substrings.
- Keep reservation/settlement per dispatch and the original period guard. Persist
  dispatch intent before sending, distinguish logical calls from provider attempts,
  and retain terminal/interrupted records. Restart must not replay uncertain work.
- Introduce a separate reliability-run artifact with run/arm/case/repeat/dispatch
  identities and immutable policy/fixture hashes. Original pilot attempt IDs and
  scores are not reused. A shared account cap does not by itself deduplicate two
  independent state files.
- No dependency, database migration or public API/SSE change is proposed for the
  evaluation increment. If implementation reveals one is needed, return for
  approval rather than expanding the scope.

## Separate reliability experiment

First verify each alternative endpoint independently with a bounded smoke. Then
use matched fixture/output limits and interleaved, predeclared arms:

1. Primary endpoint only.
2. Alternate endpoint only.
3. Primary then alternate under the bounds above.

Proposed first live scope, contingent on a second qualified GLM endpoint: utility
fixtures U01/U03/U05/U08, two repeats in each arm. That is 24 logical attempts,
at most 32 provider dispatches, plus one smoke per endpoint (34 maximum). The
incremental ceiling is **USD 1 within the existing USD 25 aggregate cap**, not new
funding. Preflight must prove the sum of worst-case dispatch bounds fits both
ceilings; otherwise reduce the predeclared case count before running. Enforce
this sub-cap cumulatively from durable experiment reservations/charges, including
unknown costs and smoke calls, rather than trusting a reported subtotal.

Use a fixed recorded interleaving schedule and bounded request pacing. An active
rate-limit cooldown yields an explicit unavailable/skipped scheduled dispatch;
do not silently replace the arm, delay only its bad cases, or keep burning
attempts. In the failover arm, selecting the backup because the primary is already
cooling down is an **avoided primary dispatch**, not a recovered failed dispatch.
Report these separately. Eight cases per arm are exploratory and do not inherit
the original 15/16 screening threshold.

Report first-dispatch availability, recovered failures, final completion and
semantic correctness, deadline failures, latency, number of provider calls,
conservative ledger cost, and separately known provider invoice cost. Include all
failed calls. A completed response is not a human quality pass. Small samples and
changing shared-pool limits preclude a production SLA claim.

Deterministic fault-injection tests must establish failover safety even if no live
failure occurs: eligible 429/503 recovery, first-call timeout charged conservatively,
expired or unqualified backup, insufficient budget, cancellation, settlement error,
no fallback after emitted output, and restart/no duplicate tool effects. Verify
same-model exactness and that unsupported parameters do not get silently dropped.

Endpoint qualification evidence and the concrete proposed live case count/spend
ceiling must be recorded before implementation/live-arm approval. If no second
qualified endpoint exists for a model, mark redundancy unavailable for that model
rather than substituting another model or relaxing qualification.

## Implementation and verification status

`scripts/model_endpoint_reliability.py` implements the separate experiment.
It requires explicit evaluation `DATABASE_URL`, `DAEMON_COMMERCIAL_CONFIG`, and
`DAEMON_INFERENCE_POLICY` environment settings; do not use production policies or
copy credential-bearing URLs into command logs. The CLI requires `--account`,
`--period 2026-09`, `--primary-route`, `--alternate-route`, a private `--state`
path, and a new `--results` path. Use the same state and immutable configuration
across phases; a new state is not permission to rerun the experiment.

1. `--phase smoke --dry-run`: validates the isolated account, route qualification,
   case bounds and ledger exposure without inference or reservations.
2. `--phase smoke`: one 45-second smoke per endpoint. Both must succeed before
   the run phase is admitted; recorded uncertain/failed smokes are not replayed.
3. `--phase run`: executes the fixed interleaved schedule, retaining cooldown
   skips, transport failures, capacity stops and pending semantic verdicts.

The results use `model-endpoint-reliability-results/1`, not the original roster
scorer format. A provider call is never a fabricated human quality verdict.

Before live execution: 108 runtime tests and a 258-test accounting/roster set
passed, including PostgreSQL integration without skips. The combined runner/runtime
suite passed 146 tests. Runtime static review accepted the exact-dispatch seam;
runner review and final integrated checks remain pending. The real-database
read-only preflight admitted a 34-dispatch worst-case bound of USD 0.067690,
with USD 0.038181 prior account exposure and zero open holds. No reliability
smoke or arm has executed at this checkpoint.

### Live smoke checkpoint — blocked on smoke output allowance

Final integrated checks passed 297 tests (including PostgreSQL and real-context
run-level interruption/resume), isolated type checking, pre-commit and scoped
security checks. Independent runtime and runner reviews accepted the implementation
after fixing resume double-counting, attribution scope lifetime, and post-pacing
timeout calculation.

Fresh endpoint/privacy/price evidence passed before the two authorized smokes.
Together completed in 2.51 seconds with the exact model/provider and a terminal
`ok`. Inceptron responded in 4.17 seconds but returned `finish_reason=length` with
no final answer after exhausting the runner's 64-token smoke allowance. The live
comparison cases have a 4,096-token allowance; the small smoke allowance was an
adapter choice and is not evidence of general endpoint incompatibility.

Both smoke records are preserved; neither was replayed. The runner correctly
denies `--phase run` because the primary smoke did not complete. No comparison
arm has run. Reliability smoke ledger charge is USD 0.000037; total pilot account
charge is USD 0.038218, with zero open holds. Private evidence is in
`reliability-state.json` and `reliability-smoke-reconciled.json` under the existing
evaluation artifact directory.

The user approved this correction: one explicitly identified qualification
recheck of Inceptron with the same 4,096-token output ceiling as the cases,
retaining the failed initial smoke and the original account baseline. This adds
one dispatch (35 maximum including the original two smokes) within the unchanged
USD 1 experiment sub-cap and USD 25 aggregate cap. Do not reset state, overwrite
the original smoke, or label the recheck as the original attempt. Preserve the
original immutable plan and record a separate, validated one-time amendment tied
to its hash. The amendment permits only the primary 4,096-token recheck, without
granting any additional fallback retries or replacing the budget baseline.

### Authorized recheck outcome

The one-time amendment passed 301 integrated tests, isolated type checking,
pre-commit, and independent acceptance review. Read-only preflight reported a
35-dispatch worst-case bound of USD 0.069598 without changing the existing state.
The distinct `smoke-primary-recheck-4096-v1` was then sent with 4,096 output tokens
and received a typed upstream HTTP 429 after 6.76 seconds. Unknown usage retained
the full USD 0.001908 reservation charge. No recheck replay was attempted.

The original two smokes, original plan identity, and frozen USD 0.038181 account
baseline remain intact. Reliability accounting reconciles USD 0.001945 across
three dispatches; aggregate account charge is USD 0.040126, with no open holds.
No comparison logical has run. `reliability-latest-results.json` records this
blocked checkpoint. The strict two-successful-smokes gate remains in force.

The user subsequently approved a narrow admission exception: accept the prior
successful exact-pinned Inceptron pilot response `O01-glm-flash-r2` as compatibility
evidence while preserving the new retryable 429 as availability evidence. Together's
successful current smoke is still required. This adds no qualification call and
does not change dispatch, spending, privacy or cooldown controls.

For first run admission, `--phase run --primary-evidence <original-pilot-state>`
validates the prior completed terminal response, exact requested/served model,
outbound provider pin, observed provider echo, and matching account, funded period,
database, fixture and commercial-policy identity. It records the source/call hashes
and a hashed admission rule tied to the existing plan and primary route. The
exception is allowed only for the recorded primary recheck's HTTP 429 and before
any logical attempt starts. Resume uses the retained admission record without
replaying qualification calls. The original smoke/recheck failures and account
baseline are unchanged. A dry run validates this evidence without writing state.
