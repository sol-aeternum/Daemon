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

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import cast

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.auth_cookies import (
    CookiePolicyError,
    RefreshCookieConfig,
    build_temporary_refresh_cookie,
    build_refresh_cookie,
    clear_refresh_cookie,
    get_refresh_cookie_name,
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
from orchestrator.services.identity import (
    AccountService,
    EmailChallengeConsumeRequest,
    EmailChallengeInvalid,
    EmailChallengeIssueRequest,
    EmailChallengeLocked,
    EmailChallengeRow,
    EmailChallengeService,
    EmailChallengeUnavailable,
    InviteOnlyRejection,
    IssueSessionRequest,
    MailMessage,
    MailSenderConfigError,
    RateLimitPolicy,
    ScopeKind,
    SignupDisabled,
    SupportsEmailChallengeQueries,
    SupportsIdentityQueries,
    SupportsSessionIssuanceQueries,
    client_ip_for_key,
    enforce_rate_limit,
    get_mail_sender,
    get_rate_limiter,
    hash_ip_for_storage,
    hash_user_agent_for_storage,
    issue_device_session,
    normalize_code,
    normalize_email,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/v1/auth", tags=["auth"])

SINGLETON_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ACCESS_TOKEN_TTL_MINUTES = 30
REFRESH_TOKEN_TTL_DAYS = 90
ENROLLMENT_TTL_MINUTES = 10
ENROLLMENT_WRONG_ATTEMPTS_INITIAL = 3
EMAIL_START_TIMING_FLOOR_SECONDS = 0.25

# Rate-limit policies for the existing auth endpoints (TODO 7).
# Values are research recommendations and match the per-IP scopes
# surfaced in `_scratch_identity_audit.md` for these endpoints. They
# are hard-coded here per the TODO 7 "do not add env vars" guardrail;
# TODO 8/9 may promote them to `daemon_rate_limit_*` config fields.
RATE_LIMIT_SETUP_PER_IP_PER_HOUR: RateLimitPolicy = RateLimitPolicy(limit=5, window_seconds=3600)
RATE_LIMIT_ENROLL_COMPLETE_PER_IP_PER_HOUR: RateLimitPolicy = RateLimitPolicy(
    limit=20, window_seconds=3600
)
RATE_LIMIT_REFRESH_PER_IP_PER_HOUR: RateLimitPolicy = RateLimitPolicy(
    limit=120, window_seconds=3600
)


class EmailStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str


class EmailStartResponse(BaseModel):
    accepted: bool = True
    challenge_id: str
    expires_at: int


class EmailCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    code: str
    client_kind: str
    device_persistence: str = "private"


class EmailCompleteResponse(BaseModel):
    access_token: str
    expires_at: int
    refresh_token: str | None = None
    token_type: str = "Bearer"


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


def _refresh_cookie_name(settings) -> str:
    return get_refresh_cookie_name(
        cookie_secure=settings.daemon_cookie_secure,
        environment=settings.daemon_environment,
    )


def _refresh_cookie_value(request: Request, settings) -> str | None:
    return request.cookies.get(_refresh_cookie_name(settings))


async def _sleep_for_start_timing_floor(started_at: float) -> None:
    remaining = EMAIL_START_TIMING_FLOOR_SECONDS - (time.monotonic() - started_at)
    if remaining > 0:
        await asyncio.sleep(remaining)


def _email_start_rate_limit_policies(
    request: Request,
    *,
    normalized_email: str,
    settings,
) -> list[tuple[ScopeKind, str, RateLimitPolicy]]:
    client_ip = client_ip_for_key(request)
    return [
        (
            "ip",
            client_ip,
            RateLimitPolicy(
                limit=settings.daemon_rate_limit_email_start_per_ip_per_hour,
                window_seconds=3600,
            ),
        ),
        (
            "ip",
            client_ip,
            RateLimitPolicy(
                limit=settings.daemon_rate_limit_email_start_per_ip_per_day,
                window_seconds=86400,
            ),
        ),
        (
            "email",
            normalized_email,
            RateLimitPolicy(
                limit=settings.daemon_rate_limit_email_start_per_email_per_hour,
                window_seconds=3600,
            ),
        ),
        (
            "email",
            normalized_email,
            RateLimitPolicy(
                limit=settings.daemon_rate_limit_email_start_per_email_per_day,
                window_seconds=86400,
            ),
        ),
    ]


def _email_complete_rate_limit_policies(
    request: Request,
    *,
    scope_value: str,
    settings,
) -> list[tuple[ScopeKind, str, RateLimitPolicy]]:
    policy = RateLimitPolicy(
        limit=settings.daemon_rate_limit_email_complete_per_ip_per_hour,
        window_seconds=3600,
    )
    return [
        ("ip", client_ip_for_key(request), policy),
        ("email", scope_value, policy),
    ]


def _build_email_code_message(
    *,
    normalized_email: str,
    plaintext_code: str,
    ttl_seconds: int,
) -> MailMessage:
    minutes = max(1, ttl_seconds // 60)
    return MailMessage(
        to_address=normalized_email,
        subject="Your Daemon sign-in code",
        body_text=(
            f"Your Daemon sign-in code is {plaintext_code}.\n\nIt expires in {minutes} minutes."
        ),
    )


def _email_complete_failure() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="code_invalid_or_expired",
    )


async def _load_email_challenge_lookup(conn, challenge_id: uuid.UUID):
    return await conn.fetchrow(
        """
        SELECT id, normalized_email
        FROM email_challenges
        WHERE id = $1
        """,
        challenge_id,
    )


async def _load_active_invite_verifier_hash(conn, normalized_email: str) -> str | None:
    value = await conn.fetchval(
        """
        SELECT token_verifier_hash
        FROM signup_invites
        WHERE normalized_email = $1
          AND status = 'active'
          AND expires_at > NOW()
        """,
        normalized_email,
    )
    return None if value is None else str(value)


def _device_name_for_client_kind(client_kind: str) -> str:
    return "Web Sign-In Device" if client_kind == "web" else "Native Sign-In Device"


@router.post("/email/start", response_model=EmailStartResponse, status_code=202)
async def email_start_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    body: EmailStartRequest,
) -> EmailStartResponse:
    started_at = time.monotonic()
    settings = get_settings()

    try:
        normalized_email = normalize_email(body.email)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_email")

    limiter = get_rate_limiter(request)
    await enforce_rate_limit(
        request=request,
        limiter=limiter,
        endpoint="auth:email:start",
        policies=_email_start_rate_limit_policies(
            request,
            normalized_email=normalized_email,
            settings=settings,
        ),
    )

    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        sender = get_mail_sender(settings)
    except MailSenderConfigError as exc:
        raise HTTPException(status_code=503, detail="email_unavailable") from exc

    pepper = validate_and_get_pepper(settings)
    ip_hash = hash_ip_for_storage(client_ip_for_key(request), pepper)
    user_agent = request.headers.get("User-Agent")
    user_agent_hash = (
        hash_user_agent_for_storage(user_agent, pepper)
        if user_agent and user_agent.strip()
        else None
    )

    async with app_state.db_pool.acquire() as conn:
        service = EmailChallengeService(cast(SupportsEmailChallengeQueries, conn), settings)
        try:
            challenge_row, plaintext_code = await service.create_challenge_for_delivery(
                EmailChallengeIssueRequest(
                    normalized_email=normalized_email,
                    ip_hash=ip_hash,
                    user_agent_hash=user_agent_hash,
                    ttl_seconds=settings.daemon_email_challenge_ttl_seconds,
                    max_attempts=settings.daemon_email_challenge_max_attempts,
                )
            )
        except EmailChallengeUnavailable as exc:
            raise HTTPException(status_code=503, detail="email_unavailable") from exc

    background_tasks.add_task(
        sender.send,
        _build_email_code_message(
            normalized_email=normalized_email,
            plaintext_code=plaintext_code,
            ttl_seconds=settings.daemon_email_challenge_ttl_seconds,
        ),
    )

    await _sleep_for_start_timing_floor(started_at)
    return EmailStartResponse(
        challenge_id=str(challenge_row.id),
        expires_at=int(challenge_row.expires_at.timestamp()),
    )


@router.post(
    "/email/complete",
    response_model=EmailCompleteResponse,
    response_model_exclude_none=True,
)
async def email_complete_endpoint(
    request: Request,
    response: Response,
    body: EmailCompleteRequest,
) -> EmailCompleteResponse:
    settings = get_settings()
    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if body.client_kind not in ("web", "native"):
        raise HTTPException(status_code=400, detail="client_kind must be 'web' or 'native'")
    if body.device_persistence not in ("private", "temporary"):
        raise HTTPException(
            status_code=400,
            detail="device_persistence must be 'private' or 'temporary'",
        )

    has_cookie = _refresh_cookie_value(request, settings) is not None
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
            allowed_origins=[
                o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()
            ],
            public_origin=settings.daemon_public_origin,
            has_cookie=has_cookie,
        )
        if not csrf_result.allowed:
            raise HTTPException(
                status_code=403,
                detail=f"CSRF/origin check failed: {csrf_result.reason}",
            )

    try:
        challenge_uuid = uuid.UUID(body.challenge_id)
    except (ValueError, AttributeError):
        raise _email_complete_failure()

    try:
        normalized_code = normalize_code(body.code)
    except ValueError:
        raise _email_complete_failure()

    limiter = get_rate_limiter(request)

    async with app_state.db_pool.acquire() as conn:
        challenge_lookup = await _load_email_challenge_lookup(conn, challenge_uuid)
        challenge_scope = body.challenge_id
        if challenge_lookup is not None and challenge_lookup["normalized_email"]:
            challenge_scope = str(challenge_lookup["normalized_email"])

        await enforce_rate_limit(
            request=request,
            limiter=limiter,
            endpoint="auth:email:complete",
            policies=_email_complete_rate_limit_policies(
                request,
                scope_value=challenge_scope,
                settings=settings,
            ),
        )

        challenge_service = EmailChallengeService(
            cast(SupportsEmailChallengeQueries, conn), settings
        )
        try:
            consumed_challenge: EmailChallengeRow = await challenge_service.consume_challenge(
                EmailChallengeConsumeRequest(
                    challenge_id=challenge_uuid,
                    plaintext_code=normalized_code,
                )
            )
        except (EmailChallengeInvalid, EmailChallengeLocked):
            raise _email_complete_failure()
        except EmailChallengeUnavailable as exc:
            raise HTTPException(status_code=503, detail="service_unavailable") from exc

        invite_token_verifier_hash: str | None = None
        if settings.daemon_signup_mode == "invite_only":
            invite_token_verifier_hash = await _load_active_invite_verifier_hash(
                conn,
                consumed_challenge.normalized_email,
            )

        account_service = AccountService(cast(SupportsIdentityQueries, conn))
        try:
            claim_result = await account_service.claim_email_identity(
                normalized_email=consumed_challenge.normalized_email,
                email_verified_at=consumed_challenge.consumed_at or datetime.now(timezone.utc),
                signup_mode=settings.daemon_signup_mode,
                invite_token_verifier_hash=invite_token_verifier_hash,
            )
        except (InviteOnlyRejection, SignupDisabled):
            raise _email_complete_failure()

        async with conn.transaction():
            issued_session = await issue_device_session(
                cast(SupportsSessionIssuanceQueries, conn),
                IssueSessionRequest(
                    user_id=claim_result.user.id,
                    tenant_id=claim_result.tenant.id,
                    client_kind=body.client_kind,
                    device_persistence=body.device_persistence,
                    device_name=_device_name_for_client_kind(body.client_kind),
                    platform=body.client_kind,
                    private_refresh_ttl_days=settings.daemon_private_refresh_ttl_days,
                    temporary_refresh_ttl_seconds=settings.daemon_temporary_refresh_ttl_seconds,
                ),
            )

    if body.client_kind == "web":
        try:
            cookie_config = make_refresh_cookie_config(
                cookie_secure=settings.daemon_cookie_secure,
                environment=settings.daemon_environment,
            )
        except CookiePolicyError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        if body.device_persistence == "temporary":
            cookie_headers = build_temporary_refresh_cookie(
                value=issued_session.refresh_token,
                config=cookie_config,
            )
        else:
            cookie_headers = build_refresh_cookie(
                value=issued_session.refresh_token,
                config=cookie_config,
                max_age=issued_session.refresh_max_age_seconds,
            )
        for header_name, header_value in cookie_headers.items():
            response.headers[header_name] = header_value
        return EmailCompleteResponse(
            access_token=issued_session.access_token,
            expires_at=int(issued_session.access_expires_at.timestamp()),
        )

    return EmailCompleteResponse(
        access_token=issued_session.access_token,
        expires_at=int(issued_session.access_expires_at.timestamp()),
        refresh_token=issued_session.refresh_token,
    )


@router.post("/setup", response_model=SetupResponse)
async def setup_endpoint(
    request: Request,
    response: Response,
    body: SetupRequest,
) -> SetupResponse:
    limiter = get_rate_limiter(request)
    await enforce_rate_limit(
        request=request,
        limiter=limiter,
        endpoint="auth:setup",
        policies=[
            (
                "ip",
                client_ip_for_key(request),
                RATE_LIMIT_SETUP_PER_IP_PER_HOUR,
            ),
        ],
    )

    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    settings = get_settings()
    csrf_result = check_csrf_origin(
        request_origin=request.headers.get("Origin"),
        sec_fetch_site=request.headers.get("Sec-Fetch-Site"),
        referer=request.headers.get("Referer"),
        allowed_origins=[
            o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()
        ],
        public_origin=settings.daemon_public_origin,
        has_cookie=_refresh_cookie_value(request, settings) is not None,
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

    try:
        cookie_config = make_refresh_cookie_config(
            cookie_secure=settings.daemon_cookie_secure,
            environment=settings.daemon_environment,
        )
    except CookiePolicyError as e:
        raise HTTPException(status_code=500, detail=str(e))

    async with app_state.db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('daemon:first_boot_setup'))")

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
            access_token, refresh_token = await _create_first_device_and_session(conn, user_id)

    app_state.setup_token_hash = None

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


@router.post(
    "/enroll/complete",
    response_model=EnrollCompleteResponse,
    response_model_exclude_none=True,
)
async def enroll_complete_endpoint(
    request: Request,
    response: Response,
    body: EnrollCompleteRequest,
) -> EnrollCompleteResponse:
    limiter = get_rate_limiter(request)
    await enforce_rate_limit(
        request=request,
        limiter=limiter,
        endpoint="auth:enroll:complete",
        policies=[
            (
                "ip",
                client_ip_for_key(request),
                RATE_LIMIT_ENROLL_COMPLETE_PER_IP_PER_HOUR,
            ),
        ],
    )

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

    has_cookie = _refresh_cookie_value(request, settings) is not None

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
            allowed_origins=[
                o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()
            ],
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

    cookie_config: RefreshCookieConfig | None = None
    if body.client_kind == "web":
        try:
            cookie_config = make_refresh_cookie_config(
                cookie_secure=settings.daemon_cookie_secure,
                environment=settings.daemon_environment,
            )
        except CookiePolicyError as e:
            raise HTTPException(status_code=500, detail=str(e))

    async with app_state.db_pool.acquire() as conn:
        exc_to_raise: HTTPException | None = None
        access_token = ""
        refresh_token = ""
        async with conn.transaction():
            pending_row = await conn.fetchrow(
                """
                SELECT id, user_id, created_by_device_id, code_verifier_hash,
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

            creating_device_row = await conn.fetchrow(
                """
                SELECT revoked_at FROM devices WHERE id = $1
                """,
                pending_row["created_by_device_id"],
            )
            if creating_device_row is None or creating_device_row["revoked_at"] is not None:
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
                    exc_to_raise = HTTPException(
                        status_code=410,
                        detail="Enrollment session expired",
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE pending_enrollments
                        SET wrong_attempts_remaining = $1
                        WHERE id = $2
                        """,
                        wrong_attempts,
                        pending_row["id"],
                    )
                    exc_to_raise = HTTPException(
                        status_code=401,
                        detail="Invalid enrollment code",
                    )

            if exc_to_raise is None:
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

    if exc_to_raise:
        raise exc_to_raise

    if body.client_kind == "web":
        assert cookie_config is not None
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
    limiter = get_rate_limiter(request)
    await enforce_rate_limit(
        request=request,
        limiter=limiter,
        endpoint="auth:refresh",
        policies=[
            (
                "ip",
                client_ip_for_key(request),
                RATE_LIMIT_REFRESH_PER_IP_PER_HOUR,
            ),
        ],
    )

    app_state = get_app_state(request)
    if app_state.db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    settings = get_settings()

    body_token = body.refresh_token if body is not None else None
    has_cookie = _refresh_cookie_value(request, settings) is not None

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
            allowed_origins=[
                o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()
            ],
            public_origin=settings.daemon_public_origin,
            has_cookie=True,
        )
        if not csrf_result.allowed:
            raise HTTPException(
                status_code=403,
                detail=f"CSRF/origin check failed: {csrf_result.reason}",
            )

    # Resolve token value based on mode
    presented_token = (
        body_token if body_token is not None else _refresh_cookie_value(request, settings)
    )
    assert presented_token is not None, "presented_token must be set at this point"
    token_hash = hash_token(presented_token)

    exc_to_raise: HTTPException | None = None
    access_token = ""
    refresh_token = ""

    async with app_state.db_pool.acquire() as conn:
        async with conn.transaction():
            # Pre-check: query client_kind BEFORE consuming, so mismatch returns 400
            # without burning an otherwise valid refresh token.  Only valid sessions
            # on active devices are candidates; revoked-device tokens fall through to
            # the invalid-token path.
            pre_row = await conn.fetchrow(
                """
                SELECT client_kind
                FROM sessions
                WHERE refresh_token_hash = $1
                  AND refresh_consumed_at IS NULL
                  AND refresh_expires_at > NOW()
                  AND revoked_at IS NULL
                  AND EXISTS (SELECT 1 FROM devices WHERE id = sessions.device_id AND revoked_at IS NULL)
                """,
                token_hash,
            )
            if pre_row is not None:
                stored_kind = pre_row["client_kind"]
                if is_web and stored_kind == "native":
                    raise HTTPException(
                        status_code=400,
                        detail="Refresh token transport mismatch",
                    )
                if not is_web and stored_kind == "web":
                    raise HTTPException(
                        status_code=400,
                        detail="Refresh token transport mismatch",
                    )
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
                        exc_to_raise = HTTPException(
                            status_code=401,
                            detail="Invalid or expired refresh token",
                            headers=cookie_headers,
                        )
                    else:
                        exc_to_raise = HTTPException(
                            status_code=401,
                            detail="Invalid or expired refresh token",
                        )
                elif existing_row is None:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid or expired refresh token",
                    )
                else:
                    exc_to_raise = HTTPException(
                        status_code=401,
                        detail="Invalid or expired refresh token",
                    )

            if exc_to_raise is None and consumed_row is not None:
                client_kind: str = consumed_row["client_kind"]

                if is_web and client_kind == "native":
                    exc_to_raise = HTTPException(
                        status_code=400,
                        detail="Refresh token transport mismatch",
                    )
                elif not is_web and client_kind == "web":
                    exc_to_raise = HTTPException(
                        status_code=400,
                        detail="Refresh token transport mismatch",
                    )

            if exc_to_raise is None and consumed_row is not None:
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
                    consumed_row["user_id"],
                    consumed_row["device_id"],
                    consumed_row["client_kind"],
                    hash_token(access_token),
                    access_expires,
                    hash_token(refresh_token),
                    refresh_expires,
                    now,
                )

    if exc_to_raise:
        raise exc_to_raise

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
# Identity-aware session issuance helper (TODO 9)
# ---------------------------------------------------------------------------
# The TODO 9 deliverable is a reusable backend-only service helper
# (`orchestrator.services.identity.issue_device_session`) that
# TODO 11 (email completion) and TODO 13 (Google completion) will
# call AFTER a successful `AccountService.claim_*_identity` call.
# No HTTP route is added in this TODO because identity session
# issuance MUST be gated on a verified identity proof (email code
# consumption or Google ID-token verification); a route that
# accepts caller-supplied user_id/tenant_id and mints Daemon
# tokens would bypass the proof-of-identity invariant and is
# explicitly out of scope for TODO 9. The thin test boundary is
# helper-level only; route-level coverage is deferred to TODO 11
# and TODO 13 which will own the proof contracts.


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
            last_seen_at=(row["last_seen_at"].isoformat() if row["last_seen_at"] else None),
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
    responses={
        204: {"description": "Device revoked or already revoked"},
        404: {"description": "Device not found"},
    },
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
    has_refresh_cookie = _refresh_cookie_value(request, settings) is not None

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
                active_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM devices
                    WHERE user_id = $1 AND revoked_at IS NULL
                    """,
                    auth.user_id,
                )
                if active_count <= 1:
                    raise HTTPException(
                        status_code=409,
                        detail="cannot_revoke_last_active_device",
                    )
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
