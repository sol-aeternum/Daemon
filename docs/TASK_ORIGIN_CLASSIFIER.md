# S0 — uncalled task-origin classifier

Owner-authorized first prerequisite slice for #477, 14 October 2026, 09:41:50 UTC
(`Sentinel_bd33d67ad7b48191aa90b98ef806c45c`: “Approved as recommended”). This
implements **only S0**, not the broader prerequisite sequence, runtime refusal,
deployment or enablement. #477 remains open. DEC04–DEC05/AC03 continuity and the
accepted [durable-request](DURABLE_REQUEST_DESIGN.md) and
[account-control](ACCOUNT_CONTROL_PERSISTENCE.md) contracts remain in force.

## Source boundary

[`orchestrator/tasks/_origin.py`](../orchestrator/tasks/_origin.py) is private,
pure, standard-library-only and **UNCALLED by runtime code**. Its immutable
contracts and [`tests/test_task_origin.py`](../tests/test_task_origin.py) use
synthetic plaintext/identifiers only. There is no store/guard import, key handling,
decryption, database/Redis/provider/HTTP/filesystem access, SQL/DDL, migration,
runtime wiring, public API/SSE, feature, environment or dependency change.

The caller must first authenticate/decrypt the saved task input and independently
establish the persisted task owner. `TaskOriginContext` explicitly supplies that
owner for comparison; constructing it does not prove ownership or post-wait
currency. `_classify_task_origin` consumes the raw authenticated JSON string, not
`TaskStore._open`, which collapses decoded nonobjects into an empty dictionary.
Supported input version follows the existing `tasks.inputs.CHAT_INPUT_VERSION`;
the synthetic tests check parity without importing runtime stores into the utility.

## Closed classification and precedence

| Observed authenticated input | Result |
| --- | --- |
| Malformed JSON (including duplicate members or non-JSON numeric constants), or a decoded nonobject | `task_origin_invalid` |
| Object missing either general-origin field | `task_origin_missing` |
| Both fields present, but unsupported/ill-typed input version or invalid origin UUID/type/range | `task_origin_invalid` |
| Well-formed original origin UUID differs from the supplied task-owner comparison | `task_origin_foreign` |
| Supported version and well-formed matching UUID/generation | `OriginalTaskOrigin` data |

This table is evaluated in order. Missing fields take precedence over unsupported
version or an invalid remaining field; invalid version/generation takes precedence
over foreign ownership. Malformed/nonobject input cannot establish field absence.
Null is a present invalid value, not absence. Only the two general fields
`origin_account_id` and `origin_account_generation` are used. Summary-only legacy
fields never supply a fallback; summary fields and attempt epochs cannot override
general-origin data. No supplied replacement origin is accepted.

The input version and generation must be exact integers, never booleans, floats
or numeric strings. Generation is inclusive `0..2^63-1`; zero is valid and never
treated as absent. The origin identifier must be a UUID-parsable string, returned
as a UUID without changing its identity. Matching data is **not provenance,
authentication, permission, content-guard scope, producer-stop, settlement or
deletion-readiness evidence**, nor proof of current account/target state.

UUID parsing uses the standard-library `UUID` string parser, not a canonical-text
equality rule; equivalent spellings are not new account identities. Integers wider
than any permitted generation are represented internally by an opaque sentinel,
so even literals beyond Python's integer digit limit remain invalid generations
instead of escaping classification. Unrelated oversized integer fields do not
alter missing-origin precedence or matching-origin data.

`TaskOriginIndeterminate` is a passive upstream contract, never returned by the
decoder: unauthenticated/unreadable ciphertext, unavailable keys, DB/schema
failures, cancellation and uncertain commits are not semantically established bad
origins. Do not pass invented plaintext/`None` for those failures. Wrong Python
argument types raise `TypeError`; operational parser/resource exceptions propagate
rather than becoming semantic refusals. S0 cannot adjudicate failures it never
observes. No exception text or source input is retained in classification results.

## Disposition and remaining prerequisites

The latest human behaviour approval supersedes older proposed automatic legacy
terminalization: **old tasks without trustworthy general-origin data are blocked
pending separate disposition, with NO automatic repair**. No recapture, backfill,
provisioning or attempt-generation substitution. A task refused after losing
permission stays stopped; restoration must not silently restart or refresh it.
S0 has no runtime consumer and chooses no `TaskStatus`, retry, cancellation,
claim/reset, publication, cleanup, settlement or entitlement initialization.

Later lifecycle/accounting (S1), evidence/deletion/readback (S2), and runtime
consumers (S3) are **unimplemented dependencies**, not adopted architecture in this
slice. Their architecture choices still need bounded design and approval; safe
recovery must reconcile the existing lost-attempt full-hold recovery and bounded
deletion/drain contracts. Durable exclusive cleanup identity, admission closure,
qualified stop/transfer and bounded report/disposition remain missing. A terminal
task is non-reclaimable, not necessarily producer-stopped, accounting-settled or
deletion-ready. Neither indefinite deployed pinning nor unsafe early settlement
or deletion is an approved substitute.

Both TaskStore and direct MemoryStore conversation-deletion paths can reach task
evidence cascades. Their protections and UI uncertainty/readback notices remain
future prerequisites; this classifier supplies no whole-user purge or non-task
reservation guarantee. Existing `_ensure_account` only reads then raises
`UnknownAccount`; historical/singleton missing-entitlement remediation remains
unresolved and fail closed. Stream-read deadlines exclude consumer suspension;
spend impact is unexecuted/unproven and requires separate qualification. S0 fixes
none of those runtime issues and supplies no account fence, marker or readiness.

## Verification boundary

Synthetic tests cover missing/summary-only/partial/null origins, malformed JSON
and nonobjects, strict versions/UUIDs/generations, foreign ownership, boundary
values, contradictory precedence, deterministic immutable data and unobserved
operational failures. Source checks cover the standard-library-only import
boundary and absence of runtime imports/callers; they are bounded source tripwires,
not authentication or a security sandbox. Final-state project gates and fresh
different-family read-only review are reported in the slice PR, including actual
skips/inventory. No new service qualification is needed to prove this pure
behaviour; skipped database cases never qualify unrelated runtime guards.

No deployment, flag change or later runtime connection before integrated acceptance
gates qualify and the owner separately authorizes it. PR/merge does not close #477;
merge is held for exact-head CI, current Code Review and parent direct re-review.
