from __future__ import annotations


import asyncio
import json
import logging

import sys

import time
import uuid

import asyncpg

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from contextlib import asynccontextmanager
from typing import Any

from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import Receive, Scope, Send
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from orchestrator.artifacts import (
    resolve_owned_artifact,
)
from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.auth_pepper import (
    PepperValidationError,
    initialize_development_pepper,
    validate_pepper_config,
)
from orchestrator.auth_runtime_state import (
    clear_setup_token_hash,
    create_setup_token_if_absent,
    lock_auth_runtime_state,
    replace_setup_token,
)
from orchestrator.services.identity import (
    RateLimitPolicy,
    ScopeKind,
    client_ip_for_key,
    enforce_rate_limit,
    get_rate_limiter,
    record_chat_rate_limit_rejection,
    record_chat_rate_limit_request,
)
from orchestrator.council.sse import stream_council, stream_council_interview_response
from orchestrator.config import (
    HostSecurityConfigError,
    HostedIdentityConfigError,
    InternalProxyConfigError,
    Settings,
    get_settings,
)
from orchestrator.compute_runtime import ComputeUnavailable, account_compute, choose_route
from orchestrator.entitlements.errors import PolicyError
from orchestrator.entitlements.policy import load_inference_policy
from orchestrator.daemon import (
    effective_provider_and_model,
    new_conversation_id,
    new_request_id,
    now_rfc3339,
    sse,
    stream_sse_chat,
    stream_with_keepalives,
)
from orchestrator.db import (
    AppState,
    check_db_health,
    close_app_state,
    get_app_state,
    init_app_state,
)
from orchestrator.database_url import (
    UnsafeDatabaseCredentialError,
    apply_resolved_database_url,
    validate_database_credentials,
)
from orchestrator.memory.encryption import ContentEncryption, EncryptionInitError
from orchestrator.timezones import extract_timezone_name
from orchestrator.session_cleanup import (
    cleanup_stale_sessions,
    start_session_cleanup_task,
)
from orchestrator.setup_token_delivery import (
    delete_setup_token_file,
    setup_token_file_exists,
    write_setup_token_file,
)
from orchestrator.routes import (
    conversations,
    entitlements,
    images,
    memories,
    skills,
    system,
    users,
    video_credits,
)
from orchestrator.routes.auth_config import router as auth_config_router
from orchestrator.routes.auth_setup import router as auth_setup_router
from orchestrator.models_cache import fetch_openrouter_models
from orchestrator.model_router import select_model_tier
from orchestrator.skills_store import build_skill_index
from orchestrator.skills_projection import SkillProjectionStore
from orchestrator.skills_sync import SkillSyncService
from orchestrator.skills_upgrade import load_repo_contents, run_upgrade_sync


from orchestrator.models import (
    ChatRequest,
    TtsRequest,
    OpenAIChatRequest,
    OpenAIChatResponse,
    OpenAIChatStreamChunk,
    OpenAIChoice,
    OpenAIDeltaMessage,
    OpenAIMessage,
    OpenAIModelInfo,
    OpenAIModelList,
    OpenAIUsage,
)
from orchestrator.prompts import DAEMON_SYSTEM_PROMPT
from orchestrator.request_body_limit import (
    REQUEST_BODY_TOO_LARGE_RESPONSES,
    RequestBodyLimitMiddleware,
)
from orchestrator.router import route_message
from orchestrator.security_headers import (
    SecurityHeadersMiddleware,
    _OuterSecurityHeadersMiddleware,
)
from orchestrator.request_id import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    _OuterCORSMiddleware,
    _OuterRequestIdMiddleware,
    get_client_request_id,
    get_request_id,
)

logger = logging.getLogger(__name__)

CORS_ALLOW_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOW_HEADERS = (
    "Authorization",
    "Content-Type",
    "X-CSRF-Token",
    "X-Request-ID",
)

# Headers the browser is allowed to read on a CORS response. Exposing
# ``X-Request-ID`` lets browser code correlate its errors with server-side
# logs; the value is server-generated (round-1 Codex finding on PR #218)
# so an attacker cannot pre-stage collisions.
CORS_EXPOSE_HEADERS = ("X-Request-ID",)


def warn_on_unsafe_cors_wildcards(
    *,
    allow_credentials: bool,
    allow_methods: Sequence[str],
    allow_headers: Sequence[str],
) -> None:
    if not allow_credentials:
        return
    if "*" in allow_methods or "*" in allow_headers:
        logger.warning(
            "Unsafe CORS configuration: wildcard methods or headers with credentials enabled"
        )


class UnsafeProductionServerConfigError(RuntimeError):
    """Raised when the process is launched with dev-only server flags in production."""


def _validate_production_server_args(settings: Settings, argv: Sequence[str] | None = None) -> None:
    if settings.daemon_environment.lower().strip() != "production":
        return
    args = sys.argv if argv is None else argv
    if any(arg == "--reload" or arg.startswith("--reload=") for arg in args):
        raise UnsafeProductionServerConfigError(
            "uvicorn --reload is not allowed when DAEMON_ENVIRONMENT=production"
        )


def _validate_startup_config(settings: Settings, argv: Sequence[str] | None = None) -> None:
    """Run all fail-closed startup-time config validations.

    Centralized so the FastAPI lifespan hook stays compact and the
    validation chain is testable in isolation (see
    tests/test_hosted_identity_config.py). Order is intentional: pepper
    first (authentication substrate), then hosted identity (deployment
    posture). Either failure aborts startup before any AppState work.
    """
    validate_database_credentials(settings)
    _validate_production_server_args(settings, argv)
    validate_pepper_config(settings)
    settings.validate_deployment_mode()
    settings.validate_hosted_identity_config()
    settings.validate_internal_proxy_config()
    if settings.daemon_encryption_key is not None:
        ContentEncryption(settings.daemon_encryption_key)
    settings.validate_host_security_config()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    apply_resolved_database_url(settings)

    try:
        _validate_startup_config(settings)
    except UnsafeDatabaseCredentialError as exc:
        logger.critical("Unsafe database credential configuration: %s", exc)
        raise
    except UnsafeProductionServerConfigError as exc:
        logger.critical("Unsafe production server configuration: %s", exc)
        raise
    except PepperValidationError as exc:
        logger.critical("Production pepper validation failed: %s", exc)
        raise
    except HostedIdentityConfigError as exc:
        logger.critical("Hosted identity config validation failed: %s", exc)
        raise
    except InternalProxyConfigError as exc:
        logger.critical("Internal proxy config validation failed: %s", exc)
        raise
    except EncryptionInitError as exc:
        logger.critical("Encryption config validation failed: %s", exc)
        raise
    except HostSecurityConfigError as exc:
        logger.critical("Host security config validation failed: %s", exc)
        raise

    state = await init_app_state(settings)
    app.state.app_state = state
    app.state.settings = settings
    logger.info("AppState initialised")

    cleanup_task = None
    cleanup_shutdown_event = None

    if state.db_pool is not None:
        await initialize_development_pepper(settings, state.db_pool)
        if state.memory_store is not None:
            try:
                backfilled = await state.memory_store.backfill_memory_content_hashes()
                if backfilled:
                    logger.info("Backfilled content_hash for %s current memories", backfilled)
            except Exception:
                logger.warning("Failed to backfill memory content hashes", exc_info=True)
        asyncio.create_task(_backfill_skill_projections(state.db_pool))
        asyncio.create_task(_sync_repo_skills(state.db_pool))
        await _check_first_boot_setup(state)

        try:
            deleted = await cleanup_stale_sessions(
                state.db_pool,
                settings.daemon_session_cleanup_grace_days,
                settings.daemon_session_cleanup_max_delete_fraction,
            )
            if deleted > 0:
                logger.info("Startup session cleanup deleted %d stale sessions", deleted)
        except Exception:
            logger.warning("Startup session cleanup failed", exc_info=True)

        cleanup_task, cleanup_shutdown_event = await start_session_cleanup_task(
            state.db_pool,
            settings.daemon_session_cleanup_grace_days,
            settings.daemon_session_cleanup_interval_seconds,
            settings.daemon_session_cleanup_max_delete_fraction,
        )

    yield

    if cleanup_shutdown_event is not None:
        cleanup_shutdown_event.set()
    if cleanup_task is not None:
        await asyncio.shield(cleanup_task)
    await close_app_state(state)
    logger.info("AppState shut down")


async def _backfill_skill_projections(db_pool: asyncpg.Pool) -> None:
    try:
        store = SkillProjectionStore(db_pool)
        service = SkillSyncService(store)
        results = await service.backfill_existing_skills()
        successful = sum(1 for r in results if r.success)
        logger.info(
            "Skill projection backfill complete: %d/%d skills",
            successful,
            len(results),
        )
    except Exception:
        logger.warning("Skill projection backfill failed", exc_info=True)


async def _sync_repo_skills(db_pool: asyncpg.Pool) -> None:
    try:
        repo_contents = load_repo_contents()
        if not repo_contents:
            logger.debug("No repo skills found, skipping sync")
            return
        result = await run_upgrade_sync(db_pool, repo_contents)
        logger.info(
            "Repo skill sync complete: %d unchanged, %d silent, %d pending, %d insert, %d deprecated, %d errors",
            result.total_unchanged,
            result.total_silent_updates,
            result.total_pending_updates,
            result.total_inserts,
            result.total_deprecated,
            result.total_errors,
        )
    except Exception:
        logger.warning("Repo skill sync failed", exc_info=True)


def _publish_setup_token(settings: Settings, token: str, *, recovery: bool = False) -> None:
    path = write_setup_token_file(settings.daemon_setup_token_file, token)
    if recovery:
        logger.info(
            ">>> Daemon recovery: all sessions expired. Open http://<host>:<port>/setup "
            "and enter the setup token from %s",
            path,
        )
        return
    logger.info(
        ">>> Daemon setup required. Open http://<host>:<port>/setup "
        "and enter the setup token from %s",
        path,
    )


async def _check_first_boot_setup(state: AppState) -> None:
    if state.db_pool is None:
        return
    settings = state.settings
    try:
        async with state.db_pool.acquire() as conn:
            async with conn.transaction():
                await lock_auth_runtime_state(conn)
                active_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM devices WHERE revoked_at IS NULL"
                )
                if active_count == 0:
                    plaintext = await create_setup_token_if_absent(conn)
                    if plaintext is None and not setup_token_file_exists(
                        settings.daemon_setup_token_file
                    ):
                        plaintext = await replace_setup_token(conn)
                    if plaintext is not None:
                        _publish_setup_token(settings, plaintext)
                    return

                has_valid_session = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM sessions s
                        JOIN devices d ON d.id = s.device_id
                        WHERE d.revoked_at IS NULL
                          AND s.refresh_consumed_at IS NULL
                          AND s.refresh_expires_at > NOW()
                          AND s.revoked_at IS NULL
                    )
                    """
                )
                if has_valid_session:
                    await clear_setup_token_hash(conn)
                    delete_setup_token_file(settings.daemon_setup_token_file)
                    return

                await conn.execute("UPDATE devices SET revoked_at = NOW() WHERE revoked_at IS NULL")
                await conn.execute(
                    "UPDATE sessions SET revoked_at = NOW() WHERE revoked_at IS NULL"
                )
                await clear_setup_token_hash(conn)
                plaintext = await create_setup_token_if_absent(conn)
                if plaintext is not None:
                    _publish_setup_token(settings, plaintext, recovery=True)
    except Exception:
        logger.warning("First-boot setup check failed", exc_info=True)


_is_production = get_settings().daemon_environment.lower().strip() == "production"
app = FastAPI(
    title="daemon-orchestrator",
    lifespan=lifespan,
    # The strict CSP does not allow FastAPI's auto-generated Swagger UI /
    # ReDoc pages (CDN-hosted assets and an inline bootstrap script). We
    # disable the rendered docs endpoints unconditionally rather than
    # serve a weakened policy in any environment — the OpenAPI schema
    # itself remains available at the default /openapi.json path so API
    # clients, SDK generators, and development tooling can still introspect
    # the surface. Tests (tests/test_security_headers_and_cors.py) cover
    # the rendered docs endpoints returning 404.
    docs_url=None,
    redoc_url=None,
    # openapi_url left at the FastAPI default ("/openapi.json") — the schema
    # has no CDN assets or inline scripts that conflict with the strict CSP,
    # so disabling it would silently remove a useful API surface for tooling.
)


# Generic-message body for the global exception handler. The detail format
# is intentionally short and vocabulary-bounded so an attacker cannot
# fingerprint the failure mode (e.g. "TypeError: 'NoneType' object has no
# attribute 'foo'" leaking the source layout). The request_id is the
# correlation handle for support; it is the same id returned in the
# X-Request-ID response header.
_GENERIC_INTERNAL_ERROR = (
    "An internal error occurred. Please retry or contact support with the request id."
)

# Stable SSE error token for streaming error paths. Replaces `str(e)` in the
# `delta.content` / SSE error envelope so provider, HTTP, and Python exception
# text never reaches the client on the stream surface (issue #79 round-1
# finding: streaming chat branches still leaked str(e) via the SSE error
# payload). The token carries the request_id correlation handle; the full
# exception is logged server-side.
_SSE_INTERNAL_ERROR_TOKEN = (
    "An internal error occurred. Please retry or contact support with the request id."
)


def _sse_error_message(request_id: str | None) -> str:
    """Stable, sanitized error message for SSE error envelopes.

    Returns the SSE token plus the request id so support has a correlation
    handle. Never returns the original exception text.
    """

    rid = request_id or "-"
    return f"{_SSE_INTERNAL_ERROR_TOKEN} (request_id={rid})"


async def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a sanitized 500 response for any unhandled exception.

    The full traceback is logged server-side with the request id; the
    response body carries only the generic message and the request id,
    plus an X-Request-ID header. Route handlers that raise FastAPI's
    ``HTTPException`` are not affected — FastAPI's default handler still
    emits the ``detail`` they supplied. This handler is the safety net
    for ``Exception`` and its non-HTTP subclasses (RuntimeError, ValueError,
    asyncpg.PostgresError, etc.).
    """

    request_id = get_request_id(request) or "-"
    client_request_id = get_client_request_id(request)
    logger.exception(
        "Unhandled exception (request_id=%s, client_request_id=%s): %s",
        request_id,
        client_request_id or "-",
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": _GENERIC_INTERNAL_ERROR, "request_id": request_id},
        headers={REQUEST_ID_HEADER: request_id},
    )


app.add_exception_handler(Exception, _generic_exception_handler)

# CORS deny-by-default: use daemon_allowed_origins, filter empty strings.
# An empty list means no cross-origin requests are allowed.
_cors_allowed = [o.strip() for o in get_settings().daemon_allowed_origins.split(",") if o.strip()]
warn_on_unsafe_cors_wildcards(
    allow_credentials=True,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed,
    allow_credentials=True,
    allow_methods=list(CORS_ALLOW_METHODS),
    allow_headers=list(CORS_ALLOW_HEADERS),
    expose_headers=list(CORS_EXPOSE_HEADERS),
)
app.add_middleware(SecurityHeadersMiddleware)
# Request ID middleware: assigns or reuses an X-Request-ID for every
# request and exposes it via request.scope["state"]["request_id"]. The
# global exception handler (registered below) reads this id to attach
# it to the sanitized 500 response without leaking the original
# exception text. The outer wrap at the bottom of the module mirrors
# the header onto 500 responses generated by Starlette's outermost
# ServerErrorMiddleware.
app.add_middleware(RequestIdMiddleware)

_request_body_settings = get_settings()
app.add_middleware(
    RequestBodyLimitMiddleware,
    global_limit=_request_body_settings.daemon_max_request_body_bytes,
    route_limits={
        "/chat": _request_body_settings.daemon_max_chat_body_bytes,
        "/chat/completions": _request_body_settings.daemon_max_chat_body_bytes,
        "/v1/chat/completions": _request_body_settings.daemon_max_chat_body_bytes,
        "/stt": _request_body_settings.daemon_max_stt_body_bytes,
        "/skills/upload": _request_body_settings.daemon_max_skill_upload_body_bytes,
    },
)

# TrustedHostMiddleware: enforce an allowlist on the inbound Host header.
# Without this, a Host-header injection (Host: attacker.com) can be used
# to generate absolute URLs in error responses that point to attacker-
# controlled domains, confuse reverse proxies, or bypass domain-based
# authentication. The allowlist is read from DAEMON_ALLOWED_HOSTS via
# the Settings class. In production an empty allowlist is rejected at
# startup; in development it falls back to ["*"] for the dev experience.
# NOTE for operators: requests proxied by the Next frontend reach the
# backend with Host values like "backend:8000" or "localhost:8000".
# Starlette strips the port before matching, so DAEMON_ALLOWED_HOSTS must
# include the BARE internal hostnames (e.g. "backend", "localhost");
# resolve_allowed_hosts() also drops any :port suffix it finds.


class CaseInsensitiveTrustedHostMiddleware(TrustedHostMiddleware):
    """Starlette matches Host case-sensitively; hostnames are not.

    Lowercase the inbound Host header before matching (and for downstream
    consumers — DNS hostnames are case-insensitive by RFC 4343) so
    ``Host: APP.DAEMON.AI`` matches an ``app.daemon.ai`` allowlist entry.
    Allowlist entries are already lowercased by ``resolve_allowed_hosts``.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.allow_any and scope["type"] in ("http", "websocket"):
            headers = MutableHeaders(scope=scope)
            host = headers.get("host", "")
            lowered = host.lower()
            if lowered != host:
                headers["host"] = lowered
        await super().__call__(scope, receive, send)


# Import-time resolution must not raise: production-startup tests exercise
# other fail-closed paths in _validate_startup_config and must be able to
# import this module first. A misconfigured allowlist falls back to ["*"]
# here, but the app still refuses to START because the lifespan validation
# chain re-raises HostSecurityConfigError (fail-closed, just later).
try:
    _allowed_hosts = get_settings().resolve_allowed_hosts()
except HostSecurityConfigError as _host_exc:
    logger.critical(
        "Host security config invalid; startup will abort in lifespan: %s",
        _host_exc,
    )
    _allowed_hosts = ["*"]
if _allowed_hosts == ["*"]:
    logger.warning(
        "TrustedHostMiddleware is configured with allowed_hosts=['*']; "
        "the backend will accept any Host header. This is the default in "
        "development but is unsafe in production. Set DAEMON_ALLOWED_HOSTS "
        "to a comma-separated allowlist (e.g. 'app.daemon.ai,*.daemon.ai')."
    )
app.add_middleware(CaseInsensitiveTrustedHostMiddleware, allowed_hosts=_allowed_hosts)


def _build_trusted_spawn_context(
    user_id: uuid.UUID,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    video_meta = metadata.get("video_generation") if isinstance(metadata, dict) else None
    trusted_video: dict[str, Any] = {"user_id": str(user_id)}
    if not isinstance(video_meta, dict):
        return {"video": trusted_video}
    duration_raw = video_meta.get("duration")
    if isinstance(duration_raw, bool):
        duration = 5
    elif isinstance(duration_raw, (int, float, str)):
        try:
            duration = int(duration_raw)
        except (TypeError, ValueError):
            duration = 5
    else:
        duration = 5
    duration = max(duration, 1)

    duration = min(duration, 30)

    source_mode_raw = video_meta.get("source_mode")
    source_mode = (
        source_mode_raw
        if source_mode_raw in {"text-to-video", "image-to-video"}
        else "text-to-video"
    )

    reference_image_url = (
        video_meta.get("reference_image_url")
        if isinstance(video_meta.get("reference_image_url"), str)
        else None
    )
    reference_image_id = (
        video_meta.get("reference_image_id")
        if isinstance(video_meta.get("reference_image_id"), str)
        else None
    )

    raw_provider = video_meta.get("provider")
    video_provider = None
    kling_model = None
    audio_enabled = None
    if isinstance(raw_provider, str) and raw_provider.strip():
        provider_lower = raw_provider.lower().strip()
        if provider_lower == "kling":
            video_provider = "fal"
            raw_kling_model = video_meta.get("kling_model")
            if isinstance(raw_kling_model, str):
                model_lower = raw_kling_model.lower().strip()
                if model_lower == "kling-v3-pro":
                    kling_model = "v3-pro"
                elif model_lower in ("kling-o3-pro", "o3-pro"):
                    kling_model = "o3-pro"
            audio_enabled = video_meta.get("audio_enabled")
        elif provider_lower in ("xai", "fal"):
            video_provider = provider_lower

    trusted_video.update(
        {
            "mode": "video",
            "duration": duration,
            "source_mode": source_mode,
            "reference_image_url": reference_image_url,
            "reference_image_id": reference_image_id,
            "video_provider": video_provider,
            "kling_model": kling_model,
            "audio_enabled": audio_enabled,
        }
    )
    return {"video": trusted_video}


def _extract_text_content(content: Any) -> str:
    if isinstance(content, dict):
        direct_text = content.get("text")
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()
        nested_content = content.get("content")
        if isinstance(nested_content, str) and nested_content.strip():
            return nested_content.strip()
        if isinstance(direct_text, dict):
            nested_text = direct_text.get("value") or direct_text.get("content")
            if isinstance(nested_text, str) and nested_text.strip():
                return nested_text.strip()

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str) and part.strip():
                text_parts.append(part.strip())
                continue

            if not isinstance(part, dict):
                continue

            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
                continue

            content_field = part.get("content")
            if isinstance(content_field, str) and content_field.strip():
                text_parts.append(content_field.strip())
                continue

            if isinstance(text, dict):
                nested_text = text.get("value") or text.get("content")
                if isinstance(nested_text, str) and nested_text.strip():
                    text_parts.append(nested_text.strip())
        return "\n".join(text_parts).strip()
    return ""


def _extract_image_parts(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    image_parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "image_url":
            continue
        image_url = part.get("image_url")
        if not isinstance(image_url, dict):
            continue
        url = image_url.get("url")
        if isinstance(url, str) and url.startswith("data:image/"):
            image_parts.append({"type": "image_url", "image_url": {"url": url}})
    return image_parts


def _content_has_image(content: Any) -> bool:
    return len(_extract_image_parts(content)) > 0


def _build_user_content_from_attachments(
    user_text: str,
    attachments: list[dict[str, Any]],
) -> str | list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    text_segments: list[str] = []
    if user_text.strip():
        text_segments.append(user_text.strip())

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue

        kind = attachment.get("kind")
        name = attachment.get("name")
        if not isinstance(name, str) or not name:
            name = "unnamed"

        if kind == "image":
            data_url = attachment.get("data_url")
            if isinstance(data_url, str) and data_url.startswith("data:image/"):
                if text_segments:
                    parts.append({"type": "text", "text": "\n\n".join(text_segments)})
                    text_segments = []
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
                continue

        if kind == "text":
            text_content = attachment.get("text_content")
            if isinstance(text_content, str) and text_content.strip():
                text_segments.append(f"[Attached file: {name}]\n{text_content.strip()}")
                continue

        text_segments.append(f"[Attached file: {name}] (binary file attached)")

    if text_segments:
        parts.append({"type": "text", "text": "\n\n".join(text_segments)})

    if not parts:
        return user_text
    if len(parts) == 1 and parts[0].get("type") == "text":
        text = parts[0].get("text")
        return text if isinstance(text, str) else user_text
    return parts


async def _account_chat_frames(
    scope_pool: Any, scope_user_id: uuid.UUID, *, auto_route: bool = False, **kwargs: Any
) -> AsyncIterator[str]:
    async for frame in _account_frames(
        scope_pool,
        scope_user_id,
        lambda: stream_sse_chat(**kwargs),
        auto_route=auto_route,
    ):
        yield frame


async def _account_frames(
    scope_pool: Any,
    scope_user_id: uuid.UUID,
    source: Callable[[], AsyncIterator[str]],
    *,
    auto_route: bool = False,
    operation: str = "chat",
    extended: bool = False,
) -> AsyncIterator[str]:
    # Keep the ContextVar scope inside one producer task. The keepalive bridge
    # may resume its input generator in a different task for each frame.
    frames: asyncio.Queue[tuple[str | None, Exception | None]] = asyncio.Queue(maxsize=1)

    async def produce() -> None:
        try:
            async with account_compute(
                scope_pool,
                scope_user_id,
                auto_route=auto_route,
                operation=operation,
                extended=extended,
            ):
                async for frame in source():
                    await frames.put((frame, None))
        except Exception as exc:
            await frames.put((None, exc))
        finally:
            await frames.put((None, None))

    producer = asyncio.create_task(produce())
    try:
        while True:
            frame, error = await frames.get()
            if error is not None:
                raise error
            if frame is None:
                break
            yield frame
    finally:
        if not producer.done():
            producer.cancel()
        try:
            await producer
        except asyncio.CancelledError:
            pass


def _approved_chat_model(model: str | None = None) -> str:
    try:
        return choose_route(model).model
    except ComputeUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _extract_council_config_response(message: str) -> dict[str, Any] | None:
    raw = message.strip()
    lowered = raw.lower()
    if not lowered.startswith("/council config:"):
        return None

    payload = raw.split(":", 1)[1].strip()
    if not payload:
        return {}

    if payload.startswith("{"):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    result: dict[str, Any] = {}
    for item in [part.strip() for part in payload.split(",") if part.strip()]:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip().lower()
        value = value.strip()

        if key == "preset":
            result["preset_name"] = value.lower()
            continue

        if key == "rounds":
            try:
                result["round_count"] = int(value)
            except ValueError:
                continue
            continue

        if key == "audit":
            result["audit_enabled"] = value.lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

    return result


def _extract_latest_council_prompt(messages: list[dict[str, Any]]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = _extract_text_content(msg.get("content")).strip()
        if not content:
            continue

        lowered = content.lower()
        if lowered.startswith("/council config:"):
            continue
        if lowered.startswith("/council"):
            prompt = content[len("/council") :].strip()
            if prompt.startswith("--default"):
                prompt = prompt[len("--default") :].strip()
            return prompt or None

    return None


def _parse_sse_frame(frame: str) -> tuple[str | None, dict[str, Any] | None]:
    event_type: str | None = None
    payload_raw: str | None = None

    for line in frame.splitlines():
        if line.startswith("event: "):
            event_type = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            payload_raw = line[len("data: ") :].strip()

    if payload_raw is None:
        return event_type, None

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return event_type, None

    if isinstance(payload, dict):
        return event_type, payload

    return event_type, None


def _extract_council_event_for_persistence(frame: str) -> dict[str, Any] | None:
    event_type, payload = _parse_sse_frame(frame)
    if payload is None:
        return None

    supported_types = {
        "council_interview",
        "council_progress",
        "council_output",
        "council_done",
        "council_error",
    }
    if event_type not in supported_types:
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}

    event: dict[str, Any] = {"type": event_type}
    for key in (
        "roster",
        "presets",
        "rounds_options",
        "audit_default",
        "stage",
        "current_round",
        "total_rounds",
        "models_complete",
        "models_total",
        "section",
        "content",
        "metadata",
        "session_id",
        "total_tokens",
        "total_cost_usd",
        "models_used",
        "error",
    ):
        if key in data:
            event[key] = data[key]

    event_id = payload.get("id")
    if isinstance(event_id, str) and event_id:
        event["id"] = event_id

    request_id = payload.get("request_id")
    if isinstance(request_id, str) and request_id:
        event["request_id"] = request_id

    return event


def _build_council_assistant_content(council_events: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for event in council_events:
        if event.get("type") == "council_output":
            content = event.get("content")
            if isinstance(content, str) and content.strip():
                sections.append(content.strip())

    if sections:
        return "\n\n".join(sections)

    for event in reversed(council_events):
        if event.get("type") == "council_error":
            error = event.get("error")
            if isinstance(error, str) and error.strip():
                return f"Council error: {error.strip()}"

    if any(event.get("type") == "council_interview" for event in council_events):
        return "Council configuration requested."

    return "Council run completed."


# ============== Health & Info Endpoints ==============


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    base: dict[str, Any] = {"status": "ok"}
    try:
        state = get_app_state(request)
        base["services"] = await check_db_health(state)
    except Exception:
        logger.warning("Health check failed", exc_info=True)
        base["status"] = "degraded"
        base["error"] = "Health check unavailable"
    return base


@app.get("/providers")
async def list_providers(
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, list[str] | str]:
    """List all available LLM providers."""
    providers = settings.list_available_providers()
    return {
        "providers": providers,
        "default": settings.default_provider,
    }


# ============== OpenAI Compatible Endpoints ==============


def _selectable_model_ids() -> set[str]:
    """Only reviewed, available text routes can be offered for manual selection."""
    try:
        policy = load_inference_policy()
    except PolicyError:
        return set()
    return {
        route.model
        for route in policy.routes.values()
        if route.provider == "openrouter"
        and route.model.startswith("openrouter/")
        and route.is_approved(policy.requirements)
        and route.supports(
            required_capabilities=frozenset({"text"}), input_tokens=1, output_tokens=1
        )
    }


@app.get("/api/models")
async def api_models_redirect(
    settings: Settings = Depends(get_settings),
):
    """Redirect /api/models to /v1/models for Open WebUI compatibility."""
    return await openai_list_models(settings)


@app.get("/models")
async def models_redirect(
    settings: Settings = Depends(get_settings),
):
    """Redirect /models to /v1/models for Open WebUI compatibility."""
    return await openai_list_models(settings)


@app.get("/v1/models")
async def openai_list_models(
    settings: Settings = Depends(get_settings),
) -> OpenAIModelList:
    """OpenAI-compatible models endpoint for Open WebUI integration.

    No auth required - model listing is public info.

    Fetches all available models from OpenRouter API dynamically with caching.
    Falls back to configured default model if OpenRouter API is unavailable.
    """
    models = [
        OpenAIModelInfo(
            id="auto",
            object="model",
            created=int(time.time()),
            owned_by="daemon",
            metadata={"capabilities": ["chat", "streaming"]},
        )
    ]
    selectable = _selectable_model_ids()
    if not selectable:
        return OpenAIModelList(data=models)
    timestamp = int(time.time())  # noqa: F841

    # Fetch OpenRouter models dynamically with caching
    try:
        openrouter_models = await fetch_openrouter_models(
            api_key=settings.openrouter_api_key,
        )

        # Add metadata and convert to OpenAIModelInfo format
        for model_data in openrouter_models:
            model_id = model_data["id"]
            if not isinstance(model_id, str):
                continue
            model_id = model_id if model_id.startswith("openrouter/") else f"openrouter/{model_id}"
            if model_id not in selectable:
                continue

            # Build metadata dict
            metadata: dict[str, Any] = {
                "capabilities": ["chat", "streaming"],
            }

            # Add pricing and context length if available from OpenRouter API
            if "pricing" in model_data:
                metadata["pricing"] = model_data["pricing"]
            if "context_length" in model_data:
                metadata["context_length"] = model_data["context_length"]

            models.append(
                OpenAIModelInfo(
                    id=model_id,
                    object="model",
                    created=model_data.get("created", int(time.time())),
                    owned_by="openrouter",
                    metadata=metadata,
                )
            )

    except Exception as e:
        logger.warning(f"Failed to fetch OpenRouter models: {e}")
        # Public catalog availability is not an inference approval signal.

    return OpenAIModelList(data=models)


@app.get("/v1/catalog")
async def get_model_catalog() -> dict[str, Any]:
    from typing import cast
    from orchestrator.catalog import FEATURED_MODELS, get_catalog
    from orchestrator.models_cache import get_cached_models

    catalog = get_catalog()
    selectable = _selectable_model_ids()

    # Add dynamic new models from cache
    cached = get_cached_models()
    featured_ids = {fm.id for fm in FEATURED_MODELS}

    # Get models that are new and not already in featured
    dynamic_new = [
        {
            "id": m["id"] if m["id"].startswith("openrouter/") else f"openrouter/{m['id']}",
            "name": m.get("name", m["id"]),
            "tagline": "Newly added",
            "badges": ["new"],
        }
        for m in cached
        if isinstance(m.get("id"), str)
        and m.get("is_new")
        and m["id"] not in featured_ids
        and (m["id"] if m["id"].startswith("openrouter/") else f"openrouter/{m['id']}")
        in selectable
    ][:2]

    featured = [
        model
        for model in cast(list[dict[str, Any]], catalog["featured"])
        if model["id"] in selectable
    ]
    featured.extend(dynamic_new)

    return {
        "auto": catalog["auto"],
        "featured": featured,
    }


@app.post(
    "/chat/completions",
    response_model=None,
    responses=REQUEST_BODY_TOO_LARGE_RESPONSES,
)
async def chat_completions_redirect(
    payload: OpenAIChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Redirect /chat/completions to /v1/chat/completions for Open WebUI compatibility."""
    return await openai_chat_completions(payload, request, settings, auth)


@app.post(
    "/v1/chat/completions",
    response_model=None,
    responses=REQUEST_BODY_TOO_LARGE_RESPONSES,
)
async def openai_chat_completions(
    payload: OpenAIChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> StreamingResponse | OpenAIChatResponse:
    """OpenAI-compatible chat completions endpoint for Open WebUI integration."""

    # Per-issue-#38 rate limit must run before payload validation so
    # abusive clients cannot flood the validation path while a
    # legitimate user's budget is being drained.
    await _enforce_chat_rate_limit(
        request=request,
        auth=auth,
        settings=settings,
        endpoint="chat:openai",
    )

    # Extract the last user message
    user_messages = [m for m in payload.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found")

    last_message = _extract_text_content(user_messages[-1].content)
    if not last_message:
        last_message = "Please help with the attached input."
    conversation_id = new_conversation_id()
    # Reuse the HTTP correlation id that the request-id middleware already
    # assigned to ``request.scope`` so the OpenAI streaming error log, the
    # SSE error chunk, and the ``X-Request-ID`` response header all
    # advertise the same handle (round-3 Codex finding on PR #219;
    # mirrors the native ``/chat`` fix at line 1788). The value is
    # forwarded to ``stream_sse_chat`` so daemon.py's internal
    # exception handler logs with the same id.
    request_id = get_request_id(request) or new_request_id()

    # Determine provider from model ID
    provider_name = settings.default_provider
    if payload.model.startswith("openrouter/"):
        provider_name = "openrouter"

    provider_config = settings.get_provider_config(provider_name)
    trusted_spawn_context = _build_trusted_spawn_context(auth.user_id, None)

    # Strip provider prefix to get actual model ID
    actual_model = payload.model
    if provider_name != "openrouter":
        for prefix in ["openrouter/", "opencode/"]:
            if actual_model.startswith(prefix):
                actual_model = actual_model[len(prefix) :]
                break
    if actual_model in {"default", "", "kimi", "auto"}:
        actual_model = ""
    actual_model = _approved_chat_model(
        actual_model if actual_model not in {"default", "", "kimi", "auto"} else None
    )

    system_prompts = [
        _extract_text_content(m.content)
        for m in payload.messages
        if m.role == "system" and _extract_text_content(m.content)
    ]
    system_prompt = system_prompts[-1] if system_prompts else DAEMON_SYSTEM_PROMPT
    user_timezone = None
    try:
        timezone_store = request.app.state.app_state.memory_store
        if timezone_store is not None:
            user_timezone = extract_timezone_name(
                await timezone_store.get_user_settings(auth.user_id)
            )
    except Exception:
        logger.warning("User timezone unavailable; using deployment default", exc_info=True)
    try:
        app_state = request.app.state.app_state
        db_pool = getattr(app_state, "db_pool", None)
        skills_block = await build_skill_index(db_pool=db_pool)
    except Exception:
        logger.warning("Skills injection failed, continuing without skills", exc_info=True)
        skills_block = ""
    if skills_block and skills_block not in system_prompt:
        system_prompt = f"{system_prompt.rstrip()}\n\n{skills_block}"

    if payload.stream:

        async def is_disconnected() -> bool:
            return await request.is_disconnected()

        async def generator():
            try:
                provider = provider_config.name  # noqa: F841
                model = actual_model  # noqa: F841
                timestamp = int(time.time())
                chunk_id = f"chatcmpl-{new_request_id()}"

                # Stream chunks
                token_count = 0  # noqa: F841
                content_buffer = ""
                reported_model = actual_model

                async for frame in _account_chat_frames(
                    getattr(request.app.state.app_state, "db_pool", None),
                    auth.user_id,
                    auto_route=payload.model in {"default", "", "kimi", "auto"},
                    settings=settings,
                    provider_config=provider_config,
                    system_prompt=system_prompt,
                    user_message=last_message,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    ping_interval_s=settings.sse_keepalive_interval_s,
                    is_disconnected=is_disconnected,
                    actual_model=actual_model,
                    user_id=auth.user_id,
                    trusted_spawn_context=trusted_spawn_context,
                    user_timezone=user_timezone,
                ):
                    if frame.startswith("event: routing") or frame.startswith("event: final"):
                        _, envelope = _parse_sse_frame(frame)
                        candidate = (envelope or {}).get("data", {}).get("model")
                        if isinstance(candidate, str) and candidate.startswith("openrouter/"):
                            reported_model = candidate
                    # Parse the SSE frame
                    if frame.startswith("event: token"):
                        # Extract content from data: line
                        lines = frame.split("\n")
                        for line in lines:
                            if line.startswith("data: "):
                                try:
                                    data = json.loads(line[6:])
                                    delta_content = data.get("data", {}).get("delta", "")
                                    if delta_content:
                                        content_buffer += delta_content
                                        chunk = OpenAIChatStreamChunk(
                                            id=chunk_id,
                                            created=timestamp,
                                            model=reported_model,
                                            choices=[
                                                OpenAIChoice(
                                                    index=0,
                                                    delta=OpenAIDeltaMessage(
                                                        role="assistant",
                                                        content=delta_content,
                                                    ),
                                                    finish_reason=None,
                                                )
                                            ],
                                        )
                                        yield f"data: {chunk.model_dump_json()}\n\n"
                                except Exception:
                                    pass

                    elif frame.startswith("event: final"):
                        # Final chunk with finish_reason
                        chunk = OpenAIChatStreamChunk(
                            id=chunk_id,
                            created=timestamp,
                            model=reported_model,
                            choices=[
                                OpenAIChoice(
                                    index=0,
                                    delta=OpenAIDeltaMessage(),
                                    finish_reason="stop",
                                )
                            ],
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"
                        yield "data: [DONE]\n\n"

            except Exception as e:
                # Streaming error — never emit `str(e)` to the client
                # (issue #79 round-1 finding). Server-side gets the full
                # exception with the request id; the SSE error chunk
                # carries the stable token plus the correlation handle.
                request_id_local = get_request_id(request)
                logger.exception(
                    "Streaming chat completion error (request_id=%s): %s",
                    request_id_local,
                    e,
                )

                error_chunk = OpenAIChatStreamChunk(
                    id=f"chatcmpl-{new_request_id()}",
                    created=int(time.time()),
                    model=payload.model,
                    choices=[
                        OpenAIChoice(
                            index=0,
                            delta=OpenAIDeltaMessage(
                                content=_sse_error_message(request_id_local),
                            ),
                            finish_reason="stop",
                        )
                    ],
                )
                yield f"data: {error_chunk.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream_with_keepalives(generator(), settings.sse_keepalive_interval_s),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        # Non-streaming response
        # Collect all content
        content_parts = []
        reported_model = actual_model

        try:

            async def is_disconnected() -> bool:
                return False

            async for frame in _account_chat_frames(
                getattr(request.app.state.app_state, "db_pool", None),
                auth.user_id,
                auto_route=payload.model in {"default", "", "kimi", "auto"},
                settings=settings,
                provider_config=provider_config,
                system_prompt=system_prompt,
                user_message=last_message,
                conversation_id=conversation_id,
                request_id=request_id,
                ping_interval_s=settings.sse_keepalive_interval_s,
                is_disconnected=is_disconnected,
                actual_model=actual_model,
                user_id=auth.user_id,
                trusted_spawn_context=trusted_spawn_context,
                user_timezone=user_timezone,
            ):
                if frame.startswith("event: routing") or frame.startswith("event: final"):
                    _, envelope = _parse_sse_frame(frame)
                    candidate = (envelope or {}).get("data", {}).get("model")
                    if isinstance(candidate, str) and candidate.startswith("openrouter/"):
                        reported_model = candidate
                if frame.startswith("event: token"):
                    lines = frame.split("\n")
                    for line in lines:
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                # Try 'delta' first (normal mode), then 'text' (mock mode)
                                token_content = data.get("data", {}).get("delta", "")
                                if not token_content:
                                    token_content = data.get("data", {}).get("text", "")
                                if token_content:
                                    content_parts.append(token_content)
                            except Exception:
                                pass

            final_content = "".join(content_parts)

            # Fallback for mock mode: if no content was collected, use mock response
            if not final_content and settings.mock_llm:
                final_content = "(mock) Mock response from Daemon"

            return OpenAIChatResponse(
                id=f"chatcmpl-{request_id}",
                created=int(time.time()),
                model=reported_model,
                choices=[
                    OpenAIChoice(
                        index=0,
                        message=OpenAIMessage(role="assistant", content=final_content),
                        finish_reason="stop",
                    )
                ],
                usage=OpenAIUsage(
                    prompt_tokens=len(system_prompt) // 4 + len(last_message) // 4,
                    completion_tokens=len(final_content) // 4,
                    total_tokens=(len(system_prompt) + len(last_message) + len(final_content)) // 4,
                ),
            )

        except ComputeUnavailable as exc:
            raise HTTPException(
                status_code=503, detail={"code": exc.code, "message": exc.message}
            ) from exc
        except Exception as exc:
            logger.exception("OpenAI-compatible chat completion failed (request_id=%s)", request_id)
            raise HTTPException(status_code=500, detail=_GENERIC_INTERNAL_ERROR) from exc


# ============== Generated Images Static Serving ==============

GENERATED_IMAGES_DIR = Path(__file__).resolve().parent.parent / "data" / "generated_images"
GENERATED_AUDIO_DIR = Path(__file__).resolve().parent.parent / "data" / "generated_audio"
GENERATED_FILES_DIR = Path(__file__).resolve().parent.parent / "data" / "generated_files"
TTS_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "tts_cache"
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/generated-images/{filename}")
async def serve_generated_image(
    filename: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> FileResponse:
    """Serve a generated image file from disk."""
    filepath = _resolve_safe_file_path(GENERATED_IMAGES_DIR, filename, auth.user_id)
    if filepath is None:
        raise HTTPException(status_code=404, detail="Image not found")

    media_type = "image/png"
    if filename.endswith((".jpg", ".jpeg")):
        media_type = "image/jpeg"
    elif filename.endswith(".webp"):
        media_type = "image/webp"
    return FileResponse(filepath, media_type=media_type)


def _resolve_safe_file_path(
    base_dir: Path,
    filename: str,
    user_id: uuid.UUID | str | None,
) -> Path | None:
    return resolve_owned_artifact(base_dir, user_id, filename)


@app.get("/generated-audio/{filename}")
async def serve_generated_audio(
    filename: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> FileResponse:
    """Serve a generated audio file from disk (TTS or sound effects)."""
    filepath = _resolve_safe_file_path(TTS_CACHE_DIR, filename, auth.user_id)
    if filepath is None:
        filepath = _resolve_safe_file_path(GENERATED_AUDIO_DIR, filename, auth.user_id)
    if filepath is None:
        raise HTTPException(status_code=404, detail="Audio not found")

    media_type = "audio/mpeg"
    if filename.endswith(".wav"):
        media_type = "audio/wav"
    elif filename.endswith((".ogg", ".opus")):
        media_type = "audio/ogg"
    return FileResponse(filepath, media_type=media_type)


@app.get("/generated-files/{filename}")
async def serve_generated_file(
    filename: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> FileResponse:
    """Serve a generated document file from disk."""
    filepath = _resolve_safe_file_path(GENERATED_FILES_DIR, filename, auth.user_id)
    if filepath is None:
        raise HTTPException(status_code=404, detail="File not found")

    # Determine media type based on extension
    media_type = "application/octet-stream"
    if filename.endswith(".docx"):
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif filename.endswith(".csv"):
        media_type = "text/csv"
    elif filename.endswith(".xlsx"):
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif filename.endswith(".txt"):
        media_type = "text/plain"

    return FileResponse(filepath, media_type=media_type)


@app.post("/tts")
async def text_to_speech(
    payload: TtsRequest,
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    raise HTTPException(
        status_code=503,
        detail={"code": "route_unavailable", "message": "Approved audio route unavailable"},
    )


@app.get("/audio/token")
async def get_audio_token(
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    """Return scoped ElevenLabs token for frontend WebSocket streaming.

    The frontend uses this token to establish direct WebSocket connections
    to ElevenLabs for real-time TTS streaming, avoiding the latency
    penalty of proxying through the backend.

    Returns a scoped single-use token instead of the raw API key
    to prevent key exposure in the browser.
    """

    raise HTTPException(
        status_code=503,
        detail={"code": "route_unavailable", "message": "Approved audio route unavailable"},
    )


@app.get("/audio/scribe-token")
async def get_scribe_token(
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:

    raise HTTPException(
        status_code=503,
        detail={"code": "route_unavailable", "message": "Approved audio route unavailable"},
    )


@app.post("/stt", responses=REQUEST_BODY_TOO_LARGE_RESPONSES)
async def speech_to_text(
    audio_file: UploadFile = File(...),
    model: str = Form("scribe_v2"),
    language: str | None = Form(None),
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:

    raise HTTPException(
        status_code=503,
        detail={"code": "route_unavailable", "message": "Approved audio route unavailable"},
    )


@app.post("/sound-effects")
async def generate_sound_effect(
    text: str = Form(...),
    duration_seconds: float = Form(2.0),
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> FileResponse:

    raise HTTPException(
        status_code=503,
        detail={"code": "route_unavailable", "message": "Approved audio route unavailable"},
    )


# ============== Legacy Daemon Endpoint ==============


def _chat_rate_limit_policies(
    request: Request,
    *,
    auth: AuthenticatedDevice,
    settings: Settings,
) -> list[tuple[ScopeKind, str, RateLimitPolicy]]:
    """Return rate-limit policies for ``/chat`` and ``/v1/chat/completions``.

    Two authenticated scopes are layered so a single compromised token cannot
    drain the operator's LLM budget regardless of which user or device the
    attacker is masquerading as (issue #38):

    * Per-session — narrowest scope, checked first so a runaway
      session never increments the broader user counter.
    * Per-user — primary defence against compromised-token abuse. The
      operator's LLM bill is bounded per account regardless of how
      many devices/tokens that account owns.

    The per-IP quota is enforced by middleware before body validation so
    malformed payloads cannot bypass it. All checks use a 60-second window.

    The route-level ``endpoint`` (``chat:daemon`` vs ``chat:openai``)
    flows into the Redis key so each chat route keeps an independent quota.
    """
    return [
        (
            "session_id",
            str(auth.session_id),
            RateLimitPolicy(
                limit=settings.daemon_rate_limit_chat_per_token_per_minute,
                window_seconds=60,
            ),
        ),
        (
            "user_id",
            str(auth.user_id),
            RateLimitPolicy(
                limit=settings.daemon_rate_limit_chat_per_user_per_minute,
                window_seconds=60,
            ),
        ),
    ]


async def _enforce_chat_rate_limit(
    *,
    request: Request,
    auth: AuthenticatedDevice,
    settings: Settings,
    endpoint: str,
) -> None:
    """Enforce ``_chat_rate_limit_policies`` for the named chat endpoint.

    Helper exists so ``/chat`` and ``/v1/chat/completions`` (and the
    ``/chat/completions`` alias) can share the same policy wiring. The
    endpoint tag is the only difference so logs and Redis keys stay
    per-route.
    """
    limiter = get_rate_limiter(request)
    await enforce_rate_limit(
        request=request,
        limiter=limiter,
        endpoint=endpoint,
        policies=_chat_rate_limit_policies(
            request,
            auth=auth,
            settings=settings,
        ),
    )


_CHAT_IP_RATE_LIMIT_ENDPOINTS = {
    "/chat": "chat:daemon",
    "/chat/completions": "chat:openai",
    "/v1/chat/completions": "chat:openai",
}


@app.middleware("http")
async def _enforce_chat_ip_rate_limit_before_body_validation(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Charge chat IP quotas before FastAPI parses or validates the body."""

    endpoint = _CHAT_IP_RATE_LIMIT_ENDPOINTS.get(request.url.path.rstrip("/") or "/")
    if request.method.upper() != "POST" or endpoint is None:
        return await call_next(request)

    record_chat_rate_limit_request(endpoint)
    settings = get_settings()
    policy = RateLimitPolicy(
        limit=settings.daemon_rate_limit_chat_per_ip_per_minute,
        window_seconds=60,
    )
    try:
        await enforce_rate_limit(
            request=request,
            policies=[("ip", client_ip_for_key(request), policy)],
            limiter=get_rate_limiter(request),
            endpoint=endpoint,
        )
    except HTTPException as exc:
        if exc.status_code == 429:
            scope = (exc.headers or {}).get("X-Daemon-Rate-Limit-Scope", "unknown")
            record_chat_rate_limit_rejection(endpoint, scope)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )
    response = await call_next(request)
    if response.status_code == 429:
        scope = response.headers.get("X-Daemon-Rate-Limit-Scope")
        if scope:
            record_chat_rate_limit_rejection(endpoint, scope)
    return response


@app.post("/chat", responses=REQUEST_BODY_TOO_LARGE_RESPONSES)
async def chat(
    payload: ChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> StreamingResponse:
    # Per-issue-#38 rate limit runs after auth so user/session scope
    # values are populated, but before any LLM-backed work so the
    # operator's budget is bounded even when the request would have
    # succeeded.
    await _enforce_chat_rate_limit(
        request=request,
        auth=auth,
        settings=settings,
        endpoint="chat:daemon",
    )
    conversation_id = payload.conversation_id or new_conversation_id()
    # Warn if no conversation_id was provided - should not happen in normal frontend flow
    if not payload.conversation_id:
        logger.warning(
            "No conversation_id provided — creating new conversation. "
            "This should not happen in normal frontend flow."
        )
    # Reuse the HTTP correlation id that the request-id middleware already
    # assigned to ``request.scope`` so the SSE error envelope, the
    # streaming-error log, and the ``X-Request-ID`` response header all
    # advertise the same handle to the browser (round-2 Codex finding on
    # PR #219; mirrors the OpenAI-compatible chat completion fix at line
    # 1340). Fall back to a fresh id when the middleware has not run
    # (e.g. background tests that bypass the ASGI stack).
    request_id = get_request_id(request) or new_request_id()

    # Get provider configuration from request or default
    provider_config = settings.get_provider_config(payload.provider)
    if app_state.db_pool is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "account_unavailable", "message": "Account compute unavailable"},
        )

    incoming_messages = payload.messages or []
    attachments = payload.attachments or []
    last_user_message = None
    last_user_msg: dict[str, Any] | None = None
    for msg in reversed(incoming_messages):
        if msg.get("role") == "user":
            last_user_msg = msg
            last_user_message = _extract_text_content(msg.get("content"))
            break
    user_message = (last_user_message or payload.message).strip()
    if not user_message and attachments:
        user_message = "Please analyze the attached files."

    council_config_response = _extract_council_config_response(user_message)
    is_council_config_response = council_config_response is not None
    is_council_command = (
        user_message.lstrip().startswith("/council") and not is_council_config_response
    )

    if is_council_config_response and council_config_response is not None:
        interview_prompt = _extract_latest_council_prompt(incoming_messages)
        if interview_prompt:
            council_config_response = {
                **council_config_response,
                "_prompt": interview_prompt,
            }

    prepared_user_content: str | list[dict[str, Any]] = user_message
    if attachments:
        prepared_user_content = _build_user_content_from_attachments(user_message, attachments)
    elif last_user_msg and isinstance(last_user_msg.get("content"), list):
        prepared_user_content = [
            part for part in last_user_msg.get("content", []) if isinstance(part, dict)
        ]

    decision = route_message(user_message, payload.metadata)

    user_model_choice = payload.model or "auto"
    if user_model_choice == "auto" and last_user_msg:
        msg_model = last_user_msg.get("model")
        if isinstance(msg_model, str):
            msg_model = msg_model.strip()
            if msg_model and msg_model != "auto":
                user_model_choice = msg_model
    has_code = "```" in user_message
    turn_count = len(incoming_messages) if incoming_messages else 0

    model_decision = select_model_tier(
        message=user_message,
        turn_count=turn_count,
        has_code_block=has_code,
        user_override=user_model_choice,
    )

    if model_decision.tier == "explicit":
        selected_model = model_decision.model
    elif model_decision.tier == "fast":
        selected_model = settings.auto_fast_model
    elif model_decision.tier == "reasoning":
        selected_model = settings.auto_reasoning_model
    else:
        selected_model = provider_config.model
    selected_model = _approved_chat_model(
        selected_model if model_decision.tier == "explicit" else None
    )

    actual_model = selected_model
    if provider_config.name != "openrouter":
        for prefix in ["openrouter/", "opencode/"]:
            if actual_model.startswith(prefix):
                actual_model = actual_model[len(prefix) :]
                break

    routing_info: dict[str, Any] = {
        "model": selected_model if model_decision.tier == "explicit" else "auto",
        "tier": model_decision.tier,
        "reason": model_decision.reason,
    }

    has_image_input = _content_has_image(prepared_user_content)
    if has_image_input:
        raise HTTPException(
            status_code=403,
            detail={"code": "modality_unavailable", "message": "Multimodal compute unavailable"},
        )

    # Initialize persistence with graceful degradation
    store = app_state.memory_store if app_state else None
    user_id = auth.user_id if store else None
    conversation_uuid = None
    conversation_exists = False

    # Create or get conversation if persistence is available
    if store and user_id:
        try:
            if payload.conversation_id:
                try:
                    conv_uuid = uuid.UUID(conversation_id.replace("conv_", ""))
                except ValueError as exc:
                    raise HTTPException(status_code=404, detail="Conversation not found") from exc

                existing = await store.get_conversation(conv_uuid)
                if not existing:
                    raise HTTPException(status_code=404, detail="Conversation not found")
                if existing.get("user_id") != user_id:
                    raise HTTPException(status_code=403, detail="Conversation forbidden")

                conversation_uuid = conv_uuid
                conversation_exists = True
            else:
                title = user_message[:50] + "..." if len(user_message) > 50 else user_message
                conv = await store.create_conversation(
                    user_id=user_id, pipeline=decision.pipeline, title=title
                )
                conversation_uuid = conv["id"]
                conversation_id = f"conv_{conversation_uuid}"

            # Insert user message
            if conversation_uuid:
                await store.insert_message(
                    conversation_id=conversation_uuid,
                    user_id=user_id,
                    role="user",
                    content=user_message,
                    model=None,
                    status="complete",
                )

                if not conversation_exists and app_state.redis:
                    try:
                        await app_state.redis.enqueue_job(
                            "generate_title",
                            str(conversation_uuid),
                            user_message,
                            _job_id=f"title:{conversation_uuid}",
                            _defer_by=0,
                        )
                    except Exception as enqueue_error:
                        logger.warning("Failed to enqueue title generation: %s", enqueue_error)
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(
                "Conversation persistence failed, continuing without persistence: %s", e
            )  # Graceful degradation - continue without persistence
            # A refreshed client may have no history of its own. Preserve readable
            # prior turns, appending the unsaved question rather than replacing one.
            if conversation_exists and conversation_uuid and not incoming_messages:
                try:
                    prior_messages = await store.get_recent_messages(
                        conversation_uuid,
                        limit=settings.chat_history_limit,
                        exclude_status=["streaming", "error", "cancelled"],
                    )
                    incoming_messages = [
                        msg for msg in prior_messages if msg.get("role") != "system"
                    ] + [{"role": "user", "content": user_message}]
                except Exception:
                    logger.warning("Conversation history unavailable during persistence failure")
            # Do not re-read history as if the current question had been stored,
            # or persist an orphan assistant reply after the user insert failed.
            store = None
            conversation_uuid = None
            conversation_exists = False

    history_messages: list[dict[str, Any]] | None = None

    def _to_history_message(msg: dict[str, Any]) -> dict[str, Any]:
        mapped: dict[str, Any] = {
            "role": msg.get("role"),
            "content": msg.get("content"),
        }
        if msg.get("reasoning_text"):
            mapped["reasoning"] = msg.get("reasoning_text")
        if msg.get("reasoning_duration_secs") is not None:
            mapped["reasoning_duration_secs"] = msg.get("reasoning_duration_secs")
        if msg.get("reasoning_model"):
            mapped["reasoning_model"] = msg.get("reasoning_model")
        return mapped

    if conversation_exists and store and conversation_uuid:
        try:
            db_messages = await store.get_recent_messages(
                conversation_uuid,
                limit=settings.chat_history_limit,
                exclude_status=["streaming", "error", "cancelled"],
            )
            history_messages = [
                _to_history_message(msg)
                for msg in db_messages
                if msg.get("role")
                and msg.get("content") is not None
                and msg.get("role") != "system"
            ]
        except Exception:
            conversation_exists = False

    if not history_messages:
        if incoming_messages:
            history_messages = [
                _to_history_message(msg)
                for msg in incoming_messages
                if msg.get("role") and msg.get("content") is not None
            ]

    if history_messages:
        for msg in reversed(history_messages):
            if msg.get("role") == "user":
                msg["content"] = prepared_user_content
                break

    assembled_system_prompt = DAEMON_SYSTEM_PROMPT
    user_timezone = None
    try:
        db_pool = getattr(app_state, "db_pool", None)
        skills_block = await build_skill_index(db_pool=db_pool)
    except Exception:
        logger.warning("Skills injection failed, continuing without skills", exc_info=True)
        skills_block = ""
    if store and user_id and conversation_uuid:
        try:
            from orchestrator.memory.injection import (
                assemble_system_prompt,
                build_memory_context,
                format_preferences_block,
            )

            user_settings = await store.get_user_settings(user_id)
            user_timezone = extract_timezone_name(user_settings)
            preferences_block = format_preferences_block(user_settings)
            memory_context = await build_memory_context(store, conversation_uuid)
            assembled_system_prompt = await assemble_system_prompt(
                memory_context=memory_context,
                preferences_block=preferences_block,
                conversation_id=conversation_uuid,
            )
        except Exception:
            logger.warning("Memory injection failed, using base prompt", exc_info=True)

    if skills_block and skills_block not in assembled_system_prompt:
        assembled_system_prompt = f"{assembled_system_prompt.rstrip()}\n\n{skills_block}"

    async def is_disconnected() -> bool:
        return await request.is_disconnected()

    async def generator():
        try:
            if is_council_config_response:
                persisted_council_events: list[dict[str, Any]] = []
                if conversation_uuid:
                    yield sse(
                        "conversation",
                        {
                            "type": "conversation",
                            "id": "evt_conversation",
                            "ts": now_rfc3339(),
                            "conversation_id": conversation_id,
                            "request_id": request_id,
                            "data": {"conversation_id": str(conversation_uuid)},
                        },
                    )

                async for frame in _account_frames(
                    app_state.db_pool,
                    auth.user_id,
                    lambda: stream_council_interview_response(
                        user_message=user_message,
                        conversation_id=conversation_id,
                        request_id=request_id,
                        stored_config=council_config_response,
                    ),
                    operation="agent",
                    auto_route=True,
                    extended=True,
                ):
                    parsed_event = _extract_council_event_for_persistence(frame)
                    if parsed_event is not None:
                        persisted_council_events.append(parsed_event)
                    yield frame

                if store and conversation_uuid and user_id and persisted_council_events:
                    try:
                        await store.insert_message(
                            conversation_id=conversation_uuid,
                            user_id=user_id,
                            role="assistant",
                            content=_build_council_assistant_content(persisted_council_events),
                            model="council",
                            tool_results=[
                                {
                                    "name": "council_events",
                                    "result": {
                                        "events": persisted_council_events,
                                    },
                                    "request_id": request_id,
                                }
                            ],
                            metadata={
                                "request_id": request_id,
                                "council_events": persisted_council_events,
                            },
                            status="complete",
                        )
                    except Exception:
                        logger.warning(
                            "Failed to persist council interview response output",
                            exc_info=True,
                        )

                yield sse(
                    "done",
                    {
                        "type": "done",
                        "id": "evt_done",
                        "ts": now_rfc3339(),
                        "conversation_id": conversation_id,
                        "request_id": request_id,
                        "data": {"status": "completed"},
                    },
                )
                return

            if is_council_command:
                persisted_council_events: list[dict[str, Any]] = []
                if conversation_uuid:
                    yield sse(
                        "conversation",
                        {
                            "type": "conversation",
                            "id": "evt_conversation",
                            "ts": now_rfc3339(),
                            "conversation_id": conversation_id,
                            "request_id": request_id,
                            "data": {"conversation_id": str(conversation_uuid)},
                        },
                    )

                async for frame in _account_frames(
                    app_state.db_pool,
                    auth.user_id,
                    lambda: stream_council(
                        user_message=user_message,
                        conversation_id=conversation_id,
                        request_id=request_id,
                    ),
                    operation="agent",
                    auto_route=True,
                    extended=True,
                ):
                    parsed_event = _extract_council_event_for_persistence(frame)
                    if parsed_event is not None:
                        persisted_council_events.append(parsed_event)
                    yield frame

                if store and conversation_uuid and user_id and persisted_council_events:
                    try:
                        await store.insert_message(
                            conversation_id=conversation_uuid,
                            user_id=user_id,
                            role="assistant",
                            content=_build_council_assistant_content(persisted_council_events),
                            model="council",
                            tool_results=[
                                {
                                    "name": "council_events",
                                    "result": {
                                        "events": persisted_council_events,
                                    },
                                    "request_id": request_id,
                                }
                            ],
                            metadata={
                                "request_id": request_id,
                                "council_events": persisted_council_events,
                            },
                            status="complete",
                        )
                    except Exception:
                        logger.warning(
                            "Failed to persist council command output",
                            exc_info=True,
                        )

                yield sse(
                    "done",
                    {
                        "type": "done",
                        "id": "evt_done",
                        "ts": now_rfc3339(),
                        "conversation_id": conversation_id,
                        "request_id": request_id,
                        "data": {"status": "completed"},
                    },
                )
                return

            trusted_spawn_context = _build_trusted_spawn_context(auth.user_id, payload.metadata)
            async for frame in _account_chat_frames(
                app_state.db_pool,
                auth.user_id,
                auto_route=model_decision.tier != "explicit",
                settings=settings,
                provider_config=provider_config,
                system_prompt=assembled_system_prompt,
                user_message=user_message,
                history_messages=history_messages,
                conversation_id=conversation_id,
                request_id=request_id,
                ping_interval_s=settings.sse_keepalive_interval_s,
                is_disconnected=is_disconnected,
                actual_model=actual_model,
                reported_model=selected_model if model_decision.tier == "explicit" else "auto",
                routing_info=routing_info,
                memory_store=store,
                user_id=user_id,
                conversation_uuid=conversation_uuid,
                queue=app_state.redis if app_state else None,
                db_pool=app_state.db_pool if app_state else None,
                trusted_spawn_context=trusted_spawn_context,
                disable_memory_write=bool(payload.disable_memory_write),
                user_timezone=user_timezone,
            ):
                yield frame
        except Exception as exc:
            if not isinstance(exc, ComputeUnavailable):
                logger.exception("Chat stream failed")
            ts = now_rfc3339()
            provider, model = effective_provider_and_model(settings, provider_config)
            model_for_events = selected_model or actual_model or model
            # Sanitize the SSE error payload — never emit `str(e)` to the
            # client (issue #79 round-1 finding). The request id is the
            # correlation handle; the full exception is logged server-side.
            logger.exception(
                "Native /chat streaming error (request_id=%s): %s",
                request_id,
                exc,
            )
            # Emit a minimal `final` + `error` + `done` sequence to keep the SSE contract stable.
            yield sse(
                "final",
                {
                    "type": "final",
                    "id": "evt_final",
                    "ts": ts,
                    "conversation_id": conversation_id,
                    "request_id": request_id,
                    "data": {
                        "message": {
                            "id": "msg_assistant_001",
                            "role": "assistant",
                            "content": "",
                            "content_type": "text/plain",
                        },
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        },
                        "model": model_for_events,
                        "provider": provider,
                        "finish_reason": "error",
                    },
                },
            )
            yield sse(
                "error",
                {
                    "type": "error",
                    "id": "evt_error",
                    "ts": ts,
                    "conversation_id": conversation_id,
                    "request_id": request_id,
                    "data": {
                        "code": "internal_error",
                        "message": _sse_error_message(request_id),
                        "retryable": False,
                    },
                },
            )
            yield sse(
                "done",
                {
                    "type": "done",
                    "id": "evt_done",
                    "ts": ts,
                    "conversation_id": conversation_id,
                    "request_id": request_id,
                    "data": {"ok": False},
                },
            )

    return StreamingResponse(
        stream_with_keepalives(generator(), settings.sse_keepalive_interval_s),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.include_router(conversations.router)
app.include_router(entitlements.router)
app.include_router(images.router)
app.include_router(memories.router)
app.include_router(skills.router)
app.include_router(system.router)
app.include_router(users.router)
app.include_router(video_credits.router)
app.include_router(auth_config_router)
app.include_router(auth_setup_router)


# Wrap the full ASGI stack with the outer security-headers middleware so
# that 500 responses generated by Starlette's outermost
# ``ServerErrorMiddleware`` for unhandled exceptions still carry the same
# HSTS / CSP / X-Frame-Options headers as normal responses. The inner
# ``SecurityHeadersMiddleware`` sits below Starlette's error handler and
# therefore does not see those responses. ``app.middleware_stack`` is
# None until the first request, so we build it eagerly here and wrap the
# resulting stack (which already has ``ServerErrorMiddleware`` on the
# outside) with our outer security-headers pass.
_built_stack = app.build_middleware_stack()
app.middleware_stack = _OuterSecurityHeadersMiddleware(_built_stack)

# Wrap again with the outer request-id middleware so that 500 responses
# generated by Starlette's outermost ServerErrorMiddleware still carry
# the X-Request-ID header. Without this outer wrap, an unhandled
# exception that bypasses the inner RequestIdMiddleware would emit a
# 500 without a request id, defeating the correlation handle that the
# global exception handler attaches to its sanitized body.
_id_built_stack = app.middleware_stack
app.middleware_stack = _OuterRequestIdMiddleware(_id_built_stack)

# Wrap once more with the outer CORS middleware so unhandled-500
# responses reach the browser at an allowed origin (round-1 Codex
# finding on PR #218). Starlette's ``ServerErrorMiddleware`` is
# outermost, so the inner ``CORSMiddleware`` does not see unhandled
# exceptions and the browser cannot read the sanitized body without
# the response carrying ``Access-Control-Allow-Origin``.
_cors_built_stack = app.middleware_stack
app.middleware_stack = _OuterCORSMiddleware(
    _cors_built_stack,
    allowed_origins=tuple(_cors_allowed),
)
