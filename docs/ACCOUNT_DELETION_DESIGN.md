# Account deletion — fence and drain

Drafted 8 October 2026 against main `06e6f6a3` for #469 (decision 12 in [DURABLE_REQUEST_DESIGN.md](DURABLE_REQUEST_DESIGN.md) §12). **Status: design APPROVED by the product owner on 8 October 2026 (decisions in §9). Not implemented.** No endpoint, migration or job described here exists yet. As with the durable-request design, the exact DDL, endpoint payloads and UI copy are reviewed in the implementation PRs, and implementation, migrations and API changes each need owner approval.

Read [DAEMON.md](DAEMON.md) for identity and [GLOSSARY.md](GLOSSARY.md) for terms. This design settles account deletion's retention, backup and cascading-deletion questions (listed as *Remaining decisions* in [DAEMON_VISION_DECISIONS.md](DAEMON_VISION_DECISIONS.md)); retention for other purposes remains open there.

## 1. Problem

Every user-owned table references `users(id) ON DELETE CASCADE` (conversations, messages, memories, entities, web snapshots, entitlement accounts and ledger rows, auth devices, durable tasks and their attempts, operations and events). Deleting the `users` row is therefore one statement, and it is unsafe while work is in flight:

- A durable attempt can be inside a provider call or a **material tool call** (a notification, a reminder, a document) when the cascade removes its task row. The task's operation fence and its evidence vanish, yet the worker can still complete the external effect before its next fenced write notices the row is gone.
- Background jobs (memory extraction, dreaming, skill evaluation, titles, summaries, entity resolution, home suggestions) can be queued or running for the account and would write to, or fail on, rows that are gone.
- Open compute reservations would be deleted unsettled, losing the conservative charge record.
- Some account data does not cascade at all: every hosted account owns a personal tenant whose `owner_user_id` is `ON DELETE RESTRICT` (so deleting the user row fails), the identity audit log keeps `normalized_email` and `provider_subject` with `user_id` set to `NULL`, email challenges and signup invites are keyed by email, and learned skills (`skill_projections` and the files in `data/skills`) carry no owner.
- Data outside PostgreSQL (generated files on disk, queued job payloads in Redis, backups, the browser, grants held at third parties, logs kept by external tool services) is not reached by the cascade at all.

Conversation deletion was settled separately (decision 11, implemented in #466). This document covers deleting the whole account, and resetting it (§10).

## 2. Principles

1. **Fence before purge.** No new work starts and no running work can produce an effect or publish once deletion is requested, before anything is removed. The fence covers every account-scoped write, not only durable tasks (§4.1).
2. **Reuse the suspension fence.** Slice 1 already makes account suspension stop every durable path: the heartbeat reports it and the attempt stops, the effect fence refuses material operations, `begin_execution` and preparation refuse, and publication ends `failed`/`account_suspended`. Deletion builds on that instead of adding a parallel mechanism.
3. **Uncertain effects are disclosed, not silently erased.** An operation that may have happened is reported to the owner before the evidence is purged (§5).
4. **Deletion means everything learned from the account, and we say what is kept.** Derived data (memories, entities, summaries, embeddings, learned skills) goes with its sources; nothing learned from the account survives elsewhere, since Daemon does not train models on account data. The deletion screen lists exactly what is retained and why (§7).
5. **Resumable and idempotent.** Deletion is a durable job with persisted phases; a crash or restart resumes it, and repeating a phase is harmless.
6. **Bounded.** Every wait has a deadline derived from existing bounds (lease, attempt timeout, reservation recovery), after which the protocol proceeds safely because the database fence already holds.

## 3. State machine

A new `account_deletions` row (one per account, not cascaded from `users`) records the request and its phase. Phases advance forward, except that a deletion in its grace period can be cancelled by a restore (§4.4):

| Phase | Entered when | What happens |
| --- | --- | --- |
| `requested` | The owner confirms deletion (fresh re-authentication, export offered first) | Row written, recording the account's prior status and suspension reason; the account is suspended for deletion; other sessions revoked; third-party grants revoked (bounded, §4.1); Daemon-managed billing cancelled; the deletion is appended to the journal outside the database (§6.6). |
| `fenced` | Suspension committed | New submissions are refused; queued tasks are cancelled; queued background jobs for the account become no-ops (§4.2). |
| `drained` | No task holds an unexpired lease, no account-scoped background job is running and no reservation is open, or the drain deadline passed | Running attempts and jobs have stopped (or their leases lapsed); reservations are settled or recovered (§4.3). |
| `reported` | The uncertain-effects report is produced | Operations whose outcome is `started`/`unknown` are summarised for the owner (§5). |
| `grace` | Immediately after `reported` | Seven days. The account stays suspended with its data intact; the owner can sign in to download the report and an export, or restore the account (§4.4). |
| `purged` | The grace period ended | The `users` row is deleted (cascade); out-of-database data is removed (§6). |
| `completed` | Propagation finished | Only the non-content deletion record remains (§7). |
| `restored` | The owner restored during `grace` | The account returns to its prior status and suspension reason (an account that was already suspended stays suspended); the deletion record is closed. Cancelled tasks stay cancelled. |

The deletion job runs on the existing arq worker, woken like durable tasks (a sweep finds deletions not yet `completed` or `restored`, including grace periods that have ended), and holds a lease on the `account_deletions` row so only one worker advances it. Each phase is a separate transaction.

## 4. Fence, drain and grace

### 4.1 Fence
- Before confirming, the owner is offered a download of the account's data (conversations, memories, files) and is told exactly what deletion removes and what is retained (§7).
- In one transaction: insert the deletion row, set `entitlement_accounts.status = 'suspended'` (with a reason distinguishing deletion from billing suspension), and revoke auth devices and sessions other than the confirming one.
- In the same phase, outside the transaction: **revoke every third-party grant Daemon holds for the account** (OAuth tokens such as the Google sign-in grant, and any connected-app authorisations as they are added) at the provider's revocation endpoint. Revocation is retried with backoff for at most 24 hours. A grant still unconfirmed after that (an outage or a permanent error) is listed in the deletion report with instructions for revoking it at the provider. Either way, stored credentials are deleted no later than purge, so a failing provider can never strand a deletion or keep a credential alive.
- **Cancel Daemon-managed billing** so nothing renews. A subscription billed through an app store that Daemon cannot cancel is named on the confirmation screen with instructions, rather than left to surprise the owner.
- From this commit no new work for the account reaches a provider: request-bound work is refused by compute admission, and durable acceptance refuses the account outright, so nothing new is queued. Replays still resolve, since a retry of an earlier accepted request must not run anew, and observers keep working so the owner can watch work stop.
- **Every account-scoped write is fenced, not only tasks.** A shared guard checks the account's deletion and suspension state inside the transaction of every account-scoped commit (conversation and message writes, memory and entity writes, summaries, titles, skills, snapshots, files), and every account-scoped write route refuses a suspended account. A background job that started before the fence therefore cannot publish after it, and no route can add content while a deletion or reset purges it.

### 4.2 Cancel
- Queued durable tasks are cancelled in the same way as conversation deletion cancels them (terminal `cancelled`, no attempt starts).
- Running tasks receive a cancel request; their attempts also see the suspension at the next heartbeat (at most 10 s) and stop, after the 2 s stop grace if a provider is mid-call.
- Background jobs: each user-scoped job checks the account's deletion state at start and exits without effect when a deletion exists, and its final writes pass the shared guard above. Running user-scoped jobs record a heartbeat against the account, so the drain can wait for them (§4.3). (Implementation note: one shared guard and one job registry used by every user-scoped job, tested per job.)

### 4.3 Drain
- Wait until no task of the account is `running` with an unexpired lease, no user-scoped background job of the account has a live heartbeat, and no `entitlement_reservations` row of the account is `open`.
- **Deadline:** lease (45 s) + attempt timeout (600 s, which also exceeds the background job timeout) + reservation recovery (2 × `request_timeout_s`), about 13 minutes with current settings. After it, any remaining lease has expired, so the database fence already rejects every write from a stale worker; remaining open reservations are settled at their full hold (the conservative rule already used for lost attempts).
- The owner sees "Stopping your running work" with the count of tasks still stopping.

### 4.4 Grace and restore
- The grace period is **seven days** from `reported`. During it the account is suspended: no work runs and no new work is accepted, but its data is intact.
- **Restore:** signing in during grace (a fresh sign-in, not an existing session) shows only the deletion status, the report, the export and *Restore account*. Confirming restore lifts the suspension and closes the deletion record as `restored`. Revoked third-party grants and cancelled billing are not reinstated automatically; the owner reconnects and resubscribes. Cancelled tasks stay cancelled.
- When grace ends, the job proceeds to purge. There is no restore after that.
- **Restore and purge cannot both happen.** Both contend on the same row lock with one conditional transition: a restore commits only if the phase is still `grace` and the grace deadline has not passed; the worker advances to purge only if the phase is still `grace` and the deadline has passed. Exactly one commits; the other sees the changed phase and stops (a late restore is told the account was deleted).

## 5. Uncertain effects

Before grace, the job collects operations whose outcome is `started` or `unknown` (an effect that may have happened), plus `needs_attention` tasks. Each item lists the tool name, the non-content target summary already stored at the fence (for example a notification channel), and timestamps — never message content.

The report is shown on the deletion screen and stays downloadable throughout the grace period. **It is discarded with the account at purge; no tombstones are kept** (decision 2).

## 6. Purge and propagation

1. **Database, in order, in one transaction:**
   1. financial records the law requires to keep are copied out anonymised (decision 3);
   2. records that do not cascade are removed: the account's identity-audit rows (by user id, and by its normalized email and provider subjects), email challenges, Google nonce challenges and signup invites for its email;
   3. account-owned learned skills: their `skill_projections` rows and canonical files in `data/skills` (skills shipped with Daemon or shared by an operator are not account-owned and are kept). This needs an owner/provenance column on skill projections, added by the implementation;
   4. the personal tenant's memberships, tenant-owned rows and the personal tenant itself, which otherwise blocks the user deletion (`ON DELETE RESTRICT`);
   5. the `users` row, whose cascade removes every remaining user-owned row: conversations and messages, memories and their embeddings, entities and the relationship graph, summaries, dream, extraction and retrieval logs, web snapshots, home suggestions, durable tasks with their attempts, operations and events, entitlement rows and auth devices.
2. **Generated files on disk** (documents, images, audio, video): removed by owner prefix in the same job, then verified by listing. The existing `cleanup_generated_files`/`cleanup_generated_images` cron jobs remain the backstop.
3. **Redis:** live task channels need no action (they carry no data at rest); per-user rate-limit, budget and cache keys are deleted by pattern. Queued, deferred and finished arq job entries for the account are **deleted, not just made no-ops**, because some carry content in their arguments (`generate_title` enqueues the user's message): before the database purge the job collects the account's conversation and task identifiers and deletes the matching job and result keys. The implementation also changes user-scoped jobs to carry identifiers only, never content.
4. **Browser:** the confirming session signs out, which clears drafts and pending-submission keys (and held submissions, with #479); the service worker never stores backend responses (with #482, for #474), so no cached account data remains.
5. **Providers:** default inference routes are zero-data-retention (DEC12), so no inference provider holds the account's content, and Daemon does not train models on account data. Provider-side deletion requests for inference are out of scope until DEC12's opt-in retention routes exist (decision 5). **Tool services are different:** the default web search service (Brave) is used under an approved standard-retention exception and keeps API query logs for up to 90 days ([SEARCH_SERVICE_APPROVALS.md](SEARCH_SERVICE_APPROVALS.md)); search queries can contain account content, and Daemon cannot delete them. The deletion screen discloses each retained tool service and its period.
6. **Backups and the deletion journal:** a backup taken before purge still contains the account, and a backup taken before the request does not even contain the `account_deletions` row. Every deletion request is therefore also appended to a **deletion journal kept outside the database and its backups** (append-only, its own durable store and retention of at least the backup period), holding only the opaque user id and a keyed hash of each login identifier. **Backups are kept for 30 days**, and every restore merges the journal and re-applies each recorded deletion (or pending grace) before the restored database serves traffic (decision 4).

**What the owner is told:** the account's content leaves Daemon's live systems when the seven-day grace period ends, and leaves backups within 30 days after that (at most 37 days from the request). Separately, search queries already sent to the web search service may remain in its logs for up to 90 days after they were made.

## 7. Record of the deletion

After `completed`, only the `account_deletions` row remains: an opaque deletion id, the phase timestamps and counts (tasks cancelled, operations reported, files removed). It holds no email, name, content or device data. It exists so a restore can re-apply the deletion and so support can confirm a deletion happened.

Retained beyond the account, and listed as such on the deletion screen:
- the deletion record above, and the journal entry (§6.6);
- logs kept by external tool services under their own retention, such as web search queries (§6.5);
- financial records only where accounting law requires them (decision 3): if billing runs through a payment processor, the processor holds the invoices and Daemon keeps none; if Daemon itself is the record of charges, an anonymised copy (amounts, dates, plan) linked only by the opaque deletion id, for the statutory period.

## 8. Tests (deterministic, no paid inference)

| Scenario | Expected |
| --- | --- |
| Deletion while a task is mid provider call | Heartbeat sees the suspension; execution stops within the stop grace; nothing is published; the task ends `failed`/`account_suspended`; purge waits for it. |
| Deletion between a material operation's fence row and its call | The operation is reported as uncertain; no second attempt runs. |
| Worker crash during the drain | The lease lapses; the deletion job's sweep resumes; the deadline is still honoured. |
| Deletion job crash in each phase | Restarted job resumes at the persisted phase; repeating a phase changes nothing. |
| Queued background job for the account | Exits without effect after the fence. |
| Replay of an earlier accepted request during deletion | Resolves to the existing task (or 404 after purge); never runs anew. |
| Open reservation at the deadline | Settled at its full hold before grace. |
| Restore during grace | Suspension lifted; data intact; cancelled tasks stay cancelled; grants and billing not reinstated. |
| Grace ends | Purge runs; restore is no longer offered. |
| Third-party grant revocation fails transiently | Retried until confirmed; the stored credential is deleted only after revocation is confirmed or the provider reports the grant unknown. |
| Billing at deletion | Daemon-managed billing cancelled at the fence; an app-store subscription is named on the confirmation screen. |
| Derived data | After purge, no memory, entity, embedding, summary or skill row references the account. |
| Restore of a pre-deletion backup | The recorded deletion is re-applied before serving. |
| Hosted account with a personal tenant | The tenant and its rows are removed before the user; the purge does not abort. |
| Identity records | No identity-audit, challenge or invite row for the account's email or provider subject remains. |
| Learned skills | The account's skill projections and files are removed; system and shared skills remain. |
| Background job running at the fence | Its final write is refused by the guard; the drain waits for its heartbeat to end. |
| Queued title job for the account | Its Redis job and result keys are deleted before the database purge. |
| Restore at the grace deadline | Exactly one of restore and purge commits. |
| Already-suspended account restores | Returns to its prior suspension, not to active. |
| Backup from before the request is restored | The journal re-applies the deletion before serving. |
| Revocation endpoint down for a day | Deletion proceeds; the grant is listed as unconfirmed in the report; the credential is deleted. |
| Reset (§10) | Content and derived data removed only after the report is acknowledged; account, sign-in and subscription kept; running work stopped first. |
| Another account | Unaffected throughout (tenant isolation). |

## 9. Owner decisions (8 October 2026)

| # | Decision |
| --- | --- |
| 1 | **Grace period of seven days**, restored by a fresh sign-in and confirmation. Running work is fenced immediately either way; the grace period only delays the purge. |
| 2 | **Uncertain-effect report shown and discarded**; no tombstones. |
| 3 | **Financial records:** retained only where accounting law requires, outside the account (see §7); the statutory period is a fact to confirm with the person who keeps the accounts, not a design choice. |
| 4 | **Backups kept 30 days**; restores replay recorded deletions before serving. |
| 5 | **Provider-side deletion** out of scope until DEC12's opt-in retention routes exist. |
| 6 | **Operator-initiated deletion** follows the same protocol, with the operator as the confirming actor and the report sent to the account's email; the grace period applies except for legal or abuse cases, which record an explicit reason. |
| 7 | **Additions after a competitor review** (§11): derived data deleted and retention disclosed (principle 4, §7); Daemon-managed billing cancelled and app-store subscriptions named (§4.1); third-party grants revoked first (§4.1); export offered before deletion (§4.1); and *Reset* offered as an alternative (§10). |

## 10. Reset

*Reset* removes the account's content and everything learned from it, but keeps the account, its sign-in and its subscription. It uses the same protocol on the content only: fence (the account is suspended for the duration), cancel, drain, report, then purge of conversations, messages, memories, entities, summaries, skills, web snapshots, files and tasks. The `users` row, auth devices, entitlement account and billing remain; third-party grants are kept. Reset requires a fresh sign-in and a typed confirmation and offers the export first; it has no grace period. The purge waits for the owner to **acknowledge the uncertain-effects report** (shown on the reset screen and downloadable, and shown again at the next sign-in if the owner left). If it is not acknowledged within seven days, the reset is cancelled and the account returns to its prior status with its content intact. After the purge the report is discarded, as for deletion. The suspension is lifted when the purge completes.

## 11. Comparison with other assistants (8 October 2026)

Dated evidence from public documentation; all three agent products launched in August–September 2026, so details may change. Sources: [Meta: manage your Muse data](https://www.meta.com/help/artificial-intelligence/2225571704857152/), [TIME on Muse](https://time.com/article/2026/10/06/meta-muse-ai-agent-privacy/), [OpenAI: deleting your account](https://help.openai.com/en/articles/6378407-how-to-delete-your-account), [Vellum on Dots](https://www.vellum.ai/blog/official-openai-dots-breakdown), and third-party guides for xAI ([AI Toolbox](https://www.ai-toolbox.co/grok-management-and-productivity/how-to-delete-grok-xai-account-2026), [Composio](https://composio.dev/content/guide-to-frok-bot)).

| | OpenAI Dots | Meta Muse | xAI Grok / Grok Bot | Daemon (this design) |
| --- | --- | --- | --- | --- |
| Undo after deletion | None; permanent | Not stated | 30 days, by signing in | 7 days, by a fresh sign-in |
| Removal from systems | Within 30 days, except as required or permitted by law | Not stated | Within 30 days, except legal, compliance or safety | At grace end; backups within 30 days after |
| In-flight agent work at deletion | Not documented | Not documented | Not documented | Fenced, drained, uncertain effects reported |
| Learned data after deletion | Not erased by disconnecting an app | "Muse may still remember" deleted information | De-identified data may be kept | Deleted with its sources; no training on account data |
| Reset without deleting | Yes (conversations, memories, scheduled tasks) | Yes (chats, files, active tasks) | Not found | Yes (§10) |
| Subscription on deletion | App-store subscriptions not cancelled | n/a | Not cancelled automatically | Daemon billing cancelled; app-store subscriptions named |
