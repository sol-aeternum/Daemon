"""Explicit isolated authenticated acceptance host, never selected by startup.

Borrows actual A/W pools; owns its H coordinators and every admitted child.
The caller closes borrowed pools only after aclose has settled. The default
construction accepts and observes only; an explicitly supplied text composition
owns original runner jobs. Neither construction selects production startup,
a queue or a recovery writer. The chat stream remains acceptance metadata,
not an answer/progress/output pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
import hmac
import math
import os
import time
from typing import Any, Literal, TypeVar
from uuid import UUID, uuid4

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator

from orchestrator.auth import (
    AuthenticatedDevice,
    _extract_bearer_token,
    _verify_access_token_on_connection,
)
from orchestrator.auth_runtime_state import lock_auth_runtime_state
from orchestrator.auth_tokens import hash_token
from orchestrator.daemon import new_request_id, stream_with_keepalives
from orchestrator.models import ChatRequest
from orchestrator.request_body_limit import RequestBodyLimitMiddleware
from orchestrator.router import COUNCIL_RETIRED_MESSAGE, is_retired_council_command
from orchestrator.routes.tasks import (
    IDEMPOTENCY_KEY_PATTERN,
    CancelOut,
    TaskOut,
    historical_task_out,
    task_out,
)
from orchestrator.routes.conversations import (
    ConversationListResponse,
    ConversationWithMessagesOut,
    ConversationOut,
    HistoryPageOut,
    MessageOut,
    _normalize_conversation,
    _normalize_message,
)
from orchestrator.history_pagination import (
    HistoryBoundary,
    InvalidHistoryCursor,
    decode_cursor,
    encode_cursor,
)
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    WriteGuardRefusal,
    capture_generation,
    guard_content_write,
)
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.session_cleanup import lock_session_cleanup
from orchestrator.tasks._invocation import block
from orchestrator.tasks.inputs import (
    _INBOX_PILOT_PURPOSE,
    _INBOX_PILOT_TEXT,
    _INBOX_OWNED_INDEX_QUESTION,
    _INBOX_TWO_JOB_PURPOSE,
    _INBOX_TWO_JOB_TEXTS,
    chat_request_fingerprint,
    valid_research_question,
)
from orchestrator.tasks.observe import _Observation
from orchestrator.tasks.store import (
    AcceptedTask,
    ConversationBusy,
    IdempotencyConflict,
    TaskNotFound,
    TaskSnapshot,
    TaskStore,
    CancelAuthorizationExpectation,
    CancelOutcome,
)
from orchestrator.services.account_control.transaction import ObservationBlocked

from .composition import HistoryAcceptance
from .coordination import HistoryCoordinator, HistoryOperation
from .effects_projection import HistoryProjection
from .inbox_lineage import InboxChild
from .model import HistoryError
from .observation import HistoricalObservation, HistoryObservation
from .read_supervisor import _observe, qualify_read_history
from .store import _check_time
from .terminal_projection import (
    TERMINAL_PROFILES,
    InboxOwnedIndexHistoryProjection,
    InboxRetiredHistoryProjection,
    InboxResearchHistoryProjection,
    InboxSourcesHistoryProjection,
    research_purpose,
    TerminalHistoryProjection,
)
from .text_execution import (
    HistoryOwnedIndexExecution,
    HistoryResearchExecution,
    HistorySourcesExecution,
    HistoryTextExecution,
)
from .model import (
    AcceptPrepare,
    Command,
    InboxOriginalRetired,
    SendPossible,
    StopIntent,
    TerminalOutputPrepare,
    commitments,
)

T = TypeVar("T")
HEADERS = {"Cache-Control": "no-store"}


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "history_unavailable", "task_id": None, "retry_safe": False},
        headers=HEADERS,
    )


@dataclass(frozen=True)
class _Submission:
    task: AcceptedTask
    auth: AuthenticatedDevice
    question: str | None = None


class _InboxPilotInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str

    @field_validator("text")
    @classmethod
    def _scripted_text(cls, value: str) -> str:
        if value != _INBOX_PILOT_TEXT:
            raise ValueError("unsupported inbox request")
        return value


class _InboxTwoJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: Literal["Do a deep dive on X.", "Do a deep dive on Y."]


class _InboxResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str

    @field_validator("text")
    @classmethod
    def _question(cls, value: str) -> str:
        value = value.strip()
        if not valid_research_question(value):
            raise ValueError("unsupported research question")
        return value


_Topic = Literal["X", "Y"]
_TOPICS: tuple[_Topic, ...] = ("X", "Y")


def _inbox_topic(message: str) -> _Topic:
    if message == _INBOX_TWO_JOB_TEXTS[0]:
        return "X"
    if message == _INBOX_TWO_JOB_TEXTS[1]:
        return "Y"
    raise ValueError("unsupported inbox topic")


@dataclass(frozen=True)
class _InboxOriginal:
    accepted: AcceptPrepare
    topic: _Topic
    conv: dict[str, Any]
    messages: list[dict[str, Any]]
    snapshot: TaskSnapshot
    handoff: UUID | None = None


class _InboxWithoutReport(BaseModel):
    state: Literal["empty", "queued", "running", "cancelled"]


class _InboxCompleted(BaseModel):
    state: Literal["completed"] = "completed"
    conversation_id: UUID


_InboxStatus = _InboxWithoutReport | _InboxCompleted
_UIReadKind = Literal["task", "conversation", "list", "inbox"]


class IsolatedAcceptanceHost:
    """Inert, explicit construction; no dependency override or account parameter.

    Auth controls serialize while the W auth scope encloses native A acceptance.
    Logical budgets bound admission/waits, not physical driver/kernel cleanup.
    No failed writer/read owner is renewed, including after historical refusal.
    """

    def __init__(
        self,
        acceptance: HistoryAcceptance,
        observation: HistoryObservation,
        *,
        fingerprint_key: str,
        max_body_bytes: int,
        keepalive_s: float,
        execution: HistoryTextExecution | None = None,
        inbox_pilot: bool = False,
        inbox_two_jobs: bool = False,
        _inbox_child: InboxChild | None = None,
    ) -> None:
        if (
            type(acceptance) is not HistoryAcceptance
            or type(observation) is not HistoryObservation
            or type(acceptance._history) is not HistoryCoordinator
            or type(observation._history) is not HistoryCoordinator
            or not fingerprint_key
            or type(max_body_bytes) is not int
            or max_body_bytes < 1
            or not math.isfinite(keepalive_s)
            or keepalive_s <= 0
            or type(inbox_pilot) is not bool
            or type(inbox_two_jobs) is not bool
            or (inbox_two_jobs and not inbox_pilot)
        ):
            raise HistoryError("isolated_host_refused")
        self._acceptance, self._reader = acceptance, observation
        self._execution = execution
        self._inbox_pilot = inbox_pilot
        self._inbox_two_jobs = inbox_two_jobs
        self._inbox_research = type(acceptance._projection) in (
            InboxResearchHistoryProjection,
            InboxSourcesHistoryProjection,
            InboxOwnedIndexHistoryProjection,
        )
        self._owned_index = type(acceptance._projection) is InboxOwnedIndexHistoryProjection
        self._inbox_child = _inbox_child
        self._original_receipt: _Submission | None = None
        self._retired_record: UUID | None = None
        self._pending_submissions = 0
        # A separate inert reader, bound to the exact acceptor H owner and
        # existing W store. Only the private admission precheck uses it.
        self._inbox_admission_reader = (
            HistoryObservation(
                observation._tasks, acceptance._history, observation._projection, observation._key
            )
            if inbox_two_jobs
            else None
        )
        self._fingerprint_key = fingerprint_key
        self._max_body_bytes, self._keepalive_s = max_body_bytes, keepalive_s
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = self._ready = self._closing = self._closed = False
        self._active: set[asyncio.Task[Any]] = set()
        self._streams: set[asyncio.Task[Any]] = set()
        self._shutdown_task: asyncio.Task[None] | None = None
        if _inbox_child is not None:
            if type(_inbox_child) is not InboxChild:
                raise HistoryError("inbox_launcher_required")
            _inbox_child.bind(
                self, acceptance._history._path, acceptance._projection.expected.history_id
            )
        self._validate()

    def _validate(self) -> None:
        acceptance, observation = self._acceptance, self._reader
        ap, wp = acceptance._projection, observation._projection
        a, w = acceptance._tasks, observation._tasks
        if (
            type(acceptance) is not HistoryAcceptance
            or type(observation) is not HistoryObservation
            or type(acceptance._history) is not HistoryCoordinator
            or type(observation._history) is not HistoryCoordinator
            or type(ap) not in (HistoryProjection, *TERMINAL_PROFILES)
            or type(ap) is not type(wp)
            or ap.profile != TERMINAL_PROFILES.get(type(ap), "h1")
            or ap.profile != wp.profile
            or acceptance._history.profile != ap.profile
            or observation._history.profile != wp.profile
            or acceptance._history._expected != ap.expected
            or observation._history._expected != wp.expected
            or ap.expected != wp.expected
            or ap.acceptor != wp.acceptor
            or ap.worker != wp.worker
            or (
                isinstance(ap, TerminalHistoryProjection)
                and (not isinstance(wp, TerminalHistoryProjection) or ap.publisher != wp.publisher)
            )
            or acceptance._history is observation._history
            or acceptance._history._path != observation._history._path
            or type(a) is not TaskStore
            or type(w) is not TaskStore
            or type(a._pool) is not asyncpg.Pool
            or type(w._pool) is not asyncpg.Pool
            or a._pool is w._pool
            or a._pool is not a._memory._pool
            or w._pool is not w._memory._pool
            or a._enc is not w._enc
            or a._cover_new_inference is not True
            or a._inference is not None
            or any(
                t._composition is not None
                or t._resource_inputs is not None
                or getattr(t._memory, "_task_composition", None) is not None
                or getattr(t._memory, "_resource_inputs", None) is not None
                for t in (a, w)
            )
            or self._acceptance._projection.expected != self._reader._projection.expected
            or self._acceptance._key != self._reader._key
        ):
            raise HistoryError("isolated_host_refused")
        self._acceptance._projection.expected.verify_key(self._acceptance._key)
        execution = self._execution
        retired_profile = type(ap) is InboxRetiredHistoryProjection
        retired_observer = retired_profile and execution is None and self._inbox_child is None
        research_profile = type(ap) in (
            InboxResearchHistoryProjection,
            InboxSourcesHistoryProjection,
            InboxOwnedIndexHistoryProjection,
        )
        if (
            self._inbox_research != research_profile
            or self._owned_index != (type(ap) is InboxOwnedIndexHistoryProjection)
            or (research_profile and not self._inbox_two_jobs)
            or (
                execution is not None
                and research_profile
                != (
                    type(execution)
                    in (
                        HistoryResearchExecution,
                        HistorySourcesExecution,
                        HistoryOwnedIndexExecution,
                    )
                )
            )
        ):
            raise HistoryError("isolated_host_refused")
        if (
            (retired_profile and not self._inbox_two_jobs)
            or (self._inbox_child is not None and not retired_profile)
            or (retired_profile and execution is not None and self._inbox_child is None)
        ):
            raise HistoryError("inbox_launcher_required")
        if self._inbox_child is not None:
            self._inbox_child.check(self)
        if (
            self._inbox_pilot
            and not retired_observer
            and not research_profile
            and (execution is None or ap.profile not in ("terminal-v3", "inbox-retired-v1"))
        ):
            raise HistoryError("isolated_host_refused")
        admission_reader = self._inbox_admission_reader
        if self._inbox_two_jobs and (
            not self._inbox_pilot
            or type(admission_reader) is not HistoryObservation
            or admission_reader._tasks is not w
            or admission_reader._history is not acceptance._history
            or admission_reader._projection is not wp
            or admission_reader._key != observation._key
        ):
            raise HistoryError("isolated_host_refused")
        if execution is not None and (
            type(execution)
            not in (
                HistoryTextExecution,
                HistoryResearchExecution,
                HistorySourcesExecution,
                HistoryOwnedIndexExecution,
            )
            or type(ap) not in TERMINAL_PROFILES
            or execution.pool is not w._pool
            or execution.encryption is not w._enc
            or execution.projection is not ap
            or execution.history in (acceptance._history, observation._history)
            or execution.history._path != acceptance._history._path
            or execution.history._expected != ap.expected
            or execution.history.profile != ap.profile
        ):
            raise HistoryError("isolated_host_refused")

    def _bind(self) -> None:
        if os.getpid() != self._pid:
            raise HistoryError("forked_isolated_host")
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise HistoryError("wrong_loop")

    async def _run(self, call: Coroutine[Any, Any, T], deadline: float, *, writing: bool) -> T:
        try:
            self._bind()
            _check_time(deadline)
            if self._closing or self._closed:
                raise HistoryError("isolated_host_closed")
            child = asyncio.create_task(call)
        except BaseException as original:
            try:
                call.close()
            except BaseException as cleanup:
                original.add_note("Unstarted isolated operation cleanup: " + type(cleanup).__name__)
            raise
        self._active.add(child)
        try:
            return await _observe(child, deadline, cancel_body=True)
        except BaseException:
            if writing:
                self._acceptance._blocked = self._acceptance._history._blocked = True
                block("history_acceptance_unavailable")
            raise
        finally:
            self._active.discard(child)

    async def open(self, *, deadline: float) -> None:
        self._bind()
        if self._started:
            raise HistoryError("isolated_host_already_started")
        self._started = True
        try:
            await self._run(self._open(deadline), deadline, writing=True)
            self._ready = True
        except BaseException as original:
            try:
                await self.aclose(deadline=deadline)
            except BaseException as cleanup:
                original.add_note("Isolated startup cleanup: " + type(cleanup).__name__)
            raise

    async def _open(self, deadline: float) -> None:
        self._validate()
        await self._acceptance._history.open(deadline=deadline)
        await self._reader._history.open(deadline=deadline)
        async with self._reader._history.operation(deadline=deadline) as operation:
            await qualify_read_history(
                operation, self._reader._tasks._pool, self._reader._projection, deadline=deadline
            )
        async with self._acceptance._history.operation(deadline=deadline), retain_pool_cleanup():
            async with pool_connection(self._acceptance._tasks._pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("acceptance_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    await self._acceptance._projection.qualify(
                        conn, writing=True, deadline=deadline
                    )
        if self._execution is not None:
            await self._execution.open(deadline=deadline)

    def _admit(self, deadline: float) -> None:
        self._bind()
        _check_time(deadline)
        self._validate()
        if not self._ready or self._closing or self._closed:
            raise HistoryError("isolated_host_closed")

    async def _auth(self, conn: Any, token: str, deadline: float) -> AuthenticatedDevice | None:
        """W only, caller retains this transaction through A COMMIT and cleanup."""
        if conn.is_in_transaction() is not True:
            raise HistoryError("auth_transaction_required")
        await self._reader._projection._limits(conn, deadline)
        if (
            await conn.fetchval("SELECT current_schemas(true)=ARRAY['pg_catalog','public']::name[]")
            is not True
        ):
            raise HistoryError("observation_namespace_refused")
        await self._reader._projection.qualify(conn, writing=False, deadline=deadline)
        if await conn.fetchval("SELECT CURRENT_USER::text") != self._reader._projection.worker:
            raise HistoryError("observation_principal_refused")
        await lock_session_cleanup(conn)
        await lock_auth_runtime_state(conn)
        # Candidate lookup grants nothing. Locks precede real token verification
        # and last_seen_at mutation; separate post-wait SQL checks wall time.
        candidate = await conn.fetchrow(
            "SELECT id,device_id FROM public.sessions WHERE access_token_hash=$1", hash_token(token)
        )
        if candidate is None:
            return None
        await conn.fetchrow(
            "SELECT id FROM public.devices WHERE id=$1 FOR SHARE", candidate["device_id"]
        )
        await conn.fetchrow("SELECT id FROM public.sessions WHERE id=$1 FOR SHARE", candidate["id"])
        owner = await conn.fetchval(
            "SELECT s.user_id FROM public.sessions s JOIN public.devices d ON d.id=s.device_id "
            "WHERE s.id=$1 AND d.id=$2 AND s.user_id=d.user_id "
            "AND s.revoked_at IS NULL AND d.revoked_at IS NULL "
            "AND s.access_token_hash=$3 AND s.access_expires_at>clock_timestamp()",
            candidate["id"],
            candidate["device_id"],
            hash_token(token),
        )
        if not isinstance(owner, UUID):
            return None
        auth = await _verify_access_token_on_connection(conn, token)
        if (
            auth is None
            or (auth.session_id, auth.device_id, auth.user_id)
            != (candidate["id"], candidate["device_id"], owner)
            or await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.sessions s JOIN public.devices d ON d.id=s.device_id "
                "WHERE s.id=$1 AND d.id=$2 AND s.user_id=$3 AND d.user_id=$3 "
                "AND s.revoked_at IS NULL AND d.revoked_at IS NULL "
                "AND s.access_token_hash=$4 AND s.access_expires_at>clock_timestamp())",
                auth.session_id,
                auth.device_id,
                auth.user_id,
                hash_token(token),
            )
            is not True
        ):
            return None
        _check_time(deadline)
        return AuthenticatedDevice(auth.user_id, auth.device_id, auth.session_id)

    @staticmethod
    def _submission_credentials(
        authorization: str | None, client_key: str | None
    ) -> tuple[str, str]:
        token = _extract_bearer_token(authorization)
        if token is None:
            raise HTTPException(401, "Missing or invalid authorization header", headers=HEADERS)
        if client_key is None or not IDEMPOTENCY_KEY_PATTERN.fullmatch(client_key):
            raise HTTPException(
                422, {"code": "invalid_idempotency_key", "message": "Invalid key"}, headers=HEADERS
            )
        return token, client_key

    async def submit(
        self,
        authorization: str | None,
        client_key: str | None,
        payload: ChatRequest,
        *,
        deadline: float,
    ) -> _Submission:
        """HTTP adapter only: no account, generation, task ID or callback input."""
        self._admit(deadline)
        if self._inbox_pilot:
            raise _unavailable()
        token, key = self._submission_credentials(authorization, client_key)
        value = payload.model_copy(deep=True)
        if self._execution is not None:
            latest = next(
                (m for m in reversed(value.messages or []) if m.get("role") == "user"), None
            )
            if (
                value.model not in (None, "auto")
                or value.provider is not None
                or value.metadata
                or value.research_effort == "research"
                or (latest is not None and latest.get("model") not in (None, "", "auto"))
            ):
                raise _unavailable()
        if (
            value.attachments
            or value.suggestion_id is not None
            or value.user_id is not None
            or value.temporal_scope is not None
            or any(type(m.get("content")) is not str for m in value.messages or [])
        ):
            raise _unavailable()
        return await self._submit_authenticated(token, key, value, deadline)

    async def _submit_inbox_pilot(
        self,
        authorization: str | None,
        client_key: str | None,
        payload: _InboxPilotInput | _InboxTwoJobInput | _InboxResearchInput,
        *,
        deadline: float,
    ) -> Any:
        self._admit(deadline)
        if (
            self._execution is None
            or not self._inbox_pilot
            or type(payload)
            is not (
                _InboxResearchInput
                if self._inbox_research
                else _InboxTwoJobInput
                if self._inbox_two_jobs
                else _InboxPilotInput
            )
        ):
            raise _unavailable()
        token, key = self._submission_credentials(authorization, client_key)
        self._pending_submissions += 1
        try:
            receipt = await self._submit_authenticated(
                token, key, ChatRequest(message=payload.text, disable_memory_write=True), deadline
            )
        finally:
            self._pending_submissions -= 1
        topic = (
            _inbox_topic(payload.text)
            if self._inbox_two_jobs and not self._inbox_research
            else None
        )
        return await self.ui_read(
            authorization, "inbox", None, deadline=deadline, topic=topic, _receipt=receipt
        )

    async def _submit_authenticated(
        self, token: str, client_key: str, payload: ChatRequest, deadline: float
    ) -> _Submission:
        result = await self._run(
            self._submit(token, client_key, payload, deadline), deadline, writing=True
        )
        if isinstance(result, HTTPException):
            raise result
        _check_time(deadline)
        if self._acceptance._blocked or self._acceptance._history._blocked:
            raise _unavailable()
        if self._execution is not None and result.task.created:
            # Only the original NEW receipt, after native/auth/reset/H cleanup.
            if self._inbox_child is not None:
                self._inbox_child.check(self)
                if self._closing or self._reader._history._blocked:
                    raise _unavailable()
            self._execution.schedule(result.task.task_id)
            if self._inbox_child is not None and self._inbox_child.handoff is None:
                self._original_receipt = result
        return result

    async def _submit(
        self, token: str, client_key: str, payload: ChatRequest, deadline: float
    ) -> _Submission | HTTPException:
        async with (
            self._acceptance._history.operation(deadline=deadline) as operation,
            retain_pool_cleanup(),
        ):
            admission: tuple[AuthenticatedDevice, CapturedGeneration] | None = None
            if self._inbox_two_jobs:
                checked = await self._inbox_precheck(
                    operation, token, client_key, payload.message, deadline
                )
                if isinstance(checked, HTTPException):
                    return checked
                admission = checked
            async with pool_connection(self._reader._tasks._pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("auth_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    auth = await self._auth(conn, token, deadline)
                    if auth is None:
                        return HTTPException(
                            status_code=401,
                            detail="Invalid or expired access token",
                            headers=HEADERS,
                        )
                    if admission is not None and auth != admission[0]:
                        return HTTPException(401, "Authorization changed", headers=HEADERS)
                    latest = next(
                        (m for m in reversed(payload.messages or []) if m.get("role") == "user"),
                        None,
                    )
                    message = ((latest["content"] if latest else "") or payload.message).strip()
                    if is_retired_council_command(message):
                        return HTTPException(
                            410, {"message": COUNCIL_RETIRED_MESSAGE}, headers=HEADERS
                        )
                    try:
                        captured = await capture_generation(conn, auth.user_id)
                    except WriteGuardRefusal:
                        return _unavailable()
                    if admission is not None and captured != admission[1]:
                        return self._ui_recovery()
                    if admission is not None:
                        captured = admission[1]
                    purpose = (
                        research_purpose(self._acceptance._projection)
                        if self._inbox_research
                        else _INBOX_TWO_JOB_PURPOSE
                        if self._inbox_two_jobs
                        else _INBOX_PILOT_PURPOSE
                        if self._inbox_pilot
                        else None
                    )
                    if purpose is not None and admission is None:
                        # H is still exclusively held by this operation until
                        # original A acceptance and both connections settle.
                        # Any prepare counts, including old generations.
                        state = await operation.verify()
                        if any(
                            type(entry.command.data) is AcceptPrepare
                            and entry.command.data.account_id == auth.user_id
                            for entry in state.entries
                        ) or (
                            await conn.fetchval(
                                "SELECT EXISTS(SELECT 1 FROM public.tasks WHERE user_id=$1) "
                                "OR EXISTS(SELECT 1 FROM public.conversations WHERE user_id=$1) "
                                "OR EXISTS(SELECT 1 FROM public.messages WHERE user_id=$1)",
                                auth.user_id,
                            )
                            is not False
                        ):
                            return self._ui_recovery()
                    # No W account SHARE is held. The original native A guard
                    # must reject any subsequent generation/lifecycle change.
                    if not message:
                        return HTTPException(422, "Message must not be empty", headers=HEADERS)
                    try:
                        conversation = (
                            UUID(payload.conversation_id.replace("conv_", ""))
                            if payload.conversation_id
                            else None
                        )
                    except ValueError:
                        return HTTPException(404, "Conversation not found", headers=HEADERS)
                    model = payload.model or "auto"
                    if model == "auto" and latest and isinstance(latest.get("model"), str):
                        model = latest["model"].strip() or "auto"
                    fingerprint = chat_request_fingerprint(
                        key=self._fingerprint_key,
                        conversation_id=conversation,
                        message=message,
                        attachments=None,
                        model=model,
                        provider=payload.provider,
                        metadata=payload.metadata,
                        disable_memory_write=bool(payload.disable_memory_write),
                        answer_detail=payload.answer_detail,
                        research_effort=payload.research_effort,
                        purpose=purpose,
                    )
                    task_input = {
                        "message": message,
                        "provider": payload.provider,
                        "disable_memory_write": bool(payload.disable_memory_write),
                    }
                    if self._execution is not None:
                        task_input["_history_text_only"] = True
                        task_input["auto_route"] = True
                    for name in ("answer_detail", "research_effort"):
                        if getattr(payload, name) is not None:
                            task_input[name] = getattr(payload, name)
                    options = dict(
                        user_id=auth.user_id,
                        conversation_id=conversation,
                        new_conversation_title=message[:50] + ("..." if len(message) > 50 else ""),
                        pipeline="cloud",
                        user_message=message,
                        task_input=task_input,
                        request_hash=fingerprint.digest,
                        request_canonical=fingerprint.canonical,
                        idempotency_key=client_key,
                        assistant_model=None,
                        captured_generation=captured,
                    )
                    lookup, request = self._acceptance._request(options, purpose=purpose)
                    if self._inbox_two_jobs and self._reader._history._blocked:
                        return _unavailable()
                    accepted = await self._acceptance._accept_owned(
                        operation, deadline, options, lookup, request, purpose=purpose
                    )
            # W auth transaction and A's actual reset have settled; outer scope
            # still owns W holder reset, filesystem work and H release.
        return _Submission(accepted, auth, message if self._inbox_research else None)

    async def _inbox_precheck(
        self,
        operation: HistoryOperation,
        token: str,
        client_key: str,
        message: str,
        deadline: float,
    ) -> tuple[AuthenticatedDevice, CapturedGeneration] | HTTPException:
        """Negative admission check under the original acceptor H operation.

        W's account/content locks and physical holder must be released before
        native A acquires its own account guard. H remains held across both.
        """
        reader = self._inbox_admission_reader
        if reader is None or self._acceptance._blocked or self._reader._history._blocked:
            return _unavailable()
        async with pool_connection(reader._tasks._pool) as conn:
            if conn.is_in_transaction():
                raise HistoryError("auth_transaction_owner_refused")
            async with conn.transaction(isolation="read_committed"):
                auth = await self._auth(conn, token, deadline)
                if auth is None:
                    return HTTPException(401, "Invalid or expired access token", headers=HEADERS)
                xid = await conn.fetchval("SELECT txid_current()")
                checked = await self._inbox_originals(reader, operation, conn, xid, auth, deadline)
                if isinstance(checked, HTTPException):
                    return checked
                current, originals = checked
                allowed = await self._inbox_can_start(operation, conn, auth, originals)
                if self._inbox_research:
                    permitted = (
                        bool(allowed)
                        and valid_research_question(message)
                        and (not self._owned_index or message == _INBOX_OWNED_INDEX_QUESTION)
                        and all(message != o.messages[0]["content"] for o in originals)
                    )
                else:
                    permitted = message in _INBOX_TWO_JOB_TEXTS and any(
                        message == f"Do a deep dive on {topic}." for topic in allowed
                    )
                if not permitted:
                    return self._ui_recovery()
                lookup, _ = commitments(reader._key, auth.user_id, client_key, b"")
                if any(hmac.compare_digest(lookup, o.accepted.key_commitment) for o in originals):
                    return self._ui_recovery()
                if await self._auth(conn, token, deadline) != auth:
                    return HTTPException(401, "Authorization changed", headers=HEADERS)
                await reader._check_owned(operation, conn, xid, deadline)
        # No read receipt or content lock travels into A. The next transaction
        # must prove the same bearer and capture; A rechecks that capture.
        return auth, current

    async def _inbox_originals(
        self,
        reader: HistoryObservation,
        operation: HistoryOperation,
        conn: Any,
        xid: int,
        auth: AuthenticatedDevice,
        deadline: float,
    ) -> tuple[CapturedGeneration, list[_InboxOriginal]] | HTTPException:
        """Closed two-job envelope, validated in full before selecting a topic."""
        state = await operation.verify()
        if not await reader._parity_owned(operation, conn, xid, state, deadline):
            return _unavailable()
        accepted = [
            e.command.data
            for e in state.entries
            if type(e.command.data) is AcceptPrepare and e.command.data.account_id == auth.user_id
        ]
        if len(accepted) > 2 or (
            self._owned_index
            and sum(type(e.command.data) is AcceptPrepare for e in state.entries) > 1
        ):
            return self._ui_recovery()
        try:
            current = (
                CapturedGeneration(auth.user_id, accepted[0].account_generation)
                if accepted
                else await capture_generation(conn, auth.user_id)
            )
            if any(a.account_generation != current.generation for a in accepted):
                return self._ui_recovery()
            conversations = [a.conversation_id for a in accepted]
            task_ids = [a.task_id for a in accepted]
            message_ids = [m for a in accepted for m in (a.user_message_id, a.result_message_id)]
            if len(set(conversations + task_ids + message_ids)) != 4 * len(accepted):
                return self._ui_recovery()
            async with guard_content_write(conn, current):
                convs = await conn.fetch(
                    "SELECT * FROM public.conversations WHERE user_id=$1 OR id=ANY($2::uuid[]) "
                    "ORDER BY id FOR SHARE",
                    auth.user_id,
                    conversations,
                )
                tasks = await conn.fetch(
                    "SELECT id FROM public.tasks WHERE user_id=$1 OR conversation_id=ANY($2::uuid[]) "
                    "OR id=ANY($3::uuid[]) ORDER BY id FOR SHARE",
                    auth.user_id,
                    conversations,
                    task_ids,
                )
                messages = await conn.fetch(
                    "SELECT * FROM public.messages WHERE user_id=$1 OR conversation_id=ANY($2::uuid[]) "
                    "OR id=ANY($3::uuid[]) ORDER BY created_at,id FOR SHARE",
                    auth.user_id,
                    conversations,
                    message_ids,
                )
                if (
                    {c["id"] for c in convs} != set(conversations)
                    or {t["id"] for t in tasks} != set(task_ids)
                    or {m["id"] for m in messages} != set(message_ids)
                ):
                    return self._ui_recovery()
                originals: list[_InboxOriginal] = []
                for a in accepted:
                    row = await reader._task_owned(
                        operation, conn, xid, auth, a, None, state, deadline
                    )
                    if row is None or await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM public.task_operations WHERE task_id=$1) "
                        "OR (SELECT count(*) FROM public.task_attempts WHERE task_id=$1)>1",
                        a.task_id,
                    ):
                        return self._ui_recovery()
                    if row["status"] == "cancelled":
                        execution = self._execution
                        if execution is None or execution.worker is None:
                            return self._ui_recovery()
                        execution.validate_worker(execution.context(), execution.worker)
                        if a.task_id not in execution._scheduled or not any(
                            type(e.command.data) is StopIntent
                            and (
                                e.command.data.account_id,
                                e.command.data.account_generation,
                                e.command.data.task_id,
                            )
                            == (auth.user_id, current.generation, a.task_id)
                            for e in state.entries
                        ):
                            return self._ui_recovery()
                    try:
                        conv, pair = reader._text_values(
                            a,
                            row,
                            next(c for c in convs if c["id"] == a.conversation_id),
                            [m for m in messages if m["conversation_id"] == a.conversation_id],
                            expected_purpose=(
                                research_purpose(self._acceptance._projection)
                                if self._inbox_research
                                else _INBOX_TWO_JOB_PURPOSE
                            ),
                        )
                        if row["status"] == "cancelled":
                            await reader._cancelled_owned(
                                operation, conn, xid, a, row, state, pair[-1]["metadata"], deadline
                            )
                        snapshot = reader._tasks._snapshot_from(row)
                        topic = (
                            _TOPICS[len(originals)]
                            if self._inbox_research
                            else _inbox_topic(pair[0]["content"])
                        )
                    except Exception:
                        return self._ui_recovery()
                    if any(o.topic == topic for o in originals):
                        return self._ui_recovery()
                    if self._inbox_research and any(
                        o.messages[0]["content"] == pair[0]["content"] for o in originals
                    ):
                        return self._ui_recovery()
                    handoff = None
                    if self._inbox_child is not None and topic == "X":
                        handoff = next(
                            (
                                e.command.record_id
                                for e in state.entries
                                if type(e.command.data) is InboxOriginalRetired
                            ),
                            None,
                        )
                    originals.append(_InboxOriginal(a, topic, conv, pair, snapshot, handoff))
                await reader._check_owned(operation, conn, xid, deadline)
                return current, originals
        except WriteGuardRefusal:
            return self._ui_recovery()

    async def _inbox_can_start(
        self,
        operation: HistoryOperation,
        conn: Any,
        auth: AuthenticatedDevice,
        originals: list[_InboxOriginal],
    ) -> list[_Topic]:
        execution = self._execution
        if (
            execution is None
            or execution.worker is None
            or execution._blocked
            or execution.history._blocked
            or self._acceptance._blocked
            or self._acceptance._history._blocked
            or self._reader._history._blocked
            or len(originals) >= 2
            or (self._owned_index and (await operation.verify()).entries)
        ):
            return []
        try:
            execution.validate_worker(execution.context(), execution.worker)
        except HistoryError:
            return []
        entries = (await operation.verify()).entries if self._inbox_child is not None else ()
        if originals:
            original = originals[0]
            task = original.accepted.task_id
            if original.snapshot.status.value != "completed":
                return []
            if self._inbox_child is not None:
                self._inbox_child.check(self)
                if (
                    original.topic != "X"
                    or original.handoff is None
                    or original.handoff != self._inbox_child.handoff
                    or len(entries) != 4
                    or execution._scheduled
                    or execution._job_tasks
                ):
                    return []
                sent = entries[1].command.data
                if type(sent) is not SendPossible or not await conn.fetchval(
                    "SELECT count(*)=1 AND bool_and(id=$2 AND scope_id=$3 AND status='settled' "
                    "AND actual_microusd BETWEEN 0 AND reserved_microusd AND overage_microusd=0) "
                    "FROM public.entitlement_reservations WHERE user_id=$1",
                    auth.user_id,
                    sent.reservation_id,
                    sent.financial_scope_id,
                ):
                    return []
            elif execution._outcomes.get(task) != "completed" or not any(
                job.done() and ident == task for job, ident in execution._job_tasks.items()
            ):
                return []
            if self._inbox_research and not execution.research_cleanup_confirmed(task):
                return []
        elif self._inbox_child is not None and (self._inbox_child.handoff is not None or entries):
            return []
        if (
            await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.entitlement_reservations WHERE user_id=$1 AND status='open')",
                auth.user_id,
            )
            is not False
        ):
            return []
        if not originals and self._inbox_child is not None:
            return ["X"]
        if self._inbox_research:
            return [_TOPICS[len(originals)]]
        return [t for t in _TOPICS if not any(o.topic == t for o in originals)]

    async def cancel(
        self, authorization: str | None, task: UUID, *, deadline: float
    ) -> CancelOutcome:
        self._admit(deadline)
        if self._execution is None:
            raise _unavailable()
        token = _extract_bearer_token(authorization)
        if token is None:
            raise HTTPException(401, "Missing or invalid authorization header", headers=HEADERS)
        try:
            result = await self._run(self._cancel(token, task, deadline), deadline, writing=True)
        except BaseException:
            self._execution.block()
            raise
        if isinstance(result, HTTPException):
            raise result
        return result

    async def _cancel(self, token: str, task: UUID, deadline: float):
        execution = self._execution
        assert execution is not None and execution.effects is not None
        effects = execution.effects
        effects._admission_deadline(deadline)
        effects._projection.expected.verify_key(self._acceptance._key)
        async with (
            effects._history.operation(deadline=deadline) as operation,
            retain_pool_cleanup(),
        ):
            state = await effects._parity(operation, deadline)
            async with pool_connection(execution.pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("auth_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    auth = await self._auth(conn, token, deadline)
                    if auth is None:
                        return HTTPException(
                            status_code=401,
                            detail="Invalid or expired access token",
                            headers=HEADERS,
                        )
                    if not any(
                        type(e.command.data) is AcceptPrepare
                        and e.command.data.task_id == task
                        and e.command.data.account_id == auth.user_id
                        for e in state.entries
                    ):
                        return HTTPException(404, "Task not found", headers=HEADERS)
                    expected = CancelAuthorizationExpectation(
                        auth.user_id,
                        auth.device_id,
                        auth.session_id,
                        hash_token(token),
                        operation,
                        conn,
                        asyncio.current_task(),
                    )
                    try:
                        result = await effects._cancel_owned(
                            operation, auth.user_id, task, deadline, expected
                        )
                    except ObservationBlocked as error:
                        if error.code == "cancel_authorization_expired":
                            return HTTPException(
                                status_code=401,
                                detail="Invalid or expired access token",
                                headers=HEADERS,
                            )
                        raise
            _check_time(deadline)
        _check_time(deadline)
        return result

    async def lookup(
        self, authorization: str | None, client_key: str, *, deadline: float
    ) -> HistoricalObservation:
        self._admit(deadline)
        token = _extract_bearer_token(authorization)
        if token is None:
            return HistoricalObservation("denied")
        return await self._run(self._lookup(token, client_key, deadline), deadline, writing=False)

    async def _lookup(self, token: str, client_key: str, deadline: float) -> HistoricalObservation:
        async with self._reader._history.operation(deadline=deadline), retain_pool_cleanup():
            async with pool_connection(self._reader._tasks._pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("auth_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    auth = await self._auth(conn, token, deadline)
        if auth is None:
            return HistoricalObservation("denied")
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(client_key):
            return HistoricalObservation("unknown")
        # Existing reader owns another H scope and independently revalidates the
        # actual identities. Initial token proof never substitutes for that check.
        return await self._reader.lookup(auth, client_key, deadline=deadline)

    async def ui_read(
        self,
        authorization: str | None,
        kind: _UIReadKind,
        ident: UUID | None,
        *,
        deadline: float,
        limit: int = 100,
        offset: int = 0,
        pagination: str | None = None,
        before: str | None = None,
        through: str | None = None,
        search: str | None = None,
        topic: _Topic | None = None,
        _receipt: _Submission | None = None,
    ) -> Any:
        """Only the explicitly constructed UI app calls this bounded read."""
        self._admit(deadline)
        if (
            (kind == "inbox" and not self._inbox_pilot)
            or (_receipt is not None and kind != "inbox")
            or (topic is not None and (not self._inbox_two_jobs or kind != "inbox"))
        ):
            raise _unavailable()
        token = _extract_bearer_token(authorization)
        if token is None:
            raise HTTPException(401, "Missing or invalid authorization header", headers=HEADERS)
        try:
            result = await self._run(
                self._ui_read(
                    token,
                    kind,
                    ident,
                    deadline,
                    limit,
                    offset,
                    pagination,
                    before,
                    through,
                    search,
                    topic=topic,
                    receipt=_receipt,
                ),
                deadline,
                writing=False,
            )
            _check_time(deadline)
            if self._reader._history._blocked:
                raise _unavailable()
        except BaseException:
            self._reader._history._blocked = True
            raise
        if isinstance(result, HTTPException):
            raise result
        return result

    async def _ui_read(
        self,
        token: str,
        kind: _UIReadKind,
        ident: UUID | None,
        deadline: float,
        limit: int,
        offset: int,
        pagination: str | None,
        before: str | None,
        through: str | None,
        search: str | None,
        *,
        topic: _Topic | None,
        receipt: _Submission | None,
    ) -> Any:
        reader = self._reader
        async with reader._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            async with pool_connection(reader._tasks._pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("auth_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    auth = await self._auth(conn, token, deadline)
                    if auth is None or (receipt is not None and auth != receipt.auth):
                        return HTTPException(
                            status_code=401,
                            detail="Invalid or expired access token",
                            headers=HEADERS,
                        )
                    if self._inbox_two_jobs:
                        xid = await conn.fetchval("SELECT txid_current()")
                        checked = await self._inbox_originals(
                            reader, operation, conn, xid, auth, deadline
                        )
                        if isinstance(checked, HTTPException):
                            return checked
                        _, originals = checked
                        can_start = await self._inbox_can_start(operation, conn, auth, originals)
                        if await self._auth(conn, token, deadline) != auth:
                            return HTTPException(401, "Authorization changed", headers=HEADERS)
                        await reader._check_owned(operation, conn, xid, deadline)
                        if kind == "inbox":
                            return self._two_job_status(originals, can_start, topic, receipt)
                        selected = next(
                            (
                                o
                                for o in originals
                                if (kind == "task" and o.accepted.task_id == ident)
                                or (kind == "conversation" and o.accepted.conversation_id == ident)
                            ),
                            None,
                        )
                        return self._ui_response(
                            auth,
                            kind,
                            ident,
                            selected.conv if selected else None,
                            selected.messages if selected else [],
                            selected.snapshot if selected else None,
                            limit,
                            offset,
                            pagination,
                            before,
                            through,
                            search,
                            list_values=[o.conv for o in originals],
                        )
                    xid = await conn.fetchval("SELECT txid_current()")
                    state = await operation.verify()
                    parity = await reader._parity_owned(operation, conn, xid, state, deadline)
                    accepted_entries = [
                        e
                        for e in state.entries
                        if type(e.command.data) is AcceptPrepare
                        and e.command.data.account_id == auth.user_id
                    ]
                    try:
                        # Current account health is required even for a truly empty fixture.
                        original = (
                            accepted_entries[0].command.data if len(accepted_entries) == 1 else None
                        )
                        current = (
                            CapturedGeneration(auth.user_id, original.account_generation)
                            if type(original) is AcceptPrepare
                            else await capture_generation(conn, auth.user_id)
                        )
                        async with guard_content_write(conn, current):
                            if not parity:
                                if reader._projection.profile == "terminal-v4" and accepted_entries:
                                    return self._ui_recovery()
                                return _unavailable()
                            if len(accepted_entries) > 1:
                                return self._ui_recovery()
                            entry = accepted_entries[0] if accepted_entries else None
                            accepted = entry.command.data if entry else None
                            if accepted is not None and (
                                type(accepted) is not AcceptPrepare
                                or accepted.account_generation != current.generation
                            ):
                                return self._ui_recovery()
                            # Native rows restrict completeness; they never select the original.
                            conversations = await conn.fetch(
                                "SELECT * FROM public.conversations WHERE user_id=$1 ORDER BY id FOR SHARE",
                                auth.user_id,
                            )
                            if accepted is None:
                                content = await conn.fetchval(
                                    "SELECT EXISTS(SELECT 1 FROM public.tasks WHERE user_id=$1) OR EXISTS(SELECT 1 FROM public.messages WHERE user_id=$1)",
                                    auth.user_id,
                                )
                                if conversations or content:
                                    return self._ui_recovery()
                                conv, messages, snapshot = None, [], None
                            else:
                                assert entry is not None
                                if (
                                    len(conversations) != 1
                                    or conversations[0]["id"] != accepted.conversation_id
                                ):
                                    return self._ui_recovery()
                                row = await reader._task_owned(
                                    operation, conn, xid, auth, accepted, None, state, deadline
                                )
                                if row is None:
                                    return self._ui_recovery()
                                tasks = await conn.fetch(
                                    "SELECT id FROM public.tasks WHERE user_id=$1 OR conversation_id=$2 ORDER BY id FOR SHARE",
                                    auth.user_id,
                                    accepted.conversation_id,
                                )
                                pair = await conn.fetch(
                                    "SELECT * FROM public.messages WHERE user_id=$1 OR conversation_id=$2 ORDER BY created_at,id FOR SHARE",
                                    auth.user_id,
                                    accepted.conversation_id,
                                )
                                if len(tasks) != 1 or tasks[0]["id"] != accepted.task_id:
                                    return self._ui_recovery()
                                if await conn.fetchval(
                                    "SELECT EXISTS(SELECT 1 FROM public.task_operations WHERE task_id=$1) OR (SELECT count(*) FROM public.task_attempts WHERE task_id=$1)>1",
                                    accepted.task_id,
                                ):
                                    return self._ui_recovery()
                                if row["status"] == "cancelled":
                                    execution = self._execution
                                    if execution is None or execution.worker is None:
                                        return self._ui_recovery()
                                    # Only an additional current-owner restriction, never a receipt.
                                    execution.validate_worker(execution.context(), execution.worker)
                                    if accepted.task_id not in execution._scheduled or not any(
                                        type(e.command.data) is StopIntent
                                        and (
                                            e.command.data.account_id,
                                            e.command.data.account_generation,
                                            e.command.data.task_id,
                                        )
                                        == (auth.user_id, current.generation, accepted.task_id)
                                        for e in state.entries
                                    ):
                                        return self._ui_recovery()
                                try:
                                    conv, messages = reader._text_values(
                                        accepted,
                                        row,
                                        conversations[0],
                                        list(pair),
                                        expected_purpose=(
                                            _INBOX_PILOT_PURPOSE if self._inbox_pilot else None
                                        ),
                                    )
                                    if row["status"] == "cancelled":
                                        await reader._cancelled_owned(
                                            operation,
                                            conn,
                                            xid,
                                            accepted,
                                            row,
                                            state,
                                            messages[-1]["metadata"],
                                            deadline,
                                        )
                                    snapshot = reader._tasks._snapshot_from(row)
                                except Exception:
                                    return self._ui_recovery()
                            # Recheck bearer/expiry after every blocking account/content lock.
                            final_auth = await self._auth(conn, token, deadline)
                            if final_auth != auth:
                                return HTTPException(
                                    status_code=401,
                                    detail="Invalid or expired access token",
                                    headers=HEADERS,
                                )
                            await reader._check_owned(operation, conn, xid, deadline)
                            if kind == "inbox":
                                return self._inbox_status(accepted, snapshot, receipt)
                            return self._ui_response(
                                auth,
                                kind,
                                ident,
                                conv,
                                messages,
                                snapshot,
                                limit,
                                offset,
                                pagination,
                                before,
                                through,
                                search,
                            )
                    except WriteGuardRefusal:
                        return self._ui_recovery()

    def _two_job_status(
        self,
        originals: list[_InboxOriginal],
        can_start: list[_Topic],
        topic: _Topic | None,
        receipt: _Submission | None,
    ) -> Any:
        labels = (
            {"research_questions": [o.messages[0]["content"] for o in originals]}
            if self._inbox_research
            else {}
        )
        if receipt is not None:
            selected = next(
                (o for o in originals if o.accepted.task_id == receipt.task.task_id), None
            )
            if selected is None or (
                selected.messages[0]["content"] != receipt.question
                if self._inbox_research
                else selected.topic != topic
            ):
                return _unavailable()
        elif topic is not None:
            selected = next((o for o in originals if o.topic == topic), None)
            if selected is None:
                return {"state": "missing", "can_start": can_start, **labels}
        elif not originals:
            return {"state": "empty", "can_start": can_start, **labels}
        elif len(originals) == 2:
            return {"state": "ambiguous", "choices": list(_TOPICS), "can_start": [], **labels}
        else:
            selected = originals[0]
        response = IsolatedAcceptanceHost._inbox_status(
            selected.accepted, selected.snapshot, receipt
        )
        if isinstance(response, HTTPException):
            return response
        return {
            **response.model_dump(mode="json"),
            "topic": selected.topic,
            "can_start": can_start,
            **labels,
        }

    @staticmethod
    def _inbox_status(
        accepted: AcceptPrepare | None,
        snapshot: TaskSnapshot | None,
        receipt: _Submission | None,
    ) -> _InboxStatus | HTTPException:
        # Only called after the original singleton guard verifies both
        # commitments, the native identity/pair and final bearer ownership.
        if accepted is None and snapshot is None and receipt is None:
            return _InboxWithoutReport(state="empty")
        if accepted is None or snapshot is None:
            return _unavailable()
        if receipt is not None:
            task = receipt.task
            if (
                accepted.task_id,
                accepted.conversation_id,
                accepted.user_message_id,
                accepted.result_message_id,
            ) != (task.task_id, task.conversation_id, task.user_message_id, task.result_message_id):
                return _unavailable()
        state = snapshot.status.value
        if state == "completed":
            return _InboxCompleted(conversation_id=accepted.conversation_id)
        if state == "queued" or state == "running" or state == "cancelled":
            return _InboxWithoutReport(state=state)
        return _unavailable()

    @staticmethod
    def _ui_recovery() -> HTTPException:
        return HTTPException(
            409,
            {"code": "restore_recovery_required", "task_id": None, "retry_safe": False},
            headers=HEADERS,
        )

    @staticmethod
    def _ui_response(
        auth: AuthenticatedDevice,
        kind: str,
        ident: UUID | None,
        conv: Any,
        messages: list[Any],
        snapshot: Any,
        limit: int,
        offset: int,
        pagination: str | None,
        before: str | None,
        through: str | None,
        search: str | None,
        *,
        list_values: list[dict[str, Any]] | None = None,
    ) -> Any:
        if kind == "task":
            return (
                task_out(snapshot)
                if snapshot is not None and snapshot.task_id == ident
                else HTTPException(404, "Task not found", headers=HEADERS)
            )
        if kind == "conversation" and (conv is None or conv["id"] != ident):
            return HTTPException(404, "Conversation not found", headers=HEADERS)
        if pagination == "cursor" and (offset or (kind == "list" and search and search.strip())):
            return HTTPException(400, "Cursor mode cannot use offset or search", headers=HEADERS)
        if pagination == "cursor" and limit > 100:
            return HTTPException(422, "Cursor page limit must be at most 100", headers=HEADERS)
        if pagination != "cursor" and (before is not None or through is not None):
            return HTTPException(400, {"code": "invalid_cursor"}, headers=HEADERS)
        try:
            upper = (
                decode_cursor(
                    before,
                    user_id=auth.user_id,
                    conversation_id=ident if kind == "conversation" else None,
                )
                if before is not None
                else None
            )
            lower = (
                decode_cursor(through, user_id=auth.user_id, conversation_id=ident)
                if through is not None
                else None
            )
        except InvalidHistoryCursor:
            return HTTPException(400, {"code": "invalid_cursor"}, headers=HEADERS)
        if conv is not None:
            conv = ConversationOut.model_validate(_normalize_conversation(dict(conv)))
        if kind == "list":
            # Search is deliberately refused, not evaluated with incomplete SQL ILIKE semantics.
            if search and search.strip():
                return HTTPException(
                    400, "Search is unsupported by the isolated text fixture", headers=HEADERS
                )
            rows = (
                ([] if conv is None else [conv])
                if list_values is None
                else sorted(
                    (
                        ConversationOut.model_validate(_normalize_conversation(dict(c)))
                        for c in list_values
                    ),
                    key=lambda c: (c.pinned, c.last_activity_at, c.id),
                    reverse=True,
                )
            )
            total = len(rows)
            if upper is not None:
                rows = [
                    c
                    for c in rows
                    if (c.pinned, c.last_activity_at, c.id) < (upper.pinned, upper.at, upper.id)
                ]
            has_more = len(rows) > offset + limit
            rows = rows[offset : offset + limit]
            boundaries = []
            for c in rows:
                activity = c.last_activity_at
                if activity is None:
                    return IsolatedAcceptanceHost._ui_recovery()
                boundaries.append(
                    encode_cursor(HistoryBoundary(activity, c.id, c.pinned), user_id=auth.user_id)
                )
            page = (
                {
                    "has_more": has_more,
                    "next_cursor": boundaries[-1] if boundaries and has_more else None,
                    "start_cursor": boundaries[0] if boundaries else None,
                    "end_cursor": boundaries[-1] if boundaries else None,
                }
                if pagination == "cursor"
                else None
            )
            return ConversationListResponse(
                conversations=rows,
                total=total,
                page=HistoryPageOut.model_validate(page) if page is not None else None,
            )
        assert conv is not None
        selected = [
            m
            for m in reversed(messages)
            if (upper is None or (m["created_at"], m["id"]) < (upper.at, upper.id))
            and (lower is None or (m["created_at"], m["id"]) >= (lower.at, lower.id))
        ]
        has_more = len(selected) > offset + limit
        selected = list(reversed(selected[offset : offset + limit]))
        boundaries = [
            encode_cursor(
                HistoryBoundary(m["created_at"], m["id"]),
                user_id=auth.user_id,
                conversation_id=ident,
            )
            for m in selected
        ]
        page = (
            {
                "has_more": has_more,
                "next_cursor": boundaries[0] if boundaries and has_more else None,
                "start_cursor": boundaries[0] if boundaries else None,
                "end_cursor": boundaries[-1] if boundaries else None,
            }
            if pagination == "cursor"
            else None
        )
        task = task_out(snapshot)
        return ConversationWithMessagesOut(
            **conv.model_dump(),
            messages=[MessageOut.model_validate(_normalize_message(dict(m))) for m in selected],
            message_page=HistoryPageOut.model_validate(page) if page is not None else None,
            active_task=task if task.status in ("queued", "running") else None,
            latest_task=task,
        )

    async def _stream(
        self, authorization: str | None, client_key: str, initial: HistoricalObservation
    ) -> AsyncIterator[str]:
        current = asyncio.current_task()
        assert current is not None
        self._streams.add(current)
        try:
            async for frame in stream_with_keepalives(
                self._frames(authorization, client_key, initial), self._keepalive_s
            ):
                yield frame
        finally:
            self._streams.discard(current)

    async def _frames(
        self, authorization: str | None, client_key: str, initial: HistoricalObservation
    ) -> AsyncIterator[str]:
        try:
            snapshot = initial.snapshot
            assert snapshot is not None
            view = _Observation(f"conv_{snapshot.conversation_id}", new_request_id())
            yield view.task_frame(snapshot)
            yield view.frame(
                "conversation",
                {"conversation_id": str(snapshot.conversation_id)},
                evt_id="evt_conversation",
            )
            # Keep the acceptance-only stream contract even when an explicit
            # executor runs the task; never manufacture output or done frames.
            while not self._closing:
                await asyncio.sleep(2)
                observed = await self.lookup(
                    authorization, client_key, deadline=time.monotonic() + 5
                )
                if (
                    observed.kind != "task"
                    or observed.snapshot is None
                    or observed.snapshot.status.value != "queued"
                ):
                    return
        except Exception:
            return  # Transport ends; the original key still requires observation.

    async def retire_original(self, *, deadline: float) -> None:
        """Single trusted launcher operation, never an HTTP route or a replay."""
        self._admit(deadline)
        execution, receipt = self._execution, self._original_receipt
        reader = self._inbox_admission_reader
        projection = self._acceptance._projection
        if (
            self._inbox_child is None
            or self._inbox_child.handoff is not None
            or type(projection) is not InboxRetiredHistoryProjection
            or reader is None
            or execution is None
            or receipt is None
            or not receipt.task.created
            or self._active
            or self._streams
            or self._pending_submissions
            or self._reader._history._blocked
            or self._acceptance._blocked
            or self._acceptance._history._blocked
        ):
            raise HistoryError("inbox_retirement_refused")
        proof = execution.original_cleanup(receipt.task.task_id)
        # No yield between checking every entrant/physical job and closing
        # both entry points. A later failure may dispose, never undo this latch.
        self._closing = execution._closing = True
        self._ready = execution._ready = False
        record = uuid4()
        async with reader._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            async with pool_connection(execution.pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("inbox_retirement_refused")
                async with conn.transaction(isolation="read_committed"):
                    await projection.qualify(conn, writing=False, deadline=deadline)
                    xid = await conn.fetchval("SELECT txid_current()")
                    originals = await self._inbox_originals(
                        reader, operation, conn, xid, receipt.auth, deadline
                    )
                    if isinstance(originals, HTTPException) or len(originals[1]) != 1:
                        raise HistoryError("inbox_retirement_refused")
                    (x,) = originals[1]
                    accepted = x.accepted
                    if x.topic != "X" or (
                        accepted.task_id,
                        accepted.conversation_id,
                        accepted.user_message_id,
                        accepted.result_message_id,
                    ) != (
                        receipt.task.task_id,
                        receipt.task.conversation_id,
                        receipt.task.user_message_id,
                        receipt.task.result_message_id,
                    ):
                        raise HistoryError("inbox_retirement_refused")
                    entries = (await operation.verify()).entries
                    if len(entries) != 3:
                        raise HistoryError("inbox_retirement_refused")
                    _, sent, terminal = entries
                    send = sent.command.data
                    identity, settlement = proof.binding.identity, proof.settlement
                    if (
                        type(send) is not SendPossible
                        or sent.command.record_id != proof.send_record
                        or type(terminal.command.data) is not TerminalOutputPrepare
                        or send.reservation_id != settlement.reservation.id
                        or (
                            send.account_id,
                            send.account_generation,
                            send.task_id,
                            send.epoch,
                            send.lifecycle_id,
                            send.producer_instance_id,
                            send.financial_scope_id,
                            send.metadata_scope_id,
                        )
                        != (
                            identity.account_id,
                            identity.generation,
                            identity.task_id,
                            identity.epoch,
                            identity.lifecycle_id,
                            identity.producer_instance_id,
                            identity.financial_scope_id,
                            identity.metadata_scope_id,
                        )
                    ):
                        raise HistoryError("inbox_retirement_refused")
                    matches = await conn.fetchval(
                        "SELECT count(*)=1 AND bool_and(id=$2 AND user_id=$3 AND period_key=$4 "
                        "AND status='settled' AND actual_microusd=$5 AND overage_microusd=0) "
                        "FROM public.entitlement_reservations WHERE scope_id=$1",
                        identity.financial_scope_id,
                        send.reservation_id,
                        identity.account_id,
                        settlement.period_key,
                        settlement.actual_microusd,
                    )
                    if matches is not True:
                        raise HistoryError("inbox_retirement_refused")
                    self._inbox_child.check(self)
                    prepared = await operation.append(
                        Command(
                            record,
                            InboxOriginalRetired(terminal.command.record_id, proof.send_record),
                        )
                    )
                    if (
                        not prepared.created
                        or not (
                            await projection.stage_retired(conn, prepared.entry, deadline=deadline)
                        ).inserted
                    ):
                        raise HistoryError("inbox_retirement_uncertain")
        _check_time(deadline)
        self._retired_record = record

    def acknowledge_retirement(self) -> None:
        """Called by the pool owner only after both normal close awaits returned."""
        if (
            self._retired_record is None
            or self._inbox_child is None
            or not self._closed
            or self._execution is None
            or self._execution._blocked
            or self._execution.history._blocked
            or self._reader._history._blocked
            or self._acceptance._blocked
            or self._acceptance._history._blocked
            or not self._reader._tasks._pool._closed
            or not self._acceptance._tasks._pool._closed
        ):
            raise HistoryError("inbox_retirement_uncertain")
        self._inbox_child._acknowledge(self, self._retired_record)

    async def _shutdown(self, deadline: float) -> None:
        first: BaseException | None = None
        # Stream cancellation owns/drains its pending qualified read first.
        # Only then cancel remaining request owners, avoiding a second cancel
        # forwarded independently to the same physical SQL operation.
        for owners in (self._streams, self._active):
            children = tuple(owners)
            for child in children:
                if not child.done():
                    child.cancel()
            for child in children:
                try:
                    await _observe(child, deadline, cancel_body=False)
                except asyncio.CancelledError:
                    pass
                except BaseException as error:
                    if first is None:
                        first = error
        if self._execution is not None:
            # No H/pool close under an outstanding runner/native cleanup owner.
            try:
                await self._execution.aclose(deadline=deadline)
            except BaseException as error:
                # aclose drains its physical child even when its logical deadline
                # or a runner failure is reported. Other H owners still need close.
                if first is None:
                    first = error
                else:
                    first.add_note("Additional isolated execution cleanup: " + type(error).__name__)
        for owner in (self._reader._history, self._acceptance._history):
            try:
                await owner.aclose(deadline=deadline)
            except BaseException as error:
                if first is None:
                    first = error
                else:
                    first.add_note("Additional isolated H cleanup: " + type(error).__name__)
        if first is not None:
            raise first
        self._closed = True

    async def aclose(self, *, deadline: float) -> None:
        self._bind()
        if asyncio.current_task() in self._active | self._streams:
            raise HistoryError("isolated_host_operation_active")
        self._closing = True
        self._ready = False
        if self._shutdown_task is None:
            call = self._shutdown(deadline)
            try:
                self._shutdown_task = asyncio.create_task(call)
            except BaseException as original:
                try:
                    call.close()
                except BaseException as cleanup:
                    original.add_note(
                        "Unstarted isolated shutdown cleanup: " + type(cleanup).__name__
                    )
                self._retired_record = None
                raise
        try:
            await _observe(self._shutdown_task, deadline, cancel_body=False)
        except BaseException:
            # Physical shutdown may finish later. That cannot repair the
            # original owner's missing acknowledgement of this retirement.
            self._retired_record = None
            raise


def create_isolated_acceptance_app(host: IsolatedAcceptanceHost) -> FastAPI:
    """No global app, lifespan start, environment selection or general router."""
    if type(host) is not IsolatedAcceptanceHost:
        raise HistoryError("isolated_host_refused")
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        global_limit=host._max_body_bytes,
        route_limits={"/chat": host._max_body_bytes},
    )

    @app.middleware("http")
    async def no_store(request: Request, call_next: Any) -> Response:
        if host._closing or not host._ready:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": _unavailable().detail}, status_code=503, headers=HEADERS)
        response = await call_next(request)
        response.headers.update(HEADERS)
        return response

    @app.post("/chat")
    async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        authorization, key = (
            request.headers.get("Authorization"),
            request.headers.get("Idempotency-Key"),
        )
        deadline = time.monotonic() + 10
        try:
            submitted = await host.submit(authorization, key, payload, deadline=deadline)
            assert key is not None
            observed = await host.lookup(
                authorization, key, deadline=min(deadline, time.monotonic() + 5)
            )
            if (
                observed.kind != "task"
                or observed.snapshot is None
                or observed.snapshot.task_id != submitted.task.task_id
            ):
                raise _unavailable()
        except IdempotencyConflict:
            raise HTTPException(
                409,
                {
                    "code": "idempotency_conflict",
                    "message": "This request key was already used for a different request",
                },
                headers=HEADERS,
            ) from None
        except ConversationBusy as error:
            raise HTTPException(
                409,
                {
                    "code": "conversation_busy",
                    "message": "This conversation is still working on an earlier request",
                    "task_id": str(error.active_task_id),
                },
                headers=HEADERS,
            ) from None
        except TaskNotFound:
            raise HTTPException(404, "Conversation not found", headers=HEADERS) from None
        except HTTPException:
            raise
        except Exception:
            raise _unavailable() from None
        return StreamingResponse(
            host._stream(authorization, key, observed),
            media_type="text/event-stream",
            headers={
                **HEADERS,
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Daemon-Task-Id": str(submitted.task.task_id),
            },
        )

    @app.get("/tasks/by-key/{idempotency_key}", response_model=TaskOut)
    async def by_key(idempotency_key: str, request: Request, response: Response) -> TaskOut:
        response.headers.update(HEADERS)
        try:
            observed = await host.lookup(
                request.headers.get("Authorization"), idempotency_key, deadline=time.monotonic() + 5
            )
        except Exception:
            raise _unavailable() from None
        return historical_task_out(observed)

    if host._execution is not None:

        @app.post("/tasks/{task_id}/cancel", response_model=CancelOut)
        async def cancel(task_id: UUID, request: Request) -> CancelOut:
            try:
                outcome = await host.cancel(
                    request.headers.get("Authorization"), task_id, deadline=time.monotonic() + 5
                )
            except HTTPException:
                raise
            except Exception:
                raise _unavailable() from None
            if not outcome.accepted:
                raise HTTPException(409, {"code": "task_finished", "status": outcome.status.value})
            return CancelOut(
                id=task_id,
                status=outcome.status.value,
                cancel_requested=outcome.status.value == "running",
            )

    return app


def create_isolated_ui_app(host: IsolatedAcceptanceHost) -> FastAPI:
    """Explicit inert opt-in; default app and production startup are unchanged."""
    if type(host._reader._projection) not in TERMINAL_PROFILES:
        raise HistoryError("isolated_ui_refused")
    app = create_isolated_acceptance_app(host)

    async def read(
        request: Request,
        kind: _UIReadKind,
        ident: UUID | None,
        **bounds: Any,
    ) -> Any:
        try:
            return await host.ui_read(
                request.headers.get("Authorization"),
                kind,
                ident,
                deadline=time.monotonic() + 5,
                **bounds,
            )
        except HTTPException:
            raise
        except Exception:
            raise _unavailable() from None

    if host._inbox_research:

        @app.get("/tasks/inbox-pilot", response_model=None)
        async def research_status(request: Request, topic: _Topic | None = None) -> Any:
            return await read(request, "inbox", None, topic=topic)

        @app.post("/tasks/inbox-pilot", response_model=None)
        async def research_start(payload: _InboxResearchInput, request: Request) -> Any:
            try:
                return await host._submit_inbox_pilot(
                    request.headers.get("Authorization"),
                    request.headers.get("Idempotency-Key"),
                    payload,
                    deadline=time.monotonic() + 10,
                )
            except HTTPException:
                raise
            except Exception:
                raise _unavailable() from None

    elif host._inbox_two_jobs:

        @app.get("/tasks/inbox-pilot", response_model=None)
        async def two_job_status(request: Request, topic: _Topic | None = None) -> Any:
            return await read(request, "inbox", None, topic=topic)

        @app.post("/tasks/inbox-pilot", response_model=None)
        async def two_job_start(payload: _InboxTwoJobInput, request: Request) -> Any:
            try:
                return await host._submit_inbox_pilot(
                    request.headers.get("Authorization"),
                    request.headers.get("Idempotency-Key"),
                    payload,
                    deadline=time.monotonic() + 10,
                )
            except HTTPException:
                raise
            except Exception:
                raise _unavailable() from None

    elif host._inbox_pilot:

        @app.get("/tasks/inbox-pilot")
        async def inbox_status(request: Request) -> _InboxStatus:
            return await read(request, "inbox", None)

        @app.post("/tasks/inbox-pilot")
        async def inbox_start(payload: _InboxPilotInput, request: Request) -> _InboxStatus:
            try:
                return await host._submit_inbox_pilot(
                    request.headers.get("Authorization"),
                    request.headers.get("Idempotency-Key"),
                    payload,
                    deadline=time.monotonic() + 10,
                )
            except HTTPException:
                raise
            except Exception:
                raise _unavailable() from None

    @app.get("/tasks/{task_id}", response_model=TaskOut)
    async def task(task_id: UUID, request: Request) -> Any:
        return await read(request, "task", task_id)

    @app.get("/conversations", response_model=ConversationListResponse)
    async def listing(
        request: Request,
        search: str | None = None,
        limit: int = Query(100, ge=1, le=100),
        offset: int = Query(0, ge=0),
        pagination: Literal["cursor"] | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await read(
            request,
            "list",
            None,
            search=search,
            limit=limit,
            offset=offset,
            pagination=pagination,
            before=cursor,
        )

    @app.get("/conversations/{conversation_id}", response_model=ConversationWithMessagesOut)
    async def conversation(
        conversation_id: UUID,
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        pagination: Literal["cursor"] | None = None,
        before: str | None = None,
        through: str | None = None,
    ) -> Any:
        return await read(
            request,
            "conversation",
            conversation_id,
            limit=limit,
            offset=offset,
            pagination=pagination,
            before=before,
            through=through,
        )

    return app
