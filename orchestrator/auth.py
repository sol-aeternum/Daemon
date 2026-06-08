"""Device access-token authentication dependency.

This module provides a FastAPI dependency that authenticates requests using
device access tokens carried in the Authorization header as Bearer tokens.

Architecture decisions followed:
  - Decision 7:  Tokens are 256-bit opaque values (secrets.token_urlsafe(32))
  - Decision 8:  Tokens stored only as SHA-256 hashes (deterministic)
  - Decision 11: Access-token TTL is 30 minutes
  - Decision 19: Protected routes accept access tokens only (bearer-token only)
  - Decision 25: Route modules must be hardened

Token verification flow:
  1. Extract Authorization header, require "Bearer <token>" format
  2. Hash the presented token with SHA-256 (same as auth_tokens.hash_token)
  3. Lookup hash in sessions table, joined to devices
  4. Require: sessions.revoked_at IS NULL
            AND sessions.access_expires_at > NOW()
            AND devices.revoked_at IS NULL
  5. Throttle devices.last_seen_at update to once per 5 minutes
  6. Attach user_id, device_id, session_id to request.state

"""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import uuid

from fastapi import HTTPException, Request
import asyncpg


@dataclass
class AuthenticatedDevice:
    """Authenticated device context attached to each authenticated request."""

    user_id: uuid.UUID
    device_id: uuid.UUID
    session_id: uuid.UUID


@dataclass
class AdminOrDeviceAuth:
    """Result of admin-or-device authentication with explicit admin signal.

    The is_admin field explicitly indicates whether auth succeeded via
    admin API key (True) or device token (False). Callers must NOT
    infer admin status from user_id comparison with sentinel UUIDs.
    """

    authenticated_device: AuthenticatedDevice
    is_admin: bool


async def _verify_access_token(
    db_pool: asyncpg.Pool,
    token: str,
) -> AuthenticatedDevice | None:
    """Verify a bearer access token and return the authenticated device context.

    Returns None if the token is invalid, expired, revoked, or the device is
    revoked. Raises HTTPException on unexpected errors.

    The presented token is hashed with SHA-256 (same as auth_tokens.hash_token)
    and looked up in sessions.access_token_hash. The session must be unexpired
    and unrevoked, and its device must be unrevoked.
    """
    from orchestrator.auth_tokens import hash_token

    token_hash = hash_token(token)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                s.user_id,
                s.device_id,
                s.id AS session_id,
                s.access_expires_at,
                s.revoked_at AS session_revoked_at,
                d.revoked_at AS device_revoked_at
            FROM sessions s
            JOIN devices d ON d.id = s.device_id
            WHERE s.access_token_hash = $1
            """,
            token_hash,
        )

        if row is None:
            return None

        if row["session_revoked_at"] is not None:
            return None

        now = await conn.fetchval("SELECT NOW()")
        if row["access_expires_at"] <= now:
            return None

        if row["device_revoked_at"] is not None:
            return None

        user_id: uuid.UUID = row["user_id"]
        device_id: uuid.UUID = row["device_id"]
        session_id: uuid.UUID = row["session_id"]

        await conn.execute(
            """
            UPDATE devices
            SET last_seen_at = NOW()
            WHERE id = $1
              AND (last_seen_at IS NULL OR last_seen_at < NOW() - INTERVAL '5 minutes')
            """,
            device_id,
        )

        return AuthenticatedDevice(
            user_id=user_id,
            device_id=device_id,
            session_id=session_id,
        )


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract the bearer token from an Authorization header value.

    Returns None if:
      - authorization is None (missing header)
      - value does not start with "Bearer "
      - bearer token is empty after stripping whitespace

    Decision 19: Bearer-token only — session cookies are not access auth.
    """
    if authorization is None:
        return None
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    return token


async def require_device_auth(
    request: Request,
) -> AuthenticatedDevice:
    """FastAPI dependency: authenticate a request via device access token.

    Use in route handlers as:
        auth: AuthenticatedDevice = Depends(require_device_auth)

    Auth flow:
      1. Require Authorization header is present and has "Bearer " prefix
      2. Hash the token and look up in sessions + devices
      3. Verify session is not revoked/expired and device is not revoked
      4. Throttle devices.last_seen_at update
      5. Attach user_id, device_id, session_id to request.state
      6. Return AuthenticatedDevice for use in the handler

    Raises HTTPException 401 for:
      - Missing Authorization header
      - Malformed header (not "Bearer <token>")
      - Empty bearer token
      - Token hash not found (unknown token)
      - Expired session
      - Revoked session
      - Revoked device
    """
    authorization = request.headers.get("Authorization")
    token = _extract_bearer_token(authorization)

    if token is None:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid authorization header",
        )

    app_state = request.app.state.app_state
    db_pool = app_state.db_pool

    if db_pool is None:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )

    auth_result = await _verify_access_token(db_pool, token)

    if auth_result is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token",
        )

    request.state.user_id = auth_result.user_id
    request.state.device_id = auth_result.device_id
    request.state.session_id = auth_result.session_id

    return auth_result


async def require_admin_or_device_auth(
    request: Request,
) -> AdminOrDeviceAuth:
    """FastAPI dependency: authenticate via admin API key OR device access token.

    Use in route handlers as:
        auth: AdminOrDeviceAuth = Depends(require_admin_or_device_auth)

    This is for admin endpoints that should accept EITHER:
      - The DAEMON_ADMIN_API_KEY credential (admin key auth), OR
      - A valid device access token (device auth)

    The Authorization header is checked as admin key first, then as device token.
    Raises HTTPException 401 if neither authentication succeeds.

    Returns AdminOrDeviceAuth with explicit is_admin signal. Callers must NOT
    infer admin status by comparing user_id to sentinel UUIDs.
    """
    from orchestrator.config import get_settings

    authorization = request.headers.get("Authorization")
    token = _extract_bearer_token(authorization)

    if token is None:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid authorization header",
        )

    settings = get_settings()

    if settings.daemon_admin_api_key and hmac.compare_digest(
        token.encode(), settings.daemon_admin_api_key.encode()
    ):
        admin_device = AuthenticatedDevice(
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            device_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            session_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        )
        return AdminOrDeviceAuth(authenticated_device=admin_device, is_admin=True)

    app_state = request.app.state.app_state
    db_pool = app_state.db_pool

    if db_pool is None:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )

    auth_result = await _verify_access_token(db_pool, token)

    if auth_result is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token",
        )

    request.state.user_id = auth_result.user_id
    request.state.device_id = auth_result.device_id
    request.state.session_id = auth_result.session_id

    return AdminOrDeviceAuth(authenticated_device=auth_result, is_admin=False)
