# Unbound restore-history schema at unselected startup (#731): proposal

**Status: A1 approved by the owner in the working session (2026-11-17, "Approve A1"),
pending the owner's confirmation on the PR, and implemented in
`orchestrator/services/restore_history/runtime.py` (`UNSELECTED_HISTORY_SQL`).** This
changes the selection rule in `RESTORE_HISTORY_RUNTIME.md` §Selection and the by-key
fallback in `RESTORE_HISTORY_OBSERVATION.md`. **The native fence (§Native fence) was
approved in the working session (2026-11-17, "Approve fence"), also pending the
owner's confirmation on the PR.** It adds migration 073 (one trigger function and
one trigger, no table, grant or environment change). Nothing here enables H, durable
chat or execution, or deploys anything. The rollout tracker #477 is unaffected.

## Problem

Migrations 056, 057, 064 and 065 create `task_history_*` tables and functions.
Today the unselected path accepts only positive catalog absence of those objects:

- `require_unselected_history` (`orchestrator/services/restore_history/runtime.py`)
  runs before native recovery in backend startup (`orchestrator/db.py`) and worker
  startup (`orchestrator/worker/worker.py`), and refuses with
  `history_configuration_required` if any `task_history_*` relation or function exists.
- The unselected by-key route (`orchestrator/routes/tasks.py`) returns 503 whenever
  `public.task_history_binding` exists.

Selected H is observation-only by design: the backend refuses mutations with
`history_unavailable`, and the worker always ends with `history_observation_only`.
Startup on `main` also requires the schema from 066, 071 and 072. On a database
migrated through 072, there is no configuration of `main` that gives a working
stack. The live upgrade from 043 is blocked on this.

## What H's presence actually proves

- `task_history_binding` is a singleton (`singleton boolean PRIMARY KEY CHECK (singleton)`).
- `task_history_acceptance` and `task_history_projection` both reference
  `task_history_binding(history_id)` with `ON DELETE RESTRICT`. All three tables
  have immutable row triggers and no-truncate triggers.
- No production code inserts a binding row. Only Postgres test fixtures do.
  Accepting H work therefore requires a binding row first, and a committed
  binding row cannot be removed through ordinary SQL.

So "H schema exists" is weaker evidence than the current check treats it as.
**"H was bound"** is what makes downgrade unsafe, and a missing binding row
implies no H acceptance or projection rows. Until a binding exists, the H
schema is inert code and empty tables.

## Proposed rule

The unselected path is allowed when either:

1. no `task_history_*` relation or function exists (today's rule, unchanged); or
2. **the H schema is present, exactly shaped, and provably unbound.** Concretely:
   - `public.task_history_binding`, `public.task_history_acceptance` and
     `public.task_history_projection` exist as ordinary tables (`relkind = 'r'`,
     not a view, foreign table or partition), all with the same owner;
   - the two foreign keys to `task_history_binding(history_id)` exist with
     `ON DELETE RESTRICT`;
   - the immutable and no-truncate triggers exist on all three tables and are
     enabled;
   - the proof of emptiness for all three tables (options A1/A2 below) holds.

Everything else still refuses with `history_configuration_required`. That
includes any binding row, an unrecognised H object shape, or catalog evidence
that can't be read.

The same predicate replaces the `to_regclass('public.task_history_binding')`
test in the by-key route. It is re-evaluated on every by-key request, so an
unselected backend stops answering by-key reads from native SQL as soon as a
binding appears. Backend and worker startup evaluate it at the same point as
today: before any native recovery or J1 write.

Selected H, the split A/W roles, J1 and the observation-only barrier are
unchanged. Removing settings from an H host whose database has been bound
still refuses, so the existing anti-downgrade guarantee holds from the first
binding on.

## How to prove "unbound" without new authority

A and W must hold no direct privilege on the H tables:
`task_history_check_caller` refuses a caller with any table or column
privilege on them. The single-owner legacy role can read them, but the split
roles cannot. The proof must work for both.

### A1: relation storage size (recommended)

Check `pg_relation_size(<table>, 'main') = 0` for the three tables.

- A committed, visible heap tuple needs at least one heap page, so size 0
  proves the table is empty. A false negative isn't possible.
- False positives fail safe. An aborted insert leaves the heap at 8192 bytes,
  so the host refuses. The operator's remedy is a plain `VACUUM` of the empty
  table, which truncates the trailing empty pages.
- This was probed on PostgreSQL 16.12. A role with no grants on
  `task_history_binding` (`has_table_privilege` false) read its size as 0. After
  a rolled-back insert, the size was 8192.
- No migration, function, grant, privilege map or ACL-inventory change is
  needed. The exact maps in `config/task_privileges/` stay valid.
- Cost: the proof rests on physical storage rather than a logical read. That's
  unusual for this codebase, so it needs a pinned comment and tests. The
  exact-shape checks above stop a view or another relation kind with size 0
  from substituting for the table.

### A2: a SECURITY DEFINER probe

A new migration (073) adds `public.task_history_unbound() RETURNS boolean`,
owned by the H owner, with `SET search_path = pg_catalog`. It returns `NOT EXISTS`
over the three tables. EXECUTE goes only to the A/W roles and the legacy app role.

- This is a logical read with explicit intent.
- Cost: a schema change plus a new protected function. It touches:
  - `function_execute` in both exact maps, and their digests;
  - the `_RESULTS` and ACL catalogue in `orchestrator/tasks/composition.py`;
  - the allowances in `task_history_check_caller`.
- Every later H review has to keep the new function in its inventory. All of
  these are owner-approved contracts.

## Alternatives considered

- **B: an explicit operator marker** (an environment value or a database row
  meaning "H deliberately unbound"). The binding-row proof is still required,
  so the marker adds an environment surface (with parity-gate and `.env.example`
  work) without closing any gap that A doesn't. Not recommended.
- **C: take 056/057/064/065 out of the default migration chain.** 059 and 062
  inventory `task_history_%` functions in their privilege checks, and 064
  rewrites `resource_output_catalog_principal` and
  `resource_output_presentation_principal`. Removing H migrations would
  therefore split the resource-output chain, and the filename-tracked
  `_migrations` history on existing databases would diverge. Not recommended.
- **D: change nothing.** The live database stays at 043 until H activation is
  designed and approved. This is the status quo, and it is safe. It leaves the
  live deployment indefinitely on old images.

## Residual risk (unchanged in kind)

`RESTORE_HISTORY_OBSERVATION.md` already records that an unconfigured runtime
can't detect a database restored to a pre-H state. Under this proposal, the
same blind spot covers a database restored to a pre-binding state, where the
independent H log on disk says otherwise. Only an independent authority closes
that gap, and that work is out of scope for this proposal. Until H has a
production initializer, no deployment can be in that state.

The A1 predicate alone is a point-in-time check: a binding could begin after it
returns while unselected recovery or a by-key read continued. §Native fence
closes that check-to-use race; an operator instruction to stop the stack is not
relied on.

## Implementation (A1)

Beyond the shape list above, the implemented predicate also refuses unlogged
tables (crash truncation could erase a binding), partitions, inheritance
children or parents (child rows read through the parent without growing its
heap), any other `task_history_*` relation that is not an index of the three
tables, and immutable triggers in `REPLICA` or disabled mode.

- A shared predicate `history_unselected_compatible(db)` in
  `orchestrator/services/restore_history/runtime.py`, used by
  `require_unselected_history` and the by-key route. The route's import is
  one exact entry in the source-inventory allowlist.
- Tests:
  - `tests/test_history_unbound_route.py`: the by-key route answers natively
    only on an exact `True`, returns 503 otherwise or on a check failure, and
    binds the six trigger names.
  - `tests/test_history_unbound_start_postgres.py`
    (`--compute-metadata-postgres`), migrated through the latest migration,
    checked as the owner and as `md_app` with no H privilege:
    - pre-H and unbound H are compatible;
    - a committed binding refuses, even after `VACUUM`;
    - an aborted insert refuses until `VACUUM`;
    - an uncommitted concurrent binding refuses;
    - nine shape changes refuse (disabled or replica triggers, dropped FK,
      unlogged table, inheritance child, extra table, extra view, binding
      replaced by a view);
    - stray or partial H objects and a 056-only schema refuse.
  - Not covered here: full backend and worker startup on an unbound H
    database. The #732 role-provisioning harness can cover that by including
    the H migrations instead of excluding them.
- Docs:
  - `RESTORE_HISTORY_RUNTIME.md` §Selection;
  - `RESTORE_HISTORY_OBSERVATION.md` (pre-H compatibility);
  - `TASK_STARTUP_COMPOSITION.md` (unselected recovery);
  - this file is the decision record.

## Native fence

Invariant: no H binding commits while any unselected process holds native
recovery or by-key read authority, and no unselected process gains that
authority once a binding exists or is in progress. One advisory key
K = (1145588051, 1), two-int form, separate from the migration runner's bigint
key. Every acquisition is a non-blocking try, so nothing waits or deadlocks.

- **L1, process presence** (`orchestrator/services/restore_history/native_fence.py`):
  backend and worker each open one dedicated, unpooled connection before any
  other database work. It takes K shared (session scope), then runs the A1
  predicate in a separate statement, so the proof's snapshot postdates the
  lock. It is held for the process lifetime with TCP keepalives. If the server
  ends it, the process exits (code 70) rather than continue unfenced.
- **L2, per borrowed connection:** the asyncpg `setup` hook of every unselected
  pool requires L1 alive, takes K shared on that connection, then re-runs the
  predicate. asyncpg's release reset (`pg_advisory_unlock_all()`) drops it, and
  a failing `setup` closes the connection. In-flight native work therefore
  holds K for exactly as long as it holds a connection.
- **L3, binder side (migration 073):** a statement-level `BEFORE INSERT`
  trigger on `task_history_binding`, `ENABLE ALWAYS` so
  `session_replication_role=replica` does not skip it. It takes K exclusively
  for the inserting transaction and raises `history_native_hosts_active` while
  any L1/L2 holder exists. Once it holds K, new L1/L2 tries fail
  (`history_fence_unavailable`); after commit they see the row and refuse.
- **Fail closed on the fence itself:** the A1 predicate also requires that exact
  trigger (always-enabled, statement-level `BEFORE INSERT`, plpgsql, not
  `SECURITY DEFINER`, owned by the binding owner, `search_path=pg_catalog`,
  body SHA-256 pinned) and no other use of its function. A disabled,
  origin-only, dropped or replaced fence refuses unselected startup and every
  later acquire. H-selected qualification (`acceptance.CATALOG`) admits exactly
  that trigger and treats any other shape as drift.

Ownership, crashes and stale connections: session advisory locks belong to the
server session. Process exit, crash or `SIGKILL` ends the session and releases
K; there is no lease to expire or row to clean up. A stopped or hung process
keeps K and so keeps binding refused (fail closed) until it ends; TCP
keepalives bound how long a vanished client host can do so. Several backend and
worker processes each hold K shared and the binder waits for all of them.
Session-mode connections are required; a transaction-mode pooler would break L1
and L2.

Residual risk, documented and not claimed as enforced:
- a live process all of whose sessions were cut (network partition or
  privileged termination) may finish an external effect it had already
  started, though it can't begin new database work (L2 refuses, and L1 loss
  exits the process);
- owner or superuser DDL can drop or alter 073 between checks. The next L2
  acquire refuses, but a binder in that window is not fenced.

Tests (`--compute-metadata-postgres`, real runner through 073, hosts as
`md_app`): `tests/test_history_native_fence_postgres.py` covers a binding
committed before qualification; an open binder transaction (rollback, commit);
binding after qualification; L2 alone with a borrowed connection; by-key reads
with the binding during the read and between the route check and the read;
terminated presence; two subprocess hosts with one `SIGKILL`ed; a `SIGSTOP`ped
host; a replica-mode binder; and five fence alterations. The H-side shape is in
`tests/test_history_acceptance_postgres.py`
(`test_h_catalog_admits_exactly_the_073_native_fence`).

## Owner decisions

1. Rule change approved (unbound H schema on the unselected path).
2. Proof: A1 (storage size, no schema change).
3. Residual-risk statement accepted with A1.
4. Native fence (L1/L2/L3, migration 073) approved as "Approve fence"; the
   residual risks above are part of that design.

Items 1–3 were given in the working session as "Approve A1", item 4 as "Approve
fence". All need the owner's own confirmation on the PR.
