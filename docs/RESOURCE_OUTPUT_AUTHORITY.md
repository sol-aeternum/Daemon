# Backend-owned output authority: presentation SQL slice

Source-only part of the owner-approved artifact-publication design following
#653. Migration `058_resource_output_presentations.sql` supplies the bounded
presentation capability for a future backend-held P login. It selects no actual
login, grants no application access, adds no table or stored payload, and installs
no publisher, socket, pool, key, filesystem initializer or runtime handler.
The H-configured runtime remains observation-only. Keep #477 open.

## Why separate it

`ResourcePresentations.record` currently obtains the ResourceInputs acceptance
pool before invoking the lifecycle assertion. That is valid in C1's explicitly
trusted synthetic host, but A in the split-principal host deliberately cannot
execute lifecycle functions. Giving W a trusted C1 pool or the backend UID would
also violate the approved privilege/private-storage boundaries.

This slice supplies a separate SQL interface rather than weakening A/W checks.
The later output collaborator must use it without moving accepted-input/task
creation out of their same A transaction. Backend-owned files retain same-EUID
0700/0600 checks; W receives no artifact mount, A/P credential or K1. No actual
service identity or production permission is changed by this migration.

## Exact capability

P may eventually receive direct EXECUTE on only these two functions:

* `resource_output_presentation_snapshot(jsonb)` serializes on the original
  account/lifecycle/task and live source rows, qualifies current account/generation,
  and returns the original encrypted task input plus at most 100 existing encrypted
  presentation rows and an observation timestamp. It deliberately permits an
  expired producer to inspect committed history. It is not user authentication,
  H qualification, source decryption or new effect authority.
* `resource_output_presentation_insert(jsonb,uuid,jsonb,text,timestamptz,integer)`
  stages one exact pinned envelope. New insertion requires the registered current
  lifecycle/task/attempt/lease, uncancelled task, current account/generation and
  same-account/task/message/conversation output references. Pins are closed typed
  resource/version pairs without duplicates. Existing per-message bounds remain
  1–100, supplied by the trusted owner; no production quota is selected here.

The caller owns its READ COMMITTED transaction. Every insert/replay result says
`pending_commit`; snapshots are observations. Neither establishes a COMMIT acknowledgement.
The SQL functions cannot establish Python transaction ownership. The future
collaborator must reject autocommit and borrowed/nested ownership, just as the
existing caller-owned reservation contract requires. Cancelling a bare autocommit
query is not proof that its effects rolled back; the SQL regression explicitly
owns and rolls back its transaction before asserting absence.
Different identity/ciphertext/timestamp reuse conflicts. New records recheck lease
after insertion/FK waits immediately before returning to a prompt caller COMMIT.
No SQL helper, returned row, timestamp or historical replay is a dispatch permit.

Every entrypoint rejects owners/superusers, inherited EXECUTE, grant option,
extra grantees, privileged memberships, direct table/column/sequence access and native
lifecycle/dispatch capabilities on P. Both public entrypoints must name the same
single nonowner login directly. Helpers remain owner-only. Fixed search paths,
qualified relations/types and PUBLIC revocation prevent temporary-object and
default-ACL redirection. P connections with a temporary namespace are explicitly
refused before calling older lifecycle helpers whose implicit type names could
otherwise resolve to temporary objects. SET ROLE is also refused; a pooled P
connection must remain a clean direct-login connection. No legacy helper is
modified or granted to P. Migration default grants outside intrinsic PUBLIC EXECUTE
are rejected atomically. Runtime source/DDL owners remain trusted.

SQL cannot authenticate encrypted envelopes or compare encrypted pin lists.
The trusted backend must decrypt/validate every bounded snapshot, verify sealed
native origin and exact pinned references, recognize matching existing envelopes
before allocating a new identity, and construct the closed C1 envelope. P is a
trusted internal capability, not an untrusted tenant sandbox. No Python output
collaborator is installed by this SQL-only slice; the existing C1 path is unchanged.

## Required later composition

Receive and authenticate a bounded local worker request before H. Under H lifetime
and operation ownership, independently qualify identity/key/full H-SQL parity and
original acceptance/no Stop, then perform native R0 reservation, catalog/file
publication and presentation transactions in that order. Keep original captured
generation and operation/slot identity. Never hold H across socket/provider calls.
The scope must retain actual native connection reset and filesystem completion
through cancellation/deadline/shutdown; a logical timeout does not end kernel work.
No success after uncertain COMMIT, cleanup or H release; no automatic new request,
retry, producer refresh, readback promotion or writer-owner replacement.

The separate COMMIT boundaries remain intentional: a reservation can exist without
a publication; a catalog version can exist without a presentation. Preserve that
incomplete state and original identity instead of silently finishing it after Stop
or expiry. Reads may observe exact committed pins; historical evidence cannot
authorize a new write. No file cleanup, repair, TTL, namespace retirement or account
purge is added. Database account cascade still does not erase files.

Full P catalog/file authority, split output collaborator, authenticated local
transport, framing/admission budgets, actual backend/worker ownership tests and
runtime composition remain subsequent source work. Real grants/credentials,
socket/filesystem provisioning, live migrations and deployment require their own
operational approvals. H lacks individual artifact publication history; artifact
restore evidence/reconciliation and lifecycle qualification remain release gates.
This slice must not be presented as artifact activation or live restore safety.


The next source-only slice supplies the separate bounded catalog snapshot, admission
and stage entrypoints in `059_resource_output_catalog.sql`; see
[catalog authority](RESOURCE_OUTPUT_CATALOG.md). It reuses these P exclusions
without changing 058 or installing a runtime publisher. No actual P grants are made.
