"""Shared auth runtime state stored in Postgres.

These values are secrets or secret verifiers that must be process-independent
when Daemon runs multiple backend workers.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import asyncpg

from orchestrator.auth_tokens import generate_setup_token, hash_token

logger = logging.getLogger(__name__)

SETUP_TOKEN_HASH_KEY = "auth.setup_token_hash"
DEVELOPMENT_PEPPER_KEY = "auth.development_pepper"
AUTH_RUNTIME_LOCK_KEY = "daemon:auth_runtime_state"


async def lock_auth_runtime_state(conn: Any) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", AUTH_RUNTIME_LOCK_KEY)


async def get_runtime_value(conn: Any, key: str) -> str | None:
    return await conn.fetchval(
        """
        SELECT value
        FROM system_state
        WHERE key = $1
        """,
        key,
    )


async def set_runtime_value(conn: Any, key: str, value: str) -> None:
    await conn.execute(
        """
        INSERT INTO system_state (key, value, created_at, updated_at)
        VALUES ($1, $2, NOW(), NOW())
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value,
            updated_at = NOW()
        """,
        key,
        value,
    )


async def delete_runtime_value(conn: Any, key: str) -> None:
    await conn.execute(
        """
        DELETE FROM system_state
        WHERE key = $1
        """,
        key,
    )


async def create_setup_token_if_absent(conn: Any) -> str | None:
    plaintext = generate_setup_token()
    token_hash = hash_token(plaintext)
    inserted = await conn.fetchrow(
        """
        INSERT INTO system_state (key, value, created_at, updated_at)
        VALUES ($1, $2, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
        RETURNING value
        """,
        SETUP_TOKEN_HASH_KEY,
        token_hash,
    )
    if inserted is None:
        return None
    return plaintext


async def get_setup_token_hash(conn: Any) -> str | None:
    return await get_runtime_value(conn, SETUP_TOKEN_HASH_KEY)


async def clear_setup_token_hash(conn: Any) -> None:
    await delete_runtime_value(conn, SETUP_TOKEN_HASH_KEY)


async def ensure_development_pepper_in_db(db_pool: asyncpg.Pool) -> str:
    generated = secrets.token_urlsafe(32)
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await lock_auth_runtime_state(conn)
            existing = await get_runtime_value(conn, DEVELOPMENT_PEPPER_KEY)
            if existing:
                return existing
            await set_runtime_value(conn, DEVELOPMENT_PEPPER_KEY, generated)
            return generated
