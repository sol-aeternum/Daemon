"""Memory API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import uuid
from typing import Any, Literal

from orchestrator.auth import (
    AuthenticatedDevice,
    AdminOrDeviceAuth,
    require_admin_or_device_auth,
    require_device_auth,
)
from orchestrator.db import get_app_state, AppState
from orchestrator.memory.embedding import embed_documents_with_metadata

router = APIRouter(prefix="/memories", tags=["memories"])


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
    status: Literal[
        "active",
        "pending",
        "superseded",
        "inactive",
        "rejected",
        "deleted",
    ] = "active"
    memory_ids: list[uuid.UUID] | None = None
    batch_size: int = 50


@router.get("")
async def list_memories(
    category: str | None = None,
    confirmed: bool | None = None,
    search: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """List memories with optional filters."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    memories = await store.list_memories(
        user_id=auth.user_id,
        category=category,
        confirmed=confirmed,
        search=search,
        include_local=True,
        limit=limit,
        offset=offset,
    )

    return {"memories": memories, "total": len(memories)}


@router.post("/export")
async def export_memories(
    data: MemoryExportRequest,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    memories = await store.export_memories(auth.user_id, status=data.status)
    return {"memories": memories}


@router.post("/import")
async def import_memories(
    data: MemoryImportRequest,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    inserted = await store.import_memories(auth.user_id, data.memories)
    return {"inserted": inserted}


@router.post("/reembed")
async def reembed_memories(
    data: MemoryReembedRequest,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    missing_ids = 0
    if data.memory_ids:
        memories: list[dict[str, Any]] = []
        for memory_id in data.memory_ids:
            memory = await store.get_memory(memory_id)
            if memory and memory.get("user_id") == auth.user_id:
                memories.append(memory)
            else:
                missing_ids += 1
    else:
        memories = await store.export_memories(auth.user_id, status=data.status)

    requested = len(data.memory_ids) if data.memory_ids else len(memories)
    if not memories:
        return {
            "requested": requested,
            "found": 0,
            "updated": 0,
            "skipped_empty": 0,
            "missing_ids": missing_ids,
            "status": data.status,
        }

    batch_size = max(1, min(data.batch_size, 200))
    updated = 0
    skipped_empty = 0

    for idx in range(0, len(memories), batch_size):
        batch = memories[idx : idx + batch_size]
        valid_batch: list[dict[str, Any]] = []
        valid_texts: list[str] = []
        for mem in batch:
            text = str(mem.get("content") or "").strip()
            if not text:
                skipped_empty += 1
                continue
            valid_batch.append(mem)
            valid_texts.append(text)

        if not valid_texts:
            continue

        embedding_result = await embed_documents_with_metadata(valid_texts)

        for mem, embedding in zip(valid_batch, embedding_result.embeddings):
            await store.update_memory_embedding(
                mem["id"],
                embedding,
                embedding_model=embedding_result.storage_model,
            )
            updated += 1

    return {
        "requested": requested,
        "found": len(memories),
        "updated": updated,
        "skipped_empty": skipped_empty,
        "missing_ids": missing_ids,
        "status": data.status,
    }


@router.delete("")
async def delete_all_memories(
    hard: bool = False,
    confirm: bool = False,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Delete all memories for the authenticated user. Requires confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass confirm=true to delete all memories",
        )
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    deleted = await store.delete_all_memories(auth.user_id, hard=hard)
    return {"deleted": deleted, "hard": hard}


@router.get("/{memory_id}")
async def get_memory(
    memory_id: uuid.UUID,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Get single memory."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    memory = await store.get_memory(memory_id)

    if not memory or memory.get("user_id") != auth.user_id:
        raise HTTPException(status_code=404, detail="Memory not found")

    return memory


@router.post("")
async def create_memory(
    data: MemoryCreate,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Create new memory."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    from orchestrator.memory.dedup import dedup_and_store

    memory_id = await dedup_and_store(
        store=store,
        user_id=auth.user_id,
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
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Update memory content."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    existing = await store.get_memory(memory_id)
    if not existing or existing.get("user_id") != auth.user_id:
        raise HTTPException(status_code=404, detail="Memory not found")
    await store.update_memory(memory_id, content=data.content)
    return {"status": "updated"}


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: uuid.UUID,
    hard: bool = False,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Delete memory (soft or hard)."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    existing = await store.get_memory(memory_id)
    if not existing or existing.get("user_id") != auth.user_id:
        raise HTTPException(status_code=404, detail="Memory not found")
    await store.delete_memory(memory_id, soft=not hard)
    return {"status": "deleted", "hard": hard}


@router.post("/{memory_id}/confirm")
async def confirm_memory(
    memory_id: uuid.UUID,
    data: MemoryConfirm,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Confirm or reject a memory."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    existing = await store.get_memory(memory_id)
    if not existing or existing.get("user_id") != auth.user_id:
        raise HTTPException(status_code=404, detail="Memory not found")
    confirmed = data.status == "confirmed"
    await store.confirm_memory(memory_id, confirmed=confirmed)
    return {"status": data.status}


class ConsolidateRequest(BaseModel):
    user_id: uuid.UUID | None = None


class DreamRequest(BaseModel):
    user_id: uuid.UUID | None = None


@router.post("/consolidate")
async def consolidate_memories_endpoint(
    data: ConsolidateRequest | None = None,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Manually trigger memory consolidation for a user or all users.

    This endpoint enqueues a consolidation job that will:
    - Find clusters of related L1 memories
    - Synthesize them into summary memories
    - Demote source memories to tier L2 (not deleted)

    If no user_id is provided, processes all users with eligible memories.

    Returns immediately with job status; actual consolidation runs async.
    """
    if app_state.redis is None:
        raise HTTPException(
            status_code=503,
            detail="Redis unavailable - cannot enqueue consolidation job",
        )

    target_user_id = auth.user_id

    try:
        # Enqueue the consolidation job
        job = await app_state.redis.enqueue_job(
            "consolidate_memories",
            str(target_user_id),
            _job_id=f"consolidate:{target_user_id or 'all'}:{uuid.uuid4().hex[:8]}",
        )

        # Handle None return from enqueue_job
        if job is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to enqueue consolidation job: returned None",
            )

        return {
            "status": "enqueued",
            "job_id": job.job_id,
            "user_id": str(target_user_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue consolidation job: {e}")


@router.post("/dream")
async def dream_memories_endpoint(
    data: DreamRequest | None = None,
    app_state: AppState = Depends(get_app_state),
    auth: AdminOrDeviceAuth = Depends(require_admin_or_device_auth),
):
    """Admin/debug endpoint to enqueue a dreaming run for one user or all users.

    Authorization rules:
    - Admin API key: may specify any user_id, or omit it to target all users.
    - Device auth: may only target auth.user_id (own user). If user_id is omitted,
      defaults to auth.user_id. Requesting a different user's ID returns 403.
    """

    if app_state.redis is None:
        raise HTTPException(
            status_code=503,
            detail="Redis unavailable - cannot enqueue dreaming job",
        )

    device = auth.authenticated_device

    if auth.is_admin:
        target_user_id = data.user_id if data else None
    else:
        requested_user_id = data.user_id if data else None
        if requested_user_id is not None and requested_user_id != device.user_id:
            raise HTTPException(
                status_code=403,
                detail="Device auth cannot target another user",
            )
        target_user_id = device.user_id

    try:
        job = await app_state.redis.enqueue_job(
            "run_dreaming_job",
            str(target_user_id) if target_user_id else None,
            _job_id=f"dream:{target_user_id or 'all'}:{uuid.uuid4().hex[:8]}",
        )
        if job is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to enqueue dreaming job: returned None",
            )

        return {
            "status": "enqueued",
            "job_id": job.job_id,
            "user_id": str(target_user_id) if target_user_id else "all",
        }
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to enqueue dreaming job: {error}",
        )
