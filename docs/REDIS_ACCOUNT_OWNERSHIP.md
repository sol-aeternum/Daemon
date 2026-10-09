# Account-owned Redis prerequisite (P0.6)

Source implementation for #469, [account deletion design](ACCOUNT_DELETION_DESIGN.md)
§6.3. Verification/release status is established by the prerequisite PR's gates
and reviews, not by this contract. Account deletion/reset, manifests, draining,
replay and eviction are **not implemented** by this prerequisite.

## Approved contracts

- A dedicated stable `DAEMON_REDIS_ACCOUNT_HASH_KEY` derives a versioned,
  domain-separated full-length HMAC-SHA256 account token. No auth/encryption
  secret reuse (including the configured admin bearer key and internal proxy HMAC
  secret) or automatic key generation. See
  [environment migration](ENV_SURFACE_MIGRATION.md).
- Owner-scoped keys use `account:v1:{token}`; home-suggestion Lua keys retain
  their common hash slot. Native ARQ key families retain their own prefixes,
  with the account namespace at the start of each job ID. Shared queue/abort
  membership is also state; scanning one top-level prefix alone is insufficient.
- User jobs carry identifiers/control metadata, never queued message snapshots.
  Titles re-read the exact inserted user message with owner/conversation checks.
  Extraction references carry message IDs, keyed source versions and fragment
  indexes. Unchanged sources resume at their exact fragment; changed sources
  restart the message from its beginning. Processing is at-least-once:
  fragments may repeat, and earlier extracted facts are not automatically undone.
- Entity projection keeps the account owner as routing metadata and passes the
  existing worker's owner/memory-ID arguments positionally. Nonempty extraction
  and failed-chunk partial progress exercise the native enqueue payload contract.
- The disposable durable-restart drill generates its own independent canonical
  ownership key, alongside its test-only cipher/auth keys. This is not automatic
  application key generation or live key provisioning; inherited credentials
  cannot override the drill-owned fixture under its sanitized Compose environment.
- User jobs write **no ARQ result key**, including preexecution failures.
  Content-free account completion markers preserve the former dedup windows;
  marker publication/cleanup and enqueue checks are atomic. Failed extraction's
  marker retains its prior explicit unblock behavior; successful extraction,
  chat wakeups and home-suggestion jobs retain their zero-result dedup policy.
- Original outcome bytes remain in memory for the existing failure audit, after
  Redis cleanup and under its existing timeout. Known shared jobs serialize
  only allowlisted counts and safe ARQ protocol metadata, not arguments, account
  IDs, exception text, titles or per-user details. Existing DB/mail failure
  evidence remains unchanged; crash-proof audit delivery is not claimed.
- Existing `/memories/consolidate` and `/memories/dream` response fields/types,
  statuses and authorization remain unchanged. Their opaque `job_id` values
  change to account-prefixed IDs for user jobs; all-user jobs stay shared.

## Shared fetch cache

New `fetch:v3` keys contain a domain-separated keyed URL/mode/version identity,
not a requested URL. Values remain the shared page cache. A trusted caller must
supply its owner for caching; without one, no cache read/write occurs. Native
snapshot fetches still bypass the cache, and retired research stays disabled.
Publication writes the shared value and an account-prefixed ownership index
atomically. Index metadata has no shorter TTL than a shared entry renewed by
another account; bounded pruning removes a reference only when the value is
absent at the atomic check. This uses the existing standalone Redis topology;
ARQ and shared multi-key publication are not Redis Cluster qualification.

## Explicit qualification limits

There is no legacy key scan/migration/deletion or namespace fallback. Old
raw-URL cache entries are not read, migrated or deleted. Existing queued
content, retained historical results and unprefixed counters/fences are not
retroactively sanitized. Normal worker finalization still removes a job's
native control state and suppresses unclassified results; read-only argument
compatibility does not establish a safe live transition.
Before a separately approved deployment, a coordinated transition must preserve
outstanding work and admission/revocation state; old persistent fences do not
disappear merely by waiting for a payload TTL. Key rotation is unqualified.

Per-user failure ownership and aggregate DB audit redaction remain blocked on
separately approved migration 048. This prerequisite does not make those
ownerless DB/mail records deletable and does not implement a deletion fence.
No migration 045–050, credential setup, deployment, retention execution, journal
or account purge is authorized. Feature-matrix deletion/reset rows stay
`Not started`.

## Design §8 acceptance mapping

| Scenario | P0.6 prerequisite check | Still required for deletion |
|---|---|---|
| Drained job resumes after Redis eviction | User result suppression on every terminal/preexecution path | Writer registry, termination proof, manifest and repeated eviction |
| Job crash after key collection | Account discovery independent of restored DB rows; identifiers-only new payloads | Durable manifest and restart-safe purge |
| Scheduled dreaming failed for several users | Shared Redis counts-only; original DB/mail audit evidence retained | Migration 048 ownership and per-user purge/redaction |
| Web fetch shortly before reset | Atomic shared-cache ownership index with opaque key names | Reset/purge index eviction and fencing |

The PR must supply executable positive and negative evidence on its final head,
including marker races, continuation mutation/missing-owner paths and startup
validation. Those checks are not equivalent to the remaining end-to-end scenarios.
