from __future__ import annotations

import pytest

from orchestrator.auth_cookies import (
    COOKIE_NAME,
    INSECURE_DEVELOPMENT_COOKIE_NAME,
    CookiePolicyError,
    build_refresh_cookie,
    clear_refresh_cookie,
    get_refresh_cookie_name,
    make_refresh_cookie_config,
)
from orchestrator.auth_csrf import (
    CSRFGuardResult,
    OriginStatus,
    check_csrf_origin,
)
from orchestrator.auth_pepper import (
    MIN_PEPPER_CHARS,
    PepperValidationError,
    is_development_environment,
    is_production_environment,
    validate_and_get_pepper,
)
from orchestrator.config import Settings


class TestRefreshCookieConfig:
    def test_cookie_name_is_exact(self):
        assert COOKIE_NAME == "__Host-daemon_refresh"

    def test_production_cookie_is_secure(self):
        config = make_refresh_cookie_config(
            cookie_secure=True, environment="production"
        )
        assert config.name == "__Host-daemon_refresh"
        assert config.http_only is True
        assert config.secure is True
        assert config.same_site == "Strict"
        assert config.path == "/"

    def test_development_insecure_cookie_uses_unprefixed_name(self):
        config = make_refresh_cookie_config(
            cookie_secure=False, environment="development"
        )
        assert config.name == INSECURE_DEVELOPMENT_COOKIE_NAME
        assert (
            get_refresh_cookie_name(cookie_secure=False, environment="development")
            == "daemon_refresh"
        )
        assert config.http_only is True
        assert config.secure is False
        assert config.same_site == "Strict"
        assert config.path == "/"

    def test_development_secure_cookie_true(self):
        config = make_refresh_cookie_config(
            cookie_secure=True, environment="development"
        )
        assert config.secure is True

    def test_production_insecure_cookie_rejected(self):
        with pytest.raises(CookiePolicyError) as exc_info:
            make_refresh_cookie_config(cookie_secure=False, environment="production")
        assert "not allowed in production" in str(exc_info.value)


class TestBuildRefreshCookie:
    def test_production_cookie_string(self):
        config = make_refresh_cookie_config(
            cookie_secure=True, environment="production"
        )
        result = build_refresh_cookie("test_refresh_token_value", config)
        cookie = result["Set-Cookie"]
        assert "__Host-daemon_refresh=test_refresh_token_value" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=Strict" in cookie
        assert "Path=/" in cookie
        assert "Domain=" not in cookie

    def test_development_insecure_cookie_omits_secure(self):
        config = make_refresh_cookie_config(
            cookie_secure=False, environment="development"
        )
        result = build_refresh_cookie("test_refresh_token_value", config)
        cookie = result["Set-Cookie"]
        assert "daemon_refresh=test_refresh_token_value" in cookie
        assert "Secure" not in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Path=/" in cookie
        assert "Domain=" not in cookie

    def test_cookie_with_max_age(self):
        config = make_refresh_cookie_config(
            cookie_secure=True, environment="production"
        )
        result = build_refresh_cookie("token", config, max_age=86400)
        assert "Max-Age=86400" in result["Set-Cookie"]


class TestClearRefreshCookie:
    def test_clear_cookie_has_max_age_zero(self):
        config = make_refresh_cookie_config(
            cookie_secure=True, environment="production"
        )
        result = clear_refresh_cookie(config)
        assert "Max-Age=0" in result["Set-Cookie"]
        assert "HttpOnly" in result["Set-Cookie"]


class TestCSRFOriginGuard:
    def test_cross_site_rejected(self):
        result = check_csrf_origin(
            request_origin="https://evil.example",
            sec_fetch_site="cross-site",
            referer=None,
            allowed_origins=["https://app.daemon.ai"],
            public_origin="https://app.daemon.ai",
            has_cookie=True,
        )
        assert result.allowed is False
        assert result.status == OriginStatus.INVALID

    def test_same_origin_allowed(self):
        result = check_csrf_origin(
            request_origin="https://app.daemon.ai",
            sec_fetch_site="same-origin",
            referer=None,
            allowed_origins=["https://app.daemon.ai"],
            public_origin="https://app.daemon.ai",
            has_cookie=True,
        )
        assert result.allowed is True
        assert result.status == OriginStatus.VALID

    def test_none_sec_fetch_site_with_cookie_requires_origin(self):
        result = check_csrf_origin(
            request_origin=None,
            sec_fetch_site="none",
            referer=None,
            allowed_origins=["https://app.daemon.ai"],
            public_origin="https://app.daemon.ai",
            has_cookie=True,
        )
        assert result.allowed is True

    def test_no_cookie_no_origin_allowed(self):
        result = check_csrf_origin(
            request_origin=None,
            sec_fetch_site=None,
            referer=None,
            allowed_origins=["https://app.daemon.ai"],
            public_origin="https://app.daemon.ai",
            has_cookie=False,
        )
        assert result.allowed is True
        assert result.status == OriginStatus.MISSING

    def test_null_origin_rejected(self):
        result = check_csrf_origin(
            request_origin="null",
            sec_fetch_site=None,
            referer=None,
            allowed_origins=["https://app.daemon.ai"],
            public_origin="https://app.daemon.ai",
            has_cookie=True,
        )
        assert result.allowed is False
        assert result.status == OriginStatus.NULL_ORIGIN

    def test_cookie_without_origin_rejected(self):
        result = check_csrf_origin(
            request_origin=None,
            sec_fetch_site=None,
            referer=None,
            allowed_origins=["https://app.daemon.ai"],
            public_origin="https://app.daemon.ai",
            has_cookie=True,
        )
        assert result.allowed is False
        assert result.status == OriginStatus.MISSING

    def test_origin_in_allowed_list(self):
        result = check_csrf_origin(
            request_origin="https://app.daemon.ai",
            sec_fetch_site=None,
            referer=None,
            allowed_origins=["https://app.daemon.ai", "https://staging.daemon.ai"],
            public_origin=None,
            has_cookie=True,
        )
        assert result.allowed is True

    def test_origin_not_in_allowed_list(self):
        result = check_csrf_origin(
            request_origin="https://evil.example",
            sec_fetch_site=None,
            referer=None,
            allowed_origins=["https://app.daemon.ai"],
            public_origin=None,
            has_cookie=True,
        )
        assert result.allowed is False

    def test_referer_fallback_allowed(self):
        result = check_csrf_origin(
            request_origin=None,
            sec_fetch_site=None,
            referer="https://app.daemon.ai/some/page",
            allowed_origins=["https://app.daemon.ai"],
            public_origin=None,
            has_cookie=True,
        )
        assert result.allowed is True
        assert "Referer" in result.reason

    def test_referer_fallback_not_in_allowed_list(self):
        result = check_csrf_origin(
            request_origin=None,
            sec_fetch_site=None,
            referer="https://evil.example/page",
            allowed_origins=["https://app.daemon.ai"],
            public_origin=None,
            has_cookie=True,
        )
        assert result.allowed is False

    def test_public_origin_validates_when_allowed_origins_empty(self):
        result = check_csrf_origin(
            request_origin="https://app.daemon.ai",
            sec_fetch_site=None,
            referer=None,
            allowed_origins=[],
            public_origin="https://app.daemon.ai",
            has_cookie=True,
        )
        assert result.allowed is True
        assert result.status == OriginStatus.VALID


class TestCORSDenyByDefault:
    def test_empty_origins_string_parses_to_empty_list(self):
        settings = Settings(daemon_allowed_origins="")
        parsed = [
            o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()
        ]
        assert parsed == []

    def test_single_origin_parses_correctly(self):
        settings = Settings(daemon_allowed_origins="https://app.daemon.ai")
        parsed = [
            o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()
        ]
        assert parsed == ["https://app.daemon.ai"]

    def test_multiple_origins_parse_correctly(self):
        settings = Settings(
            daemon_allowed_origins="https://app.daemon.ai, https://staging.daemon.ai"
        )
        parsed = [
            o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()
        ]
        assert parsed == ["https://app.daemon.ai", "https://staging.daemon.ai"]

    def test_whitespace_only_origins_parses_to_empty(self):
        settings = Settings(daemon_allowed_origins="   ,  ")
        parsed = [
            o.strip() for o in settings.daemon_allowed_origins.split(",") if o.strip()
        ]
        assert parsed == []


class TestPepperValidation:
    def test_production_missing_pepper_raises(self):
        settings = Settings(
            daemon_environment="production",
            daemon_auth_pepper=None,
        )
        with pytest.raises(PepperValidationError) as exc_info:
            validate_and_get_pepper(settings)
        assert "required in production" in str(exc_info.value)

    def test_production_weak_pepper_raises(self):
        settings = Settings(
            daemon_environment="production",
            daemon_auth_pepper="too_short",
        )
        with pytest.raises(PepperValidationError) as exc_info:
            validate_and_get_pepper(settings)
        assert "too weak in production" in str(exc_info.value)
        assert str(MIN_PEPPER_CHARS) in str(exc_info.value)

    def test_production_strong_pepper_ok(self):
        strong_pepper = "x" * MIN_PEPPER_CHARS
        settings = Settings(
            daemon_environment="production",
            daemon_auth_pepper=strong_pepper,
        )
        result = validate_and_get_pepper(settings)
        assert result == strong_pepper

    def test_production_exactly_min_chars_ok(self):
        pepper = "A" * MIN_PEPPER_CHARS
        settings = Settings(
            daemon_environment="production",
            daemon_auth_pepper=pepper,
        )
        result = validate_and_get_pepper(settings)
        assert result == pepper

    def test_development_missing_pepper_generates_ephemeral(self, caplog):
        settings = Settings(
            daemon_environment="development",
            daemon_auth_pepper=None,
        )
        result = validate_and_get_pepper(settings)
        assert len(result) == MIN_PEPPER_CHARS
        assert "process-ephemeral" in caplog.text
        assert "invalid after restart" in caplog.text

    def test_development_present_pepper_ok(self):
        settings = Settings(
            daemon_environment="development",
            daemon_auth_pepper="my_dev_pepper",
        )
        result = validate_and_get_pepper(settings)
        assert result == "my_dev_pepper"

    def test_invalid_environment_raises(self):
        settings = Settings(
            daemon_environment="invalid",
            daemon_auth_pepper=None,
        )
        with pytest.raises(PepperValidationError) as exc_info:
            validate_and_get_pepper(settings)
        assert "must be 'production' or 'development'" in str(exc_info.value)


class TestEnvironmentHelpers:
    def test_is_production_true(self):
        settings = Settings(daemon_environment="production")
        assert is_production_environment(settings) is True

    def test_is_production_false_for_dev(self):
        settings = Settings(daemon_environment="development")
        assert is_production_environment(settings) is False

    def test_is_development_true(self):
        settings = Settings(daemon_environment="development")
        assert is_development_environment(settings) is True

    def test_is_development_false_for_prod(self):
        settings = Settings(daemon_environment="production")
        assert is_development_environment(settings) is False

    def test_environment_case_insensitive(self):
        settings_prod = Settings(daemon_environment="PRODUCTION")
        assert is_production_environment(settings_prod) is True

        settings_dev = Settings(daemon_environment="Development")
        assert is_development_environment(settings_dev) is True
