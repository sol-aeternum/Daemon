"""Public runtime auth config endpoint.

Returns non-secret runtime data so the frontend can render the right
landing page and gate provider affordances without rebuilding with a
different `NEXT_PUBLIC_*` env. Authenticated: NO (intentional — the
endpoint exists to let the frontend decide where to send an
unauthenticated user). Cache-Control: no-store (intentional — a
misconfigured CDN must not serve a stale mode to a user whose
deployment just changed posture).
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from orchestrator.config import get_settings


router = APIRouter(prefix="/v1/auth/config", tags=["auth-config"])


@router.get("", include_in_schema=True)
async def get_auth_config() -> Response:
    """Return deployment mode and provider availability flags.

    Reads exactly five fields from settings:
        - daemon_deployment_mode           -> "mode"
        - daemon_hosted_identity_enabled   -> gates provider enabled flags
        - daemon_email_enabled             -> "email.enabled" when identity is enabled
        - daemon_google_enabled            -> "google.enabled" when identity is enabled
        - daemon_google_client_id          -> "google.clientId" (public, not a secret)

    Returns 200 with Cache-Control: no-store. No other config field is
    serialized; the FORBIDDEN_FIELDS contract is enforced by tests, not
    by this function (the function reads only the runtime-safe fields, by
    construction).
    """
    settings = get_settings()
    identity_enabled = settings.daemon_hosted_identity_enabled
    body = {
        "mode": settings.daemon_deployment_mode,
        "email": {
            "enabled": identity_enabled and settings.daemon_email_enabled,
        },
        "google": {
            "enabled": identity_enabled and settings.daemon_google_enabled,
            "clientId": settings.daemon_google_client_id or "",
        },
    }
    return JSONResponse(
        content=body,
        headers={"Cache-Control": "no-store"},
    )
