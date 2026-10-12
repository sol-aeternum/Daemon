# P0.5 — identity retention and ownership provenance

Owner-approved source contract, 12 October 2026, following R1/R2 in
[#469](https://github.com/sol-aeternum/Daemon/issues/469#issuecomment-6072269138).
This prerequisite does **not** implement account deletion/reset. #469's closed
state does not change that; #477 remains the enablement gate. No live migration,
sweep, deployment, credential change or flag enablement is authorized here.

## Migration contract and privacy

[`045_identity_retention_provenance.sql`](../migrations/045_identity_retention_provenance.sql)
adds internal `account_email_ownership` intervals: opaque id, owning user,
normalized address, start/end timestamps and `migration_baseline`/`identity_write`
source. A user and an address each have at most one open interval. Intervals are
half-open `[started_at, ended_at)`; reacquisition creates a new row. The users
row/unique-email constraint and AFTER trigger serialize changes and avoid orphan
history from losing `ON CONFLICT DO NOTHING` inserts. Boundary time is captured
after locking and clamped to previous start/latest address release if the clock
moves backwards. A zero-length interval claims no temporal span.

The owner approved renumbering the originally proposed 048 file to 045 after
the existing migration-continuity gate rejected absent 045–047. The SQL contract
is unchanged; no migration was applied live and no placeholder files or relaxed
numbering gate were introduced.

Normalized addresses are **plaintext PII**, matching the existing identity
schema, not encrypted message/memory content. The table denies PUBLIC access and
has no API or logging surface. It holds no credentials, code/nonce/token bytes,
raw IP/user agent or arbitrary metadata. Ownership evidence is not an additional
authentication mechanism and must not be used to merge accounts.

Supported claims lock proof → user → interval. New email-bearing audit/invite
writers with explicit account references require lock-path review: creation
capture must not introduce a conflicting later user-lock path. Arbitrary
operator SQL is not certified deadlock-free; PostgreSQL aborts a detected cycle
atomically rather than committing partial provenance.

New email challenges, invites and email-bearing audit records capture the current
interval as `email_ownership_id` while holding a SHARE lock that prevents its
closure. A lock-wait refresh can observe a successor; an absent committed owner
is explicitly unknown. These references are not retroactively filled or moved
when an address changes. They use `ON DELETE SET NULL`, not a cascade into records
that another account might subsequently claim.

Consumed email challenges and Google nonces acquire `claimed_user_id` only in a
successful account/tenant/session transaction, including first-account creation.
It references users with `ON DELETE CASCADE`, and non-NULL requires consumption.
The exact consumed row is locked/revalidated before resolving the account; email
and durable-provider resolution lock the current user, including conflict
fallback. Identity proof is still burned **before** this transaction; failures
do not revive it. A retention sweep that removes it during a long pause makes
the later claim fail closed. Google `user_id_proposed` is not claimed ownership.
The #508 browser/cache/cookie contract is unchanged.

## Legacy and eventual account deletion

Migration baseline records the account's current address **from observation time
only**, not account birth, verification, provider link or an email found in old
records. Provider link-email metadata is mutable and cannot reconstruct a lost
ownership interval. Existing creation references and claimed ids remain NULL;
old consumed rows/proposed nonces are not attributed from an address alone.
Existing explicit audit/invite account references remain attributable.

Later deletion must inspect explicit references **before** account/provenance
cascade, with these precedence rules:

- A challenge's claimed account overrides issuance provenance: A-issued/B-claimed
  is B's record, not an address match for A.
- An audit's explicit user reference overrides email creation provenance.
- Invites used or created by the account are explicit relationships, including
  an invite the account created for another address, as approved in deletion §6.1.
- Otherwise only explicit creation provenance is evidence; legacy NULLs never
  become an address-only fallback. Provider subjects are separately durable
  identities, not a reason to guess an email interval.

After attributable records are handled, deleting the user cascades that user's
ownership intervals and claimed proofs. Removing A's interval only nulls a
remaining B-claimed proof's issuance reference; it never deletes B's proof.
Reset must preserve identity provenance alongside the identity keep-list. This
slice supplies neither an account-purge helper nor the deletion/reset protocol.

## Finite retention contract

Eligibility is inclusive at `anchor + retention <= sweep_now`, using absolute
UTC durations. Known anchors are never restarted just because expiry is noticed
late or a terminal record gets an unrelated update.

| Record | Retention | Anchor |
| --- | --- | --- |
| Identity audit | 90 days | `created_at` |
| Consumed email challenge / Google nonce | Seven days | `consumed_at` |
| Unconsumed expired challenge / nonce, including locked challenges | Seven days | `expires_at` |
| Consumed invite | Seven days | `consumed_at` |
| Naturally expired invite, including still-`active` rows past expiry | Seven days | `expires_at` |
| Disabled invite | Seven days | Observed disablement, `terminal_at` |

For legacy disabled invites or consumed invites without `consumed_at`, the new
terminal anchor is migration observation time: a finite retention fallback, not
a fabricated historical timestamp. Reactivation clears `terminal_at`; an active
unexpired invite is preserved even with inconsistent legacy consumed metadata.
Unexpired unconsumed challenges/nonces, devices/sessions, provider identities,
other active credentials and ownership intervals are not swept. Unattributable
terminal rows expire by the same finite policy, never by guessed account ownership.

[`retention.py`](../orchestrator/services/identity/retention.py) captures one
database clock per invocation, selects ordered locked batches with `SKIP LOCKED`,
and rechecks eligibility in DELETE. Each of four tables commits independently;
partial failure is surfaced and a repeat resumes safely. An error stops later
tables for that invocation; a persistent earlier-table failure can prevent later
progress until repaired, so monitoring remains a rollout prerequisite.
One invocation deletes
at most 1,000 per table/4,000 total, with five-second statement and one-second
lock timeouts, plus a 30-second whole-job bound including pool acquisition.
The existing worker registers it hourly at minute 20 and retains only counts in
shared Redis results, preserving P0.6's content-free result policy.

This is bounded work, **not a verified continuous physical-removal deadline**:
scheduling, locks, downtime and backlog can delay deletion. Rollout, migration
lock duration on real data, throughput/capacity, operator monitoring and backups
remain unqualified. No live retention execution is part of this source PR.

## Acceptance boundary

Real disposable PostgreSQL regressions and controlled clocks must cover A/B
reassignment and attribution precedence, change/capture/claim/sweep contention,
rollback with irreversible proof burning, audit expiry preserving history,
unknown legacy attribution, exact boundaries, active/renewed records and repeat/
batch-safe sweeps. Final-source repository gates and current-head review are
required; this contract alone is not a passing-test or release claim.

Executable source coverage lives in `tests/test_identity_retention_postgres.py`
(migration, controlled clocks, attribution, FK behavior, lock observations and
sweeps), `tests/test_identity_claim_postgres.py` (real completion transactions,
cache/auth compatibility and failure paths), and `tests/test_identity_retention.py`
(worker registration and content-free results). PostgreSQL cases require the
existing disposable `TASKS_TEST_DATABASE_URL`, replay migrations into isolated
schemas and never read the application `DATABASE_URL`.

J1, the artifact store/catalog, worker-failure provenance, deletion endpoints,
fence/drain/grace/reset, journal replay and production qualification remain
separate. The deletion/reset feature matrix states remain **Not started**.
