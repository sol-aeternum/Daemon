"""User settings API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import get_app_state, AppState
from orchestrator.memory.injection import PERSONALITY_PRESETS

router = APIRouter(prefix="/users", tags=["users"])


class SettingsUpdate(BaseModel):
    preferences: dict[str, Any] | None = None


@router.get("/me/settings")
async def get_settings(
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Get current user settings."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    settings = await store.get_user_settings(auth.user_id)

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
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Update user settings (partial merge)."""
    store = app_state.memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")

    # Get current settings
    current = await store.get_user_settings(auth.user_id) or {}

    # Deep merge
    if update.preferences:
        current.setdefault("preferences", {})
        for key, value in update.preferences.items():
            if isinstance(value, dict) and isinstance(current["preferences"].get(key), dict):
                current["preferences"][key].update(value)
            else:
                current["preferences"][key] = value

    # Save
    await store.update_user_settings(auth.user_id, current)
    return {"status": "updated", "settings": current}


@router.get("/me/settings/presets")
async def list_presets(
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """List available personality presets."""
    return {
        "presets": [
            {"id": k, "label": k.replace("_", " ").title(), "description": v}
            for k, v in PERSONALITY_PRESETS.items()
        ]
    }
