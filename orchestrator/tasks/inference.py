"""Fixture-injected inference quarantine. Not constructed by production startup.

Markers/readback are evidence, never reusable send authority. SQL authentication
of encrypted origin stays with TaskStore; these entrypoints trust a separated
service principal, not a hostile tasks-DML holder.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any
from uuid import UUID, uuid4

from orchestrator.attempt_lifecycle import AttemptIdentity, ROLE_PRECONDITION as LIFECYCLE_ROLE
from orchestrator.attempt_lifecycle import _decode as decode_lifecycle
from orchestrator.compute_metadata import ROLE_PRECONDITION as METADATA_ROLE
from orchestrator.services.account_control.transaction import (
    ObservationBlocked,
    control_transaction,
)
from orchestrator.tasks._invocation import InferenceInvocation, require_effect

CODE = "inference_dispatch_unresolved"
NOTICE = (
    "This response was interrupted. Daemon will not restart it automatically because "
    "the provider may still be working or charging. Any saved partial answer is incomplete."
)
NEW_REQUEST_WARNING = "Starting a new request may overlap with provider work or charges from the interrupted response."

_FUNCTIONS = (
    "task_inference_dispatch_begin(jsonb,uuid,uuid,uuid)",
    "task_inference_dispatch_readback(jsonb,uuid,uuid,uuid)",
    "task_inference_recovery_lock(uuid,bigint,uuid,bigint,uuid)",
    "attempt_lifecycle_abandoned_page(uuid)",
    "attempt_lifecycle_close_abandoned(jsonb,uuid)",
)
ROLE_PRECONDITION = (
    f"SELECT ({LIFECYCLE_ROLE}) AND ({METADATA_ROLE.strip().removesuffix(';')}) AND "
    "NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
    "WHERE c.oid='public.task_inference_recovery'::regclass AND "
    "(pg_catalog.pg_has_role(CURRENT_USER,c.relowner,'MEMBER') OR "
    "pg_catalog.has_table_privilege(CURRENT_USER,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR "
    "pg_catalog.has_any_column_privilege(CURRENT_USER,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AND "
    "NOT pg_catalog.has_function_privilege(CURRENT_USER,"
    "'public.task_inference_recovery_immutable()','EXECUTE') AND "
    "NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=ANY(ARRAY["
    + ",".join("'public." + f + "'::regprocedure" for f in _FUNCTIONS)
    + "]) AND pg_catalog.pg_has_role(CURRENT_USER,p.proowner,'MEMBER')) AND "
    + " AND ".join(
        "pg_catalog.has_function_privilege(CURRENT_USER,'public." + f + "','EXECUTE')"
        for f in _FUNCTIONS
    )
)


def decode_identity(value: Any) -> AttemptIdentity:
    if not isinstance(value, dict) or set(value) != set(AttemptIdentity.__dataclass_fields__):
        raise ObservationBlocked()
    try:
        result = AttemptIdentity(
            lifecycle_id=UUID(value["lifecycle_id"]),
            account_id=UUID(value["account_id"]),
            generation=value["generation"],
            task_id=UUID(value["task_id"]),
            epoch=value["epoch"],
            financial_scope_id=UUID(value["financial_scope_id"]),
            metadata_scope_id=UUID(value["metadata_scope_id"]),
            producer_instance_id=UUID(value["producer_instance_id"]),
        )
        if json.loads(result.wire()) != value:
            raise ValueError
        return result
    except (TypeError, ValueError) as exc:
        raise ObservationBlocked() from exc


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    possible: bool
    identity: AttemptIdentity | None
    closed: bool | None


class InferenceRecovery:
    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.after_id: UUID | None = None

    async def preflight(self, conn: Any) -> None:
        require_effect()
        if await conn.fetchval(ROLE_PRECONDITION) is not True:
            raise ObservationBlocked("inference_principal_refused")

    async def lock(
        self, conn: Any, account: UUID, generation: int, task: UUID, epoch: int
    ) -> RecoveryObservation:
        """Caller transaction, before task locks. No staged closure/authentication."""
        await self.preflight(conn)
        raw = await conn.fetchval(
            "SELECT public.task_inference_recovery_lock($1,$2,$3,$4,$5)",
            account,
            generation,
            task,
            epoch,
            uuid4(),
        )
        value = json.loads(raw)
        if (
            set(value) != {"may_have_dispatched", "identity", "closed"}
            or type(value["may_have_dispatched"]) is not bool
        ):
            raise ObservationBlocked()
        identity = None if value["identity"] is None else decode_identity(value["identity"])
        if identity is not None and (
            (identity.account_id, identity.generation, identity.task_id, identity.epoch)
            != (account, generation, task, epoch)
            or type(value["closed"]) is not bool
        ):
            raise ObservationBlocked()
        if identity is None and (value["closed"] is not None or value["may_have_dispatched"]):
            raise ObservationBlocked()
        return RecoveryObservation(value["may_have_dispatched"], identity, value["closed"])

    async def close(self, conn: Any, observation: RecoveryObservation, reason: str) -> None:
        """Only AFTER TaskStore's original-origin/current-authority checks."""
        require_effect()
        if observation.identity is not None and not observation.closed:
            await conn.fetchval(
                "SELECT public.attempt_lifecycle_close($1::jsonb,$2,$3)",
                observation.identity.wire(),
                uuid4(),
                reason,
            )

    async def _owned(
        self,
        sql: str,
        args: tuple[Any, ...],
        *,
        deadline: float,
        state: InferenceInvocation,
        identity: AttemptIdentity | None = None,
    ) -> Any:
        state.require_effect()
        try:
            async with asyncio.timeout_at(deadline):
                async with control_transaction(self.pool) as conn:
                    await self.preflight(conn)
                    await conn.execute(
                        "SET LOCAL lock_timeout='250ms'; SET LOCAL statement_timeout='1000ms'"
                    )
                    state.require_effect()
                    raw = await conn.fetchval(sql, *args)
                    result = json.loads(raw)
                    if identity is not None:
                        state.require_effect()
                        await conn.execute(
                            "SELECT public.attempt_lifecycle_assert_open($1::jsonb)",
                            identity.wire(),
                        )
                    state.require_effect()
                # control_transaction returns only after own ACK and native release.
                state.require_effect()
                return result
        except BaseException:
            # Even known refusal may not be relabelled into fresh admission here.
            state.block("inference_admission_unavailable")
            raise

    async def register(self, identity: AttemptIdentity, state: InferenceInvocation) -> None:
        command = uuid4()
        # Scope creation and 049 registration share this owned transaction. The
        # immutable financial scope was already recorded on the actual attempt.
        deadline = asyncio.get_running_loop().time() + 2.0
        state.require_effect()
        try:
            async with asyncio.timeout_at(deadline):
                async with control_transaction(self.pool) as conn:
                    await self.preflight(conn)
                    state.require_effect()
                    await conn.execute(
                        "SET LOCAL lock_timeout='250ms'; SET LOCAL statement_timeout='1000ms'"
                    )
                    # The admitted recovery entrypoint acquires account/lifecycle/
                    # task locks without exposing 049's denied helper functions.
                    observation = await self.lock(
                        conn,
                        identity.account_id,
                        identity.generation,
                        identity.task_id,
                        identity.epoch,
                    )
                    if observation.identity is not None or observation.possible:
                        raise ObservationBlocked()
                    state.require_effect()
                    await conn.fetchval(
                        "SELECT public.compute_metadata_create_scope($1,$2)",
                        identity.account_id,
                        identity.metadata_scope_id,
                    )
                    state.require_effect()
                    raw = await conn.fetchval(
                        "SELECT public.attempt_lifecycle_register($1::jsonb,$2)",
                        identity.wire(),
                        command,
                    )
                    result = decode_lifecycle(raw, identity, command)
                    if (
                        result["control"]["identity"] != json.loads(identity.wire())
                        or result["receipt"]["command_id"] != str(command)
                        or result["receipt"]["opcode"] != "register"
                    ):
                        raise ObservationBlocked()
                    state.require_effect()
                    await conn.execute(
                        "SELECT public.attempt_lifecycle_assert_open($1::jsonb)", identity.wire()
                    )
                state.require_effect()
        except BaseException as exc:
            state.block("inference_registration_unavailable")
            exc.inference_identity = identity  # type: ignore[attr-defined]
            exc.inference_command_id = command  # type: ignore[attr-defined]
            raise

    async def mark(
        self, identity: AttemptIdentity, reservation: UUID, state: InferenceInvocation
    ) -> None:
        """Fresh own committed binding only. No returned/cached bearer permit."""
        state.require_send()
        runtime, nonce = uuid4(), uuid4()
        try:
            result = await self._owned(
                "SELECT public.task_inference_dispatch_begin($1::jsonb,$2,$3,$4)",
                (identity.wire(), reservation, runtime, nonce),
                deadline=asyncio.get_running_loop().time() + 2.0,
                state=state,
                identity=identity,
            )
        except BaseException as exc:
            exc.inference_identity = identity  # type: ignore[attr-defined]
            exc.inference_reservation_id = reservation  # type: ignore[attr-defined]
            exc.inference_runtime_identity = runtime  # type: ignore[attr-defined]
            exc.inference_dispatch_nonce = nonce  # type: ignore[attr-defined]
            raise
        expected = {
            "identity": json.loads(identity.wire()),
            "reservation_id": str(reservation),
            "runtime_identity": str(runtime),
            "dispatch_nonce": str(nonce),
            "created": True,
        }
        if result != expected or result.get("created") is not True:
            state.block("inference_mark_result_unavailable")
            raise ObservationBlocked()

    async def abandoned_pass(self) -> tuple[int, bool]:
        """<=32 candidates, one absolute2s budget; uncertainty stops entire pass.

        No production caller. Cursor is local observation, never release authority.
        Return count and unresolved diagnostic; no content, settlement or deletion.
        """
        state = InferenceInvocation()
        deadline = asyncio.get_running_loop().time() + 2.0
        closed = 0
        try:
            page = await self._owned(
                "SELECT COALESCE(jsonb_agg(identity),'[]'::jsonb) FROM public.attempt_lifecycle_abandoned_page($1) AS identity",
                (self.after_id,),
                deadline=deadline,
                state=state,
            )
            if not isinstance(page, list) or len(page) > 32:
                raise ObservationBlocked()
            if not page:
                self.after_id = None
            for value in page:
                identity = decode_identity(value)
                result = await self._owned(
                    "SELECT public.attempt_lifecycle_close_abandoned($1::jsonb,$2)",
                    (identity.wire(), uuid4()),
                    deadline=deadline,
                    state=state,
                )
                if (
                    set(result) != {"identity", "closed", "changed"}
                    or result["identity"] != value
                    or type(result["closed"]) is not bool
                    or type(result["changed"]) is not bool
                    or (result["changed"] and not result["closed"])
                ):
                    raise ObservationBlocked()
                self.after_id = identity.lifecycle_id
                closed += int(result["changed"])
            return closed, False
        except Exception:
            return closed, True
