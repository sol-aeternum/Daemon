from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from orchestrator import skills_store

router = APIRouter(prefix="/skills", tags=["skills"])


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


@router.get("")
async def list_skills() -> dict[str, list[skills_store.SkillSummary]]:
    return {"skills": skills_store.list_skills()}


@router.get("/{skill_id}")
async def get_skill(skill_id: str) -> skills_store.SkillDetail:
    try:
        return skills_store.get_skill(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=201)
async def create_skill(payload: SkillCreate) -> skills_store.SkillDetail:
    try:
        return skills_store.create_skill(
            name=payload.name,
            description=payload.description,
            content=payload.content,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/upload", status_code=201)
async def upload_skill(
    file: Annotated[UploadFile, File(...)],
    overwrite: Annotated[bool, Form()] = False,
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
        return skills_store.import_skill_markdown(
            filename=filename,
            markdown=markdown,
            overwrite=overwrite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{skill_id}")
async def update_skill(skill_id: str, payload: SkillUpdate) -> skills_store.SkillDetail:
    try:
        return skills_store.update_skill(
            skill_id,
            name=payload.name,
            description=payload.description,
            content=payload.content,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{skill_id}/enabled")
async def set_skill_enabled(
    skill_id: str, payload: SkillEnabledUpdate
) -> skills_store.SkillDetail:
    try:
        return skills_store.update_skill(
            skill_id,
            name=None,
            description=None,
            content=None,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str) -> dict[str, str]:
    try:
        skills_store.delete_skill(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}
