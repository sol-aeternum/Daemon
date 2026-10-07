# Daemon — Product decisions from the vision interview

Dates: 26–27 September 2026. Status: **ratified product-owner decisions; implementation not certified**.

This records the product owner's direct answers following the vision review. It approves the stated product behaviour, not database schemas, API contracts, dependencies, provider changes or deployment. Stable DEC IDs match the interview record originally saved outside the repository. See [the vision](DAEMON_VISION.md) and [dated reconciliation](DAEMON_RECONCILIATION.md).

## DEC01 — One personal workspace with optional projects

Each user has one personal Daemon workspace. Users can begin immediately and organise work into projects when useful. Resolves the workspace/project vocabulary in O04; informs V01/V03/V07.

## DEC02 — Shared relevant context unless restricted

Projects primarily organise work. Relevant account/workspace context can flow between projects by default unless explicitly restricted. Sharing does not mean loading every resource into every request.

This supersedes any implication in v0.1 that all projects are isolated by default. Account/tenant boundaries and source permissions remain mandatory. Context sharing is not external-sharing authority.

## DEC03 — Restricted projects keep their information inside

A restricted project can use permitted general workspace context, but its own files, memory and results cannot be used outside it without explicit permission. This is an asymmetric boundary, not two-way isolation. Derived memory inherits source restrictions (DEC08).

Applying changed restrictions to existing derivatives, indexes and caches remains design work.

## DEC04 — Every accepted request continues after client closure

Ordinary questions and longer tasks continue within their authority and budget. Closing the app does not cancel work. Users return to an answer/result or a truthful waiting, blocked, failed or cancelled state.

No separate background-work mode is required. This is responsibility to track and continue accepted work, not a guarantee every requested outcome is achievable.

## DEC05 — Accepted means durably saved and tracked

Once durably recorded and tracked, Daemon owns the request's progress. Inputs, authority or capacity may prevent immediate execution, with a visible explanation. Acceptance is distinct from readiness and successful completion.

Acknowledgement protocol, persistence/queue transaction boundaries and schema require architecture approval.

## DEC06 — Make independent progress, then wait on material ambiguity

Complete useful steps that do not depend on an unresolved material choice, save progress and ask the user. Do not guess that choice because the user is away. Clear requests within standing permissions do not need redundant confirmations.

## DEC07 — Uploaded documents become reusable workspace resources

Uploads become reusable files, associated with their project where applicable, under the applicable storage/retention policy until removed. A clear request authorises necessary hosted task processing within disclosed provider/data policies. New processors, destinations or operations exceeding that policy need additional authority; ordinary covered reads do not.

This does not approve infinite storage, perpetual retention, local-only inference or external sharing. Exact retention periods and deletion propagation remain open.

## DEC08 — Source-linked, scope-preserving memory may be automatic

Daemon may retain selected useful document-derived information for later work, inheriting source restrictions. Keep provenance, distinguish document claims from user facts, and support inspection, correction and removal. This does not authorise indiscriminate extraction or out-of-scope reuse.

## DEC09 — Notify on capacity interruption; allow prevention of auto-resume

For a capacity-halted task:

1. Notify the user that work was interrupted and explain the capacity pause.
2. Preserve the task and progress.
3. Provide a control preventing automatic resumption of that halted task.
4. Otherwise resume when ordinary capacity becomes available, after rechecking task currency, permissions and input freshness.
5. Extra charges still need existing authority; no automatic purchases or plan changes are authorised.

Notification channels, freshness rules and failed-recheck behaviour remain design choices. Uncertain external effects must be reconciled rather than blindly replayed.

## DEC10 — First milestone: broad assistant continuity

Prioritise reliable continuation/recovery across ordinary questions, research and existing supported tools. This supersedes the reconciliation/review's initial recommendation to start with only a text-to-CSV/DOCX workflow. That workflow can remain test coverage, not the definition of the milestone.

Identify the supported operation set and test completion, failure, cancellation and recovery by operation. Retired/unqualified execution is not re-enabled. The decision neither selects a sandbox nor requires companion-device work first.

## DEC11 — Product identity: one personal AI for everything, model-independent, user-owned context

Daemon is one personal AI for everything, from quick questions to autonomous work that runs for days. It combines consumer-assistant breadth (chat, search, media, documents, schedules, reminders, integrations) with autonomous-agent follow-through (long tasks, code, cloud and device work). All of this comes from one conversational entry point, with no product or mode boundaries. Daemon is not tied to one model or model family: each role routes to the best qualified model through configuration. The user's context is portable, model-independent, and importable and exportable.

This supersedes the v0.1 one-liner ("persistent agent workspace…") and the "multi-provider LLM orchestration platform" framing, which remain accurate only as descriptions of how Daemon works. The canonical statement is [DAEMON.md](DAEMON.md). "Spine before surface" and the build order in DAEMON.md remain **Proposed** sequencing, not ratified scope.

## DEC12 — ZDR by default; provider-retained routes opt-in only

Every role defaults to zero-data-retention (Z) routes. Provider-retained, no-training routes (R) are used only when the user opts in per model family, after seeing the provider and its retention period. R routes are never used for restricted-project data, and each use is recorded in the activity record. Routes that train on inputs (T) are never used. Privacy opt-in is independent of commercial plan. Daemon takes all reasonable steps to choose the best ZDR host per role and keeps its own dated evidence per route.

**Supersedes:** the `docs/SUBSCRIPTION_ARCHITECTURE.md` privacy invariant ("Routes require explicit approval, ZDR, no training or retention…"), **for opt-in R routes only**. For every default route, the invariant is unchanged.

**Not approved by this decision:** the schema for retention classes in `config/inference_policy.json`, the consent UX and storage, restricted-data enforcement, fallback behaviour between Z and R, and the activity-record format. These need an implementation design and approval before any R route can execute. Until then, the code stays Z-only, and that matches this decision's default.

## Subsequent baseline instruction

During integration, the product owner explicitly chose **preserve the staged removals** of shared encryption-failure counters and advisor support. Repair dangling consumers and still-supported worker functions; do not restore removed features just to satisfy obsolete tests. This instruction does not relax quality gates or authorise removal of coverage for supported behaviour.

When another dangling worker call exposed the removed memory garbage-collection method, the product owner separately approved **restoring the previous status-based policy**: permanently remove inactive memories older than 90 days and pending/rejected/deleted memories older than 30 days; never active memories. This restores the pre-existing memory cleanup contract, not a new workspace-file retention policy. No cleanup or deployment was performed during integration.

The product owner chose a **separate cleanup pass** for remaining full frontend lint/format and host-artifact debt. Finish this bounded integration with truthful blocker evidence; do not blanket-reformat unrelated files or weaken gates. Dependency changes still require separate approval.

### PR-port clarification against newer main

When preparing the PR against `2bf65150`, the product owner explicitly chose **preserve current main's working encryption metrics and advisor-event compatibility**, including council and legacy trace support. The earlier removal instruction concerned dangling callers in the older dirty checkout; it does not authorise removing these working current-main contracts. Port only applicable fixes and regression coverage. Existing memory cleanup, worker integration, auth boundaries and terminal message-state repairs on newer main are preserved rather than replaced by older implementations.

The product owner also required the README to retain the orchestration, routing and subagent architecture alongside its vision-led introduction. Product positioning does not replace the technical explanation; current capability and retired/reserved paths must remain explicit.

## Remaining decisions

- Retention durations, limits, exports, backups and cascading deletion.
- Restriction changes after information has already been used elsewhere.
- Durable tasks: the acceptance transaction, dispatch, claims, leases, fencing and the additive task API are approved in the durable-request design. Still pending: each slice's exact DDL and payloads (reviewed with that slice), resource schemas beyond chat tasks, and operation-specific reconciliation.
- Notification channels, freshness/expiry policy, cancellation/revocation bounds.
- Companion identity/transport, supported desktop packaging and containment.
- DEC12's retention-class schema, consent UX/storage, restricted-data enforcement, Z/R fallback and activity-record implementation; approval of live provider routes remains separate.

Resolve these at the increment that needs them. See [the durable-request design](DURABLE_REQUEST_DESIGN.md) for the architecture approved on 6 October 2026 and the decisions still pending.
