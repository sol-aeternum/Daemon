"""Spawn agent tool for orchestrator."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from orchestrator.tools.registry import Tool
from orchestrator.config import get_settings
from orchestrator.subagents.base import SubagentType, SubagentManager
from orchestrator.subagents.research import ResearchSubagent
from orchestrator.subagents.image import ImageSubagent
from orchestrator.subagents.audio import AudioSubagent
from orchestrator.subagents.document import DocumentSubagent

logger = logging.getLogger(__name__)

GENERATED_IMAGES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "generated_images"
)
GENERATED_AUDIO_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "generated_audio"
)


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

    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    content_hash = hashlib.sha256(image_base64.encode("utf-8")).hexdigest()[:16]
    ext = data.get("format", "png")
    filename = f"{content_hash}.{ext}"
    filepath = GENERATED_IMAGES_DIR / filename

    try:
        raw = base64.b64decode(image_base64)
        filepath.write_bytes(raw)
        logger.info(f"Saved generated image to {filepath} ({len(raw)} bytes)")
    except Exception as e:
        logger.error(f"Failed to save generated image: {e}")
        return result_dict

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

    GENERATED_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    content_hash = hashlib.sha256(audio_base64.encode("utf-8")).hexdigest()[:16]
    ext = data.get("format", "mp3")
    filename = f"{content_hash}.{ext}"
    filepath = GENERATED_AUDIO_DIR / filename

    try:
        raw = base64.b64decode(audio_base64)
        filepath.write_bytes(raw)
        logger.info(f"Saved generated audio to {filepath} ({len(raw)} bytes)")
    except Exception as e:
        logger.error(f"Failed to save generated audio: {e}")
        return result_dict

    result_dict = {**result_dict, "data": {**data}}
    result_dict["data"].pop("audio_base64", None)
    result_dict["data"].pop("audio_url", None)
    result_dict["data"]["audio_path"] = f"/generated-audio/{filename}"

    return result_dict


def _persist_file_result(result_dict: dict[str, Any]) -> dict[str, Any]:
    """Handle DocumentSubagent results - preserve file path and generation_code.

    DocumentSubagent already persists files to data/generated_files/ via its
    _persist_file method. This function ensures generation_code is preserved
    in metadata for revision flows.
    """
    # Check if this is a document result
    agent_type = result_dict.get("agent_type")
    if agent_type != "document":
        return result_dict

    data = result_dict.get("data")
    if not isinstance(data, dict):
        return result_dict

    # DocumentSubagent already saved the file and returned file_url
    # Just ensure generation_code is in metadata for revisions
    generation_code = data.get("generation_code")
    if generation_code:
        # Move generation_code to metadata for persistence
        result_dict["metadata"] = result_dict.get("metadata", {})
        result_dict["metadata"]["generation_code"] = generation_code

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
            "openrouter_api_key": settings.openrouter_api_key,
            "openrouter_base_url": settings.openrouter_base_url,
            "image_model": image_model,
            "tier_config": tier_config,  # Pass tier config for video generation checks
            "db_pool": db_pool,
        }
        _subagent_manager = SubagentManager()
        # Register default subagents
        _subagent_manager.register(ResearchSubagent(shared_config))
        _subagent_manager.register(ImageSubagent(shared_config))
        _subagent_manager.register(AudioSubagent(shared_config))
        _subagent_manager.register(DocumentSubagent(shared_config))
    elif db_pool is not None:
        image_agent = _subagent_manager.get(SubagentType.IMAGE)
        if image_agent is not None:
            image_agent.config["db_pool"] = db_pool
    return _subagent_manager


class SpawnAgentTool(Tool):
    """Tool to spawn specialized subagents for complex tasks."""

    name = "spawn_agent"
    description = "Spawn a specialized subagent for research, image generation, video generation, sound effect generation, code tasks, or document reading or document generation"
    parameters = {
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "description": "Type of subagent to spawn",
                "enum": ["research", "image", "audio", "code", "reader", "document"],
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

    def _document_spawn_allowed(self) -> bool:
        """Return True if document spawning is explicitly authorized in trusted context."""
        doc_trust = self._trusted_spawn_context.get("document")
        if isinstance(doc_trust, dict):
            return doc_trust.get("enabled") is True
        return doc_trust is True

    def _apply_trusted_context(
        self,
        agent_type: SubagentType,
        context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if agent_type == SubagentType.IMAGE:
            merged_context: dict[str, Any] = dict(context or {})
            trusted_video = self._trusted_spawn_context.get("video")
            if isinstance(trusted_video, dict):
                merged_context["mode"] = "video"
                for key in (
                    "duration",
                    "tier",
                    "user_id",
                    "source_mode",
                    "reference_image_url",
                    "reference_image_id",
                ):
                    value = trusted_video.get(key)
                    if value is not None:
                        merged_context[key] = value
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

        if subagent_type == SubagentType.DOCUMENT and not self._document_spawn_allowed():
            return json.dumps(
                {
                    "error": "document agent requires explicit authorization",
                    "agent_type": "document",
                    "hint": "trusted_spawn_context with document.enabled=true is required",
                }
            )
        context = self._apply_trusted_context(subagent_type, context)

        manager = get_subagent_manager(db_pool=self._db_pool)
        result = await manager.spawn(subagent_type, task, context, session_id)
        result_dict = result.to_dict()
        result_dict = _persist_image_result(result_dict)
        result_dict = _persist_audio_result(result_dict)
        result_dict = _persist_file_result(result_dict)

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
                            "enum": ["research", "image", "audio", "code", "reader", "document"],
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

    def _document_spawn_allowed(self) -> bool:
        """Return True if document spawning is explicitly authorized in trusted context."""
        doc_trust = self._trusted_spawn_context.get("document")
        if isinstance(doc_trust, dict):
            return doc_trust.get("enabled") is True
        return doc_trust is True

    def _apply_trusted_context(
        self,
        agent_type: SubagentType,
        context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if agent_type == SubagentType.IMAGE:
            merged_context: dict[str, Any] = dict(context or {})
            trusted_video = self._trusted_spawn_context.get("video")
            if isinstance(trusted_video, dict):
                merged_context["mode"] = "video"
                for key in (
                    "duration",
                    "tier",
                    "user_id",
                    "source_mode",
                    "reference_image_url",
                    "reference_image_id",
                ):
                    value = trusted_video.get(key)
                    if value is not None:
                        merged_context[key] = value
            return merged_context
        if agent_type == SubagentType.DOCUMENT:
            if not self._document_spawn_allowed():
                return None
        return context

    async def execute(self, **kwargs: Any) -> str:
        """Execute multiple subagents in parallel."""
        agents = kwargs.get("agents", [])
        manager = get_subagent_manager(db_pool=self._db_pool)

        spawns: list[tuple[SubagentType, str, dict[str, Any] | None, str | None]] = []
        for agent_spec in agents:
            agent_type_str = agent_spec.get("agent_type", "")
            try:
                agent_type = SubagentType(agent_type_str.lower())
            except ValueError:
                spawns.append((
                    SubagentType.IMAGE,
                    "",
                    {"_spawn_error": f"Unknown agent_type: {agent_type_str}"},
                    None,
                ))
                continue
            if agent_type == SubagentType.DOCUMENT and not self._document_spawn_allowed():
                spawns.append((
                    SubagentType.DOCUMENT,
                    agent_spec.get("task", ""),
                    {"_spawn_rejected": "document agent requires explicit authorization"},
                    agent_spec.get("session_id"),
                ))
                continue
            task = agent_spec.get("task", "")
            context = self._apply_trusted_context(
                agent_type, agent_spec.get("context")
            )
            session_id = agent_spec.get("session_id")
            spawns.append((agent_type, task, context, session_id))

        valid_spawns = [
            (at, t, c, sid)
            for at, t, c, sid in spawns
            if not (
                isinstance(c, dict)
                and ("_spawn_error" in c or "_spawn_rejected" in c)
            )
        ]

        rejected = [
            {
                "agent_type": at.value,
                "task": t,
                "session_id": sid,
                "result": c,
            }
            for at, t, c, sid in spawns
            if isinstance(c, dict)
            and ("_spawn_error" in c or "_spawn_rejected" in c)
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

        results = []
        for agent_type, task, context, session_id in valid_spawns:
            result = await manager.spawn(agent_type, task, context, session_id)
            result_dict = result.to_dict()
            result_dict = _persist_image_result(result_dict)
            result_dict = _persist_audio_result(result_dict)
            result_dict = _persist_file_result(result_dict)
            results.append(result_dict)

        return json.dumps(
            {
                "parallel_execution": True,
                "agents_spawned": len(results),
                "rejected": rejected,
                "results": results,
            }
        )
