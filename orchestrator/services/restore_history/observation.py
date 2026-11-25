"""Qualified historical reads, never acceptance acknowledgement or send authority.

Explicitly supplied to the by-key HTTP boundary; no initializer or startup host.
Ownership is H -> auth -> account -> task. Actual SQL and filesystem cleanup
settles before returning even after cancellation or a logical deadline.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import asyncpg

from orchestrator.auth import AuthenticatedDevice
from orchestrator.auth_runtime_state import lock_auth_runtime_state
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    WriteGuardRefusal,
    guard_content_write,
)
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.tasks.store import TaskSnapshot, TaskStore
from orchestrator.tasks.inputs import _INBOX_PILOT_PURPOSE, _is_inbox_pilot_request, canonical_json

from .coordination import HistoryCoordinator, HistoryOperation
from .effects_projection import HistoryProjection
from .model import (
    MAX_RECORDS,
    TERMINAL_KINDS,
    AcceptPrepare,
    HistoryError,
    HistoryState,
    NonsuccessOutputPrepare,
    SendPossible,
    commitments,
)
from .projection import compare_projections
from .store import _check_time
from .terminal_finish import finish_matches, returned_finish
from .terminal_projection import TERMINAL_PROFILES


@dataclass(frozen=True, slots=True)
class HistoricalObservation:
    kind: Literal["task", "unknown", "recovery", "unavailable", "denied"]
    snapshot: TaskSnapshot | None = None
    task_id: UUID | None = None


class HistoryObservation:
    """Borrow the actual W store, opened H owner, profile and backend-only K1.

    Constructor is inert. The supplied AuthenticatedDevice is trusted request
    context, not bearer proof; only the authenticated route may supply it in a
    runtime. Each read revalidates its actual device/session on the W connection.
    No binding, grant, mutation, dispatch receipt or recovery disposition is made.
    """

    def __init__(
        self,
        tasks: TaskStore,
        history: HistoryCoordinator,
        projection: HistoryProjection,
        key: bytes,
    ) -> None:
        self._tasks, self._history, self._projection, self._key = tasks, history, projection, key
        self._validate()

    def _validate(self) -> None:
        tasks, history, projection = self._tasks, self._history, self._projection
        if (
            type(tasks) is not TaskStore
            or type(history) is not HistoryCoordinator
            or type(projection) not in (HistoryProjection, *TERMINAL_PROFILES)
            or projection.profile != TERMINAL_PROFILES.get(type(projection), "h1")
            or history.profile != projection.profile
            or type(tasks._pool) is not asyncpg.Pool
            or tasks._pool is not tasks._memory._pool
            or history._expected != projection.expected
        ):
            raise HistoryError("observation_composition_refused")
        projection.expected.verify_key(self._key)

    async def _parity_owned(
        self, operation: HistoryOperation, conn: Any, xid: int, state: HistoryState, deadline: float
    ) -> bool:
        """Private same-owner/connection/transaction join, never an observation permit."""
        await self._check_owned(operation, conn, xid, deadline)
        await self._projection.qualify(conn, writing=False, deadline=deadline)
        if await conn.fetchval("SELECT CURRENT_USER::text") != self._projection.worker:
            raise HistoryError("observation_principal_refused")
        facts = []
        cursor = 0
        while True:
            page = await self._projection.page(conn, after_seq=cursor, deadline=deadline)
            facts.extend(page)
            if len(facts) > MAX_RECORDS:
                raise HistoryError("observation_unavailable")
            if len(page) < 32:
                break
            cursor = page[-1].seq
        report = compare_projections(
            self._projection.expected.history_id,
            state,
            tuple(facts),
            profile=self._projection.profile,
        )
        await self._check_owned(operation, conn, xid, deadline)
        return not (report.missing or report.extra or report.mismatched or report.restore_required)

    async def _check_owned(
        self, operation: HistoryOperation, conn: Any, xid: int, deadline: float
    ) -> None:
        self._validate()
        if type(operation) is not HistoryOperation or operation._coordinator is not self._history:
            raise HistoryError("observation_owner_refused")
        operation._check()
        if (
            not conn.is_in_transaction()
            or getattr(getattr(conn, "_holder", None), "_pool", None) is not self._tasks._pool
            or type(xid) is not int
            or await conn.fetchval("SELECT txid_current()") != xid
        ):
            raise HistoryError("observation_transaction_owner_refused")
        _check_time(deadline)

    async def _task_owned(
        self,
        operation: HistoryOperation,
        conn: Any,
        xid: int,
        auth: AuthenticatedDevice,
        accepted: AcceptPrepare,
        client_key: str | None,
        state: HistoryState,
        deadline: float,
    ) -> dict[str, Any] | None:
        # Caller retains the original-generation guard and any conversation lock.
        await self._check_owned(operation, conn, xid, deadline)
        terminal_profile = type(self._projection) in TERMINAL_PROFILES
        native_columns = (
            "m.status AS result_status,m.metadata AS observed_metadata,"
            "m.tool_calls,m.tool_results,m.advisor_traces,m.reasoning_text,"
            "m.reasoning_duration_secs,m.model,m.reasoning_model,m.tokens_in,m.tokens_out,"
            if terminal_profile
            else ""
        )
        row = await conn.fetchrow(
            "SELECT t.*,m.content AS result_content,"
            + native_columns
            + "(SELECT count(*) FROM public.task_attempts a WHERE a.task_id=t.id "
            "AND a.epoch<t.lease_epoch AND a.execution_started_at IS NOT NULL "
            "AND a.outcome IN ('lost','failed_retryable')) AS regeneration_count "
            "FROM public.tasks t JOIN public.messages m ON m.id=t.result_message_id "
            "WHERE t.id=$1 AND t.user_id=$2 AND m.user_id=t.user_id "
            "AND m.conversation_id=t.conversation_id AND m.role='assistant' FOR SHARE OF t,m",
            accepted.task_id,
            auth.user_id,
        )
        if row is None or (
            row["conversation_id"],
            row["user_message_id"],
            row["result_message_id"],
        ) != (accepted.conversation_id, accepted.user_message_id, accepted.result_message_id):
            return None
        if self._tasks._origin(row).generation != accepted.account_generation:
            return None
        canonical = self._tasks._open(row["input_ciphertext"]).get("_request")
        key = row["idempotency_key"]
        if (
            type(key) is not str
            or (client_key is not None and key != client_key)
            or type(canonical) is not str
        ):
            return None
        lookup, request = commitments(self._key, auth.user_id, key, canonical.encode("utf-8"))
        if not hmac.compare_digest(lookup, accepted.key_commitment) or not hmac.compare_digest(
            request, accepted.request_commitment
        ):
            return None
        result = dict(row)
        if terminal_profile:
            try:
                await self._terminal_owned(conn, accepted, result, state)
            except ValueError:
                return None
        await self._check_owned(operation, conn, xid, deadline)
        return result

    async def _terminal_owned(
        self, conn: Any, accepted: AcceptPrepare, row: dict[str, Any], state: HistoryState
    ) -> None:
        """Parity alone cannot prove native coherence or expose a finish envelope."""
        fact = next(
            (
                e.command.data
                for e in state.entries
                if isinstance(e.command.data, TERMINAL_KINDS)
                and e.command.data.task_id == accepted.task_id
            ),
            None,
        )
        status = row["status"]
        v4 = self._projection.profile == "terminal-v4"
        if fact is None:
            if status == "completed" or (v4 and status in ("failed", "needs_attention")):
                raise ValueError("missing terminal fact")
            if v4 and status in ("queued", "running") and row["result_status"] != "streaming":
                raise ValueError("inconsistent active result")
            if (v4 or status not in ("queued", "running")) and (
                "turn_finish" in self._object(row["observed_metadata"])
            ):
                raise ValueError("unsupported unprojected finish")
            return
        ancestor = next(e.command.record_id for e in state.entries if e.command.data is accepted)
        failure = isinstance(fact, NonsuccessOutputPrepare)
        expected_status, reason = (fact.status, fact.reason) if failure else ("completed", None)
        if (
            (
                fact.account_id,
                fact.account_generation,
                fact.result_message_id,
                fact.acceptance_record_id,
            )
            != (
                accepted.account_id,
                accepted.account_generation,
                accepted.result_message_id,
                ancestor,
            )
            or fact.epoch != row["lease_epoch"]
            or (status, row["terminal_code"]) != (expected_status, reason)
            or row["result_status"] != ("error" if failure else "complete")
            or row["finished_at"] is None
            or row["lease_owner"] is not None
            or row["lease_expires_at"] is not None
            or row["cancel_requested_at"] is not None
        ):
            raise ValueError("terminal fact mismatch")
        metadata = self._object(row["observed_metadata"])
        if not failure and "turn_finish" not in metadata:
            if metadata.get("terminal_status") != "complete":
                raise ValueError("legacy terminal mismatch")
            return
        if not v4:
            raise ValueError("unsupported terminal finish profile")
        result_metadata = self._terminal_metadata(row, metadata)
        if row["attempt_count"] != 1 or row["regeneration_count"] != 0:
            raise ValueError("unexpected attempt counter")
        self._no_traces(row)
        if any(
            row[name] is not None
            for name in ("advisor_traces", "reasoning_text", "reasoning_duration_secs")
        ) or row["reasoning_model"] not in (None, row["model"]):
            raise ValueError("unsupported terminal traces")
        if (
            row["model"] is not None and (type(row["model"]) is not str or not row["model"].strip())
        ) or any(
            type(row[name]) is not int or row[name] < 0 for name in ("tokens_in", "tokens_out")
        ):
            raise ValueError("unsupported terminal counters or model")
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.task_operations WHERE task_id=$1)", accepted.task_id
        ):
            raise ValueError("unexpected native operation")
        attempts = await conn.fetch(
            "SELECT epoch,outcome,terminal_code,started_at,execution_started_at,ended_at,partial_ciphertext "
            "FROM public.task_attempts WHERE task_id=$1 ORDER BY epoch LIMIT 2",
            accepted.task_id,
        )
        outcome = "failed_terminal" if status == "failed" else status
        if (
            len(attempts) != 1
            or (
                attempts[0]["epoch"],
                attempts[0]["outcome"],
                attempts[0]["terminal_code"],
                attempts[0]["ended_at"],
                attempts[0]["partial_ciphertext"],
            )
            != (fact.epoch, outcome, reason, row["finished_at"], row["result_content"])
            or attempts[0]["execution_started_at"] is None
            or not (
                attempts[0]["started_at"]
                <= attempts[0]["execution_started_at"]
                <= row["finished_at"]
            )
        ):
            raise ValueError("native attempt mismatch")
        event = await conn.fetchrow(
            "SELECT kind,created_at,payload_ciphertext FROM public.task_events WHERE task_id=$1 AND seq=$2",
            accepted.task_id,
            row["event_seq"],
        )
        if (
            event is None
            or (event["kind"], event["created_at"]) != ("finished", row["finished_at"])
            or self._tasks._open(event["payload_ciphertext"]) != {"status": status, "code": reason}
        ):
            raise ValueError("native finished event mismatch")
        row["result_metadata"] = result_metadata

    @staticmethod
    def _terminal_metadata(row: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        status, reason = row["status"], row["terminal_code"]
        finish = returned_finish(metadata.get("turn_finish"))
        if not finish_matches(finish, status, reason):
            raise ValueError("native finish mismatch")
        expected = {
            "terminal_status": "complete" if status == "completed" else "error",
            "turn_finish": finish.model_dump(exclude_none=True),
        }
        if status == "completed" and "finish_reason" in metadata:
            expected["finish_reason"] = "stop"
        if status != "completed":
            expected["terminal_reason"] = reason
        if status == "needs_attention":
            if reason == "uncertain_effect":
                raise ValueError("material operation outside isolated observation")
            expected["uncertain_tools"] = []
        if "usage" in metadata:
            usage = metadata["usage"]
            if (
                type(usage) is not dict
                or set(usage) != {"prompt_tokens", "completion_tokens", "total_tokens"}
                or any(type(n) is not int or not 0 <= n <= 2**63 - 1 for n in usage.values())
                or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]
            ):
                raise ValueError("unsupported usage counters")
            expected["usage"] = usage
        if metadata != expected:
            raise ValueError("unsupported terminal metadata")
        return expected

    def _no_traces(self, message: Any) -> None:
        for name in ("tool_calls", "tool_results"):
            trace = json.loads(message[name]) if type(message[name]) is str else message[name]
            if type(trace) is dict:
                if (
                    set(trace) != {"format", "version", "ciphertext"}
                    or trace["format"] != "daemon.encrypted_tool_trace"
                    or type(trace["version"]) is not int
                    or trace["version"] != 1
                    or type(trace["ciphertext"]) is not str
                ):
                    raise ValueError("unsupported tool envelope")
                trace = json.loads(self._tasks._enc.decrypt(trace["ciphertext"]))
            if type(trace) is not list or trace:
                raise ValueError("unsupported tool trace")

    async def _cancelled_owned(
        self,
        operation: HistoryOperation,
        conn: Any,
        xid: int,
        accepted: AcceptPrepare,
        row: dict[str, Any],
        state: HistoryState,
        metadata: dict[str, Any],
        deadline: float,
    ) -> None:
        """Negative consistency checks only; no native effect API or restore receipt."""
        await self._check_owned(operation, conn, xid, deadline)
        sends = [
            e.command.data
            for e in state.entries
            if type(e.command.data) is SendPossible and e.command.data.task_id == accepted.task_id
        ]
        if len(sends) > 1 or any(
            (send.account_id, send.account_generation, send.task_id, send.epoch, send.effect_kind)
            != (
                accepted.account_id,
                accepted.account_generation,
                accepted.task_id,
                row["lease_epoch"],
                "completion",
            )
            for send in sends
        ):
            raise ValueError("unexpected original send")
        inference = metadata.get("inference_dispatch_possible") is True
        material = metadata.get("material_effect_possible") is True
        if bool(sends) != inference or (inference and not material):
            raise ValueError("inconsistent dispatch warning")
        # The retained task SHARE serializes the cooperating native producer.
        # Inspect original evidence without taking mutation/recovery locks or
        # recomputing the producer's pre-terminal material classifier.
        attempts = await conn.fetch(
            "SELECT a.epoch,a.execution_started_at FROM public.task_attempts a "
            "JOIN public.tasks t ON t.id=a.task_id WHERE t.id=$1 AND t.user_id=$2 ORDER BY a.epoch",
            accepted.task_id,
            accepted.account_id,
        )
        if len(attempts) > 1 or any(a["epoch"] != row["lease_epoch"] for a in attempts):
            raise ValueError("unexpected original attempt")
        execution_marker = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.task_events e JOIN public.tasks t ON t.id=e.task_id "
            "WHERE t.id=$1 AND t.user_id=$2 AND e.kind IN ('execution_started','operation_started','operation_finished'))",
            accepted.task_id,
            accepted.account_id,
        )
        if not material and (
            row["attempt_count"] > 0
            or execution_marker
            or any(a["execution_started_at"] is not None for a in attempts)
        ):
            raise ValueError("missing native material warning")
        await self._check_owned(operation, conn, xid, deadline)

    def _text_values(
        self,
        accepted: AcceptPrepare,
        row: dict[str, Any],
        conversation: Any,
        messages: list[Any],
        *,
        expected_purpose: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Detached, narrow original-text values, not a general history decoder."""
        sealed = self._tasks._open(row["input_ciphertext"])
        required = {
            "message",
            "provider",
            "disable_memory_write",
            "_history_text_only",
            "auto_route",
            "origin_account_id",
            "origin_account_generation",
            "summary_origin_account_id",
            "summary_origin_account_generation",
            "_inference_recovery_version",
            "_request",
        }
        if not required <= sealed.keys() or sealed.keys() - required - {
            "answer_detail",
            "research_effort",
        }:
            raise ValueError("unsupported text input")
        request = json.loads(sealed["_request"])
        request_keys = {
            "conversation_id",
            "message",
            "attachments",
            "model",
            "provider",
            "metadata",
            "disable_memory_write",
            "content_parts",
        }
        if expected_purpose is not None:
            if (
                type(expected_purpose) is not str
                or expected_purpose != _INBOX_PILOT_PURPOSE
                or self._projection.profile != "terminal-v3"
                or type(request) is not dict
                or not _is_inbox_pilot_request(request)
            ):
                raise ValueError("unsupported text purpose")
            request_keys.add("purpose")
        if (
            type(request) is not dict
            or not request_keys <= request.keys()
            or request.keys() - request_keys - {"answer_detail", "research_effort"}
            or canonical_json(request) != sealed["_request"]
            or request["conversation_id"] not in (None, str(accepted.conversation_id))
            or type(request["message"]) is not str
            or not request["message"]
            or request["message"] != sealed["message"]
            or request["attachments"] != []
            or request["content_parts"] != []
            or request["metadata"] != {}
            or request["model"] != "auto"
            or request["provider"] is not None
            or sealed["provider"] is not None
            or type(request["disable_memory_write"]) is not bool
            or request["disable_memory_write"] is not sealed["disable_memory_write"]
            or sealed["_history_text_only"] is not True
            or sealed["auto_route"] is not True
            or type(sealed["_inference_recovery_version"]) is not int
            or sealed["_inference_recovery_version"] != 1
            or sealed["summary_origin_account_id"] != str(accepted.account_id)
            or type(sealed["summary_origin_account_generation"]) is not int
            or sealed["summary_origin_account_generation"] != accepted.account_generation
        ):
            raise ValueError("unsupported text input")
        for name, choices in (
            ("answer_detail", {"brief", "balanced", "detailed"}),
            ("research_effort", {"auto"}),
        ):
            if (name in request) != (name in sealed) or (
                name in request and (request[name] not in choices or request[name] != sealed[name])
            ):
                raise ValueError("unsupported text controls")
        title = request["message"][:50] + ("..." if len(request["message"]) > 50 else "")
        if (
            conversation["id"] != accepted.conversation_id
            or conversation["user_id"] != accepted.account_id
            or conversation["pipeline"] != "cloud"
            or conversation["title"] != title
            or conversation["summary"] is not None
            or conversation["summary_updated_at"] is not None
            or conversation["pinned"] not in (None, False)
            or conversation["title_locked"] not in (None, False)
            or self._object(conversation["metadata"]) != {}
        ):
            raise ValueError("unsupported conversation")
        for name in ("tokens_total",):
            if type(conversation[name]) is not int or conversation[name] < 0:
                raise ValueError("unsupported counter")
        status = row["status"]
        expected_status = {
            "queued": "streaming",
            "running": "streaming",
            "completed": "complete",
            "cancelled": "cancelled",
        }.get(status)
        if self._projection.profile == "terminal-v4" and status in ("failed", "needs_attention"):
            expected_status = "error"
        if (
            expected_status is None
            or row["attempt_count"] not in (0, 1)
            or row["regeneration_count"] != 0
        ):
            raise ValueError("unresolved text outcome")
        if len(messages) != 2:
            raise ValueError("unsupported messages")
        values = []
        for message, ident, role in zip(
            messages, (accepted.user_message_id, accepted.result_message_id), ("user", "assistant")
        ):
            if (message["id"], message["conversation_id"], message["user_id"], message["role"]) != (
                ident,
                accepted.conversation_id,
                accepted.account_id,
                role,
            ):
                raise ValueError("unsupported message identity")
            for name in ("tokens_in", "tokens_out"):
                if type(message[name]) is not int or message[name] < 0:
                    raise ValueError("unsupported counter")
            self._no_traces(message)
            model = message["model"]
            if model is not None and (type(model) is not str or not model.strip()):
                raise ValueError("unsupported model")
            if (
                any(
                    message[name] is not None
                    for name in ("advisor_traces", "reasoning_text", "reasoning_duration_secs")
                )
                or message["reasoning_model"] not in (None, model)
                or message["status"] != ("complete" if role == "user" else expected_status)
            ):
                raise ValueError("unsupported message state")
            metadata = self._object(message["metadata"])
            expected_metadata: dict[str, Any] = (
                {}
                if role == "user" or status in ("queued", "running")
                else {"terminal_status": expected_status}
            )
            if role == "assistant" and status == "cancelled":
                if row["terminal_code"] != "cancelled":
                    raise ValueError("unsupported terminal reason")
                expected_metadata["terminal_reason"] = "cancelled"
                for flag in ("inference_dispatch_possible", "material_effect_possible"):
                    if flag in metadata:
                        if metadata[flag] is not True:
                            raise ValueError("unsupported uncertainty flag")
                        expected_metadata[flag] = True
                if (
                    "inference_dispatch_possible" in metadata
                    and "material_effect_possible" not in metadata
                ):
                    raise ValueError("inference without material warning")
            if role == "assistant" and "result_metadata" in row:
                expected_metadata = row["result_metadata"]
            if metadata != expected_metadata:
                raise ValueError("unsupported metadata")
            content = self._tasks._enc.decrypt(message["content"])
            if role == "user" and content != request["message"]:
                raise ValueError("original user mismatch")
            values.append(
                {
                    **{
                        name: message[name]
                        for name in (
                            "id",
                            "conversation_id",
                            "user_id",
                            "role",
                            "model",
                            "tokens_in",
                            "tokens_out",
                            "status",
                            "reasoning_model",
                            "created_at",
                        )
                    },
                    "content": content,
                    "metadata": metadata,
                    "tool_calls": [],
                    "tool_results": [],
                    "advisor_traces": None,
                    "reasoning_text": None,
                    "reasoning_duration_secs": None,
                }
            )
        result_content = values[1]["content"]
        if (
            values[0]["created_at"] >= values[1]["created_at"]
            or result_content != self._tasks._enc.decrypt(row["result_content"])
            or (
                expected_purpose is not None
                and status == "completed"
                and not result_content.strip()
            )
        ):
            raise ValueError("original result mismatch")
        conv = {
            name: conversation[name]
            for name in (
                "id",
                "user_id",
                "pipeline",
                "title",
                "summary",
                "tokens_total",
                "pinned",
                "title_locked",
                "created_at",
                "updated_at",
                "summary_updated_at",
            )
        }
        conv.update(
            message_count=2,
            metadata={},
            last_activity_at=max(
                values[1]["created_at"],
                conversation["last_activity_at"] or conversation["updated_at"],
                conversation["updated_at"],
            ),
        )
        return conv, values

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        result = json.loads(value) if type(value) is str else value
        if type(result) is not dict:
            raise ValueError("unsupported object")
        return result

    async def lookup(
        self, auth: AuthenticatedDevice, client_key: str, *, deadline: float
    ) -> HistoricalObservation:
        self._validate()
        self._history._bind()
        _check_time(deadline)
        self._projection.expected.verify_key(self._key)
        if (
            self._history._expected != self._projection.expected
            or type(self._tasks._pool) is not asyncpg.Pool
            or self._tasks._pool is not self._tasks._memory._pool
            or type(auth) is not AuthenticatedDevice
            or any(not isinstance(v, UUID) for v in (auth.user_id, auth.device_id, auth.session_id))
        ):
            raise HistoryError("observation_composition_refused")
        # Copy mutable request context before scheduling the supervised owner.
        owner = AuthenticatedDevice(auth.user_id, auth.device_id, auth.session_id)
        # Lookup uses the exact approved account/key namespace. No request body
        # is inferred; the unused request commitment is not replay comparison.
        lookup, _ = commitments(self._key, owner.user_id, client_key, b"")
        child = asyncio.create_task(self._lookup(owner, client_key, lookup, deadline))
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                result = await asyncio.shield(child)
            _check_time(deadline)
            if self._history._blocked:
                raise HistoryError("observation_unavailable")
            return result
        except BaseException as original:
            self._history._blocked = True
            if not child.done():
                child.cancel()
            while not child.done():
                try:
                    await asyncio.shield(child)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                child.result()
            except BaseException as secondary:
                if secondary is not original:
                    original.add_note("Observation cleanup completion: " + type(secondary).__name__)
            raise

    async def _lookup(
        self, auth: AuthenticatedDevice, client_key: str, lookup: str, deadline: float
    ) -> HistoricalObservation:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await operation.verify()
            accepted = next(
                (
                    e.command.data
                    for e in state.entries
                    if type(e.command.data) is AcceptPrepare
                    and e.command.data.account_id == auth.user_id
                    and hmac.compare_digest(e.command.data.key_commitment, lookup)
                ),
                None,
            )
            async with pool_connection(self._tasks._pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("observation_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    await self._projection._limits(conn, deadline)
                    if (
                        await conn.fetchval(
                            "SELECT current_schemas(true)=ARRAY['pg_catalog','public']::name[]"
                        )
                        is not True
                    ):
                        raise HistoryError("observation_namespace_refused")
                    await lock_auth_runtime_state(conn)
                    authorized = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM public.sessions s JOIN public.devices d ON d.id=s.device_id "
                        "WHERE s.id=$1 AND d.id=$2 AND s.user_id=$3 AND d.user_id=$3 "
                        "AND s.revoked_at IS NULL AND d.revoked_at IS NULL "
                        "AND s.access_expires_at>clock_timestamp())",
                        auth.session_id,
                        auth.device_id,
                        auth.user_id,
                    )
                    if authorized is not True:
                        return HistoricalObservation("denied")
                    xid = await conn.fetchval("SELECT txid_current()")
                    parity = await self._parity_owned(operation, conn, xid, state, deadline)
                    if accepted is None:
                        if not parity:
                            return HistoricalObservation("unavailable")
                        sql_only = await conn.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM public.tasks WHERE user_id=$1 AND idempotency_key=$2)",
                            auth.user_id,
                            client_key,
                        )
                        return HistoricalObservation("recovery" if sql_only else "unknown")
                    try:
                        async with guard_content_write(
                            conn, CapturedGeneration(auth.user_id, accepted.account_generation)
                        ):
                            # This reuses the native generation/lifecycle guard
                            # for a read, not a grant to write. Account SHARE
                            # precedes task SHARE; no fresh generation is captured.
                            if not parity:
                                return HistoricalObservation("recovery", task_id=accepted.task_id)
                            row = await self._task_owned(
                                operation, conn, xid, auth, accepted, client_key, state, deadline
                            )
                            if row is None:
                                return HistoricalObservation("recovery", task_id=accepted.task_id)
                            return HistoricalObservation("task", self._tasks._snapshot_from(row))
                    except WriteGuardRefusal:
                        # No old content or original ID after lifecycle/generation
                        # refusal. Negative key identity still cannot become NEW.
                        return HistoricalObservation("recovery")
