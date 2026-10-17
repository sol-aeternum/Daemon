"""Uncalled storage-only metadata extension; no execution or financial authority.

An existing pool and trusted backend identities are required. Each call owns one
READ COMMITTED transaction, including caller-side eligibility preflight. No retry,
pool creation, settings lookup or I/O happens at import/construction.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID, uuid4

import asyncpg

LOCK_MS = 250
STATEMENT_MS = 1000
ACQUIRE_S = 0.5
OPERATION_S = 2.0
RELEASE_S = 0.5

# Qualified V7 predicate. Evaluate on the CALLER connection, never in a definer.
# Exact signatures deliberately fail closed when schema/function prerequisites
# are absent. MEMBER is conservative even without immediate privilege inheritance.
ROLE_PRECONDITION = """SELECT
 NOT EXISTS (
   SELECT 1 FROM pg_catalog.pg_roles r
   WHERE (r.rolsuper OR r.rolcreaterole OR r.rolbypassrls OR r.rolname IN
     ('pg_execute_server_program','pg_read_server_files','pg_write_server_files'))
     AND pg_catalog.pg_has_role(CURRENT_USER,r.oid,'MEMBER')
 )
 AND pg_catalog.has_schema_privilege(CURRENT_USER,'public','USAGE')
 AND NOT EXISTS (
   SELECT 1 FROM pg_catalog.pg_namespace n
   WHERE n.nspname='public'
     AND pg_catalog.pg_has_role(CURRENT_USER,n.nspowner,'MEMBER')
 )
 AND NOT EXISTS (
   SELECT 1 FROM pg_catalog.pg_proc p
   WHERE p.oid IN (
     'public.compute_metadata_check_account(uuid)'::regprocedure,
     'public.compute_metadata_create_scope(uuid,uuid)'::regprocedure,
     'public.compute_metadata_attach(uuid,uuid,uuid,uuid,uuid)'::regprocedure,
     'public.compute_metadata_read_scope(uuid,uuid)'::regprocedure,
     'public.compute_metadata_read_binding(uuid,uuid)'::regprocedure
   ) AND pg_catalog.pg_has_role(CURRENT_USER,p.proowner,'MEMBER')
 )
 AND NOT EXISTS (
   SELECT 1 FROM pg_catalog.pg_class c
   WHERE c.oid IN ('public.compute_scope_metadata'::regclass,'public.compute_reservation_metadata'::regclass)
     AND (pg_catalog.pg_has_role((SELECT oid FROM pg_catalog.pg_roles WHERE rolname=CURRENT_USER),c.relowner,'MEMBER')
       OR pg_catalog.has_table_privilege(CURRENT_USER,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       OR pg_catalog.has_any_column_privilege(CURRENT_USER,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))
 )
 AND NOT pg_catalog.has_function_privilege(CURRENT_USER,'public.compute_metadata_check_account(uuid)','EXECUTE')
 AND pg_catalog.has_function_privilege(CURRENT_USER,'public.compute_metadata_create_scope(uuid,uuid)','EXECUTE')
 AND pg_catalog.has_function_privilege(CURRENT_USER,'public.compute_metadata_attach(uuid,uuid,uuid,uuid,uuid)','EXECUTE')
 AND pg_catalog.has_function_privilege(CURRENT_USER,'public.compute_metadata_read_scope(uuid,uuid)','EXECUTE')
 AND pg_catalog.has_function_privilege(CURRENT_USER,'public.compute_metadata_read_binding(uuid,uuid)','EXECUTE')
 AS storage_role_eligible;
"""

_Operation = Literal["create_scope", "attach", "read_scope", "read_binding"]
_STATEMENTS: dict[_Operation, str] = {
    "create_scope": "SELECT m.* FROM public.compute_metadata_create_scope($1::uuid,$2::uuid) AS m",
    "attach": "SELECT m.* FROM public.compute_metadata_attach($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid) AS m",
    "read_scope": "SELECT m.* FROM public.compute_metadata_read_scope($1::uuid,$2::uuid) AS m",
    "read_binding": "SELECT m.* FROM public.compute_metadata_read_binding($1::uuid,$2::uuid) AS m",
}


@dataclass(frozen=True)
class ScopeMetadataIdentity:
    account_id: UUID
    metadata_scope_id: UUID


@dataclass(frozen=True)
class ReservationMetadataIdentity:
    scope: ScopeMetadataIdentity
    reservation_id: UUID
    runtime_identity: UUID
    dispatch_nonce: UUID


@dataclass(frozen=True)
class ScopeMetadata:
    identity: ScopeMetadataIdentity
    created_at: datetime


@dataclass(frozen=True)
class ReservationMetadata:
    identity: ReservationMetadataIdentity
    created_at: datetime


MetadataIdentity = ScopeMetadataIdentity | ReservationMetadataIdentity


class MetadataError(Exception):
    """Uniform safe outcome; never forward server query/key/detail text."""

    def __init__(
        self,
        code: str,
        sqlstate: str | None = None,
        *,
        identity: MetadataIdentity | None = None,
        commit_acknowledged: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.sqlstate = sqlstate
        self.identity = identity
        self.commit_acknowledged = commit_acknowledged
        self.termination_failed = False


class ComputeMetadataIdentityConflict(MetadataError):
    pass


class ComputeMetadataAccountUnavailable(MetadataError):
    pass


class ComputeMetadataUnavailable(MetadataError):
    pass


class ComputeMetadataWriteUncertain(MetadataError):
    """Resolve the original identity authoritatively; never automatically retry."""


class ComputeMetadataCommittedResultUnavailable(MetadataError):
    """COMMIT acknowledged; decode/release failed. Read back, never retry write."""


def new_scope_metadata_identity(*, account_id: UUID) -> ScopeMetadataIdentity:
    return ScopeMetadataIdentity(account_id, uuid4())


def new_reservation_metadata_identity(
    *, scope: ScopeMetadataIdentity, reservation_id: UUID
) -> ReservationMetadataIdentity:
    return ReservationMetadataIdentity(scope, reservation_id, uuid4(), uuid4())


def _classify(exc: Exception, *, write: bool, phase: str) -> MetadataError:
    state = getattr(exc, "sqlstate", None) if isinstance(exc, asyncpg.PostgresError) else None
    if write and phase in ("decode", "release"):
        return ComputeMetadataCommittedResultUnavailable("committed_result_unavailable", state)
    if write and phase == "commit":
        return ComputeMetadataWriteUncertain("write_uncertain", state)
    if phase == "statement" and isinstance(exc, asyncpg.PostgresError):
        if state == "22P02" or (
            state == "P0001" and getattr(exc, "message", None) == "identity_conflict"
        ):
            return ComputeMetadataIdentityConflict("identity_conflict", state)
        if state == "P0001" and getattr(exc, "message", None) == "metadata_account_unavailable":
            return ComputeMetadataAccountUnavailable("metadata_account_unavailable", state)
        if state in ("40P01", "40001", "55P03", "57014", "42501") or (
            state == "P0001" and getattr(exc, "message", None) == "metadata_isolation_required"
        ):
            return ComputeMetadataUnavailable("server_refused", state)
    if write and phase == "statement":
        return ComputeMetadataWriteUncertain("write_uncertain", state)
    return ComputeMetadataUnavailable("unavailable", state)


def _decode(
    key: _Operation, args: tuple[UUID, ...], row: asyncpg.Record | None
) -> ScopeMetadata | ReservationMetadata | None:
    primary = "id" if key.endswith("scope") else "reservation_id"
    if row is None or row[primary] is None:
        if key in ("create_scope", "attach"):
            raise ValueError("missing_write_result")
        return None
    fields = (
        ("account_id", "id")
        if primary == "id"
        else ("metadata_scope_id", "reservation_id", "runtime_identity", "dispatch_nonce")
    )
    if any(not isinstance(row[name], UUID) for name in fields):
        raise ValueError("invalid_result_identity")
    created_at = row["created_at"]
    if not isinstance(created_at, datetime) or created_at.utcoffset() is None:
        raise ValueError("invalid_result_timestamp")
    if primary == "id":
        identity = ScopeMetadataIdentity(row["account_id"], row["id"])
        if (identity.account_id, identity.metadata_scope_id) != args:
            raise ValueError("mismatched_result_identity")
        return ScopeMetadata(identity, created_at)
    binding = ReservationMetadataIdentity(
        ScopeMetadataIdentity(args[0], row["metadata_scope_id"]),
        row["reservation_id"],
        row["runtime_identity"],
        row["dispatch_nonce"],
    )
    if binding.reservation_id != args[2 if key == "attach" else 1] or (
        key == "attach"
        and (binding.scope.metadata_scope_id, binding.runtime_identity, binding.dispatch_nonce)
        != (args[1], args[3], args[4])
    ):
        raise ValueError("mismatched_result_identity")
    return ReservationMetadata(binding, created_at)


class ComputeMetadataStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        # Local diagnostic receipts, no keys/details. terminate() is NOT rollback evidence.
        self.cleanup_events: list[dict[str, str]] = []

    async def _call(
        self, key: _Operation, args: tuple[UUID, ...], identity: MetadataIdentity | None = None
    ) -> ScopeMetadata | ReservationMetadata | None:
        if any(not isinstance(arg, UUID) for arg in args):
            raise ComputeMetadataIdentityConflict("identity_conflict", identity=identity)
        conn = None
        phase = "acquire"
        write = key in ("create_scope", "attach")
        try:
            # The deadline INCLUDES release; no extra cleanup grace or retry task.
            async with asyncio.timeout(OPERATION_S):
                conn = await self._pool.acquire(timeout=ACQUIRE_S)
                phase = "begin"
                tx = conn.transaction(isolation="read_committed")
                await tx.start()
                await conn.execute(
                    f"SET LOCAL lock_timeout='{LOCK_MS}ms'; "
                    f"SET LOCAL statement_timeout='{STATEMENT_MS}ms'"
                )
                if await conn.fetchval(ROLE_PRECONDITION) is not True:
                    raise ComputeMetadataUnavailable("role_precondition")
                phase = "statement"
                row = await conn.fetchrow(_STATEMENTS[key], *args)
                phase = "commit"
                await tx.commit()
                phase = "decode"
                result = _decode(key, args, row)
                phase = "release"
                # Native release shields reset. Relinquish ownership before entry:
                # terminating here could recycle a holder whose OLD reset still runs.
                release_conn = conn
                conn = None
                await self._pool.release(release_conn, timeout=RELEASE_S)
                return result
        except BaseException as exc:
            termination_failed = False
            if conn is not None:
                try:
                    conn.terminate()
                    self.cleanup_events.append({"phase": phase, "action": "terminate_called"})
                except Exception as cleanup:
                    termination_failed = True
                    self.cleanup_events.append(
                        {
                            "phase": phase,
                            "action": "terminate_failed",
                            "type": type(cleanup).__name__,
                        }
                    )
            acknowledged = write and phase in ("decode", "release")
            if isinstance(exc, asyncio.CancelledError):
                # Preserve the original cancellation object/type; attach phase evidence.
                exc.metadata_identity = identity  # type: ignore[attr-defined]
                exc.metadata_outcome = (  # type: ignore[attr-defined]
                    "committed"
                    if acknowledged
                    else "uncertain"
                    if write and phase in ("statement", "commit")
                    else "not_issued"
                )
                exc.metadata_termination_failed = termination_failed  # type: ignore[attr-defined]
                raise
            if not isinstance(exc, Exception):
                raise
            error = (
                exc if isinstance(exc, MetadataError) else _classify(exc, write=write, phase=phase)
            )
            error.identity = identity
            error.commit_acknowledged = acknowledged
            error.termination_failed = termination_failed
            raise error from None

    async def create_scope(self, identity: ScopeMetadataIdentity) -> ScopeMetadata:
        if not isinstance(identity, ScopeMetadataIdentity):
            raise ComputeMetadataIdentityConflict("identity_conflict", identity=identity)
        return cast(
            ScopeMetadata,
            await self._call(
                "create_scope", (identity.account_id, identity.metadata_scope_id), identity
            ),
        )

    async def attach_reservation(
        self, identity: ReservationMetadataIdentity
    ) -> ReservationMetadata:
        if not isinstance(identity, ReservationMetadataIdentity) or not isinstance(
            identity.scope, ScopeMetadataIdentity
        ):
            raise ComputeMetadataIdentityConflict("identity_conflict", identity=identity)
        return cast(
            ReservationMetadata,
            await self._call(
                "attach",
                (
                    identity.scope.account_id,
                    identity.scope.metadata_scope_id,
                    identity.reservation_id,
                    identity.runtime_identity,
                    identity.dispatch_nonce,
                ),
                identity,
            ),
        )

    async def read_scope(
        self, *, account_id: UUID, metadata_scope_id: UUID
    ) -> ScopeMetadata | None:
        return cast(
            ScopeMetadata | None, await self._call("read_scope", (account_id, metadata_scope_id))
        )

    async def read_binding(
        self, *, account_id: UUID, reservation_id: UUID
    ) -> ReservationMetadata | None:
        return cast(
            ReservationMetadata | None,
            await self._call("read_binding", (account_id, reservation_id)),
        )
