"""Skill management tool for orchestrator tool surface.

Provides create, patch, delete, list, and view actions for skills,
wired to the canonical skill store (markdown files) and the
Task 2 projection/sync layer for metadata and usage tracking.
"""

from __future__ import annotations

import logging
from typing import Any

from orchestrator.skills_projection import SkillProjectionStore
from orchestrator.skills_store import (
    create_skill,
    delete_skill,
    get_skill,
    list_skills,
    update_skill,
)
from orchestrator.skills_sync import SkillSyncService
from orchestrator.tools.registry import Tool

logger = logging.getLogger(__name__)


# Source types that are protected from autonomous modification
_PROTECTED_SOURCE_TYPES = frozenset({"system", "imported", "manual"})


def _build_list_response(skills: list[Any]) -> str:
    """Return L0-safe metadata summaries only (no full markdown body)."""
    import json

    summaries = []
    for s in skills:
        summaries.append(
            {
                "id": s["id"],
                "name": s["name"],
                "description": s["description"],
                "enabled": s["enabled"],
                "updated_at": s["updated_at"],
                "source_type": s.get("source_type"),
                "allow_autonomous_edit": s.get("allow_autonomous_edit"),
                "repo_version": s.get("repo_version"),
                "local_version": s.get("local_version"),
                "use_count": s.get("use_count"),
                "last_used_at": s.get("last_used_at"),
            }
        )
    return json.dumps(summaries, indent=2)


def _check_modification_allowed(
    projection: dict[str, Any] | None,
    caller_autonomous: bool,
    allow_autonomous_edit: bool | None = None,
) -> tuple[bool, str]:
    """Check if modification is allowed based on protection rules.

    Returns (allowed, reason).
    """
    if not caller_autonomous:
        return True, ""

    source_type = projection.get("source_type") if projection else None

    if source_type is None:
        return (
            False,
            "Cannot determine protection status: skill has no projection row. "
            "Sync the skill first before attempting autonomous modification.",
        )

    if source_type not in _PROTECTED_SOURCE_TYPES:
        return True, ""

    if allow_autonomous_edit is None:
        allow_autonomous_edit = (
            projection.get("allow_autonomous_edit", False) if projection else False
        )

    if not allow_autonomous_edit:
        return (
            False,
            f"Skill with source_type='{source_type}' is protected from autonomous "
            "modification. Explicit allow_autonomous_edit=true required.",
        )

    return True, ""


class SkillManageTool(Tool):
    name = "skill_manage"
    description = (
        "Manage skills: create, patch, delete, list, or view skill definitions. "
        "list returns L0-safe metadata summaries (no content). "
        "view returns full skill content and increments usage metadata. "
        "patch performs targeted substring replacement in skill content. "
        "Protected skills (system/imported/manual) cannot be modified by autonomous "
        "agents unless allow_autonomous_edit=true."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "patch", "delete", "list", "view"],
                "description": "The management action to perform.",
            },
            "name": {
                "type": "string",
                "description": "Skill name (for create).",
            },
            "description": {
                "type": "string",
                "description": "Skill description (for create).",
            },
            "content": {
                "type": "string",
                "description": "Skill markdown content (for create).",
            },
            "skill_id": {
                "type": "string",
                "description": "Skill identifier (for patch, delete, view).",
            },
            "old_text": {
                "type": "string",
                "description": "Substring to replace (for patch). Required for targeted replacement.",
            },
            "new_text": {
                "type": "string",
                "description": "Replacement text (for patch). If empty, effectively deletes old_text.",
            },
            "source_type": {
                "type": "string",
                "enum": ["system", "imported", "manual", "autonomous"],
                "description": "Source type when creating a new skill (default: autonomous).",
            },
            "allow_autonomous_edit": {
                "type": "boolean",
                "description": "Override allow_autonomous_edit flag for protected skills (autonomous callers only).",
            },
            "caller_autonomous": {
                "type": "boolean",
                "default": False,
                "description": "Whether the caller is an autonomous agent (affects protection enforcement).",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        db_pool: Any,
    ) -> None:
        self._pool = db_pool
        self._projection_store: SkillProjectionStore | None = (
            SkillProjectionStore(db_pool) if db_pool is not None else None
        )
        self._sync_service: SkillSyncService | None = (
            SkillSyncService(self._projection_store)
            if self._projection_store is not None
            else None
        )

    async def execute(self, **kwargs: Any) -> str:
        import json

        action = kwargs.get("action")
        caller_autonomous = bool(kwargs.get("caller_autonomous", False))
        allow_autonomous_override = kwargs.get("allow_autonomous_edit")

        if action == "list":
            return await self._list()

        if action == "view":
            skill_id = kwargs.get("skill_id")
            if not skill_id:
                return json.dumps({"error": "skill_id is required for view action"})
            return await self._view(skill_id)

        if action == "patch":
            skill_id = kwargs.get("skill_id")
            old_text = kwargs.get("old_text")
            new_text = kwargs.get("new_text", "")
            if not skill_id:
                return json.dumps({"error": "skill_id is required for patch action"})
            if old_text is None:
                return json.dumps(
                    {
                        "error": "old_text is required for patch action (targeted substring replacement)"
                    }
                )
            return await self._patch(
                skill_id,
                old_text,
                new_text,
                caller_autonomous,
                allow_autonomous_override,
            )

        if action == "delete":
            skill_id = kwargs.get("skill_id")
            if not skill_id:
                return json.dumps({"error": "skill_id is required for delete action"})
            return await self._delete(
                skill_id,
                caller_autonomous,
                allow_autonomous_override,
            )

        if action == "create":
            name = kwargs.get("name")
            if not name:
                return json.dumps({"error": "name is required for create action"})
            description = kwargs.get("description", "")
            content = kwargs.get("content", "")
            source_type = kwargs.get("source_type", "autonomous")
            return await self._create(
                name,
                description,
                content,
                source_type,
                caller_autonomous,
            )

        return json.dumps({"error": f"Unknown action: {action}"})

    async def _list(self) -> str:
        try:
            skills = list_skills()

            if self._projection_store:
                try:
                    projections = await self._projection_store.list_projections()
                    proj_by_id = {p["skill_id"]: p for p in projections}
                    for s in skills:
                        proj = proj_by_id.get(s["id"])
                        if proj:
                            s["source_type"] = proj.get("source_type")
                            s["allow_autonomous_edit"] = proj.get(
                                "allow_autonomous_edit"
                            )
                            s["repo_version"] = proj.get("repo_version")
                            s["local_version"] = proj.get("local_version")
                            s["use_count"] = proj.get("use_count")
                            s["last_used_at"] = (
                                proj["last_used_at"].isoformat()
                                if proj.get("last_used_at")
                                else None
                            )
                except Exception as exc:
                    logger.warning("Failed to load projection metadata: %s", exc)

            return _build_list_response(skills)
        except Exception as exc:
            logger.error("list skills failed: %s", exc)
            import json

            return json.dumps({"error": f"list failed: {exc}"})

    async def _view(self, skill_id: str) -> str:
        import json

        try:
            detail = get_skill(skill_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Skill '{skill_id}' not found"})
        except Exception as exc:
            logger.error("view skill %s failed: %s", skill_id, exc)
            return json.dumps({"error": f"view failed: {exc}"})

        if self._projection_store:
            try:
                await self._projection_store.touch_usage(skill_id)
            except Exception as exc:
                logger.warning(
                    "Failed to update usage metadata for skill %s: %s", skill_id, exc
                )

        return json.dumps(detail, indent=2)

    async def _create(
        self,
        name: str,
        description: str,
        content: str,
        source_type: str,
        caller_autonomous: bool,
    ) -> str:
        import json

        if caller_autonomous and source_type in _PROTECTED_SOURCE_TYPES:
            return json.dumps(
                {
                    "error": (
                        f"Cannot create skill with source_type='{source_type}' "
                        "from autonomous context. Use source_type='autonomous' instead."
                    )
                }
            )

        try:
            detail = create_skill(
                name=name,
                description=description,
                content=content,
                enabled=True,
            )
        except FileExistsError:
            return json.dumps({"error": f"Skill '{name}' already exists"})
        except Exception as exc:
            logger.error("create skill failed: %s", exc)
            return json.dumps({"error": f"create failed: {exc}"})

        if self._sync_service:
            try:
                _ = await self._sync_service.sync_skill(
                    detail["id"], source_type=source_type
                )
            except Exception as exc:
                logger.warning("Failed to sync new skill to projection: %s", exc)

        return json.dumps(
            {
                "skill_id": detail["id"],
                "name": detail["name"],
                "description": detail["description"],
                "source_type": source_type,
                "created": True,
            },
            indent=2,
        )

    async def _patch(
        self,
        skill_id: str,
        old_text: str,
        new_text: str,
        caller_autonomous: bool,
        allow_autonomous_override: bool | None,
    ) -> str:
        import json

        try:
            detail = get_skill(skill_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Skill '{skill_id}' not found"})
        except Exception as exc:
            logger.error("patch get skill %s failed: %s", skill_id, exc)
            return json.dumps({"error": f"patch failed: {exc}"})

        current_content = detail["content"]

        projection = None
        if self._projection_store:
            try:
                projection = await self._projection_store.get_projection(skill_id)
            except Exception as exc:
                logger.warning(
                    "Failed to load projection for protection check: %s", exc
                )

        if projection is None and caller_autonomous and self._sync_service:
            sync_result = await self._sync_service.sync_skill(skill_id)
            if sync_result.success and self._projection_store:
                try:
                    projection = await self._projection_store.get_projection(skill_id)
                except Exception:
                    pass

        allowed, reason = _check_modification_allowed(
            projection,
            caller_autonomous,
            allow_autonomous_override,
        )
        if not allowed:
            return json.dumps({"error": reason})

        if old_text not in current_content:
            return json.dumps(
                {
                    "error": (
                        f"old_text not found in skill content. "
                        "patch requires exact substring match."
                    )
                }
            )

        new_content = current_content.replace(old_text, new_text, 1)

        try:
            update_skill(
                skill_id,
                name=None,
                description=None,
                content=new_content,
                enabled=None,
            )
        except Exception as exc:
            logger.error("patch update skill %s failed: %s", skill_id, exc)
            return json.dumps({"error": f"patch failed: {exc}"})

        if self._sync_service:
            try:
                _ = await self._sync_service.sync_skill(skill_id)
            except Exception as exc:
                logger.warning("Failed to sync patched skill to projection: %s", exc)

        return json.dumps(
            {
                "skill_id": skill_id,
                "patched": True,
                "replaced": old_text,
                "with": new_text,
            },
            indent=2,
        )

    async def _delete(
        self,
        skill_id: str,
        caller_autonomous: bool,
        allow_autonomous_override: bool | None,
    ) -> str:
        import json

        projection = None
        if self._projection_store:
            try:
                projection = await self._projection_store.get_projection(skill_id)
            except Exception as exc:
                logger.warning(
                    "Failed to load projection for protection check: %s", exc
                )

        if projection is None and caller_autonomous and self._sync_service:
            sync_result = await self._sync_service.sync_skill(skill_id)
            if sync_result.success and self._projection_store:
                try:
                    projection = await self._projection_store.get_projection(skill_id)
                except Exception:
                    pass

        allowed, reason = _check_modification_allowed(
            projection,
            caller_autonomous,
            allow_autonomous_override,
        )
        if not allowed:
            return json.dumps({"error": reason})

        try:
            delete_skill(skill_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Skill '{skill_id}' not found"})
        except Exception as exc:
            logger.error("delete skill %s failed: %s", skill_id, exc)
            return json.dumps({"error": f"delete failed: {exc}"})

        if self._sync_service:
            try:
                _ = await self._sync_service.delete_skill_projection(skill_id)
            except Exception as exc:
                logger.warning("Failed to delete skill projection: %s", exc)

        return json.dumps(
            {
                "skill_id": skill_id,
                "deleted": True,
            },
            indent=2,
        )
