# Budgeted embedding adapter — approved design

## Authority and status

On 4 October 2026 the owner selected Azure-hosted OpenAI
`text-embedding-3-small` through OpenRouter, at the existing 1,024 dimensions,
and approved a **dedicated embedding policy entry and token-metered adapter**.
The owner also approved local activation **after** privacy, quality and account
accounting checks pass. This document records that bounded design, not completed
implementation, provider qualification or activation.

This follows DEC11's model-independent context and DEC12's Z-default requirement.
No R route, commercial-plan change, database migration, new dependency, provider
fallback, existing-record cleanup or reembedding is authorized. Approval to merge
PR #447 does not authorize merging the embedding follow-up.

## Admission and accounting contract

- Embedding approvals are separate from completion routes and fixed-price tool
  services. An embedding model is not declared text-capable to bypass completion
  admission. Existing completion and tool contracts remain unchanged.
- A new shared embedding route selector defaults off. The portable policy denies
  execution. Having a key, selecting a model or seeing an eligible status is not
  sufficient authority to dispatch.
- The selected route binds the gateway, exact model, Azure provider pin,
  dimensions, per-input/batch limits, input price ceiling and privacy controls.
  Selection is primary, not a Voyage fallback. No hidden retry or provider
  fallback is allowed. Direct Azure credentials are outside this design.
- Reserve a conservative token-priced hold through the existing account ledger
  before sending. Preserve the active account, period, background allocation and
  scope identity. Recheck current route admission at dispatch.
- Known failure before dispatch releases the hold. Uncertain dispatch, timeout,
  cancellation after send or invalid receipt retains the conservative charge.
  A validated receipt settles input usage under the account's existing cost and
  overage contract. Settlement failures must not disappear into lexical fallback.
- Validate exact receipt identity, usage, vector count/index order, dimensions
  and finite, nonzero numeric vectors. No fuzzy model suffix matching, silent
  truncation or malformed-usage discount is permitted.
- Monitored embedding approval needs its own typed admission boundary and real
  inclusion in startup/periodic attestation, including freshness and sticky
  revocation. Reuse existing attestation storage without a schema change; do not
  pretend a completion-only monitor covers embeddings.

## Memory and privacy boundaries

Cloud embedding is permitted only for cloud-eligible inputs. Local-only or unknown
locality must not dispatch, including edits, imports, extraction and retrieval.
This design does not claim a new protocol for concurrent outbound privacy
revocation after authorization; that limitation remains separately disclosed.

Document and query embeddings use one stable provider/model/dimension identity.
Existing vector identities remain unchanged and isolated. Cross-space cosine
comparison is forbidden. Cosine is candidate discovery and retrieval evidence,
not permission to merge, supersede or discard facts. The conservative complete
equivalence contract in [MEMORY_LAYER.md](../MEMORY_LAYER.md) remains authoritative.

Denied embedding retains nullable-vector persistence and lexical retrieval where
the caller's existing contract permits it. Status distinguishes adapter admission
from successful process-local observations; reading status never probes a provider.

## Prospective fictional qualification

The earlier [transport screen](MEMORY_EMBEDDING_SCREEN.md) remains immutable and
does not establish quality or a private-memory grant. Its cumulative authorization
is **24 requests, 100,000 reserved input tokens and US$0.25**, including rejected
and uncertain sends. Nine requests, 75,711 tokens and US$0.01804121 are already
reserved. A continuation must carry those reservations forward, not create a new
allowance or release old holds. The separate judge allowance is exhausted.

Before any further dispatch, freeze the fictional fixture, criteria, exact
production formatter/parser and request hashes; independently review the runner's
budget/resume behavior. No production memories, real account identifiers or
database contents enter this screen.

The held-out retrieval screen in `tests/fixtures/azure_embedding_retrieval.json`
covers 16 fictional queries across domains, persons, siblings, negation,
uncertainty, conditions and explicit temporal state. Score each query against the
whole eligible fictional document corpus. Exclude non-active, closed, local-only,
wrong-owner and wrong-space candidates **by metadata before ranking**, never by
cosine. Such filtering is a harness check, not a substitute for production
integration tests.

Frozen pass criteria:

1. Every planned response validates through the final production receipt parser.
2. The annotated relevant document ranks first for at least **12 of 16** queries
   and in the top three for at least **15 of 16** queries.
3. Excluded candidates never enter the ranked output; offline production tests
   separately exercise locality and vector-space boundaries.
4. Account reservation, cancellation, uncertain-send and settlement failure tests
   pass on the final implementation; current exact-route privacy evidence passes.

These floors are a bounded retrieval usefulness screen, not statistical evidence
of universal quality or semantic equivalence safety. Do not adjust them after
observing vectors. A failed screen blocks activation pending a new explicit
decision; it is not permission to relabel data, change providers or rerun silently.

## Release evidence

### Public route evidence — 4 October 2026

Primary read-only GETs, independently inspected as raw JSON, found:

- The exact `openai/text-embedding-3-small` / `azure` pair in the
  [ZDR listing](https://openrouter.ai/api/v1/endpoints/zdr).
- The [model endpoint listing](https://openrouter.ai/api/v1/models/openai/text-embedding-3-small/endpoints)
  reports status `0`, an 8,192-token context and prompt pricing of
  US$0.00000002 **per token**, equivalent to US$0.02 **per million**. The
  gateway's `max_price.prompt` uses the latter unit, not the former.
- The [provider catalog](https://openrouter.ai/api/frontend/v1/all-providers)
  reports Azure `training:false`, `trainingOpenRouter:false`,
  `retainsPrompts:false`, `canPublish:false` and `requiresUserIDs:false`.
- `supported_parameters` is empty in public embedding endpoint metadata. This
  is not proof of dimension support, but the original controlled transport
  screen returned 1,024-dimensional vectors with `require_parameters:true`.
  Retain that flag and validate actual dimensions; do not relax it to pass.

Snapshot SHA-256 provenance (public payloads retained in the operator's cache):

| Source | SHA-256 |
| --- | --- |
| ZDR list | `44f6edd3fa68278259217022df84dd0a85a144d225bc52a686ed2a59fae96311` |
| Provider catalog | `37bb8ac9a752685c7b90352fbf0bae280291c519009f629ffbba77bb2944090d` |
| Model endpoints | `a0394ad61d5a3bd673d0a1ea427aa0b5421fe075a1d4530a847e0b3229ae7624` |

**Attestation limits:** this is route-specific intermediary evidence, not direct
access to Microsoft's resource configuration or an independent audit. Microsoft's
[data-privacy policy](https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/openai/data-privacy)
states no training without permission but describes possible abuse-monitoring
storage, and modified abuse monitoring can remove storage/human review. The
generic policy alone is not ZDR proof. Qualification relies on the exact current
OpenRouter ZDR entry and provider baseline under the existing monitored-approval
contract, not a blanket assertion that all Azure deployments retain nothing.

The generic `azure` pin is **not** `azure/eu`. A provider URL mentioning East US
does not establish deployment type or where every request is processed; no EU or
US residency guarantee is made. This is separate from the regional Luna judge
qualification. Gateway account logging/training settings are operator-specific;
the same-account attestation in [INFERENCE_ROUTE_APPROVALS.md](INFERENCE_ROUTE_APPROVALS.md)
is not independently verifiable from public sources or transferable to another key.

The dedicated policy/parser, shared monitor, input-token account adapter and
default-off `EMBEDDING_ROUTE_ID` are **qualified for the owner-approved local activation**.
The portable embedding entry remains unapproved; the deployment entry is approved
under monitored, non-calendar admission after the checks below. Scoped independent reviews covered
the accounting/transport implementation and subsequent dreaming/locality and
native-chat scope repairs. Native query preparation shares the existing producer
scope; local-only dream sources and cloud frozen/summary context are filtered.
Legacy adapters remain unqualified. No dependency, database or commercial-plan
changes were introduced.

`scripts/qualify_azure_embeddings.py` prepares the final production HTTP body and
validates receipts through the final production parser in a fictional in-memory
account scope. Its offline plan is two requests with a 4,781-token conservative
input reservation. `--execute --approved` also requires the immutable original
ledger and credential env file. The fixed sibling `.azure-small.json` carries
all parent reservations forward under both locks. It is not a real-account ledger
test or a runtime approval grant.

The runner received independent pre-dispatch review and its two fictional requests
completed successfully on 4 October 2026 (operator clock). Both receipts reported
`provider: Azure`, exact native model `text-embedding-3-small`, and valid
1,024-dimensional vectors. Input usage was 622 document tokens and 161 query
tokens; reported combined inference cost was US$0.00001566. The cumulative ledger
retains the larger reservations, not this discounted actual usage.

The frozen screen **passed: 14/16 top-one and 16/16 top-three**, against the
prospective floors of 12 and 15. Flight-seat preference and pet-name queries ranked
their correct answer second; this is a concrete limitation, not relabeled data.
The primary independently checked every frozen source hash, revalidated all 80
vectors through the production parser and recomputed the stored ranking results.
This does not qualify a cosine merge threshold or prove universal retrieval quality.

| Durable evidence | Value |
| --- | --- |
| Immutable original ledger SHA-256 | `03b2d55fd68f6515de0532684dcb73e7faf6bdb53d8056828b216104ffe80232` |
| Fixed `.azure-small.json` continuation SHA-256 | `decc37aee88217a7335cf04bc2d915fec99bf0fe721a9442503bc20e7904d9eb` |
| Frozen fixture SHA-256 | `5612464f079efe4cf5e88e9e25113f0c22c7df8d866fb27ab29373750a0fcdc5` |
| Actual production wire requests digest | `8bed6382506bf4157acf3c1398b4eb4f0fdfab5a9361172a849a917066358050` |
| Cumulative requests / reserved input / reserved cost | 11 / 80,492 / US$0.0181463920 |

Final backend blocking gates passed with **5,005 tests passed and 152 skipped**;
frontend blocking gates passed with **820 tests**, including locked install,
types, lint, format, audit and build. Full Bandit remains a nonblocking inventory,
not a high-severity pass substitute. Staged aggregate gates and scoped independent
reviews passed; the separate database-backed suite and owner-local activation
are recorded below. Existing stored memories
will not be reembedded by this change.

### Subsequent boundary and database checks — 5 October 2026

Final caller inspection found and repaired `memory_read` locality: permissions now
come from its server-bound, owner-checked conversation, never model-supplied tool
arguments. Cloud/unknown reads return only explicit `local_only:false` rows.
Known-local reads retain local lexical results but cannot send query embeddings;
the shared retrieval helper also suppresses hidden fallback embedding dispatch.
This repair received fresh independent review and 124 focused tests passed.
OpenAI-compatible clients without a stored conversation are treated as unknown,
not granted local-memory access. Direct owner-created/imported facts without a
conversation remain cloud-eligible under their explicit nonlocal write contract.

The owner separately authorized a disposable database on the existing local
PostgreSQL service, using only synthetic rows and no provider calls. The reviewed
`scripts/run_isolated_account_tests.py` launcher ran the existing entitlement and
contention tests plus four real-ledger/mock-HTTP adapter cases: valid receipt,
zero-vector rejection, timeout and cancellation. **All 35 tests passed** and the
launcher removed only its successfully created database. Production tables were
not read or modified. This proves the tested reservation/settlement/contention
paths, not concurrent source-locality revocation after authorization.

The first run rejected the four new cases because their fixture incorrectly used
an unsupported `memory` operation. Correcting the fixture to the existing `chat`
operation used by production memory writers fixed all four; no commercial policy
or runtime change was made. The final full-gate rerun passed and the corrected
activation plan received independent review. The deployment entry is approved
with the existing monitored non-calendar contract: revocation is sticky and no
successful check for 72 hours denies admission. Both approval expiry fields remain
null; the older calendar-renewal dates in the approval history are superseded.
Selecting or approving the entry alone does not establish deployment or effective
attestation.

### Verified owner-local activation — 5 October 2026

**Current state: rolled back; embeddings disabled.** The startup verification
below describes a provisional activation, not the final runtime state. Residual
caller inspection found that `memory_reflect` lacked trusted conversation locality.
Backend and worker were restored to the pinned #447 images/source with an empty
selector; readiness passed. The owner approved a cloud-only reflection guard:
only an owner-checked stored cloud conversation may embed/retrieve/synthesize;
local or unknown contexts refuse reflection without provider calls. Cloud results
exclude local-only and unclassified rows. Local `memory_read` retains lexical
access. Re-activation requires reviewed repair, final gates and a new release.

Clean archived source commit `05abf4861eba34b487d92cf803d0b2640b765df9` was built
and activated for **backend and worker only** using an additional local Compose
override. The original checkout and dotenv were not edited. Only the embedding
selector, release source mounts and explicit image tags changed; original data
and benchmark mounts, frontend and dependency containers were preserved.

Before restart, the public attestation bootstrap admitted all nine selected
routes, including the embedding entry, with zero failures or revocations. After
restart, backend readiness, PostgreSQL/Redis, worker runtime, exact source/policy
hashes and fresh monitored admission were checked. Frontend readiness remained
200; its container was not rebuilt. The qualified Luna medium profile is unchanged.
Static embedding eligibility was checked without a private inference probe or
invented process-local success observation.

No migration, existing-memory reembedding, duplicate cleanup, remote deployment
or follow-up PR merge occurred. The source's portable selector remains off by
default. Rollback uses the pinned pre-activation Python images, #447 archived
source and an empty selector, without rebuilding or restoring data. The durable
operator runbook is `AZURE-SMALL-ROLLOUT.md` under the local release directory.
