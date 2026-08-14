from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import parse_qs, quote, unquote, urlparse

from orchestrator.config import Settings


KNOWN_DEFAULT_POSTGRES_PASSWORDS = frozenset(
    {
        "postgres",
        "password",
        "changeme",
        "change-me",
        "daemon",
        "admin",
    }
)


class UnsafeDatabaseCredentialError(RuntimeError):
    """Raised when production starts with known-default database credentials."""


def database_password_from_url(database_url: str | None) -> str | None:
    """Extract the effective password component from an asyncpg-compatible DSN."""
    if not database_url:
        return None
    parsed = urlparse(database_url)
    if parsed.password is not None:
        return unquote(parsed.password)
    query_passwords = parse_qs(parsed.query, keep_blank_values=False).get("password", [])
    if query_passwords:
        return query_passwords[-1]
    return None


def resolve_database_url(
    configured_url: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return an explicit DSN or derive one safely from ``POSTGRES_*`` values."""
    source = os.environ if environ is None else environ
    explicit = configured_url or source.get("DATABASE_URL")
    if explicit:
        return explicit

    user = source.get("POSTGRES_USER")
    password = source.get("POSTGRES_PASSWORD")
    host = source.get("POSTGRES_HOST")
    database = source.get("POSTGRES_DB")
    if user is None or password is None or host is None or database is None:
        return None

    port = source.get("POSTGRES_PORT", "5432")
    encoded_user = quote(user, safe="")
    encoded_password = quote(password, safe="")
    encoded_database = quote(database, safe="")
    return f"postgresql://{encoded_user}:{encoded_password}@{host}:{port}/{encoded_database}"


def apply_resolved_database_url(settings: Settings) -> str | None:
    """Resolve and persist the effective DSN on a process-local Settings object."""
    database_url = resolve_database_url(settings.database_url)
    settings.database_url = database_url
    return database_url


def validate_database_credentials(settings: Settings) -> None:
    """Reject known-default passwords in every production credential source."""
    if settings.daemon_environment.strip().lower() != "production":
        return

    resolved_database_url = resolve_database_url(settings.database_url)
    password_candidates: tuple[tuple[str, str | None], ...] = (
        ("POSTGRES_PASSWORD", settings.postgres_password or None),
        ("PGPASSWORD", settings.pgpassword or None),
        ("DATABASE_URL", database_password_from_url(resolved_database_url)),
    )
    for source, password in password_candidates:
        if password is None:
            continue
        if password.strip().lower() in KNOWN_DEFAULT_POSTGRES_PASSWORDS:
            raise UnsafeDatabaseCredentialError(f"{source} uses a known default database password")
