# Historical task observation and reconnect

The owner approved this HTTP/client recovery contract on 2 November 2026.
It adds a read boundary, not acceptance acknowledgement, dispatch authority or
production H activation. The [composition](RESTORE_HISTORY_COMPOSITION.md) still
refuses historical acceptance and Send/Stop adoption. Keep #477 open.

## Responses and authority

`GET /tasks/by-key/{idempotency_key}` returns the existing TaskOut for the exact
surviving original task, including its persisted partial/completed/needs_attention
state. Task/SSE formats are unchanged. A successful observation does not prove
that a prior response was received or authorize another attempt/send.

All handled by-key responses use `Cache-Control: no-store`:

| Evidence | Response |
| --- | --- |
| Verified surviving original task, current owner/generation/lifecycle | Existing 200 TaskOut |
| Verified historical key but original task/projection cannot safely be reconciled | 409 `{"detail":{"code":"restore_recovery_required","task_id":<original UUID or null>,"retry_safe":false}}` |
| Unavailable identity/key/history/SQL evidence, failed cleanup or missing required reader | 503 `{"detail":{"code":"history_unavailable","task_id":null,"retry_safe":false}}` |
| Key absent from complete healthy H/projection evidence and owner-scoped native SQL | Existing 404 |

Foreign ownership remains indistinguishable from absence. Initial bearer
authentication is unchanged. Before disclosure, the H-owned read takes the
existing auth transaction lock and rechecks the actual session/device identities,
ownership, expiry and revocation on its own W connection. This serializes with
cooperating revocation writers; it cannot retract bytes already read/emitted or
offline downloads. Long-lived SSE reauthorization is unchanged.

An original UUID is disclosed only after the original account generation and
current active lifecycle pass the existing native account guard. Restricted,
reset or otherwise incompatible state returns recovery-required with null ID
and no old content. The surviving native stable key and encrypted canonical request must also match
the original H commitments; observing never refreshes a rotated native request
digest. No generation is recaptured to relabel old work. Read errors
never become unknown keys or empty history.

## Locking and source composition

`HistoryObservation` borrows the actual W TaskStore/pool, opened H coordinator,
strict combined 057 HistoryProjection and independently supplied backend K1.
Its constructor performs no I/O and installs nothing. The route accepts only
that exact type with the same application pool and MemoryStore. Any selected
native task/C1 composition must still validate and select that same store; the
reader cannot bypass a refused existing bundle or dangling store-only binding.
A credentials are neither accepted nor required by this reader. No SQL/role/grant/format changes
or projection repair/backfill are supplied.

Ordering is H operation ownership -> auth transaction lock -> original account
SHARE/lifecycle guard -> task/message SHARE. H verifies the complete chain and
the reader qualifies the actual current connection, independent identity/key,
bounded projection pages and all acceptance/Send/Stop facts. A changed or temporary
search path refuses rather than shadowing the native account guard; new ordinary
read queries explicitly target public relations. The existing content
guard is reused for reading: its row locks require existing UPDATE privileges,
which remain mutation authority and must not be described as read-only grants.
The read itself performs no application table mutation or history append.
The J1 import inventory names only this reader's existing CapturedGeneration,
WriteGuardRefusal and guard_content_write symbols. Negative cases continue to
reject fresh generation capture, request/phase APIs and transaction ownership
from that module; no deletion/reset implementation or runtime host is admitted.

After unchanged initial bearer authentication, the history lookup receives a
five-second absolute logical budget; this is not a total HTTP request timeout. SQL limits preserve
stricter settings. Cancellation/deadline/error drains the retained child, actual
native holder reset and filesystem work before H ownership is released. Cleanup
failure cannot deliver a saved success. Logical timeout does not bound stalled
kernel/driver cleanup. The host owns the borrowed coordinator lifecycle: a failed
coordinator stays blocked, and this adapter does not reset or replace it. Runtime
read-owner recovery remains an explicit integration/qualification requirement. No automatic retry or fresh-owner workaround runs here.
Controlled negative read results are returned through successful cleanup; they
are not thrown inside H merely to poison an otherwise healthy read owner.

`AppState.history_observation` defaults to None. Startup does not construct/open
or install it. The source inventory permits only the tasks route's exact static
no-alias HistoryObservation import; ADMITTED is unchanged and constructor,
binding, alternate-host and dynamic-import negatives remain enforced. Explicit
owned fixtures supply the reader. A pre-H deployment keeps native observation
only after positively observing H-schema absence. Existing H schema without a
reader, or unavailable catalog evidence, returns 503 without SQL-only fallback.
This compatibility check cannot independently detect an unconfigured runtime
and database restored together to a pre-H state; authenticated runtime startup
must independently retain/require its expected H identity before activation.

## Client key preservation

The by-key read uses one selected endpoint, with no fallback after absence or
transport uncertainty. Recovery-required/unavailable results remain unknown to
the task adopter, visibly report recovery status and retain the original key,
request controls and held draft. They never adopt the response's optional UUID
as task/dispatch authority. Keyed POST transport errors do not try another
backend. The bridge preserves approved 409/503 JSON rather than synthesizing
a request-rejected SSE event. No new SSE event type is introduced.

Negative recovery codes are metadata on the existing pending-submission record
and an account-cleared in-memory refusal. Such records are not expired by the
ordinary seven-day pending-key sweep or settled from a conversation snapshot;
expiry is not evidence of nonacceptance. Original held-file storage/expiry is
unchanged, and absent files cannot be reconstructed or resent under a new key.
Blocked keys cannot be sent; a successful original-task lookup can clear the
negative marker, while needs_attention retains it. Sign-out/account clearing
still removes pending records and in-memory refusals. Browser storage denial
retains only this tab's in-memory refusal and does not establish persistence
across browser loss. This is not server H retention/erasure policy.

## Qualification and launch limits

Owned synthetic SQL/POSIX and HTTP tests cover original-task/partial/completed
reopen, separate device credentials, foreign/revoked access, revocation during
H wait, original generation/lifecycle refusal, both lost acceptance-COMMIT
outcomes, missing Send/Stop projections, unavailable/corrupt evidence, W-only
read qualification and physical cleanup through cancellation/deadline/failure.
Client tests discriminate held-key/controls retention, visible scoped errors,
no mutation/fallback, needs_attention and account clearing. Exact executed
results and failed receipts are reported separately; listed cases alone are not
a claim that all release acceptance has passed.

Authenticated runtime H initialization/composition, historical retirement and
restore disposition, financial/auth/key continuity, complete lifecycle and
stream/artifact output fences, provisioning and deployed/browser acceptance
remain outstanding in the [launch checklist](DAILY_DRIVER_LAUNCH_CHECKLIST.md).
No live schema/credentials/grants, private storage/key provisioning, deployment,
durable flag activation, provider calls or exactly-once effects are established.

## Later observation-only startup

The approved [runtime lifecycle](RESTORE_HISTORY_RUNTIME.md) installs a dedicated
read-only supervisor in actual backend startup. It permits a later explicit read
to replace a failed owner only after demonstrated physical closure and complete
requalification, never by retrying the failed lookup or clearing a writer. The
original direct collaborator remains available to isolated fixtures. The runtime
host admits only its closed audited ingress and refuses producer/worker activity.
