"""Conversation API routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import uuid

from orchestrator.db import get_app_state, AppState

router = APIRouter(prefix="/conversations", tags=["conversations"])

DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


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


class ConversationWithMessagesOut(ConversationOut):
    messages: list[MessageOut]


class ConversationListResponse(BaseModel):
    conversations: list[ConversationOut]
    total: int


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


@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(
    conversation: ConversationCreate,
    app_state: AppState = Depends(get_app_state),
):
    """Create a new conversation."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    new_conv = await store.create_conversation(
        user_id=DEFAULT_USER_ID,
        title=conversation.title or "New conversation",
        pipeline="cloud",
    )
    new_conv = _normalize_conversation(new_conv)
    return new_conv


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    search: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    app_state: AppState = Depends(get_app_state),
):
    """List conversations with pagination."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    conversations = await store.list_conversations(
        user_id=DEFAULT_USER_ID,
        limit=limit,
        offset=offset,
        search=search,
    )
    conversations = [_normalize_conversation(c) for c in conversations]
    return {"conversations": conversations, "total": len(conversations)}


@router.get("/{conversation_id}", response_model=ConversationWithMessagesOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    app_state: AppState = Depends(get_app_state),
):
    """Get conversation with messages."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    conversation = await store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await store.get_messages(conversation_id, limit=limit, offset=offset)
    conversation = _normalize_conversation(conversation)
    messages = [_normalize_message(m) for m in messages]
    return {**conversation, "messages": messages}


@router.patch("/{conversation_id}", response_model=StatusResponse)
async def update_conversation(
    conversation_id: uuid.UUID,
    update: ConversationUpdate,
    app_state: AppState = Depends(get_app_state),
):
    """Update conversation fields."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
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
):
    """Delete conversation."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    deleted = await store.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}
