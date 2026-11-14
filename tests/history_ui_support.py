"""Test-only real self-hosted auth wiring, never production startup or overrides."""

from types import SimpleNamespace

from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from orchestrator.auth_runtime_state import SETUP_TOKEN_HASH_KEY, set_runtime_value
from orchestrator.auth_tokens import hash_token
from orchestrator.db import AppState
from orchestrator.routes import auth_config, auth_setup
from orchestrator.services.identity import rate_limit_dep
from orchestrator.services.restore_history.acceptance_host import create_isolated_ui_app


AUTH_ROUTES = {
    ("GET", "/v1/auth/config"),
    ("POST", "/v1/auth/setup"),
    ("POST", "/v1/auth/enroll/start"),
    ("POST", "/v1/auth/enroll/complete"),
    ("POST", "/v1/auth/refresh"),
    ("POST", "/v1/auth/cache/clear"),
}


async def actual_auth_app(s, monkeypatch, redis, origin):
    settings = s.settings
    settings.daemon_environment = "development"
    settings.daemon_deployment_mode = "self_hosted"
    settings.daemon_hosted_identity_enabled = False
    settings.daemon_cookie_secure = False
    settings.daemon_auth_pepper = "synthetic-pepper"
    settings.daemon_allowed_origins = origin
    settings.daemon_public_origin = origin
    settings.daemon_setup_token_file = str(s.path.parent / "synthetic-ui-setup-token")
    # Only configuration lookup is replaced. All credential verifiers, guards,
    # cookie/CSRF logic and dependencies execute their actual implementations.
    for module in (auth_setup, auth_config, rate_limit_dep):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    bootstrap = "synthetic-disposable-ui-setup"
    async with s.db.connection() as conn:
        await set_runtime_value(conn, SETUP_TOKEN_HASH_KEY, hash_token(bootstrap))
    app = create_isolated_ui_app(s.host)
    app.state.app_state = AppState(settings=settings, db_pool=s.w, redis=redis)
    installed = set()
    for router in (auth_setup.router, auth_config.router):
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in getattr(route, "methods", set()):
                if (method, route.path) in AUTH_ROUTES:
                    app.router.routes.append(route)
                    installed.add((method, route.path))
    assert installed == AUTH_ROUTES
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Daemon-Client-Features",
        ],
        expose_headers=["X-Daemon-Task-Id"],
    )
    return SimpleNamespace(app=app, bootstrap=bootstrap, settings=settings)
