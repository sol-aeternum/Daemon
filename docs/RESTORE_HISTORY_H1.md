# Independent restore history H1 — uncalled prototype

This implements the owner's approved isolated source/test slice only. It has no
runtime imports/callers, database connection, SQL projection schema, production
initializer, key loader, environment surface, client/API changes or activation.
J1 is unchanged. A store result or matching projection report is **never an
execution permit**, provider outcome, accounting authority or restore attestation.

## Scope and authority

`orchestrator/services/restore_history/model.py` defines closed typed H1 records,
canonical frames, keyed commitment helpers and structural replay. `store.py`
provides synchronous POSIX scans and appends against caller-supplied independent
identity and ownership facts. `projection.py` compares supplied facts without
reading or repairing a database. Initializers exist only in synthetic test code.
No actual production path, UID/GID, key or independent expected identity is
chosen by these modules. Caller-supplied facts are not independently verified
operator provisioning. No background threads, runtime operation coordinator,
shared lifetime lock ownership or deadline supervisor is installed.

The design supports cooperating local writers on a trusted single host. Shared
file writers can corrupt/forge negative history. Actor labels and SHA checksums
are not signatures; SQL A/W authority separation and compromised-writer/host-loss
protection are not established here. Independently retained history cannot be
rolled back alongside the database to manufacture apparent equality. Coordinated
log/HEAD/config rollback is undetectable without another independent authority.

## H1 format

Every record has exactly `v`, `history_id`, `seq`, `prev_digest`, `record_id`,
`kind`, `actor`, `data`. Version is integer 1. IDs are non-nil, lowercase,
hyphenated UUIDs. Integers exclude booleans and fit signed int64; generations
start at 0, epochs/sequences at 1. Hashes are lowercase fixed-size hex. Unknown
fields/types, duplicate JSON keys, noncanonical bytes and unsupported versions
are rejected. Payload JSON has sorted keys, no whitespace/BOM, strict finite
values and UTF-8. No prompt/content/provider object is a permitted record field.

| Kind | Actor | Closed data fields |
| --- | --- | --- |
| ACCEPT_PREPARE | backend | account_id, account_generation, task_id, conversation_id, user_message_id, result_message_id, commitment_version=1, key_id=1, key_commitment, request_commitment, request_schema=chat-canonical-3715-v1 |
| SEND_POSSIBLE | worker | account_id, account_generation, task_id, epoch, lifecycle_id, financial_scope_id, metadata_scope_id, producer_instance_id, reservation_id, runtime_identity, dispatch_nonce, effect_kind (completion/embedding/web_search) |
| STOP_INTENT | backend | account_id, account_generation, task_id, reason=owner |
| RESTORE_BEGIN | operator | restore_id, backup_sha256, target_binding_sha256, expected_schema_sha256, source_tree, h_cut (seq/digest), j1_cut (journal_id/seq/digest) |
| RESTORE_ASSESSED | operator | restore_id, begin_record_id, checked_through (seq/digest), report_sha256, disposition (remain_blocked/qualified_to_recheck) |

Sequences form one chain from zero digest. Stable operation IDs cannot conflict;
account/key commitment uniqueness survives generation changes. Sends/Stop require
an exact earlier acceptance; Stop forbids later sends. Open restore intervals
forbid new acceptance/sends; Stop can still be recorded. Restore cuts reference
the exact predecessor and assessment references the open restore. Structural
`qualified_to_recheck` closes an interval only; it does not validate report truth
or make any runtime ready. No task/financial/provider state is reconstructed.

Frame: four-byte big-endian payload length, payload, 32-byte SHA-256 digest of
`b"daemon.restore-history.frame.v1\n" + length + payload`. Payload limit is 8192
bytes (frame overhead is 36 bytes). Log limit is 16 MiB including frame overhead,
and 16384 records; both bounds apply. No pruning/compaction/rotation is provided.

Commitments use caller-supplied synthetic 32-byte K1, HMAC-SHA-256, raw account
UUID bytes, uint64-big-endian length prefixes, and separate key/request domains.
The exact ASCII client key is 1–128 characters `[A-Za-z0-9._:-]`. Generation is
not part of the lookup namespace. The request commitment includes the key and
caller-validated canonical request bytes, limited to 64 MiB in this prototype;
it never parses, stores or logs content. This is not a changed HTTP limit.
Pinned canonical provenance/finite-value validation remain future call-site work.
Identity's key-check binds K1 to H UUID using its own HMAC domain; backend must
verify it before future lookup. Worker can compare expected metadata without K1.
No key is generated, loaded, rotated, forgotten or copied from auth pepper.

## POSIX storage and ownership

Caller supplies an absolute directory, expected Identity and explicit Ownership
operator/publisher UIDs and GID. No path discovery, creation or fallback occurs.
Directory is operator-owned 2770 with the shared GID. IDENTITY is operator-owned
0440; LOG/OP_LOCK/APPEND_LOCK/RUNTIME_LOCK are operator-owned 0660. HEAD and
HEAD.pending are 0660 and owned by operator or an approved publisher UID.
Files must be regular, singly linked and in the expected group. Trusted ancestors
cannot be writable by other users; root-owned sticky `/tmp` permits owned test
children. Symlinks, untrusted ancestry, inode replacement and unexpected entries
fail closed. All owned-test UIDs can equal the current UID; such tests do not
qualify deployment separation between two actual service UIDs.

IDENTITY is canonical `{v,history_id,key_id,key_check}`; HEAD is canonical
`{v,history_id,seq,digest}`, each at most 256 bytes. Expected identity is supplied
independently by the caller, not learned from the log. Complete pending suffixes,
partial/corrupt frames and HEAD.pending block and are retained without promotion,
truncation or repair. `verify` reports validated structure, not an invocation's
fresh durability ACK or independent financial/authentication continuity.

Each call uses a process-local mutex, OP_LOCK then APPEND_LOCK, nonblocking flock
and the caller's **absolute monotonic deadline**. Fresh directory enumeration and
pinned inode checks prevent warmed scans ignoring new pending/unexpected files.
RUNTIME_LOCK's inode is checked, but this slice does not install runtime lifecycle
coordination. No lock is held across any provider call; there are no such callers.

Append validates the whole chain and candidate before mutation. It loops short
writes, fsyncs LOG, exclusively creates/writes/fsyncs HEAD.pending, replaces HEAD,
fsyncs the directory, checks HEAD/pins/deadline, and releases locks before returning.
Duplicate operation returns only historical `created=False`. Conflicting operation
or acceptance identities cannot append. No automatic retry is performed.

Deadline/exception after attempted mutation poisons that object against later
appends and reports uncertain outcome; acknowledged durability followed by result
or cleanup failure is `durable_result_unavailable`. No successful created result
is returned late. Kernel I/O may finish after the logical deadline: it is not a
remote cessation or finite kernel-cleanup guarantee. Synchronous ownership/locks
remain until the call finishes; `close` waits for that owner rather than closing
its descriptors from another thread. Cancellation preserves the original exception
and an uncertainty note. Lock cleanup failure cannot return success. Interrupted
bytes persist for explicit read-only inspection; reopening is not a send permit.

## Evidence and remaining work

Focused tests use synthetic keys, owned temporary POSIX files/processes and injected
faults. They cover closed formats, commitment vectors, structural contradictions,
projection payload comparison, short writes, fsync/rename/disk errors, torn and
complete suffixes, inode/mode checks, late completion, process death, contention
and resource cleanup. They do not execute all **24 proposed integration cases**.
The earlier design receipt incorrectly counted a Markdown separator as a 25th
case; its explicit local correction records 24 proposed, zero design-task tests.
Implementation-test results are reported separately from that design receipt.

Remaining: real protected non-cascading SQL projections and role qualification;
acceptance/send/Stop/publication integration; async ownership supervision; stable
client-key/error behavior; deletion/retention/J1 appliers; financial/auth/account/
cache continuity evidence; supported restore reconciliation; production key,
identity/storage provisioning and capacity/latency/retention qualification. No
original ACL/key/account recovery or exactly-once provider effect is certified.
H1 success does not resolve #477 or authorize migration/deployment/activation.
