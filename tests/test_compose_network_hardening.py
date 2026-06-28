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
    monkeypatch.setenv("DATABASE_URL", f"postgresql://daemon:{password}@postgres:5432/daemon")
    settings = Settings(daemon_environment="production")

    with pytest.raises(UnsafeDatabaseCredentialError, match="known default"):
        _validate_database_credentials(settings)


def test_production_startup_rejects_known_default_database_url_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
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
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://daemon:unique-local-secret-2026@postgres:5432/daemon"
    )
    settings = Settings(daemon_environment="production")

    _validate_database_credentials(settings)


def test_production_startup_rejects_unsafe_database_url_when_postgres_password_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A safe POSTGRES_PASSWORD must not mask an unsafe DATABASE_URL."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "unique-local-secret-2026")
    monkeypatch.setenv("DATABASE_URL", "postgresql://daemon:daemon@postgres:5432/daemon")
    settings = Settings(daemon_environment="production")

    with pytest.raises(UnsafeDatabaseCredentialError, match="DATABASE_URL uses a known default"):
        _validate_database_credentials(settings)


def test_production_startup_accepts_database_url_password_query_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncpg accepts ?password=... query options; the validator must inspect them."""
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(
        daemon_environment="production",
        database_url="postgresql://postgres:5432/daemon?user=daemon&password=unique-local-secret-2026",
    )

    _validate_database_credentials(settings)


def test_production_startup_rejects_known_default_database_url_password_query_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(
        daemon_environment="production",
        database_url="postgresql://postgres:5432/daemon?user=daemon&password=daemon",
    )

    with pytest.raises(UnsafeDatabaseCredentialError, match="DATABASE_URL uses a known default"):
        _validate_database_credentials(settings)


def test_production_startup_strips_daemon_environment_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DAEMON_ENVIRONMENT=' production ' (with surrounding whitespace) must be honored."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "daemon")
    settings = Settings(daemon_environment=" production ")

    with pytest.raises(UnsafeDatabaseCredentialError, match="known default"):
        _validate_database_credentials(settings)
