"""Durable task API (docs/DURABLE_REQUEST_DESIGN.md §7–§8, §11).

Every route is scoped to the authenticated account in SQL. A task owned by
another account is reported exactly like a missing one (404).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import AppState, get_app_state
from orchestrator.config import get_settings
from orchestrator.daemon import new_request_id, stream_with_keepalives
from orchestrator.request_id import get_request_id
from orchestrator.tasks.observe import observe_task
from orchestrator.tasks.store import TaskNotFound, TaskSnapshot, TaskStore

router = APIRouter(prefix="/tasks", tags=["tasks"])

#: Shape of an accepted Idempotency-Key (mirrors the /chat adapter).
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class OperationOut(BaseModel):
    """A material action the task started, for deciding whether to retry.

    ``target`` holds only the non-content summary recorded before the call
    (tool, destination fields); never bodies or credentials.
    """

    tool: str
    outcome: str
    started_at: datetime
    completed_at: datetime | None
    target: dict[str, Any]


class TaskOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    result_message_id: uuid.UUID
    status: str
    terminal_code: str | None
    attempt_count: int
    content_generation: int
    content_delta_seq: int
    content: str
    event_seq: int
    cancel_requested: bool
    # Filled on GET /tasks/{id}; empty where tasks are embedded elsewhere.
    operations: list[OperationOut] = []


class CancelOut(BaseModel):
    id: uuid.UUID
    status: str
    cancel_requested: bool


#: Header a client sends to declare optional task-stream features it supports.
CLIENT_FEATURES_HEADER = "X-Daemon-Client-Features"


def _client_features(request: Request) -> set[str]:
    features = request.headers.get(CLIENT_FEATURES_HEADER, "")
    return {item.strip() for item in features.split(",") if item.strip()}


def client_supports_reset(request: Request) -> bool:
    """Whether the client can replace text on a task generation reset.

    Clients that do not declare it (for example a cached older PWA) are told
    to reload instead, rather than shown regenerated text appended to the
    interrupted attempt's.
    """
    return "task-reset" in _client_features(request)


#: What a browser must declare for its chat turns to run durably.
DURABLE_CLIENT_FEATURES = frozenset({"task-cancel", "task-reset"})


def client_supports_durable_chat(request: Request) -> bool:
    """Whether this browser can drive a durable task correctly.

    It must cancel explicitly on Stop (a disconnect only detaches durable
    work) and replace text on a generation reset. The declaration comes from
    the browser's own code, forwarded by the chat proxy; an older cached
    client that does not declare it keeps request-bound chat, where aborting
    the request is the cancellation.
    """
    return DURABLE_CLIENT_FEATURES <= _client_features(request)


def observer_authorizer(
    app_state: AppState, auth: AuthenticatedDevice
) -> Callable[[], Awaitable[bool]]:
    """Re-checks a long-lived observer's credentials (fails closed).

    The task is account-owned and keeps running; only this device's view
    ends when its session or device is revoked or its access has expired.
    """
    pool = app_state.db_pool

    async def still_authorized() -> bool:
        if pool is None:
            return False
        try:
            return bool(
                await pool.fetchval(
                    """
                    SELECT s.revoked_at IS NULL AND d.revoked_at IS NULL
                           AND s.access_expires_at > now()
                    FROM sessions s JOIN devices d ON d.id = s.device_id
                    WHERE s.id = $1 AND s.device_id = $2 AND s.user_id = $3
                    """,
                    auth.session_id,
                    auth.device_id,
                    auth.user_id,
                )
            )
        except Exception:
            return False

    return still_authorized


def task_store(app_state: AppState) -> TaskStore:
    if app_state.db_pool is None or app_state.memory_store is None:
        raise HTTPException(status_code=503, detail="Task store unavailable")
    memory = app_state.memory_store
    return TaskStore(app_state.db_pool, memory.encryption, memory)


def task_out(snapshot: TaskSnapshot) -> TaskOut:
    return TaskOut(
        id=snapshot.task_id,
        conversation_id=snapshot.conversation_id,
        result_message_id=snapshot.result_message_id,
        status=snapshot.status.value,
        terminal_code=snapshot.terminal_code,
        attempt_count=snapshot.attempt_count,
        content_generation=snapshot.content_generation,
        content_delta_seq=snapshot.content_delta_seq,
        content=snapshot.content,
        event_seq=snapshot.event_seq,
        cancel_requested=snapshot.cancel_requested,
    )


@router.get("/by-key/{idempotency_key}", response_model=TaskOut)
async def get_task_by_key(
    idempotency_key: str,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> TaskOut:
    """The caller's task created with this idempotency key.

    Lets a client that stopped or lost a request before learning the task id
    find out whether the server accepted it (404: it did not, or not yet).
    """
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise HTTPException(status_code=404, detail="Task not found")
    snapshot = await task_store(app_state).task_for_key(auth.user_id, idempotency_key)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task_out(snapshot)


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> TaskOut:
    store = task_store(app_state)
    snapshot = await store.snapshot(auth.user_id, task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    out = task_out(snapshot)
    out.operations = [
        OperationOut(**operation) for operation in await store.operations(auth.user_id, task_id)
    ]
    return out


@router.get("/{task_id}/events")
async def observe(
    task_id: uuid.UUID,
    request: Request,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> StreamingResponse:
    store = task_store(app_state)
    if await store.snapshot(auth.user_id, task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    frames = observe_task(
        store,
        app_state.redis,
        auth.user_id,
        task_id,
        request_id=get_request_id(request) or new_request_id(),
        authorized=observer_authorizer(app_state, auth),
        supports_reset=client_supports_reset(request),
    )
    return StreamingResponse(
        stream_with_keepalives(frames, get_settings().sse_keepalive_interval_s),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Daemon-Task-Id": str(task_id),
        },
    )


@router.post("/{task_id}/cancel", response_model=CancelOut)
async def cancel(
    task_id: uuid.UUID,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> CancelOut:
    try:
        outcome = await task_store(app_state).request_cancel(auth.user_id, task_id)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    if not outcome.accepted:
        raise HTTPException(
            status_code=409,
            detail={"code": "task_finished", "status": outcome.status.value},
        )
    return CancelOut(
        id=task_id,
        status=outcome.status.value,
        cancel_requested=outcome.status.value == "running",
    )
