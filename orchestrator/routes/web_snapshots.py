"""Owner-checked HTTP surface for conversation-scoped web reading snapshots.

Three routes under the owning conversation, all behind ``require_device_auth``:

- ``GET    /conversations/{conversation_id}/web-snapshots``
  Bounded metadata listing. Page bodies are never decrypted for this route.
- ``GET    /conversations/{conversation_id}/web-snapshots/{snapshot_id}/export``
  One retained snapshot as a bounded downloadable JSON document.
- ``DELETE /conversations/{conversation_id}/web-snapshots/{snapshot_id}``
  Explicit deletion.

Astra owns registering ``router`` in ``orchestrator/main.py``; this module
exposes the router plus ``create_web_snapshot_store`` / ``get_web_snapshot_store``
so the app wiring, the per-turn tool registry and tests can all obtain a store
through one seam.

Disclosure rules
----------------
The authenticated account and the path conversation are the *only* selectors.
A wrong owner, a wrong conversation, an unknown id and an expired snapshot all
produce the same 404 with the same generic detail, so the routes cannot be used
to probe which of those cases applied, and a valid snapshot id from another
account is indistinguishable from a nonexistent one.

No raw exception text, URL, page title, page content or ciphertext ever reaches
a response body or a log record: typed store errors are mapped to stable
generic details, unexpected failures fall through to the application's global
sanitizing handler, and the only server-side log lines this module adds carry
stable reason codes plus the random UUIDs already present in the request path.

The export is served as an ``attachment`` with an opaque UUID-only filename and
``Cache-Control: no-store`` so neither a browser nor an intermediary caches or
inline-renders retained page text.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import Settings, get_settings
from orchestrator.memory.encryption import ContentEncryption, EncryptionInitError
from orchestrator.services.web_snapshots import (
    EXPORT_HARD_CEILING_BYTES,
    WebSnapshot,
    WebSnapshotCapacityExceeded,
    WebSnapshotContentTooLarge,
    WebSnapshotError,
    WebSnapshotExpired,
    WebSnapshotIntegrityError,
    WebSnapshotMetadata,
    WebSnapshotNotFound,
    WebSnapshotOwnerMismatch,
    WebSnapshotStore,
    WebSnapshotValidationError,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["web-snapshots"])


# --------------------------------------------------------------------------
# Generic client-facing details. Never interpolate a stored value into one.
# --------------------------------------------------------------------------

_NOT_FOUND_DETAIL = "Snapshot not found"
_STORE_UNAVAILABLE_DETAIL = "Web snapshot storage unavailable"
_INTERNAL_DETAIL = (
    "An internal error occurred. Please retry or contact support with the request id."
)
_EXPORT_TOO_LARGE_DETAIL = "Snapshot export exceeds the maximum download size"
_INVALID_REQUEST_DETAIL = "Invalid snapshot request"

#: Export document contract. Bump with an explicit migration, never in place.
EXPORT_SCHEMA = "daemon.web_snapshot_export"
EXPORT_VERSION = 1


# --------------------------------------------------------------------------
# Store factory and FastAPI dependency
# --------------------------------------------------------------------------


def create_web_snapshot_store(
    pool: asyncpg.Pool,
    settings: Settings | None = None,
    encryption: ContentEncryption | None = None,
) -> WebSnapshotStore:
    """Build a store from the shared pool.

    This is the single construction seam for the app wiring, the per-turn tool
    registry and tests. A ``ContentEncryption`` may be supplied to share the
    process-wide instance; otherwise one is built from the supplied (or
    resolved) settings, exactly as ``orchestrator/db.py`` does for the memory
    store. A deployment with no key therefore raises ``EncryptionInitError``
    here and fails closed instead of storing plaintext.
    """
    resolved_settings = settings if settings is not None else get_settings()
    resolved_encryption = (
        encryption
        if encryption is not None
        else ContentEncryption(resolved_settings.daemon_encryption_key)
    )
    return WebSnapshotStore(pool, resolved_encryption, resolved_settings)


def _shared_store(request: Request) -> WebSnapshotStore | None:
    """Return an explicitly wired shared store when the app provides one.

    Read through ``getattr`` so this module never has to own ``AppState``'s
    field list: wiring a ``web_snapshot_store`` attribute onto the app state
    (or setting ``app.state.web_snapshot_store``) is picked up automatically,
    and its absence falls back to building one from the pool.
    """
    app_state = getattr(request.app.state, "app_state", None)
    for candidate in (
        getattr(request.app.state, "web_snapshot_store", None),
        getattr(app_state, "web_snapshot_store", None),
    ):
        if isinstance(candidate, WebSnapshotStore):
            return candidate
    return None


async def get_web_snapshot_store(request: Request) -> WebSnapshotStore:
    """FastAPI dependency resolving the request's snapshot store.

    Override this dependency in tests to inject a store bound to an isolated
    schema. Raises 503 (never a raw error) when the deployment has no database
    pool or no encryption key.
    """
    shared = _shared_store(request)
    if shared is not None:
        return shared

    app_state = getattr(request.app.state, "app_state", None)
    pool = getattr(app_state, "db_pool", None)
    if pool is None:
        logger.warning("web_snapshot_store_unavailable reason=%s", "no_database_pool")
        raise HTTPException(status_code=503, detail=_STORE_UNAVAILABLE_DETAIL)

    settings = getattr(app_state, "settings", None)
    try:
        return create_web_snapshot_store(pool, settings if isinstance(settings, Settings) else None)
    except EncryptionInitError:
        # Fail closed: no key means no snapshot access, and the reason is a
        # deployment fact, not something to hand a client.
        logger.warning("web_snapshot_store_unavailable reason=%s", "encryption_key_missing")
        raise HTTPException(status_code=503, detail=_STORE_UNAVAILABLE_DETAIL) from None


# --------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------


class WebSnapshotMetadataOut(BaseModel):
    """Bounded source metadata for one retained snapshot."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    source_url: str
    final_url: str
    title: str
    extract_mode: str
    extraction_version: str
    content_chars: int
    content_bytes: int
    stored_bytes: int
    retrieved_at: datetime
    expires_at: datetime


class WebSnapshotListResponse(BaseModel):
    snapshots: list[WebSnapshotMetadataOut]
    total: int
    offset: int
    limit: int


class WebSnapshotStatusResponse(BaseModel):
    status: str


def _metadata_out(metadata: WebSnapshotMetadata) -> WebSnapshotMetadataOut:
    return WebSnapshotMetadataOut(
        id=metadata.id,
        conversation_id=metadata.conversation_id,
        source_url=metadata.source_url,
        final_url=metadata.final_url,
        title=metadata.title,
        extract_mode=metadata.extract_mode,
        extraction_version=metadata.extraction_version,
        content_chars=metadata.content_chars,
        content_bytes=metadata.content_bytes,
        stored_bytes=metadata.stored_bytes,
        retrieved_at=metadata.retrieved_at,
        expires_at=metadata.expires_at,
    )


def _export_document(snapshot: WebSnapshot) -> dict[str, Any]:
    """The bounded export payload: source metadata plus the extracted text."""
    return {
        "schema": EXPORT_SCHEMA,
        "version": EXPORT_VERSION,
        "snapshot_id": str(snapshot.id),
        "conversation_id": str(snapshot.conversation_id),
        "source_url": snapshot.source_url,
        "final_url": snapshot.final_url,
        "title": snapshot.title,
        "extract_mode": snapshot.extract_mode,
        "extraction_version": snapshot.extraction_version,
        "content_chars": snapshot.content_chars,
        "content_bytes": snapshot.content_bytes,
        "retrieved_at": snapshot.retrieved_at.isoformat(),
        "expires_at": snapshot.expires_at.isoformat(),
        "content": snapshot.content,
    }


def _map_store_error(exc: WebSnapshotError, *, conversation_id: uuid.UUID) -> HTTPException:
    """Translate a typed store failure into a non-disclosing HTTP error.

    Wrong owner, wrong conversation, unknown id and expired all collapse to the
    same 404 detail. Capacity and validation become 409/400 with generic text.
    Integrity failures become a generic 500: a corrupt envelope is a server-side
    fact and must not be described to the client.
    """
    if isinstance(exc, (WebSnapshotNotFound, WebSnapshotExpired, WebSnapshotOwnerMismatch)):
        return HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    if isinstance(exc, WebSnapshotCapacityExceeded):
        return HTTPException(status_code=409, detail=_STORE_UNAVAILABLE_DETAIL)
    if isinstance(exc, (WebSnapshotValidationError, WebSnapshotContentTooLarge)):
        return HTTPException(status_code=400, detail=_INVALID_REQUEST_DETAIL)
    if isinstance(exc, WebSnapshotIntegrityError):
        logger.error(
            "web_snapshot_integrity_failure conversation_id=%s reason=%s",
            conversation_id,
            exc.reason,
        )
        return HTTPException(status_code=500, detail=_INTERNAL_DETAIL)
    # Any other WebSnapshotError subclass: generic, never str(exc).
    logger.error(
        "web_snapshot_store_failure conversation_id=%s reason=%s",
        conversation_id,
        exc.reason,
    )
    return HTTPException(status_code=500, detail=_INTERNAL_DETAIL)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/{conversation_id}/web-snapshots", response_model=WebSnapshotListResponse)
async def list_web_snapshots(
    conversation_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthenticatedDevice = Depends(require_device_auth),
    store: WebSnapshotStore = Depends(get_web_snapshot_store),
) -> WebSnapshotListResponse:
    """List this conversation's retained sources, newest first.

    Bounded by ``limit``/``offset`` and by the store's expiry filter, so a
    delayed cleanup sweep cannot surface expired entries. Metadata only: no
    page body is decrypted or returned.
    """
    try:
        page = await store.list(auth.user_id, conversation_id, offset, limit)
    except WebSnapshotError as exc:
        raise _map_store_error(exc, conversation_id=conversation_id) from exc
    return WebSnapshotListResponse(
        snapshots=[_metadata_out(item) for item in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{conversation_id}/web-snapshots/{snapshot_id}/export")
async def export_web_snapshot(
    conversation_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    auth: AuthenticatedDevice = Depends(require_device_auth),
    store: WebSnapshotStore = Depends(get_web_snapshot_store),
) -> Response:
    """Download one retained snapshot as a bounded JSON document.

    The filename is opaque (a random UUID) and never derived from the source
    URL or page title, and the response is ``no-store`` so retained page text
    is not cached by the client or an intermediary.
    """
    try:
        snapshot = await store.get(auth.user_id, conversation_id, snapshot_id)
    except WebSnapshotError as exc:
        raise _map_store_error(exc, conversation_id=conversation_id) from exc

    payload = json.dumps(_export_document(snapshot), ensure_ascii=False).encode("utf-8")
    if len(payload) > EXPORT_HARD_CEILING_BYTES:
        # Unreachable for a row admitted under the configured bounds; the
        # ceiling exists so this route is bounded by construction rather than
        # by assumption. Refuse rather than truncate a retained source.
        logger.error(
            "web_snapshot_export_ceiling_exceeded snapshot_id=%s reason=%s",
            snapshot_id,
            "export_too_large",
        )
        raise HTTPException(status_code=413, detail=_EXPORT_TOO_LARGE_DETAIL)

    filename = f"web-snapshot-{snapshot.id}.json"
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete(
    "/{conversation_id}/web-snapshots/{snapshot_id}",
    response_model=WebSnapshotStatusResponse,
)
async def delete_web_snapshot(
    conversation_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    auth: AuthenticatedDevice = Depends(require_device_auth),
    store: WebSnapshotStore = Depends(get_web_snapshot_store),
) -> WebSnapshotStatusResponse:
    """Explicitly delete one owned snapshot.

    A wrong owner, a wrong conversation, an unknown id and an expired snapshot
    all return the same 404. Deleting the conversation itself removes its
    snapshots by foreign-key cascade.
    """
    try:
        deleted = await store.delete(auth.user_id, conversation_id, snapshot_id)
    except WebSnapshotError as exc:
        raise _map_store_error(exc, conversation_id=conversation_id) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return WebSnapshotStatusResponse(status="deleted")
