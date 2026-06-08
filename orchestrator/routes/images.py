"""Retired Studio image API surface.

The legacy image-generation implementation lived outside the supported
orchestrator package and was removed because it crashed Docker startup. Keep
these endpoints registered and authenticated so callers receive an explicit
retirement response instead of an accidental 404 while the replacement is built
under the hosted-identity auth model.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from orchestrator.auth import AuthenticatedDevice, require_device_auth

router = APIRouter(prefix="/api/images", tags=["images"])

_RETIRED_DETAIL = (
    "Studio image generation is retired in this backend build. "
    "Use Studio video generation or wait for the hosted-identity image API replacement."
)

AuthDependency = Annotated[AuthenticatedDevice, Depends(require_device_auth)]


def _raise_retired() -> None:
    raise HTTPException(status_code=410, detail=_RETIRED_DETAIL)


@router.get("/models")
async def list_image_models(auth: AuthDependency) -> None:
    """Authenticate callers before reporting that image model listing is retired."""
    _raise_retired()


@router.get("/{image_id}")
async def get_image(image_id: str, auth: AuthDependency) -> None:
    """Authenticate callers before reporting that image retrieval is retired."""
    _raise_retired()


@router.get("/{image_id}/metadata")
async def get_image_metadata(image_id: str, auth: AuthDependency) -> None:
    """Authenticate callers before reporting that image metadata is retired."""
    _raise_retired()


@router.post("/upload-reference")
async def upload_reference_image(
    auth: AuthDependency,
    file: UploadFile | None = File(default=None),
) -> None:
    """Authenticate callers before reporting that reference upload is retired."""
    _raise_retired()


@router.post("/generate")
async def generate_image(auth: AuthDependency) -> None:
    """Authenticate callers before reporting that image generation is retired."""
    _raise_retired()
