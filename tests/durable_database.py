"""Session-local migrated templates; no application DSN or persisted cache."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg


def database_dsn(dsn: str, database: str) -> str:
    """Keep connection options while overriding *every* database selector."""
    url = urlsplit(dsn)
    if url.scheme not in ("postgres", "postgresql"):
        raise ValueError("disposable PostgreSQL URL required")
    query = [
        (key, value) for key, value in parse_qsl(url.query) if key not in ("database", "dbname")
    ]
    return urlunsplit((url.scheme, url.netloc, "/" + database, urlencode(query), ""))


@dataclass(frozen=True)
class DatabaseTemplate:
    control_dsn: str
    name: str
    schema: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"tasks_template_[0-9a-f]{32}", self.name) or not re.fullmatch(
            r"tasks_test_[0-9a-f]{32}", self.schema
        ):
            raise ValueError("expected generated durable-test database and schema names")

    @asynccontextmanager
    async def clone(self) -> AsyncIterator[str]:
        """Never share mutable state, transactions or live connections across tests."""
        name = "tasks_clone_" + uuid.uuid4().hex
        admin = await asyncpg.connect(self.control_dsn)
        created = False
        try:
            await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{self.name}"')
            created = True
            yield database_dsn(self.control_dsn, name)
        finally:
            try:
                if created:
                    # No FORCE: leaked independent connections must fail teardown,
                    # rather than silently killing a test's unfinished work.
                    await admin.execute(f'DROP DATABASE "{name}"')
            finally:
                await admin.close()


@asynccontextmanager
async def migrated_template(dsn: str) -> AsyncIterator[DatabaseTemplate]:
    from tests.durable_tasks_support import MIGRATIONS, migration_sql_for_schema

    name = "tasks_template_" + uuid.uuid4().hex
    schema = "tasks_test_" + uuid.uuid4().hex
    template = DatabaseTemplate(dsn, name, schema)
    admin = await asyncpg.connect(dsn)
    created = False
    try:
        # template0, never the caller's database: no unrelated rows/settings.
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        created = True
        conn = await asyncpg.connect(database_dsn(dsn, name))
        try:
            for extension in ("pgcrypto", "vector"):
                await conn.execute(f"CREATE EXTENSION {extension} WITH SCHEMA public")
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f'SET search_path TO "{schema}", public')
            for path in sorted(MIGRATIONS.glob("*.sql")):
                async with conn.transaction():
                    await conn.execute(migration_sql_for_schema(path, schema))
        finally:
            await conn.close()
        # Clones are possible only after every template connection has closed.
        yield template
    finally:
        try:
            if created:
                await admin.execute(f'DROP DATABASE "{name}"')
        finally:
            await admin.close()
