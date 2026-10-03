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
| `z-ai/glm-5.3-flash` | `inceptron/fp8` | routine | 1,048,576 / 131,072 | 0.15 / 0.45 |
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
5. Renew both approval and operator review before **2026-10-06T00:00:00Z**, with
   refreshed evidence. At expiry all eight routes fail closed. Do not merely
   extend dates without requalification.
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
