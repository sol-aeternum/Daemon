from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from orchestrator.config import Settings, get_settings
from orchestrator.daemon import (
    effective_provider_and_model,
    new_conversation_id,
    new_request_id,
    now_rfc3339,
    sse,
    stream_sse_chat,
)
from orchestrator.models import ChatRequest
from orchestrator.prompts import DAEMON_SYSTEM_PROMPT
from orchestrator.router import route_message


app = FastAPI(title="daemon-orchestrator")


def require_api_key(settings: Settings, authorization: str | None) -> None:
    if not settings.daemon_api_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.daemon_api_key:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> StreamingResponse:
    require_api_key(settings, authorization)

    conversation_id = payload.conversation_id or new_conversation_id()
    request_id = new_request_id()

    decision = route_message(payload.message, payload.metadata)

    async def is_disconnected() -> bool:
        return await request.is_disconnected()

    async def generator():
        try:
            async for frame in stream_sse_chat(
                settings=settings,
                system_prompt=DAEMON_SYSTEM_PROMPT,
                user_message=decision.user_message,
                conversation_id=conversation_id,
                request_id=request_id,
                ping_interval_s=settings.stream_ping_interval_s,
                is_disconnected=is_disconnected,
            ):
                yield frame
        except Exception as e:
            ts = now_rfc3339()
            provider, model = effective_provider_and_model(settings)
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
                        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        "model": model,
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
