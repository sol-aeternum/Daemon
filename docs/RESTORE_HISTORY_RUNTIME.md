# H1 observation-only startup

Owner-approved source behavior. This is a real backend lifecycle installation,
not execution activation. The rollout tracker #477 stays open. No live resource,
key, UID/GID, grant, migration, configuration or deployment is supplied here.

## Selection and independent material

Any nonempty `DAEMON_RESTORE_HISTORY_*` field selects H and requires the complete
profile. All fields absent or empty preserve the pre-H host, provided catalog
checks positively establish no H tables/functions. Removing settings cannot
silently downgrade a partially migrated H schema. Selected H requires the
existing split A/W and J1 configuration even when new durable work is disabled.

Common backend/worker fields are `DIR`, `EXPECTED_FILE`, `OPERATOR_UID`,
`BACKEND_UID`, `WORKER_UID`, `GROUP_GID`, `KEY_GID`, each with the
`DAEMON_RESTORE_HISTORY_` prefix. The backend alone receives `KEY_FILE`.
Paths are canonical absolute paths. Operator, backend and worker UIDs must all
be distinct, validated before file I/O, including direct profile construction.
Backend and worker UIDs are nonroot canonical decimal identifiers; the operator
may be root or a separate nonroot UID. Key-group exclusion does not remove file
owner access, so an operator/service collision is refused. History and
key groups are distinct. The actual effective/real UID and group membership
must match the selected service. W must not belong to the key group or receive
the key path, including through a shared dotenv file. Compose omission alone is
not isolation: operator-approved separate configuration and mounts remain needed.

The independently supplied expected-identity file is canonical JSON containing
exactly `v=1`, `history_id`, `key_id=1`, `key_check`; at most 256 bytes. Its parent
is operator-owned 0750 with history GID, file operator-owned 0440 with that GID.
The backend key is exactly 32 raw bytes, independently key-check verified;
its parent is operator-owned 0750 with key GID, file operator-owned 0640 with
that GID. Both files are outside the writable history directory and distinct.
No generation, text/base64 fallback, pepper reuse or production initializer exists.
Ancestors, regular-file type, single link, permissions and pinned name/inode/
metadata are checked without following symlinks. Replacements, mutation, short
or oversized material, unsafe ownership and uncertain descriptor close refuse.
These checks are cooperating single-host H1 controls, not protection against root.

## Ordering and availability

Backend startup retains physical material loading and pool initialization through
cancellation. H lifetime opens before database recovery. Within exclusive H OP,
W must pass current-connection principal/catalog/binding and complete bounded
H/SQL parity checks, before the existing paired A/W native recovery factory can
perform J1 recovery/binding writes. Existing native checks stay in place. Pool
release is retained through actual asyncpg reset, including native factory and
J1 leases. No APPEND lock is held across recovery. A separate qualified reader
is installed only after the native bundle is ready. No mutating H collaborator
is installed. The startup logical budget is one absolute 10-second deadline;
physical cleanup can exceed it and never turns a late result into success.

When selected, HTTP is observation-only regardless of `DURABLE_CHAT_ENABLED`.
A closed exact endpoint/path/method inventory allows authentication, health,
conversation reads and task/by-key/SSE observation. Native ownership, session,
revocation and generation checks still govern those reads. Every new/unclassified
route, producer, mutation, upload, proxy-compatible inference route and native
Stop refuses before route dependencies/handlers. The response is no-store HTTP
503 with `detail={code:history_unavailable,task_id:null,retry_safe:false}`.
Selected responses are no-store. Only CORS preflight for an audited target is
passed to the existing CORS policy. Unknown websocket ingress is denied before
acceptance (HTTP 503 where the ASGI denial extension is supported, otherwise
policy close 1008). Security/request-ID/CORS outer headers remain in place.
FastAPI's effective route contexts account for included routers and prefixes;
there is no GET or module-prefix admission rule.

Backend Redis producer access, memory backfill, skill synchronization,
attestation and session-cleanup jobs are not started in this configuration.
Audited authentication bootstrap remains available after H qualification. The
worker receives W and independent expected metadata, never A credentials/K1;
its actual startup qualifies H/native evidence then deliberately fails and
closes resources before ARQ's job loop or attestation. No job registry entry,
including an unclassified future job, can run through that failed startup.
This temporary availability barrier must not be described as usable execution.

## Read-only owner renewal and shutdown

`HistoryReadSupervisor` owns a separate H coordinator and snapshots auth context
before queued admission. Reads serialize locally and acquire H OP before SQL.
A failed read is never retried automatically. It drains its child, then attempts
physical owner closure exactly once. Only demonstrated settled successful
shutdown, closed store and executor permits a *later explicit request* to create
a fresh read owner. That owner must repeat independent identity and full H/SQL
qualification. A late result is discarded. Failed/uncertain close latches the
supervisor until controlled restart; no descriptor retry or inferred success.
Writer objects and their monotonic blocked state are never changed or replaced.
A new read owner supplies no acceptance, repair, replay, retry or send authority.

Shutdown rejects new reads, drains the active read and actual SQL reset, then
closes reader and pools before releasing the main H lifetime owner. Repeated cancellation preserves the
original exception and cannot detach physical work. Cleanup failure remains a
failure. Logical deadlines do not bound kernel cleanup. Forked/foreign-loop
owners refuse before touching inherited ownership resources.

## Qualification and remaining rollout

Tests use owned temporary POSIX files and disposable network-isolated PostgreSQL.
File/profile tests and actual SQL startup tests establish different properties:
synthetic SQL tests supply synthetic material instead of provisioning two real
OS identities/groups. They do not qualify production mounts, DAC, original ACLs,
keys, account continuity, independent backup retention or the deployed topology.

Acceptance, Send, Stop and text/partial/terminal/artifact publication must next be
connected to the authenticated runtime and qualified together before removing
the barrier. Existing source adapters and receipts are not dispatch permits.
Uncertain remote sends still require attention, never automatic replacement.
Stop cannot promise remote cessation or billing cancellation. Live migration,
restricted credentials/grants, independent storage/key provisioning, restore
reconciliation, deployment and enablement remain explicit operational gates.

Selected native pools initialize one connection under retained ownership, with
existing maximum sizes unchanged. This avoids asyncpg's multi-connection initial
`gather`, whose first error can otherwise leave sibling connects running after
the initializer returns. Unselected pool sizing is unchanged. No replacement
pool, private holder mutation or background warm-up is installed.

Shutdown fences HTTP and direct read admission synchronously before allocating
cleanup tasks. If scheduling fails, a known unstarted cleanup coroutine is closed
without executing its body; the scheduling exception remains primary even when
that closure is interrupted. This does not claim physical cleanup completed or
introduce a retry. A failed shutdown never reopens admission.
