# Workload routing and model evaluation

## Authority and status

The routing realignment separates workload suitability, provider qualification,
and account capacity. It implements the capability-first direction approved in
the September 2026 routing discussion, including bounded automatic premium
selection and council diversity. It preserves the commercial/provider contract
in [SUBSCRIPTION_ARCHITECTURE.md](SUBSCRIPTION_ARCHITECTURE.md) and the vision's
data-policy and bounded-compute requirements (AC07/AC12).

| Concern | Source of truth |
| --- | --- |
| Workload candidate pools, preference groups and parameter presets | `config/model_routing.json` |
| Profile validation and request-local routing state | `orchestrator/model_routing.py` |
| Endpoint approval, verified capabilities, effective limits and price ceilings | `config/inference_policy.json` |
| Account capabilities, recurring capacity and premium trial | `config/commercial.json` and `orchestrator/entitlements/` |
| Final selection, reservation, dispatch and settlement | `orchestrator/compute_runtime.py` |
| Council role preferences | `orchestrator/council/roster.yaml` |

Candidate suitability is **provisional pending workload evaluation**. A listed
model is not an approved serving route, an availability guarantee or evidence of
measured task quality. The supplied inference policy still approves no routes.
Public catalog checks on 27 September 2026 inform the candidate pools; they do
not certify account privacy settings or endpoint hosting location.

Routing profiles: `routine`, `reasoning`, `research`, `background`, `council`.

### Operator-selected Sol 6.1 high — 30 September 2026

The operator selected `openrouter/openai/gpt-6.1-sol` to replace the prior Sol
candidate and council strategist after the bounded comparison in
[SOL_UPGRADE_EVALUATION.md](SOL_UPGRADE_EVALUATION.md). Default, reasoning and
council presets use **high**, including replacement of the former reasoning
`xhigh` preset. Sol 6.1 requires reasoning, so its declaration omits `none`.
This is an explicit rollout decision; human semantic verdicts remain pending and
no measured quality superiority is claimed. The same Azure EU deployment route
retains its limits, price ceilings, privacy controls and 6 October expiry; see
[INFERENCE_ROUTE_APPROVALS.md](INFERENCE_ROUTE_APPROVALS.md#sol-61-replacement--30-september-2026).
Routine/background/research remain Luna-only for automatic selection.

### Approved Luna-first configuration — 28 September 2026

The user approved a Luna-only automatic candidate group for `routine`,
`background` and bounded `research`, using Luna's existing low-effort preset.
Configuration and background helper parameter compatibility checks pass, with
independent review accepted. Unaccepted alternate and premium
fallback groups are removed from these three profiles; unavailable or ineligible
Luna yields a truthful unavailable/capacity result. Exact manual selection still
passes the independent qualification and account checks. Reasoning and council
placements remain provisional, with their existing declarations preserved.

The [completed follow-up](MODEL_ROUTING_FOLLOWUP_PLAN.md) records Human's 16/16
acceptable held-out answers for Luna low, Sol high and Sonnet high. Luna had the
lowest equal-coverage cost; this narrow screen found no premium semantic advantage.
It does not certify every memory operation, general coding or demanding reasoning.
Production inference routes remain unapproved; configuration approval is not
deployment or route activation authority.

Pre-activation compatibility finding: background helpers (titles, extraction,
summaries and entity extraction) supplied temperature and/or top_p, while
Luna declares only seed sampling support. The runtime correctly excludes incompatible
requests before spending; with Luna alone these calls have no eligible route.
The user approved removing legacy sampling controls from automatic background
calls. The correction passes 225 combined routing/helper/memory tests with an
ephemeral test-only encryption key, isolated basedpyright and pre-commit.
Independent review accepted the five-helper correction and guard coverage.
Declared model support is not expanded to mask the mismatch. Explicit benchmark/
manual parameter behavior is preserved. Configuration tests alone do not certify
these helper request shapes.
Contradiction detection actually uses the reasoning profile, so its existing
sampling behavior is retained; the earlier blocker description included it too
broadly. Automatic background calls omit sampling controls at the helper, while
explicit model/benchmark injections retain their previous parameters. The runtime's
unsupported-parameter rejection remains intact.

The initial full isolated snapshot suite reported 2664 passed, 23 skipped and two
failures. Subsequent approved repairs address the teardown schema mismatch (#337),
reflection test credential preflight (part of #310), contradiction-test mock warnings
(#336) and summary-worker argument mismatch (#338). The final full run reports
**2712 passed, 3 skipped**, with an explicit isolated test DSN and ephemeral test-only
encryption key; the application database DSN was empty. Final audit/migration checks
pass 17 tests after the isolation-query adjustment. Lint, formatting, collection,
feature matrix and pre-commit passed; isolated type checking passed after fixing
nine new fixture annotation/optional-row errors. Independent reviews accepted all
four repairs. No type baseline was changed in the original repository.
*These counts are dated session evidence captured while the routing realignment was
authored; they are a snapshot, not this branch's gate output. The snapshot evidence
described here predates this branch's integration changes, which are recorded in the
[integration note](#integration-note--luna-routing-pr-28-september-2026) at the end
of this document.*

Migration `040_memory_metadata.sql` adds the previously missing JSONB NOT NULL
column with an empty-object default. The audit now requires an explicit test DSN,
uses a disposable schema and verifies the actual migration contract without a
fixture-only workaround. Migration 040 has not been applied to production.
The older snapshot recorded dependency inventory (#309: 157 vulnerabilities in
20 packages) and Bandit inventory (#313: 6691 low, 33 medium, zero high). These are
historical counts, not the current branch's results; see the integration note below.
A separate application-DSN smoke-test hazard is tracked in #339. Passing this
repair scope does not authorize activation.

In the older checkout, the user approved a summary-worker checkpoint
in existing conversation metadata (`last_summarized_message_count`), without a
migration. Save the pre-generation count with a successful nonempty summary;
concurrent new messages remain eligible. Legacy missing checkpoints get one
refresh. The count tracks scheduling progress, not complete coverage of messages
outside the existing 100-message summary window. That snapshot repair was implemented and
independently reviewed with no blocking findings: 26 dedicated tests pass and the
full isolated suite reports 2712 passed, 3 skipped. Isolated type checking is clean.
Overlapping jobs can still cause an extra refresh; this is not an exactly-once
scheduling contract. Migration/audit isolation review also passed with no blockers.
**This checkpoint implementation is not part of this PR.** Current main already
has a finalized-message cursor (`last_summarized_msg_count`), bounded summary
batches and continuation recovery. This branch preserves that newer implementation
instead of importing the older checkout's competing scheduling checkpoint.

## Roster removal — 29 September 2026

The operator removed `openrouter/qwen/qwen3.8-max-0902` from the model metadata
and the reasoning/council candidate groups after qualification found only an
Alibaba endpoint with prompt retention and no exact-route ZDR listing. The
remaining candidate order and parameter presets are preserved. This removal
does not activate any production route or authorize provider-retained routing.

The separately opted-in `config/inference_policy.production.json` records eight
operator-approved ZDR routes through 6 October 2026. See
[deployment route approvals](INFERENCE_ROUTE_APPROVALS.md) for account scope,
provider evidence, native limits, expiry, and rollout. The portable default
policy remains deny-by-default; merging the deployment policy does not select it.

## Selection contract

Native and compatibility chat derive the workload from the requested work.
Complexity signals take priority over ordinary search/reminder phrases and match
word boundaries, including listed plural, third-person and gerund forms of
single-word signals (past tense is excluded). Conversation length and character
count alone do not establish a reasoning requirement; context fit is enforced
against the actual request.

Signals are read from the user's own instruction text in the current message.
Closed fenced blocks are pasted data and never count; an unclosed fence stays
ordinary text. Blockquote lines count only when nothing else remains, so an
instruction written entirely inside a quote still selects its work. A code block
on its own does not select reasoning; the instruction around it decides (approved
1 October 2026). This is a routing heuristic, not proof of authorship or a
security boundary, and the model always receives the full message. Signals are
English-only. The frozen characterization is `tests/fixtures/routing_classification.json`.

Both chat endpoints make the same decision from the same input: the text the user
wrote in the latest turn. The default text the server substitutes for an upload
with no message is model-facing only and is never classified, so such an upload
runs on the routine profile on both endpoints. An explicit model selection is exact
and runs under the `routine` scope on both endpoints, so it receives that model's
`default` preset whatever the message says (approved 1 October 2026). The
OpenAI-compatible `/v1/chat/completions` endpoint (approved 2 October 2026)
replays the request's prior user and assistant turns as conversation history,
classifies only the latest user message, and caps the answer at `max_tokens` (a
cap only lowers the output budget the runtime would otherwise use). `n` other than
1 and a non-positive `max_tokens` are refused with 400. Sampling parameters and
`stop` are forwarded only when explicitly set with an explicit model, and then
still pass the model's supported-parameter check; with automatic routing they are
ignored, because the automatically chosen model may not accept them. The endpoint
has no routing event, so it does not use the disclosed reasoning fallback.

Automatic selection intersects the workload's acceptable model groups with
independently qualified endpoints, required tools/structured-output capabilities,
context/output fit and account funding. It compares bounded request cost within
the acceptable preference group, so the cheapest eligible route for the request is
tried first and the order models are listed in within a group is not priority. The
configuration-generated [chat routing chart](CHAT_ROUTING.md) shows the current
candidates, efforts and deployment routes. A small model output ceiling must not make an
otherwise inadequate response appear cheaper.

For an automatic request without an explicit output limit, each preference group
has one common comparison target: the account's output allowance (bounded by its
remaining context; uncapped on paid plans), capped by the largest capacity of
the group's eligible routes. Every candidate in the group must support that same
target, so a smaller-cap route cannot win on price by offering a shorter answer,
while a group whose routes all fall short of the account allowance still serves
at its best feasible size rather than refusing or escalating. A route that cannot
fit the budget at that size is ineligible, and the target falls to the group's
next-largest capacity. If no route of the group fits the remaining budget at any
capacity size, each is offered at the largest output its hold can cover, provided
that still meets the profile's `min_output_tokens` (approved 2 October 2026). That
output is sent as `max_tokens`, so the hold still bounds what the provider can bill;
the routing event adds the `budget_fitted_output` reason code and the chat notes
that the reply may stop early. An explicit model with no caller output limit is
fitted the same way. The profile's `min_output_tokens` is a suitability floor,
not the dispatched answer cap. Explicit caller output limits remain exact and
still require route/account admission.

Reasoning/council can consider their provisional premium candidates where profile
and account capabilities permit them. Research retains its premium-capable flag,
but its sole automatic model is Luna; premium route classification remains an
independent endpoint-policy decision, and explicit premium pins still require
account eligibility. Routine/background execution uses routine
routes. Premium eligibility does not itself authorize spending: every attempt
must reserve capacity. Explicit model selections stay exact and still pass
capability, privacy and accounting checks.

When an automatic (inferred) reasoning request on native chat is refused because the
account lacks `premium_routing` or the budget cannot cover any reasoning route, it is
answered under the routine profile instead, and the rest of the turn stays on routine
(approved 2 October 2026). The routing SSE event discloses it with a `fallback`
object and a `fallback_<cause>` reason code, the chat shows a notice under the reply,
and routing telemetry writes a `profile_fallback` record. Explicit selections, the
OpenAI-compatible endpoint (which has no routing event to disclose on), provider
outages, other refusals and background work keep the refusal: a missing capability
is reported as `capability_unavailable`, not retryable. When several blockers apply,
capability is reported before budget, and budget before context.

The native routing SSE event carries the original `model`, `tier` and `reason`
fields plus additive ones: `profile`, `reason_codes` (`explicit`, `council`,
`complexity_signal`, `research_signal`, `default`, `fallback_<cause>`), `effort`
(the reasoning effort actually sent) and, after a fallback, `fallback`.

Nested automatic helpers inherit the enclosing account scope's premium ceiling:
entering a reasoning profile inside a routine/background account scope cannot
enable premium routes. Explicit authorized model pins still require account
premium eligibility. Standalone reasoning/council account scopes retain their
own configured ceilings.

The approved candidate's parameter preset is applied **after selection**,
including on fallback. Reasoning-effort vocabularies differ between models;
blanket provider-prefix parameters are not reliable. Arbitrary caller options
cannot override the reviewed provider transport.
Known sampling incompatibilities are filtered before reserving spend. Explicit
or benchmark model pins outside the workload catalog retain standard request
parameters subject to the independently approved endpoint's `require_parameters`
enforcement; unknown models are never added to automatic pools by that exception.

Fallback retains workload requirements and account ownership. Each attempt has
its own conservative reservation. Streaming output is not replayed after a chunk
has been emitted. Failure or unknown usage does not imply a free attempt.
Upstream streams are closed on completion, abandonment and failover, including
when a returned iterator was never started. Account cleanup awaits every held
reservation before surfacing a ledger failure.
Transport close is bounded to two seconds; a stalled close is cancelled without
waiting for cancellation acknowledgement, so it cannot block ledger settlement.

## Routing telemetry

Routing decisions are recorded only in the server log, never in a client contract:
the SSE `routing` event and its `reason` string are unchanged. The
`daemon.routing` logger (`orchestrator/routing_log.py`) writes one line per event,
`routing_event <json>`, with fields from a fixed allowlist. It owns its INFO
handler because the application has no logging configuration and uvicorn's
defaults drop INFO from application loggers.

| Event | Known when | Purpose |
| --- | --- | --- |
| `decision` | Ingress, before the account scope | Endpoint, auto or explicit, profile, classifier version, matched signal ids, admission result |
| `scope_open` / `scope_close` | Account scope entry and exit | Operation, profile, premium ceiling; exit (`normal`, `cancelled`, `closed`, `error:<code>`), counts, settled total, first-output and total duration |
| `candidates` | Before dispatch, per completion | Ordered route ids and exclusion counts by reason |
| `attempt` | At dispatch | Route, model, group, requested, preset and **sent** reasoning effort, `max_tokens`, hold bound, reservation id |
| `attempt_outcome` | After a response or failure | Outcome, failure category, retryability, whether output was released, next action |
| `settlement` | Only after settlement | Actual amount, hold bound, estimated or metered, tokens, overage, path (completed, stream end, dispatch failure, scope cleanup, tool call, expiry recovery) |

Records join to the ledger by `scope_id` (the reservation row's scope) and
`reservation_id`; `request_id` joins them to the HTTP request. The reservation row
itself also stores `workload_profile` and `reasoning_effort` (the effort actually
sent), so cost by profile and effort can be read from the ledger alone. They never contain
message or tool content, reasoning text, credentials, endpoints, headers, email or
raw user ids. Signal ids are words from the fixed classifier vocabulary. An
invalid record is dropped and emitting never raises into dispatch or settlement.

## Council and helper workloads

Council preferences describe model developers, not the `openrouter` transport.
The council requires at least three distinct developers and reports the model
actually used per role. Parallel roles have separate routing state while sharing
account accounting. A reduced or failed roster must not be presented as a
successful diverse council.
Collected council events are retained in conversation history when execution or
account cleanup fails, with an error status rather than a successful completion.
Final persistence is shielded against request cancellation on both successful and
aborted runs. Storage failures are logged; shielding does not guarantee database
availability or survival across process termination.
Unknown council presets return a clear error before inference instead of silently
substituting the default roster.

Background titles, extraction and summaries use explicit workload profiles;
reasoning helpers use the reasoning profile. Nested routing contexts select a
workload without creating a new account allowance. Model provenance comes from
the dispatched route rather than a stale configured hint. Benchmark-specific
model injections remain separate from deployment defaults.

Automatic title generation, contradiction checks and entity confirmation reserve
up to 4096 total output tokens, including reasoning, while retaining the selected
model's reviewed effort preset. Empty or truncated helper responses are treated
as incomplete results, not valid verdicts. Explicit/benchmark small output limits
are preserved. Exact helper/benchmark pins remain exact even inside an automatic
account scope; extraction attribution comes from the actual dispatch rather than
the requested benchmark model constant. These PR-review changes were tested with
mocked providers; they do not extend the earlier paid quality evaluation evidence.

## Model rotation and evaluation

The pools include DeepSeek V4.1 Flash, GLM 5.3 Flash and GLM 5.3 alongside the
other researched candidates. Their earlier omission was not an evidence-backed
exclusion. Exact IDs and presets belong in the configuration, not duplicated
default strings throughout execution code. `GLM 5.3` is the catalog name for
the flagship candidate; `Pro` is not the verified OpenRouter ID.

Before activating a route:

1. Verify the exact model and pinned endpoint, supported parameters, effective
   context/output limits, pricing tiers and applicable privacy/hosting evidence.
   Model-developer nationality does not establish the serving location.
2. Record the existing qualification evidence and review expiry. Published
   minimum catalog prices are not a conservative endpoint price ceiling. Use a
   ceiling covering long-context and time-dependent prices for the admitted
   range; exclude ranges the quote cannot cover.
3. Evaluate representative tasks under approved account scope and budgets:
   ordinary tool use, multi-turn tool loops, code reasoning, source-grounded
   synthesis, strict-schema extraction and independent council critique.
4. Compare successful task completion, tool/schema correctness, retries, total
   billed tokens (including reasoning), latency and total cost per success.
   A model exposing `reasoning_effort` does not establish its reasoning quality.
5. Review profile assignments from those results. Rotate explicit reviewed IDs;
   do not use a floating alias to bypass requalification.

Mandatory reasoning and default effort matter for short background jobs. A low
token price alone does not establish lower latency or total task cost. Memory
extraction changes should retain the existing precision/recall evaluation and
source restrictions, not only test that JSON parses.

Public reference endpoints:
- [DeepSeek V4.1 Flash](https://openrouter.ai/api/v1/model/deepseek/deepseek-v4.1-flash)
- [GLM 5.3 Flash](https://openrouter.ai/api/v1/model/z-ai/glm-5.3-flash)
- [GLM 5.3](https://openrouter.ai/api/v1/model/z-ai/glm-5.3)
- [OpenRouter provider pinning](https://openrouter.ai/docs/guides/routing/provider-selection)
- [OpenRouter ZDR controls](https://openrouter.ai/docs/guides/features/zdr)

## Migration

Legacy `AUTO_FAST_MODEL`, `AUTO_REASONING_MODEL`, `BACKGROUND_REASONING_MODEL`,
`TITLE_MODEL`, and research/code/reader model slots no longer control deployment
routing. Provider-prefix and per-model Settings parameter overrides are replaced
by validated candidate presets. Update workload pools and qualified endpoint
records together; configuring a workload never grants endpoint approval.
The routing loader caches configuration per file path. Restart backend and
worker processes together after a reviewed configuration rollout.

The public chat/model/SSE contracts and the account ledger remain the integration
boundaries. The text routing update does not establish multimodal accounting or
enable retired/unqualified tools. Actual deployment requires qualified routes,
workload evaluation and passing repository gates.

## Integration note — Luna routing PR (28 September 2026)

This branch is a bounded integration of the Luna-first routing realignment onto
main at `a8dc6b67`. The earlier test-count claims describe the older authoring
snapshot, not this branch's gate output. Its summary checkpoint was superseded
by main's finalized-message cursor before integration. No paid
model call was made, and none was required, to produce or verify this integration.

Integration seams resolved in this branch, on top of the snapshot evidence:

- `orchestrator/config.py` no longer declares the legacy workload model pins; the
  routed Settings surface now has 108 fields with the Settings-backed
  `DAEMON_MODEL_ROUTING` path, and the doc-freshness gate extracts workload
  declarations from `config/model_routing.json` instead.
- Reflection tests dispatch inside the `reasoning` routing profile
  (`routing_context`) instead of patching the removed orchestrator-model pin;
  dream-observation retrieval assertions are unchanged.
- Follow-up and endpoint-reliability runner tests use `get_settings.cache_clear`
  around mid-test env changes because routing overrides resolve through the
  cached Settings instance, and price against the runtime's `InputSize` views:
  reservation holds use `bound`, the context ceiling uses `estimate` — matching
  the production contract at `orchestrator/compute_runtime.py`.
- The follow-up runner journals `input_token_bound` and `input_token_estimate`
  as plain integers, keeping the durable experiment state JSON-serializable.

The historical evaluation code hash is frozen with the recorded runs: the recorded
evidence and duplicate guards in `docs/MODEL_ENDPOINT_RELIABILITY_RESULTS.md` and
the follow-up results apply to the code as it ran, and the runners' env access now
resolves through `Settings.explicit_evaluation_environment()` in this branch. Any
currently recorded digest therefore matches the snapshot provenance, not the
present file contents; reruns require the separately approved execution, funding
and route gates described above. This note records integration, not activation.

### Integrated branch verification

The complete backend/frontend/aggregate wrapper exited 0 on 28 September 2026:
15 successful gates, zero blocking failures; backend 3420 passed / 7 skipped,
frontend 317 passed. Both dependency audits reported no known vulnerabilities.
The non-blocking Bandit inventory contains 8140 low, 7 medium and zero high
findings, tracked in #313. Test warnings remain visible in the gate output.
Fresh review accepted the final terminal-provider-error classification correction;
automatic exhaustion preserves typed failure details without changing the existing
multi-candidate fallback contract. The optional evaluation-period guard remains
opt-in. No production migration, route approval or deployment was performed.

After addressing PR #340's review, the full wrapper again exited 0: backend
3539 passed / 7 skipped, frontend 317 passed, 15 successful gates and zero
blocking failures. Fresh independent review reported no findings. Bandit's
non-blocking inventory was 8334 low / 7 medium / zero high. The fixes include
the owner-approved shared account output target, 4096-token automatic small-helper
bounds and inherited premium ceiling, plus exact benchmark pins, profile-aware
admission, explicit exclusions and shared identity/placement lookup. No paid
evaluation or production activation accompanied these fixes.
