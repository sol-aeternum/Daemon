"""Uncalled, trusted-principal attempt control; never an authenticated user API.

Receipts/readback are observations, not authority or restore/producer-stop proof.
Only assert_open composes with a caller-owned transaction. Reinvoke it after any
savepoint rollback and immediately before each cooperating same-connection write.
No pool/config creation, runtime imports, retries or I/O at import/construction.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any, Literal, TypeAlias
from uuid import UUID

import asyncpg
import asyncpg.pool

Connection: TypeAlias = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy

_PREFIX = "public.attempt_lifecycle_"
OPERATION_S = 2.0
_SIGNATURES = (
    "identity(jsonb)",
    "lock(jsonb)",
    "live(jsonb)",
    "register(jsonb,uuid)",
    "assert_open(jsonb)",
    "close(jsonb,uuid,text)",
    "readback(jsonb,uuid)",
)
# Same caller-side privilege model as compute metadata; never run in a definer.
ROLE_PRECONDITION = (
    """SELECT
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
 (r.rolsuper OR r.rolcreaterole OR r.rolbypassrls OR r.rolname IN
 ('pg_execute_server_program','pg_read_server_files','pg_write_server_files'))
 AND pg_catalog.pg_has_role(CURRENT_USER,r.oid,'MEMBER'))
 AND pg_catalog.has_schema_privilege(CURRENT_USER,'public','USAGE')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname='public'
 AND pg_catalog.pg_has_role(CURRENT_USER,n.nspowner,'MEMBER'))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=ANY(ARRAY["""
    + ",".join("'" + _PREFIX + s + "'::regprocedure" for s in _SIGNATURES)
    + """]) AND pg_catalog.pg_has_role(CURRENT_USER,p.proowner,'MEMBER'))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE
 c.oid IN ('public.attempt_lifecycle_control'::regclass,'public.attempt_lifecycle_commands'::regclass)
 AND (pg_catalog.pg_has_role(CURRENT_USER,c.relowner,'MEMBER')
 OR pg_catalog.has_table_privilege(CURRENT_USER,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 OR pg_catalog.has_any_column_privilege(CURRENT_USER,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
 AND """
    + " AND ".join(
        ("NOT " if i < 3 else "")
        + "pg_catalog.has_function_privilege(CURRENT_USER,'"
        + _PREFIX
        + s
        + "','EXECUTE')"
        for i, s in enumerate(_SIGNATURES)
    )
)


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    lifecycle_id: UUID
    account_id: UUID
    generation: int
    task_id: UUID
    epoch: int
    financial_scope_id: UUID
    metadata_scope_id: UUID
    producer_instance_id: UUID

    def wire(self) -> str:
        values = asdict(self)
        for name, value in values.items():
            if name in ("generation", "epoch"):
                if (
                    type(value) is not int
                    or not (0 if name == "generation" else 1) <= value <= 2**63 - 1
                ):
                    raise LifecycleError("identity_refused")
            elif not isinstance(value, UUID):
                raise LifecycleError("identity_refused")
        return json.dumps(values, default=str, sort_keys=True)


class LifecycleError(Exception):
    """Safe code only; original command/identity retained for authoritative readback."""

    def __init__(
        self,
        code: str,
        *,
        identity: AttemptIdentity | None = None,
        command_id: UUID | None = None,
        sqlstate: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code, self.identity, self.command_id, self.sqlstate = (
            code,
            identity,
            command_id,
            sqlstate,
        )
        self.commit_acknowledged = False
        self.termination_failed = False


def _decode(raw: Any, identity: AttemptIdentity, command: UUID) -> dict[str, Any]:
    result = json.loads(raw)
    if set(result) != {"control", "receipt", "account_present", "references_current"}:
        raise ValueError("invalid_result")
    if any(type(result[n]) is not bool for n in ("account_present", "references_current")):
        raise ValueError("invalid_result")
    control, receipt = result["control"], result["receipt"]

    def timestamp(value: Any) -> None:
        if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("invalid_timestamp")

    if control is not None and (
        control["identity"] != json.loads(identity.wire())
        or any(
            control[name] != str(getattr(identity, name))
            for name in ("account_id", "task_id", "financial_scope_id", "metadata_scope_id")
        )
        or control["id"] != str(identity.lifecycle_id)
        or type(control["epoch"]) is not int
        or control["epoch"] != identity.epoch
        or type(control["revision"]) is not int
        or control["revision"] not in (1, 2)
        or (control["closed_at"] is None) != (control["revision"] == 1)
    ):
        raise ValueError("invalid_control")
    if control is not None:
        for name in ("attempt_started_at", "created_at"):
            timestamp(control[name])
        if control["closed_at"] is not None:
            timestamp(control["closed_at"])
    if receipt is not None and (
        control is None
        or type(receipt["revision"]) is not int
        or receipt["revision"] > control["revision"]
        or (receipt["opcode"], receipt["reason"], receipt["revision"])
        not in (
            ("register", "", 1),
            ("close", "requested", 2),
            ("close", "cancelled", 2),
            ("close", "stale", 2),
        )
        or receipt["command_id"] != str(command)
        or receipt["lifecycle_id"] != str(identity.lifecycle_id)
        or receipt["opcode"] not in ("register", "close")
    ):
        raise ValueError("invalid_receipt")
    if receipt is not None:
        timestamp(receipt["committed_at"])
    return result


async def assert_open(conn: Connection, identity: AttemptIdentity) -> None:
    """No commit/token; caller owns transaction, rollback and same-connection effect.

    No remembered Python scope: every invocation reacquires and revalidates. SQL
    function lock_timeout is scoped by PostgreSQL and cannot leak caller settings.
    Caller must configure a bounded statement/transaction lifetime; no network I/O
    or unbounded consumer work while locks are held. Raw autocommit calls confer
    no authority to any later statement.
    """
    wire = identity.wire()
    if not conn.is_in_transaction():
        raise LifecycleError("transaction_required", identity=identity)
    try:
        async with asyncio.timeout(OPERATION_S):
            if await conn.fetchval(ROLE_PRECONDITION) is not True:
                raise LifecycleError("role_precondition", identity=identity)
            await conn.execute("SELECT public.attempt_lifecycle_assert_open($1::jsonb)", wire)
    except asyncpg.PostgresError as exc:
        raise LifecycleError(
            "server_refused", identity=identity, sqlstate=getattr(exc, "sqlstate", None)
        ) from None
    except TimeoutError:
        raise LifecycleError("unavailable", identity=identity) from None


class AttemptLifecycleStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def _call(
        self,
        op: Literal["register", "close", "readback"],
        identity: AttemptIdentity,
        command_id: UUID,
        reason: str = "",
    ) -> dict[str, Any]:
        wire = identity.wire()
        if not isinstance(command_id, UUID) or (
            op == "close" and reason not in ("requested", "cancelled", "stale")
        ):
            raise LifecycleError("identity_refused", identity=identity, command_id=command_id)
        conn = None
        phase = "acquire"
        write = op != "readback"
        try:
            async with asyncio.timeout(OPERATION_S):
                conn = await self._pool.acquire(timeout=0.5)
                phase = "begin"
                tx = conn.transaction(isolation="read_committed")
                await tx.start()
                await conn.execute(
                    "SET LOCAL lock_timeout='250ms'; SET LOCAL statement_timeout='1000ms'"
                )
                if await conn.fetchval(ROLE_PRECONDITION) is not True:
                    raise LifecycleError("role_precondition")
                phase = "statement"
                args = (wire, command_id, reason) if op == "close" else (wire, command_id)
                params = "$1::jsonb,$2::uuid,$3::text" if op == "close" else "$1::jsonb,$2::uuid"
                raw = await conn.fetchval(f"SELECT {_PREFIX}{op}({params})", *args)
                phase = "commit"
                await tx.commit()
                phase = "decode"
                result = _decode(raw, identity, command_id)
                if write and result["receipt"] is None:
                    raise ValueError("missing_receipt")
                if write and (
                    result["receipt"]["opcode"] != op or result["receipt"]["reason"] != reason
                ):
                    raise ValueError("mismatched_receipt")
                phase = "release"
                await self._pool.release(conn, timeout=0.5)
                conn = None
                return result
        except BaseException as exc:
            termination_failed = False
            if conn is not None:
                try:
                    conn.terminate()
                except Exception:
                    termination_failed = True  # Never rollback or remote-stop proof.
            ack = write and phase in ("decode", "release")
            if isinstance(exc, asyncio.CancelledError):
                exc.lifecycle_identity = identity  # type: ignore[attr-defined]
                exc.lifecycle_command_id = command_id  # type: ignore[attr-defined]
                exc.lifecycle_outcome = (  # type: ignore[attr-defined]
                    "committed"
                    if ack
                    else "uncertain"
                    if write and phase in ("statement", "commit")
                    else "not_issued"
                )
                exc.lifecycle_termination_failed = termination_failed  # type: ignore[attr-defined]
                raise
            if not isinstance(exc, Exception):
                raise
            state = (
                getattr(exc, "sqlstate", None) if isinstance(exc, asyncpg.PostgresError) else None
            )
            uncertain = (
                not isinstance(exc, asyncpg.PostgresError)
                or state in ("40003", "08007")
                or (state is not None and state.startswith("08"))
            )
            code = (
                "committed_result_unavailable"
                if ack
                else "write_uncertain"
                if write and phase in ("statement", "commit") and uncertain
                else "server_refused"
                if isinstance(exc, asyncpg.PostgresError)
                else "unavailable"
            )
            error = exc if isinstance(exc, LifecycleError) else LifecycleError(code, sqlstate=state)
            error.identity, error.command_id, error.commit_acknowledged = identity, command_id, ack
            error.termination_failed = termination_failed
            raise error from None

    async def register(self, identity: AttemptIdentity, command_id: UUID) -> dict[str, Any]:
        return await self._call("register", identity, command_id)

    async def close(
        self,
        identity: AttemptIdentity,
        command_id: UUID,
        reason: Literal["requested", "cancelled", "stale"] = "requested",
    ) -> dict[str, Any]:
        return await self._call("close", identity, command_id, reason)

    async def readback(self, identity: AttemptIdentity, command_id: UUID) -> dict[str, Any]:
        return await self._call("readback", identity, command_id)
