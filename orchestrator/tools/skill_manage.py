"""Skill management tool for the orchestrator tool surface.

Create, patch, delete, list and view the calling account's skills through
the account-owned skill store. The tool is built for one account and has no
argument that names another.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from orchestrator.services.skill_index import embed_skill
from orchestrator.services.skills import SkillError, SkillStore
from orchestrator.tools.registry import Tool

logger = logging.getLogger(__name__)

# Every caller of this tool is model-driven (chat tool calls), so every call is
# autonomous. Authority is never an argument: these fields are refused rather
# than ignored so a spoof attempt is visible (#696).
_REFUSED_AUTHORITY_ARGUMENTS = ("caller_autonomous", "allow_autonomous_edit")

_REFUSALS = {
    "not_found": "Skill not found",
    "slug_taken": "A skill with that name already exists",
    "shipped_read_only": (
        "Shipped skills are read-only and protected from modification. "
        "Create a skill of your own instead."
    ),
    "autonomous_edit_not_granted": (
        "This skill is protected from autonomous modification. The owner can "
        "allow autonomous edits for it in skill settings."
    ),
    "patch_text_not_found": (
        "old_text not found in skill content. patch requires exact substring match."
    ),
    "limit_reached": "The account's skill limit is reached",
}


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _refusal(exc: SkillError) -> str:
    return _error(_REFUSALS.get(exc.reason, f"Refused: {exc.reason}"))


def _summary(skill: dict[str, Any]) -> dict[str, Any]:
    """L0-safe metadata only (no body)."""
    return {
        "id": skill["slug"],
        "name": skill["name"],
        "description": skill["description"],
        "enabled": skill["enabled"],
        "updated_at": skill["updated_at"],
        "scope": skill["scope"],
        "source_type": skill["source_type"],
        "allow_autonomous_edit": skill["allow_autonomous_edit"],
        "use_count": skill["use_count"],
        "last_used_at": skill["last_used_at"],
    }


class SkillManageTool(Tool):
    name = "skill_manage"
    description = (
        "Manage skills: create, patch, delete, list, or view skill definitions. "
        "list returns L0-safe metadata summaries (no content). "
        "view returns full skill content and increments usage metadata. "
        "patch performs targeted substring replacement in skill content. "
        "Shipped skills are read-only. Other protected skills (imported/manual) "
        "cannot be modified here unless the owner has allowed autonomous edits "
        "for that skill in settings. New skills are always created as autonomous."
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
        },
        "required": ["action"],
    }

    def __init__(self, store: SkillStore | None, user_id: uuid.UUID | None) -> None:
        # Without a store and an account the tool stays registered but refuses.
        self._store = store
        self._user_id = user_id

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action")
        refused = [key for key in _REFUSED_AUTHORITY_ARGUMENTS if key in kwargs]
        if refused:
            return _error(
                f"Unsupported argument(s): {', '.join(refused)}. Authority is not an input."
            )
        source_type = kwargs.get("source_type", "autonomous")
        if source_type != "autonomous":
            return _error(
                f"Cannot use source_type='{source_type}' from autonomous context. "
                "Skills created here are always source_type='autonomous'."
            )
        store, user_id = self._store, self._user_id
        if store is None or user_id is None:
            return _error("Skills are unavailable: no account or skill storage.")

        skill_id = kwargs.get("skill_id")
        try:
            if action == "list":
                skills = await store.list(user_id)
                return json.dumps([_summary(skill) for skill in skills], indent=2)

            if action == "create":
                name = kwargs.get("name")
                if not name:
                    return _error("name is required for create action")
                skill = await store.create(
                    user_id,
                    name=name,
                    description=kwargs.get("description", ""),
                    body=kwargs.get("content", ""),
                    actor="autonomous",
                )
                await embed_skill(store, skill)
                return json.dumps(
                    {
                        "skill_id": skill["slug"],
                        "name": skill["name"],
                        "description": skill["description"],
                        "source_type": skill["source_type"],
                        "created": True,
                    },
                    indent=2,
                )

            if action not in {"view", "patch", "delete"}:
                return _error(f"Unknown action: {action}")
            if not skill_id:
                return _error(f"skill_id is required for {action} action")

            if action == "view":
                skill = await store.get(user_id, skill_id)
                if skill["scope"] == "account":
                    await store.touch_usage(user_id, skill_id)
                return json.dumps({**_summary(skill), "content": skill["body"]}, indent=2)

            if action == "patch":
                old_text = kwargs.get("old_text")
                new_text = kwargs.get("new_text", "")
                if old_text is None:
                    return _error(
                        "old_text is required for patch action (targeted substring replacement)"
                    )
                skill = await store.patch(user_id, skill_id, old_text, new_text, actor="autonomous")
                await embed_skill(store, skill)
                return json.dumps(
                    {
                        "skill_id": skill["slug"],
                        "patched": True,
                        "replaced": old_text,
                        "with": new_text,
                    },
                    indent=2,
                )

            await store.delete(user_id, skill_id, actor="autonomous")
            return json.dumps({"skill_id": skill_id, "deleted": True}, indent=2)
        except SkillError as exc:
            return _refusal(exc)
        except Exception:
            logger.error("skill_manage %s failed", action, exc_info=True)
            return _error(f"{action} failed")
