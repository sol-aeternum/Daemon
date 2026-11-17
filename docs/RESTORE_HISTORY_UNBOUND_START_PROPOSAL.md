# Unbound restore-history schema at unselected startup (#731): proposal

**Status: proposed for owner decision, not implemented.** This changes the approved
selection rule in `RESTORE_HISTORY_RUNTIME.md` §Selection and the by-key fallback in
`RESTORE_HISTORY_OBSERVATION.md`. It has no code, migration, grant, environment or
deployment change. Nothing here enables H, durable chat or execution. The rollout
tracker #477 is unaffected.

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

An owner-role SQL session could insert a binding while unselected hosts are
running. The by-key route notices on the next request. Worker startup and
native recovery don't, until restart. The binding procedure, once written,
must stop the stack first. No current procedure binds H.

## Implementation scope if approved (A1)

- A shared predicate `history_unbound_or_absent(conn)` in
  `orchestrator/services/restore_history/runtime.py`, used by
  `require_unselected_history` and the by-key route.
- Tests:
  - unit tests for the SQL text and refusal codes;
  - Postgres tests for:
    - H schema present and unbound: backend and worker start unselected;
    - binding row present: refuses;
    - aborted insert: refuses;
    - after `VACUUM`: starts;
    - binding replaced by a view: refuses;
    - missing FK or disabled trigger: refuses;
    - by-key route both ways.
- The #732 role-provisioning harness can then include the H migrations instead
  of excluding them.
- Docs:
  - `RESTORE_HISTORY_RUNTIME.md` §Selection;
  - `RESTORE_HISTORY_OBSERVATION.md` (pre-H compatibility);
  - this file becomes a record of the decision.

## Owner decisions requested

1. Approve the rule change (allow an unbound H schema on the unselected path), or keep D.
2. Choose the proof: A1 (storage size, no schema change) or A2 (new migration and probe function).
3. Accept the residual-risk statement above as the documented limit.
