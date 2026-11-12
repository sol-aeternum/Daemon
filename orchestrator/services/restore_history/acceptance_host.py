"""Explicit isolated authenticated acceptance host, never selected by startup.

Borrows actual A/W pools; owns two H coordinators and every admitted child.
The caller closes borrowed pools only after aclose has settled. No queue,
provider, native publication profile, worker, environment selector or recovery
writer is installed here. The chat stream observes queued acceptance metadata,
not an answer/progress/output pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
import math
import os
import time
from typing import Any, TypeVar
from uuid import UUID

import asyncpg
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

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
from orchestrator.routes.tasks import IDEMPOTENCY_KEY_PATTERN, TaskOut, historical_task_out
from orchestrator.services.account_control.guard import WriteGuardRefusal, capture_generation
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.session_cleanup import lock_session_cleanup
from orchestrator.tasks._invocation import block
from orchestrator.tasks.inputs import chat_request_fingerprint
from orchestrator.tasks.observe import _Observation
from orchestrator.tasks.store import (
    AcceptedTask,
    ConversationBusy,
    IdempotencyConflict,
    TaskNotFound,
    TaskStore,
)

from .composition import HistoryAcceptance
from .coordination import HistoryCoordinator
from .effects_projection import HistoryProjection
from .model import HistoryError
from .observation import HistoricalObservation, HistoryObservation
from .read_supervisor import _observe, qualify_read_history
from .store import _check_time
from .terminal_projection import TerminalHistoryProjection

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
        ):
            raise HistoryError("isolated_host_refused")
        self._acceptance, self._reader = acceptance, observation
        self._fingerprint_key = fingerprint_key
        self._max_body_bytes, self._keepalive_s = max_body_bytes, keepalive_s
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = self._ready = self._closing = self._closed = False
        self._active: set[asyncio.Task[Any]] = set()
        self._streams: set[asyncio.Task[Any]] = set()
        self._shutdown_task: asyncio.Task[None] | None = None
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
            or type(ap) not in (HistoryProjection, TerminalHistoryProjection)
            or type(ap) is not type(wp)
            or ap.profile != ("terminal-v3" if type(ap) is TerminalHistoryProjection else "h1")
            or ap.profile != wp.profile
            or acceptance._history.profile != ap.profile
            or observation._history.profile != wp.profile
            or acceptance._history._expected != ap.expected
            or observation._history._expected != wp.expected
            or ap.expected != wp.expected
            or ap.acceptor != wp.acceptor
            or ap.worker != wp.worker
            or (
                type(ap) is TerminalHistoryProjection
                and (type(wp) is not TerminalHistoryProjection or ap.publisher != wp.publisher)
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
        token = _extract_bearer_token(authorization)
        if token is None:
            raise HTTPException(401, "Missing or invalid authorization header", headers=HEADERS)
        if client_key is None or not IDEMPOTENCY_KEY_PATTERN.fullmatch(client_key):
            raise HTTPException(
                422, {"code": "invalid_idempotency_key", "message": "Invalid key"}, headers=HEADERS
            )
        value = payload.model_copy(deep=True)
        if (
            value.attachments
            or value.suggestion_id is not None
            or value.user_id is not None
            or value.temporal_scope is not None
            or any(type(m.get("content")) is not str for m in value.messages or [])
        ):
            raise _unavailable()
        result = await self._run(
            self._submit(token, client_key, value, deadline), deadline, writing=True
        )
        if isinstance(result, HTTPException):
            raise result
        _check_time(deadline)
        if self._acceptance._blocked or self._acceptance._history._blocked:
            raise _unavailable()
        return result

    async def _submit(
        self, token: str, client_key: str, payload: ChatRequest, deadline: float
    ) -> _Submission | HTTPException:
        async with (
            self._acceptance._history.operation(deadline=deadline) as operation,
            retain_pool_cleanup(),
        ):
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
                    try:
                        captured = await capture_generation(conn, auth.user_id)
                    except WriteGuardRefusal:
                        return _unavailable()
                    # No W account SHARE is held. The original native A guard
                    # must reject any subsequent generation/lifecycle change.
                    latest = next(
                        (m for m in reversed(payload.messages or []) if m.get("role") == "user"),
                        None,
                    )
                    message = ((latest["content"] if latest else "") or payload.message).strip()
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
                    )
                    task_input = {
                        "message": message,
                        "provider": payload.provider,
                        "disable_memory_write": bool(payload.disable_memory_write),
                    }
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
                    lookup, request = self._acceptance._request(options)
                    accepted = await self._acceptance._accept_owned(
                        operation, deadline, options, lookup, request
                    )
            # W auth transaction and A's actual reset have settled; outer scope
            # still owns W holder reset, filesystem work and H release.
        return _Submission(accepted, auth)

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
            # No execution graph is installed. Keep the existing accepted task
            # stream open, but never manufacture tokens, progress or done frames.
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
                raise
        await _observe(self._shutdown_task, deadline, cancel_body=False)


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

    return app
