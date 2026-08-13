from __future__ import annotations

from orchestrator.config import Settings


def test_background_reasoning_model_default() -> None:
    """Test BACKGROUND_REASONING_MODEL default value."""
    settings = Settings()
    assert settings.background_reasoning_model == "openrouter/deepseek/deepseek-chat"


def test_background_reasoning_model_env_override() -> None:
    """Test BACKGROUND_REASONING_MODEL can be overridden via env var."""
    settings = Settings(background_reasoning_model="openrouter/anthropic/claude-3.5-sonnet")
    assert settings.background_reasoning_model == "openrouter/anthropic/claude-3.5-sonnet"


def test_background_reasoning_model_from_env(monkeypatch) -> None:
    """Test BACKGROUND_REASONING_MODEL loaded from env var."""
    monkeypatch.setenv("BACKGROUND_REASONING_MODEL", "openrouter/google/gemini-2.5-flash")
    settings = Settings()
    assert settings.background_reasoning_model == "openrouter/google/gemini-2.5-flash"


def test_dreaming_flags_defaults() -> None:
    settings = Settings()
    assert settings.dreaming_enabled is True
    assert settings.dream_schedule_hour == 3
    assert settings.dream_min_cluster_size == 5


def test_dreaming_flags_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DREAMING_ENABLED", "true")
    monkeypatch.setenv("DREAM_SCHEDULE_HOUR", "5")
    monkeypatch.setenv("DREAM_MIN_CLUSTER_SIZE", "6")
    settings = Settings()
    assert settings.dreaming_enabled is True
    assert settings.dream_schedule_hour == 5
    assert settings.dream_min_cluster_size == 6


def test_retrieval_logging_flags_defaults() -> None:
    """Test retrieval_logging_enabled and retrieval_logging_debug defaults are False."""
    settings = Settings()
    assert settings.retrieval_logging_enabled is False
    assert settings.retrieval_logging_debug is False


def test_retrieval_logging_flags_from_env(monkeypatch) -> None:
    """Test retrieval_logging flags can be overridden via env vars."""
    monkeypatch.setenv("RETRIEVAL_LOGGING_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_LOGGING_DEBUG", "true")
    settings = Settings()
    assert settings.retrieval_logging_enabled is True
    assert settings.retrieval_logging_debug is True


def test_retrieval_logging_debug_only_from_env(monkeypatch) -> None:
    """Test retrieval_logging_debug can be enabled without retrieval_logging_enabled."""
    monkeypatch.setenv("RETRIEVAL_LOGGING_DEBUG", "true")
    settings = Settings()
    assert settings.retrieval_logging_enabled is False
    assert settings.retrieval_logging_debug is True


def test_background_reasoning_model_whitespace_env_preserved(monkeypatch) -> None:
    """Test BACKGROUND_REASONING_MODEL preserves whitespace-only value as-is."""
    monkeypatch.setenv("BACKGROUND_REASONING_MODEL", "   ")
    settings = Settings()
    assert settings.background_reasoning_model == "   "


def test_background_reasoning_model_empty_string_preserved(monkeypatch) -> None:
    """Test BACKGROUND_REASONING_MODEL preserves empty string as-is."""
    monkeypatch.setenv("BACKGROUND_REASONING_MODEL", "")
    settings = Settings()
    assert settings.background_reasoning_model == ""


def test_hosted_identity_defaults() -> None:
    """Hosted identity is off by default; signup invite_only; mail sink console."""
    settings = Settings()
    assert settings.daemon_hosted_identity_enabled is False
    assert settings.daemon_signup_mode == "invite_only"
    assert settings.daemon_google_enabled is True
    assert settings.daemon_google_client_id is None
    assert settings.daemon_google_audience_allowlist == ""
    assert settings.daemon_email_enabled is True
    assert settings.daemon_mail_sender_mode == "console"
    assert settings.daemon_mail_from_address == "noreply@daemon.ai"
    assert settings.daemon_mail_smtp_host == ""
    assert settings.daemon_mail_smtp_port == 587
    assert settings.daemon_mail_smtp_use_tls is True
    assert settings.daemon_email_challenge_ttl_seconds == 600
    assert settings.daemon_email_challenge_max_attempts == 5
    assert settings.daemon_google_nonce_ttl_seconds == 600
    assert settings.daemon_rate_limit_email_start_per_email_per_hour == 3
    assert settings.daemon_rate_limit_email_start_per_email_per_day == 10
    assert settings.daemon_rate_limit_email_start_per_ip_per_hour == 5
    assert settings.daemon_rate_limit_email_start_per_ip_per_day == 20
    assert settings.daemon_rate_limit_email_complete_per_ip_per_hour == 20
    assert settings.daemon_private_refresh_ttl_days == 90
    assert settings.daemon_temporary_refresh_ttl_seconds == 0
    assert settings.daemon_hosted_identity_require_redis is True


def test_hosted_identity_env_overrides(monkeypatch) -> None:
    """Hosted identity settings are overridable via env vars."""
    monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("DAEMON_SIGNUP_MODE", "open")
    monkeypatch.setenv("DAEMON_GOOGLE_CLIENT_ID", "my-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("DAEMON_GOOGLE_AUDIENCE_ALLOWLIST", "id-a, id-b")
    monkeypatch.setenv("DAEMON_EMAIL_CHALLENGE_TTL_SECONDS", "900")
    monkeypatch.setenv("DAEMON_MAIL_SENDER_MODE", "smtp")
    settings = Settings()
    assert settings.daemon_hosted_identity_enabled is True
    assert settings.daemon_signup_mode == "open"
    assert settings.daemon_google_client_id == "my-client-id.apps.googleusercontent.com"
    assert settings.daemon_google_audience_allowlist == "id-a, id-b"
    assert settings.daemon_email_challenge_ttl_seconds == 900
    assert settings.daemon_mail_sender_mode == "smtp"


def test_signup_mode_invalid_rejected_by_literal() -> None:
    """Invalid daemon_signup_mode is rejected at construction time."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(daemon_signup_mode="free_for_all")  # type: ignore[arg-type]


def test_mail_sender_mode_invalid_rejected_by_literal() -> None:
    """Invalid daemon_mail_sender_mode is rejected at construction time."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(daemon_mail_sender_mode="sendmail")  # type: ignore[arg-type]


def test_email_challenge_ttl_bounds_enforced() -> None:
    """TTL field enforces ge/le bounds at construction time."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(daemon_email_challenge_ttl_seconds=10)
    with pytest.raises(ValidationError):
        Settings(daemon_email_challenge_ttl_seconds=10_000)


def test_email_challenge_attempts_bounds_enforced() -> None:
    """Attempts field enforces ge/le bounds at construction time."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(daemon_email_challenge_max_attempts=0)
    with pytest.raises(ValidationError):
        Settings(daemon_email_challenge_max_attempts=100)


def test_validate_hosted_identity_noop_when_disabled() -> None:
    """Validation is a no-op when hosted identity is disabled (self-hosted default)."""
    settings = Settings(
        daemon_hosted_identity_enabled=False,
        daemon_environment="production",
        redis_url=None,
        daemon_google_client_id=None,
        daemon_mail_sender_mode="console",
    )
    settings.validate_hosted_identity_config()


# ===== Issue #83 — direct os.environ.get bypasses Settings =====


def test_elevenlabs_api_key_default_none() -> None:
    """ELEVENLABS_API_KEY env var should default to None when unset."""
    settings = Settings()
    assert settings.elevenlabs_api_key is None


def test_elevenlabs_api_key_from_env(monkeypatch) -> None:
    """ELEVENLABS_API_KEY is read via Settings (not os.environ.get)."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-eleven-key")
    settings = Settings()
    assert settings.elevenlabs_api_key == "test-eleven-key"


def test_openrouter_image_model_default() -> None:
    """OPENROUTER_IMAGE_MODEL has a sensible default."""
    settings = Settings()
    assert settings.openrouter_image_model == "google/gemini-2.5-flash-image"


def test_openrouter_image_model_from_env(monkeypatch) -> None:
    """OPENROUTER_IMAGE_MODEL is overridable via Settings."""
    monkeypatch.setenv("OPENROUTER_IMAGE_MODEL", "google/gemini-3-flash")
    settings = Settings()
    assert settings.openrouter_image_model == "google/gemini-3-flash"


def test_fal_key_default_empty_string() -> None:
    """FAL_KEY has an empty-string default to match `or ""` fallback in image subagent."""
    settings = Settings()
    assert settings.fal_key == ""


def test_fal_key_from_env(monkeypatch) -> None:
    """FAL_KEY is read via Settings."""
    monkeypatch.setenv("FAL_KEY", "test-fal-key")
    settings = Settings()
    assert settings.fal_key == "test-fal-key"


def test_http_allowed_domains_default_empty() -> None:
    """DAEMON_HTTP_ALLOWED_DOMAINS has an empty default."""
    settings = Settings()
    assert settings.daemon_http_allowed_domains == ""


def test_http_allowed_domains_from_env(monkeypatch) -> None:
    """DAEMON_HTTP_ALLOWED_DOMAINS is read via Settings."""
    monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "example.com,*.trusted.org")
    settings = Settings()
    assert settings.daemon_http_allowed_domains == "example.com,*.trusted.org"


def test_fetch_allowed_content_types_default_empty() -> None:
    """FETCH_ALLOWED_CONTENT_TYPES has an empty default."""
    settings = Settings()
    assert settings.fetch_allowed_content_types == ""


def test_fetch_allowed_content_types_from_env(monkeypatch) -> None:
    """FETCH_ALLOWED_CONTENT_TYPES is read via Settings."""
    monkeypatch.setenv("FETCH_ALLOWED_CONTENT_TYPES", "text/html,application/json")
    settings = Settings()
    assert settings.fetch_allowed_content_types == "text/html,application/json"


def test_fetch_max_depth_default_none() -> None:
    """FETCH_MAX_DEPTH has None default (no override)."""
    settings = Settings()
    assert settings.fetch_max_depth is None


def test_fetch_max_depth_from_env(monkeypatch) -> None:
    """FETCH_MAX_DEPTH is read via Settings when env is set."""
    monkeypatch.setenv("FETCH_MAX_DEPTH", "3")
    settings = Settings()
    assert settings.fetch_max_depth == 3


def test_fetch_error_signatures_default_empty() -> None:
    """FETCH_ERROR_SIGNATURES has an empty default."""
    settings = Settings()
    assert settings.fetch_error_signatures == ""


def test_fetch_error_signatures_from_env(monkeypatch) -> None:
    """FETCH_ERROR_SIGNATURES is read via Settings."""
    monkeypatch.setenv("FETCH_ERROR_SIGNATURES", "not found,access denied")
    settings = Settings()
    assert settings.fetch_error_signatures == "not found,access denied"
