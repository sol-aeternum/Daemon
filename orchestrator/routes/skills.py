from __future__ import annotations

from typing import Any, Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import Settings, get_settings
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from orchestrator import skills_store
from orchestrator.skills_projection import SkillProjectionStore
from orchestrator.skills_sync import SkillSyncService
from orchestrator.skills_upgrade import (
    SkillUpgradeService,
    load_repo_contents,
    run_upgrade_sync,
)

router = APIRouter(prefix="/skills", tags=["skills"])


def require_admin_api_key(settings: Settings, authorization: str | None) -> None:
    """Reject unauthenticated admin requests."""
    if not settings.daemon_admin_api_key:
        raise HTTPException(
            status_code=403, detail="Admin dreaming trigger is disabled"
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.daemon_admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin bearer token")


def _safe_isoformat(value: Any) -> str | None:
    """Safely convert datetime or string to ISO format string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _attach_projection_metadata(
    detail: skills_store.SkillDetail,
    projection: dict[str, Any] | None,
) -> skills_store.SkillDetail:
    """Merge projection metadata into skill detail for API response."""
    if projection:
        detail["source_type"] = projection.get("source_type")
        detail["allow_autonomous_edit"] = projection.get("allow_autonomous_edit")
        detail["repo_version"] = projection.get("repo_version")
        detail["local_version"] = projection.get("local_version")
        detail["pending_update"] = projection.get("pending_update")
        detail["use_count"] = projection.get("use_count")
        detail["last_used_at"] = _safe_isoformat(projection.get("last_used_at"))
        detail["created_by"] = projection.get("created_by")
        detail["origin_url"] = projection.get("origin_url")
    return detail


async def _get_projection(request: Request, skill_id: str) -> dict[str, Any] | None:
    """Fetch projection metadata if db pool is available."""
    if not hasattr(request.app.state, "app_state"):
        return None
    app_state = request.app.state.app_state
    if not hasattr(app_state, "db_pool") or app_state.db_pool is None:
        return None
    store = SkillProjectionStore(app_state.db_pool)
    return await store.get_projection(skill_id)


def _get_sync_service(request: Request) -> SkillSyncService | None:
    """Get sync service if db pool is available."""
    if not hasattr(request.app.state, "app_state"):
        return None
    app_state = request.app.state.app_state
    if not hasattr(app_state, "db_pool") or app_state.db_pool is None:
        return None
    store = SkillProjectionStore(app_state.db_pool)
    return SkillSyncService(store)


class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="")
    content: str = Field(default="")
    enabled: bool = True


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None)
    content: str | None = None
    enabled: bool | None = None


class SkillEnabledUpdate(BaseModel):
    enabled: bool


class SkillAutonomousEditUpdate(BaseModel):
    allow_autonomous_edit: bool


class PendingUpdateAction(BaseModel):
    action: str  # "apply" or "dismiss"


@router.get("")
async def list_skills(
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, list[skills_store.SkillSummary]]:
    skills = skills_store.list_skills()
    if hasattr(request.app.state, "app_state"):
        app_state = request.app.state.app_state
        if hasattr(app_state, "db_pool") and app_state.db_pool is not None:
            store = SkillProjectionStore(app_state.db_pool)
            for skill in skills:
                try:
                    proj = await store.get_projection(skill["id"])
                    if proj:
                        skill["source_type"] = proj.get("source_type")
                        skill["allow_autonomous_edit"] = proj.get(
                            "allow_autonomous_edit"
                        )
                        skill["repo_version"] = proj.get("repo_version")
                        skill["local_version"] = proj.get("local_version")
                        skill["pending_update"] = proj.get("pending_update")
                        skill["use_count"] = proj.get("use_count")
                        skill["last_used_at"] = _safe_isoformat(
                            proj.get("last_used_at")
                        )
                except Exception:
                    continue
    return {"skills": skills}


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> skills_store.SkillDetail:
    try:
        detail = skills_store.get_skill(skill_id)
        try:
            projection = await _get_projection(request, skill_id)
            return _attach_projection_metadata(detail, projection)
        except Exception:
            return detail
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=201)
async def create_skill(
    payload: SkillCreate,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> skills_store.SkillDetail:
    try:
        result = skills_store.create_skill(
            name=payload.name,
            description=payload.description,
            content=payload.content,
            enabled=payload.enabled,
        )
        sync = _get_sync_service(request)
        if sync:
            await sync.sync_skill(result["id"], source_type="manual")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/upload", status_code=201)
async def upload_skill(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    overwrite: Annotated[bool, Form()] = False,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> skills_store.SkillDetail:
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    try:
        raw_bytes = await file.read()
        markdown = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="Skill file must be UTF-8 markdown"
        ) from exc

    try:
        result = skills_store.import_skill_markdown(
            filename=filename,
            markdown=markdown,
            overwrite=overwrite,
        )
        sync = _get_sync_service(request)
        if sync:
            await sync.sync_skill(result["id"], source_type="imported")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str,
    payload: SkillUpdate,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> skills_store.SkillDetail:
    try:
        result = skills_store.update_skill(
            skill_id,
            name=payload.name,
            description=payload.description,
            content=payload.content,
            enabled=payload.enabled,
        )
        sync = _get_sync_service(request)
        if sync:
            await sync.sync_skill(skill_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{skill_id}/enabled")
async def set_skill_enabled(
    skill_id: str,
    payload: SkillEnabledUpdate,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> skills_store.SkillDetail:
    try:
        result = skills_store.update_skill(
            skill_id,
            name=None,
            description=None,
            content=None,
            enabled=payload.enabled,
        )
        sync = _get_sync_service(request)
        if sync:
            await sync.sync_skill(skill_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, str]:
    try:
        skills_store.delete_skill(skill_id)
        sync = _get_sync_service(request)
        if sync:
            await sync.delete_skill_projection(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.patch("/{skill_id}/autonomous-edit")
async def set_skill_autonomous_edit(
    skill_id: str,
    payload: SkillAutonomousEditUpdate,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    """Toggle allow_autonomous_edit flag for a skill."""
    try:
        skills_store.get_skill(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if hasattr(request.app.state, "app_state"):
        app_state = request.app.state.app_state
        if hasattr(app_state, "db_pool") and app_state.db_pool is not None:
            store = SkillProjectionStore(app_state.db_pool)
            await store.update_autonomous_edit(skill_id, payload.allow_autonomous_edit)

    return {
        "skill_id": skill_id,
        "allow_autonomous_edit": payload.allow_autonomous_edit,
    }


@router.post("/{skill_id}/pending-update")
async def handle_pending_update(
    skill_id: str,
    payload: PendingUpdateAction,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    """Apply or dismiss a pending update for a skill via the upgrade service."""
    if not hasattr(request.app.state, "app_state"):
        raise HTTPException(status_code=503, detail="Database not available")

    app_state = request.app.state.app_state
    if not hasattr(app_state, "db_pool") or app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    store = SkillProjectionStore(app_state.db_pool)
    service = SkillUpgradeService(store)

    if payload.action == "apply":
        result = await service.apply_pending_update(skill_id)
        if not result.success:
            if "not found" in (result.error or "").lower():
                raise HTTPException(status_code=404, detail=result.error)
            raise HTTPException(status_code=400, detail=result.error)
        return {
            "skill_id": skill_id,
            "action": "applied",
            "message": "Pending update applied successfully",
        }

    elif payload.action == "dismiss":
        result = await service.dismiss_pending_update(skill_id)
        if not result.success:
            if "not found" in (result.error or "").lower():
                raise HTTPException(status_code=404, detail=result.error)
            raise HTTPException(status_code=400, detail=result.error)
        return {
            "skill_id": skill_id,
            "action": "dismissed",
            "message": "Pending update dismissed",
        }

    else:
        raise HTTPException(
            status_code=400, detail="Invalid action. Use 'apply' or 'dismiss'"
        )


@router.get("/{skill_id}/download")
async def download_skill(
    skill_id: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> PlainTextResponse:
    """Download skill as markdown file.

    Returns the canonical markdown (frontmatter + body) suitable for
    re-import via the upload endpoint.
    """
    try:
        markdown = skills_store.export_skill_markdown(skill_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    filename = f"{skill_id}.md"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "text/markdown; charset=utf-8",
    }
    return PlainTextResponse(markdown, headers=headers)


class AdminSyncResponse(BaseModel):
    success: bool
    total_unchanged: int
    total_silent_updates: int
    total_pending_updates: int
    total_inserts: int
    total_deprecated: int
    total_errors: int
    repo_skills_found: int


@router.post("/admin/sync", response_model=AdminSyncResponse)
async def admin_sync_repo_skills(
    request: Request,
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> AdminSyncResponse:
    require_admin_api_key(settings, authorization)

    if not hasattr(request.app.state, "app_state"):
        raise HTTPException(status_code=503, detail="App state not available")

    app_state = request.app.state.app_state
    if not hasattr(app_state, "db_pool") or app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    repo_contents = load_repo_contents()
    result = await run_upgrade_sync(app_state.db_pool, repo_contents)

    return AdminSyncResponse(
        success=result.total_errors == 0,
        total_unchanged=result.total_unchanged,
        total_silent_updates=result.total_silent_updates,
        total_pending_updates=result.total_pending_updates,
        total_inserts=result.total_inserts,
        total_deprecated=result.total_deprecated,
        total_errors=result.total_errors,
        repo_skills_found=len(repo_contents),
    )
