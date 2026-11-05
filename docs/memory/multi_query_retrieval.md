# Multi-query memory retrieval — M1

Status: default-off implementation design for #601, PR-1 items B1–B3. Not route
approval, benchmark authorisation, production enablement or an efficacy claim.
This document is written before implementation. Runtime wiring and evaluation
must be verified separately; the implementation ledger below distinguishes them.

## Problem and evidence

Multi-fact questions need several stored facts. One whole-message embedding can
miss those facts even when production retrieval mechanics work correctly.
The [zero-call exposure sweep](../../tests/benchmark_results/memory_exposure_sweep_2026-11-03.md)
found strict exposure of 0/5 omission cases at width 5, 1/5 at width 10 and 3/5
at width 20. It concluded `INVESTIGATE_RETRIEVAL`, not a width campaign; wider
single-query retrieval also introduced abstention distractors.

The owner-supplied summaries of PR #656 and PR #659 are development evidence,
not a fresh acceptance set. Oracle component queries at depth 10 exposed 2/5
cases with RRF versus 5/5 with round-robin; model-generated queries exposed 1/5
versus 3/5. RRF lets an original-query result bury a fact found by only one
component. Consequently M1 has **no RRF**.

The omission cases `28dc39ac`, `1192316e`, `6613b389`, `0bb5a684` and `0a34ad58`
are spent evidence. Any later use must be labelled smoke coverage, never fresh
acceptance. The quota, sub-query cap, depth and timeout below are priors chosen
partly with sight of these spent development cases. Acceptance needs fresh data.

## Target algorithm

1. Preserve today's original-query embedding and production top-5 retrieval.
2. Concurrently ask an injected `SubqueryProvider` for zero to three component
   queries. Production uses guarded inference; tests/evaluation can pin fakes.
   Prompt v2 and its JSON schema are versioned module constants with hash tests.
   Input is only the latest user message and the as-of date: no transcript,
   retrieved memories, account identifier or answer-bearing benchmark payload.
3. Components seek distinct facts, not paraphrases of the whole message. A
   single-fact question yields an empty list. Reject malformed envelopes,
   non-string/blank items, excess items and the unchanged whole message.
4. Bound decomposition by the configured timeout, measured from its start, not
   an extra timeout started after baseline retrieval completes. On refusal,
   timeout, error, parse failure or an empty list, retain the exact baseline.
5. Batch-embed all accepted components in **one query-embedding call**. Preserve
   query/storage model metadata and existing accounting safeguards; do not use
   document embeddings as query embeddings.
   The component batch requires a selected guarded embedding route capable of
   one send; unavailable/insufficient batch capacity fails open, without legacy
   embedding retries or provider fallback.
    Retrieve each at depth 10 via `retrieve_memories_for_text`, preserving scope,
    locality and historical clock. Run at most the configured 0–3 arms concurrently,
    retaining input order regardless of completion order. One named 8.0-second
    post-decomposition deadline covers batch embedding plus every retrieval arm;
    it starts when decomposition is accepted, not when the baseline finishes.
    This reuses the former component embedding bound as a whole-branch bound,
    without a new setting or a fresh timeout per child.
6. Merge deterministically, then render through the existing formatter and
   token budget. Failure in the added branch must never erase a good baseline.
   Cancellation and accounting-integrity errors retain their existing safeguards;
   fail-open does not authorise hiding an unsettled provider charge or retrying it.

### Merge interface

`additive` is the production candidate. Begin with the baseline list unchanged
and in order. In component order, append up to `quota` new IDs from each
component's depth-10 list, skipping IDs already present, including duplicates
across components. Stop at `5 + quota × n_sub`. The baseline is a prefix, not
a list to re-rank or re-score. Do not increase either global retrieval constant.

`round_robin` is evaluation-only comparison behind the same merge interface:
visit the original list and each component list cyclically, consuming the next
unseen ID, under the same overall cap. It does not promise baseline-prefix
preservation. Production rejects it outright; only the evaluation entry can use it.

### Explicitly authorised renderer change

`MemoryContextInputs.retrieved_limit: int | None = None` is an opt-in item-limit
override, not a token-budget override. Reject booleans, non-integers and values
below the current `MAX_MEMORY_ITEMS`. The renderer retains its signature and
changes only its retrieved-row slice to
`inputs.retrieved[: (inputs.retrieved_limit or MAX_MEMORY_ITEMS)]`.
One `retrieved_width(inputs)` helper owns the renderer and both exposure slices.
`None` continues to read the global at call time, so in-process evaluation width
overrides still work. Labels, truncation, L0 handling and token-budget removal
are unchanged. Only flag-on retrieval that appended at least one new result may
set the override to the actual merged length, only above five and no larger
than `5 + quota × n_sub`; all baseline/fail-open paths leave it
`None`. The exposure ID mirror and pre-budget metrics must use the same width.

Additive preserves retrieved baseline IDs, but renderer-budget interactions still
need measurement: longer prompts can change which summaries fit, and prefix
preservation is not permission to waive the zero-regression exposure gate.

### Side effects and trajectory logging

The owner-approved #665 Block A supersedes the earlier global rendered-only
amendment. Enabled gather runs the same legacy baseline helper with the caller's
original `record_side_effects`, reference-time argument and NULL query role.
Baseline touches and the baseline `record_retrieved_memories` call remain unchanged,
even when the renderer drops those rows. Empty/unapproved/parse/timeout/failure and
all-duplicate branches retain baseline prompt bytes, touches, trust calls and log
column values. A global rendered-only change is not proposed here.

Inputs carry added IDs with an empty default. Only added rows retained by the
wrapper's actual render receive extra access touches and retrieval timestamps;
L0 and baseline rows are never re-touched by this extra path. Evaluation with
`record_side_effects=False` remains read-only. No speculative render sets effects.
The narrow added-trust helper replaces the conversation list with this turn's
baseline IDs plus rendered added IDs, never an append to stored prior-turn IDs.
It compares the stored list against the captured baseline before replacing it;
a mismatch suppresses the extra trust update and added timestamps. With an empty
baseline (which skips the legacy replacement), CAS instead uses the prior-ID
snapshot from the wrapper's existing owner-qualified conversation read. JSON
strings/decoded lists are validated; malformed or missing snapshots fail closed.
The replacement contains rendered added IDs only, never stale prior-turn IDs.
No additional baseline/no-addition read or trust call is introduced.
Identical-ID ABA remains unresolved under existing last-writer-wins semantics;
there is no new schema, generation or concurrency policy.

Effects are best-effort, not atomic: an added touch can survive a later CAS mismatch;
a successful conversation CAS can survive a failed/cancelled added timestamp write.
Neither rolls back or repeats legacy baseline effects. Ordinary extra-effect
failures do not fail the prompt; cancellation and accounting failures propagate.
The baseline failure/call contract, renderer signature and budget stay unchanged.
Shared rendered-row and width helpers live in `injection.py`; exposure imports
them, never the reverse. The LongMemEval harness records no effects.

Retrieval-log rows gain nullable `query_role`: NULL for all new baseline rows,
`sub:<i>` for zero-based components. Readers still accept historical `original` rows.
The additive migration adds this column only; `query_text` stays NOT NULL.
Sub-query rows store `query_text=""` and `query_embedding=NULL`, retaining model,
candidate/selected IDs and role. No component text, derived vectors or keyed
hashes are stored. Suppress text before every log exit, including combined/L0,
normal, empty and lexical paths; candidate scores, debug/error logs and metrics
must not copy it. Diagnostics readers filter sub rows before exact/fuzzy
lookups and before fuzzy `LIMIT 50`. Original plaintext is pre-existing and out
of scope, tracked in #663. This PR neither claims log encryption nor retrofits
it, and does not expand retention.

Component rows are sanitized buffered data under each branch's ownership, not
detached writes. Failed, cancelled and no-addition branches discard their buffers.
Only a successful additive merge with added IDs flushes rows, in input-arm order,
preserving candidate/selected data. Flushes are awaited by gather; cancellation
stops remaining writes (already completed rows can remain), accounting failures
propagate, and ordinary log failures remain best-effort. No component/flush producer
outlives gather. Runtime round-robin rejection and evaluation-only controls remain.

## Routing, reasoning and privacy

Use a dedicated proposed `memory_decomposition` profile. Reusing `background`
with a reasoning override would change extraction, which currently uses Luna
at its default medium effort. Add only the new profile, Luna suitability and a
profile-specific `reasoning_effort: none` preset (off, supported by its existing
ladder). Existing profiles, candidate groups and effective presets stay unchanged.
Only the original five profiles are required in deployment-owned routing files;
`memory_decomposition` is known and validated when present, but optional (owner
P1, superseding packet A3's missing-profile load failure). If absent, entering its
routing context fails before completion, and decomposition retains the baseline
without substituting another profile. Legacy inference policies need no proposal
or new transport fields: a missing pinned route returns no components. No new
commercial-config requirement is introduced.

Profile placement grants no route permission (`model_routing.py:10–18`). Pin
the proposed decomposition route through the existing guarded-completion
`_route_id` control (`compute_runtime.py:2326–2338`), inside
`routing_context("memory_decomposition")`. Extraction supplies the context
pattern, not the route pin: decomposition additionally passes exact `model`,
`_exact_model=True` and `_dispatch_timeout_s` for the remaining deadline.
Never retry without the pin. A model-only shortlist could
select the already-approved `luna-azure-eu` route, which is not permission to
run this proposal. No approved decomposition route means baseline-only operation,
even if the flag is enabled. Attestation, account entitlement, reservation and
settlement remain on the existing guarded path; no direct client or new provider.

Propose a separately named Luna/OpenRouter route with a pinned `azure/eu`
provider, `sort: latency`, the existing Luna price ceilings as **proposal-only
ceilings**, and `approved: false`. Availability, approval and operator review
must truthfully remain unverified; this design grants none of them. Ship the
entry in both shipped completion-policy files so the feature has one consistent
pin in development and production, but as `approval_mode: expiring`, with no
baseline or invented dates/review evidence. It is not attested. Later approval
must separately convert it to monitored on the single pinned provider with a
baseline. Do not change attestation filtering: unapproved monitored entries
otherwise join listing checks/writes. Existing monitored sets, fingerprints,
staleness, writes and approved-route sets must remain unchanged (S6).

Transport validation needs explicit `multi_provider: true` opt-in supporting optional
`sort: latency | throughput` and a nonempty multi-provider `provider_only`
allowlist. Reject `:` model variants for routes using either feature. Keep ZDR
true, data collection denied, required parameters true, fallbacks false and a
pinned maximum price. Legacy routes retain identical validation and serialized
payloads; sorting must not silently alter an existing provider-order contract.
Reject empty/duplicate slugs, more than four providers, invalid sort and mismatched
order membership. With sort set, omit order from the outbound payload so it
cannot defeat sorting; otherwise use the explicit pinned order. Legacy branches
and absent-vs-null serialization remain identical, and embedding routes cannot
opt in. Monitored singleton plus sort is valid with unchanged baseline checks;
monitored multi-provider is rejected. Expiring multi-provider can exist only as
unapproved proposals, never as approved runtime routes. The primary adjudicated
the Astra second opinion and focused independent pre-change review under S1–S6;
implementation may proceed within those constraints. No approvals change here.

Decomposition and component embedding require **exactly** `pipeline == "cloud"`.
Local, missing, unknown or merely cloud-like values never authorise cloud
decomposition, regardless of whether an embedding route is selected. Flag-off,
local and unapproved mean zero new decomposition sends; baseline cloud embeddings
continue unchanged. Empty/parse failure/timeout may follow an authorised send:
fail-open promises baseline prompt bytes, not retroactive non-disclosure.
Cap the latest user text at 8,000 characters and bind one reference time for the
enabled path, shared by decomposition's as-of date and temporal retrieval.
No content in failure logs or exceptions. Do not infer cloud permission from
missing local flags. Source scope and local-only filtering apply to every arm.
This preserves DEC12's Z-only runtime and AC07/AC11 authority/data boundaries.

## Settings proposal

| Setting | Default | Purpose |
|---|---|---|
| `memory_multi_query_enabled` | `false` | Default-off runtime gate |
| `memory_multi_query_merge` | `additive` | `additive` or evaluation-only `round_robin` |
| `memory_decomposition_timeout_ms` | `1500` | Positive decomposition deadline |
| `memory_subquery_quota` | `3` | Positive unseen results per component |
| `memory_max_subqueries` | `3` | Bounded to 0–3, matching the schema |
| `memory_decomposition_profile` | `memory_decomposition` | Dedicated minimal/off reasoning profile |

Document each new environment key in `.env.example` and wire it into both
backend and worker in `docker-compose.yml`. Update the explicit settings count
in the env-parity test in the same change; add no wildcard exclusions. No
dependencies, commercial plan, SSE event or unrelated entitlement changes.
No feature-matrix row is warranted: this is default-off internal retrieval
infrastructure, not a promoted user-visible client capability.

## Rollout

Apply nullable migration 063 with deployment, even with the flag off:
diagnostics readers always require `query_role`. Flag-off inserts retain the
legacy statement when the role is `None`; enabled component inserts need the column too.
Both backend lifespan and worker startup explicitly refuse enabled operation if
`retrieval_log.query_role` is absent, naming migration 063 regardless of logging
settings. Disabled startup performs no multi-query schema check/query and baseline
inserts never write the column. Preserve and verify the migration's 1-second lock
timeout (and 5-second statement timeout); rollback has the same bounds and requires
disabling the feature and stopping dependent diagnostics first. No live migration
or deployment is authorized here. Main owns the unrelated 060, 061 and 062 migrations.
The exposure sweep remains compatible with pre-063 evidence databases, matching
exact non-empty query text and model instead of filtering by role.

## Latency, cost and failure behaviour

Let B be baseline latency, D decomposition time, T its deadline, E batch
embedding and R component retrieval. With successful parallel decomposition the
critical path is approximately `max(B, D + E + max(R_i))`; concurrent component
embedding/retrieval shares the eight-second deadline after accepted decomposition.
A decomposition-only failure
adds at most `max(0, min(D, T) - B)` before baseline rendering, excluding cleanup.
Time out/cancel and await owned work; do not leave a provider task running after
the gather finishes. Measure decomposition, batch embedding and sub-retrieval
durations with existing monotonic-time/logging conventions, without query text.
Added end-to-end p50/p95 must be measured in M2, not inferred from this formula.

The #659 development measurement on `luna-azure-eu` used **medium**, not the
proposed off/minimal reasoning: about USD 0.00006 per decomposition call and
USD 0.00000026 per embedding, with generation p50 1.84 s and p95 2.71 s.
Incremental helper cost is approximately `0.00006 + n_sub × 0.00000026` USD
(at three components, USD 0.00006078), excluding answer-prompt growth, provider
price differences, failed-call charges and retries. Those are estimates, not
approved spend. A 1500 ms prior may discard many medium-effort calls. The old
prompt produced one paraphrase for 43/50 questions, motivating a new prompt
that requests distinct fact components or an empty list.

Fail-open conditions include disabled flag, non-cloud locality, no approved
route, no usable user message, provider refusal/error, timeout, malformed JSON,
schema failure, empty components, embedding failure, retrieval failure and no
new IDs. Each must preserve the exact baseline rendered bytes and leave the
item-limit override unset. No automatic provider fallback, changed ranker,
second generation pass or permission widening is implied.

## Evaluation and enablement gates

1. **M1 offline:** synthetic fakes and local Redis/Postgres unit-test fixtures
   are allowed (R3); no provider calls, benchmark DB, ingest or holdout payload.
   Run the full pytest gate. Freeze prompt/schema hashes. Test batching, locality,
   deterministic merging, prefix/cap/de-duplication, query roles, side effects,
   all fail-open paths and legacy policy/payload identity. Use a fake clock for
   timeout tests. The full existing parity matrix is 1,944 cases from
   `tests/test_memory_context_parity.py`: pipeline (3), embedding selector (2),
   outcome (3), L0 (3), retrieved rows (3), summaries (2), messages (3), budgets
   (2), plus early returns, all 18 production/harness combinations and formatter
   identity tests. Preserve noted-date goldens and all exposure exactness/sweep
   tests. Verify mutable routing contexts do not leak across concurrent baseline
   embedding/decomposition tasks: enter the routing context inside its task.
   Test reservation/settlement ownership, settle-once, cancellation propagation
   and draining owned tasks. Accounting errors escape fail-open; no uncertain
   send is retried. Renderer `None` remains identical to the pre-change implementation;
   explicit 8-row and constrained-budget cases verify the authorised seam.
2. **M1.5 latency bake-off:** at most three ZDR model candidates, each with
   minimal/off reasoning and one pinned provider with `sort: latency`; at most
   one throughput arm. Make 50 calls per arm on dev-50 after separate approval.
   Record TTFT and total p50/p95, serving provider and cost; discard generated
   sub-queries. The owner selects the route and a timeout near its p95.
3. **M2 exposure:** on fresh dev2, compare baseline with additive using one
   frozen generation pass and the production gather/renderer, with optional
   pinned `SubqueryProvider` injection in `build_parity_memory_context`.
   Pre-register an exposure-gain threshold **before any dev2 output**, zero
   exposure regressions on dev-50 (verify despite the additive prefix property),
   abstention distractor delta, trigger rate, added latency and duplicate occupancy
   (equal content with distinct IDs remains possible; do not deduplicate by content
   in M1). An exposure-gate
   entry point uses `orchestrator/eval/exposure.py`, not a replacement ranker.
   The numeric gain threshold is not set by examining these spent dev cases.
4. **M3 answers:** a separately authorised answer campaign under v2. No v3
   restore, retry or campaign authority is granted by this design.
5. **Production:** separate owner enablement decision, possibly opt-in per
   account, after route approval and fresh evaluation evidence. This PR ships
    no approved decomposition route and makes no live benchmark run. The owner
    must also rule on retaining empty-string component query text before production;
    Block A preserves that privacy-first representation without claiming final approval.

Open question: can a cheap deterministic pre-filter avoid generation on
single-fact turns? Defer until M2 trigger-rate data exists; do not tune a
heuristic against the five spent omission cases now.

## Implementation ledger

| Item | Artifact | Status / acceptance |
|---|---|---|
| B1 | This design | Written first; no runtime approval implied |
| Renderer seam | `injection.py`, `exposure.py`, offline tests | Implemented: shared width/row helpers, unchanged token budget and renderer signature |
| B2 | `multi_query.py`, settings, gather, policy, logs | Implemented under S1–S6; #665 Block A restores exact legacy baseline effects, adds rendered-added-only effects/CAS trust composition, owned buffered logs, bounded concurrent arms/shared deadline, and enabled-only 063 startup guard; no route approved |
| B3 | `build_parity_memory_context`, `exposure.measure_multi_query_exposure` | Implemented: optional provider pin, frozen cached-vector exposure entry, additive/evaluation-only round-robin; synthetic tests, no live run |

The B3 exposure entry refuses any provider except the exact frozen
`PinnedSubqueryProvider` class and requires original/component vector pins before
reading the supplied store. Cached-vector controls are scoped to that evaluation
call; no global embedding override or new provider is used. Original and component
retrieval both disable additional embedding dispatch. The entry measures per-question
exposure, distractors and duplicate occupancy through production gather/render and
the existing exposure metrics; it sets no acceptance threshold and authorises no
campaign. The harness's omitted-provider call retains its existing call contract.
Final gate evidence is reported separately; implementation is not a claim of
live qualification or improved answer quality.

No live evaluation, holdout access, provider spend or benchmark DB connection
is part of M1. Local unit-test DB fixtures are permitted; no route is approved.
