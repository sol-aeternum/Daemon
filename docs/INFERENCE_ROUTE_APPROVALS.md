# Deployment inference route approvals — 29 September 2026

This is the dated approval record for `config/inference_policy.production.json`,
selected explicitly through the existing `DAEMON_INFERENCE_POLICY` setting.
The portable `config/inference_policy.json` remains deny-by-default. Merging this
record does not select the deployment policy, restart services, or prove that the
running chat incident (#349) is resolved. Runtime remains Z-only; DEC12 does not
authorize retained-data routes here.

## Operator approval and account attestation

On 29 September 2026, the Daemon deployment operator explicitly approved all eight
routes below, their proposed native limits, classes and price ceilings, the
deployment-specific opt-in packaging, and **6 October 2026 at 00:00 UTC** as both
approval and operator-review expiry. JSON review timestamps record the UTC review
date at midnight, not a measured time of account inspection.

The operator confirmed that the deployed OpenRouter key belongs to the same
account/workspace reviewed on 27 September, with these settings unchanged:

- Input/output logging and Broadcast disabled.
- Paid/free model training, free prompt publishing, and workspace data discounts
  disabled.

These are account-scoped operator attestations. No account identifiers or keys
are published. Another installation or account must obtain its own review rather
than treating these booleans as verified for its credentials.

## Approved inventory

Model IDs below omit the `openrouter/` prefix. Prices are maximum USD per million
input/output tokens, covering advertised prompt-length tiers rather than only
the short-prompt base price. Each route supports text, tools and JSON schema.

| Model | Exact provider pin | Class | Context / output tokens | Input / output ceiling |
|---|---|---|---|---|
| `openai/gpt-6-luna` | `azure/eu` | routine | 1,050,000 / 128,000 | 0.22 / 0.825 |
| `deepseek/deepseek-v4.1-flash` | `coreweave/fp8` | routine | 1,048,576 / 393,216 | 0.20 / 0.65 |
| `z-ai/glm-5.3-flash` | `inceptron/fp8` | routine | 1,048,576 / 131,072 | 0.225 / 0.45 |
| `openai/gpt-6.1-sol` | `azure/eu` | premium | 1,050,000 / 128,000 | 4.4 / 16.5 |
| `anthropic/claude-sonnet-5.5` | `google-vertex/europe` | premium | 1,000,000 / 128,000 | 2.2 / 11 |
| `anthropic/claude-opus-5.5` | `google-vertex/europe` | premium | 1,000,000 / 128,000 | 4.4 / 22 |
| `openai/gpt-6-astra` | `azure/us` | premium | 1,050,000 / 128,000 | 22 / 82.5 |
| `x-ai/grok-4.7` | `xai/zdr/us` | premium | 500,000 / 450,000 | 4.4 / 13.2 |

Public endpoint evidence was refreshed at **2026-09-29T12:41:27Z** using read-only
GETs. All eight exact variants had status `0`, exact membership in the
[ZDR endpoint list](https://openrouter.ai/api/v1/endpoints/zdr), and advertised
`tools`, `structured_outputs` and `reasoning` parameters. Per-model endpoint URLs
and provider sources are recorded in each route's `operator_review.evidence`.
The [provider catalog](https://openrouter.ai/api/frontend/v1/all-providers)
supplies provider policy attestations. Availability here means public metadata
checked; account admission, streaming, tools and maximal-length output have not
been live-certified by this approval change.

The GPT ceilings include the 272,000-prompt-token tier; Grok includes the
200,000-token tier and US pricing. DeepSeek's documented 384K output maximum
([model reference](https://www.alibabacloud.com/help/en/model-studio/deepseek-v4-1-flash))
and [GLM Flash's 128K maximum](https://docs.z.ai/guides/vlm/glm-5.3-flash) take
precedence over their hosts' larger 943,718-token catalog values. Other limits
are endpoint-advertised native limits, not evaluation-only 16K/4K restrictions.

Azure qualification relies on the exact regional ZDR listing plus account
attestation, not independent access to Microsoft's resource logging settings;
see [Azure data privacy](https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/openai/data-privacy).
Inceptron commits API payload processing to EU/EEA in its
[privacy policy](https://www.inceptron.io/privacy), with default zero retention
and no training in its [terms](https://www.inceptron.io/termsofservice).
Grok's ordinary provider policy reports 30-day retention: approval is restricted
to the separately ZDR-listed `xai/zdr/us` variant, never generic `xai`.

## Sol 6.1 replacement — 30 September 2026

After receiving the [bounded comparison](SOL_UPGRADE_EVALUATION.md), the operator
explicitly directed: **“swap in sol 6.1 high, deploy locally and create pr.”** This
supersedes the recommendation to retain Sol 6 pending human adjudication. It is
an operator rollout decision, not retrospective semantic acceptance: all 92 human
verdicts remain pending, and the new model was slower/more expensive in the sample.

The existing `sol-azure-eu` route now serves `openai/gpt-6.1-sol`. The provider pin,
ZDR/no-training/account controls, native context/output limits, premium class,
USD 4.40/16.50 long-prompt price ceilings and **6 October 00:00 UTC expiry** are
retained. Only this route's operator-review date advances to 30 September; the
other seven approvals are not renewed. Default, reasoning and council presets
all request **high**; `none` is removed from the supported effort declaration.
Luna-first automatic routing and council developer-diversity requirements remain.

Exact Azure EU endpoint/ZDR metadata was checked on 30 September, and the bounded
comparison completed 46 attempts per model, including two production streaming
tool probes per model. Read-only generation receipts corroborate Azure and
`openai/gpt-6.1-sol-20260929` for the streamed calls. This is not native-limit or
general workload-quality certification. Account attestation above continues to
apply to the same local deployment account; no privacy exception is introduced.

The operator separately confirmed restarting the current local backend/worker
after disclosure that their shared checkout contains newer uncommitted accounting
and search changes. The Sol rollout applies only its three runtime configuration
files to that checkout, preserving the unrelated work. No additional paid smoke
test is included; verify effective policy, presets, roster and service health.

## Sonnet 5.5 replacement — 3 October 2026

After the [Sonnet 5.5 evaluation](REASONING_EVAL_PROTOCOL.md#sonnet-55-result-3-october-2026),
the operator directed: **“swap out sonnet 5 for 5.5”**.

**Evaluation basis:**
- On the reasoning-evaluation pilot cases, Sonnet 5.5 high scored 119/120 with a
  unanimous three-reviewer blind AI panel. Sonnet 5 scored 115/120.
- It cost 10% less per attempt and had a lower p95 latency (8.2 s against 15.9 s).
- Paired gains were not statistically significant, the verdicts are AI-judged rather
  than human, and council use was not evaluated.

The existing `sonnet-vertex-europe` route now serves `anthropic/claude-sonnet-5.5`.

**Retained unchanged:**
- the `google-vertex/europe` pin with fallbacks disabled;
- the ZDR, no-training and account controls;
- the native context and output limits;
- the premium class;
- the USD 2.2 / 11 price ceilings, equal to the listed price;
- the **6 October 00:00 UTC expiry**.

Only this route's operator-review date advances, to 3 October. The other approvals
are not renewed. Presets are unchanged: high for default and reasoning, and high
with reasoning included for council. Sonnet 5.5 takes Sonnet 5's places in the
reasoning demanding group, the council diverse group and the council analyst seat.
The old Sonnet 5 ID is no longer routable.

**Endpoint evidence:** exact Vertex Europe endpoint and ZDR metadata were fetched on
2 October 2026 at 11:38 UTC (`claude-sonnet-5.5-20260928`; Google's policy states
no training and no prompt retention). The 29 September screen recorded
content-filter refusals on this endpoint for one benign fixture (#344), and the
3 October run had none across 138 calls. Account attestation above continues to
apply; no privacy exception is introduced.

## Renewal — 3 October 2026

The operator directed a 14-day bridge renewal while monitored approvals are built
(approvals that stay valid until an endpoint's ZDR status or provider data policy
changes). The rule was to renew a route only if its ZDR status and provider data
policy were unchanged since approval. All eight approvals and operator reviews now
expire **2026-10-17T00:00:00Z**, reviewed 3 October.

**Evidence:** fetched 2026-10-03T04:30Z from public OpenRouter metadata, with
SHA-256 provenance kept privately:
- the endpoint listing for every pinned model;
- the ZDR endpoint listing;
- provider data policies.

**Result:**
- Every pinned endpoint is still listed and still in the ZDR listing.
- Every provider policy matches the policy recorded at approval:
  - no training anywhere;
  - no prompt retention, except generic xAI's 30-day retention, which was already
    recorded and is why Grok is approved only on `xai/zdr/us`.
- Context and output limits still meet the pinned values.

**Not a renewal criterion, recorded for the operator:** the GLM 5.3 Flash
(`inceptron/fp8`) listed prompt price is now USD 0.225 per million, above its pinned
USD 0.15 ceiling (#421). The transport price cap refuses that route until the ceiling
or the price changes. This was the renewal-time finding; the subsequent narrow
operator-approved change is recorded below.

The account-side attestation (prompt logging disabled, training opt-out) is carried
forward unchanged; it cannot be checked from public metadata. Tool-service approvals
are unchanged.
## Monitored approvals (operator decision, 3 October 2026)

The operator decided that an inference route approval should not lapse on a
calendar. It should be revoked only when the endpoint's ZDR status changes. A route
with `"approval_mode": "monitored"` has no `approval_expires_at` or
`review_expires_at`; a date alongside monitoring is refused as ambiguous. It still
needs a named operator review with evidence and a review date that is not in the
future. Its `zdr_baseline` records what it was approved against:
- `provider_slug`: the pinned provider whose published data policy applies;
- `data_policy`: the approved values of `training`, `retainsPrompts` and, where
  relevant, `retentionDays`.

**Check.** The backend and the worker each run the check themselves, at start and
every 6 hours. Neither depends on the other to enforce a revocation. Each fetches two
public OpenRouter listings with no credentials: the ZDR endpoint listing and provider
data policies. Two changes revoke a route:
- the **exact** requested model id at the pinned provider tag leaves the ZDR listing
  (`left_zdr_listing`). A sibling model such as `<model>-pro`, or a dated revision
  listed without the requested id, does not count as listed;
- any baseline data-policy value changes (`provider_policy_changed:<key>`).

An observed revocation applies to that process's admission immediately, before any
database I/O, and lasts for the life of the process even if it cannot be recorded.
Results are then recorded, one row per monitored route, in
`inference_route_attestations` (migration 043). The write is retried, and a revocation
that cannot be persisted is logged as critical. An unreadable check records
`check_failed` and revokes nothing.

**Admission.** Backend and worker read a snapshot of those rows, refreshed every
minute. A monitored route is admitted only if:
- its current baseline has been confirmed within **72 hours**; and
- that baseline has **never been revoked**.

Otherwise it fails closed (`zdr_attestation_stale`, `zdr_attestation_revoked`, or
`zdr_attestation_unknown` before the first snapshot loads). Revocation is sticky for
the approved baseline. Re-approval is an explicit operator change to the route,
normally a new review date after requalification; the route never recovers on its
own.

**Not monitored.** The account-side attestation (prompt logging disabled, training
opt-out) cannot be checked from public metadata and is carried by the operator
review. Price, limits and capability changes are not revocation triggers. The
transport price cap still refuses dispatch when a listed price exceeds the pinned
ceiling (see #421).

**Deploy.** Apply migration 043. Run `python scripts/attest_inference_routes.py`
once against the deployment database before restarting the backend and worker onto
a monitored policy. Its exit status reflects effective admission, not just that
check:
- `0`: every monitored route would be admitted now;
- `1`: some route is stale, unknown or revoked, including a baseline revoked by an
  earlier check;
- `3`: the policy has no monitored routes.

Restart only on `0`. Expiring approvals keep their existing behaviour.

## GLM Flash input ceiling — 3 October 2026

For [#421](https://github.com/sol-aeternum/Daemon/issues/421), the operator explicitly
approved raising **only** `glm-flash-inceptron-fp8`'s input ceiling from USD 0.15 to
**USD 0.225 per million prompt tokens**, retaining the **USD 0.45 per million
completion tokens** ceiling. This is not approval for broader spending, a new
provider, automatic-routing placement, or a live inference test.

Read-only public evidence was refreshed **2026-10-03T05:56:07–05:56:08Z**:

- The [model endpoint listing](https://openrouter.ai/api/v1/models/z-ai/glm-5.3-flash/endpoints)
  and [ZDR listing](https://openrouter.ai/api/v1/endpoints/zdr) both contain the exact
  `z-ai/glm-5.3-flash` → `inceptron/fp8` pair (served name
  `z-ai/glm-5.3-flash-20260826`, status `0`, FP8). Listed per-token prices are
  `0.000000225` prompt and `0.00000045` completion, exactly USD 0.225 / 0.45 per million.
- The endpoint declares 1,048,576 context tokens, tools, structured outputs and
  reasoning support. The configured 131,072 output-token limit is retained, not
  expanded to the host's advertised 943,718.
- The [provider catalog](https://openrouter.ai/api/frontend/v1/all-providers)
  still reports Inceptron `training=false`, `trainingOpenRouter=false`,
  `retainsPrompts=false`, `canPublish=false`, Sweden headquarters and Finland
  datacenters. The [privacy policy](https://www.inceptron.io/privacy) still commits
  Customer Content to EU/EEA processing, default immediate payload discard and no
  training; the [terms](https://www.inceptron.io/termsofservice) still state default
  zero retention and no training without explicit opt-in. This matches the
  previously approved posture; it is provider attestation, not independent
  inspection. Existing account-side attestations are carried forward unchanged.

SHA-256 fingerprints of the fetched response bytes (raw evidence retained privately):

| Source | SHA-256 |
|---|---|
| Model endpoints | `c5807b340ddccf86ef0fac528d35080cb9a0a1776e3b2f14e76953a24da5b61b` |
| ZDR endpoints | `67c6ecb903788f4781d899aa2786be6d3b08ed5d652413ba2e22494ad8391e9d` |
| Provider catalog | `37bb8ac9a752685c7b90352fbf0bae280291c519009f629ffbba77bb2944090d` |
| Inceptron privacy | `a119bf0ef9acc9798407168c759039476132dc9ef218ae4a4a28a9736a41f6cd` |
| Inceptron terms | `b077e18e0dffda0f65a9fc9c1b27c076d25ceba309b5462cd641eecd42664390` |

The production JSON stores **225000 / 450000 microusd per million tokens**.
The existing transport conversion divides each by 1,000,000 microusd per USD,
sending `provider.max_price={"prompt":0.225,"completion":0.45}` (USD per million,
not per-token prices). User pricing, accounting multipliers and budgets are
unchanged; reservations continue using the configured ceiling and existing ledger.

Both approval and review expiry remain **2026-10-17T00:00:00Z**, preserving merged
[#423](https://github.com/sol-aeternum/Daemon/pull/423). The separate in-flight
[#424](https://github.com/sol-aeternum/Daemon/pull/424) monitored-approval change is
not incorporated here. Exact provider/model pins, ZDR/no-training controls,
disabled fallbacks, capabilities and native limits are unchanged. Qualified exact
manual selection and the existing council diverse group remain available; GLM
Flash is not added to routine/background/research/reasoning automatic groups.
Full `z-ai/glm-5.3` remains unapproved.

Mocked regression tests verify the production payload and existing admission paths,
including denial of expired/unapproved routes and broader fallback. No paid
inference or live dispatch test is authorized or performed. No restart or deployment
is part of this change: backend and worker cache policy and still require a
separately approved rollout/reload before this configuration can affect dispatch.

## CoreWeave location decision

The operator accepted CoreWeave's documented North American/European footprint,
including Canada, the UK and Norway, after disclosure that no exact per-request
region guarantee was found. This is a CoreWeave-specific decision.

- [CoreWeave serverless documentation](https://docs.coreweave.com/products/inference/serverless)
  says CoreWeave delivers the service through W&B Inference.
- [W&B Inference](https://wandb.ai/site/inference) says inference runs directly on
  CoreWeave infrastructure and lists DeepSeek V4.1 Flash.
- [CoreWeave's product page](https://www.coreweave.com/products/serverless-inference)
  lists that model and its 0.20/0.65 input/output price, and states that Serverless
  Inference follows a Zero Data Retention policy by default. Tracing is opt-in;
  retain logging-off and exact-route ZDR controls.
- The [official availability-zone inventory](https://docs.coreweave.com/platform/regions/all-availability-zones)
  lists US, Canadian, UK, Swedish, Norwegian, Danish and Spanish locations, with
  no China location listed. `EU-*` zone names do not establish EU membership.

This corroborates infrastructure and footprint beyond OpenRouter metadata. It is
not a measurement of an individual request's datacenter or a contractual
geographic exclusion. The exact OpenRouter provider/FP8 mapping still relies on
its endpoint metadata. Headquarters and the absence of GPU subcontractors from
a subprocessor list are not treated as proof of serving location or ownership.

## Exclusions and unchanged routing

Qwen 3.8 Max was removed from model metadata and reasoning/council candidates by
operator request. Gemini 3.8 Flash remains unapproved because only Global ZDR
variants were found. Full GLM 5.3 remains unapproved: the operator's Fireworks
preference does not establish an accepted geographic exception for its global
endpoint. Neither model has a route in this deployment policy.

Routine/background/research retain Luna-first placement. Qualification does not
promote other candidates into those profiles. The eight approved council
candidates span five developers; this is coverage, not proof of a successful
live council. Search approval is now recorded separately in
[SEARCH_SERVICE_APPROVALS.md](SEARCH_SERVICE_APPROVALS.md); no embedding, speech
or media service is approved by this inference decision.

## Deployment, renewal and rollback

1. Before rollout, verify the intended OpenRouter account/workspace still has
   the attested privacy settings. Refresh exact endpoint/ZDR membership, prices,
   capabilities and hosting evidence, and confirm both expiries are still valid.
2. For this deployment only, set the existing setting to
   `DAEMON_INFERENCE_POLICY=/app/config/inference_policy.production.json`.
   Compose already injects this setting into both backend and worker; no new
   environment contract is introduced. Keep the OpenRouter API base at
   `https://openrouter.ai/api/v1`, matching every route. Regional provider pins
   do not make the global OpenRouter gateway a regional gateway.
3. Recreate **both backend and worker** only under separate rollout authorization.
   Policy loading is cached; editing the file alone does not establish that both
   processes use it. Verify effective settings and policy admission in each.
4. Any paid synthetic smoke test needs its own bounded call count and USD cap.
   Exercise the application accounting path, streaming/usage, tools and JSON
   compatibility; public metadata and local tests do not replace this check.
5. All eight routes are monitored approvals (see above): they carry no calendar
   expiry and fail closed if a ZDR check revokes them or none has succeeded for 72
   hours. Re-approving a revoked route requires requalification and a new review
   date; never re-approve without refreshed evidence.
6. To roll back, restore the prior policy selection (or remove the override to
   select the portable deny-by-default policy) and recreate both processes.
   Returning to that default deliberately restores route-unavailable behavior.

Selecting this policy qualifies eligible background/title/memory/helper calls as
well as interactive chat. Native paid-route limits can reserve large output
holds; existing account budgets, entitlements and settlement remain enforced.
No funds, plan limits, premium eligibility or unqualified tool services are
changed. The original 29 September approval did not restart services or dispatch
paid inference. The separately authorized Sol 6.1 replacement rollout and its
verification are recorded in [SOL_UPGRADE_EVALUATION.md](SOL_UPGRADE_EVALUATION.md).
