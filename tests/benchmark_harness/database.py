from __future__ import annotations

import os
from pathlib import Path

import dotenv

from orchestrator.database_url import resolve_database_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DOCKER_HOSTS = ("postgres", "db", "database", "pgvector")


def configured_benchmark_database_url() -> str:
    """Resolve host-side benchmark credentials without a committed password."""
    dotenv.load_dotenv(PROJECT_ROOT / ".env")
    source = dict(os.environ)
    explicit = source.get("DATABASE_URL")
    if not explicit:
        if not source.get("POSTGRES_PASSWORD"):
            raise RuntimeError(
                "Set DATABASE_URL or a non-empty POSTGRES_PASSWORD before running DB benchmarks"
            )
        source.setdefault("POSTGRES_USER", "daemon")
        source.setdefault("POSTGRES_DB", "daemon")
        source["POSTGRES_HOST"] = "127.0.0.1"

    database_url = resolve_database_url(environ=source)
    if not database_url:
        raise RuntimeError(
            "Set DATABASE_URL or complete POSTGRES_* settings before running DB benchmarks"
        )

    for docker_host in _DOCKER_HOSTS:
        database_url = database_url.replace(f"@{docker_host}:", "@127.0.0.1:").replace(
            f"@{docker_host}/", "@127.0.0.1/"
        )
    return database_url
