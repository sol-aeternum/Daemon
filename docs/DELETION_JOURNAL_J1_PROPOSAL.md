# J1 — bounded deletion-journal implementation proposal

**J1a source implementation approved by the owner on 12 October 2026;
source substrate implemented, no rollout authorized.** Prepared against
`main` `24e034c043e04dc9cb823dc68d09759181ec8bd0`. This qualifies the independent
POSIX option selected in [#469's newer owner decisions][decisions], under
[ACCOUNT_DELETION_DESIGN.md §6.6](ACCOUNT_DELETION_DESIGN.md). It does not reopen
approved deletion/reset behaviour or authorize live action.

## 1. Settled baseline and recommendation

- P0.2 [#508](https://github.com/sol-aeternum/Daemon/pull/508) and P0.5
  [#512](https://github.com/sol-aeternum/Daemon/pull/512) are merged source
  prerequisites. P0.5 is migration **045**, not the formerly proposed 048.
  Neither implements deletion/reset; deletion/reset remain **Not started**, and #477
  remains the enablement gate. Do not duplicate their implementations.
- Preserve §9 decisions 1–7, newer **D8** purchased-credit refund/operator
  remediation (promotional/admin forfeiture requires acknowledgement), **R1/R2**
  90-day audit/seven-day terminal-identity retention, **R3** seven-day log age
  expiry plus size caps, **S1** verified shared POSIX artifacts, and **J1**
  independent POSIX journal qualification. S1 does not qualify the journal.
- Recommend **one J1a source-only PR: durable journal substrate, non-cascading
  applied markers and fail-closed startup barrier**. No deletion/reset endpoint,
  account-state DDL, purge handler, full fence/drain, artifact store/catalog,
  provider adapter, secret payload, live migration or rollout.
- J1a proves append/commit/replay mechanics with a test-only PostgreSQL
  projection, not real account replay. Until concrete handlers ship, a configured
  runtime with unapplied entries **cannot start**.

## 2. Storage and durability contract

One private journal directory on an independently managed POSIX filesystem,
outside PostgreSQL data, `BACKUP_DIR`, artifact storage and source checkout.
It is never replaced, rolled back or copied from a database backup during a
restore. All backend/worker processes use the **same directory/inodes** on one
host. No NFS, multi-host append, object storage or distributed lock claim.
Independent lifetime is required; surviving host/disk loss needs separately
qualified recovery, not a second folder on the same disk.

Provision explicitly while runtimes are stopped. Proposed files:

| File | Contract |
| --- | --- |
| `journal.meta` | Immutable format version and random deployment `journal_id`; synced once. Expected id also lives in deployment configuration, outside DB backups. |
| `.journal.lock` | Persistent advisory-lock inode; never unlink or replace while participants exist. |
| `entries.log` | Append-only v1 length-framed canonical UTF-8 JSON, maximum 64 KiB/frame; global contiguous sequence starting at 1. |
| `HEAD` | Durable journal id, last sequence and SHA-256 chain digest; atomic replacement after a synced append. Detects loss of an acknowledged suffix if the log alone is truncated. |

Directory is owned by service uid, mode 0700; files are owned, single-link regular
files, mode 0600. Trusted ancestors/mount administrators remain a requirement.
Open relative to a pinned directory descriptor, nofollow; reject symlinks,
unexpected files, identity replacement and unsafe permissions. Missing files at
normal startup fail: never silently initialize an empty journal. SHA-256 detects
accidental corruption, not malicious rewriting by its owner.

Each frame contains only version/id, sequence, event UUID, opaque account UUID,
optional operation UUID/kind, account and operation predecessor sequences,
previous global digest, command digest, transition kind, original UTC decision
timestamp and typed transition fields. Its digest covers the complete canonical
frame. UUIDs are not emails. No arbitrary payload dictionaries, targets, URLs,
raw suspension text or loggable exceptions. J1a admits only non-secret structural
transition fields; §6.6's encrypted revocation handles/destinations and redacted
report snapshots require later separately approved payload/key lifecycles.

J1a's exact proposed event-field allowlist (in addition to the common envelope):

| Event | Fields |
| --- | --- |
| `requested`, `reset_requested` | Prior status enum; prior suspension-reason code; complete effective cause set as sorted `(cause_code, opaque_cause_id)` pairs; positive fence generation; original drain/revocation/billing deadlines; `no_grace` and legal/abuse reason code for deletion only (reason required when `no_grace` is true, optional otherwise). |
| `grace_started` | Exact grace-start timestamp and grace deadline, seven days apart. |
| `reset_report_ready` | Exact report-ready timestamp and acknowledgement deadline, seven days apart. |
| `reset_acknowledged` | Original acknowledgement timestamp. |
| `restored`, `purged`, `reset_purged`, `reset_cancelled` | Complete effective cause set after the transition; exact resulting generation; cancellation reason enum for `reset_cancelled`. |

Unknown fields and free-text reasons fail validation. Deadline eligibility uses
the original decision timestamp under locks, not a caller-supplied timestamp;
only the trusted transaction control path supplies that value. Operational
phase/effect proof is still the future concrete handler's responsibility.

**Append order**, under an exclusive `.journal.lock`:

1. Validate metadata, HEAD and entire chain; derive account/operation heads and
   retry index from the log, never an independently mutable index. Re-read and
   revalidate from sequence 1 **on every append while holding the file lock**;
   no process-local cached prefix, last offset or head can authorize an append.
2. Resolve duplicate identity first (§3); otherwise check both expected heads
   and closed transition grammar. Allocate sequence and immutable fields.
3. Write the complete frame with a short-write loop; `fsync(entries.log)`.
4. Write/fsync a private HEAD temporary file; atomically replace HEAD and fsync
   the directory. Only now return success; release the file lock before awaiting
   DB commit. Initial provisioning also fsyncs new files and parent directory.

Write/fsync/rename/lock timeout, ENOSPC, EIO, invalid frame/version/sequence,
hash/predecessor mismatch, unexpected entry or unavailable mount fails closed:
no DB transition/marker, acknowledgement or external action. An error after bytes
were written means **outcome unknown**, not "not appended". Keep the same retry
identity; keep the authority barrier closed until recovery.

On restart, a complete valid suffix beyond HEAD may be an append interrupted
between steps 3 and 4. **If no temporary exists, or the temporary passes the
checks below**, re-fsync the suffix under exclusive lock and durably advance HEAD;
preserve it as authoritative intent even if its caller never saw success.
Only the implementation's recognizable private HEAD temporary is recoverable;
use the one fixed name `.HEAD.pending`, created exclusively under the file lock.
It must be a safe regular file with a complete valid candidate id/sequence/digest
matching the verified log and no earlier than current HEAD. Recover HEAD from
the complete log, then unlink that verified temporary and fsync the directory.
An incomplete/invalid temporary is preserved and blocks, not discarded by name.
Consequently a crash during the HEAD temporary's write can require operator
repair even when log frames are complete. This is a deliberate conservative
J1a availability limitation, not a promise of automatic recovery at every
instruction boundary. Successful append followed by DB commit failure remains
automatically recoverable; partial journal metadata remains fail-closed.
Unknown files are preserved and block. Partial frame, invalid suffix or HEAD
ahead of log blocks the **whole journal**; never auto-truncate, skip a record,
reuse its sequence or proceed for "unaffected" accounts. Repair needs operator
investigation against independent evidence, outside J1a.

This trades availability/full-log scan cost for a small auditable implementation.
Lock wait is 5 s and startup replay budget 300 s; timeout fails startup unready,
never bypasses replay. **Owner clarification on 12 October 2026:** fail-closed
cancellation waits for in-flight filesystem cleanup rather than abandon its
lock/descriptors; a hung mount may delay process exit beyond the budget. This
safe-cleanup limitation is approved for source-only J1a, not rollout clearance.
Read in bounded chunks, but always validate the full log
under lock; this is not incremental validation. Head/retry maps grow with retained
accounts/events. Segmentation/compaction is not this first PR.

## 3. Compare-and-append, retry identity and lock ordering

Every append compares `expected_account_seq` and, for operation entries,
`expected_operation_seq`. Null means no predecessor. Registry entries advance
the account head even with no operation; operation entries advance both heads.
A stale account predecessor is refused even if the operation predecessor matches.
Validate operation ownership, type and legal successor as well as heads:

- deletion: `requested` → `grace_started` → exactly one of `restored`/`purged`;
  `no_grace` is explicit on the request and permits purge, never restore;
- reset: `reset_requested` → `reset_report_ready` → `reset_acknowledged` →
  `reset_purged`, or `reset_cancelled` while awaiting acknowledgement only;
- suspension/registry/report/namespace records have explicitly typed successors
  in later slices; no extension registry or catch-all event in J1a. Valid but
  unsupported event kinds block replay rather than getting marked applied.

J1a's validator tests structural paths, not evidence that drain/report/export/
storage prerequisites completed. No runtime producer calls it yet. Later
handlers enforce those conditions under DB locks before append. A closed
operation cannot reopen or receive a second terminal event; legitimate post-purge
registry cleanup is account-scoped.

The log-derived account grammar permits at most one open deletion/reset, even
with fresh CAS heads: a second operation's request is refused while one is open.
After `restored`/`reset_cancelled`/`reset_purged`, a genuinely new operation needs
a new UUID; `purged` permanently ends account operations for that opaque id.

Use a stable caller-generated **event UUID** per logical action and operation
UUID per deletion/reset attempt, surviving connection loss/rolled-back DB
transactions. Store a canonical **command digest** over account, operation,
action and caller arguments. Exclude journal-assigned sequence, predecessors,
decision time and generation allocations: a retry returns the original frame,
not newly generated deadlines/namespaces. Same event UUID plus same command
returns its original sequence even after the head advances; changed account,
operation or command under that UUID is an idempotency conflict, with no append.
Future HTTP idempotency payloads need separate API approval, not added here.

All DB producers/recovery use this lock order:

1. Transaction-scoped account advisory lock, deterministic domain-separated
   SHA-256-to-int64 mapping of opaque account UUID. Collisions serialize extra
   accounts, never coalesce identity; compare full UUIDs.
2. Entitlement/fence row if present, then operation row if present, `FOR UPDATE`.
   A new request has no operation row; post-purge cleanup needs no user row.
3. File lock only for verification/append, released before DB commit.

Never hold the file lock while waiting for a DB lock. Under account/row locks,
reconcile **all account entries without matching applied markers** before testing
a new transition. Return an existing open operation to a repeated request;
competing kind conflicts. A new operation requires the earlier one closed and a
new explicit action identity. Commit failure cannot admit a second attempt or a
purge after a durable restore.

## 4. Journal-first application and immutable recovery facts

The journal is authoritative for accepted intent, not proof an external effect
ran. Each transition: lock/reconcile, validate condition, append durably, apply
DB mutation and insert exact `(journal_id, seq, digest)` marker **in that same
transaction**, commit, then acknowledge. Lost commit response is resolved by the
marker, never a new operation. Application failure leaves pending intent; no
compensating conflicting append.

Recovery scans **every entry in sequence order**, not latest state. Exact marker
skips it. Missing marker applies entry and marker atomically; same sequence with
another digest, duplicate event, marker for an absent frame, wrong journal id or
unsupported handler stops recovery. Time is not application evidence; a current
DB watermark cannot replace per-entry markers.
An exact marker also matches the frame's event/account/operation ids; those
denormalized columns are not allowed to contradict the digest's frame.

Record and restore rather than recalculate:

- Request time, prior status and **complete suspension-cause set**; remove only
  this operation's cause on cancellation/restore. No active-account default.
- Generation allocated at original fence; future allocation exceeds every
  generation in this account's journal and DB. Replay reinstalls the recorded
  generation at its sequence position, never increments per retry.
- Exact drain/revocation/billing/propagation deadlines once established; retries
  and DB restore consume original windows, not another day.
- `grace_started`'s seven-day deadline and `reset_report_ready`'s seven-day
  acknowledgement deadline. Durable timely restore/ack wins even if commit
  failed and recovery is after expiry. Without `grace_started`, resume fence
  and establish grace only once reporting completes, as §6.6 requires; never
  invent a past grace deadline at request time.
- Zero-grace reason as a closed non-content code; no restore on replay.
- Namespace names, publication-set hashes, cleanup references and registry
  intents exactly as recorded once storage/adapter contracts are approved.

Replay of `requested` re-cancels earlier nonterminal tasks before applying
`restored`; cancelled work stays cancelled. A reset cutoff affects pre-fence
content, not content created after a reset already marked applied in the backup.
A later cancelled/restored operation cannot erase an earlier operation's effect.

**External effects never re-dispatch merely because a DB marker vanished.**
Replay rebuilds control/evidence and queries authoritative outcomes by original
reference. `report_send_started` suppresses automatic resend even without
`report_sent`; ambiguous delivery goes to the operator. Provider submit intent
is not permission to resubmit on replay; uncertain lookup is not absence.
Namespace transitions inspect original source/destination, not new names.
Ongoing idempotent revocation/billing/deletion reconciliation, where approved,
uses original reference/deadline and independent outcome evidence. No inference,
material notification, charge/refund or submission reruns from restored tasks.
J1a has no external-effect dispatcher or provider calls.

## 5. Restore/startup barrier

DB restore is an **offline operator procedure**: stop and prove termination of
backend, workers and effect-producing jobs; withdraw routing; restore DB only,
keeping journal and expected deployment id untouched. Do not restore under
running pools. Startup cannot fence already-running processes against an
unauthorized in-place DB restore; that is an operational prerequisite.
Never unset either journal setting on an activated deployment, especially when
restoring a pre-binding dump: a DB snapshot alone cannot attest it never had a
journal, and an unconfigured process cannot discover an arbitrary external path.

On **every configured startup**, one **exclusive** PostgreSQL session advisory
recovery lock, with a common fixed two-int key `(J1_namespace_tag, 1)`, elects one
replayer across backend/worker processes. Never use the shared-lock variant.
The two-int key space is separate from §3's bigint account-lock key space;
reserve the tag in the implementation to avoid other two-int protocol locks.
PostgreSQL 16's [advisory-lock function contract][pg-locks] explicitly states
that these two key spaces do not overlap. The leader verifies the whole journal
and restored marker set, then applies missing entries in global sequence
order, taking account/operation locks as in §3. Others wait unready and verify
independently after the leader finishes. No durable `ready=true` shortcut from
a previous DB snapshot; process-local readiness comes only from this pass.

Run barrier before FastAPI lifespan yields, **any** startup DB mutation (including
development-pepper initialization or first-boot recovery), backfills/session
cleanup/attestation tasks, or arq jobs/cron. Current hooks are
`orchestrator/main.py:289–326` and `orchestrator/worker/worker.py:101–115`;
   `orchestrator/db.py` at the proposal baseline degraded DB connection errors.
 The approved clarification below makes DB mandatory in **all** modes; configured
 mode additionally requires journal schema/store. Close resources
on failure. No replay completion means no protected API/static-artifact service,
job, inference or readiness. Proxy/CDN/direct artifact origins also stay withdrawn
during restore; backend startup alone does not qualify them.

In J1a the production barrier supports verification and **empty pending sets
only**. Any unapplied entry fails because real phase handlers do not exist;
never mark it applied merely by reconstructing an in-memory state machine.
The test-only applier proves atomicity in a disposable DB, is not a runtime
fallback and never changes real accounts. Later handlers must make fence,
cancellation, purge and outside-DB reconciliation true before their markers
commit/barrier opens. No deployment before that.

## 6. Pruning remains disabled

No prune API/CLI, age-based deletion, log rewrite, marker removal or automatic
rotation in J1a. [BACKUP_RETENTION.md](BACKUP_RETENTION.md) remains authoritative:
`journal_applied_markers` is null; `journal_pruning_permitted` is false.

Future pruning needs verified **all-copy retained-backup inventory**, each dump's
own consistent applied-marker evidence, an account terminal outcome and all its
entries applied in every retained dump; closed registries must meet their own
lifecycle conditions. Missing/stale inventory or marker sets preserve everything.
Day-36 pre-purge backup still needs replay; append time and a 30-day clock do not
permit pruning. Marker retention follows §7, not the user's cascade.

## 7. Focused acceptance matrix

Use real process boundaries/POSIX locks, a disposable **real PostgreSQL** DB,
controlled UTC clock and isolated journal directory; no live services/providers.
First-PR tests use an explicitly test-only projection/phase adapter where actual
account fence/purge would be needed. Distinguish those mechanics from later
end-to-end deletion qualification.

| Scenario | Required observation |
| --- | --- |
| Crash before append, during write, after log fsync, during HEAD publication | No false acknowledgement/marker; partial frame or incomplete/invalid HEAD temporary blocks. Complete suffix with absent/valid temporary recovers same event/sequence. Inject short writes and each file/directory fsync failure; assert intentional operator-repair requirement for torn HEAD metadata. |
| Crash after durable append, before DB mutation/marker or commit | Fresh connection/replay sees missing marker; projection and marker commit together exactly once. |
| Crash/lost reply after DB commit | Same retry returns original frame; exact marker skips application; no extra operation, deadline or generation. |
| Backup snapshot between append and failed commit | Restore lacks marker despite later dump time; ordered replay applies it. Actual consistent DB snapshot/dump, not timestamp filtering. |
| Restore vs purge at exact grace deadline | Two processes contend; strictly-before restore vs at/after purge; one legal terminal append. Pre-deadline restore appended with failed commit still defeats later purge. |
| Reset acknowledgement vs timeout/owner cancel | Reconcile pending acknowledgement first; one path wins; acknowledged reset cannot cancel. |
| Registry vs request; same/different-account contenders | Both-head CAS refuses stale account head; ownership enforced; global sequences unique across processes; no deadlock. Advisory collision serializes without identity coalescing. |
| Duplicate and changed requests | Same event/command returns original after later entries; changed command/account/op conflicts; failed first commit plus request retry yields one open operation. |
| Partial/corrupt/missing storage | Partial EOF, middle corruption, sequence gap, invalid heads, HEAD ahead, wrong id/version, symlink/hardlink/replaced inode, missing mount, ENOSPC and lock timeout block all serving/markers; evidence untouched. |
| Pre-request backup; requested → restored | Apply all entries in order; earlier tasks stay cancelled; other suspensions remain. Projection now; real task/fence integration later. |
| Earlier reset, later restored deletion, older-backup replay | Earlier cutoff/generation preserved; later restore closes only itself. Backup containing reset marker leaves post-reset content alone. |
| Grace/report-ready before restore | Original seven-day deadlines/generations unchanged after late recovery; no-grace cannot restore; request without grace resumes fence, not fabricated grace. |
| Send/submission-start without completion, older restore | Test spy dispatch count remains zero in replay; unknown outcome retained, not resent/resubmitted. Actual adapter reconciliation later. |
| Startup leader crash/unsupported entry/DB outage | Backend lifespan never yields; arq runs no job; waiter takes over after connection lock release or exits unready. No startup backfill/cleanup before barrier. |
| Marker mismatch/absent-log marker/second operation | Mismatch fails closed; exact markers skip; retain/order all operations; no latest-terminal-only replay. |
| Day-36 backup/missing marker inventory | Log and markers unchanged; pruning impossible even with old terminal timestamp. |

## 8. One recommended first implementation PR and approval boundary

**PR title:** `feat: add J1 deletion-journal durability substrate and startup barrier`.

Exact proposed scope:

1. `orchestrator/services/deletion_journal/`: closed v1 types, POSIX provisioning/
   validation, compare-and-append/retry lookup, marker verification and startup
   barrier. No generic event bus, pluggable handlers or new dependency. Explicit
   offline initializer creates a **new empty** directory only; no overwrite,
   repair, import, replay-delete or prune CLI.
2. One source migration, **next contiguous number at implementation time**
   (currently `046_deletion_journal_markers.sql`), containing only:

   ```sql
   CREATE TABLE deletion_journal_binding (
       singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
       journal_id uuid NOT NULL UNIQUE
   );
   CREATE TABLE deletion_journal_applied (
       journal_id uuid NOT NULL,
       seq bigint NOT NULL CHECK (seq > 0),
       event_id uuid NOT NULL,
       account_id uuid NOT NULL,
       operation_id uuid,
       entry_digest bytea NOT NULL CHECK (octet_length(entry_digest) = 32),
       PRIMARY KEY (journal_id, seq),
       UNIQUE (journal_id, event_id),
       FOREIGN KEY (journal_id) REFERENCES deletion_journal_binding(journal_id)
           ON DELETE RESTRICT
   );
   CREATE INDEX deletion_journal_applied_account
       ON deletion_journal_applied (account_id, seq);
   ```

   No FK to users/deletion rows, so markers survive purge/reset. Binding inserted
   only by verified configured startup, including an older restored backup where
    absent; mismatching binding never replaced. This approved DDL is now the source
    migration `046_deletion_journal_markers.sql`; only disposable test schemas were
    migrated. No live migration is authorized.
3. Two shared settings: `DAEMON_DELETION_JOURNAL_DIR` (absolute existing directory),
   `DAEMON_DELETION_JOURNAL_ID` (expected UUID). Both unset/empty means unconfigured
   **only when DB has no binding/markers**; one missing, invalid path/id, or
   unconfigured settings with existing binding/markers fails. Neither permits
   clearing a previously activated deployment: pre-binding backup restore needs
    unchanged external configuration. Missing DB cannot prove disabling a
    previously configured journal safe. No prune/ignore-corruption/skip-replay
   flag. Update `orchestrator/config.py`, `.env.example`, both backend/worker
   `docker-compose.yml` environments and `tests/test_env_surface_parity.py` in
   the same future commit. No renamed keys, credentials or new Docker service.
   No automatic volume creation: separately approved mounts expose the same
   private directory in both services; absent mount fails startup.
4. Internal APIs only: `append(command, event_id, expected_account_seq,
   expected_operation_seq) -> immutable_entry`, `verify() -> verified_view`,
   `verify_applied(connection, verified_view)` and
   `require_recovery_barrier(pool, journal)`. Closed errors: conflict, unknown
   append outcome, corrupt/unavailable storage and recovery required. No HTTP/
   SSE change. Future account transitions get concrete transactions, not an
   injectable production callback in J1a.
5. Barrier in backend/worker startup **before any side-effectful hook**; configured
   mode cannot degrade without DB. Real POSIX/PG crash, CAS, marker/startup-negative
   tests above, plus unconfigured-mode regressions preserving #508/#512 behaviour.
   Document source-only limitations.
6. Run repository backend/frontend/repo gates for that implementation, fresh
   independent review and one exact-head Codex code review; human approval still
   required. No default paired Security Review/duplicate unchanged-head request.
   Runtime security/persistence changes need the configured pre-execution
   supervisory review as well as ordinary independent review.

**Unavoidable gaps, not reasons to weaken the barrier:** full replay depends on
later approved account fence/cause/generation DDL, real cancellation/purge,
S1 artifact/registry adapters, external-outcome reconciliation and payload erasure
keys. An append-only plaintext report cannot meet §6.6's discard deadline; J1a
rejects it, not retains it indefinitely. HEAD cannot detect coordinated rollback
of both log and HEAD; trusted independent storage/operator restore procedure is
necessary. Disaster recovery, backup-marker extraction/all-copy evidence,
size/throughput qualification and continuous retention remain pending. None is
represented as shipped by J1a.
Torn HEAD temporary metadata intentionally blocks automated recovery even with
an intact log (§2); operator repair is outside this first PR. Approval must accept
this availability tradeoff rather than mistake it for complete crash recovery.

**Single consolidated approval request:** approve the **source-only J1a PR**
above: single-host independent POSIX/framed-hash-chain/HEAD protocol (64 KiB
frames, 5 s lock/300 s startup budget), account+operation CAS/stable retry identity,
two-table DDL/two shared settings, internal-only API/startup barrier, real PG/POSIX
  test matrix, **no production phase applier and no pruning**. This authorizes
implementation and a source migration file only—not live migration/provisioning,
endpoints, fence/drain/purge, deployment, enablement, storage/key operations or
paid inference. All such authority remains separate.

**Approval clarification (Astra pre-execution review, 12 October 2026):** the
owner explicitly selected mandatory PostgreSQL startup even with empty journal
settings. Missing/unreachable DB blocks both backend and worker; an unconfigured
runtime proceeds only after positive absence-of-binding/markers evidence from an
accessible database. This supersedes the previous DB-less startup degradation,
not the settled #508/#512 identity/cache behaviour. No new bypass setting.

[decisions]: https://github.com/sol-aeternum/Daemon/issues/469#issuecomment-6072269138
[pg-locks]: https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS
