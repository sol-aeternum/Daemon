from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ============== OpenAI Compatible Models ==============


class OpenAIMessage(BaseModel):
    """OpenAI chat message format."""

    role: Literal["system", "user", "assistant"] = "user"
    content: str
    name: str | None = None


class OpenAIChatRequest(BaseModel):
    """OpenAI /v1/chat/completions request format."""

    model: str
    messages: list[OpenAIMessage]
    temperature: float | None = Field(default=1.0, ge=0, le=2)
    top_p: float | None = Field(default=1.0, ge=0, le=1)
    n: int | None = Field(default=1, ge=1, le=10)
    stream: bool | None = Field(default=False)
    stop: str | list[str] | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = Field(default=0, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=0, ge=-2, le=2)
    user: str | None = None


class OpenAIDeltaMessage(BaseModel):
    """Delta message for streaming responses."""

    role: str | None = None
    content: str | None = None


class OpenAIChoice(BaseModel):
    """Choice in chat completion response."""

    index: int = 0
    message: OpenAIMessage | None = None
    delta: OpenAIDeltaMessage | None = None
    finish_reason: str | None = None


class OpenAIUsage(BaseModel):
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatResponse(BaseModel):
    """OpenAI /v1/chat/completions response format."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChoice]
    usage: OpenAIUsage | None = None


class OpenAIChatStreamChunk(BaseModel):
    """OpenAI streaming response chunk."""

    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[OpenAIChoice]


class OpenAIModelInfo(BaseModel):
    """Model info for /v1/models endpoint."""

    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "daemon"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpenAIModelList(BaseModel):
    """Response for /v1/models endpoint."""

    object: str = "list"
    data: list[OpenAIModelInfo]


# ============== Legacy Daemon Models ==============


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str
    messages: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = Field(default=None)
    # Provider selection - uses default if not specified
    # Supported: "openrouter", "opencode_zen", or any custom provider
    provider: str | None = Field(
        default=None, description="LLM provider to use (default: openrouter)"
    )


class TtsRequest(BaseModel):
    text: str
    voice: str | None = None
    model: str | None = None
    speed: float | None = None
    format: str | None = None
    cache: bool | None = True
