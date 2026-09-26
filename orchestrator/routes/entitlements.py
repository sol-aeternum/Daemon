"""Authenticated, server-resolved commercial entitlement snapshot."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import AppState, get_app_state
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.errors import EntitlementsError

router = APIRouter(tags=["entitlements"])


@router.get("/users/me/entitlements")
async def get_entitlements(
    auth: AuthenticatedDevice = Depends(require_device_auth),
    app_state: AppState = Depends(get_app_state),
) -> dict[str, Any]:
    if app_state.db_pool is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "entitlements_unavailable", "message": "Entitlements unavailable"},
        )
    try:
        return await EntitlementService(app_state.db_pool).public_snapshot(auth.user_id)
    except EntitlementsError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "entitlements_unavailable", "message": "Entitlements unavailable"},
        ) from exc
