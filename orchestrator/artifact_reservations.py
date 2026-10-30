"""Uncalled R0 reservations: caller-owned transactions, never storage authority.

Trusted internal principal, not user authentication. Retain the exact command on
unknown COMMIT; stop mutations and reconcile using a fresh serialized readback.
No commit/rollback, retry, pool/settings creation, namespace or filesystem I/O.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import re
from typing import Any, Literal
from uuid import UUID

import asyncpg
from cryptography.fernet import Fernet, InvalidToken

from orchestrator.attempt_lifecycle import AttemptIdentity, Connection, LifecycleError

MAX_DESCRIPTOR_BYTES = 65536
MAX_DESCRIPTOR_TOKEN = 131072
_PREFIX = "public.artifact_output_"
SIGNATURES = (
    "job_arg(jsonb)",
    "intent_arg(jsonb)",
    "account_lock(uuid)",
    "readback(jsonb,jsonb)",
    "register(jsonb,jsonb)",
    "reserve(jsonb,jsonb,jsonb)",
)
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
    + ",".join("'" + _PREFIX + s + "'::regprocedure" for s in SIGNATURES)
    + """ ]) AND pg_catalog.pg_has_role(CURRENT_USER,p.proowner,'MEMBER'))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid IN
 ('public.artifact_output_jobs'::regclass,'public.artifact_output_intents'::regclass,
 'public.attempt_lifecycle_control'::regclass,'public.attempt_lifecycle_commands'::regclass)
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
        for i, s in enumerate(SIGNATURES)
    )
)


class ReservationError(ValueError):
    """Closed codes only. No exception contains a command, ciphertext or DB detail."""

    def __init__(self, code: str = "invalid_reservation") -> None:
        self.code = code
        super().__init__(code)


def _integer(value: object, maximum: int = 2**63 - 1, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ReservationError()


def _uuids(*values: object) -> None:
    # asyncpg returns its UUID subclass; strings still never count as typed IDs.
    if any(not isinstance(value, UUID) for value in values):
        raise ReservationError()


def _wire(value: dict[str, Any]) -> dict[str, Any]:
    return {k: str(v) if isinstance(v, UUID) else v for k, v in value.items()}


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


@dataclass(frozen=True, slots=True)
class JobBinding:
    job_id: UUID
    account_id: UUID
    account_generation: int
    task_id: UUID
    operation_id: UUID

    def wire(self) -> dict[str, Any]:
        _uuids(self.job_id, self.account_id, self.task_id, self.operation_id)
        _integer(self.account_generation)
        return _wire(asdict(self))


@dataclass(frozen=True, slots=True)
class IntentBinding:
    intent_id: UUID
    output_slot: int
    resource_id: UUID
    version_id: UUID
    lifecycle_id: UUID
    attempt_id: UUID
    attempt_epoch: int
    measured_bytes: int

    def wire(self, job: JobBinding) -> dict[str, Any]:
        _uuids(
            self.intent_id, self.resource_id, self.version_id, self.lifecycle_id, self.attempt_id
        )
        _integer(self.output_slot, 2**31 - 1)
        _integer(self.attempt_epoch, minimum=1)
        _integer(self.measured_bytes, 8388608)
        return (
            job.wire()
            | _wire(asdict(self))
            | {"representation": "generated_file", "mime": "text/csv"}
        )


@dataclass(frozen=True, slots=True, repr=False)
class IntentCommand:
    job: JobBinding
    binding: IntentBinding
    descriptor_ciphertext: str = field(repr=False)

    def wire(self) -> dict[str, Any]:
        token = self.descriptor_ciphertext
        if (
            type(token) is not str
            or not 1 <= len(token) <= MAX_DESCRIPTOR_TOKEN
            or not token.isascii()
        ):
            raise ReservationError()
        value = self.binding.wire(self.job)
        del value["operation_id"]
        return value | {"descriptor_ciphertext": token}


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReservationError()
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise ReservationError()


class DescriptorCodec:
    """Explicit Fernet; bounded closed metadata, no byte integrity/publication claim."""

    def __init__(self, cipher: Fernet):
        if not isinstance(cipher, Fernet):
            raise ReservationError()
        self._cipher = cipher

    @staticmethod
    def _descriptor(
        job: JobBinding, binding: IntentBinding, filename: str, sha256: str
    ) -> dict[str, Any]:
        # Reject giant input before UTF-8 encoding or JSON allocation.
        if type(filename) is not str or len(filename) > 1024:
            raise ReservationError()
        try:
            if len(filename.encode("utf-8")) > 1024:
                raise ReservationError()
        except UnicodeError:
            raise ReservationError() from None
        if type(sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ReservationError()
        return binding.wire(job) | {
            "schema_version": 1,
            "filename": filename,
            "sha256": sha256,
            "provenance": {
                "original_task_id": str(job.task_id),
                "original_attempt_id": str(binding.attempt_id),
                "kind": "deterministic_csv",
            },
        }

    def encode(
        self, job: JobBinding, binding: IntentBinding, *, filename: str, sha256: str
    ) -> IntentCommand:
        plaintext = _canonical(self._descriptor(job, binding, filename, sha256))
        if len(plaintext) > MAX_DESCRIPTOR_BYTES:
            raise ReservationError()
        token = self._cipher.encrypt(plaintext).decode("ascii")
        command = IntentCommand(job, binding, token)
        command.wire()
        return command

    def validate(self, command: IntentCommand) -> None:
        try:
            command.wire()
            token = command.descriptor_ciphertext.encode("ascii")
            raw = base64.b64decode(token, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw) != token:
                raise ReservationError()
            plaintext = self._cipher.decrypt(token)
            if len(plaintext) > MAX_DESCRIPTOR_BYTES:
                raise ReservationError()
            parsed = json.loads(plaintext, object_pairs_hook=_object, parse_constant=_constant)
            if type(parsed) is not dict:
                raise ReservationError()
            expected = self._descriptor(
                command.job, command.binding, parsed["filename"], parsed["sha256"]
            )
            # Exact bytes also reject bool/int aliases, duplicate/unknown keys,
            # noncanonical UUIDs, escaped scalar spelling and JSON number aliases.
            if _canonical(expected) != plaintext:
                raise ReservationError()
        except (ValueError, TypeError, KeyError, UnicodeError, InvalidToken, RecursionError):
            raise ReservationError() from None

    def decode(self, command: IntentCommand) -> dict[str, Any]:
        """Validated immutable descriptor for an independently authorised registry.

        Authentication of metadata is not publication, storage or account authority.
        """
        self.validate(command)
        return json.loads(self._cipher.decrypt(command.descriptor_ciphertext.encode("ascii")))


@dataclass(frozen=True, slots=True, repr=False)
class ReservationObservation:
    outcome: Literal["pending_commit", "present", "absent", "job_present_intent_absent"]
    job: dict[str, Any] | None = field(repr=False)
    intent: dict[str, Any] | None = field(repr=False)
    inserted: bool | None = None


def _decode(
    raw: Any, job: JobBinding, command: IntentCommand | None, *, write: bool
) -> ReservationObservation:
    try:
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        if type(value) is not dict or set(value) != (
            {"outcome", "job", "intent", "inserted"} if write else {"outcome", "job", "intent"}
        ):
            raise ValueError()
        for name, expected in (
            ("job", job.wire()),
            ("intent", command.wire() if command else None),
        ):
            row = value[name]
            if row is None:
                continue
            if (
                expected is None
                or type(row) is not dict
                or set(row) != set(expected) | {"created_at"}
            ):
                raise ValueError()
            # Canonical byte comparison avoids Python's bool/int equivalence.
            if _canonical({k: row[k] for k in expected}) != _canonical(expected):
                raise ValueError()
            if (
                type(row["created_at"]) is not str
                or datetime.fromisoformat(row["created_at"]).utcoffset() is None
            ):
                raise ValueError()
        outcome = value["outcome"]
        if write:
            if (
                outcome != "pending_commit"
                or type(value["inserted"]) is not bool
                or value["job"] is None
                or (command is not None and value["intent"] is None)
            ):
                raise ValueError()
        else:
            expected_outcome = (
                "absent"
                if value["job"] is None
                else "job_present_intent_absent"
                if command is not None and value["intent"] is None
                else "present"
            )
            if outcome != expected_outcome or (
                value["job"] is None and value["intent"] is not None
            ):
                raise ValueError()
        return ReservationObservation(outcome, value["job"], value["intent"], value.get("inserted"))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ReservationError("result_unavailable") from None


class OutputReservations:
    """No cached scope: every operation rechecks the supplied transaction and role."""

    def __init__(self, codec: DescriptorCodec):
        self._codec = codec

    async def _call(
        self,
        conn: Connection,
        op: str,
        job: JobBinding,
        command: IntentCommand | None = None,
        identity: AttemptIdentity | None = None,
    ) -> ReservationObservation:
        j = _canonical(job.wire()).decode("ascii")
        if command is not None:
            if command.job != job:
                raise ReservationError()
            self._codec.validate(command)
        c = _canonical(command.wire()).decode("ascii") if command else None
        if identity is not None:
            try:
                p = identity.wire()
            except (ValueError, LifecycleError):
                raise ReservationError() from None
            if (identity.account_id, identity.generation, identity.task_id) != (
                job.account_id,
                job.account_generation,
                job.task_id,
            ):
                raise ReservationError()
        else:
            p = None
        try:
            if not conn.is_in_transaction():
                raise ReservationError("transaction_required")
            if await conn.fetchval("SHOW transaction_isolation", timeout=2) != "read committed":
                raise ReservationError("unsupported_isolation")
            if await conn.fetchval(ROLE_PRECONDITION, timeout=2) is not True:
                raise ReservationError("unsafe_role")
            if op == "register":
                raw = await conn.fetchval(
                    "SELECT public.artifact_output_register($1::jsonb,$2::jsonb)", p, j, timeout=2
                )
            elif op == "reserve":
                raw = await conn.fetchval(
                    "SELECT public.artifact_output_reserve($1::jsonb,$2::jsonb,$3::jsonb)",
                    p,
                    j,
                    c,
                    timeout=2,
                )
            else:
                raw = await conn.fetchval(
                    "SELECT public.artifact_output_readback($1::jsonb,$2::jsonb)", j, c, timeout=2
                )
            return _decode(raw, job, command, write=op != "readback")
        except ReservationError:
            raise
        except asyncpg.PostgresError as exc:
            message = getattr(exc, "message", None)
            if not isinstance(message, str):
                message = ""
            code = {
                "reservation_conflict": "conflict",
                "reservation_unavailable": "unavailable",
                "reservation_refused": "refused",
                "lifecycle_refused": "refused",
            }.get(message, "unknown")
            raise ReservationError(code) from None
        except (OSError, TimeoutError, asyncpg.InterfaceError):
            raise ReservationError("unknown") from None

    async def register_output_job(
        self, conn: Connection, identity: AttemptIdentity, job: JobBinding
    ) -> ReservationObservation:
        return await self._call(conn, "register", job, identity=identity)

    async def reserve_output_intent(
        self, conn: Connection, identity: AttemptIdentity, command: IntentCommand
    ) -> ReservationObservation:
        return await self._call(conn, "reserve", command.job, command, identity)

    async def readback_output_reservation(
        self, conn: Connection, job: JobBinding, command: IntentCommand | None = None
    ) -> ReservationObservation:
        return await self._call(conn, "readback", job, command)
