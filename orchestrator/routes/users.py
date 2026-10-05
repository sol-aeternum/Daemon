"""User settings API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import Any

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import get_app_state, AppState
from orchestrator.memory.injection import PERSONALITY_PRESETS
from orchestrator.home_suggestions.contracts import PREFERENCE, preference_state, public_settings
from orchestrator.home_suggestions.service import HomeSuggestions

router = APIRouter(prefix="/users", tags=["users"])


class SettingsUpdate(BaseModel):
    preferences: dict[str, Any] | None = None

    @field_validator("preferences")
    @classmethod
    def validate_home_preference(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if (
            value is not None
            and PREFERENCE in value
            and (type(value[PREFERENCE]) is not bool or set(value) != {PREFERENCE})
        ):
            raise ValueError(
                "home_suggestions_enabled requires an isolated boolean preference patch"
            )
        return value


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

    return (
        public_settings(settings)
        if settings
        else {
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
    )


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

    current = await store.merge_user_preferences(auth.user_id, update.preferences or {})
    if update.preferences and PREFERENCE in update.preferences:
        try:
            # DB commits first; Redis monotonic epoch synchronization fences
            # queued/in-flight work before an opt-out is acknowledged. A stale
            # synchronizer can never restore a newer revoked epoch.
            service = HomeSuggestions(store, app_state.redis, auth.user_id)
            enabled, epoch = preference_state(current)
            if not await service.cache.sync(enabled, epoch):
                raise HTTPException(status_code=409, detail="Settings changed; reload settings")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Suggestion state unavailable; reload settings"
            ) from exc
    return {"status": "updated", "settings": public_settings(current)}


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
