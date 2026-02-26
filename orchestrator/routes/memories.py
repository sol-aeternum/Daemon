"""Memory API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import uuid
from typing import Any, Literal

from orchestrator.db import get_app_state, AppState
from orchestrator.memory.embedding import DEFAULT_MODEL, embed_batch

router = APIRouter(prefix="/memories", tags=["memories"])

DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class MemoryCreate(BaseModel):
    content: str
    category: str = "fact"


class MemoryUpdate(BaseModel):
    content: str


class MemoryConfirm(BaseModel):
    status: Literal["confirmed", "rejected"]


class MemoryExportRequest(BaseModel):
    status: str = "active"


class MemoryImportRequest(BaseModel):
    memories: list[dict[str, Any]]


class MemoryReembedRequest(BaseModel):
    status: str = "active"
    memory_ids: list[uuid.UUID] | None = None
    batch_size: int = 50


@router.get("")
async def list_memories(
    category: str | None = None,
    confirmed: bool | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    app_state: AppState = Depends(get_app_state),
):
    """List memories with optional filters."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    memories = await store.list_memories(
        user_id=DEFAULT_USER_ID,
        category=category,
        confirmed=confirmed,
        include_local=True,
        limit=limit,
        offset=offset,
    )

    return {"memories": memories, "total": len(memories)}


@router.post("/export")
async def export_memories(
    data: MemoryExportRequest,
    app_state: AppState = Depends(get_app_state),
):
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    memories = await store.export_memories(DEFAULT_USER_ID, status=data.status)
    return {"memories": memories}


@router.post("/import")
async def import_memories(
    data: MemoryImportRequest,
    app_state: AppState = Depends(get_app_state),
):
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    inserted = await store.import_memories(DEFAULT_USER_ID, data.memories)
    return {"inserted": inserted}


@router.post("/reembed")
async def reembed_memories(
    data: MemoryReembedRequest,
    app_state: AppState = Depends(get_app_state),
):
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    if data.memory_ids:
        memories = []
        for memory_id in data.memory_ids:
            memory = await store.get_memory(memory_id)
            if memory:
                memories.append(memory)
    else:
        memories = await store.export_memories(DEFAULT_USER_ID, status=data.status)

    if not memories:
        return {"updated": 0}

    batch_size = max(1, min(data.batch_size, 200))
    updated = 0

    for idx in range(0, len(memories), batch_size):
        batch = memories[idx : idx + batch_size]
        texts = [mem.get("content", "") for mem in batch]
        embeddings = await embed_batch(texts, model=DEFAULT_MODEL)

        for mem, embedding in zip(batch, embeddings):
            await store.update_memory_embedding(
                mem["id"],
                embedding,
                embedding_model=DEFAULT_MODEL,
            )
            updated += 1

    return {"updated": updated}


@router.delete("")
async def delete_all_memories(
    hard: bool = False,
    confirm: bool = False,
    app_state: AppState = Depends(get_app_state),
):
    """Delete all memories for the default user. Requires confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass confirm=true to delete all memories",
        )
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    deleted = await store.delete_all_memories(DEFAULT_USER_ID, hard=hard)
    return {"deleted": deleted, "hard": hard}


@router.get("/{memory_id}")
async def get_memory(
    memory_id: uuid.UUID,
    app_state: AppState = Depends(get_app_state),
):
    """Get single memory."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    memory = await store.get_memory(memory_id)

    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    return memory


@router.post("")
async def create_memory(
    data: MemoryCreate,
    app_state: AppState = Depends(get_app_state),
):
    """Create new memory."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    from orchestrator.memory.dedup import dedup_and_store

    memory_id = await dedup_and_store(
        store=store,
        user_id=DEFAULT_USER_ID,
        content=data.content,
        source_type="user_created",
        category=data.category,
        conversation_id=None,
    )

    return {"id": str(memory_id), "status": "created"}


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: uuid.UUID,
    data: MemoryUpdate,
    app_state: AppState = Depends(get_app_state),
):
    """Update memory content."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    await store.update_memory(memory_id, content=data.content)
    return {"status": "updated"}


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: uuid.UUID,
    hard: bool = False,
    app_state: AppState = Depends(get_app_state),
):
    """Delete memory (soft or hard)."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    await store.delete_memory(memory_id, soft=not hard)
    return {"status": "deleted", "hard": hard}


@router.post("/{memory_id}/confirm")
async def confirm_memory(
    memory_id: uuid.UUID,
    data: MemoryConfirm,
    app_state: AppState = Depends(get_app_state),
):
    """Confirm or reject a memory."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    confirmed = data.status == "confirmed"
    await store.confirm_memory(memory_id, confirmed=confirmed)
    return {"status": data.status}
