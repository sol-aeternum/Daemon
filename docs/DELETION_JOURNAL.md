# J1a — deletion-journal source substrate

Owner-approved source-only scope, 12 October 2026. See the accepted
[J1 qualification contract](DELETION_JOURNAL_J1_PROPOSAL.md) and
[account-deletion design §6.6](ACCOUNT_DELETION_DESIGN.md).
The source substrate has focused test evidence and independent source review;
repository gates still govern PR qualification. This is not rollout qualification.

## What this slice does and does not do

The private single-host POSIX journal implements durable append, complete-chain
validation, stable retry identity and account/operation compare-and-append.
Migration `046_deletion_journal_markers.sql` adds only non-cascading binding and
applied-marker tables. Backend/worker startup checks them before startup writes
or job admission. **There is no production phase applier**: an unapplied entry
blocks startup rather than falsely recording it applied.

Deletion/reset, account fence/cause/generation DDL, full drain/purge, artifact
store/catalog, external-effect adapters and secret/report payloads are not
implemented here. #508/#512 remain merged prerequisites; #469's closed state
does not certify account deletion, and #477 remains the enablement gate.
No journal pruning, log rewrite, applied-marker expiry or backup-marker extraction
is added. `BACKUP_RETENTION.md` still forbids pruning.

The subsequent source-only [J1b account-control persistence](ACCOUNT_CONTROL_PERSISTENCE.md)
adds uncalled request/cause/generation storage, not a production phase applier.
Persisted request facts still have no applied marker and continue blocking startup;
they do not establish cancellation, session/writer guards or S1 namespace retirement.

## Startup and configuration compatibility

**PostgreSQL is now mandatory at both backend and worker startup, even with both
journal settings empty.** The owner explicitly chose this during Astra review;
absence of a journal binding/markers cannot be established during a DB outage.
Both empty/unset settings allow an accessible DB only when binding/markers are
absent. A wholly pre-J1 schema is recognized through positive catalog evidence;
partial schema or query/permission failure is never treated as unconfigured.

- `DAEMON_DELETION_JOURNAL_DIR`: absolute path to an existing private journal.
- `DAEMON_DELETION_JOURNAL_ID`: expected canonical UUID, preserved independently
  of DB backups. Either set without the other fails startup.

Both are injected into backend and worker, not frontend or migrate. No volume
is automatically created or mounted. Operators need a separately approved
independent filesystem and mounts exposing the same directory/inodes. Never
unset either setting on an activated deployment, including when restoring a
pre-binding dump. Deployment configuration and journal must not be rolled back
with the DB. Coordinated log+HEAD rollback is not detectable by a local hash chain.

One exclusive session advisory recovery lock serializes starters. Every process
checks independently; no restored `ready` bit permits bypass. Cancellation or
uncertain lock cleanup terminates the DB connection rather than leak a session
lock into its pool. Filesystem threads finish/release their locks before their
descriptors can close; a stuck filesystem can delay shutdown but never readiness.
The 300-second budget initiates fail-closed cancellation; it is not a guaranteed
process-exit deadline for a kernel-blocked filesystem operation. Cleanup waits for
that operation to release its descriptors/lock rather than race a live I/O thread.
Cancellation reports an unavailable startup even if verification subsequently
discovers corruption; the files are still preserved and rechecked next startup.
Schema checks validate object kinds, column types and positive reads, not a forensic
inventory of every constraint after manual schema alteration. The source migration
is the supported creator; out-of-band operator schema edits are not qualified.

Generation validation concerns the **original accepted transition**, not a replay
allocation: restore/cancel introduces the new generation required by design §4.2
for its rebuilt namespace; purge retains the original fence generation. Replay
reinstalls each entry's recorded resulting generation exactly (§6.6), never bumps
it again or resets every entry to the request generation. This slice creates no
namespace; its structural tests pin both rules independently.

## POSIX bytes and failure semantics

Directory mode 0700; owned single-link regular files mode 0600. Trusted ancestors
and cooperating writers are required. Relative nofollow opens and pinned directory,
lock/log/metadata identities reject replacement. Normal startup never provisions.

`entries.log` frames are a four-byte unsigned big-endian payload length, canonical
UTF-8 JSON, and a 32-byte SHA-256 footer covering **length prefix plus payload**.
Total frame size is at most 64 KiB. The footer does not hash itself. Duplicate JSON
keys, noncanonical encodings, unknown fields/versions, unsafe values, broken chains
and unsupported event kinds fail. Heads/retry maps are rebuilt from sequence 1
under the persistent lock on every append; no cached prefix authorizes writes.

Append writes/fsyncs log, then writes/fsyncs `.HEAD.pending`, replaces HEAD and
fsyncs directory before returning. A partial-write loop handles short writes.
An error after bytes may exist returns `unknown_append`; a retry retains its
event/operation UUID and returns original immutable facts after revalidating
durability. A complete suffix with absent/valid temporary is promoted, not erased.
Torn frame, contradictory metadata, partial `.HEAD.pending`, unsafe/unknown files
and unavailable storage preserve evidence and block. **Torn HEAD temporary can
require operator repair even with intact log**, the approved availability tradeoff.
There is no repair command or auto-truncation. Normal provisioning/repair remains
unauthorized; tests use only their own directories.

The source provides a deliberately offline, new-directory-only initializer:
`python -m orchestrator.services.deletion_journal init ABSOLUTE_PATH UUID`.
It refuses existing directories; failure preserves partially created files for
investigation. This is a source interface description, not a live execution request.

## Acceptance evidence boundaries

- `test_deletion_journal_model.py`: controlled-time closed grammar, immutable
  deadlines/generations, both-head conflicts, duplicate/changed identities.
  Requests require their exact `(deletion|reset, operation_id)` cause; missing or
  mismatched causes fail before append and also fail full-log replay, including
  correctly hashed frames (`test_deletion_journal_request_fence.py`).
  Zero-grace deletion requests require a recorded `legal` or `abuse` reason;
  ordinary requests may omit it. Constructor/append and correctly hashed replay
  regressions cover this invariant (`test_deletion_journal_no_grace.py`).
  Deletion purge preserves the request's entire cause set, including its own
  deletion cause; only restore/cancel/reset-purge removes its operation's cause.
  Append/replay regressions cover normal and zero-grace purge and omission of
  individual or all causes (`test_deletion_journal_purge_causes.py`).
  Actual database cause-set completeness remains the future trusted producer's
  obligation under account locks: structural validation cannot certify unseen DB
  facts. No runtime producer or generic fallback is introduced by this slice.
- `test_deletion_journal_store.py`: real POSIX files and separate processes;
  partial-frame/HEAD publication crashes, same/different-account contention,
  restore/purge and reset acknowledgement/cancel races, short writes/fsync
  ordering, corruption/preservation; repeated HEAD replacement on main FS/tmpfs.
- `test_deletion_journal_postgres.py`: real disposable schemas; exact-marker
  mismatch/pending refusal, snapshot COPY dump between append and failed commit,
  real process deaths before append/mutation/commit and after commit/lost reply,
  test-only atomic ordered multi-operation projection replay, lock-holder death
  and timeout cleanup. Earlier reset markers are not collapsed by a later restore.
- `test_deletion_journal_startup.py`: actual backend/worker initializer failure
  paths, no degraded DB fallback, no post-barrier side effects, cancellation.
- `test_deletion_journal_io.py`: generic results/exceptions and copied context;
  owned-process all-task shutdown cancellation during real POSIX verification,
  including corruption. Cancellation waits for the retained executor future,
  then propagates before the caller can close descriptors; cleanup exits naturally.

The PostgreSQL projection applier exists only in tests. Its replay/marker evidence
does not prove cancellation, content cutoff, suspension restoration or real purge.
Registry, notice-send, namespace and suspension-change integration rows in the
qualification matrix remain deferred: unknown kinds are refused, not implemented
by a generic framework or used to manufacture applied markers. Original facts
and no-dispatch mechanics are qualified only within this structural substrate.
Production storage throughput, all-copy backup-marker evidence, live restore
drills, continuous retention, secret erasure and proxy/artifact traffic withdrawal
remain separately approval-blocked. No runtime enablement or live data was changed.

### Qualification matrix reconciliation

The approved proposal's §7 scenarios map to the suites above. Core append/commit
crashes, metadata/operator-repair limits, both-head conflicts, stable retry facts,
timely terminal races, exact markers, consistent older snapshots and startup
failure paths have focused executable tests. Unsupported event kinds are rejected
by the closed grammar. The matrix's registry/request/advisory-account-lock collision,
task-cancellation/content-cutoff effects, later suspension changes, notice/submission
dispatch and namespace recovery are **deferred/refused**, not claimed from a generic
test adapter. J1a adds no account advisory-lock producer or external dispatcher.
The ordered projection qualifies frame/marker mechanics, not those actual effects.
No prune/expiry entry point exists; backup all-copy marker evidence remains null
and pruning false irrespective of a day-36 backup or old terminal timestamp.
