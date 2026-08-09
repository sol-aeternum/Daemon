#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

import asyncpg

from orchestrator.database_url import resolve_database_url


MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
MIGRATION_ADVISORY_LOCK_ID = 0x4441454D4F4E


class MigrationLockError(RuntimeError):
    """Raised when another migration runner already holds the migration lock."""


async def ensure_migrations_table(conn: asyncpg.Connection):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


async def get_applied_migrations(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT filename FROM _migrations")
    return {row["filename"] for row in rows}


async def acquire_migration_lock(conn: asyncpg.Connection) -> bool:
    row = await conn.fetchrow(
        "SELECT pg_try_advisory_lock($1) AS acquired",
        MIGRATION_ADVISORY_LOCK_ID,
    )
    acquired = bool(row and row["acquired"])
    if not acquired:
        raise MigrationLockError("another migration runner is in progress")
    return acquired


async def release_migration_lock(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "SELECT pg_advisory_unlock($1)",
        MIGRATION_ADVISORY_LOCK_ID,
    )


async def apply_migration(conn: asyncpg.Connection, filepath: Path):
    filename = filepath.name
    sql_content = filepath.read_text()

    async with conn.transaction():
        await conn.execute(sql_content)
        await conn.execute("INSERT INTO _migrations (filename) VALUES ($1)", filename)


async def run_migrations():
    database_url = resolve_database_url()
    if not database_url:
        print("❌ DATABASE_URL or complete POSTGRES_* settings are required")
        sys.exit(1)

    if not MIGRATIONS_DIR.exists():
        print(f"❌ Migrations directory not found: {MIGRATIONS_DIR}")
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    lock_acquired = False
    try:
        lock_acquired = await acquire_migration_lock(conn)
        await ensure_migrations_table(conn)
        applied = await get_applied_migrations(conn)

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

        if not migration_files:
            print("⚠️  No migration files found")
            return

        pending_count = 0
        for filepath in migration_files:
            filename = filepath.name
            if filename in applied:
                print(f"⏭️  {filename} (already applied)")
            else:
                print(f"▶️  Applying {filename}...", end=" ", flush=True)
                try:
                    await apply_migration(conn, filepath)
                    print("✓")
                    pending_count += 1
                except Exception as e:
                    print(f"❌\nError applying {filename}: {e}")
                    raise

        if pending_count == 0:
            print("\n✅ All migrations already applied")
        else:
            print(f"\n✅ Applied {pending_count} migration(s)")

    finally:
        if lock_acquired:
            await release_migration_lock(conn)
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_migrations())
    except MigrationLockError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
