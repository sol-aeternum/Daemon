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

from orchestrator.tools.registry import Tool
from orchestrator.config import get_settings
from orchestrator.subagents.base import SubagentType, SubagentManager
from orchestrator.subagents.research import ResearchSubagent
from orchestrator.subagents.image import ImageSubagent
from orchestrator.subagents.audio import AudioSubagent

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


def _persist_image_result(result_dict: dict[str, Any]) -> dict[str, Any]:
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
        filename = _write_generated_media(GENERATED_IMAGES_DIR, raw, extension)
        logger.info("Saved generated image %s (%d bytes)", filename, len(raw))
    except _InvalidGeneratedMedia as exc:
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


def _persist_audio_result(result_dict: dict[str, Any]) -> dict[str, Any]:
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
        filename = _write_generated_media(GENERATED_AUDIO_DIR, raw, extension)
        logger.info("Saved generated audio %s (%d bytes)", filename, len(raw))
    except _InvalidGeneratedMedia as exc:
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


# Global subagent manager instance
_subagent_manager: SubagentManager | None = None


def get_subagent_manager(db_pool: Any | None = None) -> SubagentManager:
    """Get or initialize the global subagent manager."""
    global _subagent_manager
    if _subagent_manager is None:
        settings = get_settings()
        tier_config = settings.get_tier_config()
        image_model = (
            tier_config.image_agent.model
            if tier_config.image_agent
            else settings.tier_pro_image_model
        )
        shared_config = {
            "brave_api_key": settings.brave_api_key,
            "elevenlabs_api_key": settings.elevenlabs_api_key,
            "openrouter_api_key": settings.openrouter_api_key,
            "openrouter_base_url": settings.openrouter_base_url,
            "openrouter_image_model": settings.openrouter_image_model,
            "image_model": image_model,
            "xai_api_key": settings.xai_api_key,
            "fal_api_key": settings.fal_key,
            "tier_config": tier_config,  # Pass tier config for video generation checks
            "db_pool": db_pool,
        }
        _subagent_manager = SubagentManager()
        # Register default subagents
        _subagent_manager.register(ResearchSubagent(shared_config))
        _subagent_manager.register(ImageSubagent(shared_config))
        _subagent_manager.register(AudioSubagent(shared_config))
    elif db_pool is not None:
        image_agent = _subagent_manager.get(SubagentType.IMAGE)
        if image_agent is not None:
            image_agent.config["db_pool"] = db_pool
    return _subagent_manager


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
    ) -> None:
        self._db_pool = db_pool
        self._trusted_spawn_context = trusted_spawn_context or {}

    def _apply_trusted_context(
        self,
        agent_type: SubagentType,
        context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if agent_type == SubagentType.IMAGE:
            merged_context: dict[str, Any] = dict(context or {})
            trusted_video = self._trusted_spawn_context.get("video")
            video_requested = merged_context.get("mode") == "video"
            metadata_requested = (
                isinstance(trusted_video, dict) and trusted_video.get("mode") == "video"
            )
            if isinstance(trusted_video, dict) and (video_requested or metadata_requested):
                merged_context["mode"] = "video"
                for key in (
                    "duration",
                    "tier",
                    "user_id",
                    "source_mode",
                    "reference_image_url",
                    "reference_image_id",
                    "video_provider",
                    "kling_model",
                    "audio_enabled",
                ):
                    value = trusted_video.get(key)
                    if value is not None:
                        merged_context[key] = value
                reference_image_url = trusted_video.get("reference_image_url")
                if reference_image_url is not None:
                    merged_context["source_image_url"] = reference_image_url
            return merged_context
        return context

    async def execute(self, **kwargs: Any) -> str:
        """Execute the spawn agent tool."""
        agent_type = kwargs.get("agent_type", "")
        task = kwargs.get("task", "")
        context = kwargs.get("context")
        session_id = kwargs.get("session_id")

        try:
            subagent_type = SubagentType(agent_type.lower())
        except ValueError:
            available = [t.value for t in SubagentType]
            return json.dumps(
                {
                    "error": f"Unknown agent_type: {agent_type}",
                    "available_types": available,
                }
            )

        context = self._apply_trusted_context(subagent_type, context)

        manager = get_subagent_manager(db_pool=self._db_pool)
        result = await manager.spawn(subagent_type, task, context, session_id)
        result_dict = result.to_dict()
        result_dict = _persist_image_result(result_dict)
        result_dict = _persist_audio_result(result_dict)

        return json.dumps(result_dict)


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
    ) -> None:
        self._db_pool = db_pool
        self._trusted_spawn_context = trusted_spawn_context or {}

    def _apply_trusted_context(
        self,
        agent_type: SubagentType,
        context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if agent_type == SubagentType.IMAGE:
            merged_context: dict[str, Any] = dict(context or {})
            trusted_video = self._trusted_spawn_context.get("video")
            video_requested = merged_context.get("mode") == "video"
            metadata_requested = (
                isinstance(trusted_video, dict) and trusted_video.get("mode") == "video"
            )
            if isinstance(trusted_video, dict) and (video_requested or metadata_requested):
                merged_context["mode"] = "video"
                for key in (
                    "duration",
                    "tier",
                    "user_id",
                    "source_mode",
                    "reference_image_url",
                    "reference_image_id",
                    "video_provider",
                    "kling_model",
                    "audio_enabled",
                ):
                    value = trusted_video.get(key)
                    if value is not None:
                        merged_context[key] = value
                reference_image_url = trusted_video.get("reference_image_url")
                if reference_image_url is not None:
                    merged_context["source_image_url"] = reference_image_url
            return merged_context
        return context

    async def execute(self, **kwargs: Any) -> str:
        """Execute multiple subagents in parallel."""
        agents = kwargs.get("agents", [])

        spawns: list[tuple[SubagentType, str, dict[str, Any] | None, str | None]] = []
        for agent_spec in agents:
            agent_type_str = agent_spec.get("agent_type", "")
            try:
                agent_type = SubagentType(agent_type_str.lower())
            except ValueError:
                spawns.append(
                    (
                        SubagentType.IMAGE,
                        "",
                        {
                            "_spawn_error": f"Unknown agent_type: {agent_type_str}",
                            "_orig_agent_type": agent_type_str,
                        },
                        None,
                    )
                )
                continue
            task = agent_spec.get("task", "")
            context = self._apply_trusted_context(agent_type, agent_spec.get("context"))
            session_id = agent_spec.get("session_id")
            spawns.append((agent_type, task, context, session_id))

        valid_spawns = [
            (at, t, c, sid)
            for at, t, c, sid in spawns
            if not (isinstance(c, dict) and ("_spawn_error" in c or "_spawn_rejected" in c))
        ]

        rejected = [
            {
                "agent_type": c.get("_orig_agent_type", at.value),
                "task": t,
                "session_id": sid,
                "result": c,
            }
            for at, t, c, sid in spawns
            if isinstance(c, dict) and ("_spawn_error" in c or "_spawn_rejected" in c)
        ]

        if not valid_spawns:
            return json.dumps(
                {
                    "error": "No valid agents to spawn",
                    "agents_spawned": 0,
                    "rejected": rejected,
                    "results": [],
                }
            )

        manager = get_subagent_manager(db_pool=self._db_pool)
        results = []
        for agent_type, task, context, session_id in valid_spawns:
            result = await manager.spawn(agent_type, task, context, session_id)
            result_dict = result.to_dict()
            result_dict = _persist_image_result(result_dict)
            result_dict = _persist_audio_result(result_dict)
            results.append(result_dict)

        return json.dumps(
            {
                "parallel_execution": True,
                "agents_spawned": len(results),
                "rejected": rejected,
                "results": results,
            }
        )
