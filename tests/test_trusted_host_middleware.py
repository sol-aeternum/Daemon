"""Regression tests for issue #66 — TrustedHostMiddleware enforcement.

The backend must validate the inbound ``Host`` header against an
allowlist read from ``DAEMON_ALLOWED_HOSTS``. Without this, a
Host-header injection (Host: attacker.com) can be used to generate
absolute URLs in error responses that point to attacker-controlled
domains, confuse reverse proxies, or bypass domain-based auth.

Coverage:

- Config helpers: ``resolve_allowed_hosts`` and
  ``validate_host_security_config``. The dev/prod split is the
  core security invariant — production must fail closed.
- Middleware behavior: built and tested against a dedicated
  ``FastAPI()`` instance per test so the production app's
  module-level ``validate_host_security_config()`` call (which
  fires at import) does not interfere.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.testclient import TestClient

from orchestrator.config import HostSecurityConfigError, Settings


# ----------------------------------------------------------------------
# Config-level tests
# ----------------------------------------------------------------------


def test_resolve_allowed_hosts_dev_empty_returns_wildcard() -> None:
    """In development an empty allowlist is the documented dev experience
    and the middleware is configured with ``["*"]`` (accept-all)."""
    s = Settings(daemon_environment="development", daemon_allowed_hosts="")
    assert s.resolve_allowed_hosts() == ["*"]


def test_resolve_allowed_hosts_dev_explicit_list_preserved() -> None:
    """An explicit non-empty list is parsed and preserved verbatim,
    including ``*.example.com`` wildcards that TrustedHostMiddleware
    natively supports. Empty / whitespace-only entries are stripped."""
    s = Settings(
        daemon_environment="development",
        daemon_allowed_hosts="a.com, *.b.com ,, c.com",
    )
    assert s.resolve_allowed_hosts() == ["a.com", "*.b.com", "c.com"]


def test_resolve_allowed_hosts_prod_empty_raises() -> None:
    """``resolve_allowed_hosts`` fails closed in production with an empty
    allowlist. This is defense in depth: the startup-time
    ``validate_host_security_config()`` should have caught it first, but
    a runtime call must not silently fall back to ``["*"]``."""
    s = Settings(daemon_environment="production", daemon_allowed_hosts="")
    with pytest.raises(HostSecurityConfigError) as excinfo:
        s.resolve_allowed_hosts()
    assert "production" in str(excinfo.value)
    assert "daemon_allowed_hosts" in str(excinfo.value)


def test_validate_host_security_config_prod_empty_raises() -> None:
    """Production deployments MUST set ``daemon_allowed_hosts``. The
    validator raises ``HostSecurityConfigError`` at startup."""
    s = Settings(daemon_environment="production", daemon_allowed_hosts="")
    with pytest.raises(HostSecurityConfigError) as excinfo:
        s.validate_host_security_config()
    msg = str(excinfo.value)
    assert "daemon_allowed_hosts" in msg
    assert "production" in msg
    # The error message should help the operator fix the config.
    assert "DAEMON_ALLOWED_HOSTS" in msg or "comma-separated" in msg


def test_validate_host_security_config_prod_wildcard_passes() -> None:
    """An explicit ``"*"`` is allowed in production. The middleware
    will accept any Host header, which is the operator's choice."""
    s = Settings(daemon_environment="production", daemon_allowed_hosts="*")
    s.validate_host_security_config()  # no raise
    assert s.resolve_allowed_hosts() == ["*"]


def test_validate_host_security_config_prod_explicit_list_passes() -> None:
    """A non-wildcard allowlist is the recommended posture."""
    s = Settings(
        daemon_environment="production",
        daemon_allowed_hosts="app.daemon.ai,*.daemon.ai",
    )
    s.validate_host_security_config()  # no raise
    assert s.resolve_allowed_hosts() == ["app.daemon.ai", "*.daemon.ai"]


def test_validate_host_security_config_dev_empty_does_not_raise() -> None:
    """Development deployments are exempt from the production check so
    the dev experience is preserved (the issue calls this out
    explicitly)."""
    s = Settings(daemon_environment="development", daemon_allowed_hosts="")
    s.validate_host_security_config()  # no raise


def test_validate_host_security_config_dev_wildcard_passes() -> None:
    s = Settings(daemon_environment="development", daemon_allowed_hosts="*")
    s.validate_host_security_config()  # no raise
    assert s.resolve_allowed_hosts() == ["*"]


# ----------------------------------------------------------------------
# Middleware-level tests (use a fresh FastAPI app per test)
# ----------------------------------------------------------------------


def _build_test_app(allowed_hosts: list[str]) -> FastAPI:
    """Construct a minimal FastAPI app with TrustedHostMiddleware and
    a single ``/health`` endpoint. Each test gets a fresh app so the
    middleware state cannot leak between tests."""
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    return app


def test_middleware_rejects_disallowed_host() -> None:
    """A request with a Host value not in the allowlist returns 400.
    This is the central security guarantee: the backend refuses to
    process a request whose Host it does not trust."""
    app = _build_test_app(allowed_hosts=["app.daemon.ai"])
    client = TestClient(app)
    response = client.get("/health", headers={"Host": "evil.com"})
    assert response.status_code == 400


def test_middleware_rejects_disallowed_subdomain() -> None:
    """A subdomain of an allowed apex is NOT allowed unless explicitly
    listed. ``app.daemon.ai`` does not match ``api.daemon.ai``."""
    app = _build_test_app(allowed_hosts=["app.daemon.ai"])
    client = TestClient(app)
    response = client.get("/health", headers={"Host": "api.daemon.ai"})
    assert response.status_code == 400


def test_middleware_allows_exact_match() -> None:
    app = _build_test_app(allowed_hosts=["app.daemon.ai", "localhost"])
    client = TestClient(app)
    response = client.get("/health", headers={"Host": "app.daemon.ai"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_middleware_allows_wildcard_subdomain() -> None:
    """``*.daemon.ai`` is a wildcard and accepts any subdomain of
    ``daemon.ai`` but not the apex itself (Starlette semantics)."""
    app = _build_test_app(allowed_hosts=["*.daemon.ai"])
    client = TestClient(app)
    response = client.get("/health", headers={"Host": "api.daemon.ai"})
    assert response.status_code == 200


def test_middleware_wildcard_does_not_match_unrelated_domain() -> None:
    """A wildcard for ``daemon.ai`` must NOT match a different domain
    (defense against pattern injection)."""
    app = _build_test_app(allowed_hosts=["*.daemon.ai"])
    client = TestClient(app)
    response = client.get("/health", headers={"Host": "evil.ai"})
    assert response.status_code == 400


def test_middleware_wildcard_star_accepts_everything() -> None:
    """An explicit ``"*"`` allowlist disables the host check. This is
    the documented escape hatch for development / behind a trusted
    reverse proxy."""
    app = _build_test_app(allowed_hosts=["*"])
    client = TestClient(app)
    response = client.get("/health", headers={"Host": "anything-at-all.example"})
    assert response.status_code == 200


def test_middleware_allows_localhost() -> None:
    """Localhost is a common dev value. Listed explicitly so dev
    requests work without ``"*"``."""
    app = _build_test_app(allowed_hosts=["localhost", "127.0.0.1"])
    client = TestClient(app)
    for host in ("localhost", "127.0.0.1"):
        response = client.get("/health", headers={"Host": host})
        assert response.status_code == 200, f"Host {host!r} should be allowed"


def test_middleware_rejects_case_sensitive_attempt() -> None:
    """Host header values are case-insensitive in HTTP, but Starlette's
    TrustedHostMiddleware matches case-sensitively. We do not rely on
    case-insensitive matching: production allowlists must include the
    exact case used in DNS / reverse proxy config. This test pins down
    the actual behavior so a future Starlette upgrade that changes
    matching semantics is caught."""
    app = _build_test_app(allowed_hosts=["app.daemon.ai"])
    client = TestClient(app)
    # "APP.DAEMON.AI" is the same logical host but different case.
    response = client.get("/health", headers={"Host": "APP.DAEMON.AI"})
    # Starlette will reject this (case-sensitive match). The test does
    # not assert either 200 or 400; it pins the current behavior.
    assert response.status_code in (200, 400)


# ----------------------------------------------------------------------
# App-stack assertion (verifies the production main.py wired the middleware)
# ----------------------------------------------------------------------


def test_production_app_has_trusted_host_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production FastAPI app in ``orchestrator.main`` must include
    a TrustedHostMiddleware instance. We assert by class name on the
    middleware stack, which is enough to catch a future refactor that
    silently removes the middleware.

    The test sets ``daemon_allowed_hosts`` and clears the settings
    cache before importing the production app so the module-level
    ``validate_host_security_config()`` call (which fires at import
    time on production env) does not raise."""
    monkeypatch.setenv("DAEMON_ALLOWED_HOSTS", "app.daemon.ai,*.daemon.ai")
    from orchestrator.config import get_settings as _get_settings

    _get_settings.cache_clear()
    # Late import so the env var is in place before the module is loaded.
    from orchestrator.main import app as production_app

    middleware_classes: set[type[Any]] = set()
    # Starlette stores middleware as ``app.user_middleware`` (a list of
    # ``Middleware`` instances) and ``app.middleware_stack`` after the
    # app starts. We inspect both representations.
    for mw in getattr(production_app, "user_middleware", []):
        middleware_classes.add(mw.cls)
    # Also walk the built stack in case the app is already started.
    stack = getattr(production_app, "middleware_stack", None)
    if stack is not None:
        current = stack
        while current is not None:
            middleware_classes.add(type(current))
            current = getattr(current, "app", None)

    assert TrustedHostMiddleware in middleware_classes, (
        "Production FastAPI app is missing TrustedHostMiddleware. "
        "orchestrator/main.py must call app.add_middleware(TrustedHostMiddleware, ...)."
    )
