# Account deletion — fence and drain

Drafted 8 October 2026 against main `06e6f6a3` for #469 (decision 12 in [DURABLE_REQUEST_DESIGN.md](DURABLE_REQUEST_DESIGN.md) §12). **Status: Proposed. Not approved, not implemented.** No endpoint, migration or job described here exists yet. Implementation, migrations and API changes wait for owner approval of this document and of the open decisions in §9.

Read [DAEMON.md](DAEMON.md) for identity and [GLOSSARY.md](GLOSSARY.md) for terms. Retention durations, exports, backups and cascading deletion are listed as *Remaining decisions* in [DAEMON_VISION_DECISIONS.md](DAEMON_VISION_DECISIONS.md); this design proposes the protocol and names those decisions rather than taking them.

## 1. Problem

Every user-owned table references `users(id) ON DELETE CASCADE` (conversations, messages, memories, entities, web snapshots, entitlement accounts and ledger rows, auth devices, durable tasks and their attempts, operations and events). Deleting the `users` row is therefore one statement, and it is unsafe while work is in flight:

- A durable attempt can be inside a provider call or a **material tool call** (a notification, a reminder, a document) when the cascade removes its task row. The task's operation fence and its evidence vanish, yet the worker can still complete the external effect before its next fenced write notices the row is gone.
- Background jobs (memory extraction, dreaming, skill evaluation, titles, summaries, entity resolution, home suggestions) can be queued or running for the account and would write to, or fail on, rows that are gone.
- Open compute reservations would be deleted unsettled, losing the conservative charge record.
- Data outside PostgreSQL (generated files on disk, Redis keys, backups, the browser) is not reached by the cascade at all.

Conversation deletion was settled separately (decision 11, implemented in #466). This document covers deleting the whole account.

## 2. Principles

1. **Fence before purge.** No new work starts and no running work can produce an effect or publish once deletion is requested, before anything is removed.
2. **Reuse the suspension fence.** Slice 1 already makes account suspension stop every durable path: the heartbeat reports it and the attempt stops, the effect fence refuses material operations, `begin_execution` refuses (and, with #478, so does preparation), and publication ends `failed`/`account_suspended`. Deletion builds on that instead of adding a parallel mechanism.
3. **Uncertain effects are disclosed, not silently erased.** An operation that may have happened is reported to the owner before the evidence is purged (§5).
4. **Resumable and idempotent.** Deletion is a durable job with persisted phases; a crash or restart resumes it, and repeating a phase is harmless.
5. **Bounded.** Every wait has a deadline derived from existing bounds (lease, attempt timeout, reservation recovery), after which the protocol proceeds safely because the database fence already holds.

## 3. State machine

A new `account_deletions` row (one per account, not cascaded from `users`) records the request and its phase. Phases advance only forward:

| Phase | Entered when | What happens |
| --- | --- | --- |
| `requested` | The owner confirms deletion (recent re-authentication required) | Row written; the account is marked suspended for deletion (§4.1); all sessions except the confirming one are revoked. |
| `fenced` | Suspension committed | New submissions are refused; queued tasks are cancelled; queued background jobs for the account become no-ops (§4.2). |
| `drained` | No task holds an unexpired lease and no reservation is open, or the drain deadline passed | Running attempts have stopped (or their leases lapsed); reservations are settled or recovered (§4.3). |
| `reported` | The uncertain-effects report is produced | Operations whose outcome is `started`/`unknown` are summarised for the owner (§5). |
| `purged` | Database rows are removed | The `users` row is deleted (cascade); out-of-database data is removed (§6). |
| `completed` | Propagation finished | Only the non-content deletion record remains (§7). |

The deletion job runs on the existing arq worker, woken like durable tasks (a sweep finds deletions not yet `completed`), and holds a lease on the `account_deletions` row so only one worker advances it. Each phase is a separate transaction.

## 4. Fence and drain

### 4.1 Fence
- In one transaction: insert the deletion row, set `entitlement_accounts.status = 'suspended'` (with a reason distinguishing deletion from billing suspension), and revoke auth devices and sessions other than the confirming one. Suspension is the existing, already-enforced stop signal; nothing else needs to learn a new state.
- From this commit no new work for the account reaches a provider: request-bound work is refused by compute admission, and a durable task accepted after the fence fails at the worker's suspension check without a provider call. (Implementation note: durable acceptance should also refuse the account outright, so nothing new is queued.) Replays still resolve, since a retry of an earlier accepted request must not run anew, and observers keep working so the owner can watch work stop.

### 4.2 Cancel
- Queued durable tasks are cancelled in the same way as conversation deletion cancels them (terminal `cancelled`, no attempt starts).
- Running tasks receive a cancel request; their attempts also see the suspension at the next heartbeat (at most 10 s) and stop, after the 2 s stop grace if a provider is mid-call.
- Background jobs: each user-scoped job checks the account's deletion state at start and exits without effect when a deletion exists. (Implementation note: one shared guard used by every user-scoped job, tested per job.)

### 4.3 Drain
- Wait until no task of the account is `running` with an unexpired lease and no `entitlement_reservations` row of the account is `open`.
- **Deadline:** lease (45 s) + attempt timeout (600 s) + reservation recovery (2 × `request_timeout_s`), about 13 minutes with current settings. After it, any remaining lease has expired, so the database fence already rejects every write from a stale worker; remaining open reservations are settled at their full hold (the conservative rule already used for lost attempts).
- The owner sees "Stopping your running work" with the count of tasks still stopping; deletion cannot be undone from this point unless §9 decision 1 adds a grace period.

## 5. Uncertain effects

Before purge, the job collects operations whose outcome is `started` or `unknown` (an effect that may have happened), plus `needs_attention` tasks. Each item lists the tool name, the non-content target summary already stored at the fence (for example a notification channel), and timestamps — never message content.

The report is shown to the owner on the deletion screen and offered as a download, before purge. What happens to it afterwards is §9 decision 2: either it is discarded with the account (the owner has seen it), or a minimal tombstone (tool name, time, outcome; no target, no content, no user identifier beyond an opaque deletion id) is retained for a short, fixed period for support and dispute handling.

## 6. Purge and propagation

1. **Database:** delete the `users` row in one transaction; the cascade removes every user-owned row. Retained financial records are handled per §9 decision 3 *before* the cascade (anonymised copy or exemption), never by keeping the user row.
2. **Generated files on disk** (documents, images, audio, video): removed by owner prefix in the same job, then verified by listing. The existing `cleanup_generated_files`/`cleanup_generated_images` cron jobs remain the backstop.
3. **Redis:** live task channels need no action (they carry no data at rest); per-user rate-limit, budget and cache keys are deleted by pattern; queued arq jobs for the account are no-ops by §4.2.
4. **Browser:** the confirming session signs out, which already clears drafts and pending-submission keys (and held submissions, once the draft-key change for #476 lands). The service worker never stores backend responses once #474's change lands, so no cached account data remains.
5. **Providers:** default routes are zero-data-retention (DEC12), so nothing is held provider-side; opt-in retention routes (when DEC12's R routes exist) need provider-side deletion requests, recorded per route (§9 decision 5).
6. **Backups:** a backup taken before deletion still contains the account. Restores must replay recorded deletions before the restored database serves traffic; backups age out under the backup retention policy (§9 decision 4).

## 7. Record of the deletion

After `completed`, only the `account_deletions` row remains: an opaque deletion id, the phase timestamps, counts (tasks cancelled, operations reported, files removed) and, if decided, the tombstones from §5. It holds no email, name, content or device data. It exists so a restore can re-apply the deletion and so support can confirm a deletion happened.

## 8. Tests (deterministic, no paid inference)

| Scenario | Expected |
| --- | --- |
| Deletion while a task is mid provider call | Heartbeat sees the suspension; execution stops within the stop grace; nothing is published; the task ends `failed`/`account_suspended`; purge waits for it. |
| Deletion between a material operation's fence row and its call | The operation is reported as uncertain before purge; no second attempt runs. |
| Worker crash during the drain | The lease lapses; the deletion job's sweep resumes; the deadline is still honoured. |
| Deletion job crash in each phase | Restarted job resumes at the persisted phase; repeating a phase changes nothing. |
| Queued background job for the account | Exits without effect after the fence. |
| Replay of an earlier accepted request during deletion | Resolves to the existing task (or 404 after purge); never runs anew. |
| Open reservation at the deadline | Settled at its full hold before purge. |
| Restore of a pre-deletion backup | The recorded deletion is re-applied before serving. |
| Another account | Unaffected throughout (tenant isolation). |

## 9. Decisions for the owner

1. **Grace period.** Immediate fencing with no undo (proposed), or a delay (for example 24 hours) during which the account is suspended but can be restored.
2. **Uncertain-effect tombstones.** Show and discard (proposed), or retain minimal tombstones for a fixed period (for example 30 days).
3. **Financial records.** Whether entitlement ledger and receipt rows must outlive the account (legal or accounting retention), and if so in what anonymised form.
4. **Backups.** Backup retention period, and confirmation that restores replay recorded deletions.
5. **Provider-side deletion.** Required only once DEC12's opt-in retention routes exist; confirm it is out of scope until then.
6. **Operator-initiated deletion.** Whether the same protocol applies (proposed: yes, with the operator as the confirming actor and the report delivered to the account's email).

## 10. Relationship to slice 1

Slice 1 ships with `DURABLE_CHAT_ENABLED` off and does not rely on the cascade to stop work (decision 12). This design is listed in #477 as a gate for enabling the flag; the gate can be met either by implementing this protocol or by an explicit owner decision that account deletion stays unavailable while the flag is on.
