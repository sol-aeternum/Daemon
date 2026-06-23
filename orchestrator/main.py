from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid

import asyncpg
import httpx
import litellm
from collections.abc import AsyncIterator, Sequence
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
from fastapi.responses import FileResponse, StreamingResponse

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
from orchestrator.council.sse import stream_council, stream_council_interview_response
from orchestrator.config import (
    HostSecurityConfigError,
    HostedIdentityConfigError,
    ProviderConfig,
    Settings,
    get_settings,
)
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
from orchestrator.memory.encryption import ContentEncryption, EncryptionInitError
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
from orchestrator.router import route_message
from orchestrator.security_headers import SecurityHeadersMiddleware
from orchestrator.tools.builtin import create_default_registry
from orchestrator.tools.completion import completion_with_tools

logger = logging.getLogger(__name__)

CORS_ALLOW_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOW_HEADERS = (
    "Authorization",
    "Content-Type",
    "X-Daemon-Client-IP",
    "X-CSRF-Token",
)


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


def _validate_startup_config(settings: Settings) -> None:
    """Run all fail-closed startup-time config validations.

    Centralized so the FastAPI lifespan hook stays compact and the
    validation chain is testable in isolation (see
    tests/test_hosted_identity_config.py). Order is intentional: pepper
    first (authentication substrate), then hosted identity (deployment
    posture). Either failure aborts startup before any AppState work.
    """
    validate_pepper_config(settings)
    settings.validate_deployment_mode()
    settings.validate_hosted_identity_config()
    if settings.daemon_encryption_key is not None:
        ContentEncryption(settings.daemon_encryption_key)
    settings.validate_host_security_config()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    try:
        _validate_startup_config(settings)
    except PepperValidationError as exc:
        logger.critical("Production pepper validation failed: %s", exc)
        raise
    except HostedIdentityConfigError as exc:
        logger.critical("Hosted identity config validation failed: %s", exc)
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


app = FastAPI(title="daemon-orchestrator", lifespan=lifespan)

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
)
app.add_middleware(SecurityHeadersMiddleware)

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


DEFAULT_BILLING_USER_ID = "00000000-0000-0000-0000-000000000001"
VALID_BILLING_TIERS = {"free", "starter", "pro", "max", "byok"}


def _build_trusted_spawn_context(
    settings: Settings,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None

    video_meta = metadata.get("video_generation")
    if not isinstance(video_meta, dict):
        return None

    tier = settings.default_tier.lower().strip()
    if tier not in VALID_BILLING_TIERS:
        return None

    if tier == "byok":
        return None

    try:
        user_id = str(uuid.UUID(DEFAULT_BILLING_USER_ID))
    except ValueError:
        return None

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

    tier_config = settings.get_tier_config(tier)
    if tier_config.tier_video_max_duration is not None:
        duration = min(duration, tier_config.tier_video_max_duration)

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

    return {
        "video": {
            "mode": "video",
            "duration": duration,
            "tier": tier,
            "user_id": user_id,
            "source_mode": source_mode,
            "reference_image_url": reference_image_url,
            "reference_image_id": reference_image_id,
            "video_provider": video_provider,
            "kling_model": kling_model,
            "audio_enabled": audio_enabled,
        }
    }


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


def _model_supports_vision(model_id: str) -> bool:
    lowered = model_id.lower().strip()
    if not lowered:
        return False

    positive_tokens = (
        "gpt-4o",
        "gpt-4.1",
        "gpt-4.5",
        "o1",
        "o3",
        "o4",
        "gemini",
        "claude-3",
        "claude-4",
        "vision",
        "pixtral",
        "llava",
        "qwen-vl",
    )
    if any(token in lowered for token in positive_tokens):
        return True

    negative_tokens = ("embedding", "whisper", "tts", "audio")
    if any(token in lowered for token in negative_tokens):
        return False

    return False


def _normalize_model_for_provider(model_id: str, provider_config: ProviderConfig) -> str:
    normalized = model_id.strip()
    if not normalized:
        return normalized

    if provider_config.name == "openrouter":
        if normalized.startswith("openrouter/"):
            return normalized
        if normalized.startswith("opencode/"):
            return f"openrouter/{normalized[len('opencode/') :]}"
        return f"openrouter/{normalized}"

    for prefix in ("openrouter/", "opencode/"):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]

    return normalized


def _get_vision_fallback_model(settings: Settings, provider_config: ProviderConfig) -> str:
    tier_config = settings.get_tier_config(settings.default_tier)
    if tier_config.image_agent and tier_config.image_agent.model:
        return _normalize_model_for_provider(tier_config.image_agent.model, provider_config)
    return _normalize_model_for_provider(settings.auto_fast_model, provider_config)


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


async def _summarize_images_for_fallback(
    *,
    fallback_model: str,
    provider_config: ProviderConfig,
    user_text: str,
    image_parts: list[dict[str, Any]],
) -> tuple[str | None, str]:
    analysis_instruction = (
        "You are a vision analysis assistant. Analyze all attached images for the user request and "
        "return a concise summary another text-only model can use. Include only high-confidence details. "
        "Respond with plain text only."
    )
    request_text = user_text.strip() or "Please describe the attached images."
    analysis_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"User request:\n{request_text}\n\nProvide a concise factual summary.",
        }
    ]
    analysis_content.extend(image_parts)

    call_params: dict[str, Any] = {
        "model": fallback_model,
        "messages": [
            {"role": "system", "content": analysis_instruction},
            {"role": "user", "content": analysis_content},
        ],
        "stream": False,
        "timeout": provider_config.timeout_s,
    }

    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    try:
        response = await litellm.acompletion(**call_params)
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices", [])
        if not choices:
            return None, fallback_model

        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        if message is None and isinstance(first_choice, dict):
            message = first_choice.get("message", {})
        if message is None:
            message = {}

        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        summary = _extract_text_content(content)
        if summary:
            return summary, fallback_model
        return None, fallback_model
    except Exception:
        logger.warning("Vision fallback analysis failed", exc_info=True)
        return None, fallback_model


# ============== Health & Info Endpoints ==============


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    base: dict[str, Any] = {"status": "ok"}
    try:
        state = get_app_state(request)
        base["services"] = await check_db_health(state)
    except Exception as e:
        logger.warning("Health check failed: %s", e)
        base["status"] = "degraded"
        base["error"] = str(e)
    return base


@app.post("/v1/tools/test")
async def test_tools(
    request: Request,
    app_state: AppState = Depends(get_app_state),
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> StreamingResponse:
    """Test endpoint for tool calling. Sends a message that triggers get_time tool."""

    body = await request.json()
    user_message = body.get("message", "What time is it right now?")
    model = body.get("model", "llama-3.3-70b")

    provider_config = settings.get_provider_config("openrouter")

    store = app_state.memory_store
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001") if store else None
    registry = create_default_registry(
        brave_api_key=settings.brave_api_key,
        memory_store=store,
        user_id=user_id,
    )

    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant. Use tools when appropriate.",
        },
        {"role": "user", "content": user_message},
    ]

    async def generate():
        async for event in completion_with_tools(
            settings=settings,
            provider_config=provider_config,
            messages=messages,
            registry=registry,
            actual_model=model,
        ):
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_with_keepalives(generate(), settings.sse_keepalive_interval_s),
        media_type="text/event-stream",
    )


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
    models = []
    timestamp = int(time.time())  # noqa: F841

    # Fetch OpenRouter models dynamically with caching
    try:
        openrouter_models = await fetch_openrouter_models(
            api_key=settings.openrouter_api_key,
        )

        # Add metadata and convert to OpenAIModelInfo format
        for model_data in openrouter_models:
            model_id = model_data["id"]

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
        # Fallback to demo models when OpenRouter API fails
        demo_models = [
            OpenAIModelInfo(
                id="openrouter/moonshotai/kimi-k2.5",
                object="model",
                created=int(time.time()),
                owned_by="openrouter",
                metadata={
                    "capabilities": ["chat", "streaming"],
                },
            ),
            OpenAIModelInfo(
                id="openrouter/anthropic/claude-opus-4.6",
                object="model",
                created=int(time.time()),
                owned_by="openrouter",
                metadata={
                    "capabilities": ["chat", "streaming"],
                },
            ),
            OpenAIModelInfo(
                id="openrouter/google/gemini-2.5-flash",
                object="model",
                created=int(time.time()),
                owned_by="openrouter",
                metadata={
                    "capabilities": ["chat", "streaming"],
                },
            ),
        ]
        models.extend(demo_models)

    return OpenAIModelList(data=models)


@app.get("/v1/catalog")
async def get_model_catalog() -> dict[str, Any]:
    from typing import cast
    from orchestrator.catalog import FEATURED_MODELS, get_catalog
    from orchestrator.models_cache import get_cached_models

    catalog = get_catalog()

    # Add dynamic new models from cache
    cached = get_cached_models()
    featured_ids = {fm.id for fm in FEATURED_MODELS}

    # Get models that are new and not already in featured
    dynamic_new = [
        {
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "tagline": "Newly added",
            "badges": ["new"],
        }
        for m in cached
        if m.get("is_new") and m["id"] not in featured_ids
    ][:2]

    featured = cast(list[Any], catalog["featured"])
    featured.extend(dynamic_new)

    return {
        "auto": catalog["auto"],
        "featured": featured,
    }


@app.post("/chat/completions", response_model=None)
async def chat_completions_redirect(
    payload: OpenAIChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Redirect /chat/completions to /v1/chat/completions for Open WebUI compatibility."""
    return await openai_chat_completions(payload, request, settings, auth)


@app.post("/v1/chat/completions", response_model=None)
async def openai_chat_completions(
    payload: OpenAIChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> StreamingResponse | OpenAIChatResponse:
    """OpenAI-compatible chat completions endpoint for Open WebUI integration."""

    # Extract the last user message
    user_messages = [m for m in payload.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found")

    last_message = _extract_text_content(user_messages[-1].content)
    if not last_message:
        last_message = "Please help with the attached input."
    conversation_id = new_conversation_id()
    request_id = new_request_id()

    # Determine provider from model ID
    provider_name = settings.default_provider
    if payload.model.startswith("openrouter/"):
        provider_name = "openrouter"

    provider_config = settings.get_provider_config(provider_name)

    # Strip provider prefix to get actual model ID
    actual_model = payload.model
    if provider_name != "openrouter":
        for prefix in ["openrouter/", "opencode/"]:
            if actual_model.startswith(prefix):
                actual_model = actual_model[len(prefix) :]
                break
    if actual_model == payload.model and actual_model in {"default", "", "kimi"}:
        actual_model = provider_config.model

    system_prompts = [
        _extract_text_content(m.content)
        for m in payload.messages
        if m.role == "system" and _extract_text_content(m.content)
    ]
    system_prompt = system_prompts[-1] if system_prompts else DAEMON_SYSTEM_PROMPT
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

                async for frame in stream_sse_chat(
                    settings=settings,
                    provider_config=provider_config,
                    system_prompt=system_prompt,
                    user_message=last_message,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    ping_interval_s=settings.sse_keepalive_interval_s,
                    is_disconnected=is_disconnected,
                    actual_model=actual_model,
                ):
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
                                            model=payload.model,
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
                            model=payload.model,
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
                # Error in streaming
                error_chunk = OpenAIChatStreamChunk(
                    id=f"chatcmpl-{new_request_id()}",
                    created=int(time.time()),
                    model=payload.model,
                    choices=[
                        OpenAIChoice(
                            index=0,
                            delta=OpenAIDeltaMessage(content=f"Error: {str(e)}"),
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

        try:

            async def is_disconnected() -> bool:
                return False

            async for frame in stream_sse_chat(
                settings=settings,
                provider_config=provider_config,
                system_prompt=system_prompt,
                user_message=last_message,
                conversation_id=conversation_id,
                request_id=request_id,
                ping_interval_s=settings.sse_keepalive_interval_s,
                is_disconnected=is_disconnected,
                actual_model=actual_model,
            ):
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
                model=payload.model,
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
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


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
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    filepath = GENERATED_IMAGES_DIR / safe_name
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    media_type = "image/png"
    if safe_name.endswith(".jpg") or safe_name.endswith(".jpeg"):
        media_type = "image/jpeg"
    elif safe_name.endswith(".webp"):
        media_type = "image/webp"
    return FileResponse(filepath, media_type=media_type)


@app.get("/generated-audio/{filename}")
async def serve_generated_audio(
    filename: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> FileResponse:
    """Serve a generated audio file from disk (TTS or sound effects)."""
    safe_name = Path(filename).name
    # Check TTS cache first, then generated audio directory
    filepath = TTS_CACHE_DIR / safe_name
    if not filepath.exists():
        filepath = GENERATED_AUDIO_DIR / safe_name
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    media_type = "audio/mpeg"
    if safe_name.endswith(".wav"):
        media_type = "audio/wav"
    elif safe_name.endswith(".ogg"):
        media_type = "audio/ogg"
    return FileResponse(filepath, media_type=media_type)


@app.get("/generated-files/{filename}")
async def serve_generated_file(
    filename: str,
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> FileResponse:
    """Serve a generated document file from disk."""
    safe_name = Path(filename).name
    filepath = GENERATED_FILES_DIR / safe_name
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Determine media type based on extension
    media_type = "application/octet-stream"  # default
    if safe_name.endswith(".docx"):
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif safe_name.endswith(".csv"):
        media_type = "text/csv"
    elif safe_name.endswith(".xlsx"):
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif safe_name.endswith(".txt"):
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

    model = payload.model or "eleven_flash_v2_5"
    voice = payload.voice or "Xb7hH8MSUJpSbSDYk0k2"
    speed = payload.speed or 1.0
    fmt = payload.format or "mp3"
    use_cache = payload.cache is not False

    cache_key = hashlib.sha256(f"{model}|{voice}|{speed}|{fmt}|{text}".encode("utf-8")).hexdigest()
    filename = f"{cache_key}.{fmt}"
    filepath = TTS_CACHE_DIR / filename
    if use_cache and filepath.exists():
        return {
            "audio_path": f"/generated-audio/{filename}",
            "cached": True,
            "model": model,
            "voice": voice,
            "format": fmt,
        }

    eleven_api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not eleven_api_key:
        raise HTTPException(status_code=500, detail="ElevenLabs API key missing")

    voice_id = voice

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": eleven_api_key,
        "Content-Type": "application/json",
    }

    format_map = {
        "mp3": "mp3_22050_32",
        "wav": "pcm_22050",
        "ogg": "ogg_vorbis_22050",
    }
    output_format = format_map.get(fmt, "mp3_44100_128")

    request_body: dict[str, Any] = {
        "text": text,
        "model_id": model if model.startswith("eleven") else "eleven_multilingual_v2",
        "output_format": output_format,
    }
    if speed and speed != 1.0:
        request_body["voice_settings"] = {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0 if speed >= 1.0 else 0.5,
            "use_speaker_boost": True,
        }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=request_body, headers=headers)
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"ElevenLabs TTS request failed: {response.text}",
            )
        filepath.write_bytes(response.content)

    return {
        "audio_path": f"/generated-audio/{filename}",
        "cached": False,
        "model": model,
        "voice": voice,
        "format": fmt,
    }


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
    eleven_api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not eleven_api_key:
        raise HTTPException(status_code=500, detail="ElevenLabs API key not configured")

    # Generate scoped token for TTS WebSocket
    url = "https://api.elevenlabs.io/v1/single-use-token/tts_websocket"
    headers = {"xi-api-key": eleven_api_key}

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers=headers)
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"ElevenLabs TTS token request failed: {response.text}",
            )

    data = response.json()
    token = data.get("token")
    if not token:
        raise HTTPException(status_code=502, detail="ElevenLabs TTS token missing")

    return {
        "token": token,
        "expires_in": 900,  # 15 minutes, scoped token TTL
    }


@app.get("/audio/scribe-token")
async def get_scribe_token(
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    eleven_api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not eleven_api_key:
        raise HTTPException(status_code=500, detail="ElevenLabs API key not configured")

    url = "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe"
    headers = {"xi-api-key": eleven_api_key}

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers=headers)
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"ElevenLabs Scribe token request failed: {response.text}",
            )

    data = response.json()
    token = data.get("token")
    if not token:
        raise HTTPException(status_code=502, detail="ElevenLabs Scribe token missing")

    return {
        "token": token,
        "expires_in": 900,
    }


@app.post("/stt")
async def speech_to_text(
    audio_file: UploadFile = File(...),
    model: str = Form("scribe_v2"),
    language: str | None = Form(None),
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> dict[str, Any]:
    eleven_api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not eleven_api_key:
        raise HTTPException(status_code=500, detail="ElevenLabs API key missing")

    url = "https://api.elevenlabs.io/v1/speech-to-text"
    headers = {"xi-api-key": eleven_api_key}

    file_content = await audio_file.read()
    files = {
        "file": (
            audio_file.filename or "audio.mp3",
            file_content,
            audio_file.content_type or "audio/mpeg",
        )
    }
    data = {"model_id": model}
    if language:
        data["language_code"] = language

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers=headers, data=data, files=files)
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"STT request failed: {response.text}",
            )
        result = response.json()

    return {
        "text": result.get("text", ""),
        "language": result.get("language_code"),
        "confidence": result.get("confidence", 0.0),
        "words": result.get("words", []),
    }


@app.post("/sound-effects")
async def generate_sound_effect(
    text: str = Form(...),
    duration_seconds: float = Form(2.0),
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> FileResponse:
    eleven_api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not eleven_api_key:
        raise HTTPException(status_code=500, detail="ElevenLabs API key missing")

    cache_key = hashlib.sha256(f"{text}|{duration_seconds}".encode("utf-8")).hexdigest()
    filename = f"{cache_key}.mp3"
    filepath = TTS_CACHE_DIR / filename

    if filepath.exists():
        return FileResponse(filepath, media_type="audio/mpeg")

    url = "https://api.elevenlabs.io/v1/sound-generation"
    headers = {
        "xi-api-key": eleven_api_key,
        "Content-Type": "application/json",
    }
    request_body = {
        "text": text,
        "duration_seconds": min(max(duration_seconds, 0.5), 22.0),
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=request_body, headers=headers)
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Sound effects request failed: {response.text}",
            )
        filepath.write_bytes(response.content)

    return FileResponse(filepath, media_type="audio/mpeg")


# ============== Legacy Daemon Endpoint ==============


@app.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> StreamingResponse:
    conversation_id = payload.conversation_id or new_conversation_id()
    # Warn if no conversation_id was provided - should not happen in normal frontend flow
    if not payload.conversation_id:
        logger.warning(
            "No conversation_id provided — creating new conversation. "
            "This should not happen in normal frontend flow."
        )
    request_id = new_request_id()

    # Get provider configuration from request or default
    provider_config = settings.get_provider_config(payload.provider)

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

    actual_model = selected_model
    if provider_config.name != "openrouter":
        for prefix in ["openrouter/", "opencode/"]:
            if actual_model.startswith(prefix):
                actual_model = actual_model[len(prefix) :]
                break

    routing_info: dict[str, Any] = {
        "model": selected_model,
        "tier": model_decision.tier,
        "reason": model_decision.reason,
    }

    has_image_input = _content_has_image(prepared_user_content)
    enforce_direct_vision_voice = False
    if has_image_input and not _model_supports_vision(selected_model):
        image_parts = _extract_image_parts(prepared_user_content)
        if image_parts:
            fallback_model = _get_vision_fallback_model(settings, provider_config)
            fallback_summary, fallback_model = await _summarize_images_for_fallback(
                fallback_model=fallback_model,
                provider_config=provider_config,
                user_text=user_message,
                image_parts=image_parts,
            )
            if fallback_summary:
                summary_block = fallback_summary.strip()
                if user_message:
                    user_message = (f"{user_message}\n\nVisual findings:\n{summary_block}").strip()
                else:
                    user_message = f"Visual findings:\n{summary_block}"
                prepared_user_content = user_message
                enforce_direct_vision_voice = True
                routing_info["vision_fallback"] = {
                    "used": True,
                    "mode": "summary",
                    "model": fallback_model,
                    "summary_available": True,
                }
            else:
                selected_model = fallback_model
                actual_model = fallback_model
                if provider_config.name != "openrouter":
                    for prefix in ["openrouter/", "opencode/"]:
                        if actual_model.startswith(prefix):
                            actual_model = actual_model[len(prefix) :]
                            break
                routing_info["model"] = selected_model
                routing_info["vision_fallback"] = {
                    "used": True,
                    "mode": "handoff",
                    "model": fallback_model,
                    "summary_available": False,
                }
    elif has_image_input:
        routing_info["vision_fallback"] = {
            "used": False,
            "model": selected_model,
        }

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
                exclude_status=["streaming"],
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

    if enforce_direct_vision_voice:
        assembled_system_prompt = (
            f"{assembled_system_prompt.rstrip()}\n\n"
            "When visual findings are provided in the user message, treat them as your own direct visual observations. "
            "Do not mention fallback models, intermediary summaries, or that the image was described to you. "
            "Answer as if you directly viewed the image."
        )

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

                async for frame in stream_council_interview_response(
                    user_message=user_message,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    stored_config=council_config_response,
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

                async for frame in stream_council(
                    user_message=user_message,
                    conversation_id=conversation_id,
                    request_id=request_id,
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

            trusted_spawn_context = _build_trusted_spawn_context(settings, payload.metadata)
            async for frame in stream_sse_chat(
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
                reported_model=selected_model,
                routing_info=routing_info,
                memory_store=store,
                user_id=user_id,
                conversation_uuid=conversation_uuid,
                queue=app_state.redis if app_state else None,
                db_pool=app_state.db_pool if app_state else None,
                trusted_spawn_context=trusted_spawn_context,
                disable_memory_write=bool(payload.disable_memory_write),
            ):
                yield frame
        except Exception as e:
            ts = now_rfc3339()
            provider, model = effective_provider_and_model(settings, provider_config)
            model_for_events = selected_model or actual_model or model
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
                        "message": str(e),
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
app.include_router(images.router)
app.include_router(memories.router)
app.include_router(skills.router)
app.include_router(system.router)
app.include_router(users.router)
app.include_router(video_credits.router)
app.include_router(auth_config_router)
app.include_router(auth_setup_router)
