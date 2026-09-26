"""Spawn agent tool for orchestrator."""

from __future__ import annotations


import base64
import binascii
import hashlib
import json
import logging
import os
import stat
from collections.abc import Callable
from pathlib import Path

from typing import Any

from orchestrator.artifacts import ArtifactOwnerError, user_artifact_directory
from orchestrator.tools.registry import Tool


logger = logging.getLogger(__name__)

GENERATED_IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "generated_images"
GENERATED_AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "generated_audio"
MAX_PERSISTED_MEDIA_BYTES = 50 * 1024 * 1024


class _InvalidGeneratedMedia(ValueError):
    """Raised when a subagent result is unsafe to persist."""


def _decode_generated_media(encoded: Any) -> bytes:
    if not isinstance(encoded, str) or not encoded:
        raise _InvalidGeneratedMedia("payload is not a non-empty base64 string")

    max_encoded_length = 4 * ((MAX_PERSISTED_MEDIA_BYTES + 2) // 3)
    if len(encoded) > max_encoded_length:
        raise _InvalidGeneratedMedia("encoded payload exceeds the size limit")

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _InvalidGeneratedMedia("payload is not strict base64") from exc

    if not raw:
        raise _InvalidGeneratedMedia("decoded payload is empty")
    if len(raw) > MAX_PERSISTED_MEDIA_BYTES:
        raise _InvalidGeneratedMedia("decoded payload exceeds the size limit")
    return raw


def _looks_like_jpeg(raw: bytes) -> bool:
    return raw.startswith(b"\xff\xd8\xff")


def _looks_like_webp(raw: bytes) -> bool:
    return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"


def _looks_like_mp3(raw: bytes) -> bool:
    if raw.startswith(b"ID3"):
        return True
    if len(raw) < 3 or raw[0] != 0xFF or raw[1] & 0xE0 != 0xE0:
        return False
    version_bits = (raw[1] >> 3) & 0x03
    layer_bits = (raw[1] >> 1) & 0x03
    bitrate_index = (raw[2] >> 4) & 0x0F
    sample_rate_index = (raw[2] >> 2) & 0x03
    return (
        version_bits != 0x01
        and layer_bits != 0x00
        and bitrate_index not in {0x00, 0x0F}
        and sample_rate_index != 0x03
    )


def _looks_like_wav(raw: bytes) -> bool:
    return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WAVE"


def _looks_like_ogg(raw: bytes) -> bool:
    return raw.startswith(b"OggS")


def _looks_like_opus(raw: bytes) -> bool:
    return _looks_like_ogg(raw) and b"OpusHead" in raw[:64]


_SignatureCheck = Callable[[bytes], bool]
_MediaFormatMap = dict[str, tuple[str, _SignatureCheck]]


_IMAGE_FORMATS: _MediaFormatMap = {
    "png": ("png", lambda raw: raw.startswith(b"\x89PNG\r\n\x1a\n")),
    "jpg": ("jpg", _looks_like_jpeg),
    "jpeg": ("jpg", _looks_like_jpeg),
    "webp": ("webp", _looks_like_webp),
}
_AUDIO_FORMATS: _MediaFormatMap = {
    "mp3": ("mp3", _looks_like_mp3),
    "wav": ("wav", _looks_like_wav),
    "ogg": ("ogg", _looks_like_ogg),
    "opus": ("opus", _looks_like_opus),
}


def _validate_generated_media(
    encoded: Any,
    claimed_format: Any,
    *,
    default_format: str,
    allowed_formats: _MediaFormatMap,
) -> tuple[bytes, str]:
    if claimed_format is None:
        normalized_format = default_format
    elif isinstance(claimed_format, str):
        normalized_format = claimed_format.strip().lower()
    else:
        raise _InvalidGeneratedMedia("format is not a string")

    format_spec = allowed_formats.get(normalized_format)
    if format_spec is None:
        raise _InvalidGeneratedMedia("format is not allowlisted")

    extension, signature_check = format_spec
    raw = _decode_generated_media(encoded)
    if not signature_check(raw):
        raise _InvalidGeneratedMedia("content signature does not match the claimed format")
    return raw, extension


def _write_generated_media(directory: Path, raw: bytes, extension: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(raw).hexdigest()
    filename = f"{content_hash}.{extension}"
    filepath = directory / filename

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        descriptor = os.open(filepath, flags, 0o600)
    except FileExistsError:
        read_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            read_flags |= os.O_NOFOLLOW
        existing_descriptor = os.open(filepath, read_flags)
        with os.fdopen(existing_descriptor, "rb") as existing:
            if not stat.S_ISREG(os.fstat(existing.fileno()).st_mode) or existing.read() != raw:
                raise _InvalidGeneratedMedia("existing content-addressed path is unsafe")
        if filepath.is_symlink():
            raise _InvalidGeneratedMedia("existing content-addressed path is unsafe")
        return filename

    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
    except Exception:
        filepath.unlink(missing_ok=True)
        raise
    return filename


def _rejected_media_result(
    result_dict: dict[str, Any],
    data: dict[str, Any],
    *,
    encoded_key: str,
    url_key: str,
    media_kind: str,
    reason: str,
) -> dict[str, Any]:
    logger.warning("Rejected generated %s payload: %s", media_kind, reason)
    safe_data = {**data}
    safe_data.pop(encoded_key, None)
    safe_data.pop(url_key, None)
    return {
        **result_dict,
        "success": False,
        "error": f"Generated {media_kind} payload failed validation",
        "data": safe_data,
    }


def _persist_image_result(
    result_dict: dict[str, Any],
    user_id: Any = None,
) -> dict[str, Any]:
    """Save base64 image data to disk and replace with a servable URL path.

    Prevents the raw base64 blob from being re-injected into the LLM context,
    which causes context window overflow.
    """
    data = result_dict.get("data")
    if not isinstance(data, dict):
        return result_dict

    image_base64 = data.get("image_base64")
    if not image_base64:
        return result_dict

    try:
        raw, extension = _validate_generated_media(
            image_base64,
            data.get("format"),
            default_format="png",
            allowed_formats=_IMAGE_FORMATS,
        )
        owner_dir = user_artifact_directory(GENERATED_IMAGES_DIR, user_id, create=True)
        filename = _write_generated_media(owner_dir, raw, extension)
        logger.info("Saved generated image %s (%d bytes)", filename, len(raw))
    except (_InvalidGeneratedMedia, ArtifactOwnerError) as exc:
        return _rejected_media_result(
            result_dict,
            data,
            encoded_key="image_base64",
            url_key="image_url",
            media_kind="image",
            reason=str(exc),
        )
    except OSError:
        logger.exception("Failed to persist validated generated image")
        return _rejected_media_result(
            result_dict,
            data,
            encoded_key="image_base64",
            url_key="image_url",
            media_kind="image",
            reason="filesystem write failed",
        )

    result_dict = {**result_dict, "data": {**data}}
    result_dict["data"].pop("image_base64", None)
    result_dict["data"].pop("image_url", None)
    result_dict["data"]["image_path"] = f"/generated-images/{filename}"

    return result_dict


def _persist_audio_result(
    result_dict: dict[str, Any],
    user_id: Any = None,
) -> dict[str, Any]:
    """Save base64 audio data to disk and replace with a servable URL path.

    Prevents the raw base64 blob from being re-injected into the LLM context,
    which causes context window overflow.
    """
    data = result_dict.get("data")
    if not isinstance(data, dict):
        return result_dict

    audio_base64 = data.get("audio_base64")
    if not audio_base64:
        return result_dict

    try:
        raw, extension = _validate_generated_media(
            audio_base64,
            data.get("format"),
            default_format="mp3",
            allowed_formats=_AUDIO_FORMATS,
        )
        owner_dir = user_artifact_directory(GENERATED_AUDIO_DIR, user_id, create=True)
        filename = _write_generated_media(owner_dir, raw, extension)
        logger.info("Saved generated audio %s (%d bytes)", filename, len(raw))
    except (_InvalidGeneratedMedia, ArtifactOwnerError) as exc:
        return _rejected_media_result(
            result_dict,
            data,
            encoded_key="audio_base64",
            url_key="audio_url",
            media_kind="audio",
            reason=str(exc),
        )
    except OSError:
        logger.exception("Failed to persist validated generated audio")
        return _rejected_media_result(
            result_dict,
            data,
            encoded_key="audio_base64",
            url_key="audio_url",
            media_kind="audio",
            reason="filesystem write failed",
        )

    result_dict = {**result_dict, "data": {**data}}
    result_dict["data"].pop("audio_base64", None)
    result_dict["data"].pop("audio_url", None)
    result_dict["data"]["audio_path"] = f"/generated-audio/{filename}"

    return result_dict


class SpawnAgentTool(Tool):
    """Tool to spawn specialized subagents for complex tasks."""

    name = "spawn_agent"
    description = "Spawn a specialized subagent for research, image generation, video generation, sound effect generation, or code tasks"
    parameters = {
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "description": "Type of subagent to spawn",
                "enum": ["research", "image", "audio", "code", "reader"],
            },
            "task": {
                "type": "string",
                "description": "The task or query for the subagent to perform",
            },
            "context": {
                "type": "object",
                "description": "Optional additional context for the subagent (e.g., style preferences for images, file paths for readers, mode for image/video generation)",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "Generation mode for image agent (image or video)",
                        "enum": ["image", "video"],
                        "default": "image",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Duration in seconds for video generation (1-15)",
                        "minimum": 1,
                        "maximum": 15,
                    },
                },
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID from a previous spawn_agent result (metadata.session_id) to continue context",
            },
        },
        "required": ["agent_type", "task"],
    }

    def __init__(
        self,
        *,
        db_pool: Any | None = None,
        trusted_spawn_context: dict[str, Any] | None = None,
        user_id: Any | None = None,
    ) -> None:
        self._db_pool = db_pool
        self._user_id = user_id
        self._trusted_spawn_context = trusted_spawn_context or {}

    async def execute(self, **kwargs: Any) -> str:
        """Execute the spawn agent tool."""
        # Subagent side effects (search, image, audio, video) have independent
        # provider prices. Until those routes carry priced reservations, fail
        # before dispatch rather than bypassing the account budget.
        return json.dumps(
            {"error": "Subagent compute capacity unavailable", "code": "capacity_unavailable"}
        )


class SpawnMultipleTool(Tool):
    """Tool to spawn multiple subagents in parallel."""

    name = "spawn_multiple"
    description = "Spawn multiple subagents in parallel for concurrent execution"
    parameters = {
        "type": "object",
        "properties": {
            "agents": {
                "type": "array",
                "description": "List of agents to spawn",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent_type": {
                            "type": "string",
                            "enum": ["research", "image", "audio", "code", "reader"],
                        },
                        "task": {
                            "type": "string",
                        },
                        "context": {
                            "type": "object",
                        },
                        "session_id": {
                            "type": "string",
                        },
                    },
                    "required": ["agent_type", "task"],
                },
            },
        },
        "required": ["agents"],
    }

    def __init__(
        self,
        *,
        db_pool: Any | None = None,
        trusted_spawn_context: dict[str, Any] | None = None,
        user_id: Any | None = None,
    ) -> None:
        self._db_pool = db_pool
        self._user_id = user_id
        self._trusted_spawn_context = trusted_spawn_context or {}

    async def execute(self, **kwargs: Any) -> str:
        """Execute multiple subagents in parallel."""

        return json.dumps(
            {"error": "Parallel compute capacity unavailable", "code": "capacity_unavailable"}
        )
