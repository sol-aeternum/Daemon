from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

import orchestrator.db as db_module
from orchestrator.config import Settings
from orchestrator.database_url import (
    UnsafeDatabaseCredentialError,
    apply_resolved_database_url,
    resolve_database_url,
    validate_database_credentials,
)


ROOT = Path(__file__).resolve().parents[1]
worker_module = importlib.import_module("orchestrator.worker.worker")


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


def test_compose_does_not_use_command_substitution_in_database_url() -> None:
    """Docker Compose interpolation does not support $(...), so URL-encoding must happen in the app."""
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "$(python" not in compose
    assert "$(echo" not in compose


def test_compose_passes_postgres_env_vars_for_app_side_resolution() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()

    for service in ("migrate", "backend", "worker"):
        assert "- POSTGRES_USER=${POSTGRES_USER:-daemon}" in compose
        assert "- POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}" in compose
        assert "- POSTGRES_HOST=postgres" in compose
        assert "- POSTGRES_DB=${POSTGRES_DB:-daemon}" in compose


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
        validate_database_credentials(settings)


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
        validate_database_credentials(settings)


def test_production_startup_allows_non_default_database_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "unique-local-secret-2026")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://daemon:unique-local-secret-2026@postgres:5432/daemon"
    )
    settings = Settings(daemon_environment="production")

    validate_database_credentials(settings)


def test_production_startup_rejects_unsafe_database_url_when_postgres_password_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A safe POSTGRES_PASSWORD must not mask an unsafe DATABASE_URL."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "unique-local-secret-2026")
    monkeypatch.setenv("DATABASE_URL", "postgresql://daemon:daemon@postgres:5432/daemon")
    settings = Settings(daemon_environment="production")

    with pytest.raises(UnsafeDatabaseCredentialError, match="DATABASE_URL uses a known default"):
        validate_database_credentials(settings)


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

    validate_database_credentials(settings)


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
        validate_database_credentials(settings)


def test_production_startup_strips_daemon_environment_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DAEMON_ENVIRONMENT=' production ' (with surrounding whitespace) must be honored."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "daemon")
    settings = Settings(daemon_environment=" production ")

    with pytest.raises(UnsafeDatabaseCredentialError, match="known default"):
        validate_database_credentials(settings)


def test_production_startup_rejects_repeated_password_query_options_last_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncpg uses the LAST value of repeated query keys; the validator must mirror this."""
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(
        daemon_environment="production",
        database_url="postgresql://postgres:5432/daemon?user=daemon&password=unique&password=daemon",
    )

    with pytest.raises(UnsafeDatabaseCredentialError, match="DATABASE_URL uses a known default"):
        validate_database_credentials(settings)


def test_production_startup_rejects_pgpassword_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncpg falls back to PGPASSWORD when the DSN has no userinfo/query password."""
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("PGPASSWORD", "daemon")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(
        daemon_environment="production",
        database_url="postgresql://daemon@postgres:5432/daemon",
    )

    with pytest.raises(UnsafeDatabaseCredentialError, match="PGPASSWORD uses a known default"):
        validate_database_credentials(settings)


def test_resolve_database_url_from_postgres_env_encodes_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URL-reserved characters in POSTGRES_PASSWORD must be percent-encoded."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "daemon")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/?#word")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "daemon")

    url = resolve_database_url()

    assert url == "postgresql://daemon:p%40ss%2F%3F%23word@postgres:5432/daemon"


def test_resolve_database_url_returns_explicit_database_url_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://daemon:secret@postgres:5432/daemon")
    monkeypatch.setenv("POSTGRES_USER", "ignored")
    monkeypatch.setenv("POSTGRES_PASSWORD", "ignored")
    monkeypatch.setenv("POSTGRES_HOST", "ignored")
    monkeypatch.setenv("POSTGRES_DB", "ignored")

    assert resolve_database_url() == "postgresql://daemon:secret@postgres:5432/daemon"


def test_resolve_database_url_returns_none_when_inputs_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    assert resolve_database_url() is None


def test_production_startup_rejects_postgres_env_derived_default_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The derived DATABASE_URL (from POSTGRES_* quartet) must also be checked."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "daemon")
    monkeypatch.setenv("POSTGRES_PASSWORD", "daemon")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "daemon")
    settings = Settings(daemon_environment="production")

    with pytest.raises(
        UnsafeDatabaseCredentialError, match="POSTGRES_PASSWORD uses a known default"
    ):
        validate_database_credentials(settings)


def test_apply_resolved_database_url_updates_settings_for_connection_entrypoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_USER", "daemon user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unique@secret")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "daemon/db")
    settings = Settings(database_url=None, daemon_environment="development")

    resolved = apply_resolved_database_url(settings)

    assert resolved == "postgresql://daemon%20user:unique%40secret@postgres:5432/daemon%2Fdb"
    assert settings.database_url == resolved


@pytest.mark.asyncio
async def test_backend_pool_uses_postgres_env_derived_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_USER", "daemon")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unique@secret")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "daemon")
    captured: dict[str, object] = {}

    async def fail_after_capture(**kwargs: object) -> None:
        captured.update(kwargs)
        raise OSError("expected test stop")

    monkeypatch.setattr(db_module.asyncpg, "create_pool", fail_after_capture)
    settings = Settings(
        database_url=None,
        redis_url=None,
        daemon_environment="development",
    )

    state = await db_module.init_app_state(settings)

    assert state.db_pool is None
    assert captured["dsn"] == "postgresql://daemon:unique%40secret@postgres:5432/daemon"


@pytest.mark.asyncio
async def test_worker_pool_uses_postgres_env_derived_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_USER", "daemon")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unique@secret")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "daemon")
    settings = Settings(database_url=None, daemon_environment="development")
    captured: dict[str, object] = {}

    async def fail_after_capture(**kwargs: object) -> None:
        captured.update(kwargs)
        raise OSError("expected test stop")

    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_module.asyncpg, "create_pool", fail_after_capture)

    with pytest.raises(OSError, match="expected test stop"):
        await worker_module.on_startup({})

    assert settings.database_url == "postgresql://daemon:unique%40secret@postgres:5432/daemon"
    assert captured["dsn"] == settings.database_url
