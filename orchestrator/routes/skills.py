from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from orchestrator.auth import (
    AuthenticatedDevice,
    AdminOrDeviceAuth,
    require_admin_or_device_auth,
    require_device_auth,
)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from orchestrator.memory.encryption import EncryptionInitError
from orchestrator.request_body_limit import REQUEST_BODY_TOO_LARGE_RESPONSES
from orchestrator.services.skill_format import parse_skill_markdown, render_skill_markdown
from orchestrator.services.skill_index import embed_skill
from orchestrator.services.skills import (
    SkillError,
    SkillStore,
    create_skill_store,
    slug_for_name,
)

router = APIRouter(prefix="/skills", tags=["skills"])

# Refusals that are not a plain bad request. Every other reason is a 400.
_REFUSAL_STATUS = {
    "not_found": 404,
    "revision_not_found": 404,
    "shipped_read_only": 403,
    "slug_taken": 409,
    "revision_conflict": 409,
    "account_unavailable": 503,
    "encryption_key_mismatch": 503,
}


# The settings UI shows ``detail`` to the person as it is, so it is a sentence.
# Messages never include skill content.
_REFUSAL_MESSAGE = {
    "not_found": "Skill not found",
    "revision_not_found": "Revision not found",
    "shipped_read_only": "Shipped skills are read-only",
    "slug_taken": "A skill with that name already exists",
    "revision_conflict": "The skill was changed elsewhere. Reload it and try again",
    "limit_reached": "This account has reached its skill limit",
    "slug_invalid": (
        "Skill id must contain only lowercase letters, numbers, underscores, or hyphens"
    ),
    "name_required": "A skill needs a name",
    "name_too_long": "The skill name is too long",
    "description_required": "Skill frontmatter must include a non-empty 'description' field",
    "description_too_long": "The skill description is too long",
    "body_required": "The skill file has no instructions",
    "body_too_long": "The skill content is too long",
    "file_too_long": "The skill file is too large",
    "file_type_unsupported": "Only .md skill files are supported",
    "format_invalid": (
        "Invalid skill format. Provide frontmatter or markdown with a top-level '# Title' heading"
    ),
    "frontmatter_too_long": "The skill file's frontmatter is too long",
    "enabled_invalid": "Field 'enabled' must be true or false",
    "account_unavailable": "Account unavailable",
    "encryption_key_mismatch": "Skill storage unavailable",
}


def _refusal(exc: SkillError) -> HTTPException:
    return HTTPException(
        status_code=_REFUSAL_STATUS.get(exc.reason, 400),
        detail=_REFUSAL_MESSAGE.get(exc.reason, "The skill request was refused"),
    )


def _store(request: Request) -> SkillStore:
    """The skill store, or 503: there is no file-backed fallback."""
    app_state = getattr(request.app.state, "app_state", None)
    db_pool = getattr(app_state, "db_pool", None)
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        return create_skill_store(db_pool)
    except EncryptionInitError as exc:
        raise HTTPException(status_code=503, detail="Skill storage unavailable") from exc


def _summary(skill: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": skill["slug"],
        "name": skill["name"],
        "description": skill["description"],
        "enabled": skill["enabled"],
        "updated_at": skill["updated_at"],
        "scope": skill["scope"],
        "revision": skill["revision"],
        "source_type": skill["source_type"],
        "allow_autonomous_edit": skill["allow_autonomous_edit"],
        "use_count": skill["use_count"],
        "last_used_at": skill["last_used_at"],
        # Retired with the shipped-skill upgrade machinery; always null.
        "repo_version": None,
        "local_version": None,
        "pending_update": None,
    }


def _detail(skill: dict[str, Any]) -> dict[str, Any]:
    return {
        **_summary(skill),
        "content": skill["body"],
        # Retired provenance fields; always null.
        "created_by": None,
        "origin_url": None,
    }


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
    expected_revision: int | None = Field(default=None, ge=1)


class SkillEnabledUpdate(BaseModel):
    enabled: bool


class SkillAutonomousEditUpdate(BaseModel):
    allow_autonomous_edit: bool


@router.get("")
async def list_skills(
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, list[dict[str, Any]]]:
    store = _store(request)
    try:
        skills = await store.list(auth.user_id)
    except SkillError as exc:
        raise _refusal(exc) from exc
    skills.sort(key=lambda skill: skill["updated_at"], reverse=True)
    return {"skills": [_summary(skill) for skill in skills]}


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    store = _store(request)
    try:
        return _detail(await store.get(auth.user_id, skill_id))
    except SkillError as exc:
        raise _refusal(exc) from exc


@router.post("", status_code=201)
async def create_skill(
    payload: SkillCreate,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    store = _store(request)
    try:
        skill = await store.create(
            auth.user_id,
            name=payload.name,
            description=payload.description,
            body=payload.content,
            enabled=payload.enabled,
        )
    except SkillError as exc:
        raise _refusal(exc) from exc
    await embed_skill(store, skill)
    return _detail(skill)


@router.post(
    "/upload",
    status_code=201,
    responses=REQUEST_BODY_TOO_LARGE_RESPONSES,
)
async def upload_skill(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    overwrite: Annotated[bool, Form()] = False,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    try:
        raw_bytes = await file.read()
        markdown = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Skill file must be UTF-8 markdown") from exc

    store = _store(request)
    try:
        parsed = parse_skill_markdown(filename, markdown)
        try:
            skill = await store.create(
                auth.user_id,
                name=parsed.name,
                description=parsed.description,
                body=parsed.body,
                imported=True,
                enabled=parsed.enabled,
            )
        except SkillError as exc:
            if exc.reason != "slug_taken" or not overwrite:
                raise
            # Overwrite is a new revision of the caller's own skill; its
            # stored source type and grant are untouched.
            skill = await store.save(
                auth.user_id,
                slug_for_name(parsed.name),
                name=parsed.name,
                description=parsed.description,
                body=parsed.body,
                enabled=parsed.enabled,
            )
    except SkillError as exc:
        raise _refusal(exc) from exc
    await embed_skill(store, skill)
    return _detail(skill)


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str,
    payload: SkillUpdate,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    store = _store(request)
    content_changed = (
        payload.name is not None or payload.description is not None or payload.content is not None
    )
    try:
        if content_changed:
            skill = await store.save(
                auth.user_id,
                skill_id,
                name=payload.name,
                description=payload.description,
                body=payload.content,
                expected_revision=payload.expected_revision,
                enabled=payload.enabled,
            )
        elif payload.enabled is not None:
            # The switch is not a content revision, so no revision is expected.
            skill = await store.set_enabled(auth.user_id, skill_id, payload.enabled)
        else:
            skill = await store.get(auth.user_id, skill_id)
    except SkillError as exc:
        raise _refusal(exc) from exc
    if content_changed:
        await embed_skill(store, skill)
    return _detail(skill)


@router.patch("/{skill_id}/enabled")
async def set_skill_enabled(
    skill_id: str,
    payload: SkillEnabledUpdate,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    store = _store(request)
    try:
        return _detail(await store.set_enabled(auth.user_id, skill_id, payload.enabled))
    except SkillError as exc:
        raise _refusal(exc) from exc


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, str]:
    store = _store(request)
    try:
        await store.delete(auth.user_id, skill_id)
    except SkillError as exc:
        raise _refusal(exc) from exc
    return {"status": "deleted"}


@router.patch("/{skill_id}/autonomous-edit")
async def set_skill_autonomous_edit(
    skill_id: str,
    payload: SkillAutonomousEditUpdate,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    """Grant or revoke autonomous edits for the caller's own skill."""
    store = _store(request)
    try:
        skill = await store.set_autonomous_edit(
            auth.user_id, skill_id, payload.allow_autonomous_edit
        )
    except SkillError as exc:
        raise _refusal(exc) from exc
    return {
        "skill_id": skill["slug"],
        "allow_autonomous_edit": skill["allow_autonomous_edit"],
    }


@router.get("/{skill_id}/download")
async def download_skill(
    skill_id: str,
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> PlainTextResponse:
    """Download the current revision as a file the upload endpoint accepts."""
    store = _store(request)
    try:
        skill = await store.get(auth.user_id, skill_id)
    except SkillError as exc:
        raise _refusal(exc) from exc
    headers = {
        "Content-Disposition": f'attachment; filename="{skill["slug"]}.md"',
        "Content-Type": "text/markdown; charset=utf-8",
    }
    return PlainTextResponse(render_skill_markdown(skill), headers=headers)


@router.get("/admin/consolidation-audit")
async def admin_list_consolidation_audit(
    request: Request,
    auth: AdminOrDeviceAuth = Depends(require_admin_or_device_auth),
    user_id: uuid.UUID | None = Query(default=None),
    action_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, list[dict[str, Any]]]:
    if not auth.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    if not hasattr(request.app.state, "app_state"):
        raise HTTPException(status_code=503, detail="App state not available")

    app_state = request.app.state.app_state
    if not hasattr(app_state, "memory_store") or app_state.memory_store is None:
        raise HTTPException(status_code=503, detail="Memory store not available")

    rows = await app_state.memory_store.list_consolidation_nudge_actions(
        user_id=user_id,
        action_type=action_type,
        status=status,
        since=since,
        until=until,
        limit=limit,
    )
    return {"actions": rows}
