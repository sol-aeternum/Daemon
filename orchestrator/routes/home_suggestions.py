"""Authenticated no-store home list and explicit refresh (GET never generates)."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import AppState, get_app_state
from orchestrator.home_suggestions.contracts import SuggestionError
from orchestrator.home_suggestions.contracts import preference_state
from orchestrator.home_suggestions.service import HomeSuggestions

router = APIRouter(prefix="/home-suggestions", tags=["home-suggestions"])
NO_STORE = {"Cache-Control": "no-store"}


@router.get("")
async def list_home_suggestions(
    state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    enabled = False
    try:
        if state.memory_store is None:
            raise SuggestionError()
        enabled, _ = preference_state(await state.memory_store.get_user_settings(auth.user_id))
        if not enabled:
            return JSONResponse(
                {"enabled": False, "status": "disabled", "suggestions": []}, headers=NO_STORE
            )
        service = HomeSuggestions(state.memory_store, state.redis, auth.user_id)
        result = await service.list()
    except Exception:
        # Do not assert enabled when its authoritative DB state is unreadable.
        result = {"enabled": enabled, "status": "unavailable", "suggestions": []}
    return JSONResponse(result, headers=NO_STORE)


@router.post("/refresh")
async def refresh_home_suggestions(
    state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    try:
        result = await HomeSuggestions(state.memory_store, state.redis, auth.user_id).refresh(
            manual=True
        )
        return JSONResponse(
            result, status_code=202 if result["status"] == "queued" else 200, headers=NO_STORE
        )
    except SuggestionError as exc:
        return JSONResponse(
            {"status": "unavailable", "reason": exc.code},
            status_code=exc.status,
            headers={**NO_STORE, **({"Retry-After": "3600"} if exc.status == 429 else {})},
        )
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503, headers=NO_STORE)
