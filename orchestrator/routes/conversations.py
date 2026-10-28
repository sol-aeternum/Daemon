"""Conversation API routes."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import uuid

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import get_app_state, AppState
from orchestrator.history_pagination import (
    HistoryBoundary,
    InvalidHistoryCursor,
    decode_cursor,
    encode_cursor,
)
from orchestrator.routes.tasks import TaskOut, task_out, task_store
from orchestrator.tasks.store import TaskNotFound

router = APIRouter(prefix="/conversations", tags=["conversations"])


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    content: str
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: list[object] = Field(default_factory=list)
    tool_results: list[object] = Field(default_factory=list)
    status: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    advisor_traces: dict[str, object] | None = None
    reasoning_text: str | None = None
    reasoning_duration_secs: int | None = None
    reasoning_model: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    pipeline: str
    title: str | None = None
    summary: str | None = None
    message_count: int = 0
    tokens_total: int = 0
    pinned: bool = False
    title_locked: bool = False
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime | None = None
    summary_updated_at: datetime | None = None


class HistoryPageOut(BaseModel):
    has_more: bool
    next_cursor: str | None
    start_cursor: str | None
    end_cursor: str | None


class ConversationWithMessagesOut(ConversationOut):
    messages: list[MessageOut]
    message_page: HistoryPageOut | None = None
    # The conversation's queued or running durable task, so another device can
    # reattach to it (docs/DURABLE_REQUEST_DESIGN.md §7).
    active_task: TaskOut | None = None
    # The conversation's most recent task, including a finished one, so
    # another device can find a cancelled or needs_attention outcome.
    latest_task: TaskOut | None = None


class ConversationListResponse(BaseModel):
    conversations: list[ConversationOut]
    total: int
    page: HistoryPageOut | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    title_locked: bool | None = None


class StatusResponse(BaseModel):
    status: str


def _normalize_conversation(conversation: dict[str, object]) -> dict[str, object]:
    if conversation.get("pinned") is None:
        conversation["pinned"] = False
    if conversation.get("title_locked") is None:
        conversation["title_locked"] = False
    return conversation


def _normalize_message(message: dict[str, object]) -> dict[str, object]:
    if message.get("tool_calls") is None:
        message["tool_calls"] = []
    if message.get("tool_results") is None:
        message["tool_results"] = []
    if message.get("metadata") is None:
        message["metadata"] = {}
    if message.get("reasoning_text") is None:
        message["reasoning_text"] = None
    if message.get("reasoning_duration_secs") is None:
        message["reasoning_duration_secs"] = None
    if message.get("reasoning_model") is None:
        message["reasoning_model"] = None
    return message


class ConversationCreate(BaseModel):
    title: str | None = None
    user_id: str | None = None  # Optional user ID for benchmark isolation


@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(
    conversation: ConversationCreate,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Create a new conversation."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    new_conv = await store.create_conversation(
        user_id=auth.user_id,
        title=conversation.title or "New conversation",
        pipeline="cloud",
    )
    new_conv = _normalize_conversation(new_conv)
    return new_conv


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    search: str | None = None,
    limit: int | None = Query(None, ge=1, le=100),
    offset: int = Query(0, ge=0),
    pagination: Literal["cursor"] | None = None,
    cursor: str | None = None,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """List conversations with pagination."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    if pagination == "cursor" and (offset or (search and search.strip())):
        raise HTTPException(status_code=400, detail="Cursor mode cannot use offset or search")
    if cursor is not None and pagination != "cursor":
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor"})
    try:
        boundary = decode_cursor(cursor, user_id=auth.user_id) if cursor is not None else None
    except InvalidHistoryCursor as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor"}) from exc
    conversations, total, has_more = await store.list_conversations_page(
        user_id=auth.user_id,
        limit=limit if limit is not None else (100 if pagination == "cursor" else 20),
        offset=offset,
        search=search,
        before=boundary,
    )
    conversations = [_normalize_conversation(c) for c in conversations]
    page = None
    if pagination == "cursor":
        boundaries = [
            encode_cursor(
                HistoryBoundary(conv.last_activity_at or conv.updated_at, conv.id, conv.pinned),
                user_id=auth.user_id,
            )
            for c in conversations
            for conv in [ConversationOut.model_validate(c)]
        ]
        page = {
            "has_more": has_more,
            "next_cursor": boundaries[-1] if boundaries and has_more else None,
            "start_cursor": boundaries[0] if boundaries else None,
            "end_cursor": boundaries[-1] if boundaries else None,
        }
    return {"conversations": conversations, "total": total, "page": page}


@router.get("/{conversation_id}", response_model=ConversationWithMessagesOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    pagination: Literal["cursor"] | None = None,
    before: str | None = None,
    through: str | None = None,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Get conversation with messages."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    conversation = await store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.get("user_id") != auth.user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if pagination == "cursor" and (offset or limit > 100):
        if limit > 100:
            raise HTTPException(status_code=422, detail="Cursor page limit must be at most 100")
        raise HTTPException(status_code=400, detail="Cursor mode cannot use offset")
    if pagination != "cursor" and (before is not None or through is not None):
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor"})
    message_page = None
    if pagination == "cursor":
        try:
            before_boundary = (
                decode_cursor(before, user_id=auth.user_id, conversation_id=conversation_id)
                if before is not None
                else None
            )
            through_boundary = (
                decode_cursor(through, user_id=auth.user_id, conversation_id=conversation_id)
                if through is not None
                else None
            )
        except InvalidHistoryCursor as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_cursor"}) from exc
        messages, has_more = await store.get_message_history_page(
            conversation_id,
            user_id=auth.user_id,
            limit=limit,
            before=before_boundary,
            through=through_boundary,
        )
        boundaries = [
            encode_cursor(
                HistoryBoundary(message.created_at, message.id),
                user_id=auth.user_id,
                conversation_id=conversation_id,
            )
            for m in messages
            for message in [MessageOut.model_validate(m)]
        ]
        message_page = {
            "has_more": has_more,
            "next_cursor": boundaries[0] if boundaries and has_more else None,
            "start_cursor": boundaries[0] if boundaries else None,
            "end_cursor": boundaries[-1] if boundaries else None,
        }
    else:
        messages = await store.get_messages(conversation_id, limit=limit, offset=offset)
    conversation = _normalize_conversation(conversation)
    messages = [_normalize_message(m) for m in messages]
    active_task = latest_task = None
    if app_state.db_pool is not None:
        tasks = task_store(app_state)
        latest = await tasks.latest_for_conversation(auth.user_id, conversation_id)
        if latest is not None:
            latest_task = task_out(latest)
            if latest.status.value in ("queued", "running"):
                active_task = latest_task
    return {
        **conversation,
        "messages": messages,
        "message_page": message_page,
        "active_task": active_task,
        "latest_task": latest_task,
    }


@router.patch("/{conversation_id}", response_model=StatusResponse)
async def update_conversation(
    conversation_id: uuid.UUID,
    update: ConversationUpdate,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Update conversation fields."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    existing = await store.get_conversation(conversation_id)
    if not existing or existing.get("user_id") != auth.user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    updated = await store.update_conversation(
        conversation_id,
        title=update.title,
        pinned=update.pinned,
        title_locked=update.title_locked,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "updated"}


@router.delete("/{conversation_id}", response_model=StatusResponse)
async def delete_conversation(
    conversation_id: uuid.UUID,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Delete conversation."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    existing = await store.get_conversation(conversation_id)
    if not existing or existing.get("user_id") != auth.user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if app_state.db_pool is not None:
        # Durable tasks: never delete a running task's evidence out from under
        # an in-flight action (docs/DURABLE_REQUEST_DESIGN.md §11).
        try:
            deleted, running_task = await task_store(app_state).delete_conversation(
                auth.user_id, conversation_id
            )
        except TaskNotFound as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        if running_task is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "task_running",
                    "message": "A request in this conversation is still stopping; try again shortly",
                    "task_id": str(running_task),
                },
            )
    else:
        deleted = await store.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}
