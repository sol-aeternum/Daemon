from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from orchestrator.config import Settings
from orchestrator.main import (
    UnsafeDatabaseCredentialError,
    _validate_database_credentials,
)


ROOT = Path(__file__).resolve().parents[1]


def _compose_services() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return cast(dict[str, dict[str, Any]], data["services"])


def test_database_ports_are_bound_to_loopback_only() -> None:
    services = _compose_services()

    assert services["postgres"]["ports"] == ["127.0.0.1:5432:5432"]
    assert services["redis"]["ports"] == ["127.0.0.1:6379:6379"]


def test_compose_requires_postgres_password_without_default() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}" in compose
    assert "POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-daemon}" not in compose
    assert "postgresql://daemon:daemon@" not in compose


def test_env_example_does_not_ship_default_database_password() -> None:
    env_example = (ROOT / ".env.example").read_text()

    assert "POSTGRES_PASSWORD=daemon" not in env_example
    assert "POSTGRES_PASSWORD=" in env_example
    assert "DATABASE_URL=postgresql://daemon:daemon@" not in env_example


@pytest.mark.parametrize("password", ["postgres", "password", "changeme", "daemon"])
def test_production_startup_rejects_known_default_postgres_password(
    monkeypatch: pytest.MonkeyPatch,
    password: str,
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    settings = Settings(daemon_environment="production")

    with pytest.raises(UnsafeDatabaseCredentialError, match="known default"):
        _validate_database_credentials(settings)


def test_production_startup_rejects_known_default_database_url_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    settings = Settings(
        daemon_environment="production",
        database_url="postgresql://daemon:daemon@postgres:5432/daemon",
    )

    with pytest.raises(UnsafeDatabaseCredentialError, match="known default"):
        _validate_database_credentials(settings)


def test_production_startup_allows_non_default_database_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "unique-local-secret-2026")
    settings = Settings(daemon_environment="production")

    _validate_database_credentials(settings)
