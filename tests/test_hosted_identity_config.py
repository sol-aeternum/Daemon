"""Focused fail-closed validation tests for hosted identity config.

These tests cover the security-critical startup gates from the hosted
identity decision lock. The contract is: a misconfigured hosted deployment
must fail loudly at config validation, never silently run with a weakened
identity posture.
"""

from __future__ import annotations

import pytest

from orchestrator.config import (
    HOSTED_MAIL_SENDER_MODES,
    HOSTED_SIGNUP_MODES,
    HostedIdentityConfigError,
    Settings,
)


def _hosted_base(**overrides: object) -> Settings:
    """Construct a minimally-valid hosted identity Settings baseline.

    Caller can override any field to test failure paths.
    """
    defaults: dict[str, object] = {
        "daemon_hosted_identity_enabled": True,
        "daemon_environment": "development",
        "daemon_google_enabled": True,
        "daemon_google_client_id": "test-client-id.apps.googleusercontent.com",
        "daemon_email_enabled": True,
        "daemon_mail_sender_mode": "console",
        "redis_url": "redis://localhost:6379/0",
        "daemon_hosted_identity_require_redis": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_allowlists_exposed_as_module_constants() -> None:
    """The signup and mail-sender allowlists are exported for documentation."""
    assert "invite_only" in HOSTED_SIGNUP_MODES
    assert "open" in HOSTED_SIGNUP_MODES
    assert "disabled" in HOSTED_SIGNUP_MODES
    assert "console" in HOSTED_MAIL_SENDER_MODES
    assert "smtp" in HOSTED_MAIL_SENDER_MODES
    assert "disabled" in HOSTED_MAIL_SENDER_MODES


def test_development_with_console_sink_passes() -> None:
    """Dev console mail sink is allowed in development."""
    settings = _hosted_base(
        daemon_environment="development",
        daemon_mail_sender_mode="console",
    )
    settings.validate_hosted_identity_config()


def test_development_without_redis_passes_when_not_required() -> None:
    """Dev can run without Redis if the require-redis knob is off."""
    settings = _hosted_base(
        daemon_environment="development",
        redis_url=None,
        daemon_hosted_identity_require_redis=False,
    )
    settings.validate_hosted_identity_config()


def test_both_providers_disabled_fails() -> None:
    """At least one identity provider (Google or email) must be enabled."""
    settings = _hosted_base(
        daemon_google_enabled=False,
        daemon_email_enabled=False,
    )
    with pytest.raises(HostedIdentityConfigError, match="identity provider"):
        settings.validate_hosted_identity_config()


def test_google_enabled_without_client_id_fails() -> None:
    """Google provider requires a client ID when enabled."""
    settings = _hosted_base(daemon_google_client_id=None)
    with pytest.raises(HostedIdentityConfigError, match="client_id"):
        settings.validate_hosted_identity_config()


def test_google_disabled_does_not_require_client_id() -> None:
    """If Google is disabled, client ID is not required."""
    settings = _hosted_base(daemon_google_enabled=False)
    settings.validate_hosted_identity_config()


def test_production_requires_redis_url_when_knob_on() -> None:
    """Production hosted identity fails closed without Redis URL."""
    settings = _hosted_base(
        daemon_environment="production",
        redis_url=None,
    )
    with pytest.raises(HostedIdentityConfigError, match="redis_url"):
        settings.validate_hosted_identity_config()


def test_production_with_redis_passes() -> None:
    """Production hosted identity with Redis URL passes Redis gate."""
    settings = _hosted_base(
        daemon_environment="production",
        redis_url="redis://redis:6379/0",
        daemon_mail_sender_mode="smtp",
        daemon_mail_smtp_host="smtp.example.com",
    )
    settings.validate_hosted_identity_config()


def test_production_with_redis_require_knob_off_still_fails_other_rules() -> None:
    """Disabling the require-redis knob bypasses only the Redis gate; console sink still fails."""
    settings = _hosted_base(
        daemon_environment="production",
        redis_url=None,
        daemon_hosted_identity_require_redis=False,
        daemon_mail_sender_mode="console",
    )
    with pytest.raises(HostedIdentityConfigError, match="mail sender mode"):
        settings.validate_hosted_identity_config()


def test_production_with_console_mail_sink_fails() -> None:
    """Dev console mail sink is rejected in production."""
    settings = _hosted_base(
        daemon_environment="production",
        redis_url="redis://redis:6379/0",
        daemon_mail_sender_mode="console",
    )
    with pytest.raises(HostedIdentityConfigError, match="console"):
        settings.validate_hosted_identity_config()


def test_production_with_smtp_sink_passes() -> None:
    """SMTP sink is accepted in production (no host check here; that's runtime)."""
    settings = _hosted_base(
        daemon_environment="production",
        redis_url="redis://redis:6379/0",
        daemon_mail_sender_mode="smtp",
    )
    settings.validate_hosted_identity_config()


def test_production_with_disabled_sink_passes() -> None:
    """Disabled mail sink is accepted in production (operator opt-out)."""
    settings = _hosted_base(
        daemon_environment="production",
        redis_url="redis://redis:6379/0",
        daemon_mail_sender_mode="disabled",
    )
    settings.validate_hosted_identity_config()


def test_empty_audience_allowlist_entry_fails() -> None:
    """Audience allowlist must not contain empty comma-separated entries."""
    settings = _hosted_base(daemon_google_audience_allowlist="valid,,")
    with pytest.raises(HostedIdentityConfigError, match="audience_allowlist"):
        settings.validate_hosted_identity_config()


def test_audience_allowlist_with_valid_entries_passes() -> None:
    """Audience allowlist with valid comma-separated entries passes."""
    settings = _hosted_base(
        daemon_google_audience_allowlist="id-a.apps.googleusercontent.com,id-b.apps.googleusercontent.com",
    )
    settings.validate_hosted_identity_config()


def test_email_only_deployment_passes() -> None:
    """Email-only deployment (Google disabled) passes with a configured mail sink."""
    settings = _hosted_base(
        daemon_google_enabled=False,
        daemon_google_client_id=None,
    )
    settings.validate_hosted_identity_config()


def test_signup_mode_all_three_allowed() -> None:
    """All three signup modes pass validation when other constraints are met."""
    for mode in HOSTED_SIGNUP_MODES:
        settings = _hosted_base(daemon_signup_mode=mode)  # type: ignore[arg-type]
        settings.validate_hosted_identity_config()
