from __future__ import annotations

# pyright: reportMissingImports=false

from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderConfig(BaseSettings):
    """Configuration for a single LLM provider."""

    name: str = ""
    base_url: str = ""
    api_key: str | None = None
    model: str = ""
    # Extra headers for provider-specific requirements
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # Timeout for requests to this provider
    timeout_s: float = 90.0
    # Whether this provider requires authentication
    requires_auth: bool = True


class ModelSlotConfig(BaseSettings):
    """Configuration for a specific model slot (orchestrator, research, etc.)."""

    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    # Provider-specific parameters
    extra_params: dict[str, Any] = Field(default_factory=dict)


class TierConfig(BaseSettings):
    """Model assignments for a specific tier."""

    orchestrator: ModelSlotConfig
    research_agent: ModelSlotConfig | None = None
    code_agent: ModelSlotConfig | None = None
    image_agent: ModelSlotConfig | None = None
    reader_agent: ModelSlotConfig | None = None
    embeddings: ModelSlotConfig | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    # If set, the API requires `Authorization: Bearer <DAEMON_API_KEY>`.
    daemon_api_key: str | None = None

    # Default provider to use when none specified in request
    default_provider: str = "openrouter"

    # Request and stream settings
    request_timeout_s: float = 90.0
    stream_ping_interval_s: float = 15.0
    chat_history_limit: int = 50

    # Development fallback: stream a canned response without calling any provider.
    mock_llm: bool = False

    # ===== TIER-BASED MODEL CONFIGURATION =====
    # Model tier to use by default (free, starter, pro, max, byok)
    default_tier: str = "pro"

    # Tier: FREE ($0)
    # Uses Kimi K2.5 for orchestrator, limited/no subagents
    tier_free_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
    tier_free_orchestrator_temp: float = 0.7
    tier_free_research_model: str = ""
    tier_free_code_model: str = ""
    tier_free_image_model: str = ""
    tier_free_reader_model: str = ""
    tier_free_embeddings_model: str = ""

    # CORS configuration
    cors_allowed_origins: str = "http://localhost:3000,http://frontend:3000"

    # Tier: STARTER ($9/mo)
    # Kimi K2.5 orchestrator, basic subagents
    tier_starter_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
    tier_starter_orchestrator_temp: float = 0.7
    tier_starter_research_model: str = "openrouter/anthropic/claude-3.5-sonnet"
    tier_starter_research_temp: float = 0.5
    tier_starter_code_model: str = "openrouter/anthropic/claude-3.5-sonnet"
    tier_starter_code_temp: float = 0.3
    tier_starter_image_model: str = "google/gemini-2.5-flash-image"
    tier_starter_image_temp: float = 0.8
    tier_starter_reader_model: str = "openrouter/google/gemini-2.0-pro-exp"
    tier_starter_reader_temp: float = 0.3
    tier_starter_embeddings_model: str = "openrouter/openai/text-embedding-3-small"

    # Tier: PRO ($19/mo)
    # Kimi K2.5 orchestrator, full subagent suite
    tier_pro_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
    tier_pro_orchestrator_temp: float = 0.7
    tier_pro_research_model: str = "openrouter/anthropic/claude-3.5-sonnet"
    tier_pro_research_temp: float = 0.5
    tier_pro_code_model: str = "openrouter/anthropic/claude-3.5-sonnet"
    tier_pro_code_temp: float = 0.3
    tier_pro_image_model: str = "google/gemini-2.5-flash-image"
    tier_pro_image_temp: float = 0.8
    tier_pro_reader_model: str = "openrouter/google/gemini-2.0-pro-exp"
    tier_pro_reader_temp: float = 0.3
    tier_pro_embeddings_model: str = "openrouter/openai/text-embedding-3-small"

    # Tier: MAX ($29/mo)
    # Opus 4.6 orchestrator, premium subagents
    tier_max_orchestrator_model: str = "openrouter/anthropic/claude-opus-4.6"
    tier_max_orchestrator_temp: float = 0.7
    tier_max_research_model: str = "openrouter/anthropic/claude-3.5-sonnet"
    tier_max_research_temp: float = 0.5
    tier_max_code_model: str = "openrouter/anthropic/claude-opus-4.6"
    tier_max_code_temp: float = 0.3
    tier_max_image_model: str = "google/gemini-2.5-flash-image"
    tier_max_image_temp: float = 0.8
    tier_max_reader_model: str = "openrouter/google/gemini-2.0-pro-exp"
    tier_max_reader_temp: float = 0.3
    tier_max_embeddings_model: str = "openrouter/openai/text-embedding-3-large"

    # Tier: BYOK ($9/mo)
    # User brings their own OpenRouter key
    tier_byok_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
    tier_byok_orchestrator_temp: float = 0.7
    tier_byok_research_model: str = ""
    tier_byok_code_model: str = ""
    tier_byok_image_model: str = ""
    tier_byok_reader_model: str = ""
    tier_byok_embeddings_model: str = ""

    # ===== AUTO-ROUTING MODEL TIERS =====
    auto_fast_model: str = "openrouter/google/gemini-2.5-flash"
    auto_fast_temp: float = 0.7

    auto_reasoning_model: str = "openrouter/moonshotai/kimi-k2.5"
    auto_reasoning_temp: float = 0.7

    # ===== PROVIDER CONFIGURATION =====
    # OpenRouter (primary provider)
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_referer: str = "https://daemon.ai"
    openrouter_title: str = "Daemon AI Assistant"

    # Legacy provider settings (for backward compatibility only)

    # Brave Search API (Web search)
    brave_api_key: str | None = None

    # ===== MEMORY LAYER =====
    database_url: str | None = None
    redis_url: str | None = None
    daemon_encryption_key: str | None = None

    # ===== TITLE GENERATION =====
    title_model: str = "openrouter/openai/gpt-4o-mini"

    def get_tier_config(self, tier: str | None = None) -> TierConfig:
        """Get model configuration for a specific tier.

        Args:
            tier: Tier name (free, starter, pro, max, byok). If None, uses default_tier.

        Returns:
            TierConfig with model assignments for all slots.
        """
        tier_name = (tier or self.default_tier).lower()
        prefix = f"tier_{tier_name}_"

        def get_slot_config(slot: str) -> ModelSlotConfig | None:
            """Get config for a specific slot, returning None if model is empty."""
            model = getattr(self, f"{prefix}{slot}_model", "")
            if not model:
                return None
            return ModelSlotConfig(
                model=model,
                temperature=getattr(self, f"{prefix}{slot}_temp", 0.7),
            )

        return TierConfig(
            orchestrator=get_slot_config("orchestrator")
            or ModelSlotConfig(
                model="openrouter/moonshotai/kimi-k2.5", temperature=0.7
            ),
            research_agent=get_slot_config("research"),
            code_agent=get_slot_config("code"),
            image_agent=get_slot_config("image"),
            reader_agent=get_slot_config("reader"),
            embeddings=get_slot_config("embeddings"),
        )

    def get_provider_config(
        self, provider_name: str | None = None, tier: str | None = None
    ) -> ProviderConfig:
        """Get configuration for a specific provider.

        Args:
            provider_name: Name of the provider. If None, uses default_provider.
            tier: Optional tier for BYOK mode (uses user's own API key).

        Returns:
            ProviderConfig for the specified provider.
        """
        name = provider_name or self.default_provider
        tier_config = self.get_tier_config(tier)

        # Built-in providers
        if name == "openrouter":
            extra_headers = {
                "HTTP-Referer": self.openrouter_referer,
                "X-Title": self.openrouter_title,
            }
            # BYOK tier uses user's own API key (passed in request)
            return ProviderConfig(
                name="openrouter",
                base_url=self.openrouter_base_url,
                api_key=self.openrouter_api_key,
                model=tier_config.orchestrator.model,
                extra_headers=extra_headers,
                requires_auth=True,
                timeout_s=self.request_timeout_s,
            )
        prefix = f"PROVIDER_{name.upper()}_"
        base_url = getattr(self, f"{prefix.lower()}base_url", "")
        if base_url:
            return ProviderConfig(
                name=name,
                base_url=base_url,
                api_key=getattr(self, f"{prefix.lower()}api_key", None),
                model=getattr(self, f"{prefix.lower()}model", ""),
                requires_auth=getattr(self, f"{prefix.lower()}requires_auth", True),
                timeout_s=getattr(
                    self, f"{prefix.lower()}timeout_s", self.request_timeout_s
                ),
            )

        return ProviderConfig(
            name="openrouter",
            base_url=self.openrouter_base_url,
            api_key=self.openrouter_api_key,
            model=tier_config.orchestrator.model,
            extra_headers={
                "HTTP-Referer": self.openrouter_referer,
                "X-Title": self.openrouter_title,
            },
            requires_auth=True,
            timeout_s=self.request_timeout_s,
        )

    def list_available_tiers(self) -> list[dict[str, Any]]:
        """List all available tiers with their orchestrator models."""
        tiers = [
            {
                "id": "free",
                "name": "Free",
                "price": 0,
                "orchestrator": self.tier_free_orchestrator_model or "N/A",
            },
            {
                "id": "starter",
                "name": "Starter",
                "price": 9,
                "orchestrator": self.tier_starter_orchestrator_model,
            },
            {
                "id": "pro",
                "name": "Pro",
                "price": 19,
                "orchestrator": self.tier_pro_orchestrator_model,
            },
            {
                "id": "max",
                "name": "Max",
                "price": 29,
                "orchestrator": self.tier_max_orchestrator_model,
            },
            {
                "id": "byok",
                "name": "BYOK",
                "price": 9,
                "orchestrator": self.tier_byok_orchestrator_model,
            },
        ]
        return tiers

    def list_available_providers(self) -> list[str]:
        """List all configured providers."""
        providers = ["openrouter"]

        # Add any custom providers from env vars
        for key in self.model_dump().keys():
            if key.startswith("provider_") and key.endswith("_base_url"):
                provider_name = key[9:-10]  # Extract name from provider_{name}_base_url
                if provider_name not in providers:
                    providers.append(provider_name)

        return providers


@lru_cache
def get_settings() -> Settings:
    return Settings()
