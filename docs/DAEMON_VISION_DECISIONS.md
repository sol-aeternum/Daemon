# Daemon — Product decisions from the vision interview

Date: 26 September 2026. Status: **product-owner decisions; implementation not certified**.

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
- Task/resource schemas, API/SSE contracts, acceptance transactions, worker strategy and operation-specific reconciliation.
- Notification channels, freshness/expiry policy, cancellation/revocation bounds.
- Companion identity/transport, supported desktop packaging and containment.
- Changes to commercial/provider contracts or approval of live provider routes.

Resolve these at the increment that needs them. See [the durable-request design draft](DURABLE_REQUEST_DESIGN.md) for the next architecture approval boundary.
