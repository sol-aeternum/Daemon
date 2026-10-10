"""Memory API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from collections.abc import AsyncGenerator

from orchestrator.auth import (
    AuthenticatedDevice,
    AdminOrDeviceAuth,
    require_admin_or_device_auth,
    require_device_auth,
)
from orchestrator.db import get_app_state, AppState
from orchestrator.memory.embedding import (
    EmbeddingBatchResult,
    EmbeddingConfigurationError,
    EmbeddingRequestError,
    embed_documents_with_metadata,
    get_selected_embedding_route_id,
    raise_if_embedding_accounting_error,
)
from orchestrator.compute_runtime import account_compute, current_scope, ComputeUnavailable
from orchestrator.memory.store import MemoryContentConflictError, compute_memory_content_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories", tags=["memories"])

MAX_IMPORT_ITEMS = 500
MAX_IMPORT_CONTENT_CHARS = 2000
IMPORT_EMBED_BATCH = 50
ImportCategory = Literal["fact", "preference", "project", "summary", "correction"]


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    category: str = "fact"

    @field_validator("content", mode="before")
    @classmethod
    def _strip_content(cls, value: Any) -> Any:
        # Blank or whitespace-only text is rejected (422) before any
        # embedding, dedup or write.
        return value.strip() if isinstance(value, str) else value


MAX_EDIT_CONTENT_CHARS = 2000


class MemoryUpdate(BaseModel):
    """A person's correction: new text and, optionally, a new category."""

    content: str = Field(min_length=1, max_length=MAX_EDIT_CONTENT_CHARS)
    category: Literal["fact", "preference", "project", "summary", "correction"] | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _strip_content(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class MemoryConfirm(BaseModel):
    status: Literal["confirmed", "rejected"]


class MemoryExportRequest(BaseModel):
    status: str = "active"


class ImportedMemory(BaseModel):
    """One person-supplied memory. Only text and category are accepted.

    Extra fields (for example dates from an export file) are ignored. Status,
    source, locality, confidence, slots and embeddings are always set by the
    server, never by the request.
    """

    model_config = ConfigDict(extra="ignore")

    content: str = Field(min_length=1, max_length=MAX_IMPORT_CONTENT_CHARS)
    category: ImportCategory = "fact"

    @field_validator("content", mode="before")
    @classmethod
    def _strip_content(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


@dataclass(frozen=True)
class _ImportFact:
    """Dedup input with the same defaults as a single POST /memories write."""

    content: str
    category: str
    confidence: float = 0.8
    slot: str | None = None


class MemoryImportRequest(BaseModel):
    memories: list[ImportedMemory] = Field(max_length=MAX_IMPORT_ITEMS)


async def _embedding_account_scope(
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> AsyncGenerator[None, None]:
    """Outer scope for HTTP writers, never a scope created inside the adapter."""
    if not get_selected_embedding_route_id() or app_state.memory_store is None:
        yield
        return
    try:
        active = current_scope()
    except ComputeUnavailable:
        active = None
    if active is not None:
        if active.user_id != auth.user_id:
            raise HTTPException(status_code=503, detail="Account compute unavailable")
        yield
        return
    async with account_compute(app_state.memory_store._pool, auth.user_id, operation="chat"):
        yield


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


# Browser status filter. "all" is every status except deleted: exactly the rows
# DELETE /memories (soft) would mark deleted.
ListStatus = Literal["active", "superseded", "rejected", "pending", "all"]
NON_DELETED_STATUSES = ["active", "pending", "superseded", "inactive", "rejected"]
# Stored provenance values a person can filter by.
ListSource = Literal["extracted", "user_created", "import"]


@router.get("")
async def list_memories(
    category: ImportCategory | None = None,
    status: ListStatus = "active",
    source_type: ListSource | None = None,
    confirmed: bool | None = None,
    search: str | None = Query(None, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """List memories with optional filters and truthful paging metadata.

    ``total`` counts every row matching the same filters (not the page), and
    ``has_more`` says whether rows remain after this page.
    """
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    filters: dict[str, Any] = {
        "category": category,
        "status": NON_DELETED_STATUSES if status == "all" else status,
        "source_type": source_type,
        "confirmed": confirmed,
        "search": search or None,
        "include_local": True,
    }
    memories = await store.list_memories(
        user_id=auth.user_id,
        limit=limit,
        offset=offset,
        **filters,
    )
    total = await store.count_listed_memories(user_id=auth.user_id, **filters)
    return {
        "memories": memories,
        "total": total,
        "has_more": offset + len(memories) < total,
        "limit": limit,
        "offset": offset,
    }


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


@router.post("/import", dependencies=[Depends(_embedding_account_scope)])
async def import_memories(
    data: MemoryImportRequest,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Import memories through the same dedup path as other writes.

    Every item is stored active, with source ``import``, embedded by the
    server outside any transaction. Each item's dedup decision and writes then
    commit in their own transaction, and an item is counted only once it has
    committed, so a failure part-way reports exactly what is persisted: every
    counted item is saved, and nothing from the failing item is. Contradiction
    checks and trust signals run after commit, as for the memory tool. Like
    POST /memories, this explicit person-initiated route is outside the LLM
    tool's per-window quota (issue #221) but is bounded per request.
    """
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    from orchestrator.memory.dedup import _embedding_text, deduplicate_facts
    from orchestrator.memory.tools import apply_deferred_supersede_effects

    counts = {"created": 0, "merged": 0, "superseded": 0}
    processed = 0
    items = data.memories

    def stopped() -> HTTPException:
        logger.warning("Memory import stopped after %d of %d", processed, len(items), exc_info=True)
        return HTTPException(
            status_code=503,
            detail={
                "message": "Import stopped because a memory service was unavailable.",
                "received": len(items),
                "processed": processed,
                **counts,
            },
        )

    for start in range(0, len(items), IMPORT_EMBED_BATCH):
        batch = items[start : start + IMPORT_EMBED_BATCH]
        prepared: list[Any] | None
        try:
            embedded = await embed_documents_with_metadata(
                [_embedding_text(item.content, None) for item in batch]
            )
            prepared = [
                EmbeddingBatchResult(
                    embeddings=[vector],
                    provider=embedded.provider,
                    model=embedded.model,
                    storage_model=embedded.storage_model,
                )
                for vector in embedded.embeddings
            ]
            if len(prepared) != len(batch):
                raise RuntimeError("embedding count does not match import batch")
        except EmbeddingConfigurationError:
            # Dedup falls back to exact-match checks when embeddings are
            # unqualified, as for any other write.
            prepared = None
        except Exception as error:
            raise_if_embedding_accounting_error(error)
            raise stopped() from None

        for index, item in enumerate(batch):
            try:
                async with store._pool.acquire() as conn:
                    async with conn.transaction():
                        result = await deduplicate_facts(
                            store=store,
                            user_id=auth.user_id,
                            facts=[_ImportFact(item.content, item.category)],
                            conversation_id=None,
                            source_type="import",
                            status="active",
                            lock_conn=conn,
                            prepared_embeddings=[prepared[index]] if prepared else None,
                        )
                        outcomes = len(result.new) + len(result.merged) + len(result.superseded)
                        if outcomes != 1:
                            # Every processed item must be classified exactly
                            # once; roll back rather than report an unaccounted item.
                            raise RuntimeError(f"import item produced {outcomes} outcomes")
            except Exception:
                raise stopped() from None
            counts["created"] += len(result.new)
            counts["merged"] += len(result.merged)
            counts["superseded"] += len(result.superseded)
            processed += 1
            await apply_deferred_supersede_effects(store, result.deferred_supersede_effects)

    return {
        "received": len(items),
        "processed": processed,
        "inserted": counts["created"],
        **counts,
    }


@router.post("/reembed", dependencies=[Depends(_embedding_account_scope)])
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
            if mem.get("local_only") is not False:
                continue
            text = str(mem.get("content") or "").strip()
            if not text:
                skipped_empty += 1
                continue
            valid_batch.append(mem)
            valid_texts.append(text)

        if not valid_texts:
            continue

        try:
            embedding_result = await embed_documents_with_metadata(valid_texts)
        except EmbeddingConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "route_unavailable",
                    "message": "Approved embedding route unavailable",
                },
            ) from exc

        for mem, embedding in zip(valid_batch, embedding_result.embeddings):
            # Applies only if the memory still holds the text that was
            # embedded; an edit in the meantime keeps its own vector.
            applied = await store.update_memory_embedding(
                mem["id"],
                embedding,
                embedding_model=embedding_result.storage_model,
                expected_content_hash=compute_memory_content_hash(str(mem.get("content") or "")),
            )
            updated += int(applied)

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


@router.post("", dependencies=[Depends(_embedding_account_scope)])
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

    # Intentional cap semantics: this authenticated administrative HTTP
    # endpoint predates and is outside the LLM tool's per-window/active-row
    # abuse quota. It retains deduplication but does not participate in the
    # tool-only cap lock; bulk import below is likewise an explicit admin
    # operation. Issue #221's atomic cap applies to MemoryWriteTool writes.
    try:
        memory_id = await dedup_and_store(
            store=store,
            user_id=auth.user_id,
            content=data.content,
            source_type="user_created",
            category=data.category,
            conversation_id=None,
        )
    except EmbeddingRequestError as exc:
        raise HTTPException(status_code=503, detail="Memory embedding service unavailable") from exc

    return {"id": str(memory_id), "status": "created"}


@router.patch("/{memory_id}", dependencies=[Depends(_embedding_account_scope)])
async def update_memory(
    memory_id: uuid.UUID,
    data: MemoryUpdate,
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Correct a memory's text (and optionally its category); returns the memory.

    New text is embedded before the write and stored with its vector in one
    update. If embeddings are unqualified or the provider fails, the write
    clears the vector instead, so retrieval can never keep matching the old
    text through an obsolete embedding. The write is conditional on the
    content hash this request read; if another edit landed first, it returns
    412 rather than overwrite it.
    """
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    existing = await store.get_memory(memory_id)
    if not existing or existing.get("user_id") != auth.user_id:
        raise HTTPException(status_code=404, detail="Memory not found")

    content_changed = data.content != existing.get("content")
    embedding: list[float] | None = None
    embedding_model: str | None = None
    if content_changed and existing.get("local_only") is False:
        from orchestrator.memory.dedup import _embedding_text

        try:
            embedded = await embed_documents_with_metadata(
                [_embedding_text(data.content, existing.get("memory_slot"))]
            )
            if len(embedded.embeddings) == 1:
                embedding = embedded.embeddings[0]
                embedding_model = embedded.storage_model
        except EmbeddingConfigurationError:
            pass
        except Exception as error:
            raise_if_embedding_accounting_error(error)
            logger.warning(
                "Re-embedding an edited memory failed; clearing its vector", exc_info=True
            )

    try:
        updated = await store.update_memory_content(
            memory_id,
            data.content,
            embedding=embedding,
            embedding_model=embedding_model,
            clear_embedding=content_changed and embedding is None,
            category=data.category,
            user_id=auth.user_id,
            # Write only if the text is still what this request read, so a
            # concurrent edit is never overwritten or paired with this
            # request's vector decision.
            require_content_hash=True,
            expected_content_hash=existing.get("content_hash"),
        )
    except MemoryContentConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Memory content duplicates an existing active memory",
        ) from exc
    if updated is None:
        current = await store.get_memory(memory_id)
        if current and current.get("user_id") == auth.user_id:
            raise HTTPException(
                status_code=412,
                detail="Memory changed since it was read; reload it and try again",
            )
        raise HTTPException(status_code=404, detail="Memory not found")
    return updated


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
    try:
        await store.confirm_memory(memory_id, confirmed=confirmed)
    except MemoryContentConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Memory content duplicates an existing active memory",
        ) from exc
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
        from orchestrator.redis_jobs import enqueue_account_job

        job = await enqueue_account_job(
            app_state.redis,
            "consolidate_memories",
            str(target_user_id),
            user_id=target_user_id,
            job_id=f"consolidate:{uuid.uuid4().hex[:8]}",
            settings=app_state.settings,
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
        if target_user_id is None:
            job = await app_state.redis.enqueue_job(
                "run_dreaming_job", None, _job_id=f"dream:all:{uuid.uuid4().hex[:8]}"
            )
        else:
            from orchestrator.redis_jobs import enqueue_account_job

            job = await enqueue_account_job(
                app_state.redis,
                "run_dreaming_job",
                str(target_user_id),
                user_id=target_user_id,
                job_id=f"dream:{uuid.uuid4().hex[:8]}",
                settings=app_state.settings,
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
