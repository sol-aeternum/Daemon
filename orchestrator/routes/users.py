"""User settings API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any
import uuid

from orchestrator.db import get_app_state, AppState
from orchestrator.memory.injection import PERSONALITY_PRESETS

router = APIRouter(prefix="/users", tags=["users"])

DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class SettingsUpdate(BaseModel):
    preferences: dict[str, Any] | None = None


@router.get("/me/settings")
async def get_settings(app_state: AppState = Depends(get_app_state)):
    """Get current user settings."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    settings = await store.get_user_settings(DEFAULT_USER_ID)

    return settings or {
        "preferences": {
            "personality": "default",
            "custom_instructions": "",
            "characteristics": {
                "warmth": "default",
                "enthusiasm": "default",
                "emoji": "default",
                "formatting": "default",
            },
        }
    }


@router.patch("/me/settings")
async def update_settings(
    update: SettingsUpdate,
    app_state: AppState = Depends(get_app_state),
):
    """Update user settings (partial merge)."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    # Get current settings
    current = await store.get_user_settings(DEFAULT_USER_ID) or {}

    # Deep merge
    if update.preferences:
        current.setdefault("preferences", {})
        for key, value in update.preferences.items():
            if isinstance(value, dict) and isinstance(
                current["preferences"].get(key), dict
            ):
                current["preferences"][key].update(value)
            else:
                current["preferences"][key] = value

    # Save
    await store.update_user_settings(DEFAULT_USER_ID, current)
    return {"status": "updated", "settings": current}


@router.get("/me/settings/presets")
async def list_presets():
    """List available personality presets."""
    return {
        "presets": [
            {"id": k, "label": k.replace("_", " ").title(), "description": v}
            for k, v in PERSONALITY_PRESETS.items()
        ]
    }
