"""Explicit isolated-host and selected separate-service startup composition.

The trusted host supplies paired pools made from its ONE endpoint and database.
Separate W-only startup requires an existing matching journal binding. Catalog equality is a negative check, not cross-cluster or restore
attestation. This module accepts no DSN, creates/closes no pool and schedules no
work. Its bundle is cooperating-caller wiring, not a Python security sandbox.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import asyncpg

from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.services.deletion_journal.barrier import (
    configured_journal,
    require_recovery_barrier,
)
from orchestrator.tasks.inference import (
    InferenceRecovery,
    acceptor_catalog_preflight,
    acceptor_preflight,
)
from orchestrator.tasks._invocation import invocation, require_effect
from orchestrator.services.connection_cleanup import pool_connection
from orchestrator.tasks.store import TaskStore

_ADMITTED = object()
_TABLES = (
    "compute_scope_metadata",
    "compute_reservation_metadata",
    "attempt_lifecycle_control",
    "attempt_lifecycle_commands",
    "task_inference_recovery",
)
_HELPERS = (
    "compute_metadata_check_account",
    "attempt_lifecycle_identity",
    "attempt_lifecycle_lock",
    "attempt_lifecycle_live",
    "task_inference_recovery_immutable",
)
_COLUMNS = {
    "compute_scope_metadata": "id:uuid account_id:uuid created_at:timestamptz",
    "compute_reservation_metadata": "reservation_id:uuid metadata_scope_id:uuid runtime_identity:uuid dispatch_nonce:uuid created_at:timestamptz",
    "attempt_lifecycle_control": "id:uuid identity:jsonb account_id:uuid task_id:uuid epoch:int8 financial_scope_id:uuid metadata_scope_id:uuid attempt_started_at:timestamptz created_at:timestamptz revision:int8 closed_at:timestamptz",
    "attempt_lifecycle_commands": "command_id:uuid lifecycle_id:uuid opcode:text reason:text revision:int8 committed_at:timestamptz",
    "task_inference_recovery": "task_id:uuid account_id:uuid account_generation:int8 protocol_version:int2 created_at:timestamptz may_have_dispatched:bool first_lifecycle_id:uuid first_reservation_id:uuid first_runtime_identity:uuid first_dispatch_nonce:uuid first_marked_at:timestamptz",
}
_RESULTS = {
    "compute_metadata_check_account": ("pg_catalog.text", False),
    "compute_metadata_create_scope": ("public.compute_scope_metadata", False),
    "compute_metadata_attach": ("public.compute_reservation_metadata", False),
    "compute_metadata_read_scope": ("public.compute_scope_metadata", False),
    "compute_metadata_read_binding": ("public.compute_reservation_metadata", False),
    "attempt_lifecycle_identity": ("pg_catalog.jsonb", False),
    "attempt_lifecycle_lock": ("public.attempt_lifecycle_control", False),
    "attempt_lifecycle_live": ("pg_catalog.bool", False),
    "attempt_lifecycle_readback": ("pg_catalog.jsonb", False),
    "attempt_lifecycle_register": ("pg_catalog.jsonb", False),
    "attempt_lifecycle_assert_open": ("pg_catalog.void", False),
    "attempt_lifecycle_close": ("pg_catalog.jsonb", False),
    "task_inference_recovery_immutable": ("pg_catalog.trigger", False),
    "task_inference_dispatch_begin": ("pg_catalog.jsonb", False),
    "task_inference_dispatch_readback": ("pg_catalog.jsonb", False),
    "task_inference_recovery_lock": ("pg_catalog.jsonb", False),
    "attempt_lifecycle_abandoned_page": ("pg_catalog.jsonb", True),
    "attempt_lifecycle_close_abandoned": ("pg_catalog.jsonb", False),
}
_CONSTRAINTS = {
    "compute_scope_metadata": (
        "FOREIGN KEY (account_id) REFERENCES entitlement_accounts(user_id) ON DELETE CASCADE",
        "PRIMARY KEY (id)",
    ),
    "compute_reservation_metadata": (
        "FOREIGN KEY (metadata_scope_id) REFERENCES compute_scope_metadata(id) ON DELETE CASCADE",
        "FOREIGN KEY (reservation_id) REFERENCES entitlement_reservations(id) ON DELETE CASCADE",
        "PRIMARY KEY (reservation_id)",
        "UNIQUE (dispatch_nonce)",
        "UNIQUE (runtime_identity)",
    ),
    "attempt_lifecycle_control": (
        "CHECK (((closed_at IS NULL) = (revision = 1)))",
        "CHECK ((epoch > 0))",
        "CHECK ((revision = ANY (ARRAY[(1)::bigint, (2)::bigint])))",
        "PRIMARY KEY (id)",
        "UNIQUE (financial_scope_id)",
        "UNIQUE (metadata_scope_id)",
        "UNIQUE (task_id, epoch)",
    ),
    "attempt_lifecycle_commands": (
        "CHECK ((opcode = ANY (ARRAY['register'::text, 'close'::text])))",
        "CHECK ((((opcode = 'register'::text) AND (reason = ''::text) AND (revision = 1)) OR ((opcode = 'close'::text) AND (reason <> ''::text) AND (revision = 2))))",
        "CHECK ((reason = ANY (ARRAY[''::text, 'requested'::text, 'cancelled'::text, 'stale'::text])))",
        "CHECK ((revision = ANY (ARRAY[(1)::bigint, (2)::bigint])))",
        "FOREIGN KEY (lifecycle_id) REFERENCES attempt_lifecycle_control(id)",
        "PRIMARY KEY (command_id)",
    ),
    "task_inference_recovery": (
        "CHECK ((account_generation >= 0))",
        "CHECK ((((NOT may_have_dispatched) AND (first_lifecycle_id IS NULL) AND (first_reservation_id IS NULL) AND (first_runtime_identity IS NULL) AND (first_dispatch_nonce IS NULL) AND (first_marked_at IS NULL)) OR (may_have_dispatched AND (first_lifecycle_id IS NOT NULL) AND (first_reservation_id IS NOT NULL) AND (first_runtime_identity IS NOT NULL) AND (first_dispatch_nonce IS NOT NULL) AND (first_marked_at IS NOT NULL))))",
        "CHECK ((protocol_version = 1))",
        "FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE",
        "PRIMARY KEY (task_id)",
    ),
}


async def _schema(conn: Any, acceptor: str, worker: str, *, is_worker: bool) -> None:
    """Positive public-schema and current ACL evidence, without reading content.

    Reviewed migrations remain the supported creators; this is not a forensic
    certification of arbitrary DBA changes to function bodies or restored data.
    """
    # Positive checks for the ordinary SQL actually consumed by this slice, not
    # a grant script or certification of all generic MemoryStore/background jobs.
    required = {
        "entitlement_accounts": "SELECT UPDATE",
        "account_deletions": "SELECT",
        "account_suspension_causes": "SELECT",
        "tasks": "SELECT UPDATE",
        "task_events": "SELECT INSERT",
        "conversations": "SELECT UPDATE",
        "messages": "SELECT UPDATE",
    }
    if is_worker:
        required.update(
            {
                "task_attempts": "SELECT INSERT UPDATE",
                "task_operations": "SELECT INSERT UPDATE",
                "entitlement_reservations": "SELECT INSERT UPDATE",
                "entitlement_usage_periods": "SELECT INSERT UPDATE",
            }
        )
    else:
        for name in ("tasks", "conversations", "messages"):
            required[name] += " INSERT"
    pairs = [
        ("public." + table, privilege)
        for table, privileges in required.items()
        for privilege in privileges.split()
    ]
    if (
        await conn.fetchval(
            "SELECT bool_and(pg_catalog.has_table_privilege($3::name,t,p)) "
            "FROM unnest($1::text[],$2::text[]) AS required(t,p)",
            [table for table, _ in pairs],
            [privilege for _, privilege in pairs],
            worker if is_worker else acceptor,
        )
        is not True
    ):
        raise ObservationBlocked("inference_ordinary_privileges_refused")
    if (
        await conn.fetchval(
            """SELECT count(*)=5 AND bool_and(c.relkind='r')
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY($1::text[])""",
            list(_TABLES),
        )
        is not True
    ):
        raise ObservationBlocked("inference_schema_refused")
    results = await conn.fetch(
        "SELECT p.proname,tn.nspname||'.'||t.typname AS type,p.proretset "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_type t ON t.oid=p.prorettype "
        "JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace "
        "WHERE n.nspname='public' AND p.proname=ANY($1::text[])",
        list(_RESULTS),
    )
    if (
        len(results) != len(_RESULTS)
        or {r["proname"]: (r["type"], r["proretset"]) for r in results} != _RESULTS
    ):
        raise ObservationBlocked("inference_schema_refused")
    columns = await conn.fetch(
        "SELECT c.relname,a.attname,t.typname,a.attnotnull,"
        "pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS default "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
        "JOIN pg_catalog.pg_type t ON t.oid=a.atttypid "
        "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
        "WHERE n.nspname='public' AND c.relname=ANY($1::text[]) ORDER BY c.relname,a.attnum",
        list(_TABLES),
    )
    shapes: dict[str, list[str]] = {}
    for row in columns:
        table, column = row["relname"], row["attname"]
        shapes.setdefault(table, []).append(column + ":" + row["typname"])
        nullable = (table == "attempt_lifecycle_control" and column == "closed_at") or (
            table == "task_inference_recovery" and column.startswith("first_")
        )
        default = None
        if column in ("created_at", "committed_at"):
            default = "clock_timestamp()"
        elif (table, column) in (
            ("attempt_lifecycle_control", "revision"),
            ("task_inference_recovery", "protocol_version"),
        ):
            default = "1"
        elif (table, column) == ("task_inference_recovery", "may_have_dispatched"):
            default = "false"
        if row["attnotnull"] == nullable or row["default"] != default:
            raise ObservationBlocked("inference_schema_refused")
    if {name: " ".join(shape) for name, shape in shapes.items()} != _COLUMNS:
        raise ObservationBlocked("inference_schema_refused")
    constraints = await conn.fetch(
        "SELECT c.relname,pg_catalog.pg_get_constraintdef(k.oid) AS definition,k.convalidated AS valid "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_constraint k ON k.conrelid=c.oid "
        "WHERE n.nspname='public' AND c.relname=ANY($1::text[])",
        list(_TABLES),
    )
    definitions: dict[str, list[str]] = {}
    for row in constraints:
        definitions.setdefault(row["relname"], []).append(row["definition"])
    if {k: sorted(v) for k, v in definitions.items()} != {
        k: sorted(v) for k, v in _CONSTRAINTS.items()
    } or not all(r["valid"] for r in constraints):
        raise ObservationBlocked("inference_schema_refused")
    if (
        await conn.fetchval(
            """SELECT count(*)=18 AND bool_and(COALESCE(p.prosecdef AND p.prokind='f'
          AND 'search_path=pg_catalog'=ANY(p.proconfig),false))
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND (p.proname LIKE 'compute_metadata_%'
          OR p.proname LIKE 'attempt_lifecycle_%' OR p.proname LIKE 'task_inference_%')"""
        )
        is not True
    ):
        raise ObservationBlocked("inference_schema_refused")
    if (
        await conn.fetchval(
            """SELECT NOT EXISTS(
          SELECT 1 FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,
            pg_catalog.acldefault('r',c.relowner))) x
          WHERE n.nspname='public' AND c.relname=ANY($1::text[]) AND x.grantee<>c.relowner
        ) AND NOT EXISTS(
          SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
          WHERE n.nspname='public' AND c.relname=ANY($1::text[]) AND x.grantee<>c.relowner
          AND NOT(c.relname='task_inference_recovery' AND x.grantee=$2::regrole
            AND x.privilege_type='INSERT' AND NOT x.is_grantable
            AND a.attname IN ('task_id','account_id','account_generation','protocol_version'))
        ) AND NOT EXISTS(
          SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,
            pg_catalog.acldefault('f',p.proowner))) x
          WHERE n.nspname='public' AND (p.proname LIKE 'compute_metadata_%'
            OR p.proname LIKE 'attempt_lifecycle_%' OR p.proname LIKE 'task_inference_%')
          AND x.grantee<>p.proowner AND NOT(x.grantee=$3::regrole AND NOT x.is_grantable
            AND x.privilege_type='EXECUTE' AND NOT(p.proname=ANY($4::text[])))
        )""",
            list(_TABLES),
            acceptor,
            worker,
            list(_HELPERS),
        )
        is not True
    ):
        raise ObservationBlocked("inference_acl_refused")
    if (
        await conn.fetchval(
            """SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_default_acl d
        CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) x
        WHERE d.defaclobjtype IN ('r','f') AND (d.defaclnamespace=0 OR
          d.defaclnamespace='public'::regnamespace) AND x.grantee<>d.defaclrole
        AND NOT(d.defaclobjtype='f' AND x.grantee=0 AND x.privilege_type='EXECUTE'
          AND NOT x.is_grantable))"""
        )
        is not True
    ):
        raise ObservationBlocked("inference_acl_refused")
    if (
        await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t
        WHERE t.tgrelid='public.task_inference_recovery'::regclass
          AND t.tgfoid='public.task_inference_recovery_immutable()'::regprocedure
          AND t.tgname='task_inference_recovery_immutable' AND t.tgenabled='O'
          AND t.tgtype=19 AND NOT t.tgisinternal)"""
        )
        is not True
    ):
        raise ObservationBlocked("inference_schema_refused")


async def _connection_identity(conn: Any) -> tuple[Any, ...]:
    row = await conn.fetchrow(
        "SELECT CURRENT_USER AS principal, pg_catalog.current_database() AS db, "
        "(SELECT oid FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()) AS oid, "
        "pg_catalog.pg_postmaster_start_time() AS started, "
        "pg_catalog.inet_server_addr() AS address, pg_catalog.inet_server_port() AS port"
    )
    if row is None:
        raise ObservationBlocked("inference_database_refused")
    return tuple(row.values())


@dataclass(frozen=True, slots=True)
class WorkerComposition:
    """Worker binding carries no acceptor pool or store."""

    tasks: TaskStore
    recovery: InferenceRecovery
    pool: asyncpg.Pool
    memory: MemoryStore
    encryption: ContentEncryption
    settings: Settings
    worker_role: str
    acceptor_role: str
    _admission: object

    def validate(self) -> None:
        if type(self.tasks) is not TaskStore or type(self.recovery) is not InferenceRecovery:
            raise ObservationBlocked("inference_composition_refused")
        w, r = self.tasks, self.recovery
        if (
            self._admission is not _ADMITTED
            or w._pool is not self.pool
            or w._memory is not self.memory
            or w._enc is not self.encryption
            or w._memory._pool is not w._pool
            or w._memory.encryption is not w._enc
            or w._composition is not self
            or w._cover_new_inference is not False
            or w._inference is not r
            or r.pool is not w._pool
            or r.principals != (self.worker_role, self.acceptor_role)
            or getattr(w._memory, "_task_composition", None) is not self
        ):
            raise ObservationBlocked("inference_composition_refused")

    def validate_worker(self, ctx: dict[str, Any]) -> None:
        self.validate()
        if (
            ctx.get("task_composition") is not self
            or ctx.get("settings") is not self.settings
            or ctx.get("db_pool") is not self.tasks._pool
            or ctx.get("store") is not self.tasks._memory
            or ctx.get("task_store") is not self.tasks
        ):
            raise ObservationBlocked("inference_composition_refused")


@dataclass(frozen=True, slots=True)
class TaskComposition:
    acceptor: TaskStore
    acceptor_pool: asyncpg.Pool
    acceptor_memory: MemoryStore
    worker: WorkerComposition
    _admission: object

    @property
    def tasks(self) -> TaskStore:
        return self.worker.tasks

    @property
    def recovery(self) -> InferenceRecovery:
        return self.worker.recovery

    @property
    def acceptor_role(self) -> str:
        return self.worker.acceptor_role

    @property
    def worker_role(self) -> str:
        return self.worker.worker_role

    def validate(self) -> None:
        if type(self.worker) is not WorkerComposition or type(self.acceptor) is not TaskStore:
            raise ObservationBlocked("inference_composition_refused")
        self.worker.validate()
        a, w = self.acceptor, self.tasks
        if (
            self._admission is not _ADMITTED
            or a._pool is not self.acceptor_pool
            or a._memory is not self.acceptor_memory
            or a._pool is w._pool
            or a._enc is not w._enc
            or a._memory._pool is not a._pool
            or a._memory.encryption is not a._enc
            or a._composition is not self
            or a._cover_new_inference is not True
            or a._inference is not None
            or a._memory._task_composition is not self
        ):
            raise ObservationBlocked("inference_composition_refused")

    async def preflight(self) -> None:
        """Fresh pair observation, never a permit for a later transaction."""
        require_effect()
        self.validate()
        # The acceptance switch is not a kill switch for accepted work, owner
        # control/observation or independent original-hold accounting.
        if not self.worker.settings.durable_chat_enabled:
            raise ObservationBlocked("inference_composition_refused")
        async with asyncio.timeout(2):
            async with (
                pool_connection(self.acceptor._pool) as a,
                pool_connection(self.tasks._pool) as w,
            ):
                await acceptor_preflight(a, expected=self.acceptor_role, other=self.worker_role)
                await self.recovery.preflight(w)

    def validate_http(self, state: Any) -> None:
        self.validate()
        if (
            state.task_composition is not self
            or state.settings is not self.worker.settings
            or state.db_pool is not self.tasks._pool
            or state.memory_store is not self.tasks._memory
        ):
            raise ObservationBlocked("inference_composition_refused")


def validate_worker_composition(ctx: dict[str, Any]) -> None:
    """Detect missing as well as crossed collaborators before runner effects."""
    store = ctx.get("task_store")
    bound = getattr(store, "_composition", None)
    memory = getattr(ctx.get("store"), "_task_composition", None)
    selected = ctx.get("task_composition")
    bundle = selected if selected is not None else bound if bound is not None else memory
    if bundle is not None:
        if type(bundle) is not WorkerComposition:
            raise ObservationBlocked("inference_composition_refused")
        bundle.validate_worker(ctx)
        if invocation.get() is not None:
            raise ObservationBlocked("inherited_inference_invocation")


async def qualify_task_composition(
    *,
    enabled: bool = False,
    acceptor_pool: asyncpg.Pool | None = None,
    worker_pool: asyncpg.Pool | None = None,
    encryption: ContentEncryption | None = None,
    settings: Settings | None = None,
    acceptor_role: str | None = None,
    worker_role: str | None = None,
) -> TaskComposition | None:
    """No default I/O; explicit qualification borrows but never owns the pools."""
    if enabled is False and all(
        value is None
        for value in (acceptor_pool, worker_pool, encryption, settings, acceptor_role, worker_role)
    ):
        return None
    if (
        enabled is not True
        or not isinstance(acceptor_pool, asyncpg.Pool)
        or not isinstance(worker_pool, asyncpg.Pool)
        or acceptor_pool is worker_pool
        or not isinstance(encryption, ContentEncryption)
        or not isinstance(settings, Settings)
        or ((acceptor_role is None) != (worker_role is None))
    ):
        raise ObservationBlocked("inference_composition_refused")
    if invocation.get() is not None:
        raise ObservationBlocked("inherited_inference_invocation")
    recovery = InferenceRecovery(worker_pool)
    # Validate the ENTIRE pair before either J1 barrier may initialize a binding.
    async with asyncio.timeout(2):
        async with acceptor_pool.acquire() as a, worker_pool.acquire() as w:
            ai, wi = await _connection_identity(a), await _connection_identity(w)
            if ai[1:] != wi[1:] or ai[0] == wi[0]:
                raise ObservationBlocked("inference_database_refused")
            if acceptor_role is not None and (ai[0] != acceptor_role or wi[0] != worker_role):
                raise ObservationBlocked("inference_principal_refused")
            await acceptor_preflight(a, expected=ai[0], other=wi[0])
            recovery.principals = (wi[0], ai[0])
            await recovery.preflight(w)
            await _schema(a, ai[0], wi[0], is_worker=False)
            await _schema(w, ai[0], wi[0], is_worker=True)
    await require_recovery_barrier(acceptor_pool, settings)
    if configured_journal(settings) is None:
        await require_recovery_barrier(worker_pool, settings)
    else:
        await require_recovery_barrier(worker_pool, settings, initialize_binding=False)
    acceptor = TaskStore(
        acceptor_pool, encryption, MemoryStore(acceptor_pool, encryption), cover_new_inference=True
    )
    tasks = TaskStore(
        worker_pool,
        encryption,
        MemoryStore(worker_pool, encryption),
        inference_recovery=recovery,
    )
    worker = WorkerComposition(
        tasks, recovery, worker_pool, tasks._memory, encryption, settings, wi[0], ai[0], _ADMITTED
    )
    bundle = TaskComposition(acceptor, acceptor_pool, acceptor._memory, worker, _ADMITTED)
    acceptor._composition = bundle
    tasks._composition = worker
    acceptor._memory._task_composition = bundle
    tasks._memory._task_composition = worker
    bundle.validate()
    return bundle


async def qualify_worker_composition(
    *,
    enabled: bool = False,
    worker_pool: asyncpg.Pool | None = None,
    encryption: ContentEncryption | None = None,
    settings: Settings | None = None,
    acceptor_role: str | None = None,
    worker_role: str | None = None,
) -> WorkerComposition | None:
    """Qualify a separate W-only host against an already bound journal.

    A catalog observation is not an authenticated A session or a stored ready bit.
    Backend acceptance retains actual-pair qualification and per-call A checks.
    """
    if enabled is False and all(
        value is None for value in (worker_pool, encryption, settings, acceptor_role, worker_role)
    ):
        return None
    if (
        enabled is not True
        or not isinstance(worker_pool, asyncpg.Pool)
        or not isinstance(encryption, ContentEncryption)
        or not isinstance(settings, Settings)
        or not acceptor_role
        or not worker_role
        or acceptor_role == worker_role
        or settings.daemon_task_acceptor_database_url
        or settings.daemon_task_acceptor_role != acceptor_role
        or settings.daemon_task_worker_role != worker_role
    ):
        raise ObservationBlocked("inference_composition_refused")
    if configured_journal(settings) is None:
        raise ObservationBlocked("inference_journal_required")
    if invocation.get() is not None:
        raise ObservationBlocked("inherited_inference_invocation")
    recovery = InferenceRecovery(worker_pool)
    recovery.principals = (worker_role, acceptor_role)
    async with asyncio.timeout(2):
        async with worker_pool.acquire() as w:
            wi = await _connection_identity(w)
            if wi[0] != worker_role:
                raise ObservationBlocked("inference_principal_refused")
            await acceptor_catalog_preflight(w, acceptor_role, worker_role)
            await recovery.preflight(w)
            await _schema(w, acceptor_role, worker_role, is_worker=False)
            await _schema(w, acceptor_role, worker_role, is_worker=True)
    # The worker cannot initialize a missing binding, even with otherwise safe ACLs.
    await require_recovery_barrier(worker_pool, settings, initialize_binding=False)
    tasks = TaskStore(
        worker_pool,
        encryption,
        MemoryStore(worker_pool, encryption),
        inference_recovery=recovery,
    )
    worker = WorkerComposition(
        tasks,
        recovery,
        worker_pool,
        tasks._memory,
        encryption,
        settings,
        worker_role,
        acceptor_role,
        _ADMITTED,
    )
    tasks._composition = worker
    tasks._memory._task_composition = worker
    worker.validate()
    return worker
