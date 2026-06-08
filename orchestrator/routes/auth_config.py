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


router = APIRouter(tags=["auth-config"])


@router.api_route("", methods=["GET"], include_in_schema=True)
async def get_auth_config() -> Response:
    """Return deployment mode and provider availability flags.

    Reads exactly four fields from settings:
        - daemon_deployment_mode  -> "mode"
        - daemon_email_enabled    -> "email.enabled"
        - daemon_google_enabled   -> "google.enabled"
        - daemon_google_client_id -> "google.clientId" (public, not a secret)

    Returns 200 with Cache-Control: no-store. No other config field is
    serialized; the FORBIDDEN_FIELDS contract is enforced by tests, not
    by this function (the function only reads four fields, by
    construction).
    """
    settings = get_settings()
    body = {
        "mode": settings.daemon_deployment_mode,
        "email": {
            "enabled": settings.daemon_email_enabled,
        },
        "google": {
            "enabled": settings.daemon_google_enabled,
            "clientId": settings.daemon_google_client_id or "",
        },
    }
    return JSONResponse(
        content=body,
        headers={"Cache-Control": "no-store"},
    )
