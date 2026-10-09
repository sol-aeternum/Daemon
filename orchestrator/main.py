from __future__ import annotations


import asyncio
from dataclasses import dataclass
import json
import logging
import re

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
    ArtifactOwnerError,
    resolve_owned_artifact,
)
from orchestrator.speech.cache import audio_filename, cached_audio, run_cache_io, store_audio
from orchestrator.speech.contracts import SpeechError, SpeechRequest, canonical_voice
from orchestrator.speech.service import get_speech_provider, synthesize as synthesize_speech
from orchestrator.services.identity.rate_limiter import RateLimitUnavailableError
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
from orchestrator.compute_runtime import (
    RETRYABLE_COMPUTE_CODES,
    ComputeUnavailable,
    account_compute,
    choose_route,
    compute_error,
)
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
from orchestrator.home_suggestions.contracts import SuggestionError, render_context
from orchestrator.home_suggestions.service import HomeSuggestions
from orchestrator.routes.home_suggestions import router as home_suggestions_router
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
from orchestrator.routes.speech_stream import router as speech_stream_router
from orchestrator.routes.web_snapshots import router as web_snapshots_router
from orchestrator.routes.tasks import (
    client_supports_durable_chat,
    client_supports_reset,
    observer_authorizer,
    router as tasks_router,
    task_store,
)
from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.tasks.inputs import RequestFingerprint, chat_request_fingerprint
from orchestrator.tasks.observe import observe_task
from orchestrator.tasks.store import ConversationBusy, IdempotencyConflict, TaskNotFound
from orchestrator.models_cache import fetch_openrouter_models
from orchestrator.model_router import (
    CLASSIFIER_VERSION,
    ModelDecision,
    matched_signals,
    select_model_tier,
)
from orchestrator import routing_log
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
    get_request_id,
)

logger = logging.getLogger(__name__)

CORS_ALLOW_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOW_HEADERS = (
    "Authorization",
    "Content-Type",
    "X-CSRF-Token",
    "X-Request-ID",
    # Durable chat: submission dedupe and the client's declared ability to
    # stop and reset a task (docs/DURABLE_REQUEST_DESIGN.md §6-§8).
    "Idempotency-Key",
    "X-Daemon-Client-Features",
)

# Headers the browser is allowed to read on a CORS response. Exposing
# ``X-Request-ID`` lets browser code correlate its errors with server-side
# logs; the value is server-generated (round-1 Codex finding on PR #218)
# so an attacker cannot pre-stage collisions.
CORS_EXPOSE_HEADERS = ("X-Request-ID", "X-Daemon-Task-Id")


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
    except UnsafeDatabaseCredentialError:
        logger.critical("Unsafe database credential configuration")
        raise
    except UnsafeProductionServerConfigError:
        logger.critical("Unsafe production server configuration")
        raise
    except PepperValidationError:
        logger.critical("Production pepper validation failed")
        raise
    except HostedIdentityConfigError:
        logger.critical("Hosted identity config validation failed")
        raise
    except InternalProxyConfigError:
        logger.critical("Internal proxy config validation failed")
        raise
    except EncryptionInitError:
        logger.critical("Encryption config validation failed")
        raise
    except HostSecurityConfigError:
        logger.critical("Host security config validation failed")
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
                logger.warning("Failed to backfill memory content hashes")
        asyncio.create_task(_backfill_skill_projections(state.db_pool))
        asyncio.create_task(_sync_repo_skills(state.db_pool))
        # Monitored inference routes are admitted from the ZDR attestation snapshot,
        # and this process runs its own ZDR checks so it enforces revocations itself.
        from orchestrator.entitlements import attestation

        await attestation.start(state.db_pool)
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
            logger.warning("Startup session cleanup failed")

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
    if state.db_pool is not None:
        from orchestrator.entitlements import attestation

        await attestation.stop()
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
        logger.warning("Skill projection backfill failed")


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
        logger.warning("Repo skill sync failed")


def _publish_setup_token(settings: Settings, token: str, *, recovery: bool = False) -> None:
    write_setup_token_file(settings.daemon_setup_token_file, token)
    if recovery:
        logger.info("Daemon recovery: all sessions expired; setup is required")
        return
    logger.info("Daemon setup required")


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
        logger.warning("First-boot setup check failed")


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

    A content-free failure marker is logged server-side; the
    response body carries only the generic message and the request id,
    plus an X-Request-ID header. Route handlers that raise FastAPI's
    ``HTTPException`` are not affected — FastAPI's default handler still
    emits the ``detail`` they supplied. This handler is the safety net
    for ``Exception`` and its non-HTTP subclasses (RuntimeError, ValueError,
    asyncpg.PostgresError, etc.).
    """

    request_id = get_request_id(request) or "-"
    logger.error("Unhandled exception")
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
        "/tts": 32768,
        "/tts/stream/v1": 32768,
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
    logger.critical("Host security config invalid; startup will abort in lifespan")
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
    scope_pool: Any,
    scope_user_id: uuid.UUID,
    *,
    auto_route: bool = False,
    profile: str = "routine",
    prepare_system_prompt: Callable[[], Awaitable[str]] | None = None,
    **kwargs: Any,
) -> AsyncIterator[str]:
    request_id = kwargs.get("request_id")

    async def source() -> AsyncIterator[str]:
        if prepare_system_prompt is not None:
            kwargs["system_prompt"] = await prepare_system_prompt()
        async for frame in stream_sse_chat(**kwargs):
            yield frame

    async for frame in _account_frames(
        scope_pool,
        scope_user_id,
        source,
        auto_route=auto_route,
        profile=profile,
        request_id=request_id if isinstance(request_id, str) else None,
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
    profile: str = "routine",
    request_id: str | None = None,
) -> AsyncIterator[str]:
    # Keep the ContextVar scope inside one producer task. The keepalive bridge
    # may resume its input generator in a different task for each frame.
    frames: asyncio.Queue[tuple[str | None, Exception | None]] = asyncio.Queue(maxsize=1)

    async def produce() -> None:
        # No await may follow a cancellation: once the consumer has gone, the
        # queue can stay full forever, and the consumer is awaiting this task.
        try:
            async with account_compute(
                scope_pool,
                scope_user_id,
                auto_route=auto_route,
                operation=operation,
                extended=extended,
                profile=profile,
                request_id=request_id,
            ):
                async for frame in source():
                    await frames.put((frame, None))
        except Exception as exc:
            await frames.put((None, exc))
            return
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


def _qualified_profile_available(profile: str) -> bool:
    """Whether ``profile`` has a qualified route in this deployment right now.

    ``choose_route`` is the cheap, local qualification check: it reads the
    validated routing metadata and the inference policy and nothing else. It
    reserves no account budget and calls no provider, so it is safe to run
    before admission. The route it names is deliberately discarded — the
    profile's cheapest candidate is not a pin, and dispatch still picks the
    route this account, request and budget may actually use.
    """
    try:
        choose_route(profile=profile)
    except ComputeUnavailable:
        return False
    return True


def _approved_chat_model(model: str | None = None, *, profile: str = "routine") -> str:
    try:
        if model and model != "auto":
            # An explicit selection is admitted against its own exact route, and
            # is not a claim about what the automatic profile can serve.
            return choose_route(model).model
        # Admission qualifies the exact profile this request will dispatch
        # under. An approved route that no group of that profile may use is not
        # a servable answer, so it must not buy a 200 here. Account eligibility,
        # per-request capabilities, output bounds and budget stay at dispatch.
        if not _qualified_profile_available(profile):
            raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
        return "auto"
    except ComputeUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


_LOGGABLE_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,119}")


#: User-visible routing reason codes (a fixed vocabulary; O6). Fallback codes are
#: appended at runtime as ``fallback_<cause>``.
ROUTING_REASON_CODES = frozenset(
    {
        "explicit",
        "council",
        "complexity_signal",
        "research_signal",
        "default",
        "fallback_capability_unavailable",
        "fallback_budget_exceeded",
        "budget_fitted_output",
    }
)


def _routing_reason_codes(decision: ModelDecision, profile: str) -> list[str]:
    if decision.tier == "explicit":
        return ["explicit"]
    if profile == "council":
        return ["council"]
    if profile == "reasoning":
        return ["complexity_signal"]
    if profile == "research":
        return ["research_signal"]
    return ["default"]


@dataclass(frozen=True)
class _CompatControls:
    """What the OpenAI-compatible request asks for beyond its last message (O4)."""

    history: list[dict[str, Any]] | None
    max_output_tokens: int | None
    call_overrides: dict[str, Any] | None


_COMPAT_SAMPLING_FIELDS = ("temperature", "top_p", "presence_penalty", "frequency_penalty", "stop")


def _compat_request_controls(payload: OpenAIChatRequest, last_message: str) -> _CompatControls:
    """Honour the compatibility request's conversation, output cap and controls.

    - ``n`` other than 1 and a non-positive ``max_tokens`` are refused, not ignored.
    - Prior user/assistant turns up to the latest user message become history; other
      roles (system prompts are handled separately, tool results need tool calls
      this endpoint does not run) are not replayed.
    - ``max_tokens`` caps the answer.
    - Sampling controls and ``stop`` are forwarded only when explicitly set *and* an
      explicit model is requested: automatic routing chooses models that may not
      accept them, and the request model's defaults are not caller intent.
    """
    if payload.n is not None and payload.n != 1:
        raise HTTPException(
            status_code=400,
            detail={"code": "unsupported_parameter", "message": "Only n=1 is supported"},
        )
    if payload.max_tokens is not None and payload.max_tokens < 1:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_parameter", "message": "max_tokens must be positive"},
        )
    last_user = max(
        index for index, message in enumerate(payload.messages) if message.role == "user"
    )
    turns = [
        {"role": message.role, "content": _extract_text_content(message.content)}
        for message in payload.messages[:last_user]
        if message.role in {"user", "assistant"} and _extract_text_content(message.content)
    ]
    history = [*turns, {"role": "user", "content": last_message}] if turns else None
    overrides: dict[str, Any] = {}
    if payload.model not in {"default", "", "kimi", "auto"}:
        for name in _COMPAT_SAMPLING_FIELDS:
            value = getattr(payload, name)
            if name in payload.model_fields_set and value is not None:
                overrides[name] = value
    return _CompatControls(
        history=history,
        max_output_tokens=payload.max_tokens,
        call_overrides=overrides or None,
    )


def _admit_and_log_decision(
    model: str | None,
    *,
    profile: str,
    decision: ModelDecision,
    requested_text: str,
    endpoint: str,
    request_id: str,
    attachment_count: int = 0,
) -> str:
    """Admit exactly as :func:`_approved_chat_model` and record the routing decision.

    The record goes to the server log only (see :mod:`orchestrator.routing_log`); it
    never changes admission and carries no message content.
    """
    explicit = decision.tier == "explicit"
    complexity, research = ([], []) if explicit else matched_signals(requested_text)

    def record(admission: str) -> None:
        routing_log.emit(
            "decision",
            request_id=request_id,
            endpoint=endpoint,
            auto=not explicit,
            # This pattern is only an early shape check; routing_log additionally
            # requires a trusted configuration identity before emitting it.
            explicit_model=(
                decision.model
                if explicit and _LOGGABLE_MODEL_ID.fullmatch(decision.model)
                else ("unrecognized" if explicit else None)
            ),
            profile=profile,
            tier=decision.tier,
            classifier_version=CLASSIFIER_VERSION,
            complexity_signals=complexity,
            research_signals=research,
            empty_text=not requested_text.strip(),
            attachment_count=attachment_count,
            admission=admission,
        )

    try:
        admitted = _approved_chat_model(model, profile=profile)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code")
        record(code if isinstance(code, str) else "denied")
        raise
    record("admitted")
    return admitted


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


async def _persist_council_output(
    *,
    store: Any,
    conversation_uuid: uuid.UUID | None,
    user_id: uuid.UUID | None,
    request_id: str,
    council_events: list[dict[str, Any]],
    failed: bool,
    log_message: str,
) -> None:
    """Record collected council events, retaining an error state on abort."""
    if not (store and conversation_uuid and user_id and council_events):
        return
    try:
        await store.insert_message(
            conversation_id=conversation_uuid,
            user_id=user_id,
            role="assistant",
            content=_build_council_assistant_content(council_events),
            model="council",
            tool_results=[
                {
                    "name": "council_events",
                    "result": {"events": council_events},
                    "request_id": request_id,
                }
            ],
            metadata={"request_id": request_id, "council_events": council_events},
            status="error" if failed else "complete",
        )
    except Exception:
        logger.warning("Council event persistence failed")


async def _stream_council_events(
    *,
    db_pool: Any,
    account_user_id: uuid.UUID,
    source: Callable[[], AsyncIterator[str]],
    store: Any,
    conversation_uuid: uuid.UUID | None,
    user_id: uuid.UUID | None,
    conversation_id: str,
    request_id: str,
    log_message: str,
) -> AsyncIterator[str]:
    """Persist council progress on success, failure, or stream interruption."""
    council_events: list[dict[str, Any]] = []
    council_failed = False
    completed = False
    try:
        async for frame in _account_frames(
            db_pool,
            account_user_id,
            source,
            operation="agent",
            auto_route=True,
            extended=True,
            profile="council",
        ):
            parsed_event = _extract_council_event_for_persistence(frame)
            if parsed_event is not None:
                council_events.append(parsed_event)
                council_failed |= parsed_event.get("type") == "council_error"
            yield frame
        completed = True
    finally:
        await asyncio.shield(
            _persist_council_output(
                store=store,
                conversation_uuid=conversation_uuid,
                user_id=user_id,
                request_id=request_id,
                council_events=council_events,
                failed=council_failed or not completed,
                log_message=log_message,
            )
        )

    yield sse(
        "done",
        {
            "type": "done",
            "id": "evt_done",
            "ts": now_rfc3339(),
            "conversation_id": conversation_id,
            "request_id": request_id,
            "data": {"status": "error" if council_failed else "completed"},
        },
    )


# ============== Health & Info Endpoints ==============


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    base: dict[str, Any] = {"status": "ok"}
    try:
        state = get_app_state(request)
        base["services"] = await check_db_health(state)
    except Exception:
        logger.warning("Health check failed")
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

    except Exception:
        logger.warning("Failed to fetch OpenRouter models")
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

    # Classify what the user wrote; the default below is model-facing text only.
    requested_text = _extract_text_content(user_messages[-1].content)
    last_message = requested_text or "Please help with the attached input."
    compat_controls = _compat_request_controls(payload, last_message)
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
    # Classify before admission so the automatic choice is qualified against the
    # exact workload profile this request will dispatch under, not against any
    # approved route at all. This is the same decision native /chat makes: an
    # explicit model is exact and runs under the routine scope, whatever the
    # wording, so it receives that model's default preset on both endpoints.
    auto_requested = payload.model in {"default", "", "kimi", "auto"}
    workload_decision = select_model_tier(
        requested_text, user_override=None if auto_requested else payload.model
    )
    workload = workload_decision.profile
    actual_model = _admit_and_log_decision(
        actual_model if actual_model not in {"default", "", "kimi", "auto"} else None,
        profile=workload,
        decision=workload_decision,
        requested_text=requested_text,
        endpoint="openai",
        request_id=request_id,
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
        logger.warning("User timezone unavailable; using deployment default")
    try:
        app_state = request.app.state.app_state
        db_pool = getattr(app_state, "db_pool", None)
        skills_block = await build_skill_index(db_pool=db_pool)
    except Exception:
        logger.warning("Skills injection failed, continuing without skills")
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
                    auto_route=auto_requested,
                    profile=workload,
                    settings=settings,
                    provider_config=provider_config,
                    system_prompt=system_prompt,
                    user_message=last_message,
                    history_messages=compat_controls.history,
                    max_output_tokens=compat_controls.max_output_tokens,
                    call_overrides=compat_controls.call_overrides,
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
                capacity = compute_error(e)
                if capacity is None:
                    logger.error("Streaming chat completion error")

                error_chunk = OpenAIChatStreamChunk(
                    id=f"chatcmpl-{new_request_id()}",
                    created=int(time.time()),
                    model=payload.model,
                    choices=[
                        OpenAIChoice(
                            index=0,
                            delta=OpenAIDeltaMessage(
                                content=(
                                    f"{capacity.message} (code: {capacity.code})"
                                    if capacity
                                    else _sse_error_message(request_id_local)
                                ),
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
                auto_route=auto_requested,
                profile=workload,
                settings=settings,
                provider_config=provider_config,
                system_prompt=system_prompt,
                user_message=last_message,
                history_messages=compat_controls.history,
                max_output_tokens=compat_controls.max_output_tokens,
                call_overrides=compat_controls.call_overrides,
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

        except Exception as exc:
            capacity = compute_error(exc)
            if capacity is not None:
                raise HTTPException(
                    status_code=503, detail={"code": capacity.code, "message": capacity.message}
                ) from exc
            logger.error("OpenAI-compatible chat completion failed")
            raise HTTPException(status_code=500, detail=_GENERIC_INTERNAL_ERROR) from exc


# ============== Generated Images Static Serving ==============

GENERATED_IMAGES_DIR = Path(__file__).resolve().parent.parent / "data" / "generated_images"
GENERATED_AUDIO_DIR = Path(__file__).resolve().parent.parent / "data" / "generated_audio"
GENERATED_FILES_DIR = Path(__file__).resolve().parent.parent / "data" / "generated_files"
TTS_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "tts_cache"
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# Generated artifacts belong to one account and are checked on every read:
# no browser or shared cache may keep them (filenames can repeat across
# owners, so a cached copy could be served after an account switch).
PRIVATE_ARTIFACT_HEADERS = {"Cache-Control": "private, no-store"}


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
    return FileResponse(filepath, media_type=media_type, headers=PRIVATE_ARTIFACT_HEADERS)


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
        filepath = await run_cache_io(
            cached_audio, TTS_CACHE_DIR / "self-hosted", auth.user_id, filename
        )
    if filepath is None:
        filepath = _resolve_safe_file_path(GENERATED_AUDIO_DIR, filename, auth.user_id)
    if filepath is None:
        raise HTTPException(status_code=404, detail="Audio not found")

    media_type = "audio/mpeg"
    if filename.endswith(".wav"):
        media_type = "audio/wav"
    elif filename.endswith((".ogg", ".opus")):
        media_type = "audio/ogg"
    return FileResponse(filepath, media_type=media_type, headers=PRIVATE_ARTIFACT_HEADERS)


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

    return FileResponse(filepath, media_type=media_type, headers=PRIVATE_ARTIFACT_HEADERS)


@app.post("/tts")
async def text_to_speech(
    payload: TtsRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    try:
        speech = SpeechRequest(
            payload.text.strip(),
            canonical_voice(payload.voice),
            payload.speed if payload.speed is not None else 1,
            payload.format or "mp3",
        )
        # Fail closed for speech in ALL deployments. Compose already includes Redis.
        limiter = get_rate_limiter(request)
        if not limiter.is_redis_available:
            raise SpeechError("speech_admission_unavailable")
        try:
            decision = await limiter.check(
                "speech:tts", "user_id", str(auth.user_id), RateLimitPolicy(12, 60)
            )
        except RateLimitUnavailableError as exc:
            raise SpeechError("speech_admission_unavailable") from exc
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail={"code": "speech_rate_limited"},
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
        provider = get_speech_provider(settings)
        filename = audio_filename(provider.name, provider.model, speech)
        root = TTS_CACHE_DIR / "self-hosted"
        cached = (
            payload.cache is not False
            and await run_cache_io(cached_audio, root, auth.user_id, filename) is not None
        )
        if not cached:

            async def work():
                audio = await synthesize_speech(provider, speech, settings.tts_timeout_seconds)
                try:
                    await run_cache_io(store_audio, root, auth.user_id, filename, audio.content)
                except (ArtifactOwnerError, OSError) as exc:
                    raise SpeechError("speech_storage_unavailable") from exc

            task = asyncio.create_task(work())
            try:
                while not task.done():
                    if await request.is_disconnected():
                        raise asyncio.CancelledError
                    await asyncio.wait({task}, timeout=0.1)
                await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        else:
            logger.info(
                "speech_cache_hit characters=%d",
                len(speech.text),
            )
        return {
            "audio_path": f"/generated-audio/{filename}",
            "cached": cached,
            "model": provider.model,
            "voice": speech.voice,
            "format": speech.format,
        }
    except SpeechError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": "Speech request could not be completed"},
        ) from exc


@app.get("/tts/health")
async def speech_health(
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    provider = get_speech_provider(settings)
    ready = await provider.health()
    if not ready:
        raise HTTPException(status_code=503, detail={"code": "speech_not_ready"})
    return {"ready": ready, "provider": provider.name, "model": provider.model}


@app.get("/audio/token")
async def get_audio_token(
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    """Retired vendor-token API; speech now executes exclusively through /tts."""

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


IDEMPOTENCY_HEADER = "Idempotency-Key"
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._:-]{1,128}")


async def _durable_chat(
    *,
    payload: ChatRequest,
    request: Request,
    settings: Settings,
    app_state: AppState,
    auth: AuthenticatedDevice,
    request_id: str,
    user_message: str,
    prepared_user_content: str | list[dict[str, Any]],
    pipeline: str,
    model_decision: ModelDecision,
    selected_model: str,
    actual_model: str,
    routing_info: dict[str, Any],
) -> StreamingResponse | None:
    """Accept a native chat turn durably and observe it (DURABLE_REQUEST_DESIGN §7).

    Returns ``None`` when the turn must stay on the request-bound path (a
    conversation bound to a contextual-home suggestion). Acceptance fails
    closed: without a durable record nothing is executed.
    """
    store = task_store(app_state)
    memory = app_state.memory_store
    assert memory is not None  # task_store() refuses without it
    idempotency_key = request.headers.get(IDEMPOTENCY_HEADER)
    if idempotency_key is not None and not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_idempotency_key", "message": "Invalid key"}
        )

    conversation_uuid: uuid.UUID | None = None
    needs_title = payload.conversation_id is None
    if payload.conversation_id:
        try:
            conversation_uuid = uuid.UUID(payload.conversation_id.replace("conv_", ""))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        existing = await memory.get_conversation(conversation_uuid)
        if not existing or existing.get("user_id") != auth.user_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        existing_metadata = existing.get("metadata")
        if isinstance(existing_metadata, str):
            existing_metadata = json.loads(existing_metadata)
        if isinstance(existing_metadata, dict) and existing_metadata.get("home_suggestion") == 1:
            return None
        needs_title = (
            not existing.get("title_locked")
            and existing.get("title") in (None, "", "New conversation")
            and await memory.count_messages(conversation_uuid) == 0
        )

    explicit = model_decision.tier == "explicit"
    task_input: dict[str, Any] = {
        "message": user_message,
        "prepared_content": (
            prepared_user_content if prepared_user_content != user_message else None
        ),
        "auto_route": not explicit,
        "profile": model_decision.profile,
        "actual_model": actual_model,
        "reported_model": selected_model if explicit else "auto",
        "routing_info": routing_info,
        "provider": payload.provider,
        "disable_memory_write": bool(payload.disable_memory_write),
        "trusted_spawn_context": _build_trusted_spawn_context(auth.user_id, payload.metadata),
        "request_id": request_id,
    }
    fingerprint = _durable_request_fingerprint(payload, settings, conversation_uuid, user_message)
    try:
        accepted = await store.accept(
            user_id=auth.user_id,
            conversation_id=conversation_uuid,
            new_conversation_title=(
                user_message[:50] + "..." if len(user_message) > 50 else user_message
            ),
            pipeline=pipeline,
            user_message=user_message,
            task_input=task_input,
            request_hash=fingerprint.digest,
            request_canonical=fingerprint.canonical,
            idempotency_key=idempotency_key,
            assistant_model=actual_model if explicit else None,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "message": "This request key was already used for a different request",
            },
        ) from exc
    except ConversationBusy as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conversation_busy",
                "message": "This conversation is still working on an earlier request",
                "task_id": str(exc.active_task_id),
            },
        ) from exc
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except Exception as exc:
        logger.error("Durable chat acceptance failed")
        raise HTTPException(
            status_code=503,
            detail={"code": "task_unavailable", "message": "Request could not be saved; retry"},
        ) from exc

    if accepted.created and app_state.redis is not None:
        # Latency only: the dispatch sweep recovers a lost or failed wake-up.
        try:
            wake_seq = await store.mark_woken(accepted.task_id)
            await app_state.redis.enqueue_job(
                "run_chat_task",
                str(accepted.task_id),
                _job_id=f"task:{accepted.task_id}:{wake_seq}",
            )
        except Exception:
            logger.warning("Task wake-up enqueue failed; the sweep will recover it")
        if needs_title:
            try:
                await app_state.redis.enqueue_job(
                    "generate_title",
                    str(accepted.conversation_id),
                    user_message,
                    _job_id=f"title:{accepted.conversation_id}",
                    _defer_by=0,
                )
            except Exception:
                logger.warning("Failed to enqueue title generation")

    return _observe_task_response(
        store, app_state, auth, accepted.task_id, request_id, settings, request
    )


def _observe_task_response(
    store: Any,
    app_state: AppState,
    auth: AuthenticatedDevice,
    task_id: uuid.UUID,
    request_id: str,
    settings: Settings,
    request: Request,
) -> StreamingResponse:
    frames = observe_task(
        store,
        app_state.redis,
        auth.user_id,
        task_id,
        request_id=request_id,
        authorized=observer_authorizer(app_state, auth),
        supports_reset=client_supports_reset(request),
    )
    return StreamingResponse(
        stream_with_keepalives(frames, settings.sse_keepalive_interval_s),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Daemon-Task-Id": str(task_id),
        },
    )


def _requested_user_message(payload: ChatRequest) -> str:
    """The turn's text exactly as /chat derives it for a non-suggestion request."""
    last_user_message = None
    for msg in reversed(payload.messages or []):
        if msg.get("role") == "user":
            last_user_message = _extract_text_content(msg.get("content"))
            break
    user_message = (last_user_message or payload.message).strip()
    if not user_message and payload.attachments:
        user_message = "Please analyze the attached files."
    return user_message


def _user_model_choice(payload: ChatRequest, last_user_msg: dict[str, Any] | None) -> str:
    """The model the user asked for: the request's, or the latest user message's override."""
    choice = payload.model or "auto"
    if choice == "auto" and last_user_msg:
        msg_model = last_user_msg.get("model")
        if isinstance(msg_model, str):
            msg_model = msg_model.strip()
            if msg_model and msg_model != "auto":
                choice = msg_model
    return choice


def _durable_request_fingerprint(
    payload: ChatRequest,
    settings: Settings,
    conversation_uuid: uuid.UUID | None,
    user_message: str,
) -> RequestFingerprint:
    last_user_msg = next(
        (msg for msg in reversed(payload.messages or []) if msg.get("role") == "user"), None
    )
    content = last_user_msg.get("content") if last_user_msg else None
    return chat_request_fingerprint(
        key=validate_and_get_pepper(settings),
        conversation_id=conversation_uuid,
        message=user_message,
        attachments=payload.attachments,
        # What will actually run: a per-message override changes the model.
        model=_user_model_choice(payload, last_user_msg),
        provider=payload.provider,
        metadata=payload.metadata,
        disable_memory_write=bool(payload.disable_memory_write),
        content_parts=(
            [part for part in content if isinstance(part, dict)]
            if isinstance(content, list)
            else None
        ),
    )


async def _refuse_if_task_active(
    payload: ChatRequest, app_state: AppState, auth: AuthenticatedDevice
) -> None:
    """Raise 409 conversation_busy when a durable task is active in the conversation."""
    if not payload.conversation_id or app_state.db_pool is None or app_state.memory_store is None:
        return
    try:
        conversation_uuid = uuid.UUID(payload.conversation_id.replace("conv_", ""))
    except ValueError:
        return
    try:
        active = await task_store(app_state).active_for_conversation(
            auth.user_id, conversation_uuid
        )
    except Exception:
        # The database is unreachable: no worker can be advancing a task
        # either, and request-bound chat keeps its own degradation path.
        logger.warning("Active-task check failed; continuing")
        return
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conversation_busy",
                "message": "This conversation is still working on an earlier request",
                "task_id": str(active.task_id),
            },
        )


async def _durable_replay(
    payload: ChatRequest,
    request: Request,
    settings: Settings,
    app_state: AppState,
    auth: AuthenticatedDevice,
) -> StreamingResponse | None:
    """Resolve a same-key replay before any new-turn admission (§6).

    A retry after a lost response must attach to the task the first request
    created, not be charged as a new turn or refused as rate-limited or busy.
    Per-IP transport throttling still applies. Returns ``None`` when there is
    nothing to replay.
    """
    key = request.headers.get(IDEMPOTENCY_HEADER)
    if (
        key is None
        or not _IDEMPOTENCY_KEY.fullmatch(key)
        or payload.suggestion_id is not None
        or app_state.db_pool is None
        or app_state.memory_store is None
    ):
        return None
    conversation_uuid: uuid.UUID | None = None
    if payload.conversation_id:
        try:
            conversation_uuid = uuid.UUID(payload.conversation_id.replace("conv_", ""))
        except ValueError:
            return None
    store = task_store(app_state)
    fingerprint = _durable_request_fingerprint(
        payload, settings, conversation_uuid, _requested_user_message(payload)
    )
    try:
        existing = await store.find_by_key(
            auth.user_id, key, fingerprint.digest, fingerprint.canonical
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "message": "This request key was already used for a different request",
            },
        ) from exc
    except Exception as exc:
        # Fail closed: this key may belong to accepted durable work (which
        # the worker runs whatever the flag says), so running the request
        # again request-bound could repeat it. The client retries.
        logger.warning("Task replay lookup failed (key present)")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "replay_check_unavailable",
                "message": "Could not check for an earlier copy of this request. Try again.",
                "retryable": True,
            },
        ) from exc
    if existing is None:
        return None
    request_id = get_request_id(request) or new_request_id()
    return _observe_task_response(
        store, app_state, auth, existing.task_id, request_id, settings, request
    )


@app.post("/chat", responses=REQUEST_BODY_TOO_LARGE_RESPONSES)
async def chat(
    payload: ChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> StreamingResponse:
    durable_client = settings.durable_chat_enabled and client_supports_durable_chat(request)
    # Replays come first even when durable chat is switched off or this client
    # cannot drive it: a key accepted before a rollback must reattach to its
    # task (which the worker still runs), never run again request-bound.
    replay = await _durable_replay(payload, request, settings, app_state, auth)
    if replay is not None:
        return replay
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
    suggestion_context: dict[str, Any] | None = None
    suggestion_destination: uuid.UUID | None = None
    if payload.suggestion_id is not None:
        # No client history, destination, attachment or metadata may replace or
        # augment a server-owned candidate. Ordinary explicit model/provider
        # selection still passes the existing chat/account admission paths.
        if (
            payload.conversation_id is not None
            or incoming_messages
            or attachments
            or payload.metadata
            or payload.user_id
        ):
            raise HTTPException(
                status_code=422, detail="Suggestion requests require an isolated new chat"
            )
        try:
            suggestion_destination, suggestion_context = await HomeSuggestions(
                app_state.memory_store, app_state.redis, auth.user_id
            ).accept(payload.suggestion_id, payload.message)
            conversation_id = f"conv_{suggestion_destination}"
        except SuggestionError as exc:
            raise HTTPException(
                status_code=exc.status,
                detail={"code": exc.code, "message": "Suggestion unavailable; refresh home"},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "suggestion_unavailable",
                    "message": "Suggestion could not be saved",
                },
            ) from exc
    last_user_message = None
    last_user_msg: dict[str, Any] | None = None
    for msg in reversed(incoming_messages):
        if msg.get("role") == "user":
            last_user_msg = msg
            last_user_message = _extract_text_content(msg.get("content"))
            break
    # Classify what the user wrote; the upload default is model-facing text only.
    requested_text = (last_user_message or payload.message).strip()
    user_message = payload.message if suggestion_context is not None else requested_text
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
    if suggestion_context is not None:
        prepared_user_content = render_context(user_message, suggestion_context)
    if attachments:
        prepared_user_content = _build_user_content_from_attachments(user_message, attachments)
    elif last_user_msg and isinstance(last_user_msg.get("content"), list):
        prepared_user_content = [
            part for part in last_user_msg.get("content", []) if isinstance(part, dict)
        ]

    decision = route_message(user_message, payload.metadata)

    user_model_choice = _user_model_choice(payload, last_user_msg)
    turn_count = len(incoming_messages) if incoming_messages else 0

    model_decision = select_model_tier(
        message=requested_text,
        turn_count=turn_count,
        user_override=user_model_choice,
    )

    # A council run streams under the council profile, not under the profile the
    # command's wording would classify to, so admit it against the profile it
    # will actually dispatch under.
    admission_profile = (
        "council" if is_council_config_response or is_council_command else model_decision.profile
    )

    selected_model = _admit_and_log_decision(
        model_decision.model if model_decision.tier == "explicit" else None,
        profile=admission_profile,
        decision=model_decision,
        requested_text=requested_text,
        endpoint="chat",
        request_id=request_id,
        attachment_count=len(attachments),
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
        # Additive, user-visible routing reasons (optional work O6).
        "profile": admission_profile,
        "reason_codes": _routing_reason_codes(model_decision, admission_profile),
    }

    has_image_input = _content_has_image(prepared_user_content)
    if has_image_input:
        raise HTTPException(
            status_code=403,
            detail={"code": "modality_unavailable", "message": "Multimodal compute unavailable"},
        )

    if (
        durable_client
        and payload.suggestion_id is None
        and not is_council_command
        and not is_council_config_response
    ):
        durable = await _durable_chat(
            payload=payload,
            request=request,
            settings=settings,
            app_state=app_state,
            auth=auth,
            request_id=request_id,
            user_message=user_message,
            prepared_user_content=prepared_user_content,
            pipeline=decision.pipeline,
            model_decision=model_decision,
            selected_model=selected_model,
            actual_model=actual_model,
            routing_info=routing_info,
        )
        if durable is not None:
            return durable

    # One active task per conversation applies to every new turn (§6): a
    # request-bound turn (a client without the durable capabilities, council,
    # or any turn while the flag is off) must not run beside a durable task
    # that still owns this conversation.
    await _refuse_if_task_active(payload, app_state, auth)

    # Initialize persistence with graceful degradation
    store = app_state.memory_store if app_state else None
    user_id = auth.user_id if store else None
    conversation_uuid = None
    conversation_exists = False
    # Pre-created UI drafts (frontend creates the conversation before the first
    # message) arrive as existing conversations, so title scheduling is decided
    # from the draft's own state instead of from whether this request created
    # it (issue #362).
    existing_draft_needs_title = False
    home_bound_conversation = suggestion_destination is not None

    # Create or get conversation if persistence is available
    if suggestion_destination is not None:
        conversation_uuid = suggestion_destination
        conversation_exists = True
    elif store and user_id:
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

                existing_metadata = existing.get("metadata")
                if isinstance(existing_metadata, str):
                    existing_metadata = json.loads(existing_metadata)
                home_bound_conversation = (
                    isinstance(existing_metadata, dict)
                    and existing_metadata.get("home_suggestion") == 1
                )

                conversation_uuid = conv_uuid
                conversation_exists = True

                # Generate a title when an unlocked, still-empty draft first
                # receives content. Later turns must not requeue paid title
                # jobs (no retrospective backfill), and locked (manually set)
                # titles are preserved.
                if (
                    app_state.redis is not None
                    and not existing.get("title_locked")
                    and existing.get("title") in (None, "", "New conversation")
                ):
                    try:
                        prior_message_count = await store.count_messages(conv_uuid)
                    except Exception:
                        logger.warning("Skipping title scheduling, could not read draft state")
                    else:
                        existing_draft_needs_title = prior_message_count == 0
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

                if app_state.redis is not None and (
                    not conversation_exists or existing_draft_needs_title
                ):
                    try:
                        await app_state.redis.enqueue_job(
                            "generate_title",
                            str(conversation_uuid),
                            user_message,
                            _job_id=f"title:{conversation_uuid}",
                            _defer_by=0,
                        )
                    except Exception:
                        logger.warning("Failed to enqueue title generation")
        except HTTPException:
            raise
        except Exception as e:
            if home_bound_conversation:
                raise HTTPException(
                    status_code=503, detail="Bound conversation could not be saved"
                ) from e
            logger.warning(
                "Conversation persistence failed, continuing without persistence"
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
        metadata = msg.get("metadata")
        if isinstance(metadata, dict) and metadata.get("home_suggestion") is not None:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), str):
                raise ValueError("Invalid bound home turn")
            mapped["content"] = render_context(msg["content"], metadata["home_suggestion"])
        if msg.get("reasoning_text"):
            mapped["reasoning"] = msg.get("reasoning_text")
        if msg.get("reasoning_duration_secs") is not None:
            mapped["reasoning_duration_secs"] = msg.get("reasoning_duration_secs")
        if msg.get("reasoning_model"):
            mapped["reasoning_model"] = msg.get("reasoning_model")
        return mapped

    bound_home_turn: dict[str, Any] | None = None
    if home_bound_conversation and conversation_exists and store and conversation_uuid and user_id:
        binding_reader = getattr(store, "get_home_suggestion_turn", None)
        if binding_reader is not None:
            try:
                bound_home_turn = await binding_reader(conversation_uuid, user_id)
                if not isinstance(bound_home_turn, dict):
                    raise ValueError("Missing bound home turn")
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail="Bound source context unavailable"
                ) from exc
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
            # Keep the copied source turn after Redis expiry and after normal
            # recent-history truncation. Persisted prompt remains distinct from
            # its context on public history reads.
            if bound_home_turn is not None and not any(
                msg.get("id") == bound_home_turn.get("id") for msg in db_messages
            ):
                history_messages.insert(0, _to_history_message(bound_home_turn))
        except Exception as exc:
            if suggestion_context is not None or bound_home_turn is not None:
                raise HTTPException(
                    status_code=503, detail="Bound source context unavailable"
                ) from exc
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
    if suggestion_context is not None and not history_messages:
        history_messages = [{"role": "user", "content": prepared_user_content}]

    council_user_message = user_message
    if bound_home_turn is not None:
        bound_context = bound_home_turn["metadata"]["home_suggestion"]
        council_user_message = render_context(user_message, bound_context)
        if council_config_response is not None:
            council_config_response = {
                **council_config_response,
                "_prompt": render_context(
                    council_config_response.get("_prompt") or bound_home_turn["content"],
                    bound_context,
                ),
            }

    assembled_system_prompt = DAEMON_SYSTEM_PROMPT
    preferences_block = ""
    user_timezone = None
    try:
        db_pool = getattr(app_state, "db_pool", None)
        skills_block = await build_skill_index(db_pool=db_pool)
    except Exception:
        logger.warning("Skills injection failed, continuing without skills")
        skills_block = ""
    if store and user_id and conversation_uuid:
        try:
            from orchestrator.memory.injection import (
                format_preferences_block,
            )

            user_settings = await store.get_user_settings(user_id)
            user_timezone = extract_timezone_name(user_settings)
            preferences_block = format_preferences_block(user_settings)
        except Exception:
            logger.warning("Memory injection failed, using base prompt")

    if skills_block and skills_block not in assembled_system_prompt:
        assembled_system_prompt = f"{assembled_system_prompt.rstrip()}\n\n{skills_block}"

    async def prepare_native_system_prompt() -> str:
        # Invoked in the account producer task, before model dispatch. Query
        # embeddings share this turn's admission and reservation cleanup.
        prompt = assembled_system_prompt
        if store and user_id and conversation_uuid:
            from orchestrator.memory.embedding import raise_if_embedding_accounting_error
            from orchestrator.memory.injection import assemble_system_prompt, build_memory_context

            try:
                context = await build_memory_context(store, conversation_uuid)
                prompt = await assemble_system_prompt(
                    memory_context=context,
                    preferences_block=preferences_block,
                    conversation_id=conversation_uuid,
                )
            except Exception as error:
                raise_if_embedding_accounting_error(error)
                logger.warning("Memory injection failed, using base prompt")
        if skills_block and skills_block not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{skills_block}"
        return prompt

    async def is_disconnected() -> bool:
        return await request.is_disconnected()

    async def generator():
        try:
            if is_council_config_response:
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

                async for frame in _stream_council_events(
                    db_pool=app_state.db_pool,
                    account_user_id=auth.user_id,
                    source=lambda: stream_council_interview_response(
                        user_message=user_message,
                        conversation_id=conversation_id,
                        request_id=request_id,
                        stored_config=council_config_response,
                    ),
                    store=store,
                    conversation_uuid=conversation_uuid,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    log_message="Failed to persist council interview response output",
                ):
                    yield frame
                return

            if is_council_command:
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

                async for frame in _stream_council_events(
                    db_pool=app_state.db_pool,
                    account_user_id=auth.user_id,
                    source=lambda: stream_council(
                        user_message=council_user_message,
                        conversation_id=conversation_id,
                        request_id=request_id,
                    ),
                    store=store,
                    conversation_uuid=conversation_uuid,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    log_message="Failed to persist council command output",
                ):
                    yield frame
                return

            trusted_spawn_context = _build_trusted_spawn_context(auth.user_id, payload.metadata)
            async for frame in _account_chat_frames(
                app_state.db_pool,
                auth.user_id,
                auto_route=model_decision.tier != "explicit",
                profile=model_decision.profile,
                settings=settings,
                provider_config=provider_config,
                system_prompt=assembled_system_prompt,
                prepare_system_prompt=prepare_native_system_prompt,
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
                # Source-change event only. Reading home never dispatches work.
                # Failed/cancelled turns do not qualify; DB locality and opt-in
                # are checked again by admission and by the queued worker.
                if store and app_state.redis is not None and frame.startswith("event: done"):
                    try:
                        data_line = next(
                            line[6:] for line in frame.splitlines() if line.startswith("data: ")
                        )
                        completed = json.loads(data_line).get("data", {}).get("ok") is True
                        if completed:
                            await HomeSuggestions(store, app_state.redis, auth.user_id).refresh(
                                manual=False
                            )
                    except Exception:
                        logger.info("Contextual home source-change admission unavailable")
        except Exception as exc:
            capacity = compute_error(exc)
            ts = now_rfc3339()
            provider, model = effective_provider_and_model(settings, provider_config)
            model_for_events = selected_model or actual_model or model
            # Sanitize the SSE error payload — never emit `str(e)` to the
            # client (issue #79 round-1 finding). The request id is the
            # correlation handle; logs contain only a content-free failure marker.
            # Capacity errors carry their own sanitized code and message.
            if capacity is None:
                logger.error("Native /chat streaming error")
            else:
                logger.info("Native /chat capacity refusal")
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
                        "code": capacity.code if capacity else "internal_error",
                        "message": capacity.message if capacity else _sse_error_message(request_id),
                        "retryable": bool(capacity and capacity.code in RETRYABLE_COMPUTE_CODES),
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
            "Cache-Control": "no-store" if payload.suggestion_id is not None else "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.include_router(conversations.router)
app.include_router(tasks_router)
app.include_router(home_suggestions_router)
app.include_router(speech_stream_router)
app.include_router(web_snapshots_router)
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
