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
