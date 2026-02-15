#!/usr/bin/env python3
import asyncio
import os
import sys
from pathlib import Path

import asyncpg


MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


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


async def apply_migration(conn: asyncpg.Connection, filepath: Path):
    filename = filepath.name
    sql_content = filepath.read_text()

    async with conn.transaction():
        await conn.execute(sql_content)
        await conn.execute("INSERT INTO _migrations (filename) VALUES ($1)", filename)


async def run_migrations():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ DATABASE_URL not set")
        sys.exit(1)

    if not MIGRATIONS_DIR.exists():
        print(f"❌ Migrations directory not found: {MIGRATIONS_DIR}")
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
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
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migrations())
