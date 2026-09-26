# Durable requests — architecture decision draft

Date: 26 September 2026. **Status: PROPOSED; schema, API and execution architecture not approved.**

Product behaviour is approved in [DEC01–DEC10](DAEMON_VISION_DECISIONS.md). This draft translates it into architecture choices; it does not silently approve them. The dated working-tree assessment is in [DAEMON_RECONCILIATION.md](DAEMON_RECONCILIATION.md); [VISION_INTEGRATION_REPORT.md](VISION_INTEGRATION_REPORT.md) distinguishes that snapshot from current-main PR verification.

## 1. Objective and scope

Implement **broad assistant continuity**: ordinary answers, research and existing supported tools survive client closure through durable acceptance, observable task state and safe interruption recovery (V01/V02/V03/V06/V07, DEC04–DEC06/DEC09–DEC10, AC03/AC09/AC12).

Preserve one conversational interface. Background continuation is not a separate user mode. Accepted means recorded and tracked, not an unconditional completion promise. Keep existing account/provider qualification and budget enforcement. Do not enable unregistered advisor tools or retired media execution, or interpret unqualified routes as working capability. Preserve current main's working encryption metrics, council roles and advisor-event/trace compatibility per the PR-port clarification in the decision record.

The first architecture should support account-owned work and future optional projects without claiming restricted-project enforcement exists. A companion, general shell sandbox, new service or dependency is not a prerequisite. No database migration, task endpoint or SSE contract is introduced by this document.

## 2. Design choice A — source of truth and durable acceptance

### A1 — PostgreSQL task state; existing arq as a wake-up mechanism (recommended)

- Persist an account-owned task and its accepted input references before acknowledging acceptance.
- Persist dispatch intent atomically with acceptance, using a transactional outbox or a recoverable pending-work scan. Do not rely on an uncoordinated database insert followed by a Redis enqueue.
- Workers claim tasks using durable ownership/lease information. Duplicate queue delivery must not create duplicate logical work.
- Redis/arq can wake workers, but loss of its queue must not erase accepted work.
- Client stream attachment is observation, not ownership of execution lifetime.

**Tradeoff:** reuses current PostgreSQL/arq infrastructure and supports queryable state, but requires new records, claims, reconciliation and explicit transaction tests. Outbox versus scan is a remaining mechanism choice; approval of A1 alone does not select one silently.

### A2 — arq jobs as execution authority, PostgreSQL projection for task history

**Tradeoff:** potentially less dispatch machinery initially, but acceptance/recovery must prove no acknowledged job disappears between Redis and PostgreSQL. Queue result TTL and Redis durability become part of the user-facing guarantee. This is a weaker fit with current durable-state requirements unless those gaps are explicitly solved.

### A3 — dedicated durable workflow engine

**Tradeoff:** could supply retries/checkpoints, but adds dependencies, operational surface and migration work. Requires separate approval and evidence that current infrastructure is insufficient. Not recommended merely to resemble the conceptual architecture.

**Approval needed:** select authority/acceptance approach before schema or queue integration.

## 3. Proposed responsibilities and state invariants

Logical responsibilities, not approved table names:

| Responsibility | Required information / invariant |
| --- | --- |
| Task | Authenticated owner, objective, accepted input references, status, budget/policy context, result references, auto-resume choice and timestamps. Client-supplied owner IDs are never authority. |
| Attempt | Particular worker run/lease, timing, checkpoint and outcome. A model change or retry does not create a new user task. |
| Operation | Stable identity, operation class, authorised inputs/destination, attempt/outcome and reconciliation evidence for material effects. |
| Artifact/resource | Owner, opaque identity, storage reference, content metadata, provenance and lifecycle/scope. Public filename possession is not access authority. |
| Activity | Observable status/progress/results and blockers, without sensitive prompt dumps or hidden reasoning requirements. |

Candidate states: accepted/queued, running, waiting for input/approval/resource, paused for capacity/policy, completed, failed and cancelled. An uncertain material effect needs an explicit reconciliation state or substate. Names and representation remain proposed.

Invariant requirements:

1. Acknowledged acceptance survives client/API-process failure and queue loss under the documented durability boundary.
2. Only one valid claim can advance a task at a time; stale attempts cannot publish a newer task's result.
3. “Completed” follows durable output/outcome publication, not a final model token or successful queue return alone.
4. Client disconnect does not cancel accepted work; an explicit cancel is separately authorised and raced against commit.
5. Waiting consumes no inference loop. Clarification permits independent progress without guessing the blocked material choice.
6. Capacity pause persists progress and interruption-notification intent. User-disabled auto-resume survives restart and overrides a scheduled wake-up.
7. Resume rechecks permissions, budget and input currency. Material input changes/uncertain effects do not silently reuse old authority.
8. Retries cannot bypass compute settlement, resource restrictions or side-effect reconciliation.

## 4. Design choice B — client integration and observation

### B1 — additive task API, chat as a submission/status view (recommended)

Expose authenticated task creation/status/control and attach chat messages to task identity. Existing `/chat` can become a compatibility adapter once its migration behaviour is approved. A status lookup must work even if live streaming is unavailable; clients can reattach for progress. Decide persisted event replay versus snapshot-plus-live updates explicitly.

**Tradeoff:** clean independent task identity and cross-client lookup; requires API/frontend changes and a compatibility plan. Do not overload old SSE fields with new semantics.

### B2 — retain chat-only submission with conversation/message status as the task surface

**Tradeoff:** fewer new concepts initially, but multi-attempt identity, non-chat views, control and replay still need durable contracts. A message row alone is not a recovery design.

**Approval needed:** choose public interaction contract and compatibility scope before endpoint/SSE/client changes. Existing OpenAI-compatible completions may need a separately stated guarantee; do not silently extend or break that protocol.

## 5. Design choice C — owned resources and legacy artifact handling

The original working-tree assessment found missing download ownership checks (#312). Current main at PR preparation (`2bf65150`) already scopes generated downloads and writes to authenticated owner namespaces through `orchestrator/artifacts.py`; unowned root-level files are not served through those routes. Preserve that enforcement. A durable resource registry, task provenance, versioning and lifecycle remain separate work: age-based cleanup is not a user-approved workspace retention contract.

### C1 — PostgreSQL ownership metadata with existing filesystem storage initially (recommended starting point)

Extend the existing authenticated owner namespaces with opaque resource IDs and server-controlled storage keys; authorize using the approved metadata contract before returning bytes. Bind outputs to task/account and publish metadata/results atomically as far as the selected storage mechanism permits. Explicitly handle orphaned files/metadata and incomplete output writes. Preserve access restrictions and encryption/backup requirements; existing namespace enforcement is not a task/version/retention registry.

**Tradeoff:** avoids a new service, but durability across worker/API hosts requires a verified shared-storage deployment contract. File encryption, backup, capacity and retention remain explicit design requirements.

### C2 — PostgreSQL metadata plus object storage

**Tradeoff:** better fit for independent hosts and large artifacts, but requires storage/provider credentials, cost/policy review and deployment approval. Signed URLs do not replace owner checks or deletion policy.

### Legacy files: decision required before any migration or access expansion

- **Continue denying unowned files pending explicit reconciliation (recommended):** preserve current main's fail-closed behaviour and existing bytes; require trustworthy provenance or controlled owner import before access. Document the compatibility impact of any later migration.
- **Backfill from existing evidence:** allow only demonstrably unique ownership from trusted records. Ambiguous, missing or cross-account references remain unowned; filenames and a requester's assertion do not prove ownership.

Do not delete legacy files, infer all files belong to a singleton account or silently make existing download links public. Do not implement universal 24-hour deletion as though it were the approved workspace retention policy. Exact retention settings and deletion propagation require a separate explicit decision.

## 6. Supported-operation and recovery inventory

Before implementation, derive the active registry under qualified deployment policy and map each operation into a recovery class. This table identifies categories to audit, not a completed runtime certification.

| Category | Candidate recovery rule | Evidence needed |
| --- | --- | --- |
| Ordinary answer/inference | New attempt may regenerate from committed inputs; disclose interruption. Settle prior uncertain provider usage conservatively. | No double result publication, preserved partial output and correct account budget across restart. |
| Search/fetch/research | Reads may retry only within current policy, freshness, egress and budget bounds. External read calls can still incur costs and disclose data. | Source provenance, input policy propagation and bounded repeated calls. |
| Deterministic document output | Stable operation identity plus atomic publish/check for existing output. | Crash between file creation, metadata commit and task completion cannot duplicate or lose a committed result. |
| Memory writes | Deduplicate/reconcile the logical write and preserve provenance/scope. | Restart cannot repeatedly create or supersede memory for one operation. |
| Notifications/reminders/HTTP writes | Treat as potentially material external effects, not automatically retryable. | Provider idempotency or trustworthy effect reconciliation; otherwise pause with uncertainty. |
| Retired/unqualified capabilities | Remain unavailable. | Honest denial and preserved task state; no fallback that relaxes qualification. |

Broad continuity does not mean every tool uses the same retry policy. No unsafe replay is necessary to preserve a request: a recoverable, truthful waiting/reconciliation state is valid.

## 7. Budget and notification contracts to finalise

Preserve `account_compute` and the account reservation ledger across worker dispatch. The authenticated owner and task policy must survive queue boundaries; an in-process context variable is not durable ownership. Whole-task limits must compose with per-call limits, retries and known/unknown usage.

Choose whether a task reserves a bounded run allowance or admits each step against a cumulative task ceiling. Both need fair concurrency and release/settlement rules; neither may erase a task when denied. Do not select pricing or enable routes as part of this migration.

Persist interruption notification intent with the pause. Delivery can retry independently without resuming work. Choose supported notification channels and suppression/deduplication behaviour before promising phone delivery. A visible task record alone is not necessarily an interruption notification.

## 8. Incremental implementation plan after approval

1. Repair baseline integration and approve A/B/C plus the necessary legacy/resource lifecycle choices.
2. Implement durable acceptance, task ownership/query and worker claims, with real PostgreSQL/Redis fault tests.
3. Move ordinary answer execution behind the durable lifecycle; keep live chat as observation and preserve compatibility.
4. Integrate research and the declared existing tool set by recovery class, including owned artifact publication. No milestone claim until the declared broad set passes.
5. Add explicit cancel/clarification, capacity pauses, interruption notification and persisted auto-resume control.
6. Verify policy/freshness rechecks, cross-account denial and true cross-client reopen. Do not surface restricted projects until their resource/derivative enforcement is verified.

These steps are implementation ordering inside broad Stage 1, not a return to the rejected document-only milestone.

## 9. Acceptance/fault tests required

- Response/connection lost immediately before and after acceptance: no acknowledged request disappears; submission replay does not duplicate it.
- Database commit succeeds but Redis enqueue fails; worker restart and queue loss: recover accepted work under the documented contract.
- Two workers claim concurrently; stale worker attempts to publish after lease replacement: one valid task progression.
- Close the PWA during an answer, research and each supported tool recovery class; reopen on another signed-in client.
- Crash before/after an external effect and before/after output metadata commit; no blind repeat and no false completed state.
- Cancel during execution/publication: report effects already performed and preserve evidence of uncertainty.
- Exhaust capacity: saved state plus interruption notification; disable auto-resume, restart, restore capacity, verify it stays halted.
- With auto-resume enabled, recheck revoked authority, changed inputs, stale task context and current budget before execution.
- Deny other-account task/control/resource access. Verify source restrictions on retrieval, outputs and derived memory for any supported restricted scope.
- Run unchanged backend/frontend/documentation/security gates; passing mocks alone does not demonstrate crash or deployment durability.

## 10. Approval boundary

**Pending:** A (durable authority/acceptance mechanism), B (API/client compatibility), C (resource storage/legacy access), task-budget composition, retention/notification details and exact state/event schema.

**Already approved:** product defaults DEC01–DEC10 and the baseline instructions in the decision record, including the subsequent PR-port clarification preserving current main's working metrics and advisor-event compatibility. Baseline bug fixes can proceed without representing this draft as accepted architecture.
