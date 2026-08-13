from __future__ import annotations

# pyright: reportMissingImports=false

from functools import lru_cache
from typing import ClassVar, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ===== Hosted identity config constants =====
# Allowlist of signup modes. invite_only is the default per hosted identity
# decision lock; open is gated behind a deliberate operator change; disabled
# blocks all new hosted signups but leaves existing accounts functional.
HOSTED_SIGNUP_MODES: tuple[str, ...] = ("invite_only", "open", "disabled")
# Allowlist of mail-sender modes. console is dev-only and is rejected in
# production by validate_hosted_identity_config().
HOSTED_MAIL_SENDER_MODES: tuple[str, ...] = ("console", "smtp", "disabled")


class HostedIdentityConfigError(ValueError):
    """Raised when hosted identity configuration fails fail-closed validation."""


class HostSecurityConfigError(ValueError):
    """Raised when the deployment is misconfigured for Host-header security.

    Mirrors HostedIdentityConfigError but covers the TrustedHostMiddleware
    allowlist. Production deployments must set ``daemon_allowed_hosts`` to a
    non-empty, non-wildcard value; otherwise the host-header attack surface
    is left open and the backend may generate absolute URLs that point to
    attacker-controlled domains.
    """


class InternalProxyConfigError(ValueError):
    """Raised when trusted internal proxy headers cannot be authenticated."""


def _strip_host_port(entry: str) -> str:
    """Drop a ``:port`` suffix from an allowlist entry.

    Starlette's TrustedHostMiddleware strips the port from the incoming
    Host header before comparing, so ports in configured entries can never
    match. Wildcard entries (``*``, ``*.example.com``) and bracketed IPv6
    literals are returned unchanged apart from bracket-aware port removal.
    """
    if entry.startswith("["):
        # [::1] or [::1]:8000 — keep the bracketed literal, drop the port.
        closing = entry.find("]")
        if closing != -1:
            return entry[: closing + 1]
        return entry
    return entry.split(":", 1)[0]


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

    daemon_admin_api_key: str | None = None

    daemon_max_grant_amount_per_request: int = 100
    daemon_min_grant_description_length: int = 5

    # Auth environment: "production" or "development".
    # Production requires strong pepper and rejects insecure cookies.
    daemon_environment: str = Field(default="production")

    # Auth pepper for enrollment code HMAC verification.
    # Production: must be ≥32 random bytes (≥43 base64url chars). Missing/weak = fails startup.
    # Development: if absent, a process-ephemeral pepper is generated with a warning.
    daemon_auth_pepper: str | None = None

    # Comma-separated list of allowed CORS origins. Default denies all cross-origin.
    daemon_allowed_origins: str = ""

    # Comma-separated list of allowed Host header values for TrustedHostMiddleware.
    # Supports `*.example.com` wildcards. Empty means "all hosts in development"
    # and is rejected at startup in production. Setting this to `*` explicitly
    # disables the host check (not recommended in production).
    daemon_allowed_hosts: str = ""

    # Public origin for CSRF origin validation (e.g., "https://app.daemon.ai").
    daemon_public_origin: str | None = None

    # Whether to set Secure flag on auth cookies.
    # Production: must be true (rejected if false).
    # Development: may be false when daemon_environment=development.
    daemon_cookie_secure: bool = True

    # Session cleanup: grace period in days after expiry/revocation before deletion.
    daemon_session_cleanup_grace_days: int = Field(default=7, ge=1)

    # Session cleanup: abort if one run would delete more than this fraction
    # of the sessions table once the table is large enough for the guard.
    daemon_session_cleanup_max_delete_fraction: float = Field(default=0.5, gt=0, le=1)

    # Session cleanup: interval in seconds between cleanup task runs.
    daemon_session_cleanup_interval_seconds: int = 86400

    # Local operator handoff for the one-time first-boot setup token.
    daemon_setup_token_file: str = ".daemon/setup-token"

    # Default provider to use when none specified in request
    default_provider: str = "openrouter"

    # Request and stream settings
    request_timeout_s: float = 90.0
    daemon_max_request_body_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    daemon_max_chat_body_bytes: int = Field(default=8 * 1024 * 1024, ge=1)
    daemon_max_stt_body_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    daemon_max_skill_upload_body_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    stream_ping_interval_s: float = Field(default=15.0, ge=0)
    daemon_sse_keepalive_interval_s: float | None = Field(default=None, ge=0)
    chat_history_limit: int = 50

    # Development fallback: stream a canned response without calling any provider.
    mock_llm: bool = False

    @property
    def sse_keepalive_interval_s(self) -> float:
        return (
            self.daemon_sse_keepalive_interval_s
            if self.daemon_sse_keepalive_interval_s is not None
            else self.stream_ping_interval_s
        )

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

    # ElevenLabs API key (TTS, STT, sound effects)
    elevenlabs_api_key: str | None = None

    # OpenRouter image model override (default: google/gemini-2.5-flash-image)
    openrouter_image_model: str = "google/gemini-2.5-flash-image"

    # Comma-separated egress allowlist applied by SSRF guard.
    # Empty/unset means no allowlist filter is applied (only the IP-range
    # check). Wildcards use the `*.example.com` form.
    daemon_http_allowed_domains: str = ""

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
    # Comma-separated list of MIME content types the fetcher will accept
    # (default: empty means "no additional allowlist restriction beyond the
    # caller-supplied `allowed_content_types`").
    fetch_allowed_content_types: str = ""
    # Maximum crawl depth passed to the fetcher when not overridden by the
    # caller. None means no maximum-depth constraint is applied.
    fetch_max_depth: int | None = None
    # Comma-separated substrings used to identify short error pages (e.g.
    # "not found", "access denied"). Empty means "no signature-based
    # rejection".
    fetch_error_signatures: str = ""

    # xAI API (for Imagine image/video generation)
    xai_api_key: str = ""

    # fal.ai API key (Kling video provider)
    fal_key: str = ""

    voyage_api_key: str | None = None
    embedding_document_model: str = "voyage-4-large"
    embedding_query_model: str = "voyage-4-lite"
    embedding_dimensions: int = 1024
    embedding_fallback_providers: str = ""
    embedding_openrouter_document_model: str = "voyageai/voyage-4-large"
    embedding_openrouter_query_model: str = "voyageai/voyage-4-lite"
    embedding_openai_fallback_model: str = "text-embedding-3-small"

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
    consolidation_interval_days: int = Field(
        default=7,
        description="Supported memory consolidation intervals: 1 (daily) or 7 (weekly).",
    )

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

    # ===== DEPLOYMENT MODE =====
    # User-facing concept: which landing page do we show? self_hosted
    # surfaces /setup (legacy owner/admin enrollment); hosted surfaces /auth
    # (Google/email sign-in). Defaults to self_hosted so a fresh clone never
    # accidentally exposes the hosted sign-in surface. This is orthogonal
    # to daemon_hosted_identity_enabled (a feature flag for whether the
    # identity routes are wired up at all) — a deployment with
    # mode=hosted + identity_enabled=false is a valid staging config; a
    # deployment with mode=self_hosted + identity_enabled=true is a valid
    # dev/QA config. Validated at construction time by the Literal.
    daemon_deployment_mode: Literal["self_hosted", "hosted"] = "self_hosted"

    # ===== HOSTED IDENTITY CONFIG =====
    # When false, hosted identity routes (Google/email) are not exposed and
    # validate_hosted_identity_config() is a no-op. Default off preserves the
    # self-hosted setup-first default; explicit opt-in is required.
    daemon_hosted_identity_enabled: bool = False

    # Signup policy. invite_only is the locked default per hosted identity
    # decision lock; open requires a deliberate operator change; disabled
    # blocks all new hosted signups but leaves existing accounts functional.
    daemon_signup_mode: Literal["invite_only", "open", "disabled"] = "invite_only"

    # ----- Google provider config -----
    daemon_google_enabled: bool = True
    # Required when Google is enabled and daemon_hosted_identity_enabled is true.
    # Validated by validate_hosted_identity_config().
    daemon_google_client_id: str | None = None
    # Comma-separated additional audience allowlist (dev/staging only). Empty
    # in production deployments. Each entry is validated as a non-empty string
    # by validate_hosted_identity_config().
    daemon_google_audience_allowlist: str = ""

    # ----- Email provider config -----
    daemon_email_enabled: bool = True

    # ----- Challenge defaults (TODO 10/12 consume these) -----
    # Email code challenge TTL in seconds. Research recommends 10 minutes.
    daemon_email_challenge_ttl_seconds: int = Field(default=600, ge=30, le=3600)
    # Email code max attempts per challenge row. Research recommends 5.
    daemon_email_challenge_max_attempts: int = Field(default=5, ge=1, le=10)
    # Google nonce challenge TTL in seconds. Matches email code TTL by default.
    daemon_google_nonce_ttl_seconds: int = Field(default=600, ge=30, le=3600)

    # ----- Rate-limit thresholds (TODO 7 consumes these) -----
    # Per-normalized-email limits for /email/start.
    daemon_rate_limit_email_start_per_email_per_hour: int = Field(default=3, ge=1)
    daemon_rate_limit_email_start_per_email_per_day: int = Field(default=10, ge=1)
    # Per-IP limits for /email/start.
    daemon_rate_limit_email_start_per_ip_per_hour: int = Field(default=5, ge=1)
    daemon_rate_limit_email_start_per_ip_per_day: int = Field(default=20, ge=1)
    # Per-IP limit for /email/complete.
    daemon_rate_limit_email_complete_per_ip_per_hour: int = Field(default=20, ge=1)

    # ----- Chat endpoint rate limits (issue #38) -----
    # Per-user limit on /chat and /v1/chat/completions (defends the
    # operator's LLM budget against compromised tokens or runaway clients).
    daemon_rate_limit_chat_per_user_per_minute: int = Field(default=60, ge=1)
    # Per-token limit (defense in depth against token rotation abuse and
    # multi-device bursts from a single leaked token).
    daemon_rate_limit_chat_per_token_per_minute: int = Field(default=30, ge=1)
    # Per-IP limit on authenticated chat requests (defense in depth — a
    # single IP should never burn more than this regardless of which user
    # is "logged in" from it).
    daemon_rate_limit_chat_per_ip_per_minute: int = Field(default=120, ge=1)

    # ----- Mail sender config (TODO 10 consumes this) -----
    # console is dev-only and is rejected in production by
    # validate_hosted_identity_config(). smtp uses daemon_mail_smtp_*.
    daemon_mail_sender_mode: Literal["console", "smtp", "disabled"] = "console"
    daemon_mail_from_address: str = "noreply@daemon.ai"
    # SMTP settings (only used when daemon_mail_sender_mode == "smtp").
    daemon_mail_smtp_host: str = ""
    daemon_mail_smtp_port: int = Field(default=587, ge=1, le=65535)
    daemon_mail_smtp_username: str = ""
    daemon_mail_smtp_password: str = ""
    daemon_mail_smtp_use_tls: bool = True
    daemon_worker_failure_alert_email: str = ""

    # ----- Native / web / temporary refresh transport knobs (TODO 9) -----
    # Long-lived refresh TTL for the web and native private persistence.
    daemon_private_refresh_ttl_days: int = Field(default=90, ge=1, le=365)
    # Temporary/public refresh TTL in seconds. 0 means session cookie
    # (cleared on browser close). TODO 9 picks the exact shape.
    daemon_temporary_refresh_ttl_seconds: int = Field(default=0, ge=0, le=86400)

    # ----- Hosted identity Redis requirements -----
    # When true, validate_hosted_identity_config() requires redis_url to be
    # set. This is the fail-closed knob for the decision lock's "Redis
    # unavailable in hosted production fails closed for identity
    # nonce/challenge/rate-limit enforcement" rule.
    daemon_hosted_identity_require_redis: bool = True

    # ----- Trusted auth-proxy forwarding -----
    # Default safe posture: do NOT trust forwarded client-IP headers.
    # Hosted deployments that front `/api/v1/auth/*` with the Next.js proxy
    # may opt in so rate limits key on the original client IP instead of the
    # proxy/container hop. The rate-limit helper still requires the immediate
    # socket hop to be loopback/private before honoring forwarded headers.
    daemon_trust_proxy_forwarded_client_ip: bool = False
    # Dedicated server-only secret shared by the Next.js proxy and backend.
    # It must never use a NEXT_PUBLIC_* name or reuse the auth pepper.
    daemon_internal_proxy_hmac_secret: str = ""

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

    def validate_deployment_mode(self) -> None:
        """Validate deployment-mode configuration.

        Today the Literal type-check at construction time already rejects
        any value outside {"self_hosted", "hosted"}; this method is the
        explicit fail-closed hook for future cross-field rules (e.g.,
        refusing mode=hosted without daemon_public_origin). Keeping it as
        a no-op when the value is well-formed makes the validation chain
        symmetric with validate_hosted_identity_config() and gives the
        lifespan hook a stable call site.

        Raises:
            HostedIdentityConfigError: on a current or future violation.
        """
        if self.daemon_deployment_mode not in ("self_hosted", "hosted"):
            raise HostedIdentityConfigError(
                f"daemon_deployment_mode must be one of ('self_hosted', 'hosted'), "
                f"got {self.daemon_deployment_mode!r}"
            )

    def validate_hosted_identity_config(self) -> None:
        """Validate hosted identity configuration and fail closed on misconfig.

        Returns silently when daemon_hosted_identity_enabled is False so
        self-hosted setup-first deployments are not impacted. When hosted
        identity is enabled, this enforces:

        - at least one of (Google, email) is enabled as an identity provider;
        - in production: Redis URL is set (fail-closed for nonce/challenge
          /rate-limit enforcement per the decision lock);
        - in production, when the email provider is enabled: the mail
          sender must be a real provider — daemon_mail_sender_mode must
          not be 'console' (dev sink), 'disabled' (no sender), or 'smtp'
          with an empty daemon_mail_smtp_host;
        - in production: Google provider (if enabled) has a client ID;
        - signup mode and mail sender mode are within their allowlists
          (Literal type checks at construction enforce this, but we
          double-check for runtime robustness against env-var drift);
        - the Google audience allowlist is a comma-separated list of
          non-empty strings (each entry trimmed).

        Production deployments where only the Google provider is enabled
        (daemon_email_enabled=False) intentionally allow non-real mail
        sinks (including 'disabled') because no email codes are ever
        generated or sent on that code path. The mail sink is irrelevant
        when email is disabled.

        Raises:
            HostedIdentityConfigError: on any of the above violations.
        """
        if not self.daemon_hosted_identity_enabled:
            return

        is_production = self.daemon_environment.lower().strip() == "production"

        if not self.daemon_google_enabled and not self.daemon_email_enabled:
            raise HostedIdentityConfigError(
                "Hosted identity is enabled but both Google and email providers "
                "are disabled. At least one identity provider must be enabled."
            )

        if is_production and self.daemon_hosted_identity_require_redis and not self.redis_url:
            raise HostedIdentityConfigError(
                "Hosted identity is enabled in production but redis_url is not "
                "set. Redis is required for hosted identity nonce/challenge/"
                "rate-limit enforcement; refusing to start with a weakened posture."
            )

        if is_production and self.daemon_email_enabled:
            if self.daemon_mail_sender_mode == "console":
                raise HostedIdentityConfigError(
                    "Hosted identity is enabled in production with the email "
                    "provider, but mail sender mode is 'console' (dev sink). "
                    "Configure daemon_mail_sender_mode=smtp with a real host "
                    "before deploying to production."
                )
            if self.daemon_mail_sender_mode == "disabled":
                raise HostedIdentityConfigError(
                    "Hosted identity is enabled in production with the email "
                    "provider, but mail sender mode is 'disabled'. The email "
                    "sign-in flow requires a real sender; refusing to start "
                    "with email codes that can never be delivered."
                )
            if self.daemon_mail_sender_mode == "smtp" and not self.daemon_mail_smtp_host.strip():
                raise HostedIdentityConfigError(
                    "Hosted identity is enabled in production with the email "
                    "provider and mail sender mode 'smtp', but "
                    "daemon_mail_smtp_host is empty. Configure a non-empty "
                    "daemon_mail_smtp_host before deploying to production."
                )
            if self.daemon_mail_sender_mode == "smtp" and not self.daemon_mail_from_address.strip():
                raise HostedIdentityConfigError(
                    "Hosted identity is enabled in production with the email "
                    "provider and mail sender mode 'smtp', but "
                    "daemon_mail_from_address is empty. Configure a non-empty "
                    "daemon_mail_from_address before deploying to production."
                )

        if self.daemon_google_enabled and not (self.daemon_google_client_id or "").strip():
            raise HostedIdentityConfigError(
                "Hosted identity is enabled with Google provider but "
                "daemon_google_client_id is not set. Set the Google OAuth client ID."
            )

        allowlist_raw = self.daemon_google_audience_allowlist or ""
        if is_production and allowlist_raw.strip() != "":
            raise HostedIdentityConfigError(
                "Hosted identity is enabled in production with Google provider, but "
                "daemon_google_audience_allowlist is non-empty. The audience allowlist "
                "is for development/staging only and must be empty in production."
            )
        if allowlist_raw != "":
            for entry in allowlist_raw.split(","):
                if entry.strip() == "":
                    raise HostedIdentityConfigError(
                        "daemon_google_audience_allowlist contains an empty entry. "
                        "Each comma-separated value must be a non-empty client ID."
                    )

        if self.daemon_signup_mode not in HOSTED_SIGNUP_MODES:
            raise HostedIdentityConfigError(
                f"daemon_signup_mode must be one of {HOSTED_SIGNUP_MODES}, "
                f"got: {self.daemon_signup_mode!r}"
            )

        if self.daemon_mail_sender_mode not in HOSTED_MAIL_SENDER_MODES:
            raise HostedIdentityConfigError(
                f"daemon_mail_sender_mode must be one of {HOSTED_MAIL_SENDER_MODES}, "
                f"got: {self.daemon_mail_sender_mode!r}"
            )

    def resolve_allowed_hosts(self) -> list[str]:
        """Return the list of Host-header values to allow.

        Development (``daemon_environment != "production"``): empty
        ``daemon_allowed_hosts`` is treated as "all hosts allowed" and
        the middleware is configured with ``["*"]``. Setting an explicit
        non-empty list narrows the allowlist even in development.

        Production: ``daemon_allowed_hosts`` MUST be set to a non-empty
        list (or to a single ``"*"`` if the operator accepts the risk).
        ``validate_host_security_config()`` enforces this at startup.

        Each entry is a string. ``TrustedHostMiddleware`` natively
        supports ``*.example.com`` wildcards, so we do not parse here.
        Empty entries (from a trailing comma) are stripped. Entries are
        lowercased (hostnames are case-insensitive) and any ``:port``
        suffix is dropped: Starlette compares the incoming Host with the
        port already stripped (``host.split(":")[0]``), so an entry like
        ``backend:8000`` copied from a real Host header would never
        match. A ``"*"`` mixed with other entries is rejected: Starlette
        treats any ``"*"`` in the list as "allow all hosts", which would
        silently disable the check the operator thought they had
        narrowed.
        """
        raw = self.daemon_allowed_hosts or ""
        entries = [
            _strip_host_port(entry.strip().lower()) for entry in raw.split(",") if entry.strip()
        ]
        entries = [entry for entry in entries if entry]
        if "*" in entries and len(entries) > 1:
            raise HostSecurityConfigError(
                "daemon_allowed_hosts mixes '*' with explicit hosts "
                f"({raw!r}). Starlette treats any '*' entry as 'allow all', "
                "silently disabling the host check. Use either an explicit "
                "allowlist or a single '*'."
            )
        if not entries:
            is_production = self.daemon_environment.lower().strip() == "production"
            if is_production:
                # Caller is expected to have called validate_host_security_config()
                # before reaching here. We still fail closed in case the caller
                # forgot.
                raise HostSecurityConfigError(
                    "daemon_allowed_hosts is empty in production. Set the env var "
                    "to a comma-separated list of allowed Host values (e.g. "
                    "'app.daemon.ai,*.daemon.ai') or to a single '*' to "
                    "explicitly opt out of the host check."
                )
            return ["*"]
        return entries

    def validate_host_security_config(self) -> None:
        """Validate Host-header security configuration and fail closed.

        Production deployments must set ``daemon_allowed_hosts`` to a
        non-empty list. A wildcard ``"*"`` is allowed but logs a warning
        at startup (see main.py); a non-wildcard allowlist is the
        recommended posture. Development deployments are not subject to
        this check (the dev experience is preserved).

        Raises:
            HostSecurityConfigError: on a production deployment with an
                empty allowlist.
        """
        raw = self.daemon_allowed_hosts or ""
        entries = [
            _strip_host_port(entry.strip().lower()) for entry in raw.split(",") if entry.strip()
        ]
        entries = [entry for entry in entries if entry]
        if "*" in entries and len(entries) > 1:
            raise HostSecurityConfigError(
                "daemon_allowed_hosts mixes '*' with explicit hosts "
                f"({raw!r}). Starlette treats any '*' entry as 'allow all', "
                "silently disabling the host check. Use either an explicit "
                "allowlist or a single '*'."
            )

        is_production = self.daemon_environment.lower().strip() == "production"
        if not is_production:
            return

        if not entries:
            raise HostSecurityConfigError(
                "daemon_allowed_hosts is empty in production. The backend "
                "would accept Host headers from any value, enabling URL "
                "confusion and phishing. Set the env var to a "
                "comma-separated list of allowed Host values (e.g. "
                "'app.daemon.ai,*.daemon.ai') or to a single '*' to "
                "explicitly opt out of the host check."
            )

    def validate_internal_proxy_config(self) -> None:
        """Require authenticated forwarding whenever proxy IP trust is enabled."""
        if not self.daemon_trust_proxy_forwarded_client_ip:
            return
        if len(self.daemon_internal_proxy_hmac_secret.strip()) < 32:
            raise InternalProxyConfigError(
                "daemon_trust_proxy_forwarded_client_ip requires "
                "daemon_internal_proxy_hmac_secret with at least 32 characters"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
