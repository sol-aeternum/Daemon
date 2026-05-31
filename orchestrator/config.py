from __future__ import annotations

# pyright: reportMissingImports=false

from functools import lru_cache
from typing import ClassVar

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
    extra_params: dict[str, object] = Field(default_factory=dict)


class TierConfig(BaseSettings):
    """Model assignments for a specific tier."""

    orchestrator: ModelSlotConfig
    research_agent: ModelSlotConfig | None = None
    code_agent: ModelSlotConfig | None = None
    image_agent: ModelSlotConfig | None = None
    reader_agent: ModelSlotConfig | None = None
    embeddings: ModelSlotConfig | None = None

    # Video generation access controls
    tier_video_enabled: bool = False
    tier_video_max_duration: int | None = None
    tier_video_credit_discount: float = 0.0


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    # If set, the API requires `Authorization: Bearer <DAEMON_API_KEY>`.
    daemon_api_key: str | None = None
    daemon_admin_api_key: str | None = None

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
    tier_free_image_provider: str = "openrouter"
    tier_free_video_provider: str = "fal"
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
    tier_starter_image_provider: str = "openrouter"
    tier_starter_video_provider: str = "fal"
    tier_starter_reader_model: str = "openrouter/google/gemini-2.0-pro-exp"
    tier_starter_reader_temp: float = 0.3
    tier_starter_embeddings_model: str = "voyage-4-large"

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
    tier_pro_image_provider: str = "openrouter"
    tier_pro_video_provider: str = "fal"
    tier_pro_reader_model: str = "openrouter/google/gemini-2.0-pro-exp"
    tier_pro_reader_temp: float = 0.3
    tier_pro_embeddings_model: str = "voyage-4-large"

    # Tier: MAX ($29/mo)
    # Opus 4.6 orchestrator, premium subagents
    tier_max_orchestrator_model: str = "openrouter/anthropic/claude-opus-4.6"
    tier_max_orchestrator_temp: float = 0.7
    # Grok alternative for Max-tier orchestrator
    tier_max_orchestrator_model_grok: str = "x-ai/grok-4"
    tier_max_orchestrator_model_grok_temp: float = 0.7
    tier_max_research_model: str = "openrouter/anthropic/claude-3.5-sonnet"
    tier_max_research_temp: float = 0.5
    tier_max_code_model: str = "openrouter/anthropic/claude-opus-4.6"
    tier_max_code_temp: float = 0.3
    tier_max_image_model: str = "google/gemini-2.5-flash-image"
    tier_max_image_temp: float = 0.8
    tier_max_image_provider: str = "openrouter"
    tier_max_video_provider: str = "fal"
    tier_max_reader_model: str = "openrouter/google/gemini-2.0-pro-exp"
    tier_max_reader_temp: float = 0.3
    tier_max_embeddings_model: str = "voyage-4-large"

    # Tier: BYOK ($9/mo)
    # User brings their own OpenRouter key
    tier_byok_orchestrator_model: str = "openrouter/moonshotai/kimi-k2.5"
    tier_byok_orchestrator_temp: float = 0.7
    tier_byok_research_model: str = ""
    tier_byok_code_model: str = ""
    tier_byok_image_model: str = ""
    tier_byok_image_provider: str = "openrouter"
    tier_byok_video_provider: str = "fal"
    tier_byok_reader_model: str = ""
    tier_byok_embeddings_model: str = ""

    # ===== AUTO-ROUTING MODEL TIERS =====
    auto_fast_model: str = "openrouter/google/gemini-2.5-flash"
    auto_fast_temp: float = 0.7

    # Grok alternatives for auto-fast model
    auto_fast_model_grok: str = "x-ai/grok-4.1-fast"
    auto_fast_model_grok_temp: float = 0.7

    auto_reasoning_model: str = "openrouter/moonshotai/kimi-k2.5"
    auto_reasoning_temp: float = 0.7

    # ===== PROVIDER CONFIGURATION =====
    # OpenRouter (primary provider)
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_referer: str = "https://daemon.ai"
    openrouter_title: str = "Daemon AI Assistant"

    # Provider-level defaults for model-specific parameters (reasoning/thinking).
    # Applied by model prefix and overridden by per-model `model_extra_params`.
    provider_extra_params: dict[str, dict[str, object]] = Field(
        default_factory=lambda: {
            "openrouter/anthropic/": {"reasoning": {"enabled": True}},
            "openrouter/openai/": {"reasoning_effort": "medium"},
            "openrouter/google/": {"reasoning": {"max_tokens": 4096}},
            "anthropic/": {"thinking": {"type": "adaptive", "effort": "medium"}},
            "openai/responses/": {"reasoning_effort": "medium"},
        }
    )

    # Per-model overrides (exact model id -> extra params). Overrides provider defaults.
    model_extra_params: dict[str, dict[str, object]] = Field(
        default_factory=lambda: {
            # Claude 4.6 Opus/Sonnet: Use reasoning.max_tokens (effort is ignored - they use adaptive)
            # See: https://openrouter.ai/docs/guides/model-migrations/claude-4-6
            "openrouter/anthropic/claude-opus-4.6": {
                "reasoning": {"max_tokens": 16000},
                "verbosity": "max",
            },
            "openrouter/anthropic/claude-sonnet-4.6": {
                "reasoning": {"max_tokens": 16000},
                "verbosity": "max",
            },
        }
    )

    # Legacy provider settings (for backward compatibility only)

    # Brave Search API (Web search)
    brave_api_key: str | None = None

    # ===== FETCH SERVICE (Web content fetching) =====
    # Jina AI API key for web fetching (optional)
    jina_api_key: str | None = None
    # Cache TTL for fetched content in seconds (default: 86400 = 24 hours)
    fetch_cache_ttl_seconds: int = 86400
    # Minimum content length to consider valid (default: 200 chars)
    fetch_min_content_length: int = 200
    # Crawl4AI service URL (default: docker-compose service)
    crawl4ai_url: str = "http://crawl4ai:11235"
    # Comma-separated list of blocked domains (default: empty)
    fetch_blocked_domains: str = ""

    # xAI API (for Imagine image/video generation)
    xai_api_key: str = ""

    voyage_api_key: str | None = None
    embedding_document_model: str = "voyage-4-large"
    embedding_query_model: str = "voyage-4-lite"
    embedding_dimensions: int = 1024

    # ===== DEDUP THRESHOLDS =====
    # Calibrated from `tests/results/voyage_similarity_analysis.json` for Voyage
    # embeddings: within-scenario max=0.8374/p95=0.6621, cross-scenario
    # max=0.8046/p95=0.6080, all-pairs p95=0.6263. Bands: merge >= 0.90,
    # generic supersede >= 0.82, slot-constrained supersede >= 0.65,
    # otherwise insert as new memory.
    dedup_merge_threshold: float = Field(
        default=0.90,
        description="Voyage merge floor for near-duplicates, set above the observed within-scenario max",
    )
    dedup_supersede_threshold: float = Field(
        default=0.82,
        description="Voyage generic supersede floor, raised above the dense cross-scenario similarity band",
    )
    dedup_supersede_same_slot_threshold: float = Field(
        default=0.65,
        description="Lower Voyage supersede floor for slot-constrained matches before falling back to insert",
    )

    openai_api_key: str | None = None

    # ===== MEMORY LAYER =====
    database_url: str | None = None
    redis_url: str | None = None
    daemon_encryption_key: str | None = None

    # ===== TITLE GENERATION =====
    title_model: str = "openrouter/openai/gpt-4o-mini"

    # ===== BACKGROUND REASONING =====
    # Model used for background reasoning tasks (e.g., contradiction detection)
    background_reasoning_model: str = "openrouter/deepseek/deepseek-chat"

    # ===== DREAMING =====
    dreaming_enabled: bool = True
    dream_schedule_hour: int = Field(default=3, ge=0, le=23)
    dream_min_cluster_size: int = Field(default=5, ge=1)

    # ===== RETRIEVAL LOGGING =====
    # Force retrieval trajectory logging on for all retrieval calls.
    # Automatically enabled when running LongMemEval benchmarks.
    retrieval_logging_enabled: bool = False
    # Additional debug flag: also enables verbose retrieval debug logging.
    retrieval_logging_debug: bool = False

    # ===== MEMORY CONSOLIDATION =====
    consolidation_enabled: bool = True
    consolidation_interval_days: int = 7

    # ===== SKILL CONSOLIDATION NUDGE =====
    consolidation_nudge_enabled: bool = True
    # Conversation interval that triggers consolidation nudge job per user
    consolidation_nudge_conversation_interval: int = Field(
        default=15,
        ge=1,
        description="Number of conversations between consolidation nudge runs per user",
    )
    # Stale threshold: autonomous skills not used for this many days are flagged
    consolidation_nudge_stale_days: int = Field(
        default=30,
        ge=1,
        description="Days of inactivity before an autonomous skill is considered stale",
    )
    # Minimum autonomous skills before consolidation nudge runs
    consolidation_nudge_min_skills: int = Field(
        default=3,
        ge=1,
        description="Minimum autonomous skills needed before consolidation nudge evaluates merge potential",
    )

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

        # Set video access controls based on tier
        if tier_name == "free":
            tier_video_enabled = False
            tier_video_max_duration = 0
            tier_video_credit_discount = 0.0
        elif tier_name == "starter":
            tier_video_enabled = True
            tier_video_max_duration = None
            tier_video_credit_discount = 1.0
        elif tier_name == "pro":
            tier_video_enabled = True
            tier_video_max_duration = None
            tier_video_credit_discount = 1.0
        elif tier_name == "max":
            tier_video_enabled = True
            tier_video_max_duration = None
            tier_video_credit_discount = 0.8
        elif tier_name == "byok":
            tier_video_enabled = True
            tier_video_max_duration = None
            tier_video_credit_discount = 0.0
        else:
            # Default fallback
            tier_video_enabled = False
            tier_video_max_duration = 0
            tier_video_credit_discount = 0.0

        return TierConfig(
            orchestrator=get_slot_config("orchestrator")
            or ModelSlotConfig(model="openrouter/moonshotai/kimi-k2.5", temperature=0.7),
            research_agent=get_slot_config("research"),
            code_agent=get_slot_config("code"),
            image_agent=get_slot_config("image"),
            reader_agent=get_slot_config("reader"),
            embeddings=get_slot_config("embeddings"),
            tier_video_enabled=tier_video_enabled,
            tier_video_max_duration=tier_video_max_duration,
            tier_video_credit_discount=tier_video_credit_discount,
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
                timeout_s=getattr(self, f"{prefix.lower()}timeout_s", self.request_timeout_s),
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

    def list_available_tiers(self) -> list[dict[str, str | int]]:
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
