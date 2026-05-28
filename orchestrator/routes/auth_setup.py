"""First-boot setup and device enrollment endpoints and helpers.

Architecture decisions followed:
  - Decision 3: First boot is zero-active-device based
  - Decision 4: Setup token hash is app-state only
  - Decision 6: Setup is transaction-locked
  - Decision 7: Tokens are 256-bit opaque values
  - Decision 8: Token storage uses SHA-256 hashes
  - Decision 9: Enrollment codes use HMAC-SHA256 verifiers keyed by DAEMON_AUTH_PEPPER
  - Decision 11: Access-token TTL is 30 minutes
  - Decision 12: Refresh-token TTL is 90 days
  - Decision 13: Enrollment TTL is 10 minutes
  - Decision 14: Wrong enrollment attempts start at 3
  - Decision 16: Web refresh cookie is __Host-daemon_refresh
  - Decision 18: Cookie-backed endpoints require CSRF/origin checks
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.auth_cookies import (
    COOKIE_NAME,
    build_refresh_cookie,
    clear_refresh_cookie,
    make_refresh_cookie_config,
)
from orchestrator.auth_csrf import check_csrf_origin
from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.auth_tokens import (
    generate_enrollment_code,
    generate_token,
    hash_enrollment_code,
    hash_token,
    verify_enrollment_code,
    verify_token,
)
from orchestrator.config import get_settings
from orchestrator.db import get_app_state

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/v1/auth", tags=["auth"])

SINGLETON_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ACCESS_TOKEN_TTL_MINUTES = 30
REFRESH_TOKEN_TTL_DAYS = 90
ENROLLMENT_TTL_MINUTES = 10
ENROLLMENT_WRONG_ATTEMPTS_INITIAL = 3


class SetupRequest(BaseModel):
    setup_token: str


class SetupResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"


async def _find_or_create_singleton_user(conn) -> uuid.UUID:
    row = await conn.fetchrow(
        "SELECT id FROM users WHERE id = $1",
        SINGLETON_USER_ID,
    )
    if row is not None:
        return row["id"]
    return await conn.fetchval(
        """
        INSERT INTO users (id, email, name, username, preferences, settings)
        VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6::jsonb)
        ON CONFLICT (id) DO UPDATE SET id = EXCLUDED.id
        RETURNING id
        """,
        SINGLETON_USER_ID,
        "setup@daemon.local",
        "Default User",
        "daemon",
        "{}",
        "{}",
    )


async def _create_first_device_and_session(
    conn,
    user_id: uuid.UUID,
) -> tuple[str, str]:
    device_id = await conn.fetchval(
        """
        INSERT INTO devices (user_id, display_name, platform)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        user_id,
        "First Device",
        "web",
    )
    now = datetime.now(timezone.utc)
    access_expires = now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)
    refresh_expires = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
    access_token = generate_token()
    refresh_token = generate_token()
    await conn.execute(
        """
        INSERT INTO sessions (
            user_id, device_id, client_kind,
            access_token_hash, access_expires_at,
            refresh_token_hash, refresh_expires_at,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        user_id,
        device_id,
        "web",
        hash_token(access_token),
        access_expires,
        hash_token(refresh_token),
        refresh_expires,
        now,
    )
    return access_token, refresh_token


@router.post("/setup", response_model=SetupResponse)
async def setup_endpoint(
    request: Request,
    response: Response,
    body: SetupRequest,
) -> SetupResponse:
    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    settings = get_settings()
    csrf_result = check_csrf_origin(
        request_origin=request.headers.get("Origin"),
        sec_fetch_site=request.headers.get("Sec-Fetch-Site"),
        referer=request.headers.get("Referer"),
        allowed_origins=[o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()],
        public_origin=settings.daemon_public_origin,
        has_cookie=request.cookies.get(COOKIE_NAME) is not None,
    )
    if not csrf_result.allowed:
        raise HTTPException(
            status_code=403,
            detail=f"CSRF/origin check failed: {csrf_result.reason}",
        )

    if app_state.setup_token_hash is None:
        raise HTTPException(
            status_code=409,
            detail="setup_already_complete",
        )

    if not verify_token(body.setup_token, app_state.setup_token_hash):
        raise HTTPException(status_code=401, detail="Invalid setup token")

    async with app_state.db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('daemon:first_boot_setup'))"
            )

            active_count = await conn.fetchval(
                "SELECT COUNT(*) FROM devices WHERE revoked_at IS NULL"
            )
            if active_count > 0:
                app_state.setup_token_hash = None
                raise HTTPException(
                    status_code=409,
                    detail="setup_already_complete",
                )

            user_id = await _find_or_create_singleton_user(conn)
            access_token, refresh_token = await _create_first_device_and_session(
                conn, user_id
            )

    app_state.setup_token_hash = None

    cookie_config = make_refresh_cookie_config(
        cookie_secure=settings.daemon_cookie_secure,
        environment=settings.daemon_environment,
    )
    refresh_max_age = int(timedelta(days=REFRESH_TOKEN_TTL_DAYS).total_seconds())
    cookie_headers = build_refresh_cookie(
        value=refresh_token,
        config=cookie_config,
        max_age=refresh_max_age,
    )
    for header_name, header_value in cookie_headers.items():
        response.headers[header_name] = header_value

    return SetupResponse(access_token=access_token)


class EnrollStartResponse(BaseModel):
    pending_id: str
    code: str
    qr_payload: str
    expires_at: int


class EnrollCompleteRequest(BaseModel):
    pending_id: str
    code: str
    client_kind: str


class EnrollCompleteResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"


@router.post("/enroll/start", response_model=EnrollStartResponse)
async def enroll_start_endpoint(
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> EnrollStartResponse:
    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    settings = get_settings()
    pepper = validate_and_get_pepper(settings)

    pending_id = uuid.uuid4()
    code = generate_enrollment_code()
    code_verifier_hash = hash_enrollment_code(code, pepper)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=ENROLLMENT_TTL_MINUTES)

    async with app_state.db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pending_enrollments
                (id, user_id, created_by_device_id, code_verifier_hash,
                 wrong_attempts_remaining, expires_at, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            pending_id,
            auth.user_id,
            auth.device_id,
            code_verifier_hash,
            ENROLLMENT_WRONG_ATTEMPTS_INITIAL,
            expires_at,
            now,
        )

    qr_payload = f"daemon-enroll://{pending_id}#{code}"

    return EnrollStartResponse(
        pending_id=str(pending_id),
        code=code,
        qr_payload=qr_payload,
        expires_at=int(expires_at.timestamp()),
    )


@router.post("/enroll/complete", response_model=EnrollCompleteResponse, response_model_exclude_none=True)
async def enroll_complete_endpoint(
    request: Request,
    response: Response,
    body: EnrollCompleteRequest,
) -> EnrollCompleteResponse:
    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if body.client_kind not in ("web", "native"):
        raise HTTPException(
            status_code=400,
            detail="client_kind must be 'web' or 'native'",
        )

    settings = get_settings()
    pepper = validate_and_get_pepper(settings)

    has_cookie = request.cookies.get(COOKIE_NAME) is not None

    if body.client_kind == "native" and has_cookie:
        raise HTTPException(
            status_code=400,
            detail="cookie present but client_kind is 'native'",
        )

    if body.client_kind == "web":
        csrf_result = check_csrf_origin(
            request_origin=request.headers.get("Origin"),
            sec_fetch_site=request.headers.get("Sec-Fetch-Site"),
            referer=request.headers.get("Referer"),
            allowed_origins=[o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()],
            public_origin=settings.daemon_public_origin,
            has_cookie=has_cookie,
        )
        if not csrf_result.allowed:
            raise HTTPException(
                status_code=403,
                detail=f"CSRF/origin check failed: {csrf_result.reason}",
            )

    try:
        pending_uuid = uuid.UUID(body.pending_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=401,
            detail="Invalid enrollment session",
        )

    async with app_state.db_pool.acquire() as conn:
        async with conn.transaction():
            pending_row = await conn.fetchrow(
                """
                SELECT id, user_id, code_verifier_hash,
                       wrong_attempts_remaining, expires_at, consumed_at
                FROM pending_enrollments
                WHERE id = $1
                FOR UPDATE
                """,
                pending_uuid,
            )

            if pending_row is None:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid enrollment session",
                )

            if pending_row["consumed_at"] is not None:
                raise HTTPException(
                    status_code=410,
                    detail="Enrollment session already consumed",
                )

            now = await conn.fetchval("SELECT NOW()")
            if pending_row["expires_at"] <= now:
                raise HTTPException(
                    status_code=410,
                    detail="Enrollment session expired",
                )

            if pending_row["wrong_attempts_remaining"] <= 0:
                raise HTTPException(
                    status_code=410,
                    detail="Enrollment session expired",
                )

            try:
                code_valid = verify_enrollment_code(
                    body.code,
                    pepper,
                    pending_row["code_verifier_hash"],
                )
            except (ValueError, AttributeError):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid enrollment code",
                )

            if not code_valid:
                wrong_attempts = pending_row["wrong_attempts_remaining"] - 1
                if wrong_attempts <= 0:
                    await conn.execute(
                        """
                        UPDATE pending_enrollments
                        SET wrong_attempts_remaining = 0
                        WHERE id = $1
                        """,
                        pending_row["id"],
                    )
                    raise HTTPException(
                        status_code=410,
                        detail="Enrollment session expired",
                    )
                await conn.execute(
                    """
                    UPDATE pending_enrollments
                    SET wrong_attempts_remaining = $1
                    WHERE id = $2
                    """,
                    wrong_attempts,
                    pending_row["id"],
                )
                raise HTTPException(
                    status_code=401,
                    detail="Invalid enrollment code",
                )

            user_id: uuid.UUID = pending_row["user_id"]
            device_id = await conn.fetchval(
                """
                INSERT INTO devices (user_id, display_name, platform)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                user_id,
                "Enrolled Device",
                body.client_kind,
            )

            now = datetime.now(timezone.utc)
            access_expires = now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)
            refresh_expires = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
            access_token = generate_token()
            refresh_token = generate_token()

            await conn.execute(
                """
                INSERT INTO sessions (
                    user_id, device_id, client_kind,
                    access_token_hash, access_expires_at,
                    refresh_token_hash, refresh_expires_at,
                    created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                user_id,
                device_id,
                body.client_kind,
                hash_token(access_token),
                access_expires,
                hash_token(refresh_token),
                refresh_expires,
                now,
            )

            await conn.execute(
                """
                UPDATE pending_enrollments
                SET consumed_at = $1
                WHERE id = $2
                """,
                now,
                pending_row["id"],
            )

    if body.client_kind == "web":
        cookie_config = make_refresh_cookie_config(
            cookie_secure=settings.daemon_cookie_secure,
            environment=settings.daemon_environment,
        )
        refresh_max_age = int(timedelta(days=REFRESH_TOKEN_TTL_DAYS).total_seconds())
        cookie_headers = build_refresh_cookie(
            value=refresh_token,
            config=cookie_config,
            max_age=refresh_max_age,
        )
        for header_name, header_value in cookie_headers.items():
            response.headers[header_name] = header_value
        return EnrollCompleteResponse(access_token=access_token)

    return EnrollCompleteResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=RefreshResponse, response_model_exclude_none=True)
async def refresh_endpoint(
    request: Request,
    response: Response,
    body: RefreshRequest | None = Body(None),
) -> RefreshResponse:
    """Rotate a refresh token to issue a new access token and replacement refresh.

    Two modes:
    - Web (cookie): reads __Host-daemon_refresh cookie, requires no body refresh_token,
      creates client_kind='web' session, sets replacement cookie, returns access token only.
    - Native (body): reads body.refresh_token, requires no cookie, creates client_kind='native'
      session, returns access_token + replacement refresh_token JSON, no cookie.

    Mixed cookie + body refresh_token together returns 400 before any rotation.

    Atomic consume pattern:
    - UPDATE ... WHERE refresh_token_hash=$hash AND refresh_consumed_at IS NULL ... RETURNING
    - On zero rows: second lookup by hash distinguishes bad/expired/revoked (401) vs consumed (revoke device).
    - Consumed reuse: revoke device + all sessions, clear cookie, log sanitized warning (device_id only).
    """
    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    settings = get_settings()

    body_token = body.refresh_token if body is not None else None
    has_cookie = request.cookies.get(COOKIE_NAME) is not None

    # Determine mode and validate no mixed usage
    if has_cookie and body_token is not None:
        raise HTTPException(
            status_code=400,
            detail="refresh token present in both cookie and body",
        )

    if not has_cookie and body_token is None:
        raise HTTPException(
            status_code=400,
            detail="refresh token required in body or cookie",
        )

    is_web = has_cookie and body_token is None

    # CSRF check for web (cookie) mode
    if is_web:
        csrf_result = check_csrf_origin(
            request_origin=request.headers.get("Origin"),
            sec_fetch_site=request.headers.get("Sec-Fetch-Site"),
            referer=request.headers.get("Referer"),
            allowed_origins=[o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()],
            public_origin=settings.daemon_public_origin,
            has_cookie=True,
        )
        if not csrf_result.allowed:
            raise HTTPException(
                status_code=403,
                detail=f"CSRF/origin check failed: {csrf_result.reason}",
            )

    # Resolve token value based on mode
    presented_token = body_token if body_token is not None else request.cookies.get(COOKIE_NAME)
    assert presented_token is not None, "presented_token must be set at this point"
    token_hash = hash_token(presented_token)

    async with app_state.db_pool.acquire() as conn:
        async with conn.transaction():
            # Atomic consume: UPDATE ... WHERE valid (not consumed, not expired, not revoked, device active)
            consumed_row = await conn.fetchrow(
                """
                UPDATE sessions
                SET refresh_consumed_at = NOW()
                WHERE refresh_token_hash = $1
                  AND refresh_consumed_at IS NULL
                  AND refresh_expires_at > NOW()
                  AND revoked_at IS NULL
                  AND EXISTS (SELECT 1 FROM devices WHERE id = sessions.device_id AND revoked_at IS NULL)
                RETURNING id, user_id, device_id, client_kind, refresh_expires_at
                """,
                token_hash,
            )

            if consumed_row is None:
                # Zero rows: check second lookup to distinguish consumed-reuse from other invalid
                existing_row = await conn.fetchrow(
                    """
                    SELECT id, user_id, device_id, client_kind,
                           refresh_expires_at, refresh_consumed_at
                    FROM sessions
                    WHERE refresh_token_hash = $1
                    """,
                    token_hash,
                )

                if existing_row is None:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid or expired refresh token",
                    )

                if existing_row["refresh_consumed_at"] is not None:
                    # Consumed reuse: revoke device + all sessions, clear cookie, log sanitized warning
                    device_id_to_revoke: uuid.UUID = existing_row["device_id"]
                    logger.warning(
                        "Refresh token reuse detected for device_id=%s",
                        device_id_to_revoke,
                    )
                    await conn.execute(
                        """
                        UPDATE devices SET revoked_at = NOW()
                        WHERE id = $1 AND revoked_at IS NULL
                        """,
                        device_id_to_revoke,
                    )
                    await conn.execute(
                        """
                        UPDATE sessions SET revoked_at = NOW()
                        WHERE device_id = $1 AND revoked_at IS NULL
                        """,
                        device_id_to_revoke,
                    )
                    if is_web:
                        cookie_config = make_refresh_cookie_config(
                            cookie_secure=settings.daemon_cookie_secure,
                            environment=settings.daemon_environment,
                        )
                        cookie_headers = clear_refresh_cookie(cookie_config)
                        for header_name, header_value in cookie_headers.items():
                            response.headers[header_name] = header_value
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid or expired refresh token",
                    )

                # Unconsumed but invalid (expired/revoked/device-revoked) — 401 without revocation
                raise HTTPException(
                    status_code=401,
                    detail="Invalid or expired refresh token",
                )

            session_id: uuid.UUID = consumed_row["id"]
            user_id: uuid.UUID = consumed_row["user_id"]
            device_id: uuid.UUID = consumed_row["device_id"]
            client_kind: str = consumed_row["client_kind"]

            # Generate replacement tokens
            now = datetime.now(timezone.utc)
            access_expires = now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)
            refresh_expires = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
            access_token = generate_token()
            refresh_token = generate_token()

            await conn.execute(
                """
                INSERT INTO sessions (
                    user_id, device_id, client_kind,
                    access_token_hash, access_expires_at,
                    refresh_token_hash, refresh_expires_at,
                    created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                user_id,
                device_id,
                client_kind,
                hash_token(access_token),
                access_expires,
                hash_token(refresh_token),
                refresh_expires,
                now,
            )

    # Set replacement cookie for web mode
    if is_web:
        cookie_config = make_refresh_cookie_config(
            cookie_secure=settings.daemon_cookie_secure,
            environment=settings.daemon_environment,
        )
        refresh_max_age = int(timedelta(days=REFRESH_TOKEN_TTL_DAYS).total_seconds())
        cookie_headers = build_refresh_cookie(
            value=refresh_token,
            config=cookie_config,
            max_age=refresh_max_age,
        )
        for header_name, header_value in cookie_headers.items():
            response.headers[header_name] = header_value
        return RefreshResponse(access_token=access_token)

    return RefreshResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


# ---------------------------------------------------------------------------
# Device management
# ---------------------------------------------------------------------------


class DeviceResponse(BaseModel):
    id: str
    display_name: str
    platform: str | None
    created_at: str
    last_seen_at: str | None
    revoked_at: str | None
    current: bool
    revoked: bool


class DeviceListResponse(BaseModel):
    devices: list[DeviceResponse]


@router.get("/devices", response_model=DeviceListResponse, response_model_exclude_none=True)
async def list_devices_endpoint(
    request: Request,
    auth: AuthenticatedDevice = Depends(require_device_auth),
    include_revoked: bool = False,
) -> DeviceListResponse:
    """List all devices for the authenticated user.

    Each device shows whether it is the 'current' device (the one making
    the request). Revoked devices are excluded by default; set
    include_revoked=true to include them.
    """
    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with app_state.db_pool.acquire() as conn:
        if include_revoked:
            rows = await conn.fetch(
                """
                SELECT id, display_name, platform, created_at, last_seen_at, revoked_at
                FROM devices
                WHERE user_id = $1
                ORDER BY created_at DESC
                """,
                auth.user_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, display_name, platform, created_at, last_seen_at, revoked_at
                FROM devices
                WHERE user_id = $1 AND revoked_at IS NULL
                ORDER BY created_at DESC
                """,
                auth.user_id,
            )

    devices = [
        DeviceResponse(
            id=str(row["id"]),
            display_name=row["display_name"],
            platform=row["platform"],
            created_at=row["created_at"].isoformat(),
            last_seen_at=row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
            revoked_at=row["revoked_at"].isoformat() if row["revoked_at"] else None,
            current=(row["id"] == auth.device_id),
            revoked=(row["revoked_at"] is not None),
        )
        for row in rows
    ]

    return DeviceListResponse(devices=devices)


@router.delete(
    "/devices/{device_id}",
    status_code=204,
    responses={204: {"description": "Device revoked or already revoked"}, 404: {"description": "Device not found"}},
)
async def revoke_device_endpoint(
    request: Request,
    response: Response,
    device_id: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> None:
    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        target_device_uuid = uuid.UUID(device_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Device not found")

    settings = get_settings()
    has_refresh_cookie = request.cookies.get(COOKIE_NAME) is not None

    async with app_state.db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, user_id, revoked_at
                FROM devices
                WHERE id = $1 AND user_id = $2
                FOR UPDATE
                """,
                target_device_uuid,
                auth.user_id,
            )

            if row is None:
                raise HTTPException(status_code=404, detail="Device not found")

            is_current_device = row["id"] == auth.device_id
            already_revoked = row["revoked_at"] is not None

            if not already_revoked:
                await conn.execute(
                    """
                    UPDATE devices SET revoked_at = NOW() WHERE id = $1
                    """,
                    target_device_uuid,
                )

                await conn.execute(
                    """
                    UPDATE sessions SET revoked_at = NOW()
                    WHERE device_id = $1 AND revoked_at IS NULL
                    """,
                    target_device_uuid,
                )

    if is_current_device and has_refresh_cookie:
        cookie_config = make_refresh_cookie_config(
            cookie_secure=settings.daemon_cookie_secure,
            environment=settings.daemon_environment,
        )
        cookie_headers = clear_refresh_cookie(cookie_config)
        for header_name, header_value in cookie_headers.items():
            response.headers[header_name] = header_value

    return None
