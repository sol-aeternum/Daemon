#!/usr/bin/env python3
import asyncio
import os
import sys

import asyncpg


DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


async def seed_default_user():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ DATABASE_URL not set")
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
        result = await conn.execute(
            """
            INSERT INTO users (
                id,
                email,
                name,
                username,
                preferences,
                settings,
                created_at,
                updated_at
            )
            VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6::jsonb, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
        """,
            DEFAULT_USER_ID,
            "default@daemon.local",
            "Default User",
            "default",
            "{}",
            "{}",
        )

        if "INSERT 0 1" in result:
            print(f"✅ Created default user: {DEFAULT_USER_ID}")
        else:
            print(f"⏭️  Default user already exists: {DEFAULT_USER_ID}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed_default_user())
