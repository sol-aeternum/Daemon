# Complete output namespace inventory

Source-only continuation of the approved backend-owned publication design and
[059 catalog capability](RESOURCE_OUTPUT_CATALOG.md). Migration
`060_resource_output_namespace_inventory.sql` adds no table, login, application
grant, storage, pool, socket or runtime caller. H-configured startup remains
observation-only; [#477](https://github.com/sol-aeternum/Daemon/issues/477) stays open.

## Exact read capability

`resource_output_namespace_inventory(uuid,integer)` requires the independently
expected J1 identity and an explicit record budget from 1 through 10000. It returns
a version-1 closed JSON object with the binding identity, all applied markers and
all namespace projections. The two arrays each have that budget. The query reads
all accounts and journal identities; it does not filter away contradictory rows.
All three projections share one SQL statement/MVCC snapshot. More than the budget,
a missing/mismatched binding, or an unqualified caller refuses the whole operation,
never an apparently complete prefix. There is no cursor, pruning, retention policy
or production default. The ceiling matches the existing C1 catalog scan ceiling.

The same distinct direct P login must hold the five 058/059 capabilities and this
sixth entrypoint, all directly and without grant option. The new validating helper
is owner-only. Existing A/W, privileged membership, SET ROLE, temporary namespace,
table/column/sequence, R0 and H-function exclusions remain. Unsafe migration default
ACLs refuse atomically. SQL requires READ COMMITTED; callers may use a read-only
transaction. The read uses the existing 250 ms output-function lock timeout; the
caller still owns its statement/absolute deadlines and physical cleanup. This
capability takes no row/advisory locks and changes no projection.
P is a trusted backend-internal service capability, not a per-account user API.
No metadata from this global inventory may be exposed to a worker or user client.

## Independent parity, not effect authority

`orchestrator.namespace_inventory` provides immutable typed observations and closed
bounded JSON decoding. The shared parity check stays in the already-admitted
trusted namespace owner (`orchestrator.artifact_namespaces`); the packet module
imports no account-control store, mixed journal or namespace candidate. Existing
noncomposition guard allowlists and assertions are unchanged. The optional supplied-connection read helper is uncalled at runtime;
it owns no pool, transaction, retry or filesystem work. It cannot attest cleanup,
COMMIT, current lifecycle, bytes, physical readiness or publication authority.
Its caller must retain actual SQL cleanup through cancellation before H release.

The trusted caller must independently scan the actual J1 bytes and compare every
marker and namespace birth, including other accounts. Wrong identities, duplicate
or missing counterpart rows and mismatched digests/fields refuse. A v1 container
or lifecycle event in the mixed history requires recovery; it is never filtered
out. Existing native behavior for a journal birth with neither SQL counterpart is
preserved: that birth stays unapplied and yields no namespace state. This reader
never adopts or backfills it. A publisher still must require its own original ready
namespace and independently verify physical files under the approved ownership.
States `provisioning`, `uncertain` and `retired` are returned truthfully, not promoted.

A snapshot can become stale immediately. Complete SQL/J1 comparison does not freeze
future writers, establish independently retained artifact restore history, or grant
permission to publish. The future owner still needs H lifetime/operation ownership
before native transactions, original generation/attempt/lease/Stop fences, full
ciphertext/file authentication, short filesystem locks without DB waits, known
COMMIT and physical cleanup completion. No historical receipt authorizes replay,
replacement, retry, or a new effect. The ordinary native reader keeps its existing
uncapped direct-row behavior; this P budget does not change native lifecycle policy.

## Qualification and remaining work

Synthetic unit and owned PostgreSQL/POSIX tests cover strict decoding, complete
cross-account inventory, overflow refusal, ACL drift, restored contradictions,
unapplied births, lifecycle history, concurrent state change and cancellation.
Isolated-schema replay separately checks the actual migration payload and closed
ACLs. These are source proofs, not live acceptance or artifact restore qualification.

Restricted append reservation/read collaborators, trusted split output/file
composition, authenticated bounded local transport, full execution installation,
artifact restore/lifecycle/privacy proof and exact approved live migration/ACL/key/
UID/socket/storage/deployment actions remain launch gates. Nothing here changes
the observation-only runtime barrier or closes #477.
