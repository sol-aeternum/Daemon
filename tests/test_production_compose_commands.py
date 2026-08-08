from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from orchestrator.config import Settings
from orchestrator.main import (
    UnsafeProductionServerConfigError,
    _validate_production_server_args,
)


ROOT = Path(__file__).resolve().parents[1]


def _compose_services() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return cast(dict[str, dict[str, Any]], data["services"])


def test_backend_compose_command_does_not_enable_reload() -> None:
    backend = _compose_services()["backend"]
    command = backend["command"]

    assert "--reload" not in command.split()


def test_frontend_compose_uses_production_start_command() -> None:
    frontend = _compose_services()["frontend"]

    assert frontend["command"] == "npm run start"
    assert "volumes" not in frontend


def test_frontend_dockerfile_builds_and_starts_production_server() -> None:
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text()

    assert "RUN npm ci" in dockerfile
    assert "ENV NODE_ENV=production" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert 'CMD ["npm", "run", "start"]' in dockerfile
    assert 'CMD ["npm", "run", "dev"]' not in dockerfile


def test_frontend_start_script_uses_next_start() -> None:
    package_json = json.loads((ROOT / "frontend" / "package.json").read_text())

    assert package_json["scripts"]["start"] == "next start"


def test_production_startup_rejects_uvicorn_reload() -> None:
    settings = Settings(daemon_environment="production")

    with pytest.raises(UnsafeProductionServerConfigError, match="--reload"):
        _validate_production_server_args(
            settings,
            ["uvicorn", "orchestrator.main:app", "--reload"],
        )


def test_development_startup_allows_uvicorn_reload() -> None:
    settings = Settings(daemon_environment="development")

    _validate_production_server_args(
        settings,
        ["uvicorn", "orchestrator.main:app", "--reload"],
    )


def test_production_startup_rejects_uvicorn_reload_with_whitespace_env() -> None:
    """DAEMON_ENVIRONMENT with surrounding whitespace must still trigger the
    production guard — consistent with the rest of the codebase, which strips
    whitespace before comparing to ``"production"``."""
    settings = Settings(daemon_environment="  production  ")

    with pytest.raises(UnsafeProductionServerConfigError, match="--reload"):
        _validate_production_server_args(
            settings,
            ["uvicorn", "orchestrator.main:app", "--reload"],
        )


def test_frontend_compose_passes_next_public_build_args() -> None:
    """`npm run build` (in the Dockerfile) runs at image build time, so
    NEXT_PUBLIC_* vars must be passed as ``build.args`` so they are inlined
    into the client bundle. Without these, the production image would bundle
    defaults that disagree with .env at runtime.

    ``NEXT_PUBLIC_API_URL`` must additionally be **env-interpolated** (with
    localhost as the default), so the deployed backend URL is configurable
    via .env at deploy time and not hardcoded into the production image."""
    frontend = _compose_services()["frontend"]
    args = frontend.get("build", {}).get("args", {})

    assert "NEXT_PUBLIC_API_URL" in args
    assert "NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE" in args
    assert "NEXT_PUBLIC_GOOGLE_CLIENT_ID" in args
    assert "NEXT_PUBLIC_EMAIL_ENABLED" in args

    # NEXT_PUBLIC_API_URL must be env-interpolated, not hardcoded to localhost.
    # Pattern: ${NEXT_PUBLIC_API_URL:-<default>}  (any non-empty default works).
    api_url = args["NEXT_PUBLIC_API_URL"]
    assert isinstance(api_url, str), (
        f"NEXT_PUBLIC_API_URL must be a string in build.args, got {type(api_url)!r}"
    )
    assert api_url.startswith("${NEXT_PUBLIC_API_URL:") and api_url.endswith("}"), (
        f"NEXT_PUBLIC_API_URL must be env-interpolated "
        f"${{NEXT_PUBLIC_API_URL:-<default>}} so the deployed backend URL "
        f"is configurable; got {api_url!r}"
    )
    # Default value must be non-empty so the build still succeeds when
    # the var is unset (CI, first-run, etc.).
    default = api_url[len("${NEXT_PUBLIC_API_URL:-") : -1]
    assert default, f"NEXT_PUBLIC_API_URL build arg must have a non-empty default, got {api_url!r}"


def test_frontend_dockerfile_declares_next_public_build_args() -> None:
    """The Dockerfile must declare each NEXT_PUBLIC_* var as an ARG + ENV so
    the build step inlines it into the client bundle."""
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text()

    for var in (
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE",
        "NEXT_PUBLIC_GOOGLE_CLIENT_ID",
        "NEXT_PUBLIC_EMAIL_ENABLED",
    ):
        assert f"ARG {var}" in dockerfile, (
            f"frontend/Dockerfile must declare ARG {var} so the build step "
            "sees it before npm run build inlines it into the client bundle."
        )
        assert f"ENV {var}" in dockerfile, (
            f"frontend/Dockerfile must set ENV {var} (from ARG) so it is "
            "available during npm run build."
        )
