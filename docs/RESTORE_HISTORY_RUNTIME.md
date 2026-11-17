# H1 observation-only startup

Owner-approved source behavior. This is a real backend lifecycle installation,
not execution activation. The rollout tracker #477 stays open. No live resource,
key, UID/GID, grant, migration, configuration or deployment is supplied here.

## Selection and independent material

Any nonempty `DAEMON_RESTORE_HISTORY_*` field selects H and requires the complete
profile. All fields absent or empty preserve the pre-H host, provided catalog
checks positively establish no H tables/functions, or (#731) the exact 056/057
H tables with zero-byte main forks, so no binding can exist. See
[`RESTORE_HISTORY_UNBOUND_START_PROPOSAL.md`](RESTORE_HISTORY_UNBOUND_START_PROPOSAL.md).
Removing settings cannot silently downgrade a partially migrated, reshaped or
possibly bound H schema. Selected H requires the
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

Backend Redis producer access, memory backfill,
attestation and session-cleanup jobs are not started in this configuration.
Audited authentication bootstrap remains available after H qualification. The
worker receives W and independent expected metadata, never A credentials/K1;
its actual startup qualifies H/native evidence then deliberately fails and
closes resources before ARQ's job loop or attestation. No job registry entry,
including an unclassified future job, can run through that failed startup.
This temporary availability barrier must not be described as usable execution.

An explicitly constructed [isolated acceptance host](RESTORE_HISTORY_COMPOSITION.md#explicit-isolated-authenticated-acceptance-host)
supplies a bounded authenticated source qualification seam. It is not selected
here, adds no default route/environment switch, and cannot execute accepted work.
It neither removes this barrier nor qualifies an answer/CSV pilot.
Its explicit source profile can be H1 or terminal-v3 with exact matched A/W
projection bindings; this does not change this production host's H1 selection.
`HistoryReadSupervisor` rejects terminal projections before creating an owner or
performing I/O. Isolated second-device persisted-text reads and in-process
host/pool reconstruction are prerequisites, not process-restart or live restore
qualification, and install no execution/partial-restore/control-terminal graph.

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

### Application startup and bootstrap boundary

`tests/test_phone_startup_bootstrap_postgres.py` exercises the actual
`orchestrator.main.app` and full lifespan against the owned SQL fixture. A
fresh-interpreter case invokes the supported `orchestrator.runtime.main` backend
launcher with real Uvicorn and lifespan, using Uvicorn's inherited-socket `fd`
branch with a parent-owned loopback listener. The ordinary host/port bind branch
and production networking are not qualified by that test. Synthetic settings explicitly
disable dotenv loading; synthetic H material maps the single test UID to the
fixture files. This does not qualify production UID/GID, key or mount separation.

The database starts with the proposed ordinary W map, then existing H fixtures
apply their separate `task_history_*` function grants. A retains the broader
synthetic acceptor fixture. The startup checks therefore do **not** prove that
the ordinary W map alone is an H privilege profile or supply a deployable A map.
The exact ordinary-role ACL inventory remains separately tested in
`tests/test_exact_worker_privileges_postgres.py`; no privilege map changes here.

The bounded backend request matrix follows application callers in
`frontend/components/AuthProvider.tsx`, `frontend/components/ModelSelector.tsx`,
`frontend/hooks/useEntitlements.ts`, `frontend/hooks/useHomeSuggestions.ts` and
the task input/presentation proxies under `frontend/app/api/tasks/`:

| Request group | Selected H1 expectation |
| --- | --- |
| Health and public auth config | Success, no-store |
| Anonymous refresh without a credential | Native missing-credential refusal, not an H availability refusal |
| Authenticated conversation list and original task/by-key reads | Success, no-store |
| Public catalog (`GET /v1/catalog`), authenticated entitlement/settings reads | Success under their native checks, no-store |
| Provider models (`GET /v1/models`), home suggestions, settings writes, task inputs/presentations | `503 history_unavailable`, no-store |
| New chat, native Stop and unclassified resource routes | The same H refusal before execution |

The lifecycle case prohibits attestation, shipped-skill publication, memory
backfill and session-cleanup producers; the child prohibits outbound network
connects and checks physical pool/history cleanup after graceful termination.
H observation-only does **not** mean authentication storage is read-only:
`main._check_first_boot_setup` still runs under W. The fresh-install case writes
a setup-token verifier and private operator token file. With devices but no valid
refresh session, startup revokes the existing devices/sessions and republishes
setup material. Separate full-lifespan cases exercise both existing branches
under W and assert the original task stays queued; token values are not exposed.
This documents current startup behavior, not a new credential or grant decision.
The existing worker startup tests still require `history_observation_only` before
the job loop, including pool cleanup. No acceptance/Send/Stop/output collaborator
is installed by these tests or by production H startup.

This is **backend bootstrap-boundary qualification**, not successful complete
application bootstrap: it seeds native device sessions, does not drive Next or
a browser, and deliberately retains unavailable controls. Fresh setup/enrollment,
cookie/CSRF/proxy behavior, model selection, SSE, a restarted browser and a
physical phone remain separate acceptance. No live provider is called. A usable
pilot needs an approved route-by-route bootstrap and executable composition,
not blanket GET admission or removal of the worker barrier. Keep #477 and the
applicable #704 privilege/runtime qualification open; no deployed grants, flag
enablement, lost-output recovery, restore or phone acceptance follows here.

### B1 self-hosted browser bootstrap boundary

The owner-approved B1 source slice adds only `GET /v1/catalog`,
`GET /users/me/entitlements` and `GET /users/me/settings` to the exact audited
ingress. The catalog remains public/local metadata; authenticated reads retain
native device and owner checks. Authentication may update `devices.last_seen_at`;
these are not database-read-only sessions. The proposed ordinary W map already
covers these handlers; no SQL grants or live privileges change. Separate synthetic
H function grants and the broader A fixture are not deployable role evidence.

Browser qualification uses a single test-owned HTTPS origin with a nonempty
backend prefix, actual Next proxies and full backend lifespan. Its positive
identity/entitlement fixtures have empty own history and no unresolved accepted
work. This is a test precondition, not a new runtime guard or general history
recovery proof. Native conversation/task admissions are unchanged; they are not
promoted to independent H-qualified recovery. Fresh setup still refuses a missing
singleton or entitlement rather than provisioning or repairing it at runtime.

The auth and entitlement proxies pin one selected backend, reject redirects and
never try a second authority after transport uncertainty. See
[the auth boundary](AUTH_ARCHITECTURE.md#b1-pinned-browser-bootstrap-proxies).
Private-cache matching and targeted upgrade cleanup cover legacy root account
reads as well as prefixed requests. A fixed worker must replace the prior
controller and finish activation cleanup before upgraded-profile privacy
acceptance; the HTTP-cache acknowledgement does not prove Cache Storage cleanup
or repair already-open old tabs. No real browser profile is migrated here.

Send, native Stop, selected-H worker execution, settings/model persistence,
provider model discovery and resource composition remain refused. Home suggestions
remain visibly unavailable on refusal, without automatic polling or fabricated
disabled/success states. B1 is not deployment, physical-phone, live-provider or
general restore acceptance; #477 and #704 remain open.

The owned PostgreSQL browser fixture is
`tests/test_b1_browser_bootstrap_postgres.py` (opt in with
`--compute-metadata-postgres`, using the repository's installed browser and cached
database image). It builds the actual production Next/Serwist worker for its
reserved HTTPS `/daemon` origin. Real setup/enrollment issue both devices; the
fixture never installs preissued browser sessions. Effective ACL checks execute
under the selected H application pool, not just a standalone proposed-role test.
Its prior controller is a synthetic reproduction of the old rule boundary, not
a historical production worker artifact. Credential-lifetime isolation is for
the same singleton identity and is not distinct-account sign-in qualification.
Test-owned offline cache markers are inserted deliberately after activation to
prove they cannot satisfy protected reads; their presence is not mistaken for
failed activation cleanup. Unrelated entries must remain intact.

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
