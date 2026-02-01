from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    # If set, the API requires `Authorization: Bearer <DAEMON_API_KEY>`.
    daemon_api_key: str | None = None

    # Cloud provider (OpenRouter)
    openrouter_api_key: str | None = None
    litellm_model: str = "openrouter/anthropic/claude-opus-4.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Alternate provider: OpenCode Zen (OpenAI-compatible `/chat/completions`).
    # Docs: https://opencode.ai/docs/zen/
    opencode_api_key: str | None = None
    opencode_base_url: str = "https://opencode.ai/zen/v1"
    opencode_model: str = "opencode/claude-opus-4-5"

    request_timeout_s: float = 90.0
    stream_ping_interval_s: float = 15.0

    # Development fallback: stream a canned response without calling any provider.
    mock_llm: bool = False

    # Which upstream provider to use when `mock_llm` is false.
    # Values: `openrouter` | `opencode_zen`
    llm_provider: str = "openrouter"


@lru_cache
def get_settings() -> Settings:
    return Settings()
