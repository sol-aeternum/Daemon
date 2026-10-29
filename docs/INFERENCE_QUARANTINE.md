# Cloud-inference quarantine — source-only #477 slice

**Not enabled or deployment-qualified.** Migration
`050_task_inference_recovery.sql`, `orchestrator/tasks/inference.py` and explicit
fixture injection extend the actual acceptance, task, runner and compute paths.
The flag remains off. Source/tests do not qualify deployed roles, real provider
cessation, billing cessation, retention/erasure or restore readiness. #477 remains
open; parent direct review, final gates and deployment approval remain separate.

## Durable evidence and recovery

Only a genuinely NEW acceptance with explicit `cover_new_inference=True` inserts
one false coverage row, in the transaction that saves the encrypted original
origin. Duplicate acceptance never creates coverage or adopts legacy tasks.
Execution requires an explicitly injected `InferenceRecovery`, authenticated
original origin and existing full 049 identity. Missing coverage, collaborator,
schema or required observations refuses instead of choosing the legacy path.
Default constructors do not provide these collaborators. Explicitly selected
backend and separate worker startup now qualify them as described in
[TASK_STARTUP_COMPOSITION.md](TASK_STARTUP_COMPOSITION.md). Unconfigured startup
requires positive evidence of a pre-050 database; configuration removal cannot
downgrade a migrated host.

Explicit or inherited compute coverage also requires the existing shared invocation
before reconciliation or admission. Missing/crossed account, financial scope,
identity, collaborator or invocation refuses; resetting a ContextVar does not turn
a bound nested scope into legacy work or create a new invocation.

The content-free first-dispatch witness changes only from false to true. Its
owner/generation/version and first lifecycle/reservation/runtime/nonce tuple are
immutable. It is never cleared on completion, Stop, EOF, settlement, lease expiry
or lifecycle closure. Deletion follows the existing task FK cascade only; there
is no new TTL, purge, reset, release, downgrade or erase authority. Do not roll
back by discarding possible-dispatch evidence from existing rows.

Dispatch requires this invocation's newly acknowledged `reserve_for_attempt`
return, then atomic new 048 binding plus mark, same-connection pre-COMMIT gate,
acknowledged COMMIT and successful native release. SQL checks the exact original
account/generation, task/epoch, lifecycle and both scopes; the reservation must
be open and belong to the identity's **financial** scope. An existing binding
refuses even if identical. Readback observes evidence, never reconstructs a send
permit. There is no transaction spanning network I/O and no claimed atomic
DB-to-network revocation guarantee.

After a possible dispatch, an interrupted attempt cannot allocate a replacement
epoch, archive/reset its answer, defer or requeue (including preparation failure).
Sound current authority uses the existing no-content-copy control end with
`needs_attention` / `inference_dispatch_unresolved`. Suspension and persisted Stop
retain their higher-priority disposition; material-tool uncertainty stays distinct.
Authenticated partial output remains incomplete, and late content cannot publish.
Healthy distinct logical calls are allowed only within the same uninterrupted
attempt. Candidate/stream fallback and post-error relabelling are prohibited.

Covered cooperating writers acquire account/operation, lifecycle, task/attempt,
metadata and financial locks in the approved order. Original-origin and post-wait
lease checks precede staged close. TaskStore uses same-connection close, not a
standalone adapter inside its transaction. Conversation-first deletion remains
unqualified; contention is refusal, not retry or an excuse to change deletion.

## Invocation uncertainty and accounting

One shared object in `tasks/_invocation.py` propagates to nested/scheduled work.
Unknown ACK/COMMIT/decode/release or publication closes the invocation permanently
before another effect can begin. No provider call, mutation, settlement, reset,
helpful quarantine or lifecycle close may then start, including from finally.
Native driver-release/reset ownership is not reclaimed. Original cancellation,
identity, command and reservation evidence remain attached to the failure.

Acquired stream controls retain the exact invocation object. After uncertainty,
stop, expiry, explicit close and nested/finally cleanup initiate no new read,
transport cancellation, closer or generator-finalizer task. Work queued before
the fence checks again before SDK effects; in-progress cleanup checks after waits,
including before timeout cancellation of a cancellation-resistant closer. Late
chunks cannot publish. Healthy and known-interruption cleanup retain their existing
bounded ownership and accounting behavior.

The existing local-stop Future is only notification that local use has stopped,
not a transport-cleanup receipt. Previously started tasks/awaitables and unresolved
transports remain referenced by their existing local owners and observed without
new transport effects; task completion alone does not release an uncertain owner.
This is not a durable retention service, remote-cessation proof or a billing grant.
Primary exceptions, including the original cancellation object, survive secondary
cleanup errors. Synthetic cleanup regressions in
`tests/test_stream_cleanup_uncertainty.py` cover these timing and ownership seams;
they do not qualify real-provider cessation.

A known provider/local interruption forbids further inference without pretending
it is a DB uncertainty. With sound authority, known committed quarantine may hand
back to the **existing** ended-hold accounting path. It does not release remote
work or change financial amounts, original ownership/periods, or charge/refund
policy. Uncertain invocations require independent reconciliation rather than new
effects from the uncertain owner. The explicitly injected task sweep now retries
original open holds for a finished `needs_attention` /
`inference_dispatch_unresolved` task and matching ended original attempt. It
authenticates the sealed original origin/coverage and uses the existing recovery
lock to observe the matching possible-dispatch identity and closed lifecycle;
wrong/missing provenance refuses, never falls back to generic per-user recovery.
Observation locks are released before existing full-hold settlement takes its
account/reservation money locks. Original owner, period, scope, operation, labels
and amount survive; this is neither new execution admission nor a refund policy.

This accounting-only branch needs the existing explicit recovery and same DB pool
injection, not Redis or a provider key. Selected startup supplies that binding,
including when NEW durable submissions are disabled. Each
fresh independent pass refuses an inherited invocation, considers at most 50
original reservations with a separate process-local circular fairness cursor,
and shares one cooperative two-second deadline with transaction-local 250ms lock /
1000ms statement bounds. Default settlement callers are unchanged. Uncertainty or
cancellation ends the pass; native release ownership is not reclaimed. Original
OPEN rows remain durable pending work after rollback; a committed closure with a
lost ACK disappears from selection. A later independent pass reobserves them,
never clears an execution latch or resends. The cursor is not a success receipt,
and timeout is not remote-stop or bounded-cleanup proof. Receipt reconciliation
still selects settled receipts only. Production and broader deletion/restore
qualification remain outside this source-only repair.

Covered metered tools use the same attempt-qualified admission and shared send
fence before reservation, yield and dispatch-aware marking. The charge retains
the exact invocation even after ContextVar reset. Known interruption blocks new
sends but does not erase an acknowledged existing hold or its sound-authority
accounting. The actual search adapter marks immediately before its HTTP send;
arbitrary legacy caller I/O after a context-manager yield is not contained by Python.

Covered embedding charges also retain the original shared invocation, immutable
attempt identity and recovery collaborator. Dispatch revalidates that binding,
the current-context conflict, send callback and scope closure immediately before
the adapter's POST mark. Receipt confirmation and finally accounting check the
same original binding and uncertainty, but known interruption alone does not
erase valid usage or forbid sound-authority settlement. Secondary covered cleanup
refusals preserve the original error/cancellation and retain unresolved holds.
`tests/test_embedding_invocation_fence.py` exercises these interfaces offline;
this is not real-provider or terminal-reconciliation qualification.

## Internal surfaces and privilege prerequisite

The additive migration exposes only the approved dispatch begin/readback, ordered
recovery lock, abandoned page and abandoned close operations. Objects are schema
qualified, SECURITY DEFINER functions fix `search_path=pg_catalog`, unsafe default
ACLs refuse, and PUBLIC/helper access is revoked. No production roles or grants
are created. The trusted acceptor needs narrow coverage-column INSERT; the worker
must lack it and all direct marker/048/049 table/column privileges or owner/helper
membership. Fixture pools use separated non-owner principals. UUIDs and Python
preflight do not authenticate end users or sandbox an adversarial tasks-DML holder.

### Explicit composition and selected service startup (source only)

`orchestrator/tasks/composition.py:qualify_task_composition` accepts externally
owned pools, encryption and settings, never DSNs or credential factories. Its
default returns no bundle with no I/O; contradictory, partial, same-pool,
cross-database and unsafe-principal configurations refuse. The trusted synthetic
host must create both pools from its one owned endpoint/database. Observed catalog
identity is an additional negative check, not cross-cluster or restore attestation.
The factory does not create/close pools, provision roles, alter grants, migrate,
start jobs or introduce an environment switch. The approved startup adapter owns
pools and selects this factory using explicit service configuration; see
[TASK_STARTUP_COMPOSITION.md](TASK_STARTUP_COMPOSITION.md).

Both actual caller connections must pass capability/schema/ACL checks **before**
either J1 barrier runs. Only A may initialize a configured journal binding; W
requires an existing matching binding and cannot initialize it.
The factory checks protected object shapes, constraint presence/validation, definer
signatures/search paths, current/default ACL exposure and the immutable witness
trigger; reviewed migrations remain the supported creators, not arbitrary manual
DBA changes. Missing/unsafe evidence refuses; there is no repair or elevated fallback.
No bundle is published on failed/cancelled qualification. The host retains its own
pool ownership and partial-startup unwind; no new remote-cleanup guarantee is made.

The HTTP bundle separates A's NEW acceptor from W's owner-qualified task control
and observation store. Whole binding checks precede NEW acceptance. A rechecks its
exact four-column coverage INSERT and denied protected capabilities on the actual
acceptance transaction connection, before content mutations. Replay still precedes
NEW origin admission and creates no coverage. Current principal checks reject
SESSION_USER/CURRENT_USER substitution, privileged/owner/helper capabilities and
cross-principal membership, including NOINHERIT. Startup success is not a permit.

The worker receives a distinct W-only binding: no A pool/store is carried into
its context. The separate-service worker factory observes current A capabilities
through W catalog queries, then qualifies actual W and the existing J1 binding.
This does not authenticate A or replace the backend actual-pair checks.
TaskStore, MemoryStore/encryption, recovery and compute/accounting
pool identities are pinned; missing/crossed context refuses before runner/sweep
effects. W's existing per-operation preflights/live SQL gates remain; composed
wake-up writers and empty-candidate sweep discovery also check the actual caller
connection. P1 embedding and P2 original-hold settlement algorithms are unchanged.
Abandoned closure remains an explicit independent call, not new default cron work.

`tests/test_task_composition_postgres.py` exercises the actual authenticated HTTP
acceptance/Stop, worker, financial SQL, independent sweep, ordinary cloud answers
and `get_time` tools with scripted SDK transport and owned PostgreSQL/Redis. It
includes current-connection ACL/principal failures, partial bindings, same/crossed
pools, J1 initial-binding ordering and post-dispatch quarantine/original funded-hold
recovery. Scripted remote continuation is not real-provider cessation evidence.
Opt-in PostgreSQL skips are not qualification; final-state gates and independent
changed-logic review remain required. Default non-composition inventory admits only
the exact factory construction/import sites and two approved startup calls,
never arbitrary callers or additional activation sites.

`DURABLE_CHAT_ENABLED=false` stops NEW durable acceptance. Selected composition,
existing-task observation, Stop and recovery remain operational after restart.
Keep its roles, credentials and journal configuration. This does not disable
ordinary chat/tools or previously accepted jobs, prove no earlier activation,
detect a rolled-back DB or offer an operational rollback route. Coverage/first-
dispatch evidence is not cleared. Production principals, deployed pool binding,
restore/retention and general deletion/output fencing remain unqualified. The
owner authorized durable enablement conditionally on readiness; source tests alone
do not establish readiness.

The uncomposed abandoned consumer takes at most 32 controls per pass and shares one
absolute cooperative two-second budget across discovery, acquisition, checks,
close, COMMIT and release. Live records are skipped, already closed controls gain
no command history, uncertain outcomes stop the pass, and the cursor wraps only
after exhausting the keyset sequence. It never settles, purges, clears evidence,
provisions an account or certifies restore/retention readiness.

## User contract and qualification boundary

The existing error/done channels carry a non-retryable interruption notice:

> This response was interrupted. Daemon will not restart it automatically because
> the provider may still be working or charging. Any saved partial answer is incomplete.

There is no Resume/release endpoint. Idempotent replay observes the same task.
The ordinary composer can create a deliberately NEW request with an overlap
warning; it is not a retry/release of the old task or an account-wide drain gate.
Covered Stop says local work stopped, not that provider activity or charges ended.

`tests/test_inference_recovery.py` checks shared fences/non-composition;
`tests/test_inference_repair_seams.py` checks covered admission/send negatives;
`tests/test_inference_noncomposition.py` checks production-wide relevant inventory,
aliases, known-literal dynamic wiring, disabled defaults and adapter import I/O.
The latter is not an arbitrary dynamic-code sandbox or deployed startup proof.
`tests/test_inference_recovery_postgres.py` uses the existing opt-in network-none
owned PG fixture, separated roles, real transactions/driver faults and scripted
upstreams that continue independently after local EOF/close/error. These tests
and shared lifecycle/admission seeds use real guarded task acceptance for original
origin; synthetic setup is not permission to bypass claim validation. Mocked
frontend setup tests isolate their fixture environment without changing the real
managed Python environment used by backend transcript generation. These fixtures
are not real-provider stop/billing evidence. Complete #542/#545, observer/Next/
browser, financial, fault and full final-tree gates remain required. #539's
`55P03` versus `40P01` qualification issue is not waived by a targeted pass; its
required failure stops qualification. #490's browser debt remains disclosed.

The exact account-control import inventory and writer catalogue include the
source-only quarantine seams and migration 050 recovery marker. These additions
grant no runtime authority: every new writer candidate remains uncovered, and
unresolved SQL/ContextVar candidates remain explicitly deferred. Existing
title/summary/entity guard coverage is not extended by this classification.
