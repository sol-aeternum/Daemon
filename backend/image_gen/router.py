from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.image_gen.dispatcher import GenerationEvent, dispatch_parallel
from backend.image_gen.models import (
    TierName,
    get_image_model,
)
from backend.image_gen.storage import ImageStorage
from orchestrator.auth import AuthenticatedDevice, require_device_auth

router = APIRouter(prefix="/api/images", tags=["images"])

_ALLOWED_UPLOAD_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_MAX_REFERENCE_B64_CHARS = 14 * 1024 * 1024
_ALLOWED_TIERS = {"free", "starter", "pro", "max", "byok"}

_storage = ImageStorage()


class GenerateRequest(BaseModel):
    models: list[str] = Field(min_length=1, max_length=4)
    prompt: str = Field(min_length=1, max_length=8000)
    reference_image_b64: str | None = Field(
        default=None, max_length=_MAX_REFERENCE_B64_CHARS
    )
    reference_id: str | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None


class UploadReferenceResponse(BaseModel):
    reference_id: str
    image_url: str
    content_type: str
    size_bytes: int


@router.get("/models")
async def get_models(
    x_daemon_tier: Annotated[str | None, Header(alias="X-Daemon-Tier")] = None,
    tier: Literal["free", "starter", "pro", "max", "byok"] | None = None,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> JSONResponse:
    resolved_tier = tier or x_daemon_tier or "starter"
    if resolved_tier not in _ALLOWED_TIERS:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {resolved_tier}")

    from backend.image_gen.models import IMAGE_MODEL_CATALOG

    resolved_tier_literal = cast(TierName, resolved_tier)
    tier_rank = {"free": 0, "starter": 1, "pro": 2, "max": 3, "byok": 4}
    user_rank = tier_rank.get(resolved_tier_literal, 0)

    models = []
    for model in IMAGE_MODEL_CATALOG:
        model_dict = asdict(model)
        model_rank = tier_rank.get(model.tier_minimum, 3)
        model_dict["is_locked"] = model_rank > user_rank
        models.append(model_dict)

    return JSONResponse(content={"tier": resolved_tier, "models": models})


@router.get("/{image_id}")
async def get_image(
    image_id: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> Response:
    try:
        image_bytes, content_type = _storage.get_image(image_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc

    return Response(content=image_bytes, media_type=content_type)


@router.get("/{image_id}/metadata")
async def get_image_metadata(
    image_id: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> JSONResponse:
    try:
        metadata = _storage.get_metadata(image_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Image metadata not found") from exc

    return JSONResponse(content=metadata)


@router.post("/upload-reference", response_model=UploadReferenceResponse)
async def upload_reference(
    file: Annotated[UploadFile, File(...)],
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> UploadReferenceResponse:
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    declared_content_type = (file.content_type or "").lower().strip()
    if declared_content_type and declared_content_type not in _ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported image type")

    chunks: list[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413, detail="Reference image exceeds 10MB limit"
            )
        chunks.append(chunk)

    raw_bytes = b"".join(chunks)
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    detected_content_type, _ = _detect_image_type(raw_bytes)
    if detected_content_type is None:
        raise HTTPException(
            status_code=400, detail="Uploaded file is not a supported image"
        )

    image_b64 = base64.b64encode(raw_bytes).decode("ascii")
    reference_id = _storage.save_image(
        image_b64,
        {
            "source": "upload-reference",
            "filename": filename,
            "content_type": detected_content_type,
            "size_bytes": len(raw_bytes),
            "is_reference": True,
        },
    )

    return UploadReferenceResponse(
        reference_id=reference_id,
        image_url=f"/api/images/{reference_id}",
        content_type=detected_content_type,
        size_bytes=len(raw_bytes),
    )


@router.post("/generate")
async def generate_images(
    request: Request,
    payload: GenerateRequest,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> StreamingResponse:
    request_reference_b64 = payload.reference_image_b64
    if request_reference_b64 is None and payload.reference_id:
        try:
            ref_bytes, _ = _storage.get_image(payload.reference_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="Reference image not found"
            ) from exc
        request_reference_b64 = base64.b64encode(ref_bytes).decode("ascii")

    _validate_generation_request(
        model_ids=payload.models,
        aspect_ratio=payload.aspect_ratio,
        resolution=payload.resolution,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in dispatch_parallel(
                models=payload.models,
                prompt=payload.prompt,
                reference_image_b64=request_reference_b64,
                aspect_ratio=payload.aspect_ratio,
                resolution=payload.resolution,
                storage=_storage,
            ):
                if await request.is_disconnected():
                    break
                yield _sse("generation", _event_payload(event))
        except Exception as exc:
            yield _sse("error", {"error": str(exc)})
        finally:
            yield _sse("done", {"ok": True})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _validate_generation_request(
    *, model_ids: list[str], aspect_ratio: str | None, resolution: str | None
) -> None:
    for model_id in model_ids:
        model = get_image_model(model_id)
        if model is None:
            raise HTTPException(
                status_code=400, detail=f"Unknown image model: {model_id}"
            )

        if (
            aspect_ratio is not None
            and model.supports_aspect_ratio
            and aspect_ratio not in model.supported_aspect_ratios
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Aspect ratio {aspect_ratio} is not supported by model {model_id}",
            )

        if (
            resolution is not None
            and model.supports_resolution
            and resolution not in model.supported_resolutions
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Resolution {resolution} is not supported by model {model_id}",
            )


def _event_payload(event: GenerationEvent) -> dict[str, object]:
    payload: dict[str, object] = {
        "model_id": event.model_id,
        "status": event.status,
    }
    if event.error:
        payload["error"] = event.error
    if event.result is not None:
        payload["result"] = {
            "model_id": event.result.model_id,
            "image_id": event.result.image_id,
            "image_url": event.result.image_url,
            "generation_time_ms": event.result.generation_time_ms,
            "cost_estimate": event.result.cost_estimate,
            "width": event.result.width,
            "height": event.result.height,
        }
    return payload


def _sse(event_type: str, payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"event: {event_type}\ndata: {data}\n\n"


def _detect_image_type(data: bytes) -> tuple[str | None, str | None]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif", ".gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None, None
