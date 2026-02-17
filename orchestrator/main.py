from __future__ import annotations

import json
import hashlib
import logging
import os
import time
import uuid

import httpx
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from orchestrator.config import ProviderConfig, Settings, get_settings
from orchestrator.daemon import (
    effective_provider_and_model,
    new_conversation_id,
    new_request_id,
    now_rfc3339,
    sse,
    stream_sse_chat,
)
from orchestrator.db import (
    AppState,
    check_db_health,
    close_app_state,
    get_app_state,
    init_app_state,
)
from orchestrator.routes import conversations, memories, system, users
from orchestrator.models_cache import fetch_openrouter_models, get_fallback_model
from orchestrator.model_router import select_model_tier


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
from orchestrator.tools.builtin import create_default_registry
from orchestrator.tools.completion import completion_with_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    state = await init_app_state(settings)
    app.state.app_state = state
    logger.info("AppState initialised")
    yield
    await close_app_state(state)
    logger.info("AppState shut down")


app = FastAPI(title="daemon-orchestrator", lifespan=lifespan)

# Enable CORS for web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_api_key(settings: Settings, authorization: str | None) -> None:
    if not settings.daemon_api_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.daemon_api_key:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


# ============== Health & Info Endpoints ==============


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    base: dict[str, Any] = {"status": "ok"}
    try:
        state = get_app_state(request)
        base["services"] = await check_db_health(state)
    except Exception:
        pass
    return base


@app.post("/v1/tools/test")
async def test_tools(
    request: Request,
    app_state: AppState = Depends(get_app_state),
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> StreamingResponse:
    """Test endpoint for tool calling. Sends a message that triggers get_time tool."""
    require_api_key(settings, authorization)

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

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/providers")
async def list_providers(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, list[str] | str]:
    """List all available LLM providers."""
    require_api_key(settings, authorization)
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
    timestamp = int(time.time())

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
        print(f"Warning: Failed to fetch OpenRouter models: {e}")
        # Fallback to demo models when OpenRouter API fails
        demo_models = [
            OpenAIModelInfo(
                id="openrouter/kimi/kimi-k2.5",
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
async def get_model_catalog() -> dict[str, object]:
    from orchestrator.catalog import get_catalog

    return get_catalog()


@app.post("/chat/completions", response_model=None)
async def chat_completions_redirect(
    payload: OpenAIChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    """Redirect /chat/completions to /v1/chat/completions for Open WebUI compatibility."""
    return await openai_chat_completions(payload, request, settings, authorization)


@app.post("/v1/chat/completions", response_model=None)
async def openai_chat_completions(
    payload: OpenAIChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> StreamingResponse | OpenAIChatResponse:
    """OpenAI-compatible chat completions endpoint for Open WebUI integration."""
    require_api_key(settings, authorization)

    # Extract the last user message
    user_messages = [m for m in payload.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found")

    last_message = user_messages[-1].content
    conversation_id = new_conversation_id()
    request_id = new_request_id()

    # Determine provider from model ID
    provider_name = settings.default_provider
    if payload.model.startswith("openrouter/"):
        provider_name = "openrouter"

    provider_config = settings.get_provider_config(provider_name)

    # Strip provider prefix to get actual model ID
    actual_model = payload.model
    for prefix in ["openrouter/", "opencode/"]:
        if actual_model.startswith(prefix):
            actual_model = actual_model[len(prefix) :]
            break
    if actual_model == payload.model and actual_model in {"default", "", "kimi"}:
        actual_model = provider_config.model

    system_prompts = [m.content for m in payload.messages if m.role == "system"]
    system_prompt = system_prompts[-1] if system_prompts else DAEMON_SYSTEM_PROMPT

    if payload.stream:

        async def is_disconnected() -> bool:
            return await request.is_disconnected()

        async def generator():
            try:
                provider = provider_config.name
                model = actual_model
                timestamp = int(time.time())
                chunk_id = f"chatcmpl-{new_request_id()}"

                # Stream chunks
                token_count = 0
                content_buffer = ""

                async for frame in stream_sse_chat(
                    settings=settings,
                    provider_config=provider_config,
                    system_prompt=system_prompt,
                    user_message=last_message,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    ping_interval_s=settings.stream_ping_interval_s,
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
                                    delta_content = data.get("data", {}).get(
                                        "delta", ""
                                    )
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
            generator(),
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
                ping_interval_s=settings.stream_ping_interval_s,
                is_disconnected=is_disconnected,
                actual_model=actual_model,
            ):
                if frame.startswith("event: token"):
                    lines = frame.split("\n")
                    for line in lines:
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                delta_content = data.get("data", {}).get("delta", "")
                                if delta_content:
                                    content_parts.append(delta_content)
                            except Exception:
                                pass

            final_content = "".join(content_parts)

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
                    total_tokens=(
                        len(system_prompt) + len(last_message) + len(final_content)
                    )
                    // 4,
                ),
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


# ============== Generated Images Static Serving ==============

GENERATED_IMAGES_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "generated_images"
)
GENERATED_AUDIO_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "generated_audio"
)
TTS_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "tts_cache"
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/generated-images/{filename}")
async def serve_generated_image(filename: str) -> FileResponse:
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
async def serve_generated_audio(filename: str) -> FileResponse:
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


@app.post("/tts")
async def text_to_speech(
    payload: TtsRequest,
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    require_api_key(settings, authorization)

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    model = payload.model or "eleven_flash_v2_5"
    voice = payload.voice or "Xb7hH8MSUJpSbSDYk0k2"
    speed = payload.speed or 1.0
    fmt = payload.format or "mp3"
    use_cache = payload.cache is not False

    cache_key = hashlib.sha256(
        f"{model}|{voice}|{speed}|{fmt}|{text}".encode("utf-8")
    ).hexdigest()
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
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """Return ElevenLabs API key for frontend WebSocket streaming.

    The frontend uses this token to establish direct WebSocket connections
    to ElevenLabs for real-time TTS streaming, avoiding the latency
    penalty of proxying through the backend.
    """
    require_api_key(settings, authorization)

    eleven_api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not eleven_api_key:
        raise HTTPException(status_code=500, detail="ElevenLabs API key not configured")

    return {
        "token": eleven_api_key,
        "expires_in": 300,  # 5 minutes, client should refresh if needed
    }


@app.get("/audio/scribe-token")
async def get_scribe_token(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    require_api_key(settings, authorization)

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
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    require_api_key(settings, authorization)

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
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> FileResponse:
    require_api_key(settings, authorization)

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
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> StreamingResponse:
    require_api_key(settings, authorization)

    conversation_id = payload.conversation_id or new_conversation_id()
    request_id = new_request_id()

    # Get provider configuration from request or default
    provider_config = settings.get_provider_config(payload.provider)

    incoming_messages = payload.messages or []
    last_user_message = None
    for msg in reversed(incoming_messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            last_user_message = msg.get("content")
            break
    user_message = last_user_message or payload.message

    decision = route_message(user_message, payload.metadata)

    user_model_choice = payload.model or "auto"
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

    routing_info = {
        "model": selected_model,
        "tier": model_decision.tier,
        "reason": model_decision.reason,
    }

    # Initialize persistence with graceful degradation
    store = app_state.memory_store if app_state else None
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001") if store else None
    conversation_uuid = None

    # Create or get conversation if persistence is available
    if store and user_id:
        try:
            # Try to parse conversation_id as UUID
            try:
                conv_uuid = uuid.UUID(conversation_id.replace("conv_", ""))
                existing = await store.get_conversation(conv_uuid)
                if existing:
                    conversation_uuid = conv_uuid
                else:
                    # Create new conversation
                    title = (
                        user_message[:50] + "..."
                        if len(user_message) > 50
                        else user_message
                    )
                    conv = await store.create_conversation(
                        user_id=user_id, pipeline=decision.pipeline, title=title
                    )
                    conversation_uuid = conv["id"]
                    conversation_id = f"conv_{conversation_uuid}"
            except ValueError:
                # Invalid UUID format, create new
                title = (
                    user_message[:50] + "..."
                    if len(user_message) > 50
                    else user_message
                )
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
                )
        except Exception:
            pass  # Graceful degradation - continue without persistence
    history_messages: list[dict[str, Any]] | None = None
    if incoming_messages:
        history_messages = [
            {"role": msg.get("role"), "content": msg.get("content")}
            for msg in incoming_messages
            if msg.get("role") and msg.get("content") is not None
        ]
        for msg in reversed(history_messages):
            if msg.get("role") == "user":
                msg["content"] = decision.user_message
                break

    assembled_system_prompt = DAEMON_SYSTEM_PROMPT
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
                base_prompt=DAEMON_SYSTEM_PROMPT,
                memory_context=memory_context,
                preferences_block=preferences_block,
                conversation_id=conversation_uuid,
            )
        except Exception:
            logger.warning("Memory injection failed, using base prompt", exc_info=True)

    async def is_disconnected() -> bool:
        return await request.is_disconnected()

    async def generator():
        try:
            async for frame in stream_sse_chat(
                settings=settings,
                provider_config=provider_config,
                system_prompt=assembled_system_prompt,
                user_message=decision.user_message,
                history_messages=history_messages,
                conversation_id=conversation_id,
                request_id=request_id,
                ping_interval_s=settings.stream_ping_interval_s,
                is_disconnected=is_disconnected,
                actual_model=actual_model,
                reported_model=selected_model,
                routing_info=routing_info,
                memory_store=store,
                user_id=user_id,
                conversation_uuid=conversation_uuid,
                queue=app_state.redis if app_state else None,
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
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Include memory layer API routes
app.include_router(conversations.router)
app.include_router(memories.router)
app.include_router(system.router)
app.include_router(users.router)
