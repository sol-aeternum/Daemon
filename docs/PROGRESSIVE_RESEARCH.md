# Progressive researched answers — append-only source implementation

## Scope and authority

The owner requested implementation on 17 October 2026 and then approved the smallest
compatible end-to-end slice: **one task-owned assistant row, append-only coherent
sourced sections and explicit visible corrections**, with continued tool rounds and
existing observation/cancellation/terminal contracts. This change implements that
publication path and deterministic backend-to-client coverage. It does not qualify
real model research quality or authorize deployment/runtime enablement.

Product direction: DEC04–DEC06/DEC10–DEC12 and V01/V06/V07 in
[the decisions](DAEMON_VISION_DECISIONS.md) and [vision](DAEMON_VISION.md).
The owner subsequently approved independent per-turn detail/research controls and
Balanced + Auto defaults (17 October 2026). The exact additive native request fields
are described below. No new table, SSE event, tool, dependency, entitlement, route,
budget, permission, model concurrency or notification channel is introduced.
Approval and implementation evidence are retained outside project source under
registered task `progressive-researched-answers-20261017`.

## Answer quality and checking

The shared `orchestrator/prompts.py` instructions require proportionate checking:
simple stable questions get one direct answer; explicit search requests, changing
facts/prices, uncertainty, stakes and obscure specificity warrant verification.
**Answer detail does not determine investigation effort:** brief can require deep
checking; detailed must not introduce unsupported specifics. Both are independently
expressible in ordinary request text and the approved independent controls.

### Approved native per-turn controls (source only)

| Optional nullable field | Non-null values | Meaning |
| --- | --- | --- |
| `answer_detail` | `brief`, `balanced`, `detailed` | Exposition, not amount of checking |
| `research_effort` | `auto`, `research` | Proportionate investigation, or explicit bounded research/cross-checking |

New ordinary composer turns explicitly send **Balanced + Auto**. The two accessible
selectors are independent: Brief + Research is valid. Values belong to the current
draft/held submission, not account settings; accepted/cleared new turns and explicit
discard reset to Balanced + Auto. Draft/held recovery persists actual optional
values, including null, without backfilling historical requests.

`ChatRequest` validates invalid non-null values with existing HTTP 422 before
acceptance. The Next POST proxy forwards values unchanged, including null and invalid
values; its existing AI SDK stream reports backend refusals via `request_rejected`
with the backend status/code, not a new response contract. Missing/null adds no
control instructions, accepted-input keys or canonical fingerprint fields, preserving
legacy bytes and replay. Explicit values enter the canonical accepted request and
existing encrypted task input. Same-key changed explicit controls returns the existing
backend 409 `idempotency_conflict`, never task replacement. No input-version, hash
algorithm/domain, storage schema or compatibility-API changes are made.

Native request-bound prompts and durable attempt reconstruction append the same pure
control instructions. They grant no extra tools, budgets, permissions, model access
or concurrency; routing/qualification is unchanged. Investigation is still bounded
and instruction-following remains a model-quality limitation, not a deterministic
guarantee. Separate Council command execution is unchanged.

Before attachment serialization, composer submission captures effective values and
an immutable control-edit token. Real control edits detach the original submission
key; delayed holding/acknowledgement cannot reattach or erase a newer edited draft,
even after values change back. Historical held resends preserve absence/null rather
than reading the visible picker defaults. Pending metadata restores each original
control (deleting outgoing fields originally absent); failed-turn retries retain the
original turn's key/files/controls. Direct POST/backend requests are not coerced into
replay. Existing cancel/reconcile-before-submit semantics remain authoritative.

Central claims need checking before confident publication. An identified central
gap calls for a bounded targeted check, not just a disclaimer. When checking is
unavailable or inconclusive, narrow the answer and identify unknowns; omit/qualify
peripheral guesses. Distinguish primary testimony, independent corroboration,
inference and unknowns. Copied reports are not independent corroboration. Cite only
returned evidence supporting the claim, distinguish snippets/partial reading from
page coverage, and never infer nonexistence from a failed search. Append a precise
supported correction when later evidence contradicts visible text.

These are model instructions, **not deterministic claim-verification enforcement**.
The production prompt assembler includes them. A scripted model in a regression
does not prove that a real model will comply or that citations entail its claims.
The shared prompt also changes request-bound chat guidance if this source is
deployed, even while durable chat is off; only the new persistence barrier is
durable-only. No deployment has been performed by this task.

## Continued execution and coherent partial persistence

`orchestrator/tools/completion.py` already streams content, executes tool calls,
appends fenced tool results and continues sequential provider rounds. A response
without tool calls ends the loop. Existing tool/context/compute limits and final
tools-disabled answer remain authoritative. An early finding must accompany actual
remaining calls, not an empty update or a promise that ends the turn. No extra model
call is added solely to send early text.

`orchestrator/daemon.py` accumulates that text in the existing assistant row. The
ordinary one-second persistence throttle could leave a multi-chunk early section
only partly saved while a subsequent tool blocked. The new **durable-only pre-tool
barrier** flushes dirty accumulated answer text through the existing message sink
before recording/forwarding the continuing tool call or resuming its executor.

- Only an acknowledged non-None write advances the in-memory saved-chunk count.
  Empty/unchanged text adds no write; multiple tools in a batch do not reflush it.
- Periodic writes remain after token yields. The runner has consumed all preceding
  deltas at the barrier, so `AttemptSink` persists the matching delta sequence with
  the prefix through the existing `TaskStore.write_partial` transaction/fence.
  Delta sequence counts deltas, not characters or bytes.
- The barrier writes **content only**, never complete status or another message.
  Failed/refused/missing-writer persistence stops before the pending tool metadata,
  tool-call frame or invocation. Cancellation propagates through existing paths.
- In the combined #542 tree, any uncertain durable periodic or barrier mutation
  closes attempt admission. There is no second store write, provider/tool dispatch
  or terminal acknowledgement authorized by a missing response; cancellation
  inside an admitted write remains indeterminate too. The daemon's generic
  finally path cannot reopen the durable sink. Request-bound memory writes remain
  best-effort and are tested with a separate legacy writer, not `AttemptSink`.
  A later coherent snapshot does not prove the uncertain write's caller received
  acknowledgement. Current whole-attempt recovery may repeat read/inference work;
  this is not remote producer-stop or no-respend proof.

This is persistence **before continuing the next tool**, not atomic persistence
before the first live token is displayed, and not an independently resumable research
checkpoint. No runner/store/observer generation or lifecycle wiring is replaced.

## Ordering, reconnect, outcomes and redirection

Sections and corrections are ordinary append-only text within one result row, not
independently replaceable records. Existing task identity, generation/delta ordering,
snapshot catch-up, duplicate suppression and terminal refresh remain unchanged.
Append-only applies within an attempt: existing whole-attempt regeneration can
replace an earlier partial answer under a new generation, with interruption/reset
disclosure. This is not section-preserving restart recovery.
`final`/`done` end the whole task/turn, never an early section. Existing reset markers
reconcile snapshots or replaced attempts; they are not section replacement.

The observer publishes the current committed completed/failed/cancelled/needs_attention
outcome. Failed tool evidence remains failed/unknown; a source-incomplete answer
must say so. **A normal completed answer does not mean every requested source read
succeeded.** No new durable partial-failure/blocked state or stop-reason enum is
invented. Existing pipeline-error and unfinished-tool notices now explicitly say
work stopped and findings may be incomplete, rather than promising continuation
immediately before finalization. Limited-answer commit semantics are unchanged.
Cancellation and uncertain material effects retain current behavior; a
transport close does not certify a remote operation stopped. Redirection uses the
existing cancel/reconcile-then-submit flow, not concurrent supersession or blind retry.
An observed cancel flag is not synchronous interruption: existing heartbeat/grace
handling can still permit a repeatable read before the interrupt arrives. Tests
exercise cooperative local interruption, not a zero-latency flag-only stop guarantee.
Existing no-force-scroll behavior is unchanged; no navigation or push-spam change.

Request-bound execution remains request-bound. Durable chat is still default-off
and gated by [#477](https://github.com/sol-aeternum/Daemon/issues/477). Source endpoints
are reusable without claiming production qualification. General generation/output
fencing and lifecycle integration remain with that separate work; see
[ACCOUNT_CONTROL_PERSISTENCE.md](ACCOUNT_CONTROL_PERSISTENCE.md), J1c-1f, and
[ATTEMPT_LIFECYCLE.md](ATTEMPT_LIFECYCLE.md). This change does not wire their uncalled
seams. Research checkpoint/resume is deferred slice 2
([DURABLE_REQUEST_DESIGN.md](DURABLE_REQUEST_DESIGN.md), §16).

## Deterministic acceptance and limits

- `tests/test_progressive_research.py`: production prompt assembly and the real
  completion loop with scripted sources/dispatch; direct one-call stable answer,
  useful prefix before more checking, source failure and cooperative cancellation.
- `tests/test_progressive_research_delivery.py` and
  `tests/progressive_research_support.py`: real runner execution, daemon, completion/
  executor, AttemptSink and observer with synthetic storage/admission/transports.
  Split prefix inside the throttle interval, full running snapshot before a gated
  read, matching sequence, one row, unchanged-section no-op, periodic failure,
   barrier uncertainty/refusal/in-write cancellation/missing writer, request-bound
   compatibility, duplicate live prefix deltas, explicit correction and existing
   terminal outcomes. Synthetic accepted input uses the real original-origin
   classifier; every fake worker mutation checks the complete bound Claim before
   effects, including worker and original origin. This models post-authentication
   data, not SQL fencing or authentication itself. Unknown write outcomes are
   distinct from separately acknowledged execution failure and Stop outside a write.
- `frontend/__tests__/progressive-research-delivery.test.ts`: invokes that actual
  Python path in the task-local environment without a server, then feeds its emitted
  observer SSE through the production Next bridge and AI SDK client reducer. Checks
  active early text (no false finish), reconnect without duplicate text/execution,
   visible correction, source limits and actual acknowledged failed/cancelled status
   rendering. Additional unknown-write/cancel/periodic snapshots deliberately detach
   without invented terminal frames or waiting for a terminal that was not committed.
- `tests/test_answer_controls.py` and native stream regressions: historical canonical
  bytes/digests, explicit-pair identity, validation before dispatch, synthetic durable
  forwarding/conflict mapping and real worker prompt reconstruction. The delivery
  fixture also reconstructs Brief + Research through the actual worker prompt path.
- Frontend controls composer/proxy, held-draft, transport and page regressions:
  independent selectors, explicit defaults, null/absence replay, reload, immutable
  control-edit receipts and delayed file serialization, including changed-back edits.

No stored golden transcript substitutes for the backend in the cross-language test.
Vitest global setup generates fresh transcripts once before concurrent test workers,
with an explicit 10-second subprocess/aggregate setup budget and in-memory injection.
The UI assertions retain their default deadlines. Setup timeout or invalid output
fails the gate; no persisted transcript is reused. This also requires the locked
Python fixture environment for filtered Vitest invocations.
CI and the frontend-only local gate install their existing locked Python fixture
dependencies before Vitest; the cross-language test never downloads them itself.
Storage, admission/accounting, sources and providers are synthetic; no test qualifies
real SQL fencing, remote cancellation, restart/checkpoint recovery, historical/price
accuracy, citation entailment, contradiction recognition or production performance.
No paid application inference/live evaluation was authorized.

Future authorized qualification must measure first useful latency (acceptance to
first supported substantive finding, excluding acknowledgments) **and** supported
completion latency (acceptance to committed supported completion), first-answer
accuracy, citation entailment, follow-up burden, actual cost per successful task
including failed attempts, cancellation latency and duplicate content/actions/effects.
Record actual clock boundaries, stop reasons and available usage; missing usage is
unknown. Existing generic timings are not these research-specific measurements.
Research checkpoint resume and production enablement remain deferred. Full local
pytest/cache-browser gates consume the separately reviewed #541 prerequisite
([draft #544](https://github.com/sol-aeternum/Daemon/pull/544)); its owned Redis and
exclusive cache endpoints are documented in [OWNED_TEST_FIXTURES.md](OWNED_TEST_FIXTURES.md).
The locked Python setup stays before Vitest, and the blocking cache collision check
stays before the cache-browser suite in both local CI and CI. General browser
ports 3100/3101 remain outside this authorization. Targeted synthetic passes do not
waive complete local gates or authorize a draft PR through a wrapper bypass.
