"""Durable task API (docs/DURABLE_REQUEST_DESIGN.md §7–§8, §11).

Every route is scoped to the authenticated account in SQL. A task owned by
another account is reported exactly like a missing one (404).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import AppState, get_app_state
from orchestrator.daemon import new_request_id
from orchestrator.request_id import get_request_id
from orchestrator.tasks.observe import observe_task
from orchestrator.tasks.store import TaskNotFound, TaskSnapshot, TaskStore

router = APIRouter(prefix="/tasks", tags=["tasks"])


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


class CancelOut(BaseModel):
    id: uuid.UUID
    status: str
    cancel_requested: bool


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


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> TaskOut:
    snapshot = await task_store(app_state).snapshot(auth.user_id, task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task_out(snapshot)


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
    )
    return StreamingResponse(
        frames,
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
