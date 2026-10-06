# Durable requests — architecture decision

Drafted 26 September 2026; revised 6 October 2026 against main `6178f282`. **Status: architecture APPROVED by the product owner on 6 October 2026 (A1, B1, the reconnect model and the slice 1 decisions in §12). Not implemented.** Exact DDL, endpoint payloads and SSE event payloads are reviewed in the implementation PRs against this document; choice C (resource storage and legacy files), notification channels and retention remain pending.

Read [DAEMON.md](DAEMON.md) for canonical identity and [GLOSSARY.md](GLOSSARY.md) for terms. Product behaviour is ratified in [DEC01–DEC12](DAEMON_VISION_DECISIONS.md). This document translates it into architecture; approval of the architecture is recorded in §12 and does not authorise deployment, unrelated rewrites or expanded permissions. DEC12's opt-in R routes remain implementation-gated; current runtime routing stays Z-only. The dated working-tree assessment is in [DAEMON_RECONCILIATION.md](DAEMON_RECONCILIATION.md); [VISION_INTEGRATION_REPORT.md](VISION_INTEGRATION_REPORT.md) distinguishes that snapshot from current-main PR verification.

## 1. Objective and scope

Implement **broad assistant continuity**: ordinary answers, research and existing supported tools survive client closure through durable acceptance, observable task state and safe interruption recovery (V01/V02/V03/V06/V07, DEC04–DEC06/DEC09–DEC10, AC03/AC09/AC12). Target experience: a user submits a task, closes the phone, and later sees the saved result, or an honest account of what is blocked, on another signed-in device. Execution survives client disconnects and recovers predictably from worker restarts.

Preserve one conversational interface. Background continuation is not a separate user mode. Accepted means recorded and tracked, not an unconditional completion promise. Keep existing account/provider qualification and budget enforcement. Do not enable unregistered advisor tools or retired media execution, or interpret unqualified routes as working capability. Preserve current main's working encryption metrics, council roles and advisor-event/trace compatibility per the PR-port clarification in the decision record.

The architecture supports account-owned work and future optional projects without claiming restricted-project enforcement exists. A companion, general shell sandbox, new service or dependency is not a prerequisite. No exactly-once guarantee is made for external effects (§9).

## 2. Current main behaviour this design replaces

Inspected at `6178f282`: `orchestrator/main.py` (`/chat`, `_account_frames`), `orchestrator/daemon.py` (`stream_sse_chat`), `orchestrator/worker/`, `orchestrator/compute_runtime.py` (`account_compute`), `orchestrator/entitlements/{service,receipts}.py`, `orchestrator/auth.py`, `orchestrator/routes/conversations.py`, `orchestrator/tools/builtin.py`, `frontend/app/api/chat/route.ts` and migrations 004/013/037/039/042.

- **Execution is request-bound.** `_account_frames` cancels its producer task when the response consumer goes away; `stream_sse_chat` marks the assistant row `cancelled` when `is_disconnected()` fires. The frontend Stop button works by aborting the fetch. Once disconnect stops cancelling, Stop needs an explicit cancel call.
- **Partial output already persists.** The assistant row is inserted as `streaming` and its content is updated about once a second. History reads exclude `streaming`, `error` and `cancelled` rows.
- **Uncertain spend is already conservative.** An open reservation older than 2 × `request_timeout_s` is settled at its full hold (`reconcile_expired_reservations`); provider receipts can later lower, never raise, that charge.
- **arq constraints.** Global `job_timeout=300`. With the default `keep_result`, reusing a `_job_id` while its result key exists silently drops the enqueue (the memory-extraction path already works around this). Redis runs `redis:7-alpine` with default persistence, so queue loss is a realistic failure.
- **The chat tool registry has side effects.** `create_default_registry` registers `http_request`, `notification_send`, `reminder_set`, `skill_manage`, `generate_document` and memory write/promote/demote alongside read tools. Even "ordinary answers" can cause material effects.
- **Existence leak.** `/chat` returns 403 for another account's conversation; `routes/conversations.py` returns 404.

## 3. Design choice A — source of truth and durable acceptance

**Approved: A1 — PostgreSQL task state; existing arq as a wake-up mechanism. Dispatch intent is the task row itself, recovered by a scan; there is no separate outbox table.**

- **Atomic acceptance.** One transaction creates the conversation when new, inserts the user message and inserts the task with `status='queued'` and `next_wakeup_at=now()`. Acceptance is acknowledged (task id returned, observation stream started) only after commit.
- **Recoverable dispatch.** A sweep selects `queued` tasks with `next_wakeup_at <= now()` and `running` tasks whose lease has expired, using `FOR UPDATE SKIP LOCKED`, and enqueues a wake-up for each. It runs as an arq cron every 15 seconds. A task woken in the last 60 seconds (`last_wake_at`) is skipped, so a worker backlog gets at most one pending wake-up per task per minute rather than one per sweep (revised in review of #461). The enqueue issued right after the acceptance commit is only a latency optimisation. A failed enqueue or a flushed queue therefore delays a task by up to the 60-second wake-up suppression window (if a wake-up was just recorded), then one sweep interval plus scheduling and backlog delay; it never loses it. The sweep itself runs on arq, so while Redis or every worker is unavailable no task is dispatched; the accepted row stays durable and recovery starts once Redis and a worker return.
- **Wake-up jobs carry only the task id** (no owner, no content), use job id `task:{id}:{wake_seq}` with `keep_result=0` to avoid arq's result-key deduplication, and `max_tries=1`. `wake_seq` is a durable column on the task. The post-acceptance enqueue and every sweep wake-up advance it atomically with recording the wake-up (`last_wake_at`), so a wake-up for an expired lease always gets a new job id. arq would otherwise reject the id while the stale attempt's job is still running. Retries are owned by PostgreSQL, not arq. Task execution is its own arq function with a timeout matched to the attempt deadline, not the global 300-second default.
- Client stream attachment is observation, not ownership of execution lifetime.

Rejected: **A2** (arq jobs as execution authority with a PostgreSQL projection) makes Redis result TTLs and persistence part of the user-facing guarantee. **A3** (a dedicated workflow engine) adds dependencies and operational surface without evidence that current infrastructure is insufficient.

## 4. Records and state model

Logical records. Names are the intended ones; exact columns and indexes are reviewed in the slice 1 migration PR.

| Record | Contents and invariants |
| --- | --- |
| `tasks` | Owner (`user_id`, never client-supplied), conversation and message references (§10), encrypted accepted input, status, idempotency key and request hash, lease owner/epoch/expiry, attempt count and cap, cancel request, result message, terminal code/reason, timestamps. Partial unique index: at most one non-terminal task per conversation. |
| `task_attempts` | One worker run: epoch, worker id, timing, outcome (`completed`, `lost`, `failed_retryable`, `failed_terminal`, `cancelled`), encrypted partial content kept for disclosure, reservation scope ids. A retry or model change does not create a new user task. |
| `task_operations` | Stable identity for each material tool call: tool name, effect class, authorised target summary, attempt epoch, `started_at`, `completed_at`, outcome and reconciliation evidence. The target summary and evidence are ciphertext; plaintext columns are limited to the tool name, effect class, epoch, outcome and timestamps. |
| `task_events` | Append-only, sequence-numbered lifecycle events (status, routing, tool call/result summaries). Low volume; no token deltas and no hidden reasoning. |
| Artifact/resource | Owner, opaque identity, storage reference, provenance and lifecycle. Governed by choice C (§13), not slice 1. |

Task and attempt inputs, partial content, operation targets and evidence, and event payloads that may contain user content are encrypted with Fernet like messages. Plaintext columns hold identifiers, states, codes, counters and timestamps only (revised in review of #461).

**Slice 1 states:** `queued → running → completed | failed | cancelled | needs_attention`. `needs_attention` means work was interrupted and an outcome is uncertain; it is never retried automatically. Later slices add `waiting_input`, `waiting_approval`, `paused_capacity` and `reconciling`.

**Retry rules**

| Cause | Rule |
| --- | --- |
| Attempt lost (crash, expired lease) with no material operation started in any attempt | Regenerate from committed inputs, up to `max_attempts = 2` in total. Keep the earlier partial content on the attempt record and disclose the interruption in message metadata. |
| Retryable provider/compute error with no material operation started in any attempt | Re-queue with backoff via `next_wakeup_at`, within the same attempt cap. |
| Capacity or budget denied | Slice 1: terminal `failed` with the capacity code, an honest message and a manual retry. DEC09 pause, notification and auto-resume arrive in slice 4. |
| Policy, route or validation failure | Terminal `failed`. |
| Any whole-attempt retry (lost attempt, retryable error, or anything else) after a material operation started | `needs_attention` with the operation evidence preserved; no automatic regeneration from the original input. A later slice may resume from a proven checkpoint that cannot repeat the operation; slice 1 does not. |
| Attempt cap exhausted | Terminal `failed` with code `interrupted`. |

The material-operation check guards **every** whole-attempt retry path, not only crash recovery. The current tool loop makes further provider calls after a tool runs, so a notification can succeed and the next provider round can then fail retryably; regenerating from the original input would send it again even though no worker crashed.

**Result publication.** The result is the existing encrypted assistant message; each task owns exactly one assistant row, which later attempts overwrite under the fence. `completed` is set only by one transaction that locks the task, checks the lease epoch, marks the message `complete`, marks the task `completed` and appends the lifecycle event. A final token or a successful queue return is not completion. Follow-up jobs (memory extraction, title, skill evaluation, contextual home refresh) remain best-effort after commit; the extraction watermark covers a missed run on the next turn.

**Invariants**

1. Acknowledged acceptance survives client/API-process failure and queue loss under the documented durability boundary.
2. Only one valid claim can advance a task at a time; stale attempts cannot publish over a newer attempt's result.
3. "Completed" follows durable output/outcome publication, not a final model token or successful queue return alone.
4. Client disconnect does not cancel accepted work; an explicit cancel is separately authorised and raced against commit.
5. Waiting consumes no inference loop. Clarification permits independent progress without guessing the blocked material choice.
6. Capacity pause persists progress and interruption-notification intent. User-disabled auto-resume survives restart and overrides a scheduled wake-up.
7. Resume rechecks permissions, budget and input currency. Material input changes and uncertain effects do not silently reuse old authority.
8. Retries cannot bypass compute settlement, resource restrictions or side-effect reconciliation.

## 5. Worker ownership, leases and fencing

- **Claim.** One transaction locks the task row (`SELECT ... FOR UPDATE`) and decides everything before any new attempt starts:
  1. Not due (queued with a future `next_wakeup_at`, or running with a live lease): no-op. Duplicate deliveries end here.
  2. Running with an expired lease: mark the previous attempt ended. If cancel was requested, finish `cancelled`. Otherwise apply the §4 retry rules: if a material operation exists, finish `needs_attention`; if `attempt_count` has reached `max_attempts`, finish `failed`/`interrupted`. In those cases no attempt starts.
  3. Only a retry-eligible task is then claimed, still in the same transaction:

  ```sql
  UPDATE tasks SET status = 'running', lease_epoch = lease_epoch + 1,
         lease_owner = $worker, lease_expires_at = now() + $lease,
         attempt_count = attempt_count + 1,
         content_generation = lease_epoch + 1, content_delta_seq = 0
  WHERE id = $1
  RETURNING lease_epoch, ...
  ```

  The retry rule is therefore enforced atomically with the claim, not by a later check (revised in review of #461). The same transaction clears the result message's content, so a new `content_generation` never exposes the previous attempt's text, and a claim that cannot safely be admitted (for example, a lost attempt's holds cannot yet be settled) is handed back without consuming the attempt. Lease arithmetic uses the database clock only, never worker clocks.
- **Heartbeat.** Roughly every 10 seconds, the worker extends a 45-second lease with `... WHERE id = $1 AND lease_epoch = $e AND status = 'running' RETURNING cancel_requested_at, <account suspended>`. Zero rows means the worker has been fenced out: it cancels the provider stream immediately and writes nothing further. A heartbeat that raises (for example while PostgreSQL is unavailable) is bounded by its own timeout; the worker keeps a conservative local lease deadline from the last confirmed renewal and, if renewal cannot be confirmed before it, stops execution exactly as if fenced (revised in Codex review of #461). The returned flags carry cancellation and account suspension to the worker. Once the attempt has committed its own outcome the heartbeat stops, so post-completion work is never mistaken for fencing.
- **Fenced writes.** Partial-content updates, operation records and the final publish each lock the task row (`FOR UPDATE`) and check `lease_epoch` and `status = 'running'` in the same transaction. Cancellation does not change the epoch, so the writes that matter also check it under the same lock (revised in review of #461): an operation record is refused once `cancel_requested_at` is set or the account is suspended, and the final publish ends the task `cancelled` (keeping the content) or `failed`/`account_suspended` instead of `completed`. Partial content may still be saved after a cancel request, so the cancelled task keeps what it produced.
- **Deadlines.** Each provider call keeps the compute layer's whole-call deadline (`request_timeout_s`), so the existing 2 × timeout reservation recovery never settles a live call. A whole attempt, including every tool round, is bounded separately (10 minutes) by the task job's arq timeout.
- **A lost attempt's holds.** Each attempt records its account compute scope. When its lease is taken over, that scope's still-open reservations are settled at their full hold before the recovery attempt is admitted (revised in review of #461). Without this, a dead attempt's open hold would occupy the account's concurrency slot until the generic recovery (2 × `request_timeout_s`), and a one-slot account would refuse its own recovery attempt. Durable work stays foreground for concurrency; it is never made exempt. A fenced worker that is still alive gets a settlement conflict later and is never charged twice.
- **Graceful shutdown.** Stop claiming and let in-flight attempts finish within the shutdown grace period; abandoned attempts are recovered after lease expiry.
- **Residual window.** A worker that pauses longer than its lease keeps consuming provider tokens until its next heartbeat or write fails. Duplicate spend is bounded by the lease and the attempt deadline, not eliminated.
- **PostgreSQL unavailable mid-run.** The worker cannot heartbeat or publish; it stops after its lease. No result is published without the database.

## 6. Tenant-scoped idempotency and duplicate submissions

- The client generates an `Idempotency-Key` (UUID) for each submission and stores it with the draft before sending, so an app reload resubmits with the same key.
- A unique `(user_id, idempotency_key)` index plus a `request_hash` decides duplicates. The hash is a server-keyed HMAC-SHA-256 (keyed with the validated auth pepper, domain-separated) over the canonical accepted input, so a database snapshot alone cannot be used to test guesses of the encrypted input (revised in review of #461). Same key and same hash returns the existing task and attaches to it. Same key and different hash returns **409 `idempotency_conflict`**; the existing task is never overwritten.
- Keys are scoped per account: another account's identical key creates an independent task and reveals nothing.
- Clients that send no key (older PWAs) create a new task per request. That matches today's behaviour; the duplicate risk is documented.
- **Replays come first.** A request carrying a key that this account already used is resolved (replay or 409 conflict) before per-user rate limiting, the busy check or any other new-turn admission, and is not charged as a new turn (revised in review of #461). Per-IP transport throttling still applies.
- A submission to a conversation that already has a non-terminal task returns **409 `conversation_busy`** with the active task id. This keeps history order deterministic and matches the current UI, which blocks input while streaming.

## 7. Design choice B — client integration and observation

**Approved: B1 — additive task API, with `/chat` as a compatibility adapter.**

- **Flag.** A durable-chat environment flag, added under the env-surface parity rules, switches `/chat` from request-bound execution to the adapter: accept, enqueue, then attach an observer that emits the **existing** SSE frame types. Disconnect only detaches the observer.
- **Additions.** An `X-Daemon-Task-Id` response header and one new `task` SSE event announcing acceptance (task id and status). Endpoints: `GET /tasks/{id}` (snapshot, plus a bounded list of the task's material operations: tool, outcome, timing and the non-content target summary), `GET /tasks/{id}/events` (observe), `POST /tasks/{id}/cancel`. Cancelling a `queued` task terminalises it immediately in the same transaction, and a claim never starts an attempt for a task with a pending cancel (revised in review of #461). `GET /conversations/{id}` also returns the conversation's active task and its latest task, so another device can find a task that already ended `cancelled`, `failed` or `needs_attention` (revised in review of #461). The conversation's messages include the task's result row with its terminal status. Old SSE fields keep their meaning.
- **Stop means cancel.** The frontend Stop button calls `POST /tasks/{id}/cancel`; closing the app, navigating away or aborting the fetch only detaches. Cached older PWAs only detach when Stop is pressed, and the work continues; this is accepted for the rollout window.
- **Excluded from slice 1 (request-bound and labelled as such):** council commands and interviews, home-suggestion acceptance, and the OpenAI-compatible `/v1/chat/completions`, which keeps its synchronous protocol with no new durability guarantee.
- **Database unavailable.** In durable mode `/chat` returns **503** (retryable). The present "continue without persistence" fallback does not apply, because "accepted" must mean saved.
- Status lookup works without live streaming; clients can reattach at any time.

## 8. Reconnect semantics

**Approved: snapshot + durable lifecycle events + live deltas that are not persisted.** A full persisted per-token event log was rejected: heavy encrypted write volume for no recovery benefit, since the snapshot already holds the content.

- **Content generation.** Streamed content is scoped to a `content_generation`, equal to the lease epoch of the attempt that wrote it. A later attempt that overwrites the assistant row starts a new generation.
- **Snapshot** (`GET /tasks/{id}`): task status, terminal code/reason, `content_generation`, the partial or final content of that generation, the `delta_seq` of the last delta the content includes, and the latest `task_events` sequence number.
- **Durable events** (`task_events`): replayed from a given sequence on reattach; later the basis of the DEC12 activity record. A generation change is a durable event.
- **Live deltas:** published over Redis pub/sub, each tagged with `(content_generation, delta_seq)`, where `delta_seq` increases by one per delta within a generation. Sequence numbers rather than character offsets avoid the Python code-point versus JavaScript UTF-16 indexing mismatch. A client applies a delta only when its generation equals the client's and its `delta_seq` is exactly the next one. It drops frames from an older generation, and re-fetches the snapshot on a gap or on any newer generation, replacing (never appending to) the displayed text.
- **Generation change.** On a new generation the observer takes text and `delta_seq` from the same snapshot and continues from that sequence, even though it is lower than the previous generation's. A snapshot that lags what live deltas already showed in the same generation is not treated as a replacement (revised in review of #461).
- **Terminal refresh.** On a terminal event the client re-fetches the snapshot and displays the committed content, so the final display always equals the committed result.
- **Redis unavailable:** observers fall back to polling the snapshot. Slower, still correct.

## 9. Failure windows and uncertain effects

| Window | Durable outcome and recovery |
| --- | --- |
| Crash before the acceptance commit | Nothing durable. A client retry with the same key creates exactly one task. |
| Commit succeeds, response lost | Retry with the same key returns the same task. Without a key, a duplicate task is possible (documented). |
| Commit succeeds, enqueue fails or queue flushed | The sweep enqueues the task once any 60-second wake-up suppression window has passed, within one further sweep interval plus scheduling delay. |
| Redis or all workers unavailable | Accepted rows stay `queued`; dispatch and expired-lease recovery resume once Redis and a worker return. |
| Duplicate wake-up delivery | One claim succeeds; the others are no-ops. |
| Crash after claim, before the provider call | Lease expires; the next attempt runs. No spend from the lost attempt. |
| Crash during the provider call | The provider may have generated and billed the output. The open reservation settles at the full hold and receipts may refund later. The retry regenerates and pays again. One attempt can make several separately charged provider calls (one per tool round), so the worst-case duplicate spend is about `max_attempts` × the provider calls per attempt (bounded by the plan's tool-round limit) × the per-call ceiling, all visible in the ledger (revised in review of #461). Slice 4's cumulative task ceiling tightens this. |
| Provider finished, crash before the completion commit | As above. Never `completed` without the commit. |
| Crash after the completion commit | Result safe. Reservation settles conservatively; follow-up jobs may be missed until the next turn. |
| Material tool call made, then a crash or retryable error before the attempt completes | `needs_attention`. The user is told what may have happened (for example "a notification to ntfy may have been sent"), and the task's operation list shows the evidence. Slice 1 offers no "retry anyway" control; the user decides and resubmits. The resolve/retry flow arrives with reconciliation in slice 6 (revised in review of #461). No silent repeat. |
| Stale worker resumes after takeover | Heartbeat and writes affect zero rows; its operation insert is refused. It may still spend on its open provider stream until it notices. |
| Cancel races completion | Whichever locks the task row first wins. Cancel after completion returns 409. Cancel during a material operation reports the effect as possibly performed. |
| API process crash while a client is attached | Work is unaffected; the client reattaches from the snapshot. |

**Effect fence.** Before any material tool runs, the worker inserts a `task_operations` row under the lease fence, refusing when the lease has less than a safety margin left. That distinguishes "never attempted" from "may have happened". The gap between the fence check and the actual external call can be narrowed but not closed. Effects on Daemon's own database (reminders, skills, memory rows) can later commit in the same transaction as their operation row, which makes them effectively-once; external HTTP and notification calls cannot get that guarantee.

## 10. Context references for slice 1

A task references conversation and workspace context without a memory or filesystem redesign:

- **Stored at acceptance:** `conversation_id`, `user_message_id`, `history_cutoff_message_id` (the worker rebuilds history up to that message, so later turns do not change it), the routing decision (`model_decision`, admission profile, `routing_info`), any explicit model/provider choice, `disable_memory_write`, and the prepared text content of attachments (already bounded by the request body limit).
- **Rebuilt per attempt:** memory context, preferences, timezone, skills index and system prompt; the prompt version is recorded on the attempt. A retry may therefore see newer memory; that is disclosed, not hidden.
- **Workspace:** the account is the workspace (DEC01). No project columns or file registry are added before choice C and projects exist.

## 11. Tenant isolation and permission checks

- The owner comes only from `auth.user_id`. Task queries filter on `id AND user_id` in SQL, and another account's task, control or observation returns **404**, never 403. Idempotency lookups are account-scoped.
- Workers derive the tenant only from the claimed task row. Before each attempt they recheck that the account is active and its entitlement account is not suspended, that the conversation is still owned and not deleted, and that the route is still qualified. Every store call carries `user_id`.
- Observers and snapshots require device authentication on every attach. Pub/sub channel names never leave the server.
- **Deleting a conversation** (revised in Codex review of #461; the conservative contract, flagged for owner review): one transaction locks the conversation (as acceptance does) and its active task (as claims do). A `queued` task is cancelled and deleted with the conversation. A `running` task is asked to cancel and `DELETE /conversations/{id}` returns **409 `task_running`** with its task id until the task has stopped, so an in-flight external action never loses its operation evidence. Finished tasks are deleted with the conversation, as the user asked.
- Revoking the submitting device does not cancel the account's tasks; suspension or deletion of the account does. Deletion cascades to the tasks. Suspension is rechecked during an attempt, not only before it: the heartbeat reports it and the worker stops, an operation record is refused, and the final publish ends the task `failed` (`account_suspended`) instead of publishing. Every provider call also passes per-call admission, which already refuses a suspended account (revised in review of #461).
- Budget: rate limiting and route qualification run in the API at acceptance; `account_compute(..., background=False)` runs in the worker, so durable work still counts against concurrency ceilings. An in-process context variable is not durable ownership; the task row is.

## 12. Owner decisions recorded on 6 October 2026

| # | Decision |
| --- | --- |
| 1 | A1 with dispatch recovered by a sweep over the task table; no separate outbox table (§3). |
| 2 | B1: `/chat` adapter, task endpoints, `task` SSE event, `X-Daemon-Task-Id`, `Idempotency-Key`; Stop means cancel, disconnect means detach (§6–§7). |
| 3 | Reconnect by snapshot + durable lifecycle events + ephemeral deltas scoped to a content generation and sequence (§8; revised in review of #461). |
| 4 | Slice 1 keeps the full current tool registry behind the effect fence, rather than a read-only subset (§9). |
| 5 | `max_attempts = 2` for automatic regeneration. A retry may repeat every provider call the lost attempt made, so duplicate spend is bounded per §9 (attempts × provider calls per attempt × per-call ceiling), charged conservatively (§4; wording corrected in review of #461). |
| 6 | One non-terminal task per conversation; further submissions get 409 `conversation_busy` (§6). |
| 7 | Durable mode fails closed with 503 when the database is unavailable (§7). |
| 8 | Device revocation does not cancel tasks; account suspension or deletion does (§11). |
| 9 | Slice 1 capacity denial is terminal with manual retry; DEC09 pause, notification and auto-resume complete in slice 4 (§4). |
| 10 | Add a PostgreSQL service to backend CI so race and fencing tests run instead of skipping; implemented with slice 1. |

## 13. Design choice C — owned resources and legacy artifact handling (pending)

The original working-tree assessment found missing download ownership checks (#312). Current main at PR preparation (`2bf65150`) already scopes generated downloads and writes to authenticated owner namespaces through `orchestrator/artifacts.py`; unowned root-level files are not served through those routes. Preserve that enforcement. A durable resource registry, task provenance, versioning and lifecycle remain separate work: age-based cleanup is not a user-approved workspace retention contract. Choice C is needed by slice 3, not slice 1.

### C1 — PostgreSQL ownership metadata with existing filesystem storage initially (recommended starting point)

Extend the existing authenticated owner namespaces with opaque resource IDs and server-controlled storage keys; authorize using the approved metadata contract before returning bytes. Bind outputs to task/account and publish metadata/results atomically as far as the selected storage mechanism permits. Explicitly handle orphaned files/metadata and incomplete output writes. Preserve access restrictions and encryption/backup requirements; existing namespace enforcement is not a task/version/retention registry.

**Tradeoff:** avoids a new service, but durability across worker/API hosts requires a verified shared-storage deployment contract. File encryption, backup, capacity and retention remain explicit design requirements.

### C2 — PostgreSQL metadata plus object storage

**Tradeoff:** better fit for independent hosts and large artifacts, but requires storage/provider credentials, cost/policy review and deployment approval. Signed URLs do not replace owner checks or deletion policy.

### Legacy files: decision required before any migration or access expansion

- **Continue denying unowned files pending explicit reconciliation (recommended):** preserve current main's fail-closed behaviour and existing bytes; require trustworthy provenance or controlled owner import before access. Document the compatibility impact of any later migration.
- **Backfill from existing evidence:** allow only demonstrably unique ownership from trusted records. Ambiguous, missing or cross-account references remain unowned; filenames and a requester's assertion do not prove ownership.

Do not delete legacy files, infer all files belong to a singleton account or silently make existing download links public. Do not implement universal 24-hour deletion as though it were the approved workspace retention policy. Exact retention settings and deletion propagation require a separate explicit decision.

## 14. Supported-operation and recovery inventory

Before slice 3, derive the active registry under qualified deployment policy and map each operation into a recovery class. In slice 1, every tool not shown to be safe to repeat is treated as material. This table identifies categories to audit, not a completed runtime certification.

| Category | Candidate recovery rule | Evidence needed |
| --- | --- | --- |
| Ordinary answer/inference | New attempt may regenerate from committed inputs; disclose interruption. Settle prior uncertain provider usage conservatively. | No double result publication, preserved partial output and correct account budget across restart. |
| Search/fetch/research | Reads may retry only within current policy, freshness, egress and budget bounds. External read calls can still incur costs and disclose data. | Source provenance, input policy propagation and bounded repeated calls. |
| Deterministic document output | Stable operation identity plus atomic publish/check for existing output. | Crash between file creation, metadata commit and task completion cannot duplicate or lose a committed result. |
| Memory writes | Deduplicate/reconcile the logical write and preserve provenance/scope. | Restart cannot repeatedly create or supersede memory for one operation. |
| Notifications/reminders/HTTP writes | Treat as potentially material external effects, not automatically retryable. | Provider idempotency or trustworthy effect reconciliation; otherwise pause with uncertainty. |
| Retired/unqualified capabilities | Remain unavailable. | Honest denial and preserved task state; no fallback that relaxes qualification. |

Broad continuity does not mean every tool uses the same retry policy. No unsafe replay is necessary to preserve a request: a recoverable, truthful waiting/reconciliation state is valid.

## 15. Budget and notification contracts

Preserve `account_compute` and the account reservation ledger across worker dispatch. The authenticated owner and task policy must survive queue boundaries. Whole-task limits must compose with per-call limits, retries and known/unknown usage.

**Recommended for slice 4 (not yet approved):** admit each step against a cumulative task ceiling through the existing per-call reservation ledger, rather than reserving a bounded run allowance up front, which would hold budget and concurrency for the whole task. Denial pauses the task; it never erases it. Do not select pricing or enable routes as part of this work.

Persist interruption-notification intent with the pause. Delivery can retry independently without resuming work. Choose supported notification channels and suppression/deduplication behaviour before promising phone delivery. A visible task record alone is not necessarily an interruption notification.

## 16. Staged implementation plan

The slices are increments inside broad Stage 1 (DEC10), not a reduction of its scope. No DEC10 milestone claim until the declared operation set passes slices 1–6, including true cross-client reopen.

| Slice | Content | Acceptance criteria |
| --- | --- | --- |
| 1 — Durable ordinary answers | Records in §4, acceptance/sweep/claim/fence (§3, §5), idempotency (§6), `/chat` adapter behind the flag, task endpoints, Stop-as-cancel in the frontend, snapshot and live observation (§8), effect fence (§9), CI PostgreSQL service. Native chat turns with current auth, model roster and routing. | Close the client mid-answer and reopen on another signed-in device to the same saved result or a truthful status; every §17 scenario reaches its expected durable outcome; Stop cancels; another account cannot read, control or observe a task; all repository gates pass; Feature Matrix updated. |
| 2 — Research | Longer attempts with checkpoints and their own arq function and timeout; bounded retries for repeatable reads with freshness and provenance rules. Fix #314 (fetch SSRF guards) before fetches retry automatically. | Interrupted research resumes or regenerates within bounds without unbounded repeated calls; sources keep provenance. |
| 3 — Supported tools | Map every registered tool to a recovery class (§14); internal effects commit with their operation row; document output bound to owned artifacts under choice C. Retired tools stay unavailable. | Crash before/after each tool's effect and before/after output metadata commit: no blind repeat, no false completion, no lost committed output. |
| 4 — Cumulative budgets and capacity | Task-level ceiling with per-step admission (§15); `paused_capacity`, notification intent, persisted auto-resume opt-out, rechecks before resume (DEC09). | Exhaust capacity: state saved and interruption notified; with auto-resume disabled, restart and restore capacity and the task stays halted; with it enabled, revoked authority, changed inputs and budget are rechecked first. |
| 5 — Cancellation and clarification | Cancel raced against commit with a list of effects already performed; `waiting_input` with no inference loop (DEC06). | Cancel during execution and publication reports performed and uncertain effects; a material ambiguity waits without guessing and resumes on answer. |
| 6 — Uncertain-effect reconciliation | Per-tool reconcilers (provider idempotency keys where available), resolve/retry flow for `needs_attention`, operation history in the activity view. | Every `needs_attention` case can be resolved by evidence or by explicit user choice; no automatic replay of an uncertain external effect. |

## 17. Deterministic acceptance and fault tests

**Harness.** A scripted fake for `completion_with_tools` with `asyncio.Event` gates to pause mid-stream; fake tools that count calls; a `SimulatedCrash(BaseException)` raised at named fault points (after acceptance commit, before enqueue, after claim, mid-stream, after operation insert, after tool call, before and after completion commit); leases expired by SQL (`lease_expires_at = now() - interval '1 second'`), never by sleeping. The state transitions are also a pure function with property tests that always run. Claim and fence races run against real PostgreSQL with two connections (decision 10). No paid inference.

| Scenario | Expected durable outcome |
| --- | --- |
| Crash before acceptance commit | No task or message. Retry with the same key creates exactly one task. |
| Response lost after commit | Retry with the same key returns the same task id; a different payload with that key gets 409. |
| Commit succeeds, enqueue fails or queue flushed | The sweep enqueues it; it completes exactly once. |
| Same wake-up delivered twice | One claim; `attempt_count = 1`; one provider call. |
| Crash after claim, before provider call | Lease expires; attempt 2 completes; one reservation charged. |
| Crash mid-stream | Attempt 1 `lost` with partial content kept; its reservation settles at the full hold; attempt 2 completes; two provider calls in total. |
| Provider finished, crash before completion commit | Same as mid-stream; never `completed` without the commit. |
| Crash after completion commit | `completed`; no further attempt; reservation recovered conservatively. |
| Crash after a material operation is recorded, with or without the call | `needs_attention`; the tool was called no more than once; no automatic retry. |
| Fake material tool succeeds once, then the next provider round fails retryably | `needs_attention`; recovery never invokes that tool again. |
| Stale worker resumes after takeover | Its heartbeat and writes affect zero rows; message content comes only from the new epoch; its operation insert is refused. |
| Attempt cap exhausted | Terminal `failed` with code `interrupted`. |
| Client disconnects mid-stream, reconnects on another device | Task keeps running; snapshot plus deltas reproduce exactly the final text; a forced delta gap triggers a snapshot re-fetch. |
| Retry while a client is attached: attempt 2 regenerates different text, a shorter text, and attempt 1 frames arrive late | The client resets on the new generation, drops stale-generation frames, and its displayed text equals the committed result. |
| Redis unavailable during observation | Snapshot polling still reaches the final state. |
| Cancel races completion | Whichever locks the row first wins; cancel after completion returns 409. |
| Second submission while a task is active | 409 `conversation_busy` with the active task id. |
| Account B reads, cancels or observes account A's task, or reuses A's key; forged `user_id` in the payload | 404, or an independent task for B; the forged owner is ignored. |
| Account suspended while the task is queued | Worker recheck fails the task with no provider call. |
| Cancel requested between heartbeats, then the attempt tries a material operation | The operation record is refused and the tool is never called. |
| Account suspended mid-attempt | The heartbeat reports it and the attempt stops; operation records are refused; the final publish ends `failed`/`account_suspended`, not `completed`. |
| PostgreSQL unavailable mid-attempt (heartbeats raise) | The worker stops at its local lease deadline; it makes no provider calls after the lease could have passed to another worker. |
| Conversation deleted from another device while its task runs | 409 `task_running` and a cancel request; the task and its evidence remain until it stops. A queued task is cancelled and deleted. |
| Expired lease while the stale job is still inside arq | The sweep advances `wake_seq`, so the new wake-up has a fresh job id and the takeover is not blocked by arq job uniqueness. |
| Worker dies holding a reservation on a one-slot account | At takeover the lost attempt's open hold is settled at its full hold before the recovery attempt is admitted; the recovery is not refused for concurrency. |
| Task cancelled or stopped at `needs_attention`, then the conversation is opened on another device | `latest_task` names the task and its terminal status; the result row shows its notice. |

Run the unchanged backend, frontend, documentation and security gates as well. Passing mocks alone does not demonstrate crash or deployment durability; a staging restart check precedes any release claim.

## 18. Prerequisites and approval boundary

**Prerequisites for slice 1:** a green `scripts/local_ci.sh` on current main before starting, and the CI PostgreSQL service (decision 10). Not blockers: #316 (current main persists `forced_terminal_status`; confirm and close), #312 (already scoped on main), dependency advisories #309/#418 (handled at the gate level), #86 and #90 (the sweep covers the task part of #90). #314 is a prerequisite for slice 2 only.

**Approved:** product decisions DEC01–DEC12 and the baseline instructions in the decision record, including the PR-port clarification preserving current main's working metrics and advisor-event compatibility; the architecture in §3–§11 and the decisions in §12. DEC12 approves direction, not the retention schema, consent, restricted-data enforcement, fallback or activity-record implementation; all remain gated.

**Pending:** choice C and legacy-file handling (§13), task-budget composition (§15), notification channels, retention, and review of exact DDL and endpoint/SSE payloads in each implementation PR. Baseline bug fixes can proceed independently of this document.
